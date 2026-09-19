# Avenue Guard Components V2 Design System

## Purpose

Avenue Guard renders rich Discord messages with native Components V2. The
presentation boundary is centralized in `utils/components_v2.py`; individual
features can keep using `discord.Embed` as an internal content model while the
outgoing Discord payload contains containers, text displays, media, files,
separators, and action rows instead of a classic embed.

This keeps request reviews, support sessions, tracking, summaries, moderation
logs, release approvals, sticky notices, and error reports visually consistent
without duplicating layout code in every cog.

## Visual Grammar

Each former embed becomes one accent-colored container:

```text
Container
|- Section: author, title, description + optional thumbnail
|- Separator
|- Text Display: labelled information groups
|- Media Gallery: optional main image
|- File: visible attachments not already used as media
|- Separator
|- Text Display: compact footer and timestamp
|- Separator
`- Action Row(s): the existing buttons and selects
```

Titles use a level-two heading, labels use bold text, and low-priority metadata
uses Discord's small-text markdown. Long text is split at paragraph, line, or
word boundaries before it reaches Discord's component limits. A message never
mixes V2 components with classic `content` or `embeds` fields.

Historical messages use a two-step migration. The adapter first clears classic
content and embeds while the message is still legacy, then installs the new
container and V2 flag. Existing V2 cards receive V2-only payloads, and
controls-only edits preserve the card layout while replacing its action rows.

## Interaction Compatibility

`AvenueDesignerView` transfers existing button and select objects into V2 action
rows. Their custom IDs, disabled state, callback functions, timeout, persistence,
and interaction checks remain intact. This is important for request review,
release approval, ticket controls, and all persistent views registered during
startup.

The adapter covers channel and DM sends, interaction responses and edits,
webhook/follow-up sends, message edits, and webhook-message edits. Plain text
messages without embeds are deliberately unchanged.

## Durable State Boundary

Rendered Discord messages are presentation, not storage. Workflows that once
read IDs or status back from embed fields now resolve them from Turso using the
Discord message ID. Compatibility readers remain for old classic-embed history,
and transcript/sticky matching can read V2 text displays.

Embed dictionaries stored in the durable outbox remain valid internal payloads.
They are converted only at Discord delivery time, so retries and historical
outbox rows do not need a data migration.

## Attachments And Media

Components V2 does not display raw attachments automatically. The adapter adds
a File component for each attached file unless that attachment is already
referenced by a thumbnail or media gallery. This keeps transcripts, evidence,
audit exports, and image previews visible.

## Irreversible Message Flag

Discord's `IS_COMPONENTS_V2` flag cannot be removed from a message after that
message is converted. Avenue Guard therefore keeps the adapter enabled on every
restart and always edits a V2 message with another V2 layout. Historical classic
messages remain readable and are upgraded the next time the bot edits them.

## Testing Contract

Automated coverage verifies container structure, color accents, text grouping,
media and attachment exposure, callback/custom-ID preservation, persistent
controls, delivery-boundary installation, nested component text extraction, and
the guarantee that plain messages are not rewritten.
