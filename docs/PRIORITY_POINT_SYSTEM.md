# Avenue Guard Priority Point System v1

This is the private technical and operating guide for the production Priority Point System and its separate evidence models. PPS is a deterministic queue for human outreach. Bayesian models observe what happens after staff act; they never alter PPS, contact moderators, or turn a recommendation into a claimed submission.

## 1. The Mental Model

```text
new request wave
      |
      v
submission copies the wave's review_system_version
      |
      +-- legacy --> Send / Reject / Other / Recheck (unchanged)
      |
      `-- pps_v1 --> Send type / Reject / Other / Recheck
                          |
                          v
                  reviewed row + durable outbox
                          |
                          v
                   outreach queue entry
                          |
                 CP and level refresh worker
                          |
                          v
             owner-managed outreach cycle
                          |
               confirmed submitted_to_mod
                          |
                          v
               30-day rating observation
```

Discord is the interface, but Turso/SQLite is authoritative. The visible request card can be repaired from the saved version and result. Queue and cycle state survives restarts because it is never held only in memory.

## 2. Rollout Boundary

Schema 14 preserves the original version boundary introduced with schema 8: `review_system_version` exists on both `level_request_state` and `level_request_submissions`, and migrated rows default to `legacy`.

This is the safety rule:

```text
database migration             existing wave = legacy
restart                        saved version stays legacy
new request in existing wave   copies legacy
/requests repair               reads row version, restores legacy view
next new wave ID               explicitly saves pps_v1
new submission in that wave    copies pps_v1
```

The bot never decides from a global “PPS is enabled” flag when rendering an existing request. `RequestLevelsCog._review_system_version()` reads the persisted row. The global `priority_system.new_wave_version` is consulted only when `_open_requests_now()` creates the next wave ID. Scheduled openings use the same method and therefore create PPS waves after deployment.

Weekly requests live in `weekly_request_reviews`, not live request waves. Their version is deliberately forced to `legacy`.

## 3. Review UI And Transaction

Legacy custom IDs remain registered. PPS adds one stable component ID:

| Control | Persistent custom ID |
|---|---|
| Legacy Send | `level_request_send` |
| PPS send-type select | `level_request_pps_send_type_v1` |
| Reject / Other / Recheck | Existing legacy IDs |

The select contains exactly `rate`, `feature`, `epic`, `legendary`, and `mythic`. Selecting a tier opens the optional review modal immediately. This acknowledges Discord before any database or GD provider work.

Modal submission then performs the authoritative checks:

1. Resolve reviewer permissions again.
2. Load the row by guild and request message ID.
3. Require `status = pending`.
4. Require a valid tier for a PPS `sent` result.
5. Atomically update the row to reviewed.
6. Insert the one queue row only if that exact reviewed row, reviewer, timestamp, tier, and version exist.
7. Insert result-channel, DM, card-edit, and workflow-event intents only for that exact winning review.
8. Read the row back before reporting success.

Unique database keys and conditional `SELECT ... WHERE EXISTS(...)` guards make retries idempotent. A concurrent losing reviewer cannot enqueue a second result or PPS queue record.

## 4. Score Definition

PPS v1 calculates:

```text
P = F + G + H
```

Each component is persisted separately. The total is null until current uploader CP is known.

### 4.1 Prestige Component F

| Recommendation | T | F = 1.8^T - 1 |
|---|---:|---:|
| Rate | 0.00 | 0.00 |
| Feature | 1.25 | about 1.09 |
| Epic | 2.50 | about 3.35 |
| Legendary | 3.75 | about 8.06 |
| Mythic | 5.00 | about 17.90 |

The reviewer recommendation fixes F. Background work never changes the tier.

### 4.2 Creator Opportunity Component G

```text
G = max(0, ln(0.25 / (0.06*C + 0.01)))
```

`C` is the current Creator Point count of the GD account that uploaded the level. It is not the Discord requester's CP and not an average across collaborators.

| Current CP | G |
|---:|---:|
| 0 | about 3.22 |
| 1 | about 1.27 |
| 2 | about 0.65 |
| 3 | about 0.27 |
| 4+ | 0.00 |
| unknown | null |

Unknown and zero are intentionally different. An HTTP failure, malformed profile, provider circuit, or uploader-identity conflict stores a null CP and a null total. It never earns the zero-CP bonus.

### 4.3 Waiting Component H

```text
H = 1.5 * min(W, 4)^1.5
```

`W` is the uncapped count of completed successful outreach cycles in which the level was eligible at cycle start, did not reach a moderator, and remained eligible when the cycle completed.

| W | H |
|---:|---:|
| 0 | 0.00 |
| 1 | 1.50 |
| 2 | about 4.24 |
| 3 | about 7.79 |
| 4+ | 12.00 |

The raw waiting count keeps increasing for tie-breaking and evidence. Only the mathematical contribution caps at four.

## 5. Creator Points Resolution

`CreatorPointsResolver` is the only service allowed to supply current CP to PPS. A recommendation transaction creates the queue row, marks priority pending, and inserts a high-priority durable resolution job. The Discord interaction then completes normally while the wakeable worker resolves `level ID -> actual uploader -> current CP` with bounded parallel calls.

The resolver reuses the RequestLevels HTTP session, host locks, pacing, retry-after handling, provider telemetry, and circuit breakers. It gathers independent evidence from GDBrowser HTML, direct Boomlings, GDHistory, and GDRate+. GDBrowser's server-rendered level page is parsed semantically through `#authorLink`; that exact profile route is then parsed through `#cp` and the labelled Account ID / Player ID block. The GDBrowser JSON profile endpoint is a conservative fallback only.

The direct Boomlings profile request uses `getGJUserInfo20.php`. `utils/gd_profile.py` verifies these response keys:

| Boomlings key | Meaning used by Avenue Guard |
|---|---|
| `2` | GD user ID |
| `8` | Creator Points |
| `16` | GD account ID |

The returned account ID must match the requested account, the user ID must be valid, and key 8 must exist as an integer. GDHistory contributes identity unless its real contract ever exposes an explicit CP field. GDRate+ contributes only explicit fields in its existing level response. Usernames are NFKC-normalized and case-folded for matching only; display capitalization is retained and fuzzy matching is forbidden.

Source precedence is verified direct Boomlings, agreement by at least two independent providers, verified GDBrowser HTML, then another verified trusted single source. Conflicting live values are never averaged and remain unranked while verification retries. Explicit zero is valid; missing or malformed CP remains null.

The first accepted result fills `creator_points_at_recommendation` without overwriting it later. Subsequent checks update `current_creator_points`. Provider observations are structured and fingerprinted without retaining full HTML. Accepted snapshots preserve source, confidence, timestamp, and response fingerprint. Level-to-uploader identity is cached separately for 30 days by default because it is stable; current CP is cached for six hours because it changes. Retries occur after 30 seconds, 2 minutes, 10 minutes, 1 hour, 6 hours, 24 hours, then daily. Unresolved entries create one deduplicated attention item after 15 minutes and escalate after 24 hours.

## 6. Queue State Machine

```text
queued ---- cycle starts ----> in_cycle
  |                              |
  | level becomes rated          | confirmed moderator submission
  v                              v
rated                      awaiting_outcome ---- rating observed ----> rated
  |
  ` evidence retained

queued/in_cycle ---- level unavailable ----> invalid
queued/in_cycle ---- owner withdrawal ------> withdrawn (reserved state)
```

`submitted_to_mod` is represented as a confirmed attempt plus `submitted_to_mod_ts`; the durable queue moves immediately to `awaiting_outcome`. It is no longer part of the ordinary ranked queue.

Incomplete scores do not belong to the ranked queue, cannot enter an outreach cycle, and cannot be claimed through the normal Reviewer workflow. They appear in a separate pending-data list with null score and null rank. Complete ties use:

1. Higher total P.
2. Higher uncapped W.
3. Older `queued_ts`.
4. Stable queue ID.

## 7. Outreach Cycles

An outreach cycle is separate from a request wave. It represents one real staff round of attempting to place queue candidates in front of GD moderators.

At start, the bot creates a cycle and snapshots every currently `queued` candidate into `level_outreach_cycle_entries`. The snapshot includes P, completeness, F/G/H, CP, W, and model version. Then those records move to `in_cycle`. A recommendation added after start remains `queued` and is not eligible to age from that cycle.

Attempts support:

- `planned`
- `attempted`
- `submitted_to_mod`
- `failed`

Routes support `direct`, `network`, `stream`, `event`, and `other`. Target labels and notes are private and visible only through owner commands.

A cycle can complete successfully only if at least one entry has a confirmed `submitted_to_mod` attempt. During the one guarded transaction:

- submitted entries keep W unchanged and stay in outcome tracking;
- eligible start entries still in `in_cycle` gain exactly one W and return to `queued`;
- late entries are untouched;
- rated, invalid, or otherwise ineligible entries are untouched;
- the cycle becomes completed and cannot age anything again.

A zero-submission cycle is cancelled/closed as unsuccessful. It preserves attempts but increases no waiting score.

## 8. Background Maintenance

`PrioritySystemCog` owns `avenue-guard:priority-maintenance`. `OperationsCog` supervises it as `priority.maintenance` and can restart it if it dies.

Each pass uses `maintenance_batch_size`; Creator Points jobs use bounded concurrency while ordinary queue maintenance remains bounded:

- refresh stale level metadata;
- resolve uploader identity;
- refresh stale current CP;
- recompute G and P after a successful CP lookup;
- mark active levels rated or invalid from reliable evidence;
- refresh records whose 30-day outcome boundary is due;
- complete the fixed outcome as rated/not rated only after a successful provider observation.

Successful snapshots use the normal CP and level cache intervals. Failed checks receive the shorter failure retry interval. One failed provider request is logged and deferred without terminating the loop or blocking a review.

## 9. Database Tables

Schema 15 is additive.

| Table | Durable responsibility |
|---|---|
| `level_outreach_queue` | Current score, source request, state, CP, waiting and outcome fields |
| `level_outreach_cycles` | Cycle lifecycle, actor, timestamps, success and private notes |
| `level_outreach_cycle_entries` | Immutable start-of-cycle score/evidence snapshots and aging guards |
| `level_outreach_attempts` | Planned, attempted, submitted and failed outreach evidence |
| `level_outreach_cp_snapshots` | Append-only CP lookup/override history |
| `level_outreach_level_snapshots` | Append-only current level/rating lookup history |
| `creator_points_resolution_jobs` | Durable prioritized retries, attention thresholds and resolution state |
| `creator_level_identities` | Reusable long-lived level-to-uploader identity cache |
| `creator_points_current` | Reusable short-lived accepted current CP by canonical creator |
| `creator_points_provider_observations` | Structured provider evidence, errors, latency and response fingerprints |
| `staff_outreach_episodes` | Reconstructable per-level episode, first confirmed submission, fixed outcome and eventual outcome |
| `level_outreach_targets` | Private normalized target identity; never projected publicly |
| `level_outreach_opportunities` | One episode + level + target Bernoulli opportunity; follow-ups remain one trial |
| `level_network_eras` | Explicit access environments and exact carryover prior |
| `bayes_model_versions` | Immutable version/config registrations |
| `bayes_model_snapshots` | Immutable posterior, readiness, calibration and config-hash snapshots |
| `bayes_predictions` | Immutable access, rating and public combined-path prediction records |
| `bayes_capacity_forecasts` | Reproducible cycle/sandbox posterior predictive forecasts and actuals |
| `bayes_model_exclusions` | Audited, reversible evidence exclusions with explicit reason |
| `level_notification_subscriptions` | Per-level durable DM preferences |
| `level_notification_events` / `level_notification_deliveries` | Idempotent event and outbox delivery audit |

Important transitions also write `workflow_events` with correlation IDs. Discord result delivery continues through `discord_outbox`.

No historical request row is rewritten into PPS. No legacy request is backfilled into the queue. The model worker only projects prospective PPS attempts with an existing episode and sufficient target semantics. Ambiguous records receive an explicit exclusion instead of invented evidence.

## 10. Owner Commands

All `/pps` commands fail closed to `impact.allowed_user_ids`. Reviewer roles can review cards but cannot manage cycles, overrides, private targets, or queue evidence.

| Command | Purpose |
|---|---|
| `/pps dashboard` | Queue totals, scored/CP-pending split, active cycle, outcomes, model and rollout diagnostic |
| `/pps queue` | Private ranked pages of active `queued` and `in_cycle` entries |
| `/pps level` | Source, score, CP, cycles, attempts, private notes and audit events |
| `/pps cycle` | Start, view, complete or cancel a cycle |
| `/pps outreach` | Record an attempt or confirmed moderator submission |
| `/pps refresh` | Force external metadata refresh or make a reasoned CP override |
| `/pps stats` | Prospective recommendation, submission, rating and fixed-window evidence totals |

Manual CP overrides require a reason and append both a snapshot and workflow event. They do not overwrite the first recommendation CP snapshot.

## 11. Repair And Recovery

`/requests repair` reads each request row's saved version:

- `legacy` restores the original four-button view;
- `pps_v1` restores the send-type select plus three buttons;
- reviewed cards are rebuilt disabled;
- a reviewed PPS `sent` row with a valid tier can idempotently recreate its missing queue row;
- a legacy request is never eligible for that queue repair.

The outbox also reconstructs the disabled view from the saved version. Restart registers both persistent component sets before database startup.

## 12. Configuration

`priority_system` stores bounded parameters, not executable formulas. `utils.priority_system.priority_settings()` rejects an unknown model version, invalid tier ordering, non-finite values, and unsafe intervals or batch sizes.

```json
{
  "new_wave_version": "pps_v1",
  "model_version": "pps_v1",
  "prestige_base": 1.8,
  "prestige_x": {
    "rate": 0,
    "feature": 1.25,
    "epic": 2.5,
    "legendary": 3.75,
    "mythic": 5
  },
  "creator_opportunity": {
    "numerator": 0.25,
    "slope": 0.06,
    "offset": 0.01,
    "zero_from_cp": 4
  },
  "waiting": {
    "multiplier": 1.5,
    "exponent": 1.5,
    "score_cap_cycles": 4
  }
}
```

Changing parameters does not silently rewrite cycle snapshots. A future PPS v2 should use a new model version and an explicit recomputation/migration decision.

## 13. Production Smoke Test

Use staging or a controlled request wave where possible.

1. Back up the database and deploy schema 15.
2. Run `/pps dashboard`; confirm the deployment-time current wave says `legacy` and the next new wave says `pps_v1`.
3. Run `/requests repair`; confirm existing cards still have the green generic Send button.
4. Submit another request to that same existing wave; confirm it is still legacy.
5. Open the next new wave; confirm its card has the five-option menu and no generic Send button.
6. Recommend a test request; confirm the user-facing result says recommended/queued rather than sent to a moderator.
7. Inspect `/pps queue` and `/pps level`; CP may begin unknown, then become known after refresh.
8. Confirm HTTP/provider failure leaves CP and P unknown, not CP zero.
9. Start a cycle and record one failed attempt; confirm the entry remains in-cycle.
10. Record one confirmed submission, then complete the cycle. Confirm that submitted entry gains no W and other start candidates gain exactly one.
11. Run complete again; confirm no second increment.
12. Start a no-submission cycle; confirm completion is refused and cancel adds no W.
13. Restart the bot; confirm queue, active cycle, attempts and both component types still work.
14. Confirm weekly review cards still use the old Send workflow.

The optional read-only provider smoke test does not open or mutate the Avenue Guard database:

```bash
.venv/bin/python scripts/smoke_creator_points.py --live \
  --level 145233080 --level 149457878 --timeout 8
```

Use `--api-fallback` only when specifically testing the cautious GDBrowser JSON fallback. A third-party outage is reported in the JSON result and does not make this command fail CI.

## 14. Public Presentation Boundary

The public level routes are `/api/level/[level-id]` and `/api/levels/[level-id]`. Both return the same schema-2 allowlisted payload. Public data can include the level ID/name, reliably resolved uploader name, recommendation type, recommendation time, sanitized queue/outreach/outcome states, public priority band, confirmed moderator-submission time, observed-rating time and last update time.

Exact queue rank is converted server-side with `public_priority_band(position, total)`. Position one is always `top_priority`; all other active ranks use `position / total`: up to 10% is top, over 10% through 30% is high, over 30% through 70% is standard, and over 70% is lower. Inactive rows have a null band. Exact position, active total, P/F/G/H, CP, requester/reviewer identity, review text, provider diagnostics, routes, targets and private notes never cross the public cache boundary.

Public lifecycle values are derived independently. `Rated` is an outcome, never an outreach status. Missing submission/rating evidence remains unknown, and the website only marks timeline stages supported by persisted timestamps or authoritative queue state.

## 15. Outreach Episodes And Opportunities

Every active/requeued lifetime is a separate `staff_outreach_episodes` row. The first confirmed submission opens exactly one 30-day episode outcome. A later manual requeue preserves the old episode and creates the next episode with refreshed CP, recalculated PPS and `W = 0`.

An opportunity is unique to episode + level + normalized private target. Planned, attempted, failed, submitted and follow-up events roll into that row. Planned-only opportunities are not evidence; an attempted opportunity explicitly closed without submission is a failure; confirmed submission is a success. Same-target follow-ups never increase the Bernoulli trial count. Different targets remain different opportunities.

## 16. Bayesian Evidence Models

The shadow family is versioned independently from `pps_v1`:

- `access_model_v1`: `P(confirmed submission | resolved real opportunity)` in the current network era;
- `rating_model_v1`: `P(rated within 30 days | episode had a confirmed submission)`;
- `capacity_model_v1`: posterior predictive confirmed-submission totals for planned opportunities.

Global models begin at `Beta(1,1)`. Route and recommendation-tier subgroups shrink toward the applicable global posterior mean with configurable effective sample size. A new network era uses the configured reset policy or the previous posterior mean compressed to a weak prior: `alpha = 1 + m*n`, `beta = 1 + (1-m)*n`.

Pre-rated episodes, malformed prospective records and manual exclusions never enter the relevant likelihood. The original recommendation and eventual official tier remain separate fields.

## 17. Readiness, Calibration And Publication

Statuses are `collecting`, `provisional`, `active`, `degraded` and `paused`. Central configuration controls minimum observations/successes/failures, maximum 90% credible-interval width, freshness, subgroup readiness and calibration thresholds. Evidence labels (`limited`, `moderate`, `strong`) combine count, interval width and freshness.

Each posterior snapshot stores alpha, beta, 50/80/90/95% credible intervals, evidence counts, data cutoff, exact prior, readiness reason, calibration metrics and config hash. Resolved prediction snapshots retain their original probability and gain outcome/Brier fields. Reliability bins and ECE are evaluated only after the configured minimum resolved count.

Public reads use persisted active snapshots only. Access and rating estimates may activate independently. The combined Avenue-path estimate appears only when both are active and is calculated from deterministic Monte Carlo draws of both posteriors, never by multiplying interval endpoints. Degraded, paused or new-era collecting models disappear automatically.

## 18. Capacity Forecasts

Capacity simulations draw route/global access probabilities and then candidate outcomes. Follow-ups are not new trials. Forecasts persist the selected snapshot IDs, seed, draw count, expected submissions and:

```text
Kq = max { k : P(S >= k) >= q }
```

`K80`, `K90` and `K95` are conservative internal commitments, not public promises. The Statistics Lab simulator is non-mutating; cycle-start forecasts are persisted and resolved against actual confirmed submissions.

## 19. Network Eras

Exactly one era is active. Owner/Dev starts a new era with mandatory private reason and optional sanitized public reason. The transaction closes the old era, records the carryover prior and emits an audit event. Access and capacity return to collecting/provisional until current-era evidence passes readiness.

## 20. Notifications

Requester subscriptions are created after a PPS recommendation unless existing preferences opt out. `/requests follow`, `/requests unfollow`, `/requests notifications` and notification preferences support other members and optional public-band changes. Default major/final DMs cover confirmed moderator submission, observed official rating, withdrawal, invalidation, explicit requeue and an unrated 30-day completion. Routine attempts, CP refreshes, rank movement and provider refreshes never DM.

Every event and recipient uses a stable idempotency key, the existing durable Discord outbox and a persisted delivery row. Public messages contain no target, route, reviewer or private note. Current-rated wording reports observation and never implies Avenue causation.

## 21. Statistics Lab And Public Boundary

Staff Portal > Admin > PPS > Statistics Lab displays era history, posterior summaries, credible intervals, shadow predictions, readiness reasons, calibration bins, exclusions, forecast history, notification health and the simulation sandbox according to capability. Dev can manage exclusions, pauses and eras; less privileged roles receive progressively sanitized summaries.

Public payloads expose only rounded active estimates, whole-percent 90% likely ranges, evidence label, model version and timestamps. They never expose raw alpha/beta, counts, routes, targets, actors or exclusions. `/methodology/queue/` is the permanent public source of truth and is linked as **How we order the queue**.

## 22. Explicit Non-Features

The system does not implement AI ranking, automatic moderator contact, external-moderator DMs, public exact PPS/rank, causal claims about ratings, or guessed legacy evidence. Humans still review levels and perform outreach. The models quantify uncertainty after human action; they do not make live request decisions.
