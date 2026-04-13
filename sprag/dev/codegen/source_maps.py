"""Helpers for emitting source maps for generated browser files."""

from __future__ import annotations

import json
from dataclasses import dataclass


_BASE64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


@dataclass(frozen=True)
class GeneratedArtifact:
    code: str
    source_map: str | None = None


@dataclass(frozen=True)
class GeneratedLineMapping:
    source_line: int
    name: str | None = None


def count_lines(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def mappings_for_text(
    text: str,
    *,
    source_line: int | None,
    name: str | None = None,
) -> list[GeneratedLineMapping | None]:
    if not text:
        return []
    if source_line is None:
        return [None] * count_lines(text)
    return [GeneratedLineMapping(source_line=source_line, name=name)] * count_lines(text)


def build_source_map(
    *,
    generated_file: str,
    source_file: str,
    source_content: str,
    line_mappings: list[GeneratedLineMapping | None],
    extra: dict | None = None,
) -> str:
    names: list[str] = []
    name_indexes: dict[str, int] = {}
    encoded_lines: list[str] = []
    prev_source = 0
    prev_source_line = 0
    prev_source_column = 0
    prev_name = 0

    for mapping in line_mappings:
        if mapping is None:
            encoded_lines.append("")
            continue

        fields = [
            0,  # generated column
            0 - prev_source,
            (mapping.source_line - 1) - prev_source_line,
            0 - prev_source_column,
        ]
        prev_source = 0
        prev_source_line = mapping.source_line - 1
        prev_source_column = 0

        if mapping.name:
            name_index = name_indexes.get(mapping.name)
            if name_index is None:
                name_index = len(names)
                names.append(mapping.name)
                name_indexes[mapping.name] = name_index
            fields.append(name_index - prev_name)
            prev_name = name_index

        encoded_lines.append("".join(_encode_vlq(field) for field in fields))

    payload = {
        "version": 3,
        "file": generated_file,
        "sources": [source_file],
        "sourcesContent": [source_content],
        "names": names,
        "mappings": ";".join(encoded_lines),
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload, indent=2, sort_keys=True)


def _encode_vlq(value: int) -> str:
    signed = _to_vlq_signed(value)
    chunks: list[str] = []
    while True:
        digit = signed & 31
        signed >>= 5
        if signed:
            digit |= 32
        chunks.append(_BASE64_CHARS[digit])
        if not signed:
            break
    return "".join(chunks)


def _to_vlq_signed(value: int) -> int:
    if value < 0:
        return ((-value) << 1) + 1
    return value << 1
