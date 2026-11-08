"""Opt-in guild-scoped bot handoffs, verified from Discord transport metadata.

The policy belongs to the receiving adapter, never ambient environment or message text IDs.
"""

import asyncio
import logging

import discord

from gateway.platforms._shared import platform_gate_env

logger = logging.getLogger("plugins.platforms.discord.adapter")


def in_collaboration_guild(adapter, message):
    rules = adapter.config.extra.get("bot_collaboration_guilds", [])
    guild_id = str(getattr(getattr(message, "guild", None), "id", ""))
    return (
        not isinstance(message.channel, discord.DMChannel)
        and isinstance(rules, list)
        and guild_id.isascii() and guild_id.isdecimal() and int(guild_id) > 0
        and guild_id in rules
        and getattr(message.author, "bot", False) is True
        and message.author.id != getattr(adapter._client.user, "id", None)
    )


def own_reply(adapter, message):
    reference = getattr(message, "reference", None)
    if (message.type != discord.MessageType.reply or reference is None
            or getattr(message, "guild", None) is None
            or isinstance(message.channel, discord.DMChannel)):
        return False
    target = getattr(reference, "resolved", None) or getattr(reference, "cached_message", None)
    own_id = getattr(adapter._client.user, "id", None)
    return bool(
        own_id is not None
        and getattr(getattr(target, "author", None), "id", None) == own_id
        and getattr(target, "id", None) is not None
        and target.id == getattr(reference, "message_id", None)
        and getattr(reference, "guild_id", None) == message.guild.id
        and getattr(reference, "channel_id", None) == message.channel.id
        and getattr(getattr(target, "guild", None), "id", None) == message.guild.id
        and getattr(getattr(target, "channel", None), "id", None) == message.channel.id
    )


def scoped_reply_candidate(adapter, message):
    """The legacy exception grants only mention-gate relief for one exact transport scope."""
    rules = adapter.config.extra.get("bot_reply_allowlist", [])
    if (not isinstance(rules, list) or not rules
            or message.type != discord.MessageType.reply
            or getattr(message.author, "bot", False) is not True
            or isinstance(message.channel, discord.DMChannel)):
        return False
    scope = {
        "guild_id": str(getattr(getattr(message, "guild", None), "id", "")),
        "channel_id": str(getattr(message.channel, "id", "")),
        "author_id": str(getattr(message.author, "id", "")),
    }
    return (all(value.isascii() and value.isdecimal() and int(value) > 0 for value in scope.values())
            and any(isinstance(rule, dict) and rule == scope for rule in rules))


def addressed_collaborator(adapter, message):
    return in_collaboration_guild(adapter, message) and (
        adapter._self_is_raw_mentioned(message) or own_reply(adapter, message)
    )


async def resolve_collaboration_reply(adapter, message):
    """Only fetch a same-channel reply for an eligible bot; a snowflake alone proves nothing."""
    if (not (in_collaboration_guild(adapter, message) or scoped_reply_candidate(adapter, message))
            or adapter._get_allow_bots() == "none"
            or adapter._self_is_raw_mentioned(message)
            or message.type != discord.MessageType.reply
            or adapter._dedup.contains(str(message.id))):
        return
    reference = getattr(message, "reference", None)
    if (reference is None or getattr(reference, "resolved", None) is not None
            or getattr(reference, "cached_message", None) is not None
            or getattr(reference, "guild_id", None) != message.guild.id
            or getattr(reference, "channel_id", None) != message.channel.id
            or not isinstance(getattr(reference, "message_id", None), int)
            or reference.message_id <= 0):
        return
    keys = adapter._discord_channel_keys(message, adapter._get_parent_channel_id(message.channel))
    ignored = adapter._get_ignored_channels()
    if "*" in ignored or keys & ignored:
        return
    try:
        target = await asyncio.wait_for(message.channel.fetch_message(reference.message_id), timeout=10)
    except (discord.HTTPException, OSError, TimeoutError):
        return
    # Admission still verifies returned ID, author, guild AND channel.
    reference.resolved = target


class DiscordAdmissionMixin:
    def _is_scoped_own_bot_reply(self, message):
        return scoped_reply_candidate(self, message) and own_reply(self, message)

    def _collaboration_bot_admitted(self, message, guild_addressed):
        allow_bots = self._get_allow_bots()
        if allow_bots == "none":
            return False
        # No continuation window or free-response setting permits unaddressed guild chatter.
        if in_collaboration_guild(self, message):
            return guild_addressed
        if self._is_scoped_own_bot_reply(message):
            return True
        continuation = self._is_bot_tag_debounce_continuation(message)
        if allow_bots == "mentions" and not self._self_is_explicitly_mentioned(message) and not continuation:
            return False
        return not (self._discord_bots_require_inline_mention()
                    and not self._self_is_raw_mentioned(message) and not continuation)

    def _discord_message_admission(self, message, *, claim):
        """Return (admitted, role_authorized) without changing native auth or dedup ownership."""
        message_id = str(getattr(message, "id", ""))
        if claim:
            if self._dedup.is_duplicate(message_id):
                return False, False
        elif self._dedup.contains(message_id):
            return False, False
        if message.author == self._client.user:
            return False, False
        if message.type not in {discord.MessageType.default, discord.MessageType.reply}:
            return False, False
        role_authorized = False
        guild_addressed = addressed_collaborator(self, message)
        if getattr(message.author, "bot", False):
            if not self._collaboration_bot_admitted(message, guild_addressed):
                return False, False
        else:
            msg_guild = getattr(message, "guild", None)
            is_dm = isinstance(message.channel, discord.DMChannel) or msg_guild is None
            msg_channel_ids = None
            if not is_dm:
                msg_channel_ids = {str(message.channel.id)}
                parent_id = self._get_parent_channel_id(message.channel)
                if parent_id:
                    msg_channel_ids.add(parent_id)
            if not self._is_allowed_user(
                str(message.author.id), message.author, guild=msg_guild, is_dm=is_dm,
                channel_ids=msg_channel_ids,
            ):
                self._warn_if_fail_closed_default()
                return False, False
            role_authorized = bool(getattr(self, "_allowed_role_ids", set()))
        if not guild_addressed and not self._mentions_admit_message(message):
            return False, False
        return True, role_authorized

    def _mentions_admit_message(self, message):
        raw_self_mention = self._self_is_explicitly_mentioned(message)
        if isinstance(message.channel, discord.DMChannel) or not (message.mentions or raw_self_mention):
            return True
        other_bots_mentioned = any(
            mentioned.bot and mentioned != self._client.user for mentioned in message.mentions)
        if other_bots_mentioned and not raw_self_mention:
            return False
        ignore_no_mention = platform_gate_env("DISCORD_IGNORE_NO_MENTION", "true").lower() in {"true", "1", "yes"}
        if ignore_no_mention and not raw_self_mention and not other_bots_mentioned:
            # A joined thread is not someone else's conversation; preserve the native exemption.
            if not self._in_bot_thread(message):
                parent_id = None
                if hasattr(message.channel, "parent_id") and message.channel.parent_id:
                    parent_id = str(message.channel.parent_id)
                free_channels = self._discord_free_response_channels()
                channel_keys = self._discord_channel_keys(message, parent_id)
                if "*" not in free_channels and not (channel_keys & free_channels):
                    logger.debug(
                        "[%s] admission: dropping message %s — mentions others, not self, "
                        "not a bot thread, channel not free-response",
                        self.name, getattr(message, "id", "?"))
                    return False
        return True
