from __future__ import annotations

import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ct_share.common import (
    find_patient_source_on_disc,
    iter_ready_discs,
    parse_patient_folder,
    shared_root,
)
from ct_share.install import (
    append_log,
    autostart_installed,
    first_run_install,
    install_admin_autostart,
    load_config,
    uninstall_admin_autostart,
)
from ct_share.setup_dialog import run_setup_if_needed, show_setup_dialog


class AdminApp:
    def __init__(self) -> None:
        first_run_install("admin")
        self._ready = False
        if not run_setup_if_needed(None):
            return

        self.cfg = load_config()
        self.poll_seconds = float(self.cfg.get("poll_seconds", 2))
        self._seen: set[str] = set()
        self._busy = False
        self._stop = threading.Event()

        self.root = tk.Tk()
        self.root.title("CT Share — Админ")
        self.root.geometry("400x200")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self._build()
        self._ready = True
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._watcher = threading.Thread(target=self._watch_loop, daemon=True)
        self._watcher.start()

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Ожидание диска с КТ…", font=("", 11)).pack(anchor="w")
        self.status = ttk.Label(frame, text="Мониторинг дисковода", foreground="#444")
        self.status.pack(anchor="w", pady=(8, 0))

        self.share_lbl = ttk.Label(
            frame,
            text=f"Общая папка:\n{self.cfg.get('shared_folder', '')}",
            wraplength=360,
            foreground="#666",
            font=("", 9),
        )
        self.share_lbl.pack(anchor="w", pady=(12, 0))

        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Настройки", command=self._settings).pack(side="left")
        self.auto_btn = ttk.Button(btns, text=self._autostart_label(), command=self._toggle_autostart)
        self.auto_btn.pack(side="left", padx=(8, 0))

    def _autostart_label(self) -> str:
        return "Убрать автозапуск" if autostart_installed() else "Автозапуск при входе"

    def _settings(self) -> None:
        if show_setup_dialog(self.root, load_config()):
            self.cfg = load_config()
            self.poll_seconds = float(self.cfg.get("poll_seconds", 2))
            self.share_lbl.configure(text=f"Общая папка:\n{self.cfg.get('shared_folder', '')}")

    def _toggle_autostart(self) -> None:
        if autostart_installed():
            uninstall_admin_autostart()
        else:
            install_admin_autostart()
        self.auto_btn.configure(text=self._autostart_label())

    def _on_close(self) -> None:
        self._stop.set()
        self.root.destroy()

    def _set_status(self, text: str) -> None:
        if not self.root.winfo_exists():
            return
        self.root.after(0, lambda: self.status.configure(text=text))

    def _watch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                self._set_status(f"Ошибка: {exc}")
                append_log("admin", f"watch error: {exc}")
            self._stop.wait(self.poll_seconds)

    def _tick(self) -> None:
        if self._busy:
            return

        ready = {str(p) for p in iter_ready_discs()}
        self._seen &= ready

        for root in iter_ready_discs():
            key = str(root)
            if key in self._seen:
                continue

            found = find_patient_source_on_disc(root)
            if not found:
                self._seen.add(key)
                self._set_status(f"{root} — не похоже на КТ")
                continue

            folder_name, source = found
            self._seen.add(key)
            self._busy = True
            self.root.after(0, lambda fn=folder_name, src=source: self._ask_and_copy(fn, src))
            return

        if not ready:
            self._set_status("Дисковод пуст")
        else:
            self._set_status("Диск проверен")

    def _ask_and_copy(self, folder_name: str, source: Path) -> None:
        fio, birth = parse_patient_folder(folder_name)
        label = f"{fio}" + (f"\nДата рождения: {birth}" if birth else "")
        try:
            share = shared_root()
        except OSError as exc:
            messagebox.showerror("Сеть", f"Нет доступа к общей папке:\n{exc}", parent=self.root)
            self._busy = False
            return

        dest = share / folder_name
        answer = messagebox.askyesno(
            "Поделиться КТ?",
            f"Найден снимок:\n\n{label}\n\nПапка:\n{folder_name}\n\n"
            f"Скопировать в общую сетевую папку?\n{share}",
            parent=self.root,
            icon=messagebox.QUESTION,
        )
        if not answer:
            self._busy = False
            self._set_status("Отменено")
            return

        if dest.exists():
            overwrite = messagebox.askyesno(
                "Уже есть",
                f"Папка уже существует:\n{dest}\n\nЗаменить?",
                parent=self.root,
            )
            if not overwrite:
                self._busy = False
                self._set_status("Пропущено — уже есть")
                return
            try:
                shutil.rmtree(dest)
            except OSError as exc:
                messagebox.showerror("Ошибка", f"Не удалось удалить старую папку:\n{exc}", parent=self.root)
                self._busy = False
                return

        progress = tk.Toplevel(self.root)
        progress.title("Копирование")
        progress.geometry("320x90")
        progress.resizable(False, False)
        progress.transient(self.root)
        progress.grab_set()
        ttk.Label(progress, text="Копирование КТ…", padding=16).pack()
        bar = ttk.Progressbar(progress, mode="indeterminate", length=260)
        bar.pack(padx=16, pady=(0, 16))
        bar.start(12)

        def worker() -> None:
            err: Exception | None = None
            try:
                is_drive_root = len(source.parts) == 1 or str(source).endswith(":\\")
                if is_drive_root:
                    dest.mkdir(parents=True, exist_ok=True)
                    for child in source.iterdir():
                        target = dest / child.name
                        if child.is_dir():
                            shutil.copytree(child, target)
                        else:
                            shutil.copy2(child, target)
                else:
                    shutil.copytree(source, dest)
            except Exception as exc:  # noqa: BLE001
                err = exc

            def done() -> None:
                progress.destroy()
                self._busy = False
                if err:
                    append_log("admin", f"copy failed: {err}")
                    messagebox.showerror("Ошибка копирования", str(err), parent=self.root)
                    self._set_status("Ошибка копирования")
                else:
                    append_log("admin", f"copied: {dest}")
                    messagebox.showinfo("Готово", f"КТ скопирован:\n{dest}", parent=self.root)
                    self._set_status(f"Скопировано: {folder_name}")

            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def run(self) -> None:
        if not getattr(self, "_ready", False):
            return
        self.root.mainloop()


def run_gui() -> None:
    AdminApp().run()
