"""Opt-in guild-scoped, explicitly addressed bot collaboration.

Only transport message metadata establishes guild membership and reply authorship.
The policy is per adapter; it never reads ambient environment or grants human access.
"""
import asyncio


def in_collaboration_guild(adapter, message):
    rules = adapter.config.extra.get("bot_collaboration_guilds", [])
    guild_id = str(getattr(getattr(message, "guild", None), "id", ""))
    return (
        isinstance(rules, list)
        and guild_id.isascii() and guild_id.isdecimal() and int(guild_id) > 0
        and guild_id in rules
        and getattr(message.author, "bot", False) is True
        and message.author.id != getattr(adapter._client.user, "id", None)
    )


def own_reply(adapter, message):
    from discord import MessageType

    reference = getattr(message, "reference", None)
    if message.type != MessageType.reply or reference is None:
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


def addressed_collaborator(adapter, message):
    return in_collaboration_guild(adapter, message) and (
        adapter._self_is_raw_mentioned(message) or own_reply(adapter, message)
    )


async def resolve_collaboration_reply(adapter, message):
    """Fetch only a same-channel guild reply, once, using the receiving SDK channel."""
    from discord import HTTPException, MessageType

    if (not in_collaboration_guild(adapter, message)
            or adapter._get_allow_bots() == "none"
            or adapter._self_is_raw_mentioned(message)
            or message.type != MessageType.reply
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
    except (HTTPException, OSError, TimeoutError):
        return  # Missing/deleted/inaccessible references never establish authorship.
    # own_reply checks returned ID, author, guild AND channel before granting anything.
    reference.resolved = target
