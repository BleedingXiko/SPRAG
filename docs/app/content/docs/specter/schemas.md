---
title: Schemas
description: Declarative payload validation for actions and custom endpoints.
order: 22
---

# Schemas

Schemas validate action payloads before your handler code runs. Invalid data is rejected with a structured 400 response automatically.

## Declaration

```python
from sprag import Schema, Field

increment_schema = Schema("increment", {
    "count": Field(int, required=True),
})
```

## Field options

```python
Field(type, required=False, default=None, choices=None)
```

| Parameter | Description |
|---|---|
| `type` | Python type: `int`, `str`, `float`, `bool`, `list`, `dict` |
| `required` | Whether the field must be present |
| `default` | Default value if not provided |
| `choices` | List of allowed values |

## Usage with actions

```python
@action(schema=Schema("create_post", {
    "title": Field(str, required=True),
    "body": Field(str, required=True),
    "tags": Field(list, default=[]),
    "status": Field(str, default="draft", choices=["draft", "published"]),
}))
def create_post(self, title, body, tags, status):
    # All values are validated and type-coerced
    return {"post": save_post(title, body, tags, status)}
```

## Type coercion

Schemas coerce incoming values to the declared type. A string `"42"` becomes the integer `42` for an `int` field. This handles the common case of form data and JSON payloads without manual conversion.

## Validation errors

When validation fails, the browser receives a structured error response:

```json
{
  "errors": {
    "title": "Required field",
    "status": "Must be one of: draft, published"
  }
}
```

Your Module can display these inline using the error dict.

## Standalone usage

Schemas work outside of `@action` too — use them to validate data in custom HTTP routes:

```python
import json

def build_routes(self, router):
    @router.route("/api/items", methods=["POST"])
    def create_item():
        schema = Schema("item", {
            "name": Field(str, required=True),
            "quantity": Field(int, default=1),
        })
        outcome = schema.validate(json.loads(self.request.body or "{}"))
        if not outcome.ok:
            return {"errors": outcome.meta.get("errors", {})}, 400
        return {"item": save_item(outcome.value)}
```
