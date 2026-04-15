import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sprag import (
    Component,
    Controller,
    Field,
    InMemorySessionStore,
    Request,
    Schema,
    Screen,
    SessionPolicy,
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
from sprag.runtime.socket_bridge import SpragSocketBridge


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
        self._profiles_by_user = {
            "user-ada": [
                {
                    "id": "profile-owner",
                    "slug": "owner",
                    "label": "Owner Workspace",
                    "roles": ("owner", "editor"),
                    "permissions": ("memo:publish", "profile:switch"),
                },
                {
                    "id": "profile-auditor",
                    "slug": "auditor",
                    "label": "Auditor Workspace",
                    "roles": ("auditor",),
                    "permissions": ("profile:switch",),
                },
            ]
        }
        self._profiles = {
            profile["id"]: dict(profile)
            for profiles in self._profiles_by_user.values()
            for profile in profiles
        }

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

    def _profiles_for_user(self, user):
        if not user:
            return []
        return [dict(profile) for profile in self._profiles_by_user.get(user["id"], [])]

    def _public_profile(self, profile):
        if not profile:
            return None
        return {
            "id": profile["id"],
            "slug": profile["slug"],
            "label": profile["label"],
        }

    def default_profile_for(self, user):
        profiles = self._profiles_for_user(user)
        return dict(profiles[0]) if profiles else None

    def resolve_profile(self, user, profile):
        if user is None or profile is None:
            return None
        if isinstance(profile, dict):
            candidate = profile.get("id") or profile.get("slug")
        else:
            candidate = str(profile).strip()
        if not candidate:
            return None
        for existing in self._profiles_for_user(user):
            if candidate in {existing["id"], existing["slug"]}:
                return dict(existing)
        return None

    def load_user(self, session, request):
        user_id = session.get("auth_user_id")
        if not user_id:
            return None
        user = self._by_id.get(user_id)
        return dict(user) if user is not None else None

    def load_active_profile(self, user, session, request):
        profile_id = session.get("active_profile_id")
        if not profile_id or user is None:
            return None
        profile = self.resolve_profile(user, profile_id)
        return dict(profile) if profile is not None else None

    def login_session(self, user, session, request, extra_session=None):
        session.set("auth_user_id", user["id"])
        if extra_session:
            session.patch(dict(extra_session))

    def set_active_profile(self, profile, user, session, request, extra_session=None):
        resolved = self.resolve_profile(user, profile)
        if resolved is None:
            session.delete("active_profile_id")
        else:
            session.set("active_profile_id", resolved["id"])
        if extra_session:
            session.patch(dict(extra_session))

    def authorize(
        self,
        user,
        active_profile,
        session,
        request,
        *,
        roles=None,
        permissions=None,
    ) -> bool:
        if user is None:
            return False
        if roles:
            if active_profile is None:
                return False
            profile_roles = set(active_profile.get("roles") or ())
            if not profile_roles.intersection(roles):
                return False
        if permissions:
            if active_profile is None:
                return False
            profile_permissions = set(active_profile.get("permissions") or ())
            if not all(permission in profile_permissions for permission in permissions):
                return False
        return True

    def public_snapshot(self, user, session, request):
        if user is None:
            return {}
        viewer = session.get("viewer")
        if not isinstance(viewer, dict):
            viewer = self.viewer_for(user)
        return {
            "viewer": viewer,
            "active_profile": self._public_profile(request.active_profile),
        }


class AuthScreen(Screen):
    def render(self, data):
        auth = json.dumps(data["__sprag_auth__"], sort_keys=True)
        return ui.main(
            ui.div(data.get("viewer_name", "anonymous"), data_role="viewer-name"),
            ui.div(data.get("active_profile_name", "no-profile"), data_role="profile-name"),
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
                "remember": Field(bool, required=False, default=False),
            },
        ),
    )
    def submit_login(self, username, password, next="/protected", remember=False):
        auth = self.app.providers["auth"]
        user = auth.authenticate(username, password)
        if user is None:
            return {"authenticated": False, "error": "bad credentials"}
        self.login(
            user,
            viewer=auth.viewer_for(user),
            active_profile=auth.default_profile_for(user),
            extra_session={"note": "signed-in"},
            remember=remember,
        )
        return self.redirect(next, status=303)


@requires_auth()
class ProtectedController(Controller):
    route = "/protected"
    last_socket_probe = None

    def load(self):
        return {
            "viewer_name": self.request.user["name"],
            "active_profile_name": (self.request.active_profile or {}).get("label"),
            "session_note": self.request.session.get("note"),
        }

    @action(schema=Schema("whoami", {}))
    def whoami(self):
        return {
            "viewer_name": self.request.user["name"],
            "active_profile": {
                "slug": (self.request.active_profile or {}).get("slug"),
                "label": (self.request.active_profile or {}).get("label"),
            },
            "session_note": self.request.session.get("note"),
        }

    @action(schema=Schema("set_note", {"note": Field(str, required=True)}))
    def set_note(self, note):
        self.request.session.set("note", note)
        return {"note": note}

    @action(schema=Schema("switch_profile", {"profile": Field(str, required=True)}))
    def switch_profile(self, profile):
        active_profile = self.set_active_profile(profile)
        return {
            "session_id": self.request.session_id,
            "active_profile": {
                "slug": (active_profile or {}).get("slug"),
                "label": (active_profile or {}).get("label"),
            },
        }

    @action(schema=Schema("clear_profile", {}))
    def clear_profile(self):
        self.set_active_profile(None)
        return {
            "active_profile": None,
            "session_id": self.request.session_id,
        }

    @requires_auth(roles=("owner", "auditor"))
    @action(schema=Schema("staff_area", {}))
    def staff_area(self):
        return {"allowed": True, "role_check": True}

    @requires_auth(permissions=("memo:publish", "profile:switch"))
    @action(schema=Schema("publish_memo", {}))
    def publish_memo(self):
        return {"published": True, "via": "decorator"}

    @action(schema=Schema("imperative_publish", {}))
    def imperative_publish(self):
        self.require_auth(permissions=("memo:publish", "profile:switch"))
        return {"published": True, "via": "imperative"}

    @action(schema=Schema("needs_profile", {}))
    def needs_profile(self):
        self.require_auth(require_active_profile=True)
        return {"profile": self.request.active_profile["slug"]}

    @action(name="logout", schema=Schema("logout", {}))
    def end_session(self):
        self.logout()
        return {
            "session_id": self.request.session_id,
            "authenticated": self.request.user is not None,
        }

    @requires_auth(permissions=("memo:publish", "profile:switch"))
    @action(
        schema=Schema("upload_doc", {"title": Field(str, required=True)}),
    )
    def upload_doc(self, title):
        return {
            "title": title,
            "viewer_name": self.request.user["name"],
        }

    def build_events(self, handler):
        handler.on("auth:probe", self.handle_socket_probe)

    def handle_socket_probe(self, payload=None):
        self.require_auth(require_active_profile=True)
        ProtectedController.last_socket_probe = {
            "username": self.request.user["username"],
            "profile_slug": self.request.active_profile["slug"],
        }


@requires_auth(permissions="memo:publish")
class MethodOverrideController(Controller):
    route = "/method-guard"

    def load(self):
        return {"viewer_name": self.request.user["name"]}

    @action(schema=Schema("default_guard", {}))
    def default_guard(self):
        return {"guard": "default"}

    @requires_auth()
    @action(schema=Schema("override_guard", {}))
    def override_guard(self):
        return {
            "guard": "override",
            "profile": (self.request.active_profile or {}).get("slug"),
        }


@requires_auth(permissions="memo:publish")
class GuardedMountBoot(Controller):
    route = "/guarded-mount"

    def load(self):
        return {
            "viewer_name": self.request.user["name"],
            "active_profile_name": (self.request.active_profile or {}).get("label"),
        }


class FakeWebSocket:
    def __init__(self, cookie=""):
        self.environ = {"HTTP_COOKIE": cookie}
        self.sent = []
        self.closed = False

    def send(self, message):
        self.sent.append(json.loads(message))

    def close(self):
        self.closed = True


class AuthContractTests(unittest.TestCase):
    def setUp(self):
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
            controller=MethodOverrideController,
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
        ProtectedController.last_socket_probe = None
        self.app = self._make_app()
        self.wsgi = SpragWSGIApp(self.app, Path("."))

    def _make_app(self, *, session_policy=None):
        return SimpleNamespace(
            providers={
                "session_store": InMemorySessionStore(),
                "auth": DemoAuthService(),
            },
            pages=lambda: self.pages,
            mounts=lambda: self.mounts,
            session_policy=session_policy,
        )

    def _wsgi_call(self, *, method, path, body=b"", content_type=None, cookie=None, app=None):
        app = app or self.app
        wsgi = self.wsgi if app is self.app else SpragWSGIApp(app, Path("."))
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

        response = b"".join(wsgi(environ, start_response))
        return started["status"], started["headers"], response

    def _login_cookie(self, *, remember=False, app=None):
        app = app or self.app
        _, headers, _ = self._wsgi_call(method="GET", path="/login", app=app)
        initial = _session_id_from_cookie(headers["Set-Cookie"])
        body = json.dumps(
            {
                "route": "/login",
                "action": "login",
                "payload": {
                    "username": "ada",
                    "password": "engine",
                    "next": "/protected",
                    "remember": remember,
                },
            }
        ).encode("utf-8")
        status, headers, response = self._wsgi_call(
            method="POST",
            path="/__sprag__/actions",
            body=body,
            content_type="application/json",
            cookie=f"SPRAG_SID={initial}",
            app=app,
        )
        self.assertEqual(status, "200 OK")
        payload = json.loads(response.decode("utf-8"))
        self.assertEqual(payload["redirect"]["location"], "/protected")
        rotated = _session_id_from_cookie(headers["Set-Cookie"])
        return initial, rotated, headers

    def _action(self, route, action_name, payload=None, *, cookie=None, app=None):
        body = json.dumps(
            {
                "route": route,
                "action": action_name,
                "payload": payload or {},
            }
        ).encode("utf-8")
        status, headers, response = self._wsgi_call(
            method="POST",
            path="/__sprag__/actions",
            body=body,
            content_type="application/json",
            cookie=cookie,
            app=app,
        )
        return status, headers, json.loads(response.decode("utf-8"))

    def test_request_active_profile_resolves_for_page_mount_action_and_socket_context(self):
        _, rotated_session_id, _headers = self._login_cookie()

        page_request = Request(
            path="/protected",
            headers={"Cookie": f"SPRAG_SID={rotated_session_id}"},
        )
        page_result = render_page(self.protected_page, request=page_request, app=self.app)
        self.assertEqual(page_result.data["viewer_name"], "Ada")
        self.assertEqual(page_result.data["active_profile_name"], "Owner Workspace")
        self.assertEqual(page_request.user["username"], "ada")
        self.assertEqual(page_request.active_profile["slug"], "owner")

        mount_request = Request(
            path="/guarded-mount",
            headers={"Cookie": f"SPRAG_SID={rotated_session_id}"},
        )
        mount_result = render_mount(self.guarded_mount, request=mount_request, app=self.app)
        self.assertEqual(mount_result.data["viewer_name"], "Ada")
        self.assertEqual(mount_request.user["username"], "ada")
        self.assertEqual(mount_request.active_profile["slug"], "owner")

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
        self.assertEqual(action_result.value["active_profile"]["slug"], "owner")
        self.assertEqual(action_request.active_profile["slug"], "owner")

        bridge = SpragSocketBridge(self.app)
        bridge.provide_registry()
        controller = ProtectedController()
        controller.bind_app(self.app)
        try:
            controller.build_handler(bridge)
            ws = FakeWebSocket(f"SPRAG_SID={rotated_session_id}")
            connection = bridge.connect(ws)
            bridge.handle_message(connection, json.dumps({"type": "hello", "route": "/protected"}))
            bridge.handle_message(
                connection,
                json.dumps(
                    {
                        "type": "emit",
                        "event": "auth:probe",
                        "route": "/protected",
                        "payload": {},
                    }
                ),
            )
            self.assertEqual(
                ProtectedController.last_socket_probe,
                {"username": "ada", "profile_slug": "owner"},
            )
        finally:
            bridge.clear_registry()

    def test_login_persists_profile_state_and_remember_cookie_only_when_configured(self):
        app = self._make_app(
            session_policy=SessionPolicy(remember_me_ttl_seconds=3600)
        )
        _, remembered_session_id, headers = self._login_cookie(remember=True, app=app)
        remembered_snapshot = app.providers["session_store"].load(remembered_session_id)
        remembered_meta = remembered_snapshot["__sprag_session__"]
        self.assertEqual(remembered_snapshot["auth_user_id"], "user-ada")
        self.assertEqual(remembered_snapshot["active_profile_id"], "profile-owner")
        self.assertTrue(remembered_meta["remember_me"])
        self.assertIn("Max-Age=3600", headers["Set-Cookie"])

        _, session_id, headers = self._login_cookie(remember=True)
        snapshot = self.app.providers["session_store"].load(session_id)
        meta = snapshot["__sprag_session__"]
        self.assertFalse(meta["remember_me"])
        self.assertNotIn("Max-Age", headers["Set-Cookie"])

    def test_set_active_profile_updates_session_without_rotating_session_id(self):
        _, rotated_session_id, _headers = self._login_cookie()
        status, headers, payload = self._action(
            "/protected",
            "switch_profile",
            {"profile": "auditor"},
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "200 OK")
        self.assertEqual(payload["value"]["session_id"], rotated_session_id)
        self.assertEqual(payload["value"]["active_profile"]["slug"], "auditor")
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(
            self.app.providers["session_store"].load(rotated_session_id)["active_profile_id"],
            "profile-auditor",
        )

    def test_expired_sessions_are_invalidated_before_auth_resolution(self):
        app = self._make_app(
            session_policy=SessionPolicy(idle_ttl_seconds=5, absolute_ttl_seconds=30)
        )
        session_store = app.providers["session_store"]
        session_store._store.set({"expired-session": {
            "auth_user_id": "user-ada",
            "active_profile_id": "profile-owner",
            "note": "stale",
            "__sprag_session__": {
                "created_at": 100,
                "last_seen": 100,
                "absolute_expiry": 130,
                "remember_me": False,
            },
        }})
        with patch("sprag.runtime.session.time.time", return_value=200):
            status, headers, _body = self._wsgi_call(
                method="GET",
                path="/protected",
                cookie="SPRAG_SID=expired-session",
                app=app,
            )
        self.assertEqual(status, "302 Found")
        self.assertEqual(headers["Location"], "/login?next=%2Fprotected")
        self.assertNotEqual(_session_id_from_cookie(headers["Set-Cookie"]), "expired-session")
        self.assertEqual(session_store.load("expired-session"), {})

    def test_requires_auth_redirects_and_returns_401_or_403_by_context(self):
        status, headers, _body = self._wsgi_call(method="GET", path="/protected")
        self.assertEqual(status, "302 Found")
        self.assertEqual(headers["Location"], "/login?next=%2Fprotected")

        status, headers, _body = self._wsgi_call(method="GET", path="/guarded-mount")
        self.assertEqual(status, "302 Found")
        self.assertEqual(headers["Location"], "/login?next=%2Fguarded-mount")

        status, _headers, payload = self._action("/protected", "whoami")
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

        _, rotated_session_id, _headers = self._login_cookie()
        self._action(
            "/protected",
            "switch_profile",
            {"profile": "auditor"},
            cookie=f"SPRAG_SID={rotated_session_id}",
        )

        status, _headers, forbidden = self._wsgi_call(
            method="GET",
            path="/method-guard",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "403 Forbidden")
        self.assertIn("Forbidden.", forbidden.decode("utf-8"))

        status, _headers, forbidden = self._wsgi_call(
            method="GET",
            path="/guarded-mount",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "403 Forbidden")
        self.assertIn("Forbidden.", forbidden.decode("utf-8"))

        status, _headers, payload = self._action(
            "/protected",
            "publish_memo",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "403 Forbidden")
        self.assertEqual(payload["error"], "Forbidden.")

        status, _headers, response = self._wsgi_call(
            method="POST",
            path="/__sprag__/uploads",
            body=upload,
            content_type=f"multipart/form-data; boundary={boundary}",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        payload = json.loads(response.decode("utf-8"))
        self.assertEqual(status, "403 Forbidden")
        self.assertEqual(payload["error"], "Forbidden.")

    def test_role_permission_and_active_profile_requirements_are_enforced(self):
        _, rotated_session_id, _headers = self._login_cookie()

        status, _headers, payload = self._action(
            "/protected",
            "staff_area",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "200 OK")
        self.assertTrue(payload["value"]["allowed"])

        status, _headers, payload = self._action(
            "/protected",
            "publish_memo",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "200 OK")
        self.assertTrue(payload["value"]["published"])

        self._action(
            "/protected",
            "switch_profile",
            {"profile": "auditor"},
            cookie=f"SPRAG_SID={rotated_session_id}",
        )

        status, _headers, payload = self._action(
            "/protected",
            "staff_area",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "200 OK")
        self.assertTrue(payload["value"]["allowed"])

        status, _headers, payload = self._action(
            "/protected",
            "publish_memo",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "403 Forbidden")
        self.assertEqual(payload["error"], "Forbidden.")

        self._action(
            "/protected",
            "clear_profile",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        status, _headers, payload = self._action(
            "/protected",
            "needs_profile",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "403 Forbidden")
        self.assertEqual(payload["error"], "Active profile required.")

    def test_method_level_guard_overrides_class_level_guard(self):
        _, rotated_session_id, _headers = self._login_cookie()
        self._action(
            "/protected",
            "switch_profile",
            {"profile": "auditor"},
            cookie=f"SPRAG_SID={rotated_session_id}",
        )

        status, _headers, payload = self._action(
            "/method-guard",
            "default_guard",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "403 Forbidden")
        self.assertEqual(payload["error"], "Forbidden.")

        status, _headers, payload = self._action(
            "/method-guard",
            "override_guard",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "200 OK")
        self.assertEqual(payload["value"]["guard"], "override")
        self.assertEqual(payload["value"]["profile"], "auditor")

    def test_imperative_require_auth_matches_decorator_behavior(self):
        status, _headers, payload = self._action("/protected", "imperative_publish")
        self.assertEqual(status, "401 Unauthorized")
        self.assertEqual(payload["error"], "Authentication required.")

        _, rotated_session_id, _headers = self._login_cookie()
        self._action(
            "/protected",
            "switch_profile",
            {"profile": "auditor"},
            cookie=f"SPRAG_SID={rotated_session_id}",
        )

        status, _headers, imperative = self._action(
            "/protected",
            "imperative_publish",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "403 Forbidden")
        self.assertEqual(imperative["error"], "Forbidden.")

        status, _headers, decorated = self._action(
            "/protected",
            "publish_memo",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "403 Forbidden")
        self.assertEqual(decorated["error"], "Forbidden.")

        self._action(
            "/protected",
            "switch_profile",
            {"profile": "owner"},
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        status, _headers, imperative = self._action(
            "/protected",
            "imperative_publish",
            cookie=f"SPRAG_SID={rotated_session_id}",
        )
        self.assertEqual(status, "200 OK")
        self.assertTrue(imperative["value"]["published"])

    def test_render_payload_contains_sanitized_active_profile_without_private_fields(self):
        _, rotated_session_id, _headers = self._login_cookie()
        request = Request(
            path="/protected",
            headers={"Cookie": f"SPRAG_SID={rotated_session_id}"},
        )
        result = render_page(self.protected_page, request=request, app=self.app)
        self.assertEqual(
            result.data["__sprag_auth__"]["viewer"],
            {"id": "user-ada", "username": "ada", "name": "Ada"},
        )
        self.assertEqual(
            result.data["__sprag_auth__"]["active_profile"],
            {
                "id": "profile-owner",
                "slug": "owner",
                "label": "Owner Workspace",
            },
        )
        self.assertTrue(result.data["__sprag_auth__"]["authenticated"])
        self.assertIn('"active_profile": {"id": "profile-owner", "label": "Owner Workspace", "slug": "owner"}', result.html)
        self.assertNotIn("server-only-secret", result.html)
        self.assertNotIn("memo:publish", result.html)
        self.assertNotIn("roles", result.html)
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
