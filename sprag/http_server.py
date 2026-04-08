"""Shared HTTP server helpers for SPRAG dev and dist serving."""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from gevent.pool import Pool
from gevent.pywsgi import WSGIServer

from .request import Request
from .runtime import render_page
from .server import ActionDispatchError, bus, dispatch_controller_action


class SpragWSGIApp:
    """WSGI application for SPRAG with page rendering, action dispatch, and static assets."""

    def __init__(self, app, directory):
        self._sprag_app = app
        self._directory = Path(directory)

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")

        if method == "POST" and path == "/__sprag__/actions":
            return self._handle_action(environ, start_response)

        if method == "GET" and path == "/__sprag__/events":
            return self._handle_events(environ, start_response)

        if method == "GET":
            route_path = path.rstrip("/") or "/"
            page = self._match_page(route_path)
            if page is not None:
                return self._handle_page(environ, start_response, page)
            return self._handle_static(environ, start_response, path)

        return self._respond(start_response, 405, "text/plain", b"Method Not Allowed")

    # -- Page rendering ------------------------------------------------------

    def _match_page(self, route_path):
        for _module_name, page in self._sprag_app.pages():
            if page.path == route_path:
                return page
        return None

    def _handle_page(self, environ, start_response, page):
        parsed_path = environ.get("PATH_INFO", "/")
        parsed_qs = environ.get("QUERY_STRING", "")
        query = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed_qs).items()}
        headers = self._extract_headers(environ)

        request = Request(
            path=parsed_path,
            query=query,
            headers=headers,
            method="GET",
        )

        try:
            result = render_page(page, request=request, app=self._sprag_app)
        except Exception:
            tb = traceback.format_exc()
            print(f"[SPRAG] render crash on {page.path}:\n{tb}", file=sys.stderr)
            body = (
                f"<!DOCTYPE html><html><body>"
                f"<h1>SPRAG Render Error</h1><pre>{tb}</pre>"
                f"</body></html>"
            ).encode("utf-8")
            return self._respond(start_response, 500, "text/html; charset=utf-8", body)

        if result.render_error:
            print(f"[SPRAG] render error on {page.path}: {result.render_error}", file=sys.stderr)
        if result.data_error:
            print(f"[SPRAG] data error on {page.path}: {result.data_error}", file=sys.stderr)

        body = result.html.encode("utf-8")
        return self._respond(start_response, 200, "text/html; charset=utf-8", body)

    # -- Action dispatch -----------------------------------------------------

    def _handle_action(self, environ, start_response):
        try:
            content_length = int(environ.get("CONTENT_LENGTH", "0"))
        except ValueError:
            return self._json_response(
                start_response, 400,
                {"ok": False, "error": "Invalid Content-Length header."},
            )

        raw_body = environ["wsgi.input"].read(content_length) if content_length else b""
        try:
            request_body = json.loads(raw_body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._json_response(
                start_response, 400,
                {"ok": False, "error": "Invalid JSON request body."},
            )

        route_path = request_body.get("route")
        action_name = request_body.get("action")
        payload = request_body.get("payload")
        headers = self._extract_headers(environ)

        request = Request(
            path=route_path or "/",
            method="POST",
            headers=headers,
            body=raw_body,
        )

        try:
            result = dispatch_controller_action(
                self._sprag_app.pages(),
                route_path=route_path,
                action_name=action_name,
                payload=payload,
                request=request,
                app=self._sprag_app,
            )
        except ActionDispatchError as exc:
            return self._json_response(
                start_response, exc.status_code,
                {
                    "ok": False,
                    "route": route_path,
                    "action": action_name,
                    "error": str(exc),
                },
            )

        return self._json_response(
            start_response, result.status,
            {
                "ok": result.ok,
                "route": route_path,
                "action": action_name,
                "value": result.value,
                "error": result.error,
                "status": result.status,
            },
        )

    # -- Server-Sent Events (bus bridge) --------------------------------------

    def _handle_events(self, environ, start_response):
        """Stream Specter bus events to the browser via SSE."""
        import gevent
        from gevent.event import Event
        from gevent.queue import Queue

        event_queue = Queue()

        def _on_bus_event(data):
            event_queue.put(data)

        unsub = bus.on("sprag:broadcast", _on_bus_event)

        def event_stream():
            yield b"retry: 1000\n\n"
            try:
                while True:
                    try:
                        data = event_queue.get(timeout=30)
                        payload = json.dumps(data, sort_keys=True) if not isinstance(data, str) else data
                        yield f"data: {payload}\n\n".encode("utf-8")
                    except gevent.queue.Empty:
                        yield b": keepalive\n\n"
            except GeneratorExit:
                pass
            finally:
                unsub()

        start_response("200 OK", [
            ("Content-Type", "text/event-stream"),
            ("Cache-Control", "no-cache"),
            ("Connection", "keep-alive"),
            ("X-Accel-Buffering", "no"),
        ])
        return event_stream()

    # -- Static file serving -------------------------------------------------

    def _handle_static(self, environ, start_response, path):
        # Resolve to filesystem path under the build directory
        relative = path.lstrip("/")
        file_path = self._directory / relative

        # Prevent directory traversal
        try:
            file_path = file_path.resolve()
            if not str(file_path).startswith(str(self._directory.resolve())):
                return self._respond(start_response, 403, "text/plain", b"Forbidden")
        except (OSError, ValueError):
            return self._respond(start_response, 400, "text/plain", b"Bad Request")

        if file_path.is_file():
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            body = file_path.read_bytes()
            return self._respond(start_response, 200, content_type, body)

        # Try index.html inside directory
        index = file_path / "index.html" if file_path.is_dir() else None
        if index and index.is_file():
            body = index.read_bytes()
            return self._respond(start_response, 200, "text/html; charset=utf-8", body)

        return self._respond(start_response, 404, "text/plain", b"Not Found")

    # -- Helpers -------------------------------------------------------------

    def _extract_headers(self, environ):
        headers = {}
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                header_name = key[5:].replace("_", "-").title()
                headers[header_name] = value
            elif key == "CONTENT_TYPE":
                headers["Content-Type"] = value
            elif key == "CONTENT_LENGTH":
                headers["Content-Length"] = value
        return headers

    def _respond(self, start_response, status, content_type, body):
        status_line = f"{status} {_STATUS_PHRASES.get(status, 'Unknown')}"
        start_response(status_line, [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
        ])
        return [body]

    def _json_response(self, start_response, status, payload):
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        return self._respond(start_response, status, "application/json; charset=utf-8", body)


_STATUS_PHRASES = {
    200: "OK",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
}


def serve_sprag_app(app, directory, *, host="127.0.0.1", port=8000, max_workers=16, banner=None):
    """Serve a SPRAG app with gevent, bounded concurrency, and action dispatch."""
    did_boot = False
    if hasattr(app, "boot") and not getattr(app, "_booted", False):
        app.boot()
        did_boot = True

    wsgi_app = SpragWSGIApp(app, directory)
    # gevent's WSGIServer uses ``spawn`` as a *callable* — pass a Pool, not
    # a semaphore. A Pool both bounds concurrency and is callable as
    # ``pool(fn, *args)`` which is what the server expects.
    pool = Pool(max_workers)

    server = WSGIServer(
        (host, port),
        wsgi_app,
        spawn=pool,
        log=None,
    )

    if banner:
        for line in banner:
            print(line)

    try:
        server.serve_forever()
    finally:
        if did_boot and hasattr(app, "shutdown"):
            app.shutdown()
