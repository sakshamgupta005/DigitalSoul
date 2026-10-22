"""Shared server code for the synthetic Acme demo apps (standard library only).

These apps are local test fixtures for the Digital Ghost demo workflow. They hold
synthetic data only and never connect to a real customer system.
"""

import argparse
import html
import json
import re
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MAX_BODY_BYTES = 16 * 1024
LOOPBACK_BIND_HOSTS = {"127.0.0.1", "::1", "localhost"}
RESET_PATH = "/__demo__/reset"
RESET_HEADER = "X-Demo-Reset"
RESET_CONFIRMATION = "wipe-synthetic-demo-data"
SHARED_STATIC = Path(__file__).resolve().parent / "static"

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def esc(value):
    return html.escape(str(value), quote=True)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class HttpError(Exception):
    def __init__(self, status, code, message, headers=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


class Request:
    def __init__(self, method, path, query, headers, body):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body

    def param(self, name, default=None):
        values = self.query.get(name)
        if not values:
            return default
        if len(values) > 1:
            raise HttpError(422, "VALIDATION_ERROR", f"Query parameter '{name}' given more than once.")
        return values[0]

    def json(self):
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise HttpError(415, "UNSUPPORTED_MEDIA_TYPE", "Request body must be application/json.")
        try:
            value = json.loads(self.body.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError) as exc:
            raise HttpError(400, "INVALID_JSON", f"Request body is not valid JSON: {exc}") from None
        if not isinstance(value, dict):
            raise HttpError(422, "VALIDATION_ERROR", "Request body must be a JSON object.")
        return value


def _reject_constant(name):
    raise ValueError(f"non-finite number {name} is not allowed")


class Response:
    def __init__(self, status, body, content_type, headers=None):
        self.status = status
        self.body = body
        self.content_type = content_type
        self.headers = headers or {}


def json_response(status, payload, headers=None):
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return Response(status, body, "application/json; charset=utf-8", headers)


def html_response(status, text):
    return Response(status, text.encode("utf-8"), "text/html; charset=utf-8")


def parse_limit(request, default=50):
    raw = request.param("limit", str(default))
    if not raw.isdigit() or not 1 <= int(raw) <= 200:
        raise HttpError(422, "VALIDATION_ERROR", "limit must be an integer from 1 to 200.")
    return int(raw)


class DemoApp:
    """Base class: SQLite storage, seed/reset, routing and HTML page shell."""

    name = ""
    title = ""
    hostname = ""
    default_port = 0
    nav = ()
    static_files = {}

    def __init__(self, db_path=None):
        app_dir = Path(sys.modules[type(self).__module__].__file__).resolve().parent
        self.app_dir = app_dir
        self.db_path = Path(db_path) if db_path else app_dir / "data" / f"{self.name}.sqlite3"
        self.seed = json.loads((app_dir / "seed.json").read_text(encoding="utf-8"))
        self.routes = []
        self._register_routes()
        self.route("GET", r"/api/health", self.health)
        self.route("GET", r"/static/(?P<name>[a-z0-9_.-]+)", self.static)

    # Storage -----------------------------------------------------------------

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def reset(self):
        """Drop and recreate every table, then load seed.json. Demo data only."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in self.schema:
                conn.execute(statement)
            self.load_seed(conn)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def ensure_seeded(self):
        if not self.db_path.exists():
            self.reset()
            return
        conn = self.connect()
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            conn.close()
        if not set(self.tables) <= tables:
            self.reset()

    def snapshot(self):
        """Full table contents in a stable order, used to check reset repeatability."""
        conn = self.connect()
        try:
            return {
                table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
                for table in self.tables
            }
        finally:
            conn.close()

    # Routing -----------------------------------------------------------------

    def route(self, method, pattern, handler):
        self.routes.append((method, re.compile(pattern), handler))

    def dispatch(self, request):
        matched = [(m, rx.fullmatch(request.path), h) for m, rx, h in self.routes]
        matched = [(m, match, h) for m, match, h in matched if match]
        if not matched:
            raise HttpError(404, "NOT_FOUND", f"No route for {request.path}.")
        for method, match, handler in matched:
            if method == request.method:
                return handler(request, **match.groupdict())
        allowed = ", ".join(sorted({m for m, _, _ in matched}))
        raise HttpError(405, "METHOD_NOT_ALLOWED", f"{request.method} is not allowed here.", {"Allow": allowed})

    def health(self, request):
        return json_response(200, {"app": self.name, "synthetic_demo_data": True, "status": "ok"})

    def static(self, request, name):
        allowed = {**{"demo.css": SHARED_STATIC / "demo.css"}, **self.static_files}
        path = allowed.get(name)
        if path is None:
            raise HttpError(404, "NOT_FOUND", f"No static file {name}.")
        content_type = "text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8"
        return Response(200, Path(path).read_bytes(), content_type)

    # HTML --------------------------------------------------------------------

    def page(self, title, main_html, main_attrs="", script=None):
        nav = " ".join(f'<a href="{esc(href)}">{esc(label)}</a>' for href, label in self.nav)
        script_tag = f'<script src="/static/{esc(script)}" defer></script>' if script else ""
        return html_response(
            200,
            f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="demo-app" content="{esc(self.name)}">
<title>{esc(title)}</title>
<link rel="stylesheet" href="/static/demo.css">
{script_tag}
</head>
<body>
<p class="demo-banner" data-testid="demo-banner" role="note">SYNTHETIC DEMO DATA. {esc(self.title)} is a local test fixture for the Digital Ghost demo. It is not a real system and holds no real customer data.</p>
<header><span class="brand">{esc(self.title)}</span><nav aria-label="{esc(self.title)}">{nav}</nav></header>
<main{main_attrs}>
{main_html}
</main>
</body>
</html>
""",
        )

    def html_error(self, status, message):
        response = self.page(f"{self.title} - {status}", f"<h1>{status}</h1><p>{esc(message)}</p>")
        response.status = status
        return response


def make_handler(app, enable_reset_endpoint=False, quiet=False):
    allowed_hosts = {"127.0.0.1", "localhost", "[::1]", app.hostname}

    class Handler(BaseHTTPRequestHandler):
        server_version = f"{app.name}-demo"
        sys_version = ""

        def log_message(self, format, *args):
            if not quiet:
                super().log_message(format, *args)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PATCH(self):
            self._handle("PATCH")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

        def _handle(self, method):
            parts = urlsplit(self.path)
            wants_html = not parts.path.startswith(("/api/", "/__demo__/"))
            try:
                self._check_host()
                body = self._read_body()
                request = Request(method, parts.path, parse_qs(parts.query, keep_blank_values=True), self.headers, body)
                if parts.path == RESET_PATH and enable_reset_endpoint:
                    response = self._reset(request)
                else:
                    response = app.dispatch(request)
            except HttpError as err:
                if wants_html and err.status in (404, 405):
                    response = app.html_error(err.status, err.message)
                else:
                    response = json_response(err.status, {"error": {"code": err.code, "message": err.message}})
                response.headers.update(err.headers)
            except Exception as exc:  # pragma: no cover - defensive, logged
                self.log_error("Unhandled error: %r", exc)
                response = json_response(500, {"error": {"code": "INTERNAL", "message": "Internal error."}})
            self._send(response)

        def _check_host(self):
            host = (self.headers.get("Host") or "").strip().lower()
            name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
            if name not in allowed_hosts:
                raise HttpError(421, "MISDIRECTED_REQUEST", "Host header is not a loopback or demo host.")

        def _read_body(self):
            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                raise HttpError(411, "LENGTH_REQUIRED", "Chunked request bodies are not supported.")
            raw = self.headers.get("Content-Length")
            if raw is None:
                return b""
            if not raw.isdigit():
                raise HttpError(400, "BAD_REQUEST", "Invalid Content-Length.")
            length = int(raw)
            if length > MAX_BODY_BYTES:
                raise HttpError(413, "PAYLOAD_TOO_LARGE", f"Request body exceeds {MAX_BODY_BYTES} bytes.")
            return self.rfile.read(length)

        def _reset(self, request):
            if request.method != "POST":
                raise HttpError(405, "METHOD_NOT_ALLOWED", "Use POST.", {"Allow": "POST"})
            if request.headers.get(RESET_HEADER) != RESET_CONFIRMATION:
                raise HttpError(403, "RESET_NOT_CONFIRMED", f"Send {RESET_HEADER}: {RESET_CONFIRMATION}.")
            app.reset()
            return json_response(200, {"app": app.name, "reset": True, "synthetic_demo_data": True})

        def _send(self, response):
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            for key, value in {**SECURITY_HEADERS, **response.headers}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.body)

    return Handler


def create_server(app, host="127.0.0.1", port=None, enable_reset_endpoint=False, quiet=False):
    if host not in LOOPBACK_BIND_HOSTS:
        raise ValueError(f"Refusing to bind {app.name} to non-loopback host {host!r}; demo apps are local only.")
    app.ensure_seeded()
    handler = make_handler(app, enable_reset_endpoint=enable_reset_endpoint, quiet=quiet)
    server_class = ThreadingHTTPServer
    if ":" in host:
        import socket

        class IPv6Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6

        server_class = IPv6Server
    return server_class((host, app.default_port if port is None else port), handler)


def serve_in_thread(server):
    thread = threading.Thread(target=server.serve_forever, name=f"demo-{server.server_port}", daemon=True)
    thread.start()
    return thread


def main(app_class, argv=None):
    parser = argparse.ArgumentParser(description=f"{app_class.title} (synthetic demo app; local test fixture only)")
    parser.add_argument("--db", help="SQLite file (default: data/ inside the app directory)")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run the demo server on loopback")
    serve.add_argument("--host", default="127.0.0.1", help="Loopback address only (default 127.0.0.1)")
    serve.add_argument("--port", type=int, default=app_class.default_port)
    serve.add_argument(
        "--enable-reset-endpoint",
        action="store_true",
        help=f"Expose POST {RESET_PATH} for integration tests (off by default)",
    )
    sub.add_parser("reset", help="Wipe and reseed this demo app's synthetic data")
    args = parser.parse_args(argv)

    app = app_class(args.db)
    if args.command == "reset":
        app.reset()
        print(f"Reset synthetic demo data for {app.name}: {app.db_path}")
        return 0
    server = create_server(app, args.host, args.port, args.enable_reset_endpoint)
    print(f"{app.title} (synthetic demo data) on http://{args.host}:{server.server_port}/  db={app.db_path}")
    if args.enable_reset_endpoint:
        print(f"Reset endpoint enabled: POST {RESET_PATH} with header {RESET_HEADER}: {RESET_CONFIRMATION}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
