# Staff Portal Command Parity

This inventory describes which privileged Avenue Guard operations are available in Discord and on the Staff Portal. The portal does not execute slash-command text. It calls `StaffPortalService`, which delegates to the same request, PPS, tracking, outbox, operations, and cache services used by the bot.

Status meanings:

- **Available**: implemented as a capability-gated web action.
- **Read only**: health/state is visible, while mutation remains in Discord.
- **Recovery only**: intentionally retained as a Discord fallback.
- **Not exposed**: withheld because the current command has no sufficiently narrow shared service or handles sensitive files/data.

| Discord command or family | Shared backend path | Web action | Minimum capability | Status |
|---|---|---|---|---|
| `/open-requests` | `RequestLevelsCog._open_requests_now` | Admin / Requests / Open | `requests.manage` | Available |
| `/close-requests` | `RequestLevelsCog._set_state_closed` | Admin / Requests / Close | `requests.manage` | Available |
| `/pending-openings` create | scheduled-opening table + bot scheduler | Admin / Requests / Schedule | `requests.schedule` | Available |
| `/pending-openings` list/edit/cancel | scheduled-opening table + request validation helpers | Admin / Requests / Scheduled openings | `requests.schedule` | Available |
| `/pending-openings` open-now | request scheduler interaction workflow | None | `requests.schedule` | Recovery only until extracted into a context-free service |
| `/refresh-request-button` | `RequestLevelsCog.refresh_or_create_request_button` | Admin / Requests / Refresh button | `requests.manage` | Available |
| `/requests repair` | `RequestLevelsCog.repair_request_system` | Admin / Requests / Repair | `requests.manage` | Available |
| `/requests pending/history/analytics` | request tables and audit rows | Wave progress and pending count | `requests.manage` | Partial; detailed Discord reports retained |
| `/pps dashboard` | `PrioritySystemService.dashboard` | Admin / PPS | `pps.manage_cycles` | Available |
| `/pps cycle start/complete/cancel` | `PrioritySystemService` | Admin / PPS / Manage cycle | `pps.manage_cycles` | Available |
| `/pps CP override` | `PrioritySystemService.manual_cp_override` | Admin / PPS / Override | `pps.override` | Available to Owner+ |
| `/pps queue`, filters, exact order | queue service queries | Work / Queue | `queue.view` | Available |
| `/pps outreach` | outreach episode/attempt service | Work / Outreach | `outreach.record` | Available |
| `/pps refresh` | PPS metadata service | None | `pps.manage_standard` | Recovery only pending a bounded refresh-job API |
| `/tracking disable_reward` | `TrackingCog.disable_weekly_reward_for_current_week` | Admin / Community / Tracking | `tracking.manage` | Available |
| `/tracking enable_reward` | `TrackingCog.enable_weekly_reward_for_current_week` | Admin / Community / Tracking | `tracking.manage` | Available |
| `/tracking force_dm` | `TrackingCog.force_dm_for_user` | None | `tracking.manage` | Not exposed; direct unsolicited DM stays in Discord |
| `/tracking reset` | `TrackingCog.reset_current_week` | None | `tracking.manage` | Recovery only due destructive data reset |
| `/ticket` operations | ticket tables and Help workflows | Ticket counts and health | `support.manage` | Read only; channel/transcript actions remain Discord-side |
| `/forum required_word` | persisted forum rule configuration | Rule status | `forum.manage` | Read only until the remote config writer is shared |
| `/server_icon status` | icon configuration | Mode/count/current index | `public_cache.manage` | Read only |
| `/server_icon` mutation | config writer + background rotation | None | `public_cache.manage` | Recovery only until config persistence is extracted |
| `/bot dashboard`, `/bot health`, `/bot doctor` | runtime, DB, worker, provider snapshots | Admin / Operations; Dev / System | `operations.view` | Available with sanitized diagnostics |
| `/bot resync` | multiple config/view/cache operations | targeted cache and task recovery actions | `developer.resync` | Partial; broad resync remains Discord recovery |
| `/bot restart` | process restart flow | None | `developer.restart` | Not exposed; second-confirmation web restart is not yet safely isolated |
| `/bot backup` | database backup delivery workflow | latest state only | `backups.manage` | Not exposed; backup files remain in the controlled Discord delivery path |
| `/bot restore-drill` | `OperationsCog.run_restore_drill` | Admin / System / Restore drill | `restore_drills.manage` | Available to Dev in v1 |
| `/bot retention` | `OperationsCog.run_retention` | status only | `retention.manage` | Recovery only for destructive cleanup |
| release approval commands | `ReleaseCog` | release/task status in diagnostics | `releases.manage` | Recovery only; approval DM remains authoritative |
| public level cache refresh | `PrioritySystemCog.refresh_public_level_cache` | Admin / System / Rebuild public cache | `developer.resync` | Available to Dev |
| background task repair | `OperationsCog.restart_stopped_tasks` | Admin / System / Restart stopped tasks | `developer.tasks` | Available to Dev |
| error incidents | `error_incidents` + ErrorReporter | summaries in Operations; sanitized trace in detail | `operations.view`, full trace `audit.view_full` | Available |

## Authorization Notes

- Browser navigation uses capability hints only. Avenue Guard rechecks the capability on every endpoint.
- Admin can run ordinary request, PPS, community, staff, and health operations.
- Owner can manage Admin access and use sensitive product operations.
- Dev can inspect engineering diagnostics and run bounded recovery actions.
- Dev membership itself is config-only and cannot be granted through either the web role form or an ordinary slash command.
- No web response includes secrets, database tokens, bot tokens, OAuth secrets, raw environment variables, or arbitrary SQL access.
