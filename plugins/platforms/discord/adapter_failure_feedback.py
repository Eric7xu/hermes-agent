"""Discord terminal-failure send policy, independent of routine mention defaults."""
from gateway.bot_failure_feedback import validated_owner_ids


def failure_send_options(metadata, reply_to, channel):
    from plugins.platforms.discord.adapter import discord

    if not isinstance(metadata, dict) or "failure_owner_ids" not in metadata:
        return None
    owners = validated_owner_ids(metadata["failure_owner_ids"])
    reference = None
    if reply_to:
        reference = discord.MessageReference(message_id=int(reply_to), channel_id=int(channel.id),
                                             fail_if_not_exists=False)
    return {
        "reference": reference,
        "allowed_mentions": discord.AllowedMentions(
            everyone=False, roles=False, users=[discord.Object(id=int(uid)) for uid in owners],
            replied_user=False),
    }
