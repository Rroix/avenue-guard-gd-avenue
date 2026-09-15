# Turso Contention And Request Recovery

Private follow-up for Rodrigo. Release proposal: **3.22.2**. Database schema: **6**.

This supplements the [initial runtime diagnosis](INCIDENT_DIAGNOSIS_2026-09-15.md). Process isolation fixed the native driver's ability to hold the Discord interpreter lock. The newly reported errors exposed a different problem: too much work still competed for the one primary connection. Fixing the interpreter boundary did not, by itself, fix queue contention.

## What The Errors Mean

| Reported symptom | Meaning and source-level diagnosis |
| --- | --- |
| Historical health sampler: `DatabaseBusyError` | The sampler could not acquire the primary queue before its deadline. It did not start that operation. Its probe and separate history writes competed with real workflows. |
| Active ticket cache: `DatabaseBusyError` | Cache reads still queued behind remote writes. A failed cold load could be retried by successive server messages, increasing contention. |
| Pending request message missing during refresh | The refresher directly fetched the saved ID without the shared legacy-ID resolver. A genuinely deleted card required manual repair. A rounded historical ID could therefore also look deleted. |
| Many features fail together | Shared connection contention can affect unrelated callers. This does not prove every error has the same cause, or that Turso itself was down throughout the incident. |

The supplied messages do not identify the exact operation that held the production connection or establish the duration of every outage. Inspection did establish avoidable contention and unsafe recovery paths. Busy errors now include the operation, current holder, and waiting count for future incident diagnosis; they still mean the caller's operation was not started.

## The Revised Storage Boundary

```text
                         Discord process
                               |
                  +------------+------------+
                  |                         |
            snapshot reads             writes / recovery
                  |                         |
        four read-only slots          one ownership lock
                  |                         |
         standard SQLite reader       isolated libSQL worker
                  |                         |
                  +--- local replica -------+-- Turso primary
```

Normal remote `fetchone` and `fetchall` now read the initialized replica through independent, read-only SQLite connections. They do not wait for the primary connection or issue a pull. Writes, migration, backup, and recovery retain their existing ownership rules. Local-only deployment reads retain their normal serialized path; the explicit snapshot APIs are also available there.

The [Turso embedded-replica documentation](https://docs.turso.tech/features/embedded-replicas/introduction) describes local reads and automatic replica updates following successful writes. This preserves read-your-writes for the initiating replica after a successful write returns. It does not mean a read can see an uncommitted operation or another client's unsynchronized changes. Production should continue to use one bot instance. Process-local review/state locks are not a distributed coordination system.

Snapshots open the database with `mode=ro` and `query_only=ON`, preserve exact integer parameters, and retain the historical rounded-ID fallback when an exact lookup returns no rows. They cannot silently turn into writes. SQLite's progress handler stops excessively long queries. Cancellation keeps a reader slot occupied until its underlying thread exits, preventing timed-out callers from creating an unbounded pile of active reader threads. Replica corruption marks the cache unready for controlled reconstruction; it is not treated as an empty database or an invitation to write disposable fallback state.

| Work | Boundary |
| --- | --- |
| Standard remote reads | Four concurrent readers; five-second caller deadline |
| Short snapshot read | 0.75-second caller deadline |
| Interactive write queue | Existing ten-second acquisition deadline |
| Background activity batch | 0.5-second acquisition deadline; retain the batch if busy |
| Historical health write | 0.25-second acquisition deadline; keep the latest live sample if busy |
| Native operations | Existing normal-operation and worker RPC budgets remain in force |
| Request recovery fetch/send/edit | Fifteen-second per-call deadlines |
| Per-card refresh failure | Ten-minute retry backoff; other cards remain eligible |
| Failed ticket cache load | Thirty-second retry backoff; only one load starts at once |

These acquisition limits do not cancel a write that has already started. Unknown completion still requires receipt-based or guarded recovery, not blind replay.

## Activity: Small Transactions With Receipts

Previously the flush applied individual counter writes and then separate cooldown writes. A mid-batch error could leave some counters applied. Re-merging the entire failed batch into the next flush could then increment those counters a second time. Separate cooldown persistence also made cancellation and partial failure harder to reason about.

```text
Collect at most 50 counter rows + eligible cooldown rows
                         |
                  assign stable batch ID
                         |
                     BEGIN IMMEDIATE
                         |
       add counters only if this receipt does not exist
                         |
       update cooldowns only if this receipt does not exist
                         |
                   insert unique receipt
                         |
                        COMMIT
                         |
        success -> clear batch / yield to other writers
        uncertain -> keep SAME batch ID and immutable data
```

The `activity_flush_batches` ledger is committed with the counters and cooldowns. A retry of the same batch sees its receipt and does not increment again. New messages remain in a separate buffer and receive a different batch ID. Cooldowns take the maximum timestamp, and a cooldown is not moved ahead of activity for that user still waiting in the buffer.

General `Database.executemany` is now transaction-based as well: a failed statement rolls back its earlier statements rather than committing a partial batch. Non-idempotent general batches are still not automatically replayed.

Normal activity flushing yields after four chunks. Ranking, impact export, weekly processing, and graceful shutdown attempt a full drain, yielding the primary lock between chunks. Weekly winner selection checks for unconfirmed activity belonging to that week and postpones rather than choosing from incomplete counters. Resetting a guild's current week is serialized against flushes and preserves an uncertain receipt ID for any remaining activity from other guilds or weeks.

The synthetic 260-member regression case drains in six transactions: at most 18 multi-row write statements including receipts, rather than 520 individual counter/cooldown statements. This is a statement-volume comparison, not a measured production latency or guaranteed hosting speedup.

Schema 5 to 6 is a small additive transaction creating the ledger and updating the database contract. Business records are not replaced. Schema-probe failures now propagate instead of triggering a full migration as though the database were empty. A database newer than the running bot is rejected rather than silently downgraded. Receipts are included in normal database backups and are deliberately outside telemetry cleanup policies.

## Quieter Background Work, Clearer Health

Idle outbox recovery first performs a local read. It writes only if a processing claim is actually stale. Previously it performed a no-op primary update on every polling cycle, even with an empty queue.

The health sampler uses a short local probe, builds one runtime/provider batch, and keeps its latest sample in memory before attempting history persistence. If the primary queue is busy, it increments the deferred-sample count and yields. The next sample is not held behind a queue of old telemetry. A missing or uninitialized replica is not reported healthy simply because the short probe returned `None`.

Ticket-cache loading is single-flight. Concurrent messages do not initiate duplicate cold loads, and a failed or incomplete load is backed off instead of retried on every message. A partial load remains eligible for recovery; loading preserves tickets opened concurrently rather than overwriting their cache entries. Messages in the configured ticket category can still check their durable ticket row while the cache is cold. This does not bypass support access rules or invent ticket state when storage is unavailable.

The dashboard distinguishes current writers/readers, background deferrals, long-held connections, recent interactive queue timeouts, and genuine primary-write failures. Expected short background deferrals do not replace the last interactive timeout evidence. The runtime heartbeat also carries a `write_queue_stalled` flag when an active operation reaches the normal ten-second queue threshold. `/ready` and the public service status become degraded while that flag is active, even if replica reads and Discord heartbeats still work; they return to normal when the active stall clears. This does not reset process uptime.

Actual exceptions still use the counted, storage-independent incident reporter introduced in 3.22.1. Backing off or yielding expected background work avoids log floods without hiding unexpected failures.

## Request Cards: Recovery Without Undoing Reviews

```text
Expired pending request -> fresh external validation
                                    |
                         resolve saved Discord pointer
                                    |
               +--------------------+-------------------+
               |                    |                   |
          card found          confirmed missing    HTTP/permission error
               |                    |                   |
        reuse exact ID       recreate with nonce       back off
               |             retain delivery receipt   do NOT recreate
               +--------------------+
                                    |
                         recheck under review lock
                                    |
                     pending and same level still?
                                    |
                      guarded JSON + pointer update
                        + durable edit in one commit
                                    |
                       edit / retry through outbox
                                    |
            queued delivery rechecks exact pending JSON under same lock
```

The resolver checks historical rounded pointers against author and nearby-message context; it never guesses an ambiguous match. A confirmed missing pending card is recreated with a deterministic enforced Discord nonce. Its successful send is retained in memory before storage confirmation. Retrying a lost confirmation reuses that card, checks whether its new ID was already committed, and never deletes a canonical card that has since been reviewed or edited.

Both wave and weekly request cards use this path. The persisted pointer and validation edit intent commit atomically. Template variables are rendered with the corrected message ID. Queued validation edits carry the exact JSON snapshot and request kind. Delivery acquires the same review lock as final decisions, then discards obsolete work if the request was reviewed or subsequently edited. It therefore cannot restore action buttons after a final review or overwrite a newer user edit.

Per-card failure backoff is applied to the selection query before its batch limit. Two failing old cards cannot occupy every slot and starve later requests. Manual request repair and automatic validation recovery share a card-recovery lock to avoid simultaneous replacement sends through those paths.

## Honest Limits

- Turso/S3 outages, invalid credentials, and unavailable Discord channels still prevent successful writes or deliveries. The bot can contain and report them, not make providers infallible.
- Buffered activity, unpersisted incident deltas, the latest deferred health sample, and in-process send receipts are not guaranteed to survive an abrupt Render process kill. A successfully committed remote record and its receipt remain durable; this is not a promise that every pre-commit observation is durable.
- Deferred historical health samples are gaps, not silently fabricated data. Activity batches remain retryable while the process survives.
- Discord's nonce deduplication window is limited. A long outage plus loss of all receipt evidence can still require manual reconciliation.
- A card deleted again after a fresh validation snapshot may wait until the next expired refresh or `/requests repair` before recreation. The bot does not treat unrelated transport failures as proof of deletion.
- Historical ambiguous IDs or already inflated counters cannot be corrected safely without independent evidence. This repair prevents new retry inflation; it does not invent historical values.
- No production outage duration or universal bot availability claim can be derived from the few supplied error messages.

## Verification And Deployment

The full local quality gate passed: compilation, selected Ruff rules, **200 tests** with resource/deprecation warnings treated as errors, Bandit, and dependency audit. The new contention suite contributes **33 test cases**, including a real native worker, independent readers behind a held writer, atomic rollback, retry receipt/backup survival, cancellation ownership, migration failure handling, weekly postponement, cache coalescing/partial-load recovery/concurrent creation, telemetry deferral, readiness degradation, legacy/missing-card recovery, lost confirmations, and guarded queued edits after review or user edit.

No Render deploy, Git push, live Discord mutation, or website publication was performed in this repair. Deploy normally using the existing valid Turso credentials; no paid Render disk or replacement database URL is needed. Preserve a backup first, inspect startup's schema-6 result, and run the staging checks added to `TEST_CHECKLIST.md`. Approve the 3.22.2 release DM after those checks pass. The website's approved-release API will then receive the notes through the existing flow.

## Changed Files

| File | This follow-up |
| --- | --- |
| `utils/db.py` | Independent bounded readers, atomic executemany, activity receipt batches, additive schema 6, explicit migration failures, contention metrics |
| `utils/config_schema.py` | Database schema contract 6 |
| `utils/outbox.py` | Read-first stale recovery; exact pending-snapshot guards for queued validation edits |
| `utils/keepalive.py` | Sanitized read/backpressure metrics and degraded readiness during a slow active write |
| `cogs/Tracking.py` | Single-flight chunked flush, immutable retries, serialized reset, full-drain ranking, weekly postponement |
| `main.py` | Full-drain graceful activity shutdown |
| `cogs/Operations.py` | Local health probe, atomic history batch, short queue wait, latest in-memory sample and deferral count |
| `cogs/Help.py` | Single-flight ticket cache loading and retry backoff |
| `cogs/RequestLevels.py` | Legacy/missing-card recovery, receipts, atomic durable validation edits, race protection, non-starving backoff |
| `cogs/Commands.py` | Visible contention in full/recovery dashboards and draining before impact export |
| `tests/test_turso_contention.py` | 33 new regression cases |
| `tests/test_database.py` | Schema 6 and activity ledger expectations |
| `tests/test_runtime_resilience.py` | Updated validation-race fixture |
| `release.json` | Owner-approval proposal for 3.22.2 |
| `README.md` | Revised read/write model, recovery guarantees, test count and follow-up link |
| `TEST_CHECKLIST.md` | New contention/recovery staging checks |
| `docs/INCIDENT_DIAGNOSIS_2026-09-15.md` | Link to this subsequent diagnosis; original incident evidence retained |
| `docs/TURSO_CONTENTION_RECOVERY_2026-09-15.md` | This follow-up |

The long PDF/DOCX private manual was not regenerated in this follow-up. Its schema-5 incident chapter is supplemented by this schema-6 technical record.

## Website Update Text

Version: `3.22.2`  
Title: `Turso contention and request recovery`

```text
Kept replica reads responsive during slow Turso writes | Made activity batches atomic and retry-safe | Protected weekly rankings from unconfirmed activity | Reduced idle database writes and background log floods | Fixed partial and concurrent ticket cache recovery | Automatically recovered missing request cards | Prevented stale validation edits from reopening reviewed requests | Improved queue diagnostics and readiness | Added 33 recovery tests and updated documentation
```
