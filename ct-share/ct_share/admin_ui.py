from __future__ import annotations

import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ct_share.common import (
    DetectedScan,
    copy_tree_with_progress,
    describe_drives,
    detect_scan,
    human_fio_to_folder,
    iter_ready_media,
    media_fingerprint,
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
        ttk.Button(btns, text="Выбрать диск…", command=self._pick_folder).pack(side="left")
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
        chosen = filedialog.askdirectory(parent=self.root, title="Диск или папка с КТ")
        if not chosen:
            return
        root = Path(chosen)
        det = detect_scan(root)
        if not det:
            # всё равно предложим скопировать весь выбранный путь
            det = DetectedScan(fio_folder="", display_name="", birth_date=None, source=root)
        self._busy = True
        self._ask_and_copy(det)

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

            det = detect_scan(root)
            if not det:
                self._set_status(f"{root} — нет КТ. {describe_drives()}")
                continue

            self._handled.add(fp)
            self._busy = True
            append_log(
                "admin",
                f"detected fio={det.fio_folder!r} birth={det.birth_date} src={det.source}",
            )
            self.root.after(0, lambda d=det: self._ask_and_copy(d))
            return

        self._handled &= ready_fps

        if not ready_fps:
            self._set_status(f"Носитель не вставлен. {describe_drives()}")
        else:
            self._set_status(f"Готово. {describe_drives()}")

    def _ask_fio(self, suggested: str) -> str | None:
        """Диалог ввода ФИО для имени папки."""
        win = tk.Toplevel(self.root)
        win.title("ФИО пациента")
        win.geometry("420x160")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.attributes("-topmost", True)

        result: dict[str, str | None] = {"value": None}

        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="Введите ФИО пациента — так будет названа папка:",
            wraplength=380,
        ).pack(anchor="w")

        var = tk.StringVar(value=suggested)
        entry = ttk.Entry(frame, textvariable=var, font=("", 12))
        entry.pack(fill="x", pady=(10, 0))
        entry.focus_set()
        entry.selection_range(0, tk.END)

        def ok() -> None:
            text = var.get().strip()
            if not text:
                messagebox.showwarning("ФИО", "Введите ФИО пациента.", parent=win)
                return
            result["value"] = text
            win.destroy()

        def cancel() -> None:
            result["value"] = None
            win.destroy()

        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(16, 0))
        ttk.Button(btns, text="OK", command=ok).pack(side="right")
        ttk.Button(btns, text="Отмена", command=cancel).pack(side="right", padx=(0, 8))
        win.bind("<Return>", lambda _e: ok())
        win.bind("<Escape>", lambda _e: cancel())
        win.protocol("WM_DELETE_WINDOW", cancel)
        self.root.wait_window(win)
        return result["value"]

    def _ask_and_copy(self, det: DetectedScan) -> None:
        try:
            share = shared_root()
        except OSError as exc:
            messagebox.showerror("Сеть", f"Нет доступа к общей папке:\n{exc}", parent=self.root)
            self._busy = False
            return

        self.root.lift()
        self.root.attributes("-topmost", True)

        hint = det.display_name or "не удалось определить автоматически"
        if det.birth_date:
            hint += f"\nДата рождения (из снимка): {det.birth_date}"

        answer = messagebox.askyesno(
            "Поделиться КТ?",
            f"Найден диск с КТ.\n\n"
            f"ФИО (авто): {hint}\n\n"
            f"Будет скопировано ВСЁ содержимое диска\n{det.source}\n\n"
            f"в общую папку:\n{share}",
            parent=self.root,
            icon=messagebox.QUESTION,
        )
        if not answer:
            self._busy = False
            self._set_status("Отменено")
            return

        fio_input = self._ask_fio(det.display_name or "")
        if not fio_input:
            self._busy = False
            self._set_status("Отменено")
            return

        fio_folder = human_fio_to_folder(fio_input)
        dest = share / fio_folder

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
        progress.geometry("440x140")
        progress.resizable(False, False)
        progress.transient(self.root)
        progress.grab_set()
        progress.attributes("-topmost", True)

        ttk.Label(progress, text=f"Копирование → {fio_folder}", padding=(16, 12, 16, 4)).pack(anchor="w")
        file_lbl = ttk.Label(progress, text="Подсчёт файлов…", foreground="#555", wraplength=400)
        file_lbl.pack(anchor="w", padx=16)
        bar = ttk.Progressbar(progress, mode="determinate", length=400, maximum=100)
        bar.pack(padx=16, pady=(8, 4))
        pct_lbl = ttk.Label(progress, text="0%")
        pct_lbl.pack(anchor="e", padx=16, pady=(0, 12))

        def on_progress(done: int, total: int, rel: str) -> None:
            pct = int(done * 100 / max(total, 1))

            def ui() -> None:
                bar["maximum"] = total
                bar["value"] = done
                short = rel if len(rel) < 60 else "…" + rel[-57:]
                file_lbl.configure(text=f"{done} / {total}: {short}")
                pct_lbl.configure(text=f"{pct}%")

            self.root.after(0, ui)

        def worker() -> None:
            err: Exception | None = None
            try:
                copy_tree_with_progress(det.source, dest, on_progress=on_progress)
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
                    append_log("admin", f"copied all → {dest}")
                    messagebox.showinfo("Готово", f"КТ скопирован:\n{dest}", parent=self.root)
                    self._set_status(f"Скопировано: {fio_folder}")

            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def run(self) -> None:
        if not getattr(self, "_ready", False):
            return
        self.root.mainloop()


def run_gui() -> None:
    AdminApp().run()
