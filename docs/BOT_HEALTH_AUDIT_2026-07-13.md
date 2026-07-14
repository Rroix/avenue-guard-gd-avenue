# Avenue Guard Full Bot Health Audit

**Audit date:** 2026-07-13  
**Scope:** startup, storage, all cogs, all persistent workflows, configuration, external APIs, permissions, security, performance, recovery, tests, and operator documentation

## Executive Result

Avenue Guard's architecture is appropriate for its current single-server workload: one Discord process, modular cogs, an embedded SQLite-compatible replica, Turso as the durable primary, persistent Discord views, and database-backed workflow state.

This audit found and fixed failure modes concentrated around restarts, transient Turso errors, incomplete Discord member caches, and Discord/database actions that cannot be committed in one shared transaction. No known dependency vulnerabilities or high-severity static security findings remain in the audited dependency set.

The bot now has an automated regression suite. It currently contains **35 passing tests** and a repeatable quality command at `scripts/quality_check.sh`.

## What Was Reviewed

| Area | Reviewed behavior |
|---|---|
| Startup and hosting | Environment loading, database preflight, Discord login retry, Cloudflare backoff, health server, cog loading, persistent views, shutdown flushing |
| Turso and SQLite | Connection lifecycle, migrations, local replica sync, retry/reconnect behavior, atomic writes, backups, restore restrictions, runtime settings |
| Live requests | Request button, requirements, acknowledgement, validation, duplicate prevention, waves, limits, timers, editing, summaries, reviews, result delivery |
| Scheduled requests | Date/time parsing, queue behavior, open-now confirmation, edit/delete actions, atomic state changes, announcements, restart persistence |
| Weekly requests | Tracking rank, reward disable/enable, force DM, reminders, timeout/decline, submission validation, review buttons, recap and streak data |
| Tracking | Exclusions, cooldowns, buffered writes, anti-farm rules, rankings, cold member cache, weekly reset and summaries |
| Help and tickets | DM routing, FAQ flows, submissions, staff replies, ticket creation/status/inactivity/closure, transcripts, feedback, startup reconciliation |
| Moderation | Autodelete restrictions, role DMs, review-access agreement channel, admin whitelist and accepted-message cleanup |
| Forums and sticky messages | Sticky replacement, stale IDs, forum reminders, required-word matching, safe regex mode, deletion DM/log workflow |
| Background systems | Daily telemetry, daily and weekly summaries, status rotation, server icon rotation, backups and impact persistence |
| Commands and diagnostics | Permission gates, health/dashboard/config checks, repair tools, transcript search, impact exports, safe response behavior |
| Security | Secret handling, mentions, URL validation, SSRF resistance, SQL construction, regex complexity, log redaction, dependency audit |

## Main Reliability Fixes

### Storage and Turso

- Remote sync failures after a successful local commit no longer make callers repeat non-idempotent writes.
- Recoverable Hrana, invalid transaction state, Turso S3, HTTP 500, timeout, and connection-reset errors trigger reconnect/retry behavior.
- A pending remote sync no longer blocks normal local-replica reads and writes during a short Turso outage.
- Startup still performs a strict remote sync, so invalid credentials fail before Discord commands become available.
- Production now refuses to silently fall back to disposable SQLite when a Turso URL is configured but its token or replica path is unavailable.
- Database backups use SQLite's backup API and run an integrity check, including for a live replica with WAL state.
- A temporary Turso outage no longer prevents creation of a valid backup from the transactionally consistent local replica.
- Migrations create tables before indexes, expose migration failures, repair ticket sequences, and include all newer workflow columns.
- Mutable server-icon and forum-required-word settings are stored in Turso-backed runtime settings, not only the host filesystem.
- Graceful shutdown now flushes buffered tracking activity, current-day telemetry, and the Turso replica on Discord's active event loop before Pycord closes it.

### Live and Weekly Requests

- Wave submission counting changes only after a successful form and staff-message transaction.
- Per-user and per-level duplicates are checked while submission locks are held.
- Closing a wave persists an edit deadline for every pending request; old-wave requests remain editable until their own stored deadline.
- Automatic closures, manual closures, replacement waves, scheduled openings, and request limits update state consistently.
- Scheduled openings remain pending when another wave is active and are consumed atomically when opened.
- GD validation requires exact level IDs, combines GDBrowser and Boomlings conservatively, and auto-blocks missing IDs only when every enabled provider succeeds and agrees.
- Provider requests are rate-limited independently, cached, deduplicated while in flight, and protected by a circuit breaker.
- Request templates are bounded to Discord's field and aggregate embed limits.
- Review result notifications are delivered before review state is finalized; failed notifications do not falsely mark a request reviewed.
- The repair command now recreates missing live and weekly pending cards and disables buttons on both live and weekly reviewed cards.
- Detached request refresh work is tracked, logged, and cancelled cleanly during cog unload.

### Tracking and Summaries

- Activity increments are buffered and requeued if persistence fails; cooldown timestamps are not committed for counts that failed to save.
- Cold Discord member caches no longer erase valid members from `/tracking top`, `/tracking me`, or eligible totals.
- The weekly reward run resolves its bounded candidate list and retries the whole run before writing its completion marker if Discord member lookup fails transiently.
- Weekly recap anti-farm totals are bounded to the exact audited week instead of leaking events from the following week.
- Daily snapshots are persisted before the summary is sent to Discord.
- A transient daily-summary exception is contained so the recurring task remains alive for the next attempt.
- Voice sessions are split at the day boundary so historical voice totals are not duplicated or lost.
- Daily and command-by-user telemetry is included in durable impact data.

### Help and Tickets

- DM help and ticket creation fetch members when Discord's local cache is incomplete.
- Help sessions expire and stale preview/session data is rejected.
- Ticket creation and cooldown writes are atomic; failed database setup removes the newly created Discord channel.
- Ticket opening messages follow status changes caused by commands, user messages, staff messages, inactivity prompts, and closure.
- Startup reconciles open database rows whose Discord ticket channels disappeared while the bot was offline.
- Ticket transcript delivery, indexing, channel deletion, final status, and satisfaction prompts have compensating cleanup paths.
- A transcript message is removed if indexing fails or channel deletion fails, preventing duplicate or misleading archived artifacts.
- Satisfaction prompts are persistent across restarts, serialized against duplicate clicks, limited to seven days, and deleted if their database pointer cannot be saved.
- Failure to restore satisfaction views no longer prevents the ticket inactivity scanner from starting.

### Moderation, Forums, Responses, and Icons

- The review-access phrase is case-insensitive, allows at most one character of variation, rejects appended disclaimers, grants the configured role, and deletes the accepted message afterward.
- Role DMs are serialized and deduplicated, and all fallback messages suppress accidental mentions.
- Sticky replacement treats an already-deleted saved message as normal recovery rather than an error.
- Forum deletion checks inspect the title and initial user content, use bounded input, and fail safely when history cannot be read.
- Regex required-word mode rejects grouping, lookarounds, backreferences, nested quantifiers, and repeated wildcard patterns.
- Forum configuration changes roll back in memory if durable persistence fails.
- Message-response rules with no usable output are reported and no longer stop a later valid matching rule.
- Icon URLs reject credentials, localhost, and literal private IPs; downloads validate DNS, redirects, MIME type, and size.
- Icon changes are serialized between automatic and slash-command actions, and expiring Discord attachment URLs produce a clear warning.

## Security Review

- Error logs redact Discord tokens, Turso tokens, database URLs with credentials, and JWT-shaped secrets.
- Error posts use suppressed mentions and deduplicate repeated infrastructure failures.
- User-supplied showcase URLs are validated but never fetched by the bot.
- Server-icon fetching blocks direct private-network targets and validates each redirect.
- SQL values use bound parameters. The former generic impact grouping helper was replaced by an allowlist of complete SQL statements.
- Remaining dynamic SQL fragments contain only hardcoded clauses or generated `?` placeholders.
- Persistent views validate ownership or role permissions before changing state.
- Bandit reports **zero medium or high findings** after documented Render health-bind and fixed SQL-fragment exceptions.
- `pip-audit -r requirements.txt` reports **no known vulnerabilities**.
- The required installer version was raised to `pip>=26.1.2` because the previous Render build tool version had known advisories.

## Automated Verification

The following checks pass:

```text
Python compileall                          PASS
Ruff correctness checks                   PASS
Pytest                                    35 passed
Bandit medium/high security scan          PASS
pip-audit production requirements         PASS
config.json and responses.json parsing    PASS
All eight cogs load in a clean smoke test PASS
git diff whitespace validation            PASS
```

Important regression tests include:

- migration from a completely empty database;
- transaction rollback after a later statement fails;
- 25 concurrent, unique ticket IDs;
- backup integrity and latest-write inclusion;
- exact GD ID parsing and conservative provider disagreement;
- request ID/URL validation, type aliases, edit deadlines, and month/day scheduling;
- cold-cache tracking rank preservation and known-role exclusion;
- daily persistence before Discord delivery;
- runtime config round-trip through the database;
- graceful shutdown flushing each runtime data source exactly once;
- review phrase matching, icon URL safety, regex safety, and secret redaction;
- refusal to silently degrade configured Turso storage.

## Operational Limits

Some guarantees depend on external systems and cannot be proven offline:

1. Discord permissions, role hierarchy, real channel types, and live button/modal delivery must be checked with `/bot config_check`, `/bot dashboard`, and the manual checklist after deployment.
2. GDBrowser, Boomlings, Turso, Discord, image hosts, and Render can become unavailable. The bot now retries, fails conservatively, or records a repairable state, but cannot make an external service available.
3. Discord messages and database commits are separate systems. The bot uses ordering, locks, idempotent states, and compensating deletion, but a process termination at the exact network/commit boundary can still require `/requests repair` or ticket reconciliation.
4. Background locks assume one active bot process. Keep Render at one worker (`WEB_CONCURRENCY=1`) unless a future version replaces process-local locks with distributed leases.
5. Upload restore is intentionally disabled while Turso is the primary database. Remote restoration must use Turso's backup/import tooling to avoid replacing only the local replica.

## Recommended Deployment Check

After deploying this audit build:

1. Confirm `/status` reports `online` rather than `startup_error`.
2. Run `/bot dashboard`, then its Config and Repair Tips views.
3. Run `/bot config_check` and resolve every reported channel, role, time, template, and provider issue.
4. Run `/requests repair` once to reconcile old request and weekly review messages.
5. Run `/tracking me` and `/tracking top` after sending one eligible message.
6. Run `/bot backup` and confirm the Discord attachment opens as a valid SQLite database.
7. Complete the high-risk sections in `TEST_CHECKLIST.md`: requests, scheduled openings, weekly DM, ticket close/transcript, daily summary, sticky replacement, and review-access agreement.
