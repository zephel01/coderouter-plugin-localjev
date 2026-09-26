"""Minimal LocalJev / TypeSafe System One client (stdlib only)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("coderouter.plugin.localjev")


class LocalJevError(Exception):
    """Upstream LocalJev call failed or returned an unexpected body."""


def systemone(
    *,
    base_url: str,
    api_key: str,
    model: str,
    state: str,
    questions: dict[str, Any],
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """POST ``{base_url}/v1/systemone`` and return the JSON body."""
    url = base_url.rstrip("/") + "/v1/systemone"
    payload = {"model": model, "state": state, "questions": questions}
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "coderouter-plugin-localjev",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise LocalJevError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LocalJevError(f"connect failed: {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LocalJevError(f"timeout after {timeout_s}s: {url}") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise LocalJevError(f"non-JSON response from {url}") from exc
    if not isinstance(data, dict):
        raise LocalJevError("systemone response is not an object")
    return data


def ready(*, base_url: str, timeout_s: float = 10.0) -> dict[str, Any]:
    """GET ``{base_url}/ready``.

    LocalJev answers 200 ``{"status":"ready","upstream_model":...}`` only
    when the upstream ``GET /v1/models`` lists an id equal to
    ``LOCALJEV_UPSTREAM_MODEL``; otherwise 503. ``/ready`` is unauthenticated
    (only ``/v1/*`` checks the bearer token).
    """
    url = base_url.rstrip("/") + "/ready"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 - diagnostics only
            raise LocalJevError(f"HTTP {exc.code} from {url}") from exc
    except urllib.error.URLError as exc:
        raise LocalJevError(f"connect failed: {url}: {exc.reason}") from exc
