from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
import traceback


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="IdentBridge")
    p.add_argument("--service", action="store_true", help="Headless keep-alive service")
    p.add_argument("--install-autostart", action="store_true", help="Install scheduled task (admin)")
    p.add_argument("--uninstall-autostart", action="store_true", help="Remove scheduled task (admin)")
    p.add_argument("--self-test", action="store_true", help="Write OK to log and exit")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    args = parse_args(argv)

    # Ensure project root is importable when running as script
    if not getattr(sys, "frozen", False):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)

    from manager.install import (
        ensure_install_layout,
        install_autostart,
        message_box,
        uninstall_autostart,
    )
    from manager.paths import install_dir
    from manager.service import append_log

    os.environ["IDENT_BRIDGE_DIR"] = str(install_dir())
    ensure_install_layout()

    try:
        if args.self_test:
            append_log("self-test: OK")
            message_box("Self-test OK. Check C:\\ident-bridge\\bridge.log")
            return 0
        if args.install_autostart:
            install_autostart()
            return 0
        if args.uninstall_autostart:
            uninstall_autostart()
            return 0
        if args.service:
            from manager.service import run_service_forever

            return run_service_forever()

        from manager.ui import run_gui

        run_gui()
        return 0
    except Exception:
        err = traceback.format_exc()
        append_log("fatal:\n" + err)
        message_box("IdentBridge crashed. Open C:\\ident-bridge\\bridge.log")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
