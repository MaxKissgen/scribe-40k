"""RFC 6901 JSON Pointers.

Flags, the assignment tray and the editor's autosave all address fields by pointer, so
this is the shared vocabulary between backend and frontend. Small enough to implement
directly rather than take a dependency for.
"""

from __future__ import annotations

from typing import Any


def parse(pointer: str) -> list[str]:
    """Split a pointer into unescaped tokens. ``""`` is the whole document."""
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"a JSON Pointer must start with '/': {pointer!r}")
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def join(*tokens: object) -> str:
    return "".join(f"/{escape(str(token))}" for token in tokens)


_MISSING = object()


def resolve(document: Any, pointer: str, default: Any = _MISSING) -> Any:
    """Read the value at ``pointer``.

    Raises ``KeyError`` when the path does not exist and no default is given.
    """
    current = document
    for token in parse(pointer):
        try:
            current = current[int(token)] if isinstance(current, list) else current[token]
        except (KeyError, IndexError, TypeError, ValueError):
            if default is _MISSING:
                raise KeyError(f"{pointer} does not resolve") from None
            return default
    return current


def exists(document: Any, pointer: str) -> bool:
    return resolve(document, pointer, default=_MISSING) is not _MISSING


def set_value(document: Any, pointer: str, value: Any) -> None:
    """Write ``value`` at ``pointer``, in place.

    Intermediate containers must already exist. This is deliberate: the character document
    is created complete by :func:`scribe40k.blank.blank_character`, so a pointer that does
    not resolve is a bug or a hallucinated field, not something to paper over by inventing
    structure.

    Appending to an array uses the RFC's ``-`` token.
    """
    tokens = parse(pointer)
    if not tokens:
        raise ValueError("cannot replace the whole document")

    current = document
    for token in tokens[:-1]:
        current = current[int(token)] if isinstance(current, list) else current[token]

    last = tokens[-1]
    if isinstance(current, list):
        if last == "-":
            current.append(value)
        else:
            current[int(last)] = value
    else:
        current[last] = value


def deep_merge(target: dict, source: dict) -> list[str]:
    """Merge ``source`` into ``target`` in place; return the pointers that were written.

    Used to fold each section job's fragment into the blank character document. The rules
    suit that job specifically:

    * ``None`` in the source never overwrites a value in the target. A model omitting a
      field it could not read must not erase a printed constant.
    * Dicts merge recursively; lists replace wholesale, because a list on this sheet is a
      complete answer (all the gear, all the specialisations), not a patch.
    """
    written: list[str] = []
    _merge(target, source, "", written)
    return written


def _merge(target: dict, source: dict, prefix: str, written: list[str]) -> None:
    for key, value in source.items():
        path = f"{prefix}/{escape(key)}"

        if value is None:
            continue

        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value, path, written)
        else:
            target[key] = value
            written.append(path)
