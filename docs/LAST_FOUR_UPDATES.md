# Avenue Guard: Detailed Record of the Last Four Updates

This document records the four development updates beginning with the change from one generic **Send** result to the five recommendation types **Rate**, **Feature**, **Epic**, **Legendary**, and **Mythic**.

The private historical-audit release (`3.23.0`) and everything before it are intentionally outside this document. The first update covered here is Priority Point System v1 (`3.24.0`). The following three development updates belong to the `3.25.0` public-recommendation release and its immediate hardening work.

## Scope at a Glance

| Update | Version context | Main purpose |
| --- | --- | --- |
| 1 | `3.24.0` | Replace generic sending in new request waves with five recommendation types and create the durable Priority Point System |
| 2 | `3.25.0` | Improve recommendation messages, expose permanent public level information, remove reminder-message clutter, and rebuild the first hosted validation fallback |
| 3 | `3.25.0` visual/configuration pass | Add the exact five supplied faces as Discord application emojis and connect their final IDs to the send-type menu |
| 4 | `3.25.0` reliability pass | Add GDHistory as an independent positive-evidence provider and close validation/cache gaps exposed by Render |

---

## Update 1: Priority Point System v1 and Five Recommendation Types

### 1. Review workflow changed for new waves

The old review workflow offered one generic **Send** button. For newly created PPS waves, that was replaced with one persistent **Send type** selection menu containing exactly:

| Stored value | Displayed recommendation |
| --- | --- |
| `rate` | Rate |
| `feature` | Feature |
| `epic` | Epic |
| `legendary` | Legendary |
| `mythic` | Mythic |

Choosing a type opens the existing optional review modal. A successful recommendation still stores the high-level result as `sent` for compatibility with existing summaries and result handling, while the new `send_type` field stores the selected recommendation tier.

The wording was deliberately changed from claiming that a level had been sent to a moderator to saying that it had been **recommended** and added to an outreach queue. Actual moderator outreach remains a separate human action.

`Reject`, `Other`, and `Recheck` remained available. Weekly-request reviews remained on the legacy four-button workflow and were not moved into PPS.

### 2. Safe rollout boundary

Database schema 8 added `review_system_version` to request-wave state and individual submissions. Existing rows receive the migration default `legacy`.

The rollout behaves as follows:

```text
deployment
   |
   +-- current wave remains legacy
   +-- new submissions to that wave remain legacy
   +-- weekly reviews remain legacy
   |
   `-- next genuinely new wave is saved as pps_v1
           |
           `-- every submission copies pps_v1 from the wave
```

This prevents a restart, repair command, or deployment from silently changing controls on an already active request wave. Both legacy and PPS persistent Discord views are registered during startup, and request repair chooses the correct view from the saved row rather than a global runtime switch.

### 3. Atomic review and queue creation

The review transaction was expanded so that a PPS recommendation and its queue entry are treated as one authoritative operation.

The modal submission now:

1. Rechecks reviewer permissions.
2. Reloads the request by guild and Discord message ID.
3. Requires the request to still be pending.
4. Requires a valid recommendation type for a PPS `sent` result.
5. Atomically marks the request reviewed.
6. Inserts one outreach-queue row only when the exact reviewed row, reviewer, timestamp, type, and workflow version match.
7. Creates result-channel, DM, request-card edit, and workflow-event outbox intents only for the winning review.
8. Reads the result back before telling Discord that the operation succeeded.

Unique keys, conditional inserts, and idempotency keys prevent two reviewers or retried Discord interactions from creating duplicate recommendations or queue rows.

### 4. Deterministic priority formula

PPS v1 introduced a deterministic operational score:

```text
P = F + G + H
```

This is a queue priority, not a probability and not a prediction that a moderator will rate the level.

#### Prestige component F

```text
F = 1.8^T - 1
```

| Recommendation | T | Approximate F |
| --- | ---: | ---: |
| Rate | 0.00 | 0.00 |
| Feature | 1.25 | 1.09 |
| Epic | 2.50 | 3.35 |
| Legendary | 3.75 | 8.06 |
| Mythic | 5.00 | 17.90 |

The reviewer fixes this component by choosing the recommendation type. Background workers never change that human decision.

#### Creator-opportunity component G

```text
G = max(0, ln(0.25 / (0.06 * C + 0.01)))
```

`C` is the current Creator Point count of the Geometry Dash account that uploaded the level. It is not the requester's Discord account and is not averaged across listed collaborators.

| Current CP | Approximate G |
| ---: | ---: |
| 0 | 3.22 |
| 1 | 1.27 |
| 2 | 0.65 |
| 3 | 0.27 |
| 4 or more | 0.00 |
| Unknown | `null` |

Unknown CP is never converted to zero. A failed request, malformed profile, provider outage, or uploader identity conflict leaves both G and the total priority incomplete.

#### Waiting component H

```text
H = 1.5 * min(W, 4)^1.5
```

`W` is the number of completed successful outreach cycles in which the level was eligible at the start, did not reach a moderator, and remained eligible when the cycle completed.

| Waiting cycles | Approximate H |
| ---: | ---: |
| 0 | 0.00 |
| 1 | 1.50 |
| 2 | 4.24 |
| 3 | 7.79 |
| 4 or more | 12.00 |

The raw waiting count keeps increasing for evidence and tie-breaking. Only the score contribution is capped.

### 5. Durable outreach queue

Schema 8 added six PPS tables:

| Table | Purpose |
| --- | --- |
| `level_outreach_queue` | Current queue state, score components, CP, waiting, current level state, and fixed-window outcome |
| `level_outreach_cycles` | Owner-managed outreach-cycle lifecycle and private notes |
| `level_outreach_cycle_entries` | Immutable evidence snapshot for every candidate eligible when a cycle starts |
| `level_outreach_attempts` | Planned, attempted, confirmed-submission, and failed outreach records |
| `level_outreach_cp_snapshots` | Append-only CP lookup and override history |
| `level_outreach_level_snapshots` | Append-only current level/rating lookup history |

Indexes were added for queue ranking, level/account lookup, cycle entries, attempts, and snapshot history. Queue uniqueness is enforced per request message and per requester within a wave.

The queue ranks complete scores before incomplete scores. Ties are resolved by total priority, uncapped waiting count, oldest queue time, and finally stable queue ID.

### 6. Outreach cycles and evidence

An outreach cycle represents a real staff round of trying to place candidate levels in front of Geometry Dash moderators. It is separate from a request wave.

Starting a cycle snapshots every currently queued candidate. Recommendations added after the start are not retroactively included in that cycle.

Attempt states were added:

| State | Meaning |
| --- | --- |
| `planned` | Staff intends to use a route |
| `attempted` | Outreach was attempted but no moderator submission is confirmed |
| `submitted_to_mod` | Staff confirms that the level reached a moderator |
| `failed` | The route failed |

Route types are `direct`, `network`, `stream`, `event`, and `other`. Targets and notes remain private.

A successful cycle must contain at least one confirmed moderator submission. Submitted entries do not gain waiting points. Other eligible start-of-cycle entries gain one waiting cycle exactly once and return to the ranked queue. Completing the same cycle again cannot increment them twice.

A cycle with no confirmed submission is cancelled or closed unsuccessfully and adds no waiting points.

### 7. Queue lifecycle and outcomes

The durable queue state machine supports:

```text
queued -> in_cycle -> awaiting_outcome -> rated
   |          |
   |          +-- cycle completes without submission -> queued with W + 1
   |
   +-- reliable rating observation -> rated
   +-- reliable level-unavailable observation -> invalid
   +-- owner action -> withdrawn
```

Confirmed moderator submission starts a fixed 30-day outcome window. Provider observations are stored, and the eventual rated/not-rated result is based on evidence collected after submission rather than an assumption made during review.

### 8. Creator Points and metadata refresh

The PPS worker reuses the bot's existing HTTP session, provider pacing, circuit breakers, and normalized GD results.

Creator Points are fetched through the direct Boomlings profile endpoint. The parser verifies the returned GD user ID, GD account ID, and CP field before accepting the value. A mismatched account or missing CP remains unknown.

The first successful CP lookup fills `creator_points_at_recommendation` without later overwriting it. New successful checks update `current_creator_points`. Every success or failure is appended to the snapshot history.

### 9. Supervised background maintenance

A supervised `priority.maintenance` worker was added. It performs bounded sequential batches rather than large concurrent provider bursts.

Maintenance handles:

- Stale level metadata refreshes.
- Uploader identity resolution.
- Current CP refreshes.
- Score recomputation after valid CP data arrives.
- Reliable rated or unavailable state transitions.
- Fixed 30-day outcome checks.
- Shorter retry windows for failed external lookups.

One provider failure is logged and deferred without killing the worker or blocking a reviewer interaction.

### 10. Owner-only PPS commands

The private `/pps` group was added with access restricted through `impact.allowed_user_ids`.

| Command | Purpose |
| --- | --- |
| `/pps dashboard` | Queue totals, completed/pending scores, active cycle, outcomes, worker state, and rollout diagnostics |
| `/pps queue` | Paginated private ranking of queued and in-cycle candidates |
| `/pps level` | One level's score components, source request, CP, cycle history, attempts, private notes, and audit events |
| `/pps cycle` | Start, inspect, complete, or cancel an outreach cycle |
| `/pps outreach` | Record an attempt or a confirmed moderator submission |
| `/pps refresh` | Force metadata refresh or apply a reasoned CP override |
| `/pps stats` | Show prospective recommendation, submission, rating, and outcome evidence totals |

Manual CP overrides require a reason and append audit evidence instead of silently replacing historical values.

### 11. Recovery and compatibility

`/requests repair` was expanded to restore the view saved on each request:

- Legacy rows regain the original buttons.
- PPS rows regain the recommendation menu.
- Reviewed controls remain disabled.
- Missing queue entries can be recreated idempotently only for valid reviewed PPS recommendations.
- Legacy rows can never enter PPS through repair.

The outbox reconstructs controls from the persisted workflow version. Startup registers both view families before normal operation. Schema upgrades are additive, and a database newer than the program still fails closed instead of being downgraded.

### 12. Configuration and validation

The `priority_system` configuration section was added with bounded values for:

- New-wave workflow version.
- Model version.
- Prestige base and the five tier coordinates.
- Creator-opportunity formula parameters.
- Waiting multiplier, exponent, and score cap.
- CP and level refresh intervals.
- Outcome-window duration.
- Worker interval, batch size, and failed-lookup retry delay.

Configuration parsing rejects unknown model versions, non-finite numbers, out-of-range values, and prestige coordinates that do not increase from Rate through Mythic.

### 13. Documentation and tests

This update added the private PPS runbook, updated the main README, extended the manual source and generated manual artifacts, and expanded the manual production checklist.

Regression coverage was added for formulas, unknown CP, tier validation, legacy rollout, persistent views, concurrent review safety, queue insertion, cycles, waiting increments, outcome windows, repairs, outbox reconstruction, commands, configuration, Turso behavior, and restart durability.

### Files changed in Update 1

```text
README.md
TEST_CHECKLIST.md
cogs/Commands.py
cogs/Operations.py
cogs/PrioritySystem.py
cogs/RequestLevels.py
config.json
docs/Avenue_Guard_Manual.docx
docs/Avenue_Guard_Manual.md
docs/Avenue_Guard_Manual.pdf
docs/HISTORICAL_REQUEST_AUDIT.md
docs/PRIORITY_POINT_SYSTEM.md
main.py
release.json
scripts/build_manual.py
services/priority_system.py
tests/test_command_schema.py
tests/test_database.py
tests/test_priority_system.py
tests/test_runtime_resilience.py
tests/test_slash_commands.py
tests/test_turso_contention.py
utils/config_schema.py
utils/db.py
utils/outbox.py
utils/priority_system.py
utils/priority_system_schema.py
utils/views.py
```

---

## Update 2: Public Recommendation Pages, Cleaner Results, and Initial Validation Repair

### 1. Recommendation result message

The successful PPS result was rewritten around the human recommendation rather than a generic `Sent` status.

The result now provides:

- A title in the form `Your level was recommended for [type]`.
- The requester mention.
- The submitted level name.
- Wording that the level was deemed `[type]-worthy`.
- A permanent public link for queue order and public status.
- Reviewer attribution using the reviewer mention.

The public wording does not claim that moderator outreach has already happened.

### 2. Reviewer-card wording and links

The verbose sentence `Queued for human outreach; no moderator contact is implied yet` was shortened to `Queued for outreach`.

After a PPS recommendation, the staff-facing request card also receives a `View public level page` link. Repaired reviewed cards reconstruct the same concise status and link.

When a requester presses the request button after their request has already been reviewed, the response now says `Your request has already been reviewed` instead of referring ambiguously to `that request`.

### 3. Permanent public level URLs

Every recommended level receives a stable URL shaped as:

```text
https://gdavenue.netlify.app/level/[level-id]
```

The base URL is configurable through `priority_system.public_level_base_url`.

The page contract exposes the level name, level ID, recommendation time, recommendation type, public queue status, queue position when active, active queue total, and last update time.

The page design pairs a dark GD Avenue presentation with the matching recommendation artwork. It uses the LevelThumbs service for the level thumbnail and falls back to a solid black background when no thumbnail can be loaded. The intended desktop composition divides the hero diagonally between the level image and the black information surface, while smaller layouts remain readable without requiring that split.

### 4. Privacy-filtered bot API

The keepalive service gained public endpoints for one level:

```text
/api/level/[level-id]
/api/levels/[level-id]
```

The API payload is explicitly allowlisted. It does not expose requester IDs, reviewer IDs, review text, private outreach notes, internal priority points, or provider diagnostics.

Invalid or unknown IDs receive a JSON `level_not_found` response with HTTP 404. Successful payloads use a short public cache lifetime. Both the aiohttp keepalive server and the fallback standard-library HTTP server implement the same endpoint behavior.

### 5. Public cache synchronization

`PrioritySystemCog` builds the public cache from durable Turso rows rather than Discord messages. It calculates active queue positions using the same complete-score-first ranking as the private queue.

The cache refreshes:

- During PPS startup.
- After cycle changes.
- After outreach changes.
- After manual refreshes or overrides.
- After maintenance changes queue/rating state.
- Immediately after a request is recommended.

Only the newest queue record for a level is exported publicly.

### 6. Removed reminder-message clutter

The separate automatic queue-age/expired-validation reminder message was removed. It had been adding noise and could interfere with normal request-card behavior.

The useful parts were retained:

- Request age remains visible inside the request card.
- Existing aging thresholds remain available.
- Staff can still press `Recheck` for current validation.
- Normal request-card edits continue to update the existing message rather than sending another reminder message.

Obsolete startup-write supervision and tests that existed only for the removed automatic refresh path were cleaned up.

### 7. First hosted validation resilience pass

GDRate+ was added as a hosted exact-level fallback. The parser verifies that the returned ID exactly matches the requested ID and normalizes current name, creator, difficulty, length, stars, platformer/demon state, and Rate/Feature/Epic/Legendary/Mythic flags when supplied.

The direct Boomlings request was updated to use the current GD form fields and headers expected by that endpoint.

GDBrowser was disabled by default because hosted Cloudflare access was unreliable and the service is not intended as a critical production dependency.

Provider error handling was strengthened so that:

- HTTP 401/403 becomes access denied.
- HTTP 429 becomes rate limited with bounded retry timing.
- HTTP 5xx becomes upstream unavailable.
- HTML challenge pages cannot look like missing levels.
- Oversized bodies are rejected.
- A mismatched returned ID is rejected.
- A provider error cannot be interpreted as `exists = false`.

### 8. Tri-state rating information

Rating state now distinguishes:

```text
Rated
Unrated
Unknown
```

When all external providers fail, the request card no longer claims the level is unrated or has zero stars. It shows unknown information and keeps the request reviewable.

### 9. Release metadata and tests

`release.json` moved to `3.25.0`, titled **Public Level Recommendations**.

Tests were added or updated for public payload privacy, endpoint aliases, 404 responses, queue position, provider parsing, access denial, rate limiting, bounded bodies, exact IDs, unknown rating state, accepted-message wording, reviewer links, restored controls, startup behavior, and public-cache refreshes.

### Files changed primarily by Update 2

```text
cogs/Commands.py
cogs/Operations.py
cogs/PrioritySystem.py
cogs/RequestLevels.py
config.json
main.py
release.json
services/historical_audit.py
services/priority_system.py
tests/test_config_and_responses.py
tests/test_gd_validation.py
tests/test_priority_system.py
tests/test_release_updates.py
tests/test_request_helpers.py
tests/test_startup_writes.py
tests/test_turso_contention.py
utils/gd_validation.py
utils/keepalive.py
utils/views.py
```

The separate website project contains the presentation layer for `/level/[level-id]`; the bot repository contains the durable data source, privacy boundary, route contract, and Discord links that feed it.

---

## Update 3: Exact Send-Type Artwork and Discord Emoji Integration

### 1. Final asset strategy

The generated redraw approach was discarded. The committed assets are direct transparent crops from the supplied five-face master image, preserving the original art rather than restyling it.

The final files are:

| Type | File | Pixel dimensions | Discord application emoji name |
| --- | --- | ---: | --- |
| Rate | `assets/discord-emojis/pps_rate.png` | 135 x 135 | `pps_rate` |
| Feature | `assets/discord-emojis/pps_feature.png` | 170 x 179 | `pps_feature` |
| Epic | `assets/discord-emojis/pps_epic.png` | 213 x 213 | `pps_epic` |
| Legendary | `assets/discord-emojis/pps_legendary.png` | 212 x 217 | `pps_legendary` |
| Mythic | `assets/discord-emojis/pps_mythic.png` | 243 x 218 | `pps_mythic` |

All five files use 8-bit RGBA PNG data with transparent backgrounds.

### 2. Final Discord emoji IDs

The uploaded application emojis were connected through `config.json`:

| Type | Emoji ID |
| --- | --- |
| Rate | `1550265958688489472` |
| Feature | `1550265953588224102` |
| Epic | `1550265952333996052` |
| Legendary | `1550265955026993283` |
| Mythic | `1550265957463621773` |

Names and IDs are configurable independently for future replacements.

### 3. Runtime emoji loader

`configure_pps_send_type_emojis()` was added to normalize the configuration into `discord.PartialEmoji` objects.

The loader:

- Accepts the documented object form with `name` and `id`.
- Accepts a simple ID value as a compatibility form.
- Requires an ASCII decimal positive ID.
- Ignores malformed or empty values without breaking startup.
- Clears old in-memory values before reloading.

The loader runs during bot startup and when configuration is reloaded.

### 4. Recommendation menu integration

Each of the five select options now receives its matching application emoji. The same emoji mapping is used when a reviewed request is reconstructed with disabled controls, so persistent views do not lose their icons after a restart or repair.

If an emoji has not been uploaded or its ID is blank, the menu remains functional and simply displays that option without an icon.

### 5. Asset documentation and tests

`assets/discord-emojis/README.md` documents filenames, required application emoji names, upload steps, and the relevant configuration path.

Tests verify every configured ID and name, normal menu rendering, disabled reviewed-menu rendering, fallback behavior for missing IDs, startup registration, and configuration defaults.

### Files changed primarily by Update 3

```text
assets/discord-emojis/README.md
assets/discord-emojis/pps_rate.png
assets/discord-emojis/pps_feature.png
assets/discord-emojis/pps_epic.png
assets/discord-emojis/pps_legendary.png
assets/discord-emojis/pps_mythic.png
config.json
main.py
tests/test_config_and_responses.py
tests/test_priority_system.py
utils/views.py
```

---

## Update 4: Render Validation Recovery with GDHistory

### 1. Production failure that prompted the update

The bot could still display:

```text
Level validation could not run right now. Please check this level manually.
Sources: boomlings: unavailable (upstream access denied) |
gdrateplus: unavailable (upstream unavailable).
```

This was not evidence that the submitted level was invalid. Render was receiving an HTTP denial from Boomlings while GDRate+ was simultaneously returning a server-side outage.

A GDHistory parser and fetcher had been started, but the provider had not been registered with the live request cog, configuration, dashboard checks, historical-audit service, or PPS metadata worker. Therefore it could not help production requests.

### 2. GDHistory provider completed and enabled

The provider uses the GDHistory brief endpoint:

```text
https://history.geometrydash.eu/api/v1/level/[level-id]/brief/
```

It validates:

- Response shape.
- Exact returned level ID.
- Public status.
- Deleted status.
- Presence of a level name.

When confirmed, it normalizes:

- Level ID and name.
- Uploader name, GD user ID, and GD account ID.
- Stars.
- Difficulty, including individual demon tiers.
- Length and platformer state.
- Feature score.
- Epic tier.
- Derived Featured, Epic, Legendary, and Mythic flags.
- Rated, demon, and platformer state.

### 3. Positive-evidence-only safety rule

GDHistory is a preservation index, so it is deliberately asymmetric:

```text
matching public, non-deleted record -> confirms that the level exists
404 or success=false             -> unknown / not indexed
deleted, stale, or mismatched row -> unknown / not current
```

A GDHistory miss never becomes `exists = false`. It cannot auto-reject a request.

This makes the fallback useful when it has evidence while preventing an indexing delay from being mistaken for a nonexistent level.

### 4. Full integration

`gdhistory` was added to the common provider order and connected to:

- Live request submission validation.
- Manual reviewer rechecks.
- Provider circuits, retries, locks, pacing, and telemetry.
- Provider-health output in the bot dashboard.
- `config_check` provider and interval validation.
- Historical audit level refreshes.
- PPS level snapshot refreshes.

The default minimum interval is 0.25 seconds. It uses the existing shared aiohttp session, certificate bundle, timeout, bounded body reader, retry policy, and circuit-breaker infrastructure.

### 5. Configuration defaults

`config.json` now enables:

```json
{
  "gdhistory": true,
  "gdrateplus": true,
  "boomlings": true,
  "gdbrowser": false
}
```

The inline configuration documentation explains that GDHistory is positive-evidence-only and cannot auto-reject an unindexed level.

### 6. Cache-generation protection

Turso may retain a valid validation-cache row produced before a new provider was enabled. Previously, that old failure could continue to be returned until its normal expiration.

Cache reads now compare the providers stored in the cached result with the currently enabled provider set. If any enabled provider is absent, the cache is treated as stale and the lookup runs immediately.

This gives configuration/provider changes an implicit cache generation without adding another migration or rewriting historical rows.

### 7. Better diagnostics

Provider summaries now preserve the classified failure and HTTP status when available:

```text
boomlings: unavailable (access denied; HTTP 403)
gdrateplus: unavailable (upstream unavailable; HTTP 503)
gdhistory: found
```

This distinguishes DNS/network failures, rate limits, access denials, server outages, malformed responses, non-indexed levels, and open circuits more clearly.

### 8. Exact failure-shape verification

The implementation was tested against the same production shape:

```text
Boomlings -> HTTP 403 access denied
GDRate+   -> HTTP 503 upstream unavailable
GDHistory -> matching public level
```

The combined result correctly reported the level as existing and populated its metadata. The failed providers remained visible as diagnostics but no longer forced the generic validation-unavailable warning.

### 9. Tests and quality checks

New tests cover:

- Successful GDHistory metadata normalization.
- All five current rating tiers.
- Exact level-ID matching.
- Public/deleted-state checks.
- Non-indexed records remaining unknown.
- HTTP 404 remaining unknown.
- Correct brief-endpoint URL construction.
- HTTP status inclusion in source summaries.
- Cache invalidation when an enabled provider is missing.
- Cache reuse when the full enabled provider set is present.
- Configuration defaults and intervals.
- Integration with request helpers, historical audits, and PPS snapshots.

After this update:

```text
302 tests passed
Python compilation passed
Critical Ruff checks passed
config.json parsing passed
Dependency validation passed
git diff whitespace validation passed
Live GDHistory lookup passed
```

### Files changed in Update 4

```text
cogs/Commands.py
cogs/RequestLevels.py
config.json
services/historical_audit.py
services/priority_system.py
tests/test_config_and_responses.py
tests/test_gd_validation.py
tests/test_request_helpers.py
utils/gd_validation.py
```

---

## Combined Result After the Four Updates

The request system now separates four previously conflated concepts:

```text
request review
    -> human recommendation type
        -> deterministic outreach priority
            -> human outreach attempt
                -> observed rating outcome
```

The public-facing result explains the recommendation without claiming moderator contact. The private PPS preserves the evidence and ordering needed for outreach. The website receives a privacy-filtered durable view. The send-type menu uses the supplied visual language. External GD validation can continue when either Boomlings or GDRate+ is unavailable, while unknown information remains unknown instead of becoming a false rejection or a fake zero.

The following boundaries remain intentional:

- Legacy waves are not silently upgraded.
- Weekly reviews are not inserted into PPS.
- Historical requests are not retroactively scored into the production queue.
- PPS is deterministic and does not contain the future Bayesian model.
- The bot does not contact Geometry Dash moderators automatically.
- A recommendation does not mean that outreach has happened.
- A provider failure does not mean a level is missing.
- Missing Creator Points do not mean zero Creator Points.
- Public level data excludes requester, reviewer, review-text, exact-score, and private-outreach information.

## Version and Commit Reference

| Boundary | Version | Repository commit |
| --- | --- | --- |
| Before this document's scope | `3.23.0` | `d015a72` |
| Update 1: PPS v1 | `3.24.0` | `de00c17` |
| Updates 2 and 3: public recommendation and visuals | `3.25.0` | `3085728` |
| Update 4: GDHistory validation hardening | `3.25.0` reliability patch | `b5b2295` |

The two middle topics share one repository commit because the public-page, message, validation, and emoji work was tested and committed together. They are documented as separate updates here because they were separate requested development passes with different purposes.
