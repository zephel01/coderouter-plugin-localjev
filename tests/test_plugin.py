"""Unit tests against the real LocalJev wire shape.

The fixtures here are copied from an actual ``POST /v1/systemone``
response (localjev 0.2, ``src/server.ts`` -> ``decodeAnswers``), not from
an imagined one: ``{"model":..., "answers": {key: answer}, "usage": {...}}``
with per-answer ``type`` discriminators.
"""

from __future__ import annotations

import pytest

from coderouter_plugin_localjev import plugin as mod
from coderouter_plugin_localjev.client import LocalJevError
from coderouter_plugin_localjev.plugin import LocalJevPlugin

LIVE_ANSWERS = {
    "needs_tools": {"type": "noul", "noul": 0.82},
    "high_risk": {"type": "noul", "noul": 0.12},
    "route": {
        "type": "choice",
        "choice": "reasoning",
        "probabilities": {"coding": 0.2, "reasoning": 0.6, "general": 0.2},
        "confidence": 0.135,
    },
}
LIVE_BODY = {"model": "localjev-0.2", "answers": LIVE_ANSWERS, "usage": {}}


class FakeMessage:
    def __init__(self, role: str, content):
        self.role = role
        self.content = content


class FakeRequest:
    """Stand-in for AnthropicRequest (pydantic ``model_copy`` semantics)."""

    def __init__(self, messages, system=None, profile=None):
        self.messages = messages
        self.system = system
        self.profile = profile

    def model_copy(self, update=None):
        clone = FakeRequest(self.messages, self.system, self.profile)
        for key, value in (update or {}).items():
            setattr(clone, key, value)
        return clone


def one_turn(text="add a retry to the uploader", system=None, profile=None):
    return FakeRequest([FakeMessage("user", text)], system=system, profile=profile)


@pytest.fixture
def stub_systemone(monkeypatch):
    calls: list[dict] = []

    def fake(**kwargs):
        calls.append(kwargs)
        return LIVE_BODY

    monkeypatch.setattr(mod, "systemone", fake)
    return calls


# -- formatting -----------------------------------------------------


def test_format_decision_reads_the_real_shape():
    blob = mod._format_decision(LIVE_ANSWERS)
    assert "needs_tools: yes=0.82" in blob
    assert "route: reasoning" in blob
    assert "p=0.60" in blob and "conf=0.14" in blob
    # no Python dict repr leaking into the system prompt
    assert "{" not in blob and "'type'" not in blob


def test_format_decision_survives_unknown_answer_types():
    blob = mod._format_decision({"x": {"type": "mystery", "v": 1}, "y": 3})
    assert "x:" in blob and "y: 3" in blob


# -- routing --------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_is_written_from_the_route_choice(stub_systemone):
    p = LocalJevPlugin(profiles_available=["coding", "reasoning", "general"])
    out = await p.transform(one_turn())
    assert out.profile == "reasoning"


@pytest.mark.asyncio
async def test_unknown_profile_is_refused(stub_systemone):
    """A profile absent from providers.yaml would be a 500 at chain resolve."""
    p = LocalJevPlugin(
        profile_map={"reasoning": "does-not-exist"},
        profiles_available=["coding", "general"],
    )
    out = await p.transform(one_turn())
    assert out.profile is None


@pytest.mark.asyncio
async def test_min_confidence_blocks_a_weak_switch(stub_systemone):
    p = LocalJevPlugin(min_confidence=0.5, profiles_available=["reasoning"])
    out = await p.transform(one_turn())
    assert out.profile is None  # confidence 0.135 < 0.5


@pytest.mark.asyncio
async def test_set_profile_off_leaves_routing_alone(stub_systemone):
    p = LocalJevPlugin(set_profile=False)
    out = await p.transform(one_turn(profile="coding"))
    assert out.profile == "coding"


# -- injection ------------------------------------------------------


@pytest.mark.asyncio
async def test_append_keeps_the_cache_prefix(stub_systemone):
    system = [{"type": "text", "text": "SYSTEM A", "cache_control": {"type": "ephemeral"}}]
    p = LocalJevPlugin()
    out = await p.transform(one_turn(system=system))
    assert out.system[0] == system[0]          # prefix untouched
    assert out.system[-1]["text"].startswith("[localjev]")


@pytest.mark.asyncio
async def test_prepend_is_available_when_asked(stub_systemone):
    p = LocalJevPlugin(inject_position="prepend")
    out = await p.transform(one_turn(system="SYSTEM A"))
    assert out.system.startswith("[localjev]")
    assert out.system.endswith("SYSTEM A")


# -- gating / failure modes -----------------------------------------


@pytest.mark.asyncio
async def test_second_turn_is_skipped(stub_systemone):
    req = FakeRequest(
        [FakeMessage("user", "a"), FakeMessage("assistant", "b"), FakeMessage("user", "c")]
    )
    out = await LocalJevPlugin().transform(req)
    assert out is req
    assert stub_systemone == []


@pytest.mark.asyncio
async def test_empty_state_is_skipped(stub_systemone):
    req = one_turn("   ")
    out = await LocalJevPlugin().transform(req)
    assert out is req
    assert stub_systemone == []


@pytest.mark.asyncio
async def test_state_is_truncated(stub_systemone):
    await LocalJevPlugin(state_max_chars=10).transform(one_turn("x" * 500))
    assert stub_systemone[0]["state"] == "x" * 10


@pytest.mark.asyncio
async def test_fail_open_passes_the_request_through(monkeypatch):
    def boom(**_):
        raise LocalJevError("connect failed")

    monkeypatch.setattr(mod, "systemone", boom)
    req = one_turn()
    out = await LocalJevPlugin(fail_open=True).transform(req)
    assert out is req


@pytest.mark.asyncio
async def test_fail_closed_raises(monkeypatch):
    def boom(**_):
        raise LocalJevError("connect failed")

    monkeypatch.setattr(mod, "systemone", boom)
    with pytest.raises(LocalJevError):
        await LocalJevPlugin(fail_open=False).transform(one_turn())


@pytest.mark.asyncio
async def test_missing_answers_key_is_tolerated(monkeypatch):
    monkeypatch.setattr(mod, "systemone", lambda **_: {"model": "x"})
    req = one_turn()
    assert await LocalJevPlugin().transform(req) is req


# -- observer -------------------------------------------------------


@pytest.mark.asyncio
async def test_observer_instance_sees_the_filter_decision(stub_systemone, caplog):
    """The loader builds one instance per entry-point group, so these are
    deliberately two different objects sharing the class-level store."""
    filter_instance = LocalJevPlugin(profiles_available=["reasoning"])
    observer_instance = LocalJevPlugin(profiles_available=["reasoning"])
    req = one_turn()
    out = await filter_instance.transform(req)

    caplog.set_level("INFO", logger="coderouter.plugin.localjev")
    await observer_instance.on_event(
        "request_completed", {"request": out, "provider": "local-coder", "latency_ms": 42.0}
    )
    assert any(r.message == "localjev-observed" for r in caplog.records)
    record = next(r for r in caplog.records if r.message == "localjev-observed")
    assert record.route == "reasoning"


@pytest.mark.asyncio
async def test_observer_ignores_unknown_events():
    await LocalJevPlugin().on_event("something_else", {})


def test_invalid_inject_position_is_rejected():
    with pytest.raises(ValueError):
        LocalJevPlugin(inject_position="sideways")
