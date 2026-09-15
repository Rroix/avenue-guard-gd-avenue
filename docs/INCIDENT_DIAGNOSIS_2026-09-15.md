# Avenue Guard Runtime Incident Diagnosis

Private technical record for Rodrigo. Release proposal: **3.22.1**. Database schema: **5**.

## What Was Actually Established

The screenshots show failed slash/component interactions, an undelivered DM, and stale Geometry Dash validation evidence. They do not contain a Render traceback or prove that all three symptoms have one cause. Local investigation did establish a runtime-blocking defect in the deployed native database driver and several independent recovery and observability defects.

The key experiment used the installed `libsql==0.1.11` driver and an intentionally CPU-heavy local query called through `asyncio.to_thread`. The query took approximately **0.402 seconds** and an independently scheduled event-loop heartbeat had a maximum gap of approximately **0.411 seconds**. A separate thread therefore did not isolate this native call from Discord. These are local measurements, not estimates of the September 15 outage duration.

The [libSQL Python implementation](https://github.com/tursodatabase/libsql-python/blob/main/src/lib.rs) uses native blocking operations without releasing Python's GIL around the relevant work. The GIL is the interpreter execution lock. A thread performing native work while retaining that lock can prevent the main Python thread from running, even though the asyncio coroutine is awaiting a thread future. A remote call taking several seconds can consequently prevent heartbeats, slash acknowledgements, buttons, DMs, supervisors, and logs from progressing together.

Discord requires an initial interaction response within **three seconds**; opening a modal is an initial response, not a follow-up to a deferral. See the [Discord interaction documentation](https://docs.discord.com/developers/interactions/receiving-and-responding). A stalled event loop explains how a process can still answer HTTP liveness checks yet fail Discord interactions.

The public service was sampled in `discord_login` and later `online` during investigation. That establishes only those sampled states. Exact causes of every offline interval still require Render process/gateway logs and hosting information.

## Failure Chain

```text
Old path
Discord coroutine -> asyncio.to_thread -> native libSQL call retains GIL
                                                 |
                        main interpreter cannot run reliably
                                                 |
                   late acknowledgements / missed heartbeats
                                                 |
                failed buttons + stalled supervision and logging

New path
Discord process -> thread waits on IPC -> isolated libSQL process
       |                                      |
       |                              bounded native operation
       |                                      |
       +-> heartbeat / button / error log     result or worker termination
```

Process isolation contains the native driver's interpreter lock. It does not make Turso available during a provider outage or remove the need to recover an uncertain write.

## Corrected Defects

| Defect | Why it mattered | Correction |
| --- | --- | --- |
| Native driver held the interpreter lock | Threaded calls could still freeze unrelated Discord work | Spawn one libSQL worker process; communicate only materialized values over a pipe |
| Shared database queue had no acquisition deadline | A slow operation could hold every caller indefinitely | Ten-second queue acquisition deadline, explicit busy error, queue metrics |
| Cancelling a threaded query released connection ownership too early | Recovery or another query could use the same connection while the original native call was still running | Shield the operation and retain the lock until its thread finishes |
| Review button queried storage before opening its modal | Even a fast local read could queue behind a remote operation and miss the initial deadline | Open the modal/Other panel first; validate current state and reviewer rights on submission |
| Persistent components registered late | Saved buttons could have no handlers during startup | Register nine persistent view types before database preflight and Discord login |
| Cold command-ID lookup could trigger sync or use the wrong guild field | Known guild commands could fail before the ID cache filled | Resolve known command names with the actual interaction guild; do not REST-sync before acknowledging an invocation |
| Logger wrote to storage first | Storage failure delayed both stdout and Discord logs | Print and buffer immediately; independent bounded Discord delivery and persistence workers |
| Repeated log entries silently deduplicated | One visible log looked like one occurrence | Edit the incident embed with count and last-seen time; persist retry-safe deltas with UUID batch IDs |
| Component/modal failures had no central handlers | Button errors could bypass the dashboard incident model | Global component and modal handlers, traceback capture, safe user response |
| Dashboard depended on many sequential queries | Diagnostics failed when the database was the broken component | Five-second shielded build deadline and memory-only recovery view |
| Missing tasks skipped supervision | A missing task was not repaired | Restart missing/stopped/failed expected work and leave disabled optional work alone |
| Supervisor awaited database-dependent repair/history | The watcher could stall on the same database it should observe | Independent restart/history tasks and a memory-only heartbeat watchdog |
| Operations bootstrap ran before starting its pillars | Slow startup storage prevented supervisor/outbox/watchdog creation | Start pillars immediately and run persisted-settings bootstrap independently with retries |
| Empty default retention override replaced configuration | Startup unintentionally cleared configured retention | Apply only an actual stored dictionary override |
| Repeated ready events reran full initialization | Reconnects could duplicate initialization work | One initialization lock and completion flag; resume only refreshes connection state |
| A failed runtime initialization could leave a superficially online session | Startup errors were not always treated as retryable session failure | Close failed sessions, enforce a five-minute gateway initialization deadline, retry with existing startup backoff |
| Completed smoke work displayed as stopped | Health state contained false alarms | Mark successful one-shot tasks completed |
| Smoke test lost its result if schema/history storage failed | Diagnosis disappeared precisely during an outage | Store the result in memory before persistence; record schema probe errors explicitly |
| Outbox abandoned claims recovered only at startup | Interrupted actions could remain processing while the process stayed online | Runtime stale-claim recovery after two minutes |
| Discord success followed by receipt-write failure could resend | A second attempt had no evidence that Discord had accepted the first | Retain in-process delivery receipts and use deterministic enforced Discord nonces for recent sends |
| Reviewed embed edits were not durable | Saved reviews could leave active buttons after an HTTP edit failure | Commit a disabled-button edit with the review decision and notification intents |
| Validation expiry lacked a routine refresh path | Request cards advertised refresh times in the past | Small bounded pending-validation batches and explicit expired-snapshot wording |
| Validation refresh could race with review or edit | Stale data could overwrite new fields or reactivate buttons | Recheck pending state and level ID under the review lock; compare-and-set the previous JSON snapshot |
| Reviewed-wave repair used a weekly-only channel field | Repair could fail after a decision was saved | Resolve the live review channel from the proper source |

## Storage Boundaries And Deadlines

`utils/libsql_worker.py` owns a synchronous facade, but the native connection exists only in the spawned child. SQL, parameters, rows, cursor metadata, and errors cross the process boundary. Native cursors and asyncio objects do not. Decimal binding of large Discord IDs remains intact; the worker does not reintroduce floating-point snowflake loss.

| Boundary | Default | Meaning |
| --- | --- | --- |
| Queue acquisition | 10 seconds | The caller has not started its operation when this deadline expires |
| One worker RPC | 20 seconds | A stalled child can be terminated instead of hanging the Discord process |
| Normal database operation | 30-second shared worker budget | Multi-statement operations cannot extend their child RPC budget indefinitely |
| Connection and migration work | 90-second worker budget | Startup allows a larger bounded schema/replica preparation window |
| Read-only replica snapshot | 0.75-second caller deadline | Modal gating does not acquire the primary connection lock or pull remote state |
| Full dashboard build | 5 seconds | A cached build may finish in the background; users receive the recovery view meanwhile |
| One outbox Discord delivery | 45 seconds | Slow Discord I/O eventually releases the delivery attempt for retry |
| Individual incident Discord I/O | 15 seconds | A broken notification path cannot permanently hold the delivery worker |
| Gateway runtime initialization | 300 seconds | A session that never initializes is closed for the normal backed-off retry |

`TURSO_WORKER_TIMEOUT_SECONDS` optionally changes the RPC deadline. Leave it unset initially. A large value does not extend the enclosing normal-operation budget. No new dependency or paid disk is required. One extra process has a memory cost; monitor Render memory after deployment rather than starting multiple bot instances against the same database.

Deadlines use `time.monotonic()` because wall-clock corrections must not change a timeout. Stored timestamps use epoch time for records and Discord display. This is a monotonic clock distinction, not an external atomic-clock synchronization service.

```python
# Simplified ownership rule, not a copy of the complete implementation.
async with database_lock:
    running = asyncio.create_task(asyncio.to_thread(native_worker_operation))
    try:
        result = await asyncio.shield(running)
    except asyncio.CancelledError:
        # Do not allow a reconnect or another query to use the connection
        # while its previous thread is still waiting for a worker reply.
        await wait_until_the_thread_finishes(running)
        raise
```

An RPC timeout means **completion is unknown**, not necessarily that a write failed. Non-idempotent writes therefore retain the existing no-blind-retry policy. Idempotent operations may reconnect and retry. Reviews use guarded state transitions plus unique outbox keys so a retry cannot create a second final-result queue row. After an uncertain review commit the response tells the reviewer to check the current decision before retrying.

Schema 5 adds `error_incident_batches`. An incident delta and its unique batch ID are committed together. Retrying the same batch after an uncertain commit does not increment the occurrence count again. The migration is additive; business records are not replaced.

## Logging And Delivery Guarantees

```text
Failure -> redact and print deployment log immediately
                  |
                  +-> memory incident count -> Discord embed update
                  |
                  +-> pending delta + batch ID -> Turso transaction -> durable count
```

The reporter buffers up to 512 incident groups and retries persistence while the process is alive. The recovery dashboard includes pending persistence counts. If Turso remains unavailable and Render kills the process, memory-only deltas cannot be guaranteed to survive. Deployment stdout remains a separate forensic channel, subject to Render's own retention. Backups and remote storage remain necessary; no code can honestly promise data is impossible to lose under every infrastructure failure.

Likewise, a deterministic Discord nonce deduplicates recent sends only within Discord's recent-message window. In-process receipts cover a successful send followed by a database confirmation failure while that process survives. These measures reduce duplicates; they are not an unlimited exactly-once delivery promise across a long outage and process loss.

## Readiness And The Public Website

`/health` and `/` retain HTTP 200 process liveness. `/ready` reports HTTP 200 only when the gateway state is online, the runtime heartbeat is less than 15 seconds old, the database is connected, the remote worker is alive when applicable, primary writes are not marked degraded, and expected runtime tasks are not stopped/failed/missing. Intentionally disabled tasks and completed auxiliary tasks are valid states.

`/api/bot` exposes sanitized `ready` and `responsive` fields. The status becomes **Degraded** when the gateway is online but runtime readiness fails, or **Unavailable** when the online runtime heartbeat is stale. No SQL text, token, requester ID, or private error body is added to the public heartbeat metrics. Process uptime remains separate from Discord connection uptime and is not reset by health polling.

Point an UptimeRobot check intended to measure usable bot availability at `https://avenue-guard.onrender.com/ready`, not just `/health`. An HTTP monitor cannot keep a free host awake with an absolute guarantee; hosting suspend/restart policy remains an external constraint.

## The DM And Boomlings Symptoms

Clyde's delivery-denied message happens before a bot message event exists. The bot cannot receive or log a DM Discord refuses to deliver. Mutual-server membership, privacy settings, blocks, verification/screening, and other Discord restrictions should be checked separately. See [Discord's explanation of DM delivery failures](https://support.discord.com/hc/en-us/articles/360060145013-Why-isn-t-my-DM-going-through). The screenshots alone do not identify which restriction applied.

Boomlings HTTP 403 is an upstream denial, not evidence the level is missing or a fatal bot runtime error. Existing fallback/cooldown logic remains in place. If GDBrowser finds the level, that positive evidence is retained. Pending cards now receive small scheduled refreshes; an expired card explicitly asks for Recheck until refreshed. Provider availability still cannot be guaranteed by the bot.

## Verification And Deployment

The automated suite contains **167 passing tests** after these changes. New tests cover real native-process isolation, worker deadlines, exact large IDs, rollback/backup, queue acquisition, cancellation ownership, local snapshots, incident delivery during a stuck database, uncertain incident commits, missing-task recovery, independent bootstrap, concurrent startup hooks, cold command IDs, early persistent views, startup deadlines, global component/modal errors, readiness, dashboard fallback, durable reviewed-button edits, post-send receipt failures and receipt cleanup after uncertain confirmations, smoke failures without storage, and refresh/review races.

Deployment does not require replacing the Turso URL/token or mounting a premium disk. Deploy the code normally with the existing valid database credentials. Startup pulls the primary and applies the additive schema migration. Approve the **3.22.1** release DM only after the Discord smoke checks pass; website changes remain gated by the existing owner approval flow.

After deployment, check `/ready`, run `/bot dashboard`, open a saved review modal, submit one test review, and confirm all old buttons disable and exactly one result appears. Confirm automatic close/scheduled openings, activity flushes, daily/weekly summaries, and the outbox loop are running. Repeated errors should increase a visible count. Use `TEST_CHECKLIST.md` for test-deployment failure injection rather than damaging live data or revoking production credentials casually.

The work was verified locally. No Render deployment, Git push, or website publication was performed from this workspace during this repair.

## Changed Files

| File | Change |
| --- | --- |
| `main.py` | Early persistent views, command-ID fallback, one-time initialization, startup deadline/backoff, managed shutdown |
| `utils/libsql_worker.py` | New process-isolated native connection and bounded IPC facade |
| `utils/db.py` | Bounded queue, cancellation ownership, isolated remote access, local snapshots, schema fast path, incident batch migration, health metrics |
| `utils/errors.py` | Storage-independent counted incidents, idempotent persistence, bounded delivery, central component/modal errors |
| `utils/supervision.py` | New shared per-cog startup lock |
| `utils/config_schema.py` | Database schema contract 5 |
| `utils/keepalive.py` | Heartbeat, readiness route, sanitized degraded/unavailable status |
| `utils/outbox.py` | Runtime stale claims, bounded delivery, receipts/nonces, disabled reviewed-view edits |
| `utils/gd_validation.py` | Clear expired-snapshot wording |
| `cogs/Operations.py` | Independent pillars/bootstrap, missing-task repair, watchdog, retained defaults, visible smoke failures |
| `cogs/Commands.py` | Bounded dashboard build and memory-only recovery/incident view |
| `cogs/RequestLevels.py` | Immediate review modal response, durable final card edit, safe periodic validation, reviewed-wave repair |
| `tests/test_runtime_resilience.py` | New runtime/outage/race regression suite |
| `tests/test_database.py` | Schema 5 and isolated recovery expectations |
| `tests/test_gd_validation.py` | Expired-evidence wording expectation |
| `tests/test_release_updates.py` | Healthy runtime heartbeat in public status fixture |
| `release.json` | Owner-approved 3.22.1 proposal |
| `README.md` | Current runtime/storage/readiness behavior and test count |
| `Readme-new.md` | Public reliability explanation and honest delivery limits |
| `TEST_CHECKLIST.md` | Runtime outage and deployment checks |
| `scripts/build_manual.py` | Revised architecture explanations and current code excerpts |
| `docs/Avenue_Guard_Manual.md` | Regenerated private technical manual |
| `docs/Avenue_Guard_Manual.docx` | Regenerated visual private technical manual |
| `docs/Avenue_Guard_Manual.pdf` | Regenerated visual private technical manual |
| `docs/INCIDENT_DIAGNOSIS_2026-09-15.md` | This diagnosis, evidence, deployment guidance, and changed-file inventory |

## Public Update Text

Version: `3.22.1`  
Title: `Runtime isolation and resilient recovery`

Changes for the `/bot release` single-line field:

```text
Isolated Turso work to prevent Discord freezes | Hardened startup, reconnects and background-task recovery | Restored saved buttons earlier and removed pre-modal database waits | Added counted incident updates and storage-independent error reporting | Added a recovery dashboard and accurate readiness checks | Improved interrupted delivery recovery and durable reviewed-button disabling | Refreshed stale pending validation safely and fixed request repair | Expanded outage regression tests and private documentation
```
