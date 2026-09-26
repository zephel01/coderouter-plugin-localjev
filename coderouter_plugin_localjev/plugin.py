"""InputFilter + Observer that consults LocalJev before chain dispatch.

Where this runs in CodeRouter
-----------------------------
``FallbackEngine._generate_anthropic_impl`` calls the input_filter chain
*after* the tool-loop guard and *before* chain resolution
(``coderouter/routing/fallback.py``). Two consequences the implementation
below depends on:

1. Returning a NEW request object (``model_copy``) invalidates the M11
   "prepared dispatch" the ingress computed, so the engine re-resolves the
   chain — which is what makes a ``profile`` written here actually take
   effect.
2. The ingress validated ``request.profile`` against ``config.profiles``
   *before* this filter ran (``ingress/anthropic_routes.py``). A profile
   name written here is therefore NOT validated, and an unknown one
   reaches ``config.profile_by_name`` and raises ``KeyError`` — a 500.
   Hence ``profiles_available`` below: when set, it is an allowlist and
   anything outside it leaves the profile untouched.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from typing import Any

from coderouter_plugin_localjev.client import LocalJevError, systemone
from coderouter_plugin_localjev.defaults import (
    DEFAULT_MODEL,
    DEFAULT_PROFILE_MAP,
    DEFAULT_QUESTIONS,
)

logger = logging.getLogger("coderouter.plugin.localjev")

_MAX_BLOB_CHARS = 1200


def _message_text(content: Any) -> str:
    """Flatten an Anthropic message content field to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif getattr(block, "type", None) == "text":
                parts.append(str(getattr(block, "text", "")))
        return "\n".join(p for p in parts if p)
    return ""


def _last_user_text(request: Any) -> str:
    messages = getattr(request, "messages", None) or []
    for msg in reversed(list(messages)):
        if getattr(msg, "role", None) == "user":
            return _message_text(getattr(msg, "content", ""))
    return ""


def _user_turn_count(request: Any) -> int:
    messages = getattr(request, "messages", None) or []
    return sum(1 for msg in messages if getattr(msg, "role", None) == "user")


def _fingerprint(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8", "replace")).hexdigest()[:16]


def _merge_system(existing: Any, blob: str, position: str) -> Any:
    """Add ``blob`` to the system field.

    Default is ``append``. Claude Code sends ``system`` as a list of text
    blocks with ``cache_control`` on them; inserting a block at the front
    changes the cached prefix and invalidates the prompt cache, while
    appending leaves every earlier block's prefix intact.
    """
    if existing is None or existing == "":
        return blob
    if isinstance(existing, str):
        return f"{existing}\n\n{blob}" if position == "append" else f"{blob}\n\n{existing}"
    if isinstance(existing, list):
        block = {"type": "text", "text": blob}
        return [*existing, block] if position == "append" else [block, *existing]
    return blob


def _format_decision(answers: dict[str, Any]) -> str:
    """Render LocalJev ``answers`` as a compact advisory block.

    The wire shape (verified against ``localjev/src/server.ts`` and
    ``decodeAnswers`` in ``src/engine.ts``) is::

        {"needs_tools": {"type": "noul", "noul": 0.82},
         "route": {"type": "choice", "choice": "coding",
                   "probabilities": {...}, "confidence": 0.13}}

    There is no top-level ``nouls`` / ``choices`` / ``scores`` object.
    """
    lines = [
        "[localjev] System One 判定 (参考値: logit 直読みではなく "
        "モデルの自己申告確率)",
    ]
    for key, ans in answers.items():
        if not isinstance(ans, dict):
            lines.append(f"- {key}: {ans}")
            continue
        kind = ans.get("type")
        if kind == "noul":
            noul = ans.get("noul")
            lines.append(
                f"- {key}: yes={noul:.2f}"
                if isinstance(noul, (int, float))
                else f"- {key}: {noul}"
            )
        elif kind == "choice":
            choice = ans.get("choice")
            probs = ans.get("probabilities") or {}
            p = probs.get(choice)
            conf = ans.get("confidence")
            detail = []
            if isinstance(p, (int, float)):
                detail.append(f"p={p:.2f}")
            if isinstance(conf, (int, float)):
                detail.append(f"conf={conf:.2f}")
            suffix = f" ({', '.join(detail)})" if detail else ""
            lines.append(f"- {key}: {choice}{suffix}")
        elif kind == "score":
            score = ans.get("score")
            conf = ans.get("confidence")
            suffix = f" (conf={conf:.2f})" if isinstance(conf, (int, float)) else ""
            lines.append(
                f"- {key}: {score:.2f}{suffix}"
                if isinstance(score, (int, float))
                else f"- {key}: {score}{suffix}"
            )
        else:
            lines.append(f"- {key}: {ans}")
    blob = "\n".join(lines)
    return blob[:_MAX_BLOB_CHARS]


def _choice(answers: dict[str, Any], key: str) -> tuple[str | None, float]:
    """Return ``(label, confidence)`` for a choice answer, or ``(None, 0.0)``."""
    ans = answers.get(key)
    if not isinstance(ans, dict) or ans.get("type") != "choice":
        return None, 0.0
    label = ans.get("choice")
    conf = ans.get("confidence")
    return (
        str(label) if label is not None else None,
        float(conf) if isinstance(conf, (int, float)) else 0.0,
    )


class LocalJevPlugin:
    """Consult LocalJev on the inbound request, inject and/or route.

    Config keys (``plugins.config.localjev`` in providers.yaml):

    base_url            LocalJev origin. Default http://127.0.0.1:8080
    api_key             Bearer token, only needed when LocalJev itself
                        was started with LOCALJEV_API_KEY set
    model               System One alias. Default jev-latest
    timeout_s           HTTP timeout in seconds. Default 20
    fail_open           On any LocalJev error, pass the request through
                        unchanged. Default True
    first_user_only     Only consult on the first user turn. Default True
    inject              Add the decision to ``system``. Default True
    inject_position     append (default, keeps the prompt-cache prefix)
                        or prepend
    set_profile         Write ``request.profile`` from the route choice.
                        Default True
    route_question      Choice question used for the profile map.
                        Default "route"
    min_confidence      Skip the profile switch below this confidence.
                        Default 0.0 (always switch)
    profiles_available  Allowlist of profile names that exist in this
                        providers.yaml. STRONGLY recommended: an unknown
                        profile raises KeyError deep in the engine
    questions           Override the default System One question set
    profile_map         choice label -> CodeRouter profile name
    state_max_chars     Truncate the state sent to LocalJev. Default 4000
    """

    name = "localjev"

    # The loader instantiates the class once per entry-point group
    # (``coderouter.input_filter`` and ``coderouter.observer``), so the
    # observer is a DIFFERENT object from the filter that made the
    # decision. A class-level, bounded store is what lets the observer
    # correlate ``request_completed`` back to the decision.
    _DECISIONS: OrderedDict[str, dict[str, Any]] = OrderedDict()
    _DECISIONS_MAX = 32

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        timeout_s: float = 20.0,
        fail_open: bool = True,
        first_user_only: bool = True,
        inject: bool = True,
        inject_position: str = "append",
        set_profile: bool = True,
        route_question: str = "route",
        min_confidence: float = 0.0,
        profiles_available: list[str] | None = None,
        questions: dict[str, Any] | None = None,
        profile_map: dict[str, str] | None = None,
        state_max_chars: int = 4000,
        **_: Any,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout_s = float(timeout_s)
        self.fail_open = bool(fail_open)
        self.first_user_only = bool(first_user_only)
        self.inject = bool(inject)
        if inject_position not in ("append", "prepend"):
            raise ValueError("inject_position must be 'append' or 'prepend'")
        self.inject_position = inject_position
        self.set_profile = bool(set_profile)
        self.route_question = route_question
        self.min_confidence = float(min_confidence)
        self.profiles_available = (
            {str(p) for p in profiles_available} if profiles_available else None
        )
        self.questions = questions or DEFAULT_QUESTIONS
        self.profile_map = profile_map or dict(DEFAULT_PROFILE_MAP)
        self.state_max_chars = int(state_max_chars)

    # -- helpers ----------------------------------------------------

    @classmethod
    def _remember(cls, key: str, decision: dict[str, Any]) -> None:
        cls._DECISIONS[key] = decision
        cls._DECISIONS.move_to_end(key)
        while len(cls._DECISIONS) > cls._DECISIONS_MAX:
            cls._DECISIONS.popitem(last=False)

    def _state_of(self, request: Any) -> str:
        state = _last_user_text(request)
        if len(state) > self.state_max_chars:
            state = state[: self.state_max_chars]
        return state

    def _target_profile(self, answers: dict[str, Any]) -> str | None:
        label, conf = _choice(answers, self.route_question)
        if label is None:
            return None
        if conf < self.min_confidence:
            logger.info(
                "localjev-route-skipped-low-confidence",
                extra={"label": label, "confidence": conf,
                       "min_confidence": self.min_confidence},
            )
            return None
        target = self.profile_map.get(label)
        if target is None:
            logger.info("localjev-route-unmapped", extra={"label": label})
            return None
        if self.profiles_available is not None and target not in self.profiles_available:
            # Guard: the ingress already finished validating profiles by
            # the time this filter runs, so an unknown name here would
            # surface as an unhandled KeyError (HTTP 500) at chain
            # resolution instead of a clean 400.
            logger.warning(
                "localjev-route-unknown-profile",
                extra={"profile": target, "known": sorted(self.profiles_available)},
            )
            return None
        return target

    # -- InputFilter ------------------------------------------------

    async def transform(self, request: Any) -> Any:
        if self.first_user_only and _user_turn_count(request) > 1:
            return request

        state = self._state_of(request)
        if not state.strip():
            return request

        try:
            data = await asyncio.to_thread(
                systemone,
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                state=state,
                questions=self.questions,
                timeout_s=self.timeout_s,
            )
        except LocalJevError as exc:
            logger.warning("localjev-failed", extra={"error": str(exc)[:400]})
            if self.fail_open:
                return request
            raise

        answers = data.get("answers")
        if not isinstance(answers, dict) or not answers:
            logger.warning("localjev-empty-answers", extra={"body": str(data)[:200]})
            return request

        self._remember(_fingerprint(state), answers)

        updates: dict[str, Any] = {}
        if self.set_profile:
            target = self._target_profile(answers)
            if target is not None and target != getattr(request, "profile", None):
                updates["profile"] = target
        if self.inject:
            updates["system"] = _merge_system(
                getattr(request, "system", None),
                _format_decision(answers),
                self.inject_position,
            )

        logger.info(
            "localjev-decided",
            extra={
                "questions": list(answers.keys()),
                "profile": updates.get("profile"),
                "injected": bool(self.inject),
            },
        )

        if not updates:
            return request
        return request.model_copy(update=updates)

    # -- Observer ---------------------------------------------------

    async def on_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type != "request_completed":
            return
        request = payload.get("request")
        if request is None:
            return
        answers = self._DECISIONS.get(_fingerprint(self._state_of(request)))
        if answers is None:
            return
        label, conf = _choice(answers, self.route_question)
        logger.info(
            "localjev-observed",
            extra={
                "provider": payload.get("provider"),
                "latency_ms": payload.get("latency_ms"),
                "profile": getattr(request, "profile", None),
                "route": label,
                "confidence": conf,
            },
        )
