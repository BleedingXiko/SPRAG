---
title: Auth
description: Session management, authentication guards, and login/logout flows.
order: 14
---

# Auth

SPRAG includes a built-in auth surface for session management and access control. It's designed to be simple for development and swappable for production.

## Setup

Wire auth providers into your `App`. SPRAG looks for `session_store` and `auth` in `app.providers`, and falls back to in-memory sessions plus anonymous auth if you do not provide them:

```python
from sprag import App, InMemorySessionStore, SessionPolicy

app = App(
    routes="app.routes",
    providers={
        "session_store": InMemorySessionStore(),
        "auth": MyAuthService(),
    },
    session_policy=SessionPolicy(
        idle_ttl_seconds=3600,
        absolute_ttl_seconds=86400,
        remember_me_ttl_seconds=2592000,
    ),
)
```

- **`InMemorySessionStore`** — stores sessions in memory. Fine for development; swap for Redis/database-backed store in production.
- **`auth` provider** — implements user loading, login session stamping, authorization, and the public auth snapshot.

## Login and logout

From any controller:

```python
@action(schema=Schema("login", {
    "email": Field(str, required=True),
    "password": Field(str, required=True),
}))
def submit_login(self, email, password):
    user = authenticate(email, password)
    if not user:
        return {"errors": {"email": "Invalid credentials"}}

    # Sets session data and rotates the session ID
    self.login(user, viewer=user.id, active_profile=user.profile)
    return self.redirect("/dashboard")

@action(name="logout", schema=Schema("logout", {}))
def sign_out(self):
    self.logout()
    return self.redirect("/")
```

`self.login()` sets the session and rotates the session ID to prevent fixation. `self.logout()` clears it.

## Request context

Inside any controller method:

```python
self.request.user          # Authenticated user object (or None)
self.request.session       # RequestSession helper
self.request.session_id    # Session ID string
self.request.active_profile # Active profile object (or None)
self.request.cookies       # Request cookies
```

## Auth guards

Use `@requires_auth` to protect routes or individual actions:

```python
from sprag import requires_auth

# Guard the entire controller
@requires_auth(redirect_to="/login")
class DashboardController(Controller):
    route = "/dashboard"

    def load(self):
        return {"user": self.request.user}
```

Or guard specific methods:

```python
class ProfileController(Controller):
    route = "/profile"

    def load(self):
        # Public — anyone can view
        return {"profile": get_public_profile()}

    @requires_auth
    @action(schema=Schema("update", {...}))
    def update(self, **data):
        # Protected — logged-in users only
        save_profile(self.request.user, data)
        return {"profile": get_profile(self.request.user)}
```

## Browser-side auth

SPRAG injects an auth snapshot into route data under `__sprag_auth__`. That same snapshot is also shipped in the browser boot payload, but the stable author-facing name in route data is `__sprag_auth__`:

```python
class DashboardScreen(Screen):
    def render(self, data):
        auth = data["__sprag_auth__"]
        viewer = auth.get("viewer") or {}
        return ui.div(viewer.get("name", "anonymous"))
```

For SSR and route data flows, prefer reading `data["__sprag_auth__"]` or explicitly projecting the fields your Module needs from `load()`.

## Custom auth providers

To integrate a real auth backend (Firebase, Auth0, database), implement the auth service interface and provide it to the App:

```python
class MyAuthService:
    def load_user(self, session, request):
        token = request.cookies.get("auth_token")
        return verify_token(token)

    def load_active_profile(self, user, session, request):
        return None

    def authorize(self, user, active_profile, session, request, *, roles=None, permissions=None):
        return user is not None

app = App(
    routes="app.routes",
    providers={
        "session_store": RedisSessionStore(redis_url),
        "auth": MyAuthService(),
    },
)
```
