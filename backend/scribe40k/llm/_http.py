"""Shared HTTP plumbing for the REST-based providers.

Used by the Mistral and OpenAI-compatible providers. The Anthropic provider uses the
official ``anthropic`` SDK instead, which brings its own retry handling.
"""

from __future__ import annotations

import contextlib
import random
import time
from typing import Any

import httpx

#: Status codes worth trying again.
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


class HttpProviderError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout: float,
    max_retries: int,
) -> dict[str, Any]:
    """POST JSON with exponential backoff on transient failures.

    Honours ``Retry-After`` when the server sends one, since guessing a shorter delay only
    makes a rate limit worse.
    """
    delay = 1.0
    last: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        except httpx.RequestError as exc:
            last = HttpProviderError(f"could not reach {url}: {exc}")
        else:
            if response.status_code < 400:
                return response.json()

            body = response.text[:2000]
            last = HttpProviderError(
                f"{url} returned {response.status_code}: {body}",
                status=response.status_code,
                body=body,
            )
            if response.status_code not in RETRYABLE_STATUS:
                raise last

            retry_after = response.headers.get("retry-after")
            if retry_after:
                with contextlib.suppress(ValueError):
                    delay = max(delay, float(retry_after))

        if attempt < max_retries:
            time.sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 2, 60.0)

    raise last if last else HttpProviderError(f"{url} failed with no recorded error")
