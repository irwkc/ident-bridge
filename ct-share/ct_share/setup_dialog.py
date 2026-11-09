"""Первый запуск: указать общую сетевую папку."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ct_share.install import config_needs_setup, load_config, save_config


def run_setup_if_needed(parent: tk.Tk | None = None) -> bool:
    """True, если конфиг готов (или пользователь сохранил). False — отмена."""
    cfg = load_config()
    if not config_needs_setup(cfg):
        return True
    return show_setup_dialog(parent, cfg)


def show_setup_dialog(parent: tk.Tk | None = None, cfg: dict | None = None) -> bool:
    cfg = dict(cfg or load_config())
    result = {"ok": False}

    win = tk.Toplevel(parent) if parent else tk.Tk()
    win.title("CT Share — Настройка")
    win.geometry("460x220")
    win.resizable(False, False)
    win.attributes("-topmost", True)
    if parent:
        win.transient(parent)
        win.grab_set()

    frame = ttk.Frame(win, padding=16)
    frame.pack(fill="both", expand=True)

    ttk.Label(
        frame,
        text="Укажите общую сетевую папку для КТ-снимков",
        font=("", 11),
    ).pack(anchor="w")
    ttk.Label(
        frame,
        text="Например: \\\\SERVER\\KT  или  Z:\\KT",
        foreground="#666",
    ).pack(anchor="w", pady=(4, 10))

    path_var = tk.StringVar(value=str(cfg.get("shared_folder", "")))
    row = ttk.Frame(frame)
    row.pack(fill="x")
    entry = ttk.Entry(row, textvariable=path_var, font=("", 11))
    entry.pack(side="left", fill="x", expand=True)
    entry.focus_set()

    def browse() -> None:
        chosen = filedialog.askdirectory(parent=win, title="Общая папка КТ")
        if chosen:
            path_var.set(chosen)

    ttk.Button(row, text="Обзор…", command=browse).pack(side="left", padx=(8, 0))

    recent_var = tk.StringVar(value=str(cfg.get("recent_count", 5)))
    opts = ttk.Frame(frame)
    opts.pack(fill="x", pady=(12, 0))
    ttk.Label(opts, text="Сколько последних снимков у врача:").pack(side="left")
    ttk.Spinbox(opts, from_=1, to=50, width=5, textvariable=recent_var).pack(side="left", padx=(8, 0))

    def save() -> None:
        folder = path_var.get().strip()
        if not folder:
            messagebox.showwarning("Настройка", "Укажите путь к общей папке.", parent=win)
            return
        try:
            recent = int(recent_var.get())
        except ValueError:
            recent = 5
        cfg["shared_folder"] = folder
        cfg["recent_count"] = max(1, min(50, recent))
        save_config(cfg)
        result["ok"] = True
        win.destroy()

    def cancel() -> None:
        result["ok"] = not config_needs_setup(load_config())
        win.destroy()

    btns = ttk.Frame(frame)
    btns.pack(fill="x", pady=(16, 0))
    ttk.Button(btns, text="Сохранить", command=save).pack(side="right")
    ttk.Button(btns, text="Отмена", command=cancel).pack(side="right", padx=(0, 8))

    win.protocol("WM_DELETE_WINDOW", cancel)
    if parent:
        parent.wait_window(win)
    else:
        win.mainloop()
    return bool(result["ok"])
