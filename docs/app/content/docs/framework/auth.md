---
title: Auth
description: Session management, authentication guards, and login/logout flows.
order: 14
---

# Auth

SPRAG includes a built-in auth surface for session management and access control. It's designed to be simple for development and swappable for production.

## Setup

Wire auth providers into your App:

```python
from sprag import App, shell
from sprag import InMemorySessionStore, AnonymousAuthService

app = App(
    routes="app.routes",
    shell=app_shell,
    providers={
        "session_store": InMemorySessionStore(),
        "auth": AnonymousAuthService(),
    },
)
```

- **`InMemorySessionStore`** — stores sessions in memory. Fine for development; swap for Redis/database-backed store in production.
- **`AnonymousAuthService`** — no-op auth that treats every request as unauthenticated. Replace with your auth provider.

## Login and logout

From any controller:

```python
@action(schema=Schema("login", {
    "email": Field(str, required=True),
    "password": Field(str, required=True),
}))
def login(self, email, password):
    user = authenticate(email, password)
    if not user:
        return {"errors": {"email": "Invalid credentials"}}

    # Sets session, rotates session ID
    self.login(user, viewer=user.id, active_profile=user.profile)
    return self.redirect("/dashboard")

@action()
def logout(self):
    self.logout()  # Invalidates session
    return self.redirect("/")
```

`self.login()` sets the session and rotates the session ID to prevent fixation. `self.logout()` clears it.

## Request context

Inside any controller method:

```python
self.request.user          # Authenticated user object (or None)
self.request.session       # Session dict
self.request.session_id    # Session ID string
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

The auth state is included in the browser boot payload at `window.__SPRAG_PAYLOAD__.auth`. Your Module can read it to conditionally render UI:

```python
class NavModule(Module):
    def on_start(self):
        auth = self.state.get("auth", {})
        if auth.get("user"):
            self.set_state({"logged_in": True, "username": auth["user"]["name"]})
```

## Custom auth providers

To integrate a real auth backend (Firebase, Auth0, database), implement the auth service interface and provide it to the App:

```python
class MyAuthService:
    def authenticate(self, request):
        # Return user object or None
        token = request.cookies.get("auth_token")
        return verify_token(token)

app = App(
    routes="app.routes",
    shell=app_shell,
    providers={
        "session_store": RedisSessionStore(redis_url),
        "auth": MyAuthService(),
    },
)
```
