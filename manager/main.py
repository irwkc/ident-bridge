from __future__ import annotations

import argparse
import sys


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="IdentBridge")
    p.add_argument("--service", action="store_true", help="Headless keep-alive service")
    p.add_argument("--install-autostart", action="store_true", help="Install scheduled task (admin)")
    p.add_argument("--uninstall-autostart", action="store_true", help="Remove scheduled task (admin)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Ensure install dir env before importing app/service paths consumers
    from manager.install import (
        ensure_install_layout,
        install_autostart,
        uninstall_autostart,
    )
    from manager.paths import install_dir
    import os

    os.environ.setdefault("IDENT_BRIDGE_DIR", str(install_dir()))
    ensure_install_layout()

    if args.install_autostart:
        print(install_autostart())
        return 0
    if args.uninstall_autostart:
        print(uninstall_autostart())
        return 0
    if args.service:
        from manager.service import run_service_forever

        return run_service_forever()

    from manager.ui import run_gui

    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
