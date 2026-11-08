"""Terminal Bot failure feedback, separate from quiet routine-notice policy."""
from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass, field

from gateway.config import Platform
from gateway.platforms.event import MessageEvent

logger = logging.getLogger(__name__)
_SNOWFLAKE = re.compile(r"[1-9][0-9]{16,19}\Z")


@dataclass
class _FailureDeliveryScope:
    runner: object
    session_key: str
    generation: int
    receipts: dict = field(default_factory=dict)


_failure_scope: ContextVar[_FailureDeliveryScope | None] = ContextVar("bot_failure_delivery", default=None)


def failure_delivery_scope(handler):
    """Share receipts across queue/final/error objects only for this handler invocation.

    Context-local ownership isolates simultaneous turns and is reset even on cancellation. The
    native run key/generation also survives exception paths that no longer have a TurnContext.
    """
    from functools import wraps

    @wraps(handler)
    async def scoped(runner, event, source, _quick_key, run_generation):
        token = _failure_scope.set(_FailureDeliveryScope(runner, _quick_key, run_generation))
        try:
            return await handler(runner, event, source, _quick_key, run_generation)
        finally:
            _failure_scope.reset(token)
    return scoped


def _delivery_receipts(runner, event, source, inbound, session_key, generation):
    scope = _failure_scope.get()
    if scope is not None and scope.runner is runner:
        session_key, generation, receipts = scope.session_key, scope.generation, scope.receipts
    else:
        generation = generation if generation is not None else getattr(event, "_bot_failure_generation", None)
        receipts = event.__dict__.setdefault("_bot_failure_receipts", {})
    # This runs after the queued stream wait, not merely after _run_agent returns to its caller.
    if generation is not None and not runner._is_session_run_current(session_key, generation):
        return None, None
    event._bot_failure_generation = generation
    identity = (generation, source.platform, source.profile, source.chat_id, source.thread_id, inbound)
    return receipts, identity


def validated_owner_ids(value):
    """Fail closed on the whole setting, never coerce booleans, names or mention text."""
    if not isinstance(value, list) or len(value) > 10:
        return []
    if any(not isinstance(uid, str) or not _SNOWFLAKE.fullmatch(uid) for uid in value):
        return []
    return list(dict.fromkeys(value))


def is_terminal_failure(result):
    """Classify native results before normalization; model prose is not an error signal."""
    from gateway.response_filters import is_intentional_silence_agent_result
    from gateway.run import _is_gateway_hidden_reasoning_incomplete_turn

    if result.get("failed") or result.get("_terminal_failure") or result.get("partial"):
        return True
    text = result.get("final_response") or ""
    if is_intentional_silence_agent_result(result, text):
        return False
    if result.get("interrupted") and result.get("api_calls", 0):
        return False  # an intentional stop/steer, not a failed request
    return bool(result.get("partial") or _is_gateway_hidden_reasoning_incomplete_turn(result)
                or not text.strip() or text == "(empty)")


def failure_text(result, source, response=""):
    """Use native safe copy; no raw exception detail and no media/control directives."""
    from gateway.run import (_normalize_empty_agent_response, _sanitize_gateway_final_response,
                             _is_gateway_hidden_reasoning_incomplete_turn)
    from gateway.response_filters import is_intentional_silence_response
    from agent.i18n import t

    if _is_gateway_hidden_reasoning_incomplete_turn(result):
        response = t("gateway.errors.generic_failed")
    if not response or response == "(empty)" or is_intentional_silence_response(response):
        response = _normalize_empty_agent_response(result, "")
    if is_intentional_silence_response(response):
        response = t("gateway.errors.generic_failed")
    response = _sanitize_gateway_final_response(source.platform, response)
    # Hidden-reasoning exhaustion intentionally normalizes to empty for humans. A Bot needs a
    # concrete failure receipt instead, not the sentinel or a fabricated successful answer.
    response = response or t("gateway.errors.generic_failed")
    return response[:1400]


def _owner_ids(runner, source):
    from gateway.run import _load_gateway_config

    if source.platform != Platform.DISCORD:
        return []
    try:
        config = _load_gateway_config(runner._resolve_profile_home_for_source(source) / "config.yaml")
        value = config.get("gateway", {}).get("bot_failure_owner_ids", {}).get("discord")
        owners = validated_owner_ids(value)
    except Exception:
        logger.warning("Bot failure owner configuration unreadable; requester feedback retained", exc_info=True)
        return []
    if not owners:
        logger.warning("Bot failure owner alert not configured or invalid; requester feedback retained")
    return owners


async def deliver_bot_failure(runner, event, source, result, response, session_key, generation):
    """One combined referenced final, through the owning transport and native delivery ledger.

    The handler's context-local receipt spans distinct queue/final/error event/result objects.
    A rejected send stays in the ledger for bounded recovery; business work is never rerun here.
    """
    from gateway.platforms.base import BasePlatformAdapter

    from gateway.warning_notifications import diagnostic_wake_muted
    if diagnostic_wake_muted(event):
        return
    source = result.get("queued_terminal_source", source)
    inbound = result.get("queued_terminal_inbound_id", event.message_id)
    receipts, identity = _delivery_receipts(runner, event, source, inbound, session_key, generation)
    if receipts is None or identity in receipts or result.get("_bot_failure_delivery"):
        return
    receipts[identity] = "attempting"
    result["_bot_failure_delivery"] = "attempting"
    owners = _owner_ids(runner, source)
    text = failure_text(result, source, response)
    if owners:
        text = " ".join(f"<@{uid}>" for uid in owners) + "\n" + text
    metadata = dict(runner._thread_metadata_for_source(source, inbound) or {})
    metadata.update(notify=True, failure_owner_ids=owners)
    delivery_event = MessageEvent(text="", source=source, message_id=inbound or "")
    delivery_event._failure_delivery = {"owner_ids": owners, "reply_to": inbound or None}
    # No new adapter lookup policy: native resolver preserves receiving transport provenance.
    adapter = runner._delivery_adapter_for(source)
    status = "transport_unavailable"
    try:
        if isinstance(adapter, BasePlatformAdapter):
            sent, _ = await adapter.send_final_ledgered(
                delivery_event, session_key, text, metadata, reply_to=inbound or None)
        elif adapter is not None:
            sent = await adapter.send(chat_id=source.chat_id, content=text,
                                      reply_to=inbound or None, metadata=metadata)
        else:
            sent = None
        if sent is not None:
            status = "delivered" if sent.success else "send_failed"
    except Exception:
        status = "send_error"
        logger.warning("Bot failure feedback transport raised; task remains failed", exc_info=True)
    receipts[identity] = result["_bot_failure_delivery"] = status
    if status == "delivered":
        runner._note_bot_reply(source)
    else:
        logger.warning("Bot failure feedback/owner alert %s; task remains failed", status)
