# GD Avenue Staff Portal

## Purpose

The Staff Portal is the preferred web interface for GD Avenue's internal review and outreach work. It does not replace the `/pps` Discord tools, create a second priority model, or let a browser connect to Turso. Both interfaces call the same Avenue Guard service methods and use the same durable queue, cycle, attempt, outbox, and workflow-event records.

The public website repository owns the presentation layer. Avenue Guard remains the business and authorization boundary.

```text
Browser
  |
  | same-origin cookie + CSRF token
  v
Netlify Functions
  |  Discord OAuth exchange
  |  X-Avenue-Portal-Key + opaque session token
  v
Avenue Guard private HTTP bridge
  |  fresh Discord member/role resolution
  v
StaffPortalService
  |  existing PrioritySystemService + DiscordOutbox
  v
Database wrapper -> local libSQL replica -> Turso durable truth
```

## Security Boundary

- Discord OAuth uses the `identify` scope. Avenue Guard verifies guild membership and current Discord roles itself.
- The browser never receives the Discord OAuth token, the private Avenue Guard API key, Turso credentials, or database access.
- The staff session token is random and only its SHA-256 digest is stored. The session cookie is `Secure`, `HttpOnly`, `SameSite=Strict`.
- Mutations use a double-submit CSRF token: browser JavaScript reads the non-HttpOnly CSRF cookie and sends it as a header, Netlify compares both values, and Avenue Guard verifies the token hash tied to the server session.
- Every authenticated request resolves the member's current Discord roles. Removing a role removes effective access on the next request; a role label saved in the session is not trusted.
- Netlify is the only intended caller of the private API and authenticates with `STAFF_API_TOKEN` / `AVENUE_GUARD_API_TOKEN`.
- Mutations require capability checks and an idempotency key. Dangerous actions also require a reason and explicit confirmation where applicable.
- Public level routes read only the allowlisted in-memory public cache. They never expose requester/reviewer IDs, exact PPS, exact rank, Creator Points, targets, private routes, notes, applications, QA, or audit events.

## Role And Capability Model

Discord roles are mapped in `config.json` under `staff_portal`.

| Role | Main capabilities |
|---|---|
| Applicant | Save, submit, track, and withdraw their own application |
| Reviewer | Portal access, queue view, own claim/release, outreach attempts/submissions/follow-ups, own tasks, private/team notes |
| Head Reviewer | Reviewer capabilities plus reassignment, stale release, queue state operations, team tasks, review QA, tier adjustment before submission, Reviewer applications |
| Admin | Normal request, PPS, tracking, forum, support, staff, and operations management; summarized incidents |
| Owner | Admin capabilities plus higher staff access, overrides, backups, restore drills, releases, full audit, and safe configuration |
| Dev | Owner capabilities plus sanitized runtime, schema, outbox, worker, provider, incident, and recovery diagnostics |

`Dev` is assigned only through `staff_portal.dev_user_ids`; it is never inferred from a Discord role or grantable from the portal. Legacy internal keys `judge` and `head_judge` remain accepted as aliases while every API and UI label is canonical.

The capability map lives in `utils/staff_auth.py`. UI visibility is convenience only; `services/staff_portal.py` enforces every capability again.

## Navigation

The portal deliberately has only four top-level modules:

```text
Overview
Work
  My Work | Queue | Outreach | Tasks | Notes
Team
  Overview | Statistics | Review QA | Applications | Staff
Admin (Admin+)
  Operations | Requests | PPS | Community | Staff | Audit | System | Configuration
```

Each Admin section is capability-gated again by Avenue Guard. Hiding a browser tab is never treated as authorization. The System section is Dev-only.

Overview is personalized and uses real counts. Progress bars appear only where the database provides a denominator, such as completed tasks or reviewed requests in the current wave. Counts are contribution indicators, not reviewer-quality or send-rate scores.

## Durable Data

Database schema 10 adds these tables without replacing existing request/PPS tables:

- `staff_web_sessions`
- `staff_queue_claims`
- `staff_queue_claim_events`
- `staff_outreach_episodes`
- `staff_tasks`
- `staff_notes`
- `staff_review_qa`
- `staff_applications`
- `staff_application_events`
- `staff_application_notes`
- `staff_members`
- `staff_portal_profiles`
- `staff_portal_nickname_history`
- `staff_milestones`
- `staff_idempotency`

It also adds `episode_id`, `private_target_key`, and `event_ts` to `level_outreach_attempts`.

Claims do not alter PPS, queue membership, or outreach counts. Exactly one claim row exists per queue entry. Claim ownership changes have their own history table and workflow events.

Outreach episodes preserve attempts and outcomes across a manual requeue. A confirmed submission is distinct from an attempt. A follow-up to the same normalized target in the same episode does not count as a new confirmed submission. A different target can be a new confirmed submission.

Application acceptance persists `accepted_pending_role` before requesting the Discord role side effect. The durable outbox uses a stable idempotency key; reconciliation recreates a missing outbox row and changes the application to `accepted` only after delivery succeeds.

System tasks are materialized for stale claims, unknown CP, due outcome windows, and applications waiting seven days. Existing unchanged tasks are read without producing a Turso write on every page view.

Staff access combines live Discord roles with persisted staff-change intent. This keeps inactive staff visible to the owner for restoration while current Discord roles remain the only source of effective access. Applicant status reads also reconcile delivered role outbox entries, so `accepted_pending_role` cannot remain stale merely because staff have not reopened the application queue.

## Private API

All private endpoints require the service key. Except for OAuth session creation, they also require a staff session. Mutation routes require CSRF and idempotency.

| Route | Purpose |
|---|---|
| `POST /api/staff/auth/session` | Exchange verified Discord user ID for an opaque portal session |
| `GET/DELETE /api/staff/session` | Read current capabilities or revoke the session |
| `GET /api/staff/overview` | Personalized progress and attention summary |
| `GET/PATCH /api/staff/profile` | Resolved Discord identity and private portal nickname |
| `GET /api/staff/queue` | Paginated exact-order queue and filters |
| `GET /api/staff/queue/{id}` | Internal level summary, PPS, outreach, history, notes |
| `POST /api/staff/queue/{id}/{action}` | Claim, release, reassign, state, requeue, or tier action |
| `GET/POST /api/staff/outreach` | Private outreach timeline or new event |
| `GET/POST/PATCH /api/staff/tasks...` | Personal, assigned, team, and system tasks |
| `GET/POST/PATCH /api/staff/notes...` | Scoped internal notes |
| `GET /api/staff/team` | Team workflow progress |
| `GET /api/staff/statistics` | Real activity aggregates and median turnaround |
| `GET/POST /api/staff/qa...` | Head/owner review QA |
| `GET/POST /api/staff/applications...` | Application management and decisions |
| `GET/POST /api/staff/staff...` | Capability-gated staff access and nickname management |
| `GET /api/staff/operations` | Structured runtime, database, worker, provider, outbox, and incident summaries |
| `GET /api/staff/operations/incidents/{fingerprint}` | Sanitized full trace for Owner/Dev |
| `GET/POST /api/staff/requests` | Request waves, scheduled openings, button refresh, and repair |
| `GET/POST /api/staff/community` | Tracking, support, forum, and server-presentation operations |
| `GET/POST /api/staff/pps` | Existing PPS dashboard and cycle/override services |
| `GET/POST /api/staff/system` | Dev-only sanitized diagnostics and safe recovery actions |
| `GET /api/staff/audit` | Filtered durable workflow events |
| `GET/PATCH /api/staff/configuration` | Allowlisted safe settings only |
| `GET /api/staff/search` | Permission-aware internal search |
| `GET/POST /api/apply...` | Applicant self-service |

The public endpoints are `GET /api/levels?q=...` and `GET /api/level/{level_id}`.

## Environment Variables

Render / Avenue Guard:

```text
STAFF_API_TOKEN=<at least 32 random bytes, URL-safe>
```

Netlify / GD Avenue website:

```text
DISCORD_CLIENT_ID=<Avenue Guard application ID>
DISCORD_CLIENT_SECRET=<Discord OAuth client secret>
OAUTH_STATE_SECRET=<at least 32 random bytes, independent from STAFF_API_TOKEN>
AVENUE_GUARD_API_URL=https://avenue-guard.onrender.com
AVENUE_GUARD_API_TOKEN=<exact same value as Render STAFF_API_TOKEN>
```

Generate the two random secrets separately, for example:

```bash
openssl rand -base64 48
```

Never place these values in `config.json`, JavaScript, Git, Netlify build output, or Discord messages.

## Discord OAuth Setup

In the Discord Developer Portal for Avenue Guard, add this exact redirect URL:

```text
https://gdavenue.netlify.app/api/auth/callback
```

For local Netlify development, add the local callback separately if needed. Production uses only the `identify` scope; guild role truth comes from Avenue Guard's live Discord connection.

## Deployment

1. Configure `admin_role_ids`, `owner_role_ids` or `owner_user_ids`, and the explicit `dev_user_ids` allowlist.
2. Deploy Avenue Guard. Startup applies the additive schema-10 tables through the existing database wrapper and Turso worker.
3. Confirm Render `/ready` returns HTTP 200.
4. Add `STAFF_API_TOKEN` to Render and redeploy.
5. Add the five website environment variables to Netlify.
6. Add the Discord OAuth redirect URL.
7. Deploy the website repository containing `netlify.toml`, `netlify/functions`, `/staff`, `/apply`, `/levels`, and `/level`.
8. Sign in as Reviewer, Head Reviewer, Admin, Owner, Dev, and an ordinary member to verify role-specific access.

If the bot is unavailable, Netlify returns a concise 503 and does not fall back to direct Turso access. Discord `/pps` remains the recovery interface.

## Production Smoke Test

- Open `/staff` signed out and complete Discord OAuth.
- Verify an ordinary member cannot open staff modules but can open `/apply`.
- Remove a test Reviewer role and verify their next staff request is denied.
- Confirm Reviewer, Head Reviewer, Admin, Owner, and Dev navigation differs as expected.
- Claim one queue entry in two browser sessions; verify only one owner wins.
- Release the owner's own claim; verify a Head Reviewer cannot release a fresh third-party claim but can release it after the configured stale threshold.
- Record an attempt, confirmed submission, same-target follow-up, and different-target submission.
- Verify the same-target second submission is rejected and suggests a follow-up.
- Requeue an eligible entry and verify W resets while old episode history remains.
- Create, complete, and inspect a personal task; verify generated attention tasks are not duplicated by refreshes.
- Create each permitted note scope and verify a different Reviewer cannot read a private note.
- Perform a QA action and confirm a post-submission tier correction is owner-only.
- Save and submit an application; retry the request and verify only one active application exists.
- Accept a test application and confirm `accepted_pending_role` changes only after the outbox delivers the Reviewer role.
- Promote/demote a test staff member and inspect the outbox and audit event.
- Search `/levels` by exact ID, name, and creator; inspect the response for absence of private fields.
- Check queue, applications, tasks, detail drawer, and statistics at 390 px width.
- Deactivate a test Reviewer, wait for role removal, and verify the inactive record remains available for Restore.
- Run `./scripts/quality_check.sh` in the bot repository.

The implementation was verified with Python compilation, critical Ruff checks, 395 passing Python tests, Bandit's high-severity gate, configuration validation, 26 website/Netlify tests, JavaScript syntax checks, and desktop/mobile browser inspection. The local `pip-audit` process stalled while importing its HTTP dependency and was stopped; rerun that external dependency audit in CI or the deployment environment before release.

## Recovery And Rollback

The portal can be disabled immediately by setting `staff_portal.enabled` to `false` in runtime configuration or removing `STAFF_API_TOKEN`. This leaves all Discord workflows and `/pps` intact. The schema is additive; disabling the portal does not require dropping tables. Netlify can independently roll back the site deployment without changing bot state.
