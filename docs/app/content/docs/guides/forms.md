---
title: Forms
description: The SPRAG form convention — DOM-owned inputs, Module-owned state, validation, and file uploads.
order: 41
---

# Forms

SPRAG forms follow a simple convention: the DOM owns the input elements, the Module owns the state (draft, errors, dirty, submission).

## Basic form

```python
from sprag import Component, ui

class ContactForm(Component):
    def render(self, props=None):
        errors = self.state.get("errors", {})
        return ui.form(
            ui.div(
                ui.label("Name", for_="name"),
                ui.input(name="name", type="text", required=True),
                ui.span(errors.get("name", ""), class_="error") if errors.get("name") else None,
            ),
            ui.div(
                ui.label("Email", for_="email"),
                ui.input(name="email", type="email", required=True),
                ui.span(errors.get("email", ""), class_="error") if errors.get("email") else None,
            ),
            ui.div(
                ui.label("Message", for_="message"),
                ui.textarea(name="message"),
            ),
            ui.button("Send", type="submit"),
        )
```

## Handling submission

Use `form_data()` to snapshot form inputs at submit time:

```python
from sprag import Module

class ContactModule(Module):
    def on_start(self):
        self.delegate(self.element, "submit", "form", self.on_submit)

    def on_submit(self, event, target):
        event.prevent_default()
        data = self.form_data(event)  # {"name": "...", "email": "...", "message": "..."}
        self.call_action("send_message", data).then(self._on_submit_result)

    def _on_submit_result(self, result):
        self.set_state(result.value or {})
```

`self.form_data(event)` reads all named inputs from the form and returns a plain dict.

## Validation errors

When the server `@action` detects invalid data, return an errors dict:

```python
# server.py
@action(schema=Schema("send_message", {
    "name": Field(str, required=True),
    "email": Field(str, required=True),
    "message": Field(str, required=True),
}))
def send_message(self, name, email, message):
    errors = {}
    if "@" not in email:
        errors["email"] = "Invalid email address"
    if errors:
        return {"errors": errors}
    send_email(name, email, message)
    return {"sent": True, "errors": {}}
```

The action result can carry `errors`, and the Module should copy `result.value` into local state so the Component can re-render with those messages.

## Error normalisation

Use `action_error_message()` to handle network and server errors consistently:

```python
from sprag import Module

class ContactModule(Module):
    def on_submit(self, event, target):
        event.prevent_default()
        (
            self.call_action("send_message", self.form_data(event))
            .then(self._on_submit_result)
            .catch(self._on_submit_error)
        )

    def _on_submit_result(self, result):
        self.set_state(result.value or {})

    def _on_submit_error(self, error):
        self.set_state({"error": self.action_error_message(error)})
```

## Debounced autosave

For forms that save as the user types:

```python
from sprag import Module, debounce

class DraftModule(Module):
    def on_start(self):
        self.delegate(self.element, "input", "input, textarea", self._on_input)

    def _on_input(self, event, target):
        self._save_draft()

    @debounce(1.0)
    def _save_draft(self):
        data = self._collect_form_data()
        self.call_action("save_draft", data)
```

The `@debounce(1.0)` decorator coalesces rapid keystrokes into a single save call after 1 second of inactivity.

## File uploads

Don't use `form_data()` for file inputs. Use the Module's upload methods instead:

```python
class AvatarModule(Module):
    def on_start(self):
        self.delegate(self.element, "submit", "form", self.on_submit)

    def on_submit(self, event, target):
        event.prevent_default()
        self.upload_form("avatar", event, self.on_progress)

    def on_progress(self, progress):
        self.set_state({"upload_percent": progress.percent})
```

## Redirect after submit

If the server action returns a redirect, the Module follows it automatically:

```python
# server.py
@action(schema=Schema("send_message", {...}))
def send_message(self, **data):
    save(data)
    return self.redirect("/thank-you")
```

No browser-side redirect code needed.
