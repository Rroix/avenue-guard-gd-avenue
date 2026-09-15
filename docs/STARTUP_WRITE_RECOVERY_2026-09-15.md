# Startup Writes And Uptime Recovery

Private incident follow-up for Rodrigo. Release proposal: **3.22.3**. Database schema remains **6**.

This follows the [Turso contention repair](TURSO_CONTENTION_RECOVERY_2026-09-15.md). The new screenshot and message report two unstarted startup writes: expired GD cache deletion and uptime initialization. Both timed out behind a transaction. They do not establish that the database was corrupt, the token was invalid, or that every feature stopped. The old generic `holder=transaction` message also did not identify which feature owned that transaction.

## Source-Level Causes

1. `RequestLevelsCog.start_background` still attempted a primary DELETE before starting automatic close, scheduled opening, and validation tasks. Cleanup was optional, but its position and ordinary ten-second queue wait made it part of the startup path.
2. `ReleaseCog.start_background` attempted one-time uptime setup after other cogs and the supervisor had already started jobs. It marked startup attempted before its I/O completed, logged a busy setup as failed, and did not independently retry all unfinished bootstrap work.
3. The supervisor could classify not-yet-started cogs as missing and start repairs while the main initializer was still traversing its startup list. Per-cog locks prevented duplicate execution of the same cog, but did not stop separate cogs from starting concurrently.
4. Uptime used read-then-increment writes without a timestamp predicate on the write. Its initialization timestamp was taken on each attempt. Retry logic therefore needed more than suppressing the busy exception: it needed stable boundaries and idempotent updates.

The exact production transaction holder and its delay require deployment logs. New task ownership and operation labels provide that evidence for subsequent incidents.

## Revised Startup Order

```text
Database preflight, runtime settings and allowed-guild checks
                            |
                 capture uptime startup boundary
                 short guarded persistence attempt
                            |
                   start background cogs
                            |
        +-------------------+----------------------+
        |                   |                      |
   request tasks       status metrics       release bootstrap
   start directly      run independently    retry unfinished setup
        |                                          |
   later bounded                            uptime / cache / proposal
   GD cache maintenance
                            |
               capture end-of-initialization boundary
                     publish gateway online
                            |
               supervisor repairs become eligible
```

The uptime attempt can defer; it is not allowed to require a permanently free primary queue. Its acquisition limit is 0.5 seconds. An already started native operation still has the existing worker budgets and cancellation ownership rules. The patch does not pretend that this limit cancels a remote write after it starts.

Release startup creates its metrics and bootstrap tasks immediately. Bootstrap retries every 30 seconds until its steps complete, preserves the existing owner-approval workflow, and reports actual exceptions through the incident reporter. A busy unstarted write produces a concise deployment-log deferral instead of abandoning setup. The metrics loop can recreate an interrupted bootstrap. Shutdown cancels and joins both tasks before closing storage.

The supervisor continues observing startup states, but does not launch missing-cog repairs while the main runtime initializer remains in charge. Existing per-cog startup locks still apply. The existing runtime initialization watchdog remains the escape path if initialization never completes.

## Uptime Checkpoints Without Replay Inflation

The aggregate row still stores the original tracking start, last committed heartbeat, total observed seconds, and total online seconds. No table or existing history is replaced.

Initialization captures one startup boundary and keeps it across retries. Its transaction inserts the singleton only if absent and adds the restart gap only when the stored heartbeat is earlier than that boundary. A lost confirmation followed by a retry cannot add the same gap again.

Each subsequent observation carries a timestamp and whether the preceding interval was online. Forced disconnect/resume observations are captured before the first await, so queue contention does not erase those boundaries. Consecutive observations with the same state coalesce; alternating states remain separate.

```sql
-- Simplified heartbeat update: elapsed time comes from the stored checkpoint,
-- not an earlier read that could become stale before the write starts.
UPDATE bot_uptime_tracker
SET observed_seconds = observed_seconds + :target - last_heartbeat_ts,
    online_seconds = online_seconds +
        CASE WHEN :was_online THEN :target - last_heartbeat_ts ELSE 0 END,
    last_heartbeat_ts = :target
WHERE id = 1 AND last_heartbeat_ts < :target;
```

If the target was already committed, the WHERE predicate prevents another increment. If a later observation coalesces into a newer target, only the remaining elapsed time is added. Pending states are removed only after confirming the stored heartbeat; cancellation or an unknown write result keeps them retryable. Transactions contain at most 32 observations, preventing a large backlog from becoming one unbounded operation.

```text
Example with a delayed writer

1000                1080             1140             1200
  |------ online -----|--- offline ---|---- online -----|
         80 s                60 s            60 s

Observed: 200 s    Online: 140 s    Percentage: 70%
```

Read-only uptime snapshots use a short independent replica read. They never initialize the tracker or write a heartbeat. When persistence is delayed, snapshots project known history through the buffered observations while the process survives. If no committed row has ever been loaded, they return unknown percentage/zero tracking origin rather than inventing durable history. A temporary unavailable replica can retain the last known history in memory.

The admin dashboard displays Current or Pending uptime persistence, flags delays longer than two minutes, and shows the active writer's owning task in its recovery view. Operations snapshots include the release bootstrap's running/completed state; this optional publication bootstrap is not itself a reason to call a usable gateway offline. Existing write-stall readiness still applies.

Service-process uptime remains the existing monotonic process measurement. These checkpoints affect persisted availability percentage, not the process start timestamp. A busy write or read-only status poll does not restart the uptime clock.

## Validation Cache Maintenance

Request startup no longer performs the DELETE. Its existing periodic validation task runs maintenance later:

```text
Is the initialized replica available and maintenance due?
                            |
               short read: any expired cache row?
                     /                      \
                   no                       yes
                   |                         |
              wait one hour         delete at most 200 expired rows
                                    guarded transaction / 0.25 s queue
                                      /                  \
                                    busy                success
                                     |                    |
                              back off five min     check next minute
```

Fresh cache rows and request submission data are untouched. Unexpected cleanup errors remain counted incidents and back off for five minutes; expected busy deferrals do not create the old startup error. Cancellation still propagates. Maintenance does not stop the subsequent pending-validation refresh when it has to defer.

## Better Ownership Evidence

Database transactions accept a diagnostic label without changing their underlying write-health or retry semantics. Uptime uses `uptime.initialize` and `uptime.checkpoint`; cache maintenance uses `requests.validation_cleanup`; activity uses `tracking.activity`.

The primary guard also records the current asyncio task name. Busy messages include that owner, and startup temporarily names the owning task `avenue-guard:startup:<CogName>` before restoring its previous name in a finally block. Automatic close and scheduled-opening tasks receive persistent descriptive names. This is diagnostic ownership, not a new lock or a distributed transaction system.

## Verification And Limits

The full local quality gate passed with **219 tests**, including **19 new startup-write cases**, warning-strict pytest, compilation, selected Ruff rules, Bandit, and dependency audit. Tests include a real native replica worker, busy initial setup, delayed reconnect boundaries, lost initialization/checkpoint confirmations, read-only snapshots, bounded backlog flushing, cancellation, independent task startup, bootstrap retries and shutdown, bounded/read-first cache cleanup, real-error visibility, and scoped owner names.

Successfully committed uptime history remains in Turso and in database backups. Buffered observations are memory state: cancellation does not discard them, but a killed Render process during a prolonged database outage can. A later boot conservatively accounts for the unobserved restart gap; the bot cannot recover exact transitions that never reached durable storage. This patch does not promise perfect historical availability under all provider failures.

The supplied error lines identify queue contention, not a confirmed permanent Turso outage. A primary that continues stalling will still affect writes, now with more useful ownership evidence. No database timeout was made unlimited, no premium disk was added, and no credentials were changed.

No Git push, Render deploy, live Discord mutation, or website publication was performed. Redeploy normally with the current Turso URL/token, preserve a backup, run the startup checks added to `TEST_CHECKLIST.md`, and approve the 3.22.3 release DM after checking the result. The existing owner approval remains required before notes appear on the website.

## Changed Files

| File | Change |
| --- | --- |
| `main.py` | Uptime boundaries before/after cog startup; release-task shutdown |
| `cogs/Release.py` | Guarded uptime writes, retained observations, read-only previews, independent retrying bootstrap, lifecycle management |
| `cogs/RequestLevels.py` | Remove startup DELETE; bounded cache maintenance, backoff and named tasks |
| `cogs/Operations.py` | Suppress premature startup repairs; expose bootstrap state |
| `cogs/Commands.py` | Pending uptime checkpoint, sustained-delay diagnostics and writer owner |
| `utils/db.py` | Diagnostic labels and active task ownership; preserve base write-health semantics |
| `utils/keepalive.py` | Sanitized owner metric and optional bootstrap readiness classification |
| `utils/supervision.py` | Scoped descriptive startup task names |
| `tests/test_startup_writes.py` | 19 new regression cases |
| `release.json` | Owner-approval proposal for 3.22.3 |
| `README.md` | Current startup/write behavior and test count |
| `TEST_CHECKLIST.md` | Startup-write staging checks |
| `docs/STARTUP_WRITE_RECOVERY_2026-09-15.md` | This diagnosis and changed-file inventory |

The full PDF/DOCX manual was not regenerated; this record supplements its previous runtime chapters.

## Website Changelog

Version: `3.22.3`  
Title: `Startup writes and uptime recovery`

```text
Fixed uptime startup retries under database contention | Preserved delayed online/offline checkpoints without double-counting | Moved expired validation-cache cleanup into bounded maintenance | Prevented premature startup repairs | Added pending uptime diagnostics and writer ownership | Added 19 startup-recovery tests
```
