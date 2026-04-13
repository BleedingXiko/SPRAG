import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sprag import (
    Component,
    Controller,
    Field,
    InMemorySessionStore,
    Request,
    Schema,
    Screen,
    action,
    mount,
    page,
    requires_auth,
    ui,
)
from sprag.dev.scaffold import scaffold_project
from sprag.runtime.http.wsgi import SpragWSGIApp
from sprag.runtime.rendering import render_mount, render_page
from sprag.runtime.server import dispatch_controller_action


def _multipart_body(boundary, *, fields=None, files=None):
    fields = fields or {}
    files = files or []
    chunks = []
    for name, value in fields.items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode("utf-8"),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                    str(item).encode("utf-8"),
                    b"\r\n",
                ]
            )
    for name, filename, content_type, data in files:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                data,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks)


def _session_id_from_cookie(header):
    cookie = header.split(";", 1)[0]
    return cookie.split("=", 1)[1]


class DemoAuthService:
    name = "auth"

    def __init__(self):
        self._users = {
            "ada": {
                "id": "user-ada",
                "username": "ada",
                "password": "engine",
                "name": "Ada",
                "secret": "server-only-secret",
            }
        }
        self._by_id = {user["id"]: dict(user) for user in self._users.values()}

    def authenticate(self, username, password):
        user = self._users.get(str(username or "").strip().lower())
        if user is None or user["password"] != password:
            return None
        return dict(user)

    def viewer_for(self, user):
        return {
            "id": user["id"],
            "username": user["username"],
            "name": user["name"],
        }

    def load_user(self, session, request):
        user_id = session.get("auth_user_id")
        if not user_id:
            return None
        user = self._by_id.get(user_id)
        return dict(user) if user is not None else None

    def public_snapshot(self, user, session, request):
        if user is None:
            return {}
        return {"viewer": self.viewer_for(user)}

    def login_session(self, user, session, request, extra_session=None):
        session.set("auth_user_id", user["id"])
        if extra_session:
            session.patch(extra_session)


class AuthScreen(Screen):
    def render(self, data):
        auth = json.dumps(data["__sprag_auth__"], sort_keys=True)
        return ui.main(
            ui.div(data.get("viewer_name", "anonymous"), data_role="viewer-name"),
            ui.div(auth, data_role="auth-snapshot"),
        )


class MountRoot(Component):
    def render(self, props=None):
        return ui.div("mount")


class LoginController(Controller):
    route = "/login"

    def load(self):
        return {"title": "login"}

    @action(
        name="login",
        schema=Schema(
            "login",
            {
                "username": Field(str, required=True),
                "password": Field(str, required=True),
                "next": Field(str, required=False, default="/protected"),
            },
        ),
    )
    def submit_login(self, username, password, next="/protected"):
        auth = self.app.providers["auth"]
        user = auth.authenticate(username, password)
        if user is None:
            return {"authenticated": False, "error": "bad credentials"}
        self.login(user, viewer=auth.viewer_for(user), extra_session={"note": "signed-in"})
        return self.redirect(next, status=303)


@requires_auth()
class ProtectedController(Controller):
    route = "/protected"

    def load(self):
        return {
            "viewer_name": self.request.user["name"],
            "session_note": self.request.session.get("note"),
        }

    @action(schema=Schema("whoami", {}))
    def whoami(self):
        return {
            "viewer_name": self.request.user["name"],
            "session_note": self.request.session.get("note"),
        }

    @action(schema=Schema("set_note", {"note": Field(str, required=True)}))
    def set_note(self, note):
        self.request.session.set("note", note)
        return {"note": note}

    @action(name="logout", schema=Schema("logout", {}))
    def end_session(self):
        self.logout()
        return {
            "session_id": self.request.session_id,
            "authenticated": self.request.user is not None,
        }

    @action(
        schema=Schema("upload_doc", {"title": Field(str, required=True)}),
    )
    def upload_doc(self, title):
        return {
            "title": title,
            "viewer_name": self.request.user["name"],
        }


class MethodGuardController(Controller):
    route = "/method-guard"

    def load(self):
        return {"viewer_name": "public"}

    @action(schema=Schema("public", {}))
    def public(self):
        return {"public": True}

    @requires_auth()
    @action(schema=Schema("secret", {}))
    def secret(self):
        return {"viewer_name": self.request.user["name"]}


@requires_auth()
class GuardedMountBoot(Controller):
    route = "/guarded-mount"

    def load(self):
        return {"viewer_name": self.request.user["name"]}


class AuthContractTests(unittest.TestCase):
    def setUp(self):
        self.session_store = InMemorySessionStore()
        self.auth = DemoAuthService()
        self.login_page = page(
            path="/login",
            controller=LoginController,
            screen=AuthScreen,
            mode="document",
        )
        self.protected_page = page(
            path="/protected",
            controller=ProtectedController,
            screen=AuthScreen,
            mode="document",
        )
        self.method_page = page(
            path="/method-guard",
            controller=MethodGuardController,
            screen=AuthScreen,
            mode="document",
        )
        self.guarded_mount = mount(
            "/guarded-mount",
            component=MountRoot,
            boot=GuardedMountBoot,
        )
        self.pages = [
            ("app.routes.login.page", self.login_page),
            ("app.routes.protected.page", self.protected_page),
            ("app.routes.method_guard.page", self.method_page),
        ]
        self.mounts = [("app.mounts.guarded.mount", self.guarded_mount)]
        self.app = SimpleNamespace(
            providers={"session_store": self.session_store, "auth": self.auth},
            pages=lambda: self.pages,
            mounts=lambda: self.mounts,
        )
        self.wsgi = SpragWSGIApp(self.app, Path("."))

    def _wsgi_call(self, *, method, path, body=b"", content_type=None, cookie=None):
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8000",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "wsgi.url_scheme": "http",
        }
        if content_type:
            environ["CONTENT_TYPE"] = content_type
        if cookie:
            environ["HTTP_COOKIE"] = cookie
        started = {}

        def start_response(status, headers):
            started["status"] = status
            started["headers"] = dict(headers)

        response = b"".join(self.wsgi(environ, start_response))
        return started["status"], started["headers"], response

    def _login_cookie(self):
        _, headers, _ = self._wsgi_call(method="GET", path="/login")
        initial = _session_id_from_cookie(headers["Set-Cookie"])
        body = json.dumps(
            {
                "route": "/login",
                "action": "login",
                "payload": {
                    "username": "ada",
                    "password": "engine",
                    "next": "/protected",
                },
            }
        ).encode("utf-8")
        status, headers, response = self._wsgi_call(
            method="POST",
            path="/__sprag__/actions",
            body=body,
            content_type="application/json",
            cookie=f"SPRAG_SID={initial}",
        )
        self.assertEqual(status, "200 OK")
        payload = json.loads(response.decode("utf-8"))
        self.assertEqual(payload["redirect"]["location"], "/protected")
        rotated = _session_id_from_cookie(headers["Set-Cookie"])
        return initial, rotated

    def test_session_lifecycle_covers_create_reuse_rotate_invalidate_and_cookie_rewrite(self):
        status, headers, _body = self._wsgi_call(method="GET", path="/login")
        self.assertEqual(status, "200 OK")
        first_session_id = _session_id_from_cookie(headers["Set-Cookie"])

        status, headers, _body = self._wsgi_call(
            method="GET",
            path="/login",
            cookie=f"SPRAG_SID={first_session_id}",
        )
        self.assertEqual(status, "200 OK")
        self.assertNotIn("Set-Cookie", headers)

        login_session_id, rotated_session_id = self._login_cookie()
        self.assertNotEqual(rotated_session_id, login_session_id)
        self.assertEqual(self.session_store.load(first_session_id), {})
        self.assertEqual(self.session_store.load(login_session_id), {})
        self.assertEqual(self.session_store.load(rotated_session_id)["auth_user_id"], "user-ada")

        body = json.dumps(
            {
                "route": "/protected",
                "action": "set_note",
                "payload": {"note": "updated"},
            }
        ).encode("utf-8")
        status, headers, response = self._wsgi_call(
            method="POST",
            path="/__sprag__/actions",
            body=body,
            content_type="application/json",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        payload = json.loads(response.decode("utf-8"))
        self.assertEqual(status, "200 OK")
        self.assertEqual(payload["value"]["note"], "updated")
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(self.session_store.load(rotated_session_id)["note"], "updated")

        body = json.dumps(
            {
                "route": "/protected",
                "action": "logout",
                "payload": {},
            }
        ).encode("utf-8")
        status, headers, response = self._wsgi_call(
            method="POST",
            path="/__sprag__/actions",
            body=body,
            content_type="application/json",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        payload = json.loads(response.decode("utf-8"))
        invalidated_session_id = _session_id_from_cookie(headers["Set-Cookie"])
        self.assertEqual(status, "200 OK")
        self.assertFalse(payload["value"]["authenticated"])
        self.assertEqual(payload["value"]["session_id"], invalidated_session_id)
        self.assertNotEqual(invalidated_session_id, rotated_session_id)
        self.assertEqual(self.session_store.load(rotated_session_id), {})
        self.assertEqual(self.session_store.load(invalidated_session_id), {})

    def test_render_page_mount_and_action_requests_expose_request_session_and_user(self):
        _, rotated_session_id = self._login_cookie()

        page_request = Request(
            path="/protected",
            headers={"Cookie": f"SPRAG_SID={rotated_session_id}"},
        )
        page_result = render_page(self.protected_page, request=page_request, app=self.app)
        self.assertEqual(page_result.data["viewer_name"], "Ada")
        self.assertEqual(page_request.user["username"], "ada")
        self.assertEqual(page_request.session.get("note"), "signed-in")

        mount_request = Request(
            path="/guarded-mount",
            headers={"Cookie": f"SPRAG_SID={rotated_session_id}"},
        )
        mount_result = render_mount(self.guarded_mount, request=mount_request, app=self.app)
        self.assertEqual(mount_result.data["viewer_name"], "Ada")
        self.assertEqual(mount_request.user["username"], "ada")
        self.assertEqual(mount_request.session.get("note"), "signed-in")

        action_request = Request(
            path="/protected",
            method="POST",
            headers={"Cookie": f"SPRAG_SID={rotated_session_id}"},
        )
        action_result = dispatch_controller_action(
            self.pages,
            route_path="/protected",
            action_name="whoami",
            payload={},
            request=action_request,
            app=self.app,
            mounts=self.mounts,
        )
        self.assertEqual(action_result.value["viewer_name"], "Ada")
        self.assertEqual(action_request.user["username"], "ada")
        self.assertEqual(action_request.session.get("note"), "signed-in")

    def test_requires_auth_redirects_pages_and_mounts_and_returns_401_for_actions_and_uploads(self):
        status, headers, _body = self._wsgi_call(method="GET", path="/protected")
        self.assertEqual(status, "302 Found")
        self.assertEqual(headers["Location"], "/login?next=%2Fprotected")

        status, headers, _body = self._wsgi_call(method="GET", path="/guarded-mount")
        self.assertEqual(status, "302 Found")
        self.assertEqual(headers["Location"], "/login?next=%2Fguarded-mount")

        body = json.dumps(
            {
                "route": "/protected",
                "action": "whoami",
                "payload": {},
            }
        ).encode("utf-8")
        status, _headers, response = self._wsgi_call(
            method="POST",
            path="/__sprag__/actions",
            body=body,
            content_type="application/json",
        )
        payload = json.loads(response.decode("utf-8"))
        self.assertEqual(status, "401 Unauthorized")
        self.assertEqual(payload["error"], "Authentication required.")

        boundary = "sprag-auth-boundary"
        upload = _multipart_body(
            boundary,
            fields={
                "__sprag_route": "/protected",
                "__sprag_action": "upload_doc",
                "__sprag_payload": json.dumps({"title": "Secret file"}),
            },
            files=[("asset", "secret.txt", "text/plain", b"secret")],
        )
        status, _headers, response = self._wsgi_call(
            method="POST",
            path="/__sprag__/uploads",
            body=upload,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        payload = json.loads(response.decode("utf-8"))
        self.assertEqual(status, "401 Unauthorized")
        self.assertEqual(payload["error"], "Authentication required.")

    def test_class_level_and_method_level_requires_auth_both_work(self):
        body = json.dumps(
            {
                "route": "/method-guard",
                "action": "public",
                "payload": {},
            }
        ).encode("utf-8")
        status, _headers, response = self._wsgi_call(
            method="POST",
            path="/__sprag__/actions",
            body=body,
            content_type="application/json",
        )
        payload = json.loads(response.decode("utf-8"))
        self.assertEqual(status, "200 OK")
        self.assertTrue(payload["value"]["public"])

        body = json.dumps(
            {
                "route": "/method-guard",
                "action": "secret",
                "payload": {},
            }
        ).encode("utf-8")
        status, _headers, response = self._wsgi_call(
            method="POST",
            path="/__sprag__/actions",
            body=body,
            content_type="application/json",
        )
        payload = json.loads(response.decode("utf-8"))
        self.assertEqual(status, "401 Unauthorized")
        self.assertEqual(payload["error"], "Authentication required.")

        _, rotated_session_id = self._login_cookie()
        status, _headers, response = self._wsgi_call(
            method="POST",
            path="/__sprag__/actions",
            body=body,
            content_type="application/json",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        payload = json.loads(response.decode("utf-8"))
        self.assertEqual(status, "200 OK")
        self.assertEqual(payload["value"]["viewer_name"], "Ada")

    def test_render_payload_contains_sanitized_auth_snapshot_only(self):
        _, rotated_session_id = self._login_cookie()
        request = Request(
            path="/protected",
            headers={"Cookie": f"SPRAG_SID={rotated_session_id}"},
        )
        result = render_page(self.protected_page, request=request, app=self.app)
        self.assertEqual(
            result.data["__sprag_auth__"]["viewer"],
            {"id": "user-ada", "username": "ada", "name": "Ada"},
        )
        self.assertTrue(result.data["__sprag_auth__"]["authenticated"])
        self.assertIn('"auth": {"active_profile": null, "authenticated": true', result.html)
        self.assertIn('"viewer": {"id": "user-ada", "name": "Ada", "username": "ada"}', result.html)
        self.assertNotIn("server-only-secret", result.html)
        self.assertIn("data-role=\"auth-snapshot\"", result.html)

    def test_labs_template_scaffolds_and_builds_auth_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scaffold_project(root, "labs-auth", template="labs")
            sys.path.insert(0, str(root))
            try:
                for name in list(sys.modules):
                    if name == "app" or name.startswith("app."):
                        del sys.modules[name]
                imported = __import__("app", fromlist=["app"])
                labs_app = imported.app
                output = labs_app.build(root / "dist")
            finally:
                sys.path.remove(str(root))
                for name in list(sys.modules):
                    if name == "app" or name.startswith("app."):
                        del sys.modules[name]

            protected_path = root / "dist" / "auth-demo" / "protected" / "index.html"
            login_path = root / "dist" / "login" / "index.html"
            self.assertIn("/auth-demo/protected", [route["path"] for route in output["routes"]])
            self.assertTrue(login_path.exists())
            self.assertTrue(protected_path.exists())
            self.assertIn("Redirecting", protected_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
