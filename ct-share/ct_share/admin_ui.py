from __future__ import annotations

import shutil
import threading
import zipfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ct_share.common import (
    describe_drives,
    find_patient_source_on_disc,
    iter_ready_media,
    media_fingerprint,
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
        self._handled: set[str] = set()
        self._busy = False
        self._stop = threading.Event()

        self.root = tk.Tk()
        self.root.title("CT Share — Админ")
        self.root.geometry("420x230")
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

        ttk.Label(frame, text="Ожидание диска / флешки с КТ…", font=("", 11)).pack(anchor="w")
        self.status = ttk.Label(frame, text="Мониторинг…", foreground="#444", wraplength=380)
        self.status.pack(anchor="w", pady=(8, 0))

        self.share_lbl = ttk.Label(
            frame,
            text=f"Общая папка:\n{self.cfg.get('shared_folder', '')}",
            wraplength=380,
            foreground="#666",
            font=("", 9),
        )
        self.share_lbl.pack(anchor="w", pady=(12, 0))

        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Выбрать папку…", command=self._pick_folder).pack(side="left")
        ttk.Button(btns, text="Настройки", command=self._settings).pack(side="left", padx=(8, 0))
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

    def _pick_folder(self) -> None:
        chosen = filedialog.askdirectory(parent=self.root, title="Папка или диск с КТ")
        if not chosen:
            return
        root = Path(chosen)
        found = find_patient_source_on_disc(root)
        if not found:
            # Если выбрали саму папку пациента
            if root.is_dir():
                found = (root.name, root)
            else:
                messagebox.showwarning("КТ", "Не удалось распознать снимок в этой папке.", parent=self.root)
                return
        folder_name, source = found
        self._busy = True
        self._ask_and_copy(folder_name, source)

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

        ready_fps: set[str] = set()
        for root in iter_ready_media():
            fp = media_fingerprint(root)
            ready_fps.add(fp)
            if fp in self._handled:
                continue

            found = find_patient_source_on_disc(root)
            if not found:
                # Не КТ — не помечаем навсегда: при смене содержимого спросим снова
                self._set_status(f"{root} — нет КТ. {describe_drives()}")
                continue

            folder_name, source = found
            self._handled.add(fp)
            self._busy = True
            append_log("admin", f"detected {folder_name} on {root}")
            self.root.after(0, lambda fn=folder_name, src=source: self._ask_and_copy(fn, src))
            return

        # Носитель извлечён — снова спросим при следующей вставке
        self._handled &= ready_fps

        if not ready_fps:
            self._set_status(f"Носитель не вставлен. {describe_drives()}")
        else:
            self._set_status(f"Готово. {describe_drives()}")

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
        self.root.lift()
        self.root.attributes("-topmost", True)
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
                _copy_scan(source, dest)
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


def _copy_scan(source: Path, dest: Path) -> None:
    if source.is_file() and source.suffix.lower() == ".zip":
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source, "r") as zf:
            zf.extractall(dest)
        return

    is_drive_root = len(source.parts) == 1 or str(source).endswith(":\\")
    if is_drive_root:
        dest.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            target = dest / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
        return

    shutil.copytree(source, dest)


def run_gui() -> None:
    AdminApp().run()
