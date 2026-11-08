"""Bot failures are useful feedback, not routine automatic notices."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource
from tests.gateway.test_gateway_silence_tokens import _runner

OWNER = "111111111111111111"


def source(bot=True, profile=None):
    return SessionSource(platform=Platform.DISCORD, chat_id="222222222222222222",
                         thread_id="333333333333333333", chat_type="group",
                         user_id="444444444444444444", is_bot=bot, profile=profile)


def result(text="", **kw):
    return dict(final_response=text, messages=[], api_calls=1, **kw)


def setup(monkeypatch, tmp_path, value):
    runner = _runner(monkeypatch, tmp_path)
    runner._reply_anchor_for_event = lambda e: e.message_id or None
    runner._room_allow_silence = lambda _: False
    runner._resolve_profile_home_for_source = lambda s: tmp_path / (s.profile or "default")
    home = tmp_path / "default"
    home.mkdir(exist_ok=True)
    (home / "config.yaml").write_text(f'gateway:\n  bot_failure_owner_ids:\n    discord: ["{OWNER}"]\n')
    runner._hmwa_compression_exhaustion_reset = AsyncMock(side_effect=lambda value, text, entry, *_: (text, entry))
    runner._deliver_platform_notice = AsyncMock()  # unrelated first-use /sethome guidance
    runner._hmwa_post_turn_hooks = AsyncMock()
    adapter = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(success=True)))
    runner._delivery_adapter_for = lambda s: adapter
    runner._run_agent = AsyncMock(return_value=value)
    return runner, adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [result(failed=True, error="secret raw exception"),
    result("(empty)"), result(""), result("NO_REPLY", failed=True), result("NO_REPLY", partial=True),
    result("Codex response remained incomplete after 3 continuation attempts", partial=True,
           error="Codex response remained incomplete after 3 continuation attempts"),
    RuntimeError("secret raw exception")],
    ids=["failed", "retry-empty", "empty-result", "failed-marker", "partial-marker", "hidden-only", "exception"])
async def test_native_turn_reports_failure_and_owner_in_original_thread(monkeypatch, tmp_path, value):
    runner, adapter = setup(monkeypatch, tmp_path, value)
    if isinstance(value, Exception):
        runner._run_agent.side_effect = value
    event = MessageEvent(text="diagnose", source=source(), message_id="555555555555555555")
    response = await runner._handle_message_with_agent(event, event.source, "agent:main:discord:group:room", 1)
    assert response is None  # one combined delivery, no second outer-adapter copy
    adapter.send.assert_awaited_once()
    call = adapter.send.await_args
    assert call.kwargs["reply_to"] == event.message_id
    assert call.kwargs["chat_id"] == event.source.chat_id
    assert call.kwargs["metadata"]["thread_id"] == event.source.thread_id
    assert call.kwargs["metadata"]["failure_owner_ids"] == [OWNER]
    assert f"<@{OWNER}>" in call.kwargs["content"]
    assert "secret raw exception" not in call.kwargs["content"]
    assert "NO_REPLY" not in call.kwargs["content"]
    assert "remained incomplete after" not in call.kwargs["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bot,text", [(True, "NO_REPLY"), (False, "NO_REPLY"),
    (True, "The test failed because its fixture is missing.")])
async def test_success_silence_and_business_prose_do_not_alert(monkeypatch, tmp_path, bot, text):
    runner, adapter = setup(monkeypatch, tmp_path, result(text))
    event = MessageEvent(text="diagnose", source=source(bot), message_id="555555555555555555")
    response = await runner._handle_message_with_agent(event, event.source, "agent:main:discord:group:room", 1)
    adapter.send.assert_not_awaited()
    if bot and text == "NO_REPLY":
        assert response == ""
    elif text != "NO_REPLY":
        assert response == text
    else:
        assert response  # mandatory human response remains


@pytest.mark.asyncio
@pytest.mark.parametrize("opener_bot", [False, True])
async def test_terminal_request_owns_failure_and_reference(monkeypatch, tmp_path, opener_bot):
    terminal = source(not opener_bot)
    value = result(failed=True, queued_terminal_source=terminal,
                   queued_terminal_inbound_id="666666666666666666")
    runner, adapter = setup(monkeypatch, tmp_path, value)
    event = MessageEvent(text="first", source=source(opener_bot), message_id="555555555555555555")
    response = await runner._handle_message_with_agent(event, event.source, "agent:main:discord:group:room", 1)
    if terminal.is_bot:
        adapter.send.assert_awaited_once()
        assert adapter.send.await_args.kwargs["reply_to"] == "666666666666666666"
        assert response is None
    else:
        adapter.send.assert_not_awaited()
        assert response and f"<@{OWNER}>" not in response


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", [None, "all", [True], ["<@111111111111111111>"], [], [OWNER]])
async def test_profile_scoped_config_missing_or_malformed_keeps_feedback(monkeypatch, tmp_path, setting, caplog):
    import json
    runner, adapter = setup(monkeypatch, tmp_path, result(failed=True))
    other = tmp_path / "satellite"
    other.mkdir()
    (other / "config.yaml").write_text(json.dumps({"gateway": {"bot_failure_owner_ids": {"discord": setting}}}))
    event = MessageEvent(text="test", source=source(profile="satellite"), message_id="555555555555555555")
    await runner._handle_message_with_agent(event, event.source, "agent:satellite:discord:group:room", 1)
    call = adapter.send.await_args.kwargs
    expected = [OWNER] if setting == [OWNER] else []
    assert call["metadata"]["failure_owner_ids"] == expected
    assert (f"<@{OWNER}>" in call["content"]) == bool(expected)
    if not expected:
        assert "not configured or invalid" in caplog.text


@pytest.mark.asyncio
async def test_queued_and_final_failure_share_receipt_but_next_failure_is_allowed(monkeypatch, tmp_path):
    value = result(failed=True)
    runner, adapter = setup(monkeypatch, tmp_path, value)
    s = source()
    ctx = SimpleNamespace(source=s, session_key="agent:main:discord:group:room", run_generation=1,
                          stream_consumer_holder=[None], mute_notification_reply=False,
                          inbound_message_id="555555555555555555")
    await runner._run_agent_deliver_first_response(ctx, adapter, value, value, None)
    event = MessageEvent(text="test", source=s, message_id=ctx.inbound_message_id)
    for _ in range(2):
        assert await runner._hmwa_deliver_turn_response(event, s, SimpleNamespace(session_id="s"),
            ctx.session_key, 1, value, [], "failure", "", False) is None
    assert adapter.send.await_count == 1
    await runner._hmwa_deliver_turn_response(
        MessageEvent(text="next", source=s, message_id="666666666666666666"), s,
        SimpleNamespace(session_id="s"), ctx.session_key, 2, result(failed=True), [], "failure", "", False)
    assert adapter.send.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_transport_failure_never_reruns_business_or_claims_success(monkeypatch, tmp_path, raises, caplog):
    value = result(failed=True)
    runner, adapter = setup(monkeypatch, tmp_path, value)
    if raises:
        adapter.send.side_effect = RuntimeError("offline")
    else:
        adapter.send.return_value = SimpleNamespace(success=False, error="offline")
    event = MessageEvent(text="test", source=source(), message_id="555555555555555555")
    assert await runner._handle_message_with_agent(event, event.source, "agent:main:discord:group:room", 1) is None
    runner._run_agent.assert_awaited_once()
    adapter.send.assert_awaited_once()
    assert value["failed"] is True
    assert value["_bot_failure_delivery"] in {"send_failed", "send_error"}
    assert "task remains failed" in caplog.text


@pytest.mark.asyncio
async def test_sanitized_failure_and_missing_reference(monkeypatch, tmp_path):
    value = result("API_KEY=super-secret-credential-value @everyone <@&777777777777777777> " + "x" * 4000, failed=True)
    runner, adapter = setup(monkeypatch, tmp_path, value)
    event = MessageEvent(text="test", source=source(), message_id="")
    await runner._handle_message_with_agent(event, event.source, "agent:main:discord:group:room", 1)
    sent = adapter.send.await_args.kwargs
    assert sent["reply_to"] is None
    assert len(sent["content"]) < 1500
    assert "super-secret-credential-value" not in sent["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("opener_bot", [True, False])
async def test_recursive_queue_exception_uses_terminal_event(monkeypatch, tmp_path, opener_bot):
    runner, adapter = setup(monkeypatch, tmp_path, result("unused"))
    pending = MessageEvent(text="queued", source=source(not opener_bot), message_id="666666666666666666", internal=True)
    opener = MessageEvent(text="opening", source=source(opener_bot), message_id="555555555555555555")
    runner._MAX_INTERRUPT_DEPTH = 8
    runner._is_goal_continuation_event = lambda _: False
    runner._session_key_for_source = lambda _: "agent:main:discord:group:room"
    runner._prepare_profile_scoped_inbound_message_text = AsyncMock(return_value="queued")
    runner._refresh_agent_cache_message_count = AsyncMock()
    runner._intake_adapter_for = lambda _: None
    ctx = SimpleNamespace(source=opener.source, session_id="s", session_key="agent:main:discord:group:room",
        run_generation=1, _interrupt_depth=0, history=[], _status_thread_metadata=None,
        context_prompt=None, result_holder=[None])
    async def run(**kwargs):
        if kwargs.get("_interrupt_depth"):
            raise RuntimeError("private terminal exception")
        return await runner._run_agent_queued_followup(ctx, None, "queued", pending,
            "", {"interrupted": True, "messages": []}, None)
    runner._run_agent = AsyncMock(side_effect=run)
    response = await runner._handle_message_with_agent(opener, opener.source, ctx.session_key, 1)
    if pending.source.is_bot:
        adapter.send.assert_awaited_once()
        assert adapter.send.await_args.kwargs["reply_to"] == pending.message_id
        assert response is None
    else:
        adapter.send.assert_not_awaited()
        assert response


@pytest.mark.asyncio
async def test_stale_failure_never_notifies(monkeypatch, tmp_path):
    runner, adapter = setup(monkeypatch, tmp_path, result(failed=True))
    runner._is_session_run_current = lambda *_: False
    event = MessageEvent(text="test", source=source(), message_id="555555555555555555")
    assert await runner._handle_message_with_agent(event, event.source, "agent:main:discord:group:room", 1) is None
    adapter.send.assert_not_awaited()


