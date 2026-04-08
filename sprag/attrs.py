"""HTML attribute naming policy for SPRAG UI trees."""

from __future__ import annotations


def normalize_attr_key(key: str) -> str:
    """Return the HTML attribute name for a Python keyword argument.

    Python keyword arguments cannot contain hyphens, so SPRAG's authoring
    surface uses underscores for hyphenated attribute families:

    - ``data_role="x"`` -> ``data-role="x"``
    - ``aria_label="x"`` -> ``aria-label="x"``

    For Python reserved words, SPRAG follows the usual trailing-underscore
    convention:

    - ``class_="x"`` -> ``class="x"``
    - ``for_="x"`` -> ``for="x"``
    """
    if key.startswith(("data_", "aria_")):
        return key.replace("_", "-")
    if key.endswith("_") and not key.startswith("_"):
        return key[:-1]
    return key
