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
- Dev role preview is evaluated by Avenue Guard, never by the browser. Preview requests receive the selected role's capabilities, and every mutation is rejected until preview mode is left.
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
| Admin | Head Reviewer capabilities plus Mod applications and standard administration |
| Admin | Normal request, PPS, tracking, forum, support, staff, and operations management; summarized incidents |
| Owner | Admin capabilities plus higher staff access, overrides, backups, restore drills, releases, full audit, and safe configuration |
| Dev | Owner capabilities plus sanitized runtime, schema, outbox, worker, provider, incident, and recovery diagnostics |

`Dev` is assigned only through `staff_portal.dev_user_ids`; it is never inferred from a Discord role or grantable from the portal. Legacy internal keys `judge` and `head_judge` remain accepted as aliases while every API and UI label is canonical.

Dev users may add or remove persisted team profiles by exact Discord ID. Adding a profile still requires the target to be a current guild member and requests the configured Discord role through the durable outbox. Removing a profile removes all managed staff roles and keeps a minimal audited `removed` record rather than erasing history.

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

Database schema 11 includes these tables without replacing existing request/PPS tables:

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

It also adds `episode_id`, `private_target_key`, and `event_ts` to `level_outreach_attempts`; reversible `hidden_from_state` storage to `level_outreach_queue`; and application prompt, review-thread, and interview-ticket delivery identifiers to `staff_applications`.

Claims do not alter PPS, queue membership, or outreach counts. Exactly one claim row exists per queue entry. Claim ownership changes have their own history table and workflow events.

Outreach episodes preserve attempts and outcomes across a manual requeue. A confirmed submission is distinct from an attempt. A follow-up to the same normalized target in the same episode does not count as a new confirmed submission. A different target can be a new confirmed submission.

Application acceptance persists `accepted_pending_role` before requesting the Discord role side effect. The durable outbox uses a stable idempotency key; reconciliation recreates a missing outbox row and changes the application to `accepted` only after delivery succeeds.

Private session responses include a versioned API contract and explicit feature keys. The website gates newer controls against those keys, so deploying the website before its matching Avenue Guard release leaves the affected Dev controls visibly disabled instead of calling a backend route that does not exist.

Application forms are configured by type through `staff_portal.application_forms`. Reviewer (`judge`) and Mod (`mod`) applications share the same typed question engine, server-side validation, durable state machine, Discord review channel, notes, interviews, and decisions. Short text, long text, and single-choice questions are supported. A question marked `uses_review_prompt` receives one weighted entry from `staff_portal.application_review_levels`; the chosen key is persisted with the Reviewer draft so refreshes cannot reroll it. The applicant browser suggests its local IANA timezone only when the saved timezone answer is blank. Avenue Guard permits one active application per type and applies the five-day cooldown only to repeat submissions of that same type.

On submission, the outbox creates one review discussion in `application_review_channel_id` containing the application type, applicant, Staff Portal link, and a stable snapshot of every question, answer, and selected showcase. Forum channels receive a forum post; normal text channels receive a notification with a public thread attached. Head Reviewers can review Reviewer applications; Admins can additionally review Mod applications; Owner and Dev capabilities can review every stored type. Mod role delivery is optional through `staff_portal.mod_role_ids`; when it is empty, acceptance is recorded without ever substituting the Reviewer role.

Application delivery is restart-safe. A submitted application keeps its outbox identity and Discord thread identity in Turso. Startup reconciliation revives a dead or missing application delivery, while the outbox reuses an existing stored thread before sending the answer snapshot. Dead-letter logs include the outbox ID, action, channel, target user, attempt count, and terminal error so a configuration or permission problem is diagnosable without database access.

A Dev can use `DELETE /api/apply/mine` with the exact confirmation value `DELETE` to remove their own stale application data. The operation atomically removes application rows, child events and notes, retires pending application deliveries, and clears application idempotency cache entries. Existing Discord threads or interview channels are preserved for audit safety and reported in the response rather than silently deleted. If a deleted submission was still within its normal five-day cooldown, Avenue Guard retains only a minimal per-type cooldown receipt and replaces the remaining delay with 24 hours from deletion; deleting application content can therefore never remove the cooldown entirely.

The interview decision persists before side effects. Its outbox action creates or reuses a private ticket, records it in the normal `tickets` table, posts the opening status, and enqueues a separate durable applicant DM. If an application is already in the interview state, **Do another interview** creates a distinct, restart-safe interview run with its own marker and delivery keys. Accept-without-interview uses the existing role outbox; rejection also uses a durable DM. Staff can send an audited private message to the applicant at every visible application stage without changing the application status. Retries use stable per-run keys and stored Discord IDs to avoid creating duplicate threads, tickets, or notifications.

The `hidden` queue state is Dev-only and reversible. Normal queue reads, counts, public caches, priority statistics, and maintenance workflows exclude hidden rows. The previous queue state is retained in `hidden_from_state` and restored explicitly, with both actions recorded in `workflow_events`.

Manual personal, assigned, and team tasks enqueue private Avenue Guard notifications after the task row is committed. Personal tasks notify their creator, assigned tasks notify the selected active staff member, and team tasks notify every active staff identity, including the creator. Each notification contains the title, description, priority, type, and due date. Stable per-task/per-recipient idempotency keys prevent duplicate logical notifications during outbox retries. The staff-only assignee directory is resolved server-side and task or queue reassignment rejects an ID that is not an active staff identity.

The optional linked-record fields attach a task to an internal level queue entry, staff application, or staff task. They do not change workflow state or notify the linked record's owner. Both the type and numeric internal record ID are required together; ordinary tasks should leave both blank.

### Application configuration

Each `application_forms.<type>.questions` list is ordered exactly as it appears on `/apply`. Supported types are `short_text`, `long_text`, and `single_choice`; choice options are validated on the server, not only in HTML. Set `uses_review_prompt: true` only on a form that needs a randomly selected level-review showcase. `application_types` controls the enabled types, while the public Appeal application remains disabled until its form and workflow are intentionally configured.

Each `application_review_levels` entry has a stable key, display name, Geometry Dash level ID, HTTPS YouTube URL, and positive integer `weight`. Selection probability is relative: weights `1`, `2`, and `7` produce approximately 10%, 20%, and 70% over many new drafts. The selected key is written to the draft once, so saving, refreshing, or submitting cannot reroll the level.

The current reviewer pool contains Synergy, Madeline, V I B E, Speed, and Distimia at equal weight. Avenue Guard validates each YouTube video ID and exposes a derived `youtube-nocookie.com` embed URL; `/apply` renders that showcase directly above the response field while retaining a normal YouTube link as a fallback.

System tasks are materialized for stale claims, unknown CP, due outcome windows, and applications waiting seven days. Existing unchanged tasks are read without producing a Turso write on every page view.

Staff access combines live Discord roles with persisted staff-change intent. This keeps inactive staff visible to the owner for restoration while current Discord roles remain the only source of effective access. Applicant status reads also reconcile delivered role outbox entries, so `accepted_pending_role` cannot remain stale merely because staff have not reopened the application queue.

## Discord Identity Integrity

Discord snowflakes are exact 64-bit identifiers. SQLite `INTEGER` and Python `int` preserve them, but JavaScript `Number` cannot represent identifiers above `2^53 - 1` exactly. Every staff-portal response therefore serializes Discord identity fields as JSON strings, and browser code keeps them as strings without `Number(...)` or `parseInt(...)` conversion.

The remaining historical corruption was traced to the old `libsql-python` 0.1.x parameter binder: it attempted an `i32` extraction and then fell back to `f64` for larger Python integers. The value first differed when that driver bound a valid Python `int` for Turso, before the API or browser rendered it. Current database writes stringify large integer parameters before libSQL binding, so the driver receives every digit losslessly.

At startup, the existing bounded Turso repair compares legacy float buckets with current Discord guild members, bot users, and configured owner identities. The repair now covers the newer staff, QA, application, claim, outreach, task, note, profile, and milestone tables and their actor columns. It repairs only one-to-one matches; ambiguous buckets remain unchanged. Each attempted staff repair is recorded atomically in `staff_snowflake_repairs` with the table, column, old value, repaired value, status, source, changed-row count, and timestamp. Dev users can see aggregate repaired and conflict counts under **Admin > System > Schema and delivery diagnostics**.

The configured portal Admin role is `901431567719731230`. Owner and Dev access remain user-ID/config based and are still resolved server-side from current Discord membership and configuration.

## Portal Workspace

The v2 portal is organized as a continuous operational workspace rather than a collection of dashboard cards. Its primary navigation is limited to Overview, Work, Team, and Admin; role-gated secondary sections expose only the tools available to the authenticated staff member. Queue records, tasks, applications, QA records, and staff profiles open in keyboard-accessible side inspectors so the main working context remains visible.

Overview is personal to the signed-in staff member, while Queue and My Work prioritize compact, scannable records. Admin and developer diagnostics use progressive disclosure so routine health information appears first and recovery controls remain available without dominating the page. `Cmd+K` or `Ctrl+K` opens the shared search and jump interface, and the layout collapses to a full-width inspector and stacked navigation on small screens.

## Private API

All private endpoints require the service key. Except for OAuth session creation, they also require a staff session. Mutation routes require CSRF and idempotency.

| Route | Purpose |
|---|---|
| `POST /api/staff/auth/session` | Exchange verified Discord user ID for an opaque portal session |
| `GET/DELETE /api/staff/session` | Read current capabilities or revoke the session |
| `GET/DELETE /api/apply/mine` | Read the applicant's records or perform a confirmed self-reset |
| `GET /api/staff/overview` | Personalized progress and attention summary |
| `GET/PATCH /api/staff/profile` | Resolved Discord identity and private portal nickname |
| `GET /api/staff/queue` | Paginated exact-order queue and filters |
| `GET /api/staff/queue/{id}` | Internal level summary, PPS, outreach, history, notes |
| `POST /api/staff/queue/{id}/{action}` | Claim, release, reassign, state, requeue, tier, Dev hide, or Dev restore action |
| `GET/POST /api/staff/outreach` | Private outreach timeline or new event |
| `GET/POST/PATCH /api/staff/tasks...` | Personal, assigned, team, and system tasks |
| `GET/POST/PATCH /api/staff/notes...` | Scoped internal notes |
| `GET /api/staff/team` | Team workflow progress |
| `GET /api/staff/statistics` | Real activity aggregates and median turnaround |
| `GET/POST /api/staff/qa...` | Head/owner review QA |
| `GET/POST /api/staff/applications...` | Application management and decisions |
| `GET /api/apply/options` | Enabled application types plus per-type active application and cooldown state |
| `GET /api/apply/form/<type>` | Server-defined typed application form and resumable draft |
| `GET/POST /api/staff/staff...` | Capability-gated staff access, Dev profile creation/removal, and nickname management |
| `GET /api/staff/operations` | Structured runtime, database, worker, provider, outbox, and incident summaries |
| `GET /api/staff/operations/incidents/{fingerprint}` | Sanitized full trace for Owner/Dev |
| `GET/POST /api/staff/requests` | Request waves, scheduled openings, button refresh, and repair |
| `GET/POST /api/staff/community` | Tracking, support, forum, and server-presentation operations |
| `GET/POST /api/staff/pps` | Existing PPS dashboard and cycle/override services |
| `GET/POST /api/staff/system` | Dev-only sanitized diagnostics and safe recovery actions |
| `GET /api/staff/audit` | Filtered durable workflow events |
| `GET/PATCH /api/staff/configuration` | Allowlisted safe settings only |
| `GET /api/staff/search` | Permission-aware internal search |
| `GET /api/apply/form` | Current draft plus server-configured typed questions and assigned review prompt |
| `GET/POST /api/apply...` | Applicant save, submit, status, and withdrawal self-service |

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
2. Deploy Avenue Guard. Startup applies the additive schema-11 migration through the existing database wrapper and Turso worker.
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
- Enter each Dev **View as** role and verify GET responses match that role while POST/PATCH/DELETE requests return `view_mode_read_only`.
- Claim one queue entry in two browser sessions; verify only one owner wins.
- Release the owner's own claim; verify a Head Reviewer cannot release a fresh third-party claim but can release it after the configured stale threshold.
- Record an attempt, confirmed submission, same-target follow-up, and different-target submission.
- Verify the same-target second submission is rejected and suggests a follow-up.
- Requeue an eligible entry and verify W resets while old episode history remains.
- Create personal, assigned, and team tasks; verify the recipient DMs include title, description, and due date, including a self-DM for the personal task and one DM per active staff member for the team task.
- Search the assignee picker by staff name, role, and Discord ID; verify inactive and non-staff identities cannot be submitted through the API.
- Link a task to a queue entry and verify the inspector explains and displays the internal record; verify an incomplete or unsupported link is rejected.
- Create each permitted note scope and verify a different Reviewer cannot read a private note.
- Perform a QA action and confirm a post-submission tier correction is owner-only.
- Save and submit an application; retry the request and verify only one active application exists for that type.
- Verify the application keeps the same weighted review prompt across refreshes and that its Discord review thread contains every configured question, answer, and showcase link.
- Proceed with interview and verify one private ticket, one opening status, one ticket database row, and one applicant DM.
- From the interview state, choose **Do another interview** and verify a second ticket and DM are created without reusing the first run.
- Send a staff DM from an active and a completed application; verify delivery is queued and neither application status changes.
- Accept a test application and confirm `accepted_pending_role` changes only after the outbox delivers the Reviewer role.
- Add a test team member by Discord ID, verify the profile appears before first web sign-in, then remove it and verify managed roles are removed.
- Hide a test level and verify it disappears from public and normal internal data, then restore it to its previous state.
- Assign a task to another user and verify the assignment DM is delivered once.
- Promote/demote a test staff member and inspect the outbox and audit event.
- Search `/levels` by exact ID, name, and creator; inspect the response for absence of private fields.
- Check queue, applications, tasks, detail drawer, and statistics at 390 px width.
- Deactivate a test Reviewer, wait for role removal, and verify the inactive record remains available for Restore.
- Run `./scripts/quality_check.sh` in the bot repository.

Release verification must include Python compilation, targeted Ruff checks, the full Python and website test suites, configuration validation, JavaScript syntax checks, and desktop/mobile browser inspection. Dependency auditing should also run in CI or the deployment environment.

## Recovery And Rollback

The portal can be disabled immediately by setting `staff_portal.enabled` to `false` in runtime configuration or removing `STAFF_API_TOKEN`. This leaves all Discord workflows and `/pps` intact. The schema is additive; disabling the portal does not require dropping tables. Netlify can independently roll back the site deployment without changing bot state.
