# Bot failure feedback

Terminal Bot-origin failures are request feedback, not routine automatic notices.
The gateway returns bounded, secret-redacted failure text in the original channel/thread,
referenced to the actual failed inbound message when available. This also applies to the
terminal request in a queued chain. A healthy `NO_REPLY` remains silent, including in a
room whose human policy requires a reply. Model-authored troubleshooting prose is unchanged.
Busy acknowledgments, status/progress, compression notices and usage footers remain quiet.
Admission, authorization, loop limits, generation checks and diagnostic mute remain in force.

## Same-channel owner notification (Discord)

In the **serving runtime profile's** `config.yaml`, explicitly set:

```yaml
gateway:
  bot_failure_owner_ids:
    discord: ["111111111111111111"] # replace with the intended human owner's user ID
```

This non-secret setting is a list of at most ten decimal Discord user-ID strings
(17–20 digits). It is not an allowlist and grants no inbound permission. The entire
list fails closed if malformed. No recipient is inferred from admins, allowed users,
message text, the requesting Bot or a different profile. Missing/invalid config logs
`owner alert not configured or invalid`; requester failure feedback is still sent.

One final message includes both failure feedback and the configured owner mention(s).
Discord's per-send `AllowedMentions` allows only these users; role/everyone mentions and
reply-author pings are disabled. The actual reply reference is retained even when ordinary
replies are configured off. A missing reference is not invented. A rejected/deleted reference
uses the adapter's existing unreferenced fallback. No DM or separate alert channel is used;
a forum parent without its original thread is refused rather than creating a new post.

The receiving transport is selected by the native delivery resolver. The native final-delivery
ledger stores the owner policy and reference alongside its existing obligation so bounded
recovery retains mention safety. A same-turn receipt prevents duplicate queue/final/error
notifications; it does not suppress later failed requests. A failed alert transport is logged
as failed, never as successful work, and does not re-execute the request. A total transport
failure cannot guarantee either recipient receives the message; native bounded ledger recovery
handles delivery retry, not business retry. Live delivery still requires owner-authorized deployment.
