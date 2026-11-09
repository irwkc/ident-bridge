from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
import traceback


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="CTShareDoctor")
    p.add_argument("--self-test", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    args = parse_args(argv)

    if not getattr(sys, "frozen", False):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)

    from ct_share.install import append_log, ensure_install_layout, message_box
    from ct_share.paths import install_dir

    os.environ["CT_SHARE_DIR"] = str(install_dir())
    ensure_install_layout()

    try:
        if args.self_test:
            append_log("doctor", "self-test: OK")
            message_box("Self-test OK. См. C:\\ct-share\\doctor.log", "CT Share Врач")
            return 0

        from ct_share.doctor_ui import run_gui

        run_gui()
        return 0
    except Exception:
        err = traceback.format_exc()
        append_log("doctor", "fatal:\n" + err)
        message_box("CT Share Врач аварийно завершился. См. C:\\ct-share\\doctor.log", "CT Share", "error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
