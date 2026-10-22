"""Reset or serve both synthetic Acme demo apps together.

From the repository root:
    python demo-apps/manage.py reset                # wipe and reseed both apps (demo data only)
    python demo-apps/manage.py serve                # portal on :7701, CRM on :7702 (loopback)
    python demo-apps/manage.py serve --enable-reset-endpoint
"""

import argparse
import importlib.util
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from demo_common import RESET_CONFIRMATION, RESET_HEADER, RESET_PATH, create_server, serve_in_thread  # noqa: E402


def load_app_class(directory, class_name):
    spec = importlib.util.spec_from_file_location(f"{directory.replace('-', '_')}_server", HERE / directory / "server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, class_name)


APPS = (("acme-portal", "AcmePortal"), ("acme-crm", "AcmeCrm"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Synthetic Acme demo apps (local test fixtures only)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reset", help="Wipe and reseed both apps")
    serve = sub.add_parser("serve", help="Serve both apps on 127.0.0.1")
    serve.add_argument("--enable-reset-endpoint", action="store_true")
    args = parser.parse_args(argv)

    apps = [load_app_class(directory, cls)() for directory, cls in APPS]
    if args.command == "reset":
        for app in apps:
            app.reset()
            print(f"Reset synthetic demo data for {app.name}: {app.db_path}")
        return 0

    servers = [create_server(app, enable_reset_endpoint=args.enable_reset_endpoint) for app in apps]
    for app, server in zip(apps, servers):
        serve_in_thread(server)
        print(f"{app.title} (synthetic demo data) on http://127.0.0.1:{server.server_port}/")
    if args.enable_reset_endpoint:
        print(f"Reset endpoints enabled: POST {RESET_PATH} with header {RESET_HEADER}: {RESET_CONFIRMATION}")
    print("Press Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
