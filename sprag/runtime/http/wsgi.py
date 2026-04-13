"""Shared HTTP server helpers for SPRAG dev and dist serving."""

from __future__ import annotations

import cgi
import gzip
import json
import mimetypes
import os
import re
import sys
import time
import traceback
from collections import OrderedDict
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlparse

from gevent.pool import Pool
from gevent.pywsgi import WSGIServer

# Content types eligible for gzip compression.
_COMPRESSIBLE_TYPES = {
    "text/html",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/json",
    "text/plain",
    "text/xml",
    "application/xml",
    "image/svg+xml",
}

_GZIP_MIN_SIZE = 1024
_GZIP_LEVEL = 5
_GZIP_CACHE_MAX = 128
_gzip_cache: OrderedDict = OrderedDict()
_gzip_cache_lock = Lock()

from ..request import Request, UploadedFile
from ..routing import match_page_route, normalize_route_path
from ..rendering import render_mount, render_page
from ..session import commit_request_session, hydrate_request
from ..socket_bridge import controller_uses_socket_bridge
from ..server import ActionDispatchError, bus, dispatch_controller_action

SERVER_MODES = ("auto", "wsgi", "websocket")


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

        if method == "POST" and path == "/__sprag__/uploads":
            return self._handle_upload(environ, start_response)

        if method == "GET" and path == "/__sprag__/events":
            return self._handle_events(environ, start_response)

        if method == "GET":
            route_path = normalize_route_path(path.rstrip("/") or "/")
            matched_page = self._match_page(route_path)
            if matched_page is not None:
                return self._handle_page(environ, start_response, matched_page)
            mount = self._match_mount(route_path)
            if mount is not None:
                return self._handle_mount(environ, start_response, mount)
            return self._handle_static(environ, start_response, path)

        return self._respond(start_response, 405, "text/plain", b"Method Not Allowed")

    # -- Page rendering ------------------------------------------------------

    def _match_page(self, route_path):
        return match_page_route(self._sprag_app.pages(), route_path)

    def _match_mount(self, route_path):
        for _module_name, mount in self._sprag_app.mounts():
            if mount.path == route_path:
                return mount
        return None

    def _handle_page(self, environ, start_response, matched_page):
        page = matched_page.page
        parsed_path = environ.get("PATH_INFO", "/")
        parsed_qs = environ.get("QUERY_STRING", "")
        query = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed_qs).items()}
        headers = self._extract_headers(environ)
        request = hydrate_request(Request(
            path=normalize_route_path(parsed_path),
            params=matched_page.params,
            query=query,
            headers=headers,
            method="GET",
        ), app=self._sprag_app, raw_cookie=environ.get("HTTP_COOKIE"))

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
            return self._respond(
                start_response,
                500,
                "text/html; charset=utf-8",
                body,
                extra_headers=self._session_headers(request),
            )

        if result.render_error:
            print(f"[SPRAG] render error on {page.path}: {result.render_error}", file=sys.stderr)
        if result.data_error:
            print(f"[SPRAG] data error on {page.path}: {result.data_error}", file=sys.stderr)
        if result.redirect is not None:
            return self._respond_redirect(
                start_response,
                result.redirect.location,
                status=result.redirect.status,
                extra_headers=self._session_headers(request),
            )

        body = result.html.encode("utf-8")
        return self._respond_gzip(
            environ,
            start_response,
            result.status,
            "text/html; charset=utf-8",
            body,
            extra_headers=self._session_headers(request),
        )

    # -- Mount rendering ----------------------------------------------------

    def _handle_mount(self, environ, start_response, mount):
        parsed_path = environ.get("PATH_INFO", "/")
        parsed_qs = environ.get("QUERY_STRING", "")
        query = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed_qs).items()}
        headers = self._extract_headers(environ)
        request = hydrate_request(Request(
            path=parsed_path,
            query=query,
            headers=headers,
            method="GET",
        ), app=self._sprag_app, raw_cookie=environ.get("HTTP_COOKIE"))

        try:
            result = render_mount(mount, request=request, app=self._sprag_app)
        except Exception:
            tb = traceback.format_exc()
            print(f"[SPRAG] mount crash on {mount.path}:\n{tb}", file=sys.stderr)
            body = (
                f"<!DOCTYPE html><html><body>"
                f"<h1>SPRAG Mount Error</h1><pre>{tb}</pre>"
                f"</body></html>"
            ).encode("utf-8")
            return self._respond(
                start_response,
                500,
                "text/html; charset=utf-8",
                body,
                extra_headers=self._session_headers(request),
            )

        if result.data_error:
            print(f"[SPRAG] mount data error on {mount.path}: {result.data_error}", file=sys.stderr)
        if result.redirect is not None:
            return self._respond_redirect(
                start_response,
                result.redirect.location,
                status=result.redirect.status,
                extra_headers=self._session_headers(request),
            )

        body = result.html.encode("utf-8")
        return self._respond_gzip(
            environ,
            start_response,
            result.status,
            "text/html; charset=utf-8",
            body,
            extra_headers=self._session_headers(request),
        )

    # -- Action dispatch -----------------------------------------------------

    def _handle_action(self, environ, start_response):
        headers = self._extract_headers(environ)
        request = hydrate_request(Request(
            path="/",
            method="POST",
            headers=headers,
        ), app=self._sprag_app, raw_cookie=environ.get("HTTP_COOKIE"))
        try:
            content_length = int(environ.get("CONTENT_LENGTH", "0"))
        except ValueError:
            return self._json_response(
                start_response, 400,
                {"ok": False, "error": "Invalid Content-Length header."},
                extra_headers=self._session_headers(request),
            )

        raw_body = environ["wsgi.input"].read(content_length) if content_length else b""
        try:
            request_body = json.loads(raw_body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._json_response(
                start_response, 400,
                {"ok": False, "error": "Invalid JSON request body."},
                extra_headers=self._session_headers(request),
            )

        route_path = request_body.get("route")
        action_name = request_body.get("action")
        payload = request_body.get("payload")
        request.path = route_path or "/"
        request.body = raw_body

        try:
            result = dispatch_controller_action(
                self._sprag_app.pages(),
                route_path=route_path,
                action_name=action_name,
                payload=payload,
                request=request,
                app=self._sprag_app,
                mounts=self._sprag_app.mounts(),
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
                extra_headers=self._session_headers(request),
            )

        return self._json_response(
            start_response, 200,
            {
                "ok": result.ok,
                "route": route_path,
                "action": action_name,
                "value": result.value,
                "error": result.error,
                "status": result.status,
                "redirect": result.redirect.as_payload() if result.redirect is not None else None,
            },
            extra_headers=self._session_headers(request),
        )

    def _handle_upload(self, environ, start_response):
        headers = self._extract_headers(environ)
        request = hydrate_request(Request(
            path="/",
            method="POST",
            headers=headers,
        ), app=self._sprag_app, raw_cookie=environ.get("HTTP_COOKIE"))
        content_type = environ.get("CONTENT_TYPE", "")
        if "multipart/form-data" not in content_type.lower():
            return self._json_response(
                start_response,
                415,
                {
                    "ok": False,
                    "error": "SPRAG uploads require multipart/form-data.",
                },
                extra_headers=self._session_headers(request),
            )

        try:
            route_path, action_name, payload, form, files = self._parse_upload_request(environ)
        except ValueError as exc:
            return self._json_response(
                start_response,
                400,
                {
                    "ok": False,
                    "error": str(exc),
                },
                extra_headers=self._session_headers(request),
            )

        request.path = route_path or "/"
        request.content_type = content_type
        request.form = form
        request.files = files

        try:
            result = dispatch_controller_action(
                self._sprag_app.pages(),
                route_path=route_path,
                action_name=action_name,
                payload=payload,
                request=request,
                app=self._sprag_app,
                mounts=self._sprag_app.mounts(),
            )
        except ActionDispatchError as exc:
            return self._json_response(
                start_response,
                exc.status_code,
                {
                    "ok": False,
                    "route": route_path,
                    "action": action_name,
                    "error": str(exc),
                },
                extra_headers=self._session_headers(request),
            )

        return self._json_response(
            start_response,
            200,
            {
                "ok": result.ok,
                "route": route_path,
                "action": action_name,
                "value": result.value,
                "error": result.error,
                "status": result.status,
                "redirect": result.redirect.as_payload() if result.redirect is not None else None,
            },
            extra_headers=self._session_headers(request),
        )

    def _parse_upload_request(self, environ):
        form_storage = cgi.FieldStorage(
            fp=environ["wsgi.input"],
            environ=environ,
            keep_blank_values=True,
        )
        raw_fields = {}
        raw_files = {}
        for item in form_storage.list or []:
            if not getattr(item, "name", None):
                continue
            if getattr(item, "filename", None):
                upload = UploadedFile(
                    name=item.name,
                    filename=item.filename,
                    content_type=getattr(item, "type", None),
                    data=item.file.read() if item.file is not None else b"",
                    headers=dict(item.headers or {}),
                )
                self._append_multipart_value(raw_files, item.name, upload)
                continue
            self._append_multipart_value(raw_fields, item.name, item.value)

        route_path = self._first_multipart_value(raw_fields.pop("__sprag_route", None))
        action_name = self._first_multipart_value(raw_fields.pop("__sprag_action", None))
        payload_raw = self._first_multipart_value(raw_fields.pop("__sprag_payload", None))
        form = self._collapse_multipart_map(raw_fields)
        files = self._collapse_multipart_map(raw_files)

        if payload_raw is None:
            payload = form
        else:
            try:
                payload = json.loads(payload_raw or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError("Invalid upload payload JSON.") from exc

        return route_path, action_name, payload, form, files

    @staticmethod
    def _append_multipart_value(target, name, value):
        if name in target:
            current = target[name]
            if isinstance(current, list):
                current.append(value)
            else:
                target[name] = [current, value]
            return
        target[name] = value

    @staticmethod
    def _first_multipart_value(value):
        if isinstance(value, list):
            return value[0] if value else None
        return value

    @classmethod
    def _collapse_multipart_map(cls, values):
        collapsed = {}
        for key, value in values.items():
            if isinstance(value, list):
                collapsed[key] = value
            else:
                collapsed[key] = value
        return collapsed

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
            cache_headers = _static_cache_headers(file_path, environ)
            if cache_headers is None:
                return self._respond(start_response, 304, "text/plain", b"",
                                     extra_headers=[("Content-Length", "0")])
            # Serve pre-compressed .gz file if client accepts and it exists
            if _client_accepts_gzip(environ):
                gz_path = file_path.with_name(file_path.name + ".gz")
                if gz_path.is_file():
                    body = gz_path.read_bytes()
                    return self._respond(
                        start_response, 200, content_type, body,
                        extra_headers=[
                            ("Content-Encoding", "gzip"),
                            ("Vary", "Accept-Encoding"),
                            *cache_headers,
                        ],
                    )
            body = file_path.read_bytes()
            return self._respond(start_response, 200, content_type, body,
                                 extra_headers=cache_headers)

        # Try index.html inside directory
        index = file_path / "index.html" if file_path.is_dir() else None
        if index and index.is_file():
            cache_headers = _static_cache_headers(index, environ)
            if cache_headers is None:
                return self._respond(start_response, 304, "text/plain", b"",
                                     extra_headers=[("Content-Length", "0")])
            body = index.read_bytes()
            return self._respond(start_response, 200, "text/html; charset=utf-8", body,
                                 extra_headers=cache_headers)

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

    def _respond(self, start_response, status, content_type, body, *, extra_headers=None, environ=None):
        headers = [
            ("Content-Type", content_type),
        ]
        if extra_headers:
            if isinstance(extra_headers, tuple):
                headers.append(extra_headers)
            else:
                headers.extend(extra_headers)

        # Runtime gzip for eligible dynamic responses (not already compressed)
        already_encoded = any(k.lower() == "content-encoding" for k, v in headers)
        if (
            not already_encoded
            and environ is not None
            and len(body) >= _GZIP_MIN_SIZE
            and _client_accepts_gzip(environ)
            and _is_compressible_type(content_type)
        ):
            compressed = _gzip_compress(body)
            if compressed is not None and len(compressed) < len(body):
                body = compressed
                headers.append(("Content-Encoding", "gzip"))
                headers.append(("Vary", "Accept-Encoding"))

        headers.append(("Content-Length", str(len(body))))
        status_line = f"{status} {_STATUS_PHRASES.get(status, 'Unknown')}"
        start_response(status_line, headers)
        return [body]

    def _respond_redirect(self, start_response, location, *, status=302, extra_headers=None):
        headers = [
            ("Location", location),
            ("Content-Length", "0"),
        ]
        if extra_headers:
            if isinstance(extra_headers, tuple):
                headers.append(extra_headers)
            else:
                headers.extend(extra_headers)
        status_line = f"{status} {_STATUS_PHRASES.get(status, 'Redirect')}"
        start_response(status_line, headers)
        return [b""]

    def _respond_gzip(self, environ, start_response, status, content_type, body, *, extra_headers=None):
        return self._respond(
            start_response, status, content_type, body,
            extra_headers=extra_headers, environ=environ,
        )

    def _json_response(self, start_response, status, payload, *, extra_headers=None):
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        return self._respond(
            start_response,
            status,
            "application/json; charset=utf-8",
            body,
            extra_headers=extra_headers,
        )

    def _session_headers(self, request):
        headers = commit_request_session(request, app=self._sprag_app)
        return headers or None


_STATUS_PHRASES = {
    200: "OK",
    301: "Moved Permanently",
    302: "Found",
    303: "See Other",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    415: "Unsupported Media Type",
    500: "Internal Server Error",
}


def serve_sprag_app(
    app,
    directory,
    *,
    host="127.0.0.1",
    port=8000,
    max_workers=16,
    banner=None,
    server_mode=None,
):
    """Serve a SPRAG app with gevent, bounded concurrency, and action dispatch."""
    did_boot = False
    if hasattr(app, "boot") and not getattr(app, "_booted", False):
        app.boot()
        did_boot = True

    wsgi_app = SpragWSGIApp(app, directory)
    resolved_server_mode = resolve_server_mode(app, server_mode)
    if app is not None:
        setattr(app, "_resolved_server_mode", resolved_server_mode)
    # gevent's WSGIServer uses ``spawn`` as a *callable* — pass a Pool, not
    # a semaphore. A Pool both bounds concurrency and is callable as
    # ``pool(fn, *args)`` which is what the server expects.
    pool = Pool(max_workers)
    server_kwargs = {
        "spawn": pool,
        "log": None,
    }
    if resolved_server_mode == "websocket":
        try:
            from geventwebsocket.handler import WebSocketHandler
            from geventwebsocket.resource import Resource, WebSocketApplication
        except ImportError as exc:
            raise RuntimeError(
                "SPRAG server_mode='websocket' requires the 'gevent-websocket' package. "
                "Install it or switch the app/server back to server_mode='wsgi'."
            ) from exc
        server_kwargs["handler_class"] = WebSocketHandler
        socket_bridge = app.socket_bridge()

        class _SpragSocketApplication(WebSocketApplication):
            bridge = socket_bridge

            def handle(self):
                self.bridge.handle_websocket(self.ws)

        app_resource = Resource(
            [
                (r"^/__sprag__/socket$", _SpragSocketApplication),
                (r"^/.*", wsgi_app),
            ]
        )
        server = WSGIServer((host, port), app_resource, **server_kwargs)
    else:
        server = WSGIServer((host, port), wsgi_app, **server_kwargs)

    if banner:
        for line in banner:
            print(line)

    try:
        server.serve_forever()
    finally:
        if app is not None:
            setattr(app, "_resolved_server_mode", None)
        if did_boot and hasattr(app, "shutdown"):
            app.shutdown()


def resolve_server_mode(app, override=None):
    mode = override or getattr(app, "server_mode", "auto")
    if mode not in SERVER_MODES:
        raise ValueError(
            f"Unknown SPRAG server mode {mode!r}. Expected one of: {', '.join(SERVER_MODES)}."
        )
    if mode != "auto":
        return mode
    return "websocket" if app_uses_socket_bridge(app) else "wsgi"


_HASHED_FILENAME_RE = re.compile(r'\.[0-9a-f]{8}\.\w+$')


def _is_hashed_filename(name: str) -> bool:
    return bool(_HASHED_FILENAME_RE.search(name))


def _static_cache_headers(file_path: Path, environ: dict) -> list[tuple[str, str]] | None:
    """Return cache headers for a static file, or ``None`` for a 304 response.

    Content-hashed files get immutable caching.  HTML gets ``no-cache``
    with an ETag for conditional revalidation.  Everything else gets a
    short max-age with an ETag.

    Returns ``None`` when the client's conditional headers (``If-None-Match``
    or ``If-Modified-Since``) match — the caller should respond with 304.
    """
    stat = file_path.stat()
    mtime = stat.st_mtime
    last_modified = formatdate(mtime, usegmt=True)
    etag = f'W/"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
    name = file_path.name

    # Check conditional request headers for non-hashed files.
    if not _is_hashed_filename(name):
        if_none_match = environ.get("HTTP_IF_NONE_MATCH", "")
        if if_none_match and etag in if_none_match:
            return None
        if_modified = environ.get("HTTP_IF_MODIFIED_SINCE", "")
        if if_modified:
            try:
                client_mtime = parsedate_to_datetime(if_modified).timestamp()
                if mtime <= client_mtime:
                    return None
            except (ValueError, TypeError):
                pass

    headers = [("Last-Modified", last_modified)]
    if _is_hashed_filename(name):
        headers.append(("Cache-Control", "public, max-age=31536000, immutable"))
    elif name.endswith(".html"):
        headers.extend([
            ("Cache-Control", "no-cache"),
            ("ETag", etag),
        ])
    else:
        headers.extend([
            ("Cache-Control", "public, max-age=60"),
            ("ETag", etag),
        ])
    return headers


def _client_accepts_gzip(environ) -> bool:
    accept = environ.get("HTTP_ACCEPT_ENCODING", "")
    return "gzip" in accept


def _is_compressible_type(content_type: str) -> bool:
    base_type = content_type.split(";")[0].strip().lower()
    return base_type in _COMPRESSIBLE_TYPES


def _gzip_compress(data: bytes) -> bytes | None:
    try:
        return gzip.compress(data, compresslevel=_GZIP_LEVEL)
    except Exception:
        return None


def app_uses_socket_bridge(app) -> bool:
    """Return True when discovered SPRAG surfaces imply websocket transport.

    Auto mode should stay invisible for normal SPRAG apps. If a project grows
    a real socket bridge, SPRAG can detect that from the app itself instead of
    forcing the user to opt into a server flag just to make the runtime honest.
    """
    if app is None:
        return False

    pages = []
    mounts = []
    if hasattr(app, "pages"):
        try:
            pages = list(app.pages() or [])
        except Exception:
            pages = []
    if hasattr(app, "mounts"):
        try:
            mounts = list(app.mounts() or [])
        except Exception:
            mounts = []

    for _module_name, page in pages:
        if _controller_uses_sockets(getattr(page, "controller", None)):
            return True
    for _module_name, mount in mounts:
        if _controller_uses_sockets(getattr(mount, "boot", None)):
            return True
    return False


def _controller_uses_sockets(controller_cls) -> bool:
    return controller_uses_socket_bridge(controller_cls)
