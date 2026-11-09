"""New-base queued terminal author must own runtime notice policy, not the opener."""
from unittest.mock import AsyncMock
import pytest
from tests.gateway.test_gateway_silence_tokens import _event, _runner, _source


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_bot", [False, True])
@pytest.mark.parametrize("result", [
    {"final_response": "(empty)"},
    {"final_response": "NO_REPLY", "failed": True, "error": "provider failure"},
])
async def test_queued_terminal_author_owns_failure_notices(monkeypatch, tmp_path, terminal_bot, result):
    runner = _runner(monkeypatch, tmp_path)
    opener, terminal = _source(), _source()
    opener.is_bot, terminal.is_bot = not terminal_bot, terminal_bot
    runner._run_agent = AsyncMock(return_value={**result, "messages": [],
        "queued_terminal_source": terminal, "queued_terminal_inbound_id": "43",
        "queued_terminal_display_kind": None, "queued_terminal_reply_expected": True})
    event = _event(reply_expected=True); event.source = opener
    response = await runner._handle_message_with_agent(event, opener, "lane", 1)
    assert bool(response) is not terminal_bot


@pytest.mark.parametrize("terminal_bot", [False, True])
def test_queued_terminal_author_owns_runtime_footer(monkeypatch, tmp_path, terminal_bot):
    runner = _runner(monkeypatch, tmp_path)
    opener, terminal = _source(), _source()
    opener.is_bot, terminal.is_bot = not terminal_bot, terminal_bot
    monkeypatch.setattr("gateway.runtime_footer.build_footer_line", lambda **_: "usage footer")
    monkeypatch.setattr("gateway.run._load_gateway_config", dict)
    footer = runner._hmwa_runtime_footer_line({"queued_terminal_source": terminal}, opener, 1)
    assert footer == ("" if terminal_bot else "usage footer")
