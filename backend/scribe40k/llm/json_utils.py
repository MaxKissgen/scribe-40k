"""Getting JSON back out of a model reply.

Providers that support native structured output return clean JSON. The rest need coaxing:
fenced code blocks, a sentence of preamble, or a trailing explanation are all common. This
module recovers the payload without resorting to a second API call.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> Any | None:
    """Best-effort parse of a model reply into JSON.

    Tries, in order: the whole string, any fenced code block, then the longest balanced
    ``{...}`` or ``[...]`` span. Returns ``None`` when nothing parses, so the caller can
    decide whether to repair or fail.
    """
    if not text or not text.strip():
        return None

    candidates: list[str] = [text.strip()]
    candidates.extend(match.group("body").strip() for match in _FENCE_RE.finditer(text))

    span = _balanced_span(text)
    if span:
        candidates.append(span)

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _balanced_span(text: str) -> str | None:
    """The longest balanced object or array in ``text``, ignoring braces inside strings."""
    best: str | None = None

    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth = 0
            in_string = False
            escaped = False

            for index in range(start, len(text)):
                char = text[index]

                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue

                if char == '"':
                    in_string = True
                elif char == opener:
                    depth += 1
                elif char == closer:
                    depth -= 1
                    if depth == 0:
                        span = text[start : index + 1]
                        if best is None or len(span) > len(best):
                            best = span
                        break

            start = text.find(opener, start + 1)

    return best
