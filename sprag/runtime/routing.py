"""File-based route pattern helpers for exact, slug, and catch-all pages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteMatch:
    module_name: str
    page: object
    path: str
    params: dict


@dataclass(frozen=True)
class BuildEntry:
    path: str
    params: dict


@dataclass(frozen=True)
class _Segment:
    kind: str
    value: str


def is_dynamic_path(path: str) -> bool:
    return any(segment.kind != "literal" for segment in _parse_segments(path))


def match_page_route(pages, actual_path: str) -> RouteMatch | None:
    normalized = normalize_route_path(actual_path)
    for module_name, page in sorted(pages, key=_page_match_sort_key):
        params = match_route_path(page.path, normalized)
        if params is not None:
            return RouteMatch(
                module_name=module_name,
                page=page,
                path=normalized,
                params=params,
            )
    return None


def match_route_path(pattern: str, actual_path: str) -> dict | None:
    pattern_segments = _parse_segments(pattern)
    actual_segments = _path_segments(actual_path)
    params = {}
    index = 0

    for segment in pattern_segments:
        if segment.kind == "literal":
            if index >= len(actual_segments) or actual_segments[index] != segment.value:
                return None
            index += 1
            continue
        if segment.kind == "param":
            if index >= len(actual_segments):
                return None
            params[segment.value] = actual_segments[index]
            index += 1
            continue
        if segment.kind == "catch_all":
            if index >= len(actual_segments):
                return None
            params[segment.value] = actual_segments[index:]
            index = len(actual_segments)
            break

    if index != len(actual_segments):
        return None
    return params


def build_entries_for_page(page) -> list[BuildEntry]:
    pattern = page.path
    if not is_dynamic_path(pattern):
        return [BuildEntry(path=normalize_route_path(pattern), params={})]

    if page.static_paths is None:
        raise ValueError(
            f"Dynamic SPRAG route {pattern!r} requires page(..., static_paths=...) "
            "so the static build knows which concrete pages to emit."
        )

    source = page.static_paths() if callable(page.static_paths) else page.static_paths
    entries = []
    for item in source or []:
        if isinstance(item, str):
            path = normalize_route_path(item)
            params = match_route_path(pattern, path)
            if params is None:
                raise ValueError(
                    f"Static path {item!r} does not match dynamic route pattern {pattern!r}."
                )
            entries.append(BuildEntry(path=path, params=params))
            continue
        if isinstance(item, dict) and "path" in item:
            path = normalize_route_path(item["path"])
            params = dict(item.get("params") or {})
            if not params:
                matched = match_route_path(pattern, path)
                if matched is None:
                    raise ValueError(
                        f"Static path {item['path']!r} does not match dynamic route pattern {pattern!r}."
                    )
                params = matched
            entries.append(BuildEntry(path=path, params=params))
            continue
        if isinstance(item, dict):
            params = dict(item)
            entries.append(BuildEntry(path=fill_route_path(pattern, params), params=params))
            continue
        raise TypeError(
            f"Unsupported static_paths item for route {pattern!r}: {item!r}. "
            "Use a string path, a params dict, or {'path': ..., 'params': ...}."
        )

    seen = set()
    deduped = []
    for entry in entries:
        if entry.path in seen:
            continue
        seen.add(entry.path)
        deduped.append(entry)
    return deduped


def fill_route_path(pattern: str, params: dict) -> str:
    parts = []
    for segment in _parse_segments(pattern):
        if segment.kind == "literal":
            parts.append(segment.value)
            continue
        if segment.value not in params:
            raise ValueError(
                f"Missing route param {segment.value!r} for dynamic route {pattern!r}."
            )
        value = params[segment.value]
        if segment.kind == "param":
            parts.append(_clean_param_segment(value, pattern=pattern, name=segment.value))
            continue
        catch_all_parts = _clean_catch_all_segments(value, pattern=pattern, name=segment.value)
        if not catch_all_parts:
            raise ValueError(
                f"Catch-all route param {segment.value!r} for {pattern!r} must not be empty."
            )
        parts.extend(catch_all_parts)
    if not parts:
        return "/"
    return "/" + "/".join(parts)


def normalize_route_path(path: str) -> str:
    if not path or path == "/":
        return "/"
    return "/" + str(path).strip("/")


def _page_match_sort_key(item):
    _module_name, page = item
    segments = _parse_segments(page.path)
    literal_count = sum(1 for segment in segments if segment.kind == "literal")
    dynamic_count = sum(1 for segment in segments if segment.kind != "literal")
    catch_all_count = sum(1 for segment in segments if segment.kind == "catch_all")
    return (
        dynamic_count,
        catch_all_count,
        -literal_count,
        -len(segments),
        page.path,
    )


def _parse_segments(path: str) -> list[_Segment]:
    normalized = normalize_route_path(path)
    parts = _path_segments(normalized)
    segments = []
    catch_all_seen = False
    for index, part in enumerate(parts):
        if part.startswith("[...") and part.endswith("]"):
            name = part[4:-1]
            if catch_all_seen:
                raise ValueError(f"SPRAG route pattern {path!r} may only have one catch-all segment.")
            if index != len(parts) - 1:
                raise ValueError(f"SPRAG catch-all segment must be final in route pattern {path!r}.")
            if not name:
                raise ValueError(f"SPRAG catch-all segment in {path!r} must have a name.")
            segments.append(_Segment("catch_all", name))
            catch_all_seen = True
            continue
        if part.startswith("[") and part.endswith("]"):
            name = part[1:-1]
            if not name:
                raise ValueError(f"SPRAG dynamic segment in {path!r} must have a name.")
            segments.append(_Segment("param", name))
            continue
        segments.append(_Segment("literal", part))
    return segments


def _path_segments(path: str) -> list[str]:
    normalized = normalize_route_path(path)
    if normalized == "/":
        return []
    return [part for part in normalized.strip("/").split("/") if part]


def _clean_param_segment(value, *, pattern: str, name: str) -> str:
    segment = str(value).strip("/")
    if not segment or "/" in segment:
        raise ValueError(
            f"Route param {name!r} for {pattern!r} must be a single non-empty path segment."
        )
    return segment


def _clean_catch_all_segments(value, *, pattern: str, name: str) -> list[str]:
    if isinstance(value, (list, tuple)):
        parts = [str(part).strip("/") for part in value if str(part).strip("/")]
    else:
        parts = [part for part in str(value).strip("/").split("/") if part]
    if any("/" in part for part in parts):
        raise ValueError(
            f"Catch-all route param {name!r} for {pattern!r} must expand into clean path segments."
        )
    return parts
