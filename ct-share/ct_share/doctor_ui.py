from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from ct_share.common import recent_scans, search_scans, shared_root
from ct_share.install import first_run_install, load_config
from ct_share.setup_dialog import run_setup_if_needed, show_setup_dialog


class DoctorApp:
    def __init__(self) -> None:
        first_run_install("doctor")
        self._ready = False
        self._items: list = []
        if not run_setup_if_needed(None):
            return

        self.cfg = load_config()
        self.recent_count = int(self.cfg.get("recent_count", 5))

        self.root = tk.Tk()
        self.root.title("CT Share — Врач")
        self.root.geometry("440x340")
        self.root.minsize(360, 280)
        self._build()
        self._ready = True
        self.refresh()

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)

        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Label(top, text="Поиск пациента").pack(side="left")
        ttk.Button(top, text="Настройки", command=self._settings).pack(side="right")

        self.query = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=self.query, font=("", 12))
        entry.pack(fill="x", pady=(4, 8))
        entry.focus_set()
        entry.bind("<Return>", lambda _e: self.refresh())
        self.query.trace_add("write", lambda *_: self.refresh())

        self.hint = ttk.Label(
            frame,
            text=f"Последние {self.recent_count} загрузок",
            foreground="#555",
        )
        self.hint.pack(anchor="w")

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True, pady=(4, 8))

        self.listbox = tk.Listbox(
            list_frame,
            activestyle="dotbox",
            font=("", 11),
            height=8,
            exportselection=False,
        )
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", lambda _e: self.open_selected())
        self.listbox.bind("<Return>", lambda _e: self.open_selected())

        btns = ttk.Frame(frame)
        btns.pack(fill="x")
        ttk.Button(btns, text="Открыть", command=self.open_selected).pack(side="left")
        ttk.Button(btns, text="Обновить", command=self.refresh).pack(side="left", padx=(8, 0))

        self.share_lbl = ttk.Label(
            frame,
            text=self.cfg.get("shared_folder", ""),
            foreground="#888",
            font=("", 8),
        )
        self.share_lbl.pack(anchor="w", pady=(8, 0))

    def _settings(self) -> None:
        if show_setup_dialog(self.root, load_config()):
            self.cfg = load_config()
            self.recent_count = int(self.cfg.get("recent_count", 5))
            self.share_lbl.configure(text=self.cfg.get("shared_folder", ""))
            self.refresh()

    def refresh(self) -> None:
        if not getattr(self, "listbox", None):
            return
        q = self.query.get().strip()
        try:
            if q:
                self._items = search_scans(q)
                self.hint.configure(text=f"Найдено: {len(self._items)}")
            else:
                self._items = recent_scans(self.recent_count)
                self.hint.configure(text=f"Последние {self.recent_count} загрузок")
        except OSError as exc:
            self._items = []
            self.hint.configure(text=f"Нет доступа к общей папке: {exc}")

        self.listbox.delete(0, tk.END)
        for scan in self._items:
            self.listbox.insert(tk.END, scan.display_name)
        if self._items:
            self.listbox.selection_set(0)

    def open_selected(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        scan = self._items[sel[0]]
        path = scan.path
        if not path.exists():
            messagebox.showerror("Ошибка", f"Папка не найдена:\n{path}", parent=self.root)
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except OSError as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self.root)

    def run(self) -> None:
        if not getattr(self, "_ready", False):
            return
        try:
            shared_root()
        except OSError as exc:
            messagebox.showwarning(
                "Сеть",
                f"Не удалось открыть общую папку.\nПроверьте настройки.\n\n{exc}",
                parent=self.root,
            )
        self.root.mainloop()


def run_gui() -> None:
    DoctorApp().run()
