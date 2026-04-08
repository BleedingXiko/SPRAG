"""Controller action helpers."""


def action(fn=None, *, schema=None, name=None):
    """Mark a controller method as a route action.

    Can be used bare (@action) or with parameters:
        @action(schema=Schema('inc', {'count': Field(int, required=True)}))
    """
    def decorator(method):
        method._sprag_action = True
        method._sprag_action_meta = {
            "name": name or method.__name__,
            "schema": schema,
        }
        return method

    if fn is not None:
        # Bare @action usage
        return decorator(fn)
    return decorator
