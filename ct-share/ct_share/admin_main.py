from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
import traceback


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="CTShareAdmin")
    p.add_argument("--install-autostart", action="store_true")
    p.add_argument("--uninstall-autostart", action="store_true")
    p.add_argument("--self-test", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    args = parse_args(argv)

    if not getattr(sys, "frozen", False):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)

    from ct_share.install import (
        append_log,
        ensure_install_layout,
        install_admin_autostart,
        message_box,
        uninstall_admin_autostart,
    )
    from ct_share.paths import install_dir

    os.environ["CT_SHARE_DIR"] = str(install_dir())
    ensure_install_layout()

    try:
        if args.self_test:
            append_log("admin", "self-test: OK")
            message_box("Self-test OK. См. C:\\ct-share\\admin.log", "CT Share Админ")
            return 0
        if args.install_autostart:
            install_admin_autostart()
            return 0
        if args.uninstall_autostart:
            uninstall_admin_autostart()
            return 0

        from ct_share.admin_ui import run_gui

        run_gui()
        return 0
    except Exception:
        err = traceback.format_exc()
        append_log("admin", "fatal:\n" + err)
        message_box("CT Share Админ аварийно завершился. См. C:\\ct-share\\admin.log", "CT Share", "error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
