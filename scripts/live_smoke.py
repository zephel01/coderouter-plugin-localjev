#!/usr/bin/env python3
"""Run the plugin against a running LocalJev and print what it would do.

    LOCALJEV_BASE_URL=http://127.0.0.1:8080 python3 scripts/live_smoke.py \
        "add a retry to the uploader"

Exits non-zero when LocalJev is unreachable or /ready is not ready, so it
works as a pre-flight check before enabling the plugin in providers.yaml.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coderouter_plugin_localjev.client import LocalJevError, ready  # noqa: E402
from coderouter_plugin_localjev.plugin import LocalJevPlugin  # noqa: E402

BASE_URL = os.environ.get("LOCALJEV_BASE_URL", "http://127.0.0.1:8080")
API_KEY = os.environ.get("LOCALJEV_API_KEY", "")
PROFILES = [p for p in os.environ.get("LOCALJEV_PROFILES", "coding,reasoning,general").split(",") if p]


class Msg:
    def __init__(self, role, content):
        self.role, self.content = role, content


class Req:
    def __init__(self, text):
        self.messages = [Msg("user", text)]
        self.system = [{"type": "text", "text": "You are Claude Code.",
                        "cache_control": {"type": "ephemeral"}}]
        self.profile = None

    def model_copy(self, update=None):
        clone = Req("")
        clone.messages, clone.system, clone.profile = self.messages, self.system, self.profile
        for key, value in (update or {}).items():
            setattr(clone, key, value)
        return clone


async def main() -> int:
    state = " ".join(sys.argv[1:]) or "add a retry to the uploader and run the tests"
    try:
        status = ready(base_url=BASE_URL)
    except LocalJevError as exc:
        print(f"NG  /ready unreachable: {exc}")
        return 2
    print(f"/ready -> {json.dumps(status, ensure_ascii=False)}")
    if status.get("status") != "ready":
        print("NG  LocalJev is up but its upstream model is not loaded "
              "(check LOCALJEV_UPSTREAM_MODEL against upstream /v1/models)")
        return 3

    plugin = LocalJevPlugin(base_url=BASE_URL, api_key=API_KEY, profiles_available=PROFILES)
    request = Req(state)
    out = await plugin.transform(request)

    print(f"\nstate: {state}")
    print(f"profile: {request.profile} -> {out.profile}")
    print("system block appended:")
    print(out.system[-1]["text"] if isinstance(out.system, list) else out.system)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
