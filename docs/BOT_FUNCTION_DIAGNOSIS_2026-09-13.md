# Avenue Guard Function-by-Function Diagnosis

**Audit date:** 2026-09-13  
**Runtime scope:** 26 Python modules, 908 definitions, 22,864 physical lines
**Method:** AST inventory, per-function control-flow scoring, interaction-order review, persistence/Discord I/O mapping, compile, tests, Ruff, Bandit, and dependency audit

## Reading This Report

Every runtime function, method, nested callback, and modal handler has one row below. The attention label is a review priority, not proof of a defect: orchestration code and schema declarations are naturally larger. Complexity is a deterministic branch score used to find code that deserves focused tests.

- **Routine:** compact control flow with no static risk signal.
- **Focused review:** a long path, broad recovery, interaction timing, or several I/O boundaries.
- **High attention:** very large/branch-heavy orchestration or several silent recovery paths.

## Executive Diagnosis

- All runtime modules parse and compile.
- The complete automated suite passes: 122 tests.
- Ruff's correctness and bug checks pass.
- Bandit reports no medium or high security findings.
- The production dependency set has no known published vulnerabilities.
- Slash commands and support component handlers acknowledge interactions before slow work, except modal-first commands that must query the local replica or open the modal as their initial response.
- Turso-backed workflow state, request waves, tickets, tracking, summaries, runtime settings, and help submissions remain restart-persistent.

## Current Review Fixes

- Pinned the Render runtime to Python 3.13 and refreshed production dependency bounds.
- Replaced implicit event-loop lookup during startup with explicit loop ownership and cleanup.
- Restored the Discord presence intent required for accurate daily online-member summaries.
- Preserved every Discord snowflake exactly when using the legacy libSQL Python binding; large integers are never routed through floating point.
- Added compatible reads and in-place repair for historical IDs that Turso/libSQL rounded before this release.
- Reconciled message and channel pointers against live Discord evidence instead of trusting a rounded database value.
- Recovered closed-ticket recipients from exact guild membership, transcript embeds, and transcript attachments before declaring them unavailable.
- Made ticket satisfaction outcomes durable so deleted or inaccessible users are logged once instead of retried on every restart.
- Reserved weekly offers, reminders, and release approvals before sending Discord messages to prevent duplicates after uncertain writes.
- Removed a redundant remote pull after every embedded-replica write; startup and explicit resync remain authoritative pull points.
- Quarantined and rebuilt corrupt local replica files when Turso reports `file is not a database`.
- Kept scheduled openings and auto-closes transactional, restart-safe, and isolated from transient Turso failures.
- Suppressed expected Discord 404s for genuinely deleted saved messages while retaining actionable diagnostics.
- Made presence rotation tolerate a transport that is closing during reconnect or shutdown.
- Expanded migration, 64-bit ID, recovery, restart-idempotency, and notification-delivery regression coverage.

## Attention Summary

| Classification | Definitions |
|---|---:|
| Routine | 725 |
| Focused review | 151 |
| High attention | 32 |

## Function Inventory

### `cogs/Background.py`

86 definitions: 71 routine, 13 focused, 2 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 32 | `_day_key` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 36 | `_parse_hhmm` | internal helper | 10 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 47 | `_fmt_minutes` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 55 | `_fmt_num` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 58 | `_fmt_delta` | internal helper | 8 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 67 | `_fmt_percent` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 101 | `BackgroundCog.__init__` | internal helper | 13 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 115 | `BackgroundCog.cog_unload` | helper | 19 | 5 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 135 | `BackgroundCog.start_background` | helper | 60 | 22 | 10 | 0 | 0 | 7 broad / 0 silent | **Focused review**: 7 broad catches |
| 196 | `BackgroundCog.on_config_reload` | helper | 45 | 21 | 0 | 0 | 0 | 8 broad / 4 silent | **High attention**: 8 broad catches; 4 silent recovery paths |
| 245 | `BackgroundCog._excluded_channels` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 253 | `BackgroundCog._status_rotation_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 256 | `BackgroundCog._status_rotation_interval` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 259 | `BackgroundCog._status_list` | internal helper | 14 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 274 | `BackgroundCog._presence_transport_is_closing` | internal helper | 13 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 288 | `BackgroundCog._server_icon_rotation_enabled` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 292 | `BackgroundCog._server_icon_interval` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 296 | `BackgroundCog._server_icon_urls` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 301 | `BackgroundCog._database_backup_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 304 | `BackgroundCog._database_backup_interval_seconds` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 308 | `BackgroundCog._server_icon_current_index` | internal helper | 8 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 317 | `BackgroundCog._server_icon_candidate_indices` | internal helper | 14 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 332 | `BackgroundCog._looks_like_server_icon_image` | internal helper | 10 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 343 | `BackgroundCog._assert_public_server_icon_url` | internal helper | 26 | 12 | 1 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 370 | `BackgroundCog._download_server_icon` | internal helper | 40 | 18 | 2 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 411 | `BackgroundCog._detect_current_server_icon_index` | internal helper | 18 | 8 | 2 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 430 | `BackgroundCog._persist_server_icon_state` | internal helper | 6 | 2 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 437 | `BackgroundCog._remember_server_icon_error` | internal helper | 7 | 3 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 445 | `BackgroundCog._config_write_lock` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 453 | `BackgroundCog.rotate_server_icon_once` | helper | 15 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 469 | `BackgroundCog._rotate_server_icon_once_locked` | internal helper | 70 | 19 | 6 | 0 | 1 | 2 broad / 0 silent | **Focused review**: 1 Discord operation; 2 broad catches |
| 540 | `BackgroundCog._render_status_text` | internal helper | 55 | 14 | 5 | 3 | 0 | 3 broad / 0 silent | **Routine**: 3 persistence calls; 3 broad catches |
| 543 | `BackgroundCog._render_status_text._SafeDict.__missing__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 596 | `BackgroundCog._daily_summary_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 599 | `BackgroundCog._daily_summary_channel_id` | internal helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 605 | `BackgroundCog._daily_reset_after_report` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 608 | `BackgroundCog._daily_summary_due` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 615 | `BackgroundCog._daily_summary_already_sent` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 622 | `BackgroundCog._record_daily_summary_sent` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 628 | `BackgroundCog._voice_sessions_from_guild` | internal helper | 9 | 6 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 638 | `BackgroundCog._stats_payload` | internal helper | 23 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 662 | `BackgroundCog._stats_from_payload` | internal helper | 16 | 12 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 679 | `BackgroundCog._load_daily_stats` | internal helper | 11 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 691 | `BackgroundCog._persist_daily_stats` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 698 | `BackgroundCog._persist_current_day` | internal helper | 9 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 708 | `BackgroundCog._rollover_boundary_ts` | internal helper | 6 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 715 | `BackgroundCog._add_voice_until` | internal helper | 8 | 4 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 724 | `BackgroundCog._track_background_persist` | internal helper | 18 | 5 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 727 | `BackgroundCog._track_background_persist._done` | internal helper | 13 | 5 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 743 | `BackgroundCog._rollover_if_needed` | internal helper | 28 | 7 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 776 | `BackgroundCog.on_message` | event listener | 14 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 792 | `BackgroundCog.on_message_edit` | event listener | 11 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 805 | `BackgroundCog.on_message_delete` | event listener | 11 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 818 | `BackgroundCog.on_reaction_add` | event listener | 11 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 831 | `BackgroundCog.on_member_join` | event listener | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 840 | `BackgroundCog.on_member_remove` | event listener | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 849 | `BackgroundCog.on_member_ban` | event listener | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 858 | `BackgroundCog.on_member_unban` | event listener | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 867 | `BackgroundCog.on_member_update` | event listener | 10 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 879 | `BackgroundCog.on_voice_state_update` | event listener | 23 | 14 | 1 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 904 | `BackgroundCog.on_application_command_completion` | event listener | 14 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 920 | `BackgroundCog.on_application_command_error` | event listener | 15 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 940 | `BackgroundCog.update_snapshot` | background loop | 22 | 11 | 5 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |
| 963 | `BackgroundCog._log_snapshot_failure` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 967 | `BackgroundCog._before_snapshot` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 971 | `BackgroundCog._snapshot_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 975 | `BackgroundCog.database_backup` | background loop | 33 | 13 | 5 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 1010 | `BackgroundCog._before_database_backup` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1014 | `BackgroundCog._database_backup_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1018 | `BackgroundCog.rotate_status` | background loop | 40 | 11 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1060 | `BackgroundCog._before_rotate` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1064 | `BackgroundCog._rotate_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1068 | `BackgroundCog.rotate_server_icon` | background loop | 23 | 11 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1093 | `BackgroundCog._before_server_icon_rotate` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1097 | `BackgroundCog._server_icon_rotate_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1100 | `BackgroundCog._start_daily_report_loop` | internal helper | 14 | 5 | 0 | 0 | 0 | 2 broad / 2 silent | **Focused review**: 2 broad catches; 2 silent recovery paths |
| 1115 | `BackgroundCog._top_channel_lines` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1123 | `BackgroundCog._top_member_lines` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1131 | `BackgroundCog._top_command_lines` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1139 | `BackgroundCog._summary_color` | internal helper | 8 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1148 | `BackgroundCog._send_daily_summary_for_day` | internal helper | 7 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1156 | `BackgroundCog._send_daily_summary_for_day_locked` | internal helper | 173 | 33 | 11 | 0 | 3 | 7 broad / 3 silent | **High attention**: 3 Discord operations; split candidate; 7 broad catches; 3 silent recovery paths |
| 1331 | `BackgroundCog.daily_report` | background loop | 12 | 4 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1345 | `BackgroundCog._before_daily` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1349 | `BackgroundCog._daily_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1352 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Commands.py`

132 definitions: 95 routine, 30 focused, 7 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 75 | `_fmt_num` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 82 | `_fmt_percent` | internal helper | 8 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 92 | `_ticket_status_key` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 104 | `_ticket_status_label` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 109 | `AdminDashboardView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 115 | `AdminDashboardView._show` | internal helper | 17 | 5 | 7 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 134 | `AdminDashboardView.overview` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 138 | `AdminDashboardView.config` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 142 | `AdminDashboardView.repairs` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 146 | `AdminDashboardView.refresh` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 151 | `CommandsCog.__init__` | internal helper | 78 | 13 | 5 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 211 | `CommandsCog.__init__.resync` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 215 | `CommandsCog.__init__.restart` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 219 | `CommandsCog.__init__.dance` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 223 | `CommandsCog.__init__.rps` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 227 | `CommandsCog.__init__.gambling` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 230 | `CommandsCog._in_allowed_guild` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 233 | `CommandsCog._defer` | internal helper | 7 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 241 | `CommandsCog._send` | internal helper | 4 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 247 | `CommandsCog._claim_fun_cooldown` | internal helper | 28 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 276 | `CommandsCog._log_admin_action` | internal helper | 19 | 5 | 2 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 296 | `CommandsCog._impact_owner_ids` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 299 | `CommandsCog._is_impact_owner_ctx` | internal helper | 7 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 307 | `CommandsCog._is_release_owner_ctx` | internal helper | 8 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 316 | `CommandsCog._backup_channel_id` | internal helper | 7 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 324 | `CommandsCog._backup_local_dir` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 328 | `CommandsCog._restore_upload_dir` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 333 | `CommandsCog._backup_retention_count` | internal helper | 6 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 340 | `CommandsCog._prune_local_backups` | internal helper | 14 | 4 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 355 | `CommandsCog._database_path` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 358 | `CommandsCog._database_storage_note` | internal helper | 36 | 21 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 395 | `CommandsCog._zip_backup_file` | internal helper | 7 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 403 | `CommandsCog._post_database_backup` | internal helper | 75 | 13 | 11 | 2 | 3 | 2 broad / 1 silent | **Focused review**: 2 persistence calls; 3 Discord operations; 2 broad catches; 1 silent recovery path |
| 479 | `CommandsCog._restore_safe_filename` | internal helper | 4 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 484 | `CommandsCog._save_restore_attachment` | internal helper | 17 | 6 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 502 | `CommandsCog._extract_sqlite_restore_file` | internal helper | 26 | 12 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 529 | `CommandsCog._validate_restore_database` | internal helper | 23 | 7 | 1 | 4 | 0 | none | **Routine**: 4 persistence calls |
| 530 | `CommandsCog._validate_restore_database._run` | internal helper | 20 | 7 | 0 | 4 | 0 | none | **Routine**: 4 persistence calls |
| 553 | `CommandsCog._impact_scalar` | internal helper | 11 | 6 | 1 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 565 | `CommandsCog._impact_float` | internal helper | 11 | 6 | 1 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 577 | `CommandsCog._impact_group_counts` | internal helper | 43 | 5 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 621 | `CommandsCog._impact_daily_totals` | internal helper | 118 | 31 | 1 | 1 | 0 | 5 broad / 4 silent | **High attention**: 1 persistence call; split candidate; 5 broad catches; 4 silent recovery paths |
| 740 | `CommandsCog._impact_window_rows` | internal helper | 14 | 7 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 755 | `CommandsCog._impact_window_sum` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 759 | `CommandsCog._impact_window_average` | internal helper | 5 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 765 | `CommandsCog._impact_percent_change` | internal helper | 9 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 775 | `CommandsCog._impact_forecast` | internal helper | 61 | 17 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 837 | `CommandsCog._collect_impact_metrics` | internal helper | 325 | 39 | 47 | 3 | 0 | 1 broad / 0 silent | **High attention**: 3 persistence calls; split candidate; 1 broad catch |
| 1163 | `CommandsCog._impact_metric_rows` | internal helper | 46 | 19 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1166 | `CommandsCog._impact_metric_rows.add` | helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1210 | `CommandsCog._impact_csv` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1217 | `CommandsCog._impact_daily_csv` | internal helper | 23 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1241 | `CommandsCog._impact_breakdown_csv` | internal helper | 18 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1260 | `CommandsCog._impact_markdown` | internal helper | 97 | 4 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1358 | `CommandsCog._impact_report_embed` | internal helper | 62 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1421 | `CommandsCog._impact_files` | internal helper | 14 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1436 | `CommandsCog.bot_impact` | helper | 72 | 17 | 17 | 1 | 2 | 4 broad / 0 silent | **Focused review**: 1 persistence call; 2 Discord operations; 4 broad catches |
| 1509 | `CommandsCog.bot_release` | helper | 58 | 6 | 10 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1568 | `CommandsCog.bot_releases` | helper | 68 | 19 | 9 | 0 | 1 | 1 broad / 0 silent | **Focused review**: 1 Discord operation; 1 broad catch |
| 1637 | `CommandsCog.bot_backup` | helper | 18 | 5 | 10 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1656 | `CommandsCog.bot_restore` | helper | 104 | 24 | 19 | 2 | 1 | 4 broad / 0 silent | **Focused review**: 2 persistence calls; 1 Discord operation; split candidate; 4 broad catches |
| 1761 | `CommandsCog.bot_storage` | helper | 60 | 14 | 7 | 2 | 1 | none | **Routine**: 2 persistence calls; 1 Discord operation |
| 1822 | `CommandsCog._is_admin_ctx` | internal helper | 6 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1829 | `CommandsCog._is_mod_ctx` | internal helper | 7 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1837 | `CommandsCog._request_reviewer_role_ids` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1841 | `CommandsCog._is_request_staff_ctx` | internal helper | 13 | 7 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1855 | `CommandsCog._server_icon_status_embed` | internal helper | 47 | 20 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1903 | `CommandsCog._notify_background_config_reload` | internal helper | 8 | 3 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 1912 | `CommandsCog._server_icon_operation_lock` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1917 | `CommandsCog._config_write_lock` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1925 | `CommandsCog._save_server_icon_config` | internal helper | 21 | 5 | 5 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 1947 | `CommandsCog.server_icon_status` | helper | 7 | 3 | 5 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 1955 | `CommandsCog.server_icon_mode` | helper | 28 | 6 | 7 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 1984 | `CommandsCog.server_icon_add` | helper | 29 | 9 | 10 | 0 | 7 | none | **Routine**: 7 Discord operations |
| 2014 | `CommandsCog.server_icon_replace` | helper | 37 | 12 | 9 | 0 | 6 | none | **Routine**: 6 Discord operations |
| 2052 | `CommandsCog.server_icon_remove` | helper | 35 | 11 | 7 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 2088 | `CommandsCog.server_icon_set` | helper | 28 | 7 | 9 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2117 | `CommandsCog.server_icon_next` | helper | 14 | 5 | 8 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2132 | `CommandsCog._resolve_member` | internal helper | 15 | 5 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 2148 | `CommandsCog._task_state` | internal helper | 13 | 8 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2162 | `CommandsCog._count_db` | internal helper | 6 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 2169 | `CommandsCog._dashboard_issues` | internal helper | 105 | 38 | 1 | 0 | 0 | 1 broad / 0 silent | **High attention**: split candidate; 1 broad catch |
| 2275 | `CommandsCog._admin_dashboard_embed` | internal helper | 141 | 36 | 9 | 2 | 0 | 2 broad / 0 silent | **High attention**: 2 persistence calls; split candidate; 2 broad catches |
| 2417 | `CommandsCog.bot_dashboard` | helper | 8 | 3 | 6 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2428 | `CommandsCog.bot_health` | helper | 89 | 22 | 12 | 3 | 2 | 4 broad / 0 silent | **Focused review**: 3 persistence calls; 2 Discord operations; 4 broad catches |
| 2437 | `CommandsCog.bot_health._count` | internal helper | 6 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 2470 | `CommandsCog.bot_health._task_state` | internal helper | 13 | 8 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2518 | `CommandsCog.bot_doctor` | helper | 139 | 49 | 6 | 0 | 2 | none | **High attention**: 2 Discord operations; split candidate |
| 2533 | `CommandsCog.bot_doctor.channel_perm_report` | helper | 16 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2658 | `CommandsCog._template_variables` | internal helper | 12 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2671 | `CommandsCog._request_template_allowed_vars` | internal helper | 80 | 1 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 2752 | `CommandsCog._looks_like_color_value` | internal helper | 23 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2776 | `CommandsCog._validate_request_templates` | internal helper | 69 | 28 | 0 | 0 | 0 | none | **Focused review**: split candidate |
| 2797 | `CommandsCog._validate_request_templates.check_text` | helper | 8 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2806 | `CommandsCog._validate_request_templates.walk` | helper | 26 | 18 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 2846 | `CommandsCog.bot_config_check` | helper | 399 | 104 | 5 | 0 | 2 | 2 broad / 0 silent | **High attention**: 2 Discord operations; split candidate; 2 broad catches |
| 2857 | `CommandsCog.bot_config_check.check_channel` | helper | 13 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2871 | `CommandsCog.bot_config_check.check_role` | helper | 9 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2934 | `CommandsCog.bot_config_check.check_hhmm` | helper | 7 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2953 | `CommandsCog.bot_config_check.check_number` | helper | 22 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3246 | `CommandsCog._parse_snowflake_arg` | internal helper | 8 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 3255 | `CommandsCog._request_change_lines` | internal helper | 22 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3264 | `CommandsCog._request_change_lines.short` | helper | 5 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3278 | `CommandsCog.requests_history` | helper | 74 | 13 | 9 | 3 | 2 | 2 broad / 0 silent | **Focused review**: 3 persistence calls; 2 Discord operations; 2 broad catches |
| 3353 | `CommandsCog.requests_repair` | helper | 36 | 13 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3390 | `CommandsCog.requests_pending` | helper | 129 | 29 | 8 | 3 | 2 | 1 broad / 0 silent | **Focused review**: 3 persistence calls; 2 Discord operations; split candidate; 1 broad catch |
| 3474 | `CommandsCog.requests_pending.request_name` | helper | 8 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 3522 | `CommandsCog.tracking_top` | helper | 60 | 20 | 8 | 1 | 2 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 2 Discord operations; 1 broad catch; 1 silent recovery path |
| 3584 | `CommandsCog.tracking_me` | helper | 44 | 12 | 8 | 1 | 2 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 2 Discord operations; 1 broad catch; 1 silent recovery path |
| 3629 | `CommandsCog.tracking_force_dm` | helper | 22 | 5 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3652 | `CommandsCog.tracking_reset` | helper | 17 | 5 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3670 | `CommandsCog.tracking_disable_reward` | helper | 21 | 5 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3692 | `CommandsCog.tracking_enable_reward` | helper | 26 | 6 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3720 | `CommandsCog.ticket_close` | helper | 27 | 9 | 10 | 1 | 6 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 6 Discord operations; 1 broad catch; 1 silent recovery path |
| 3748 | `CommandsCog.ticket_status` | helper | 78 | 17 | 14 | 2 | 6 | 1 broad / 0 silent | **Focused review**: 2 persistence calls; 6 Discord operations; 1 broad catch |
| 3827 | `CommandsCog.ticket_transcripts` | helper | 65 | 19 | 7 | 1 | 4 | none | **Focused review**: 1 persistence call; 4 Discord operations |
| 3894 | `CommandsCog._parse_channel_id` | internal helper | 10 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 3905 | `CommandsCog._configured_forum_entries` | internal helper | 29 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3935 | `CommandsCog._resolve_forum_entry` | internal helper | 41 | 15 | 0 | 0 | 0 | 4 broad / 3 silent | **Focused review**: 4 broad catches; 3 silent recovery paths |
| 3977 | `CommandsCog.forum_required_word` | helper | 118 | 31 | 16 | 0 | 9 | 3 broad / 0 silent | **High attention**: 9 Discord operations; split candidate; 3 broad catches |
| 4097 | `CommandsCog._resync` | internal helper | 49 | 11 | 13 | 1 | 3 | 4 broad / 0 silent | **Focused review**: 1 persistence call; 3 Discord operations; 4 broad catches |
| 4148 | `CommandsCog._restart` | internal helper | 16 | 4 | 7 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 4166 | `CommandsCog._dance` | internal helper | 7 | 3 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 4175 | `CommandsCog._rps` | internal helper | 97 | 23 | 15 | 0 | 8 | 3 broad / 1 silent | **Focused review**: 8 Discord operations; 3 broad catches; 1 silent recovery path |
| 4188 | `CommandsCog._rps.outcome` | helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 4195 | `CommandsCog._rps.RPSView.__init__` | internal helper | 12 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 4208 | `CommandsCog._rps.RPSView._make_callback` | internal helper | 62 | 18 | 12 | 0 | 5 | 3 broad / 1 silent | **Focused review**: 5 Discord operations; 3 broad catches; 1 silent recovery path |
| 4209 | `CommandsCog._rps.RPSView._make_callback._cb` | internal helper | 60 | 18 | 12 | 0 | 5 | 3 broad / 1 silent | **Focused review**: 5 Discord operations; 3 broad catches; 1 silent recovery path |
| 4273 | `CommandsCog._rps_get_streak` | internal helper | 8 | 2 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 4282 | `CommandsCog._rps_update_streak` | internal helper | 28 | 4 | 4 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 4312 | `CommandsCog._gambling` | internal helper | 58 | 21 | 9 | 0 | 6 | 3 broad / 2 silent | **Focused review**: 6 Discord operations; 3 broad catches; 2 silent recovery paths |
| 4371 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Help.py`

177 definitions: 146 routine, 25 focused, 6 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 32 | `_format_duration` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 57 | `DMRecipientUnavailable.__init__` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 62 | `_ticket_status_key` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 74 | `_ticket_status_label` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 79 | `HelpSessionControlView.__init__` | internal helper | 21 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 101 | `HelpSessionControlView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 108 | `HelpSessionControlView.start` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 114 | `HelpSessionControlView.back` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 120 | `HelpSessionControlView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 126 | `HelpSessionControlView.start_over` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 133 | `HelpSubmissionPreviewView.__init__` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 140 | `HelpSubmissionPreviewView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 147 | `HelpSubmissionPreviewView.submit` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 153 | `HelpSubmissionPreviewView.edit` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 159 | `HelpSubmissionPreviewView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 165 | `HelpSubmissionPreviewView.start_over` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 172 | `HelpTicketTopicView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 178 | `HelpTicketTopicView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 184 | `HelpTicketTopicView._make_topic_callback` | internal helper | 6 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 185 | `HelpTicketTopicView._make_topic_callback._callback` | internal helper | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 192 | `HelpTicketTopicView.moderation` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 196 | `HelpTicketTopicView.requests` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 200 | `HelpTicketTopicView.server` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 204 | `HelpTicketTopicView.other` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 208 | `HelpTicketTopicView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 214 | `HelpTicketTopicView.back` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 220 | `HelpTicketTopicView.start_over` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 227 | `FaqPageView.__init__` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 240 | `FaqPageView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 247 | `FaqPageView.previous` | UI callback | 7 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 256 | `FaqPageView.next` | UI callback | 7 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 265 | `FaqPageView.back` | UI callback | 3 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 271 | `PartnershipConfirmView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 277 | `PartnershipConfirmView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 284 | `PartnershipConfirmView.confirm` | UI callback | 3 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 289 | `PartnershipConfirmView.cancel` | UI callback | 3 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 295 | `BanInfoModal.__init__` | internal helper | 64 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 360 | `BanInfoModal.callback` | helper | 13 | 6 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 376 | `BanInfoConfirmView.__init__` | internal helper | 14 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 391 | `BanInfoConfirmView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 398 | `BanInfoConfirmView.confirm` | UI callback | 8 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 408 | `BanInfoConfirmView.cancel` | UI callback | 6 | 3 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 417 | `TicketSatisfactionView.__init__` | internal helper | 18 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 436 | `TicketSatisfactionView._make_callback` | internal helper | 6 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 437 | `TicketSatisfactionView._make_callback._callback` | internal helper | 4 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 447 | `HelpCog.__init__` | internal helper | 21 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 469 | `HelpCog.cog_unload` | helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 473 | `HelpCog.start_background` | helper | 19 | 7 | 6 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |
| 493 | `HelpCog.on_config_reload` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 496 | `HelpCog._member_from_actor` | internal helper | 12 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 509 | `HelpCog._resolve_member` | internal helper | 16 | 5 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 526 | `HelpCog._resolve_dm_recipient` | internal helper | 52 | 21 | 2 | 0 | 2 | 1 broad / 0 silent | **Focused review**: 2 Discord operations; 1 broad catch |
| 579 | `HelpCog._help_color` | internal helper | 11 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 591 | `HelpCog._help_embed` | internal helper | 10 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 602 | `HelpCog._delete_interaction_source` | internal helper | 13 | 4 | 2 | 0 | 1 | 2 broad / 2 silent | **Focused review**: 1 Discord operation; 2 broad catches; 2 silent recovery paths |
| 616 | `HelpCog._ack_and_delete_source` | internal helper | 5 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 622 | `HelpCog._respond_interaction` | internal helper | 4 | 2 | 2 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 627 | `HelpCog._cooldowns` | internal helper | 7 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 635 | `HelpCog._submission_label` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 642 | `HelpCog._submission_prefix` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 649 | `HelpCog._submission_code` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 652 | `HelpCog._attachment_data` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 663 | `HelpCog._merge_attachments` | internal helper | 11 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 675 | `HelpCog._attachments_text` | internal helper | 13 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 689 | `HelpCog._has_attachments` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 693 | `HelpCog._short_text` | internal helper | 5 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 699 | `HelpCog._embed_char_count` | internal helper | 7 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 707 | `HelpCog._add_bounded_field` | internal helper | 15 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 723 | `HelpCog._normalize_duplicate_text` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 729 | `HelpCog._ticket_scan_loop` | internal helper | 9 | 4 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 739 | `HelpCog._log_background_error` | internal helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 746 | `HelpCog._load_active_ticket_channels` | internal helper | 28 | 5 | 3 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 775 | `HelpCog._ticket_channel_from_stored_id` | internal helper | 27 | 4 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 803 | `HelpCog._reconcile_missing_ticket_channels` | internal helper | 33 | 9 | 4 | 2 | 0 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 broad catch |
| 837 | `HelpCog._scan_tickets` | internal helper | 103 | 23 | 13 | 5 | 3 | 5 broad / 3 silent | **Focused review**: 5 persistence calls; 3 Discord operations; split candidate; 5 broad catches; 3 silent recovery paths |
| 945 | `HelpCog.on_message` | event listener | 92 | 27 | 15 | 2 | 1 | 3 broad / 0 silent | **Focused review**: 2 persistence calls; 1 Discord operation; split candidate; 3 broad catches |
| 1041 | `HelpCog._remaining_help_cooldown` | internal helper | 9 | 2 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1051 | `HelpCog._touch_help_cooldown` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1058 | `HelpCog._cooldown_until` | internal helper | 9 | 3 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1068 | `HelpCog._cooldown_embed` | internal helper | 8 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1077 | `HelpCog._flow_start_limit_message` | internal helper | 58 | 17 | 0 | 0 | 0 | 2 broad / 0 silent | **Focused review**: 2 broad catches |
| 1136 | `HelpCog._weekly_status_text` | internal helper | 20 | 9 | 3 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1157 | `HelpCog._request_result_label` | internal helper | 11 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1169 | `HelpCog._request_state_text` | internal helper | 33 | 14 | 2 | 2 | 0 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 broad catch |
| 1203 | `HelpCog._active_ticket_text` | internal helper | 12 | 4 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1216 | `HelpCog._recent_help_status_text` | internal helper | 36 | 9 | 2 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1253 | `HelpCog._cooldown_status_text` | internal helper | 7 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1261 | `HelpCog._send_dm_dashboard` | internal helper | 21 | 5 | 7 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1283 | `HelpCog._send_former_member_dashboard` | internal helper | 8 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1292 | `HelpCog._home_menu_view` | internal helper | 5 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1298 | `HelpCog._faq_entries` | internal helper | 4 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1303 | `HelpCog._send_ticket_topics` | internal helper | 7 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1314 | `HelpCog.handle_help_selection` | workflow handler | 194 | 32 | 37 | 0 | 14 | none | **High attention**: 14 Discord operations; split candidate |
| 1509 | `HelpCog._send_faq` | internal helper | 8 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1518 | `HelpCog._faq_page_embed` | internal helper | 23 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1542 | `HelpCog._send_faq_page` | internal helper | 8 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1551 | `HelpCog.handle_faq_page` | workflow handler | 18 | 3 | 4 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1570 | `HelpCog._send_weekly_status` | internal helper | 41 | 16 | 7 | 2 | 2 | none | **Focused review**: 2 persistence calls; 2 Discord operations |
| 1615 | `HelpCog._help_session_cache_store` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1622 | `HelpCog._help_session_lock` | internal helper | 11 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1634 | `HelpCog._help_session_tombstone_store` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1641 | `HelpCog._claimed_dm_message_store` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1648 | `HelpCog._claim_dm_message` | internal helper | 14 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1663 | `HelpCog.should_yield_weekly_dm` | helper | 15 | 6 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1679 | `HelpCog._prune_help_session_memory` | internal helper | 25 | 14 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1705 | `HelpCog._help_session_lifetime` | internal helper | 8 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1714 | `HelpCog._log_help_session_storage_error` | internal helper | 8 | 2 | 1 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 1723 | `HelpCog._start_help_session` | internal helper | 20 | 2 | 2 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1744 | `HelpCog._clear_help_session` | internal helper | 14 | 2 | 2 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1759 | `HelpCog._get_help_session` | internal helper | 44 | 15 | 4 | 0 | 0 | 2 broad / 0 silent | **Focused review**: 2 broad catches |
| 1804 | `HelpCog.has_active_help_session` | helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1807 | `HelpCog._help_stage_prompt_embed` | internal helper | 49 | 10 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1857 | `HelpCog._preview_stage` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1860 | `HelpCog._edit_stage_for_kind` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1867 | `HelpCog._fresh_edit_data` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1875 | `HelpCog._submission_core_text` | internal helper | 8 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1884 | `HelpCog._submission_preview_embed` | internal helper | 19 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1904 | `HelpCog._show_submission_preview` | internal helper | 7 | 1 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1912 | `HelpCog._is_duplicate_help_submission` | internal helper | 36 | 9 | 1 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 1949 | `HelpCog._submission_log_channel` | internal helper | 16 | 7 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1966 | `HelpCog._submission_staff_embed` | internal helper | 28 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1995 | `HelpCog._insert_help_submission` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2002 | `HelpCog._submit_help_submission` | internal helper | 40 | 8 | 11 | 2 | 2 | 2 broad / 1 silent | **Focused review**: 2 persistence calls; 2 Discord operations; 2 broad catches; 1 silent recovery path |
| 2043 | `HelpCog._help_max_submission_chars` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2049 | `HelpCog._handle_help_session_message` | internal helper | 4 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2054 | `HelpCog._handle_help_session_message_locked` | internal helper | 181 | 27 | 28 | 3 | 10 | none | **High attention**: 3 persistence calls; 10 Discord operations; split candidate |
| 2236 | `HelpCog._handle_typed_back` | internal helper | 49 | 8 | 10 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 2286 | `HelpCog._edit_prompt_embed` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2299 | `HelpCog.handle_help_session_control` | workflow handler | 8 | 2 | 3 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 2308 | `HelpCog._handle_help_session_control_locked` | internal helper | 89 | 14 | 18 | 0 | 5 | none | **Focused review**: 5 Discord operations |
| 2398 | `HelpCog.handle_help_submission_preview` | workflow handler | 6 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2405 | `HelpCog._handle_help_submission_preview_locked` | internal helper | 59 | 12 | 18 | 0 | 6 | none | **Routine**: 6 Discord operations |
| 2465 | `HelpCog._parse_ticket_reference` | internal helper | 14 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2480 | `HelpCog._staff_log_embed` | internal helper | 14 | 3 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 2495 | `HelpCog._log_help_action` | internal helper | 27 | 9 | 3 | 0 | 2 | 2 broad / 0 silent | **Routine**: 2 Discord operations; 2 broad catches |
| 2523 | `HelpCog._ticket_label` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2526 | `HelpCog._requester_id_from_help_log_message` | internal helper | 26 | 14 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2553 | `HelpCog._is_ticket_transcript_message` | internal helper | 21 | 14 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2575 | `HelpCog._reconcile_requester_id` | internal helper | 17 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2593 | `HelpCog._requester_id_from_message_content` | internal helper | 4 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2598 | `HelpCog._handle_staff_help_reply` | internal helper | 9 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2608 | `HelpCog._handle_staff_help_reply_locked` | internal helper | 133 | 37 | 18 | 3 | 4 | 7 broad / 5 silent | **High attention**: 3 persistence calls; 4 Discord operations; split candidate; 7 broad catches; 5 silent recovery paths |
| 2745 | `HelpCog._submit_appeal` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2749 | `HelpCog._submit_report` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2753 | `HelpCog._submit_bot_issue` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2760 | `HelpCog._ban_info_code` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2763 | `HelpCog._known_user_history` | internal helper | 38 | 12 | 3 | 1 | 1 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 2802 | `HelpCog._ban_info_staff_embed` | internal helper | 43 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2846 | `HelpCog._create_ban_info_request` | internal helper | 89 | 8 | 16 | 5 | 6 | 2 broad / 1 silent | **Focused review**: 5 persistence calls; 6 Discord operations; 2 broad catches; 1 silent recovery path |
| 2936 | `HelpCog._can_handle_ban_info` | internal helper | 11 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2948 | `HelpCog.handle_ban_info_button` | workflow handler | 16 | 6 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 2965 | `HelpCog.handle_ban_info_modal` | workflow handler | 115 | 18 | 11 | 2 | 4 | 1 broad / 0 silent | **Focused review**: 2 persistence calls; 4 Discord operations; split candidate; 1 broad catch |
| 3081 | `HelpCog._ban_info_delivery_embed` | internal helper | 41 | 13 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3123 | `HelpCog._update_ban_info_staff_message` | internal helper | 75 | 20 | 6 | 1 | 1 | 2 broad / 0 silent | **Focused review**: 1 persistence call; 1 Discord operation; 2 broad catches |
| 3199 | `HelpCog.finalize_ban_info` | helper | 106 | 15 | 17 | 3 | 6 | 1 broad / 0 silent | **Focused review**: 3 persistence calls; 6 Discord operations; split candidate; 1 broad catch |
| 3309 | `HelpCog._create_transcript_request` | internal helper | 8 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3318 | `HelpCog._create_transcript_request_locked` | internal helper | 76 | 13 | 6 | 3 | 3 | 3 broad / 1 silent | **Focused review**: 3 persistence calls; 3 Discord operations; 3 broad catches; 1 silent recovery path |
| 3395 | `HelpCog.handle_transcript_request_decision` | workflow handler | 5 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3401 | `HelpCog._handle_transcript_request_decision_locked` | internal helper | 200 | 25 | 32 | 7 | 5 | 5 broad / 0 silent | **High attention**: 7 persistence calls; 5 Discord operations; split candidate; 5 broad catches |
| 3602 | `HelpCog._dm_transcript` | internal helper | 124 | 20 | 16 | 2 | 2 | 7 broad / 0 silent | **Focused review**: 2 persistence calls; 2 Discord operations; split candidate; 7 broad catches |
| 3730 | `HelpCog.handle_ticket_topic` | workflow handler | 10 | 4 | 5 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 3741 | `HelpCog.handle_partnership_confirmation` | workflow handler | 25 | 3 | 4 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 3767 | `HelpCog._recover_ticket_creator_id` | internal helper | 68 | 23 | 3 | 1 | 0 | 1 broad / 0 silent | **Focused review**: 1 persistence call; 1 broad catch |
| 3836 | `HelpCog._recover_closed_ticket_creator_id` | internal helper | 99 | 22 | 6 | 2 | 0 | 1 broad / 0 silent | **Focused review**: 2 persistence calls; 1 broad catch |
| 3936 | `HelpCog.update_ticket_opening_status` | helper | 97 | 25 | 12 | 3 | 4 | 5 broad / 2 silent | **Focused review**: 3 persistence calls; 4 Discord operations; split candidate; 5 broad catches; 2 silent recovery paths |
| 4034 | `HelpCog._create_staff_ticket` | internal helper | 19 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 4054 | `HelpCog._create_staff_ticket_locked` | internal helper | 164 | 30 | 26 | 4 | 4 | 6 broad / 0 silent | **High attention**: 4 persistence calls; 4 Discord operations; split candidate; 6 broad catches |
| 4219 | `HelpCog._next_ticket_id` | internal helper | 2 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 4222 | `HelpCog.handle_ticket_close_prompt` | workflow handler | 61 | 15 | 12 | 2 | 3 | 2 broad / 2 silent | **Focused review**: 2 persistence calls; 3 Discord operations; 2 broad catches; 2 silent recovery paths |
| 4284 | `HelpCog._send_ticket_satisfaction_prompt` | internal helper | 143 | 24 | 16 | 8 | 3 | 4 broad / 2 silent | **Focused review**: 8 persistence calls; 3 Discord operations; split candidate; 4 broad catches; 2 silent recovery paths |
| 4428 | `HelpCog._restore_ticket_satisfaction_views` | internal helper | 71 | 8 | 6 | 3 | 0 | 2 broad / 0 silent | **Focused review**: 3 persistence calls; 2 broad catches |
| 4500 | `HelpCog.handle_ticket_satisfaction` | workflow handler | 57 | 10 | 9 | 2 | 6 | 2 broad / 0 silent | **Routine**: 2 persistence calls; 6 Discord operations; 2 broad catches |
| 4558 | `HelpCog.close_ticket_channel` | helper | 15 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 4574 | `HelpCog._close_ticket_channel_locked` | internal helper | 153 | 38 | 33 | 6 | 8 | 14 broad / 4 silent | **High attention**: 6 persistence calls; 8 Discord operations; split candidate; 14 broad catches; 4 silent recovery paths |
| 4610 | `HelpCog._close_ticket_channel_locked._restore_open_status` | internal helper | 11 | 2 | 3 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 4667 | `HelpCog._close_ticket_channel_locked._cleanup_transcript_artifact` | internal helper | 21 | 5 | 4 | 1 | 1 | 2 broad / 1 silent | **Focused review**: 1 persistence call; 1 Discord operation; 2 broad catches; 1 silent recovery path |
| 4729 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/MessageResponses.py`

9 definitions: 7 routine, 1 focused, 1 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 17 | `MessageResponsesCog.__init__` | internal helper | 7 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 25 | `MessageResponsesCog.load_rules` | helper | 20 | 8 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 46 | `MessageResponsesCog.on_config_reload` | helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 50 | `MessageResponsesCog._max_response_chars` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 56 | `MessageResponsesCog._log_rule_error` | internal helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 63 | `MessageResponsesCog.validate_rules` | helper | 24 | 18 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 88 | `MessageResponsesCog._cooldown_ok` | internal helper | 18 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 108 | `MessageResponsesCog.on_message` | event listener | 90 | 41 | 7 | 0 | 2 | 2 broad / 1 silent | **High attention**: 2 Discord operations; split candidate; 2 broad catches; 1 silent recovery path |
| 200 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Mod.py`

11 definitions: 7 routine, 4 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 14 | `_review_access_text` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 18 | `_within_one_edit` | internal helper | 22 | 11 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 43 | `ModCog.__init__` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 49 | `ModCog.on_message` | event listener | 48 | 14 | 5 | 0 | 2 | 2 broad / 0 silent | **Routine**: 2 Discord operations; 2 broad catches |
| 98 | `ModCog._handle_review_access_message` | internal helper | 84 | 21 | 13 | 0 | 7 | 7 broad / 3 silent | **Focused review**: 7 Discord operations; 7 broad catches; 3 silent recovery paths |
| 183 | `ModCog._dm_templates_for_role` | internal helper | 24 | 11 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 208 | `ModCog._send_role_dm` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 212 | `ModCog._send_role_dm_locked` | internal helper | 31 | 8 | 2 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 245 | `ModCog.on_raw_reaction_add` | event listener | 52 | 15 | 5 | 0 | 2 | 2 broad / 0 silent | **Focused review**: 2 Discord operations; 2 broad catches |
| 299 | `ModCog.on_member_update` | event listener | 50 | 18 | 3 | 0 | 1 | 2 broad / 1 silent | **Focused review**: 1 Discord operation; 2 broad catches; 1 silent recovery path |
| 350 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Release.py`

34 definitions: 27 routine, 6 focused, 1 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 39 | `_row_value` | internal helper | 6 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 47 | `_field_chunks` | internal helper | 13 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 65 | `ReleaseCog.__init__` | internal helper | 10 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 76 | `ReleaseCog.cog_unload` | helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 80 | `ReleaseCog._enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 83 | `ReleaseCog.owner_ids` | helper | 9 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 93 | `ReleaseCog.is_owner` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 96 | `ReleaseCog._manifest_path` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 104 | `ReleaseCog._public_release_limit` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 112 | `ReleaseCog.website_url` | helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 119 | `ReleaseCog._version_floor` | internal helper | 10 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 130 | `ReleaseCog._latest_recorded_version` | internal helper | 14 | 5 | 1 | 1 | 0 | 0 broad / 1 silent | **Focused review**: 1 persistence call; 1 silent recovery path |
| 145 | `ReleaseCog._latest_approved_version` | internal helper | 11 | 4 | 1 | 1 | 0 | 0 broad / 1 silent | **Focused review**: 1 persistence call; 1 silent recovery path |
| 157 | `ReleaseCog._resolve_owner` | internal helper | 16 | 6 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 174 | `ReleaseCog._approval_embed` | internal helper | 59 | 11 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 234 | `ReleaseCog._proposal_id_from_interaction` | internal helper | 10 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 245 | `ReleaseCog._send_approval_dm` | internal helper | 75 | 9 | 9 | 4 | 1 | 3 broad / 0 silent | **Focused review**: 4 persistence calls; 1 Discord operation; 3 broad catches |
| 321 | `ReleaseCog.propose_release` | helper | 19 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 341 | `ReleaseCog._propose_release_locked` | internal helper | 75 | 13 | 6 | 3 | 0 | none | **Focused review**: 3 persistence calls |
| 417 | `ReleaseCog._ensure_manifest_proposal` | internal helper | 36 | 12 | 5 | 1 | 0 | none | **Routine**: 1 persistence call |
| 454 | `ReleaseCog.refresh_public_release_cache` | helper | 8 | 2 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 464 | `ReleaseCog._uptime_snapshot_from_row` | internal helper | 30 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 495 | `ReleaseCog._initialize_uptime_tracker` | internal helper | 29 | 5 | 3 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 525 | `ReleaseCog.record_uptime_sample` | helper | 41 | 6 | 4 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 567 | `ReleaseCog.record_uptime_transition` | helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 570 | `ReleaseCog.uptime_snapshot` | helper | 16 | 1 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 587 | `ReleaseCog._allowed_guild_metrics` | internal helper | 62 | 22 | 2 | 0 | 0 | 1 broad / 0 silent | **Focused review**: 1 broad catch |
| 650 | `ReleaseCog.refresh_public_metrics` | helper | 35 | 9 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 686 | `ReleaseCog._metrics_loop` | internal helper | 13 | 5 | 4 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 700 | `ReleaseCog.start_background` | helper | 24 | 6 | 8 | 0 | 0 | 4 broad / 0 silent | **Focused review**: 4 broad catches |
| 725 | `ReleaseCog.release_overview` | helper | 25 | 5 | 2 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 751 | `ReleaseCog.handle_release_decision` | workflow handler | 19 | 2 | 3 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 771 | `ReleaseCog._handle_release_decision_locked` | internal helper | 164 | 25 | 21 | 6 | 9 | 3 broad / 0 silent | **High attention**: 6 persistence calls; 9 Discord operations; split candidate; 3 broad catches |
| 937 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/RequestLevels.py`

159 definitions: 135 routine, 16 focused, 8 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 90 | `_SafeDict.__missing__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 95 | `LevelRequestModal.__init__` | internal helper | 39 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 135 | `LevelRequestModal.callback` | helper | 15 | 8 | 3 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 153 | `ReviewModal.__init__` | internal helper | 13 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 167 | `ReviewModal.callback` | helper | 7 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 177 | `FirstRequestChoiceView.__init__` | internal helper | 8 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 186 | `FirstRequestChoiceView._will` | internal helper | 4 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 193 | `OtherReasonView.__init__` | internal helper | 9 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 203 | `OtherReasonView._make_callback` | internal helper | 4 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 204 | `OtherReasonView._make_callback._callback` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 210 | `ScheduledOpeningEditModal.__init__` | internal helper | 47 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 258 | `ScheduledOpeningEditModal.callback` | helper | 12 | 7 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 273 | `ScheduledOpeningsView.__init__` | internal helper | 33 | 10 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 307 | `ScheduledOpeningsView._allowed` | internal helper | 12 | 5 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 320 | `ScheduledOpeningsView._select` | internal helper | 8 | 3 | 2 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 329 | `ScheduledOpeningsView._refresh` | internal helper | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 335 | `ScheduledOpeningsView._edit` | internal helper | 15 | 6 | 4 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 351 | `ScheduledOpeningsView._delete` | internal helper | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 357 | `ScheduledOpeningsView._open_now` | internal helper | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 365 | `ScheduledOpenNowConfirmView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 371 | `ScheduledOpenNowConfirmView._allowed` | internal helper | 12 | 5 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 385 | `ScheduledOpenNowConfirmView.confirm` | UI callback | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 392 | `ScheduledOpenNowConfirmView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 399 | `RequestLevelsCog.__init__` | internal helper | 158 | 3 | 6 | 0 | 0 | none | **High attention**: split candidate |
| 423 | `RequestLevelsCog.__init__.refresh_request_button` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 427 | `RequestLevelsCog.__init__.open_requests` | slash command | 49 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 478 | `RequestLevelsCog.__init__.close_requests` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 482 | `RequestLevelsCog.__init__.requests_are` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 486 | `RequestLevelsCog.__init__.edit_request` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 490 | `RequestLevelsCog.__init__.pending_openings` | slash command | 67 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 558 | `RequestLevelsCog.cog_unload` | helper | 14 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 573 | `RequestLevelsCog.close_resources` | helper | 15 | 7 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 589 | `RequestLevelsCog.start_background` | helper | 11 | 8 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 601 | `RequestLevelsCog.on_config_reload` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 604 | `RequestLevelsCog._start_background_task` | internal helper | 13 | 3 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 605 | `RequestLevelsCog._start_background_task.runner` | helper | 7 | 3 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 618 | `RequestLevelsCog._cfg` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 621 | `RequestLevelsCog._cfg_int` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 624 | `RequestLevelsCog._cfg_int_list` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 627 | `RequestLevelsCog._reviewer_role_ids` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 631 | `RequestLevelsCog._post_close_edit_seconds` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 635 | `RequestLevelsCog._edit_deadline_ts_for_state` | internal helper | 19 | 7 | 0 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 655 | `RequestLevelsCog._edit_window_text` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 662 | `RequestLevelsCog._can_edit_submission` | internal helper | 32 | 14 | 0 | 0 | 0 | 3 broad / 1 silent | **Focused review**: 3 broad catches; 1 silent recovery path |
| 695 | `RequestLevelsCog._current_user_submission` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 701 | `RequestLevelsCog._current_user_submission_local` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 707 | `RequestLevelsCog._latest_editable_user_submission` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 714 | `RequestLevelsCog._editable_user_submission_for_modal` | internal helper | 20 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 735 | `RequestLevelsCog._state_after_timed_close_check` | internal helper | 12 | 6 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 748 | `RequestLevelsCog._request_initial_values` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 754 | `RequestLevelsCog._message` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 757 | `RequestLevelsCog._message_formatted` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 760 | `RequestLevelsCog._request_button_label` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 763 | `RequestLevelsCog._request_type_normalize_text` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 769 | `RequestLevelsCog._normalize_request_type` | internal helper | 9 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 779 | `RequestLevelsCog._request_type_label` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 785 | `RequestLevelsCog._request_type_help` | internal helper | 2 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 788 | `RequestLevelsCog._request_type_from_row` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 795 | `RequestLevelsCog._clean_open_message` | internal helper | 7 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 803 | `RequestLevelsCog._request_open_condition_text` | internal helper | 11 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 815 | `RequestLevelsCog._send_open_announcement` | internal helper | 55 | 23 | 4 | 0 | 1 | 3 broad / 0 silent | **Focused review**: 1 Discord operation; 3 broad catches |
| 871 | `RequestLevelsCog._color_name` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 874 | `RequestLevelsCog._format` | internal helper | 5 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 880 | `RequestLevelsCog._submitted_ago` | internal helper | 6 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 887 | `RequestLevelsCog._clean_level_id` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 890 | `RequestLevelsCog._normalize_level_id` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 893 | `RequestLevelsCog._valid_url` | internal helper | 12 | 7 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 906 | `RequestLevelsCog._validate_request_data` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 917 | `RequestLevelsCog._level_validation_cfg` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 921 | `RequestLevelsCog._level_validation_enabled` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 925 | `RequestLevelsCog._level_validation_cache_seconds` | internal helper | 5 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 931 | `RequestLevelsCog._level_validation_timeout_seconds` | internal helper | 5 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 937 | `RequestLevelsCog._level_validation_message` | internal helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 943 | `RequestLevelsCog._level_validation_providers` | internal helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 952 | `RequestLevelsCog._level_validation_rate_limit_message` | internal helper | 44 | 19 | 0 | 0 | 0 | 3 broad / 0 silent | **Focused review**: 3 broad catches |
| 997 | `RequestLevelsCog._provider_failure_cfg` | internal helper | 11 | 3 | 0 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 1009 | `RequestLevelsCog._provider_circuit_open` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1016 | `RequestLevelsCog._record_provider_validation_result` | internal helper | 9 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1026 | `RequestLevelsCog._provider_min_interval` | internal helper | 11 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1038 | `RequestLevelsCog._fetch_validation_provider` | internal helper | 20 | 5 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1059 | `RequestLevelsCog._get_level_validation_session` | internal helper | 17 | 7 | 1 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 1077 | `RequestLevelsCog._safe_json_loads` | internal helper | 5 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1083 | `RequestLevelsCog._cached_level_validation` | internal helper | 17 | 5 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1101 | `RequestLevelsCog._lookup_level_validation` | internal helper | 62 | 20 | 7 | 1 | 0 | 1 broad / 0 silent | **Focused review**: 1 persistence call; 1 broad catch |
| 1164 | `RequestLevelsCog._apply_level_validation_vars` | internal helper | 81 | 30 | 0 | 0 | 0 | 1 broad / 0 silent | **High attention**: split candidate; 1 broad catch |
| 1246 | `RequestLevelsCog._validate_level_external` | internal helper | 28 | 14 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1275 | `RequestLevelsCog._request_type_validation_error` | internal helper | 32 | 23 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1308 | `RequestLevelsCog._has_reviewer_role` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1312 | `RequestLevelsCog._embed_from_template` | internal helper | 50 | 21 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1363 | `RequestLevelsCog._reply_ephemeral` | internal helper | 5 | 2 | 2 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 1369 | `RequestLevelsCog._log_request_admin_action` | internal helper | 23 | 7 | 2 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1393 | `RequestLevelsCog._state_label` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1396 | `RequestLevelsCog._request_button_embed` | internal helper | 15 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1412 | `RequestLevelsCog._pct` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1417 | `RequestLevelsCog._wave_summary_vars` | internal helper | 47 | 20 | 2 | 1 | 0 | none | **Focused review**: 1 persistence call |
| 1465 | `RequestLevelsCog._reviewer_stats_lines` | internal helper | 21 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1487 | `RequestLevelsCog._wave_summary_embed` | internal helper | 32 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1520 | `RequestLevelsCog.update_wave_summary` | helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1524 | `RequestLevelsCog._update_wave_summary_unlocked` | internal helper | 79 | 17 | 15 | 4 | 6 | 6 broad / 3 silent | **Focused review**: 4 persistence calls; 6 Discord operations; 6 broad catches; 3 silent recovery paths |
| 1604 | `RequestLevelsCog._base_state_vars` | internal helper | 25 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1630 | `RequestLevelsCog._row_value` | internal helper | 7 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1638 | `RequestLevelsCog._duplicate_history_warning` | internal helper | 25 | 6 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1664 | `RequestLevelsCog._days_in_month` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1667 | `RequestLevelsCog._add_month` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1673 | `RequestLevelsCog._scheduled_local_time_exists` | internal helper | 15 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1689 | `RequestLevelsCog._parse_scheduled_open_ts` | internal helper | 41 | 16 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1731 | `RequestLevelsCog._scheduled_opening_rows` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1738 | `RequestLevelsCog.get_scheduled_opening` | helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1745 | `RequestLevelsCog._scheduled_openings_embed` | internal helper | 37 | 14 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1783 | `RequestLevelsCog.refresh_pending_openings_panel` | helper | 5 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1789 | `RequestLevelsCog.delete_scheduled_opening` | helper | 16 | 2 | 6 | 1 | 2 | none | **Routine**: 1 persistence call; 2 Discord operations |
| 1806 | `RequestLevelsCog.open_scheduled_opening_now` | helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1810 | `RequestLevelsCog._open_scheduled_opening_now_locked` | internal helper | 44 | 11 | 9 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 1855 | `RequestLevelsCog.handle_scheduled_opening_edit_modal` | workflow handler | 68 | 20 | 13 | 1 | 9 | 2 broad / 0 silent | **Focused review**: 1 persistence call; 9 Discord operations; 2 broad catches |
| 1879 | `RequestLevelsCog.handle_scheduled_opening_edit_modal.optional_positive` | helper | 11 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1924 | `RequestLevelsCog._data_vars` | internal helper | 60 | 35 | 0 | 0 | 0 | 1 broad / 0 silent | **High attention**: split candidate; 1 broad catch |
| 1985 | `RequestLevelsCog._weekly_data_vars` | internal helper | 27 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2013 | `RequestLevelsCog._result_label` | internal helper | 6 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2020 | `RequestLevelsCog._status_channel_id` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2025 | `RequestLevelsCog._result_template_key` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2032 | `RequestLevelsCog._get_state` | internal helper | 9 | 2 | 3 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 2042 | `RequestLevelsCog._get_state_local` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2048 | `RequestLevelsCog._set_state_closed` | internal helper | 36 | 9 | 6 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 2085 | `RequestLevelsCog._open_requests_now` | internal helper | 88 | 20 | 11 | 1 | 0 | 2 broad / 0 silent | **Focused review**: 1 persistence call; 2 broad catches |
| 2174 | `RequestLevelsCog._auto_close_loop` | internal helper | 17 | 9 | 5 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2192 | `RequestLevelsCog._scheduled_open_loop` | internal helper | 44 | 14 | 8 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 2237 | `RequestLevelsCog._in_allowed_guild` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2240 | `RequestLevelsCog._defer_command` | internal helper | 5 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2246 | `RequestLevelsCog._cached_interaction_member` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2253 | `RequestLevelsCog._resolve_member` | internal helper | 18 | 5 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 2272 | `RequestLevelsCog._is_admin` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2275 | `RequestLevelsCog._is_mod` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2283 | `RequestLevelsCog._configured_channel` | internal helper | 9 | 6 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 2293 | `RequestLevelsCog.refresh_or_create_request_button` | helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2297 | `RequestLevelsCog._refresh_or_create_request_button_unlocked` | internal helper | 70 | 19 | 14 | 3 | 6 | 6 broad / 3 silent | **Focused review**: 3 persistence calls; 6 Discord operations; 6 broad catches; 3 silent recovery paths |
| 2368 | `RequestLevelsCog.refresh_request_button` | helper | 13 | 5 | 8 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 2382 | `RequestLevelsCog.open_requests` | helper | 83 | 37 | 15 | 1 | 9 | none | **High attention**: 1 persistence call; 9 Discord operations; split candidate |
| 2466 | `RequestLevelsCog.pending_openings` | helper | 96 | 34 | 25 | 3 | 15 | none | **High attention**: 3 persistence calls; 15 Discord operations; split candidate |
| 2563 | `RequestLevelsCog.close_requests` | helper | 16 | 5 | 10 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 2580 | `RequestLevelsCog.requests_are` | helper | 15 | 7 | 4 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2596 | `RequestLevelsCog._requirements_ok` | internal helper | 8 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2605 | `RequestLevelsCog.handle_request_button` | workflow handler | 86 | 19 | 15 | 0 | 12 | none | **Focused review**: 12 Discord operations |
| 2692 | `RequestLevelsCog.edit_request` | helper | 18 | 3 | 4 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 2711 | `RequestLevelsCog.handle_first_choice` | workflow handler | 20 | 7 | 5 | 0 | 4 | 1 broad / 0 silent | **Routine**: 4 Discord operations; 1 broad catch |
| 2732 | `RequestLevelsCog.handle_request_form` | workflow handler | 212 | 33 | 40 | 7 | 3 | 8 broad / 1 silent | **High attention**: 7 persistence calls; 3 Discord operations; split candidate; 8 broad catches; 1 silent recovery path |
| 2945 | `RequestLevelsCog.handle_request_edit_form` | workflow handler | 267 | 44 | 41 | 5 | 5 | 8 broad / 3 silent | **High attention**: 5 persistence calls; 5 Discord operations; split candidate; 8 broad catches; 3 silent recovery paths |
| 3213 | `RequestLevelsCog._refresh_closed_wave` | internal helper | 7 | 3 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 3221 | `RequestLevelsCog._submission_by_message` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3227 | `RequestLevelsCog._weekly_submission_by_message` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3233 | `RequestLevelsCog._review_target_by_message` | internal helper | 8 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3242 | `RequestLevelsCog._review_target_by_message_local` | internal helper | 24 | 4 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3267 | `RequestLevelsCog._channel_by_id` | internal helper | 8 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3276 | `RequestLevelsCog._review_target_channel` | internal helper | 16 | 6 | 3 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3293 | `RequestLevelsCog.handle_review_button` | workflow handler | 16 | 8 | 7 | 0 | 6 | none | **Routine**: 6 Discord operations |
| 3310 | `RequestLevelsCog.handle_review_submission` | workflow handler | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3313 | `RequestLevelsCog.handle_other_reason` | workflow handler | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3316 | `RequestLevelsCog._finalize_review` | internal helper | 122 | 23 | 27 | 2 | 4 | 6 broad / 1 silent | **Focused review**: 2 persistence calls; 4 Discord operations; split candidate; 6 broad catches; 1 silent recovery path |
| 3439 | `RequestLevelsCog.repair_request_system` | helper | 332 | 83 | 44 | 15 | 7 | 15 broad / 4 silent | **High attention**: 15 persistence calls; 7 Discord operations; split candidate; 15 broad catches; 4 silent recovery paths |
| 3773 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Sticky.py`

28 definitions: 21 routine, 7 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 17 | `StickyCog.__init__` | internal helper | 20 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 38 | `StickyCog.cog_unload` | helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 45 | `StickyCog._start_background_task` | internal helper | 19 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 49 | `StickyCog._start_background_task._done` | internal helper | 12 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 65 | `StickyCog.reload_from_config` | helper | 32 | 15 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 98 | `StickyCog.on_config_reload` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 101 | `StickyCog._required_rule_from_config` | internal helper | 31 | 13 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 133 | `StickyCog._get_sticky_for_channel` | internal helper | 8 | 4 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 146 | `StickyCog.on_message` | event listener | 49 | 15 | 1 | 0 | 0 | 1 broad / 0 silent | **Focused review**: 1 broad catch |
| 190 | `StickyCog.on_message._remove` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 196 | `StickyCog._do_sticky` | internal helper | 21 | 5 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 218 | `StickyCog._replace_sticky` | internal helper | 4 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 223 | `StickyCog._replace_sticky_locked` | internal helper | 87 | 26 | 14 | 3 | 4 | 6 broad / 2 silent | **Focused review**: 3 persistence calls; 4 Discord operations; split candidate; 6 broad catches; 2 silent recovery paths |
| 314 | `StickyCog._get_thread_lock` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 322 | `StickyCog._trim_forum_runtime_state` | internal helper | 9 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 332 | `StickyCog._forum_template_for_thread` | internal helper | 11 | 7 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 344 | `StickyCog._thread_has_bot_message` | internal helper | 19 | 13 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 364 | `StickyCog._send_forum_first_message` | internal helper | 16 | 8 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 381 | `StickyCog._schedule_required_word_check` | internal helper | 10 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 392 | `StickyCog._normalize_required_word_text` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 397 | `StickyCog._required_regex_is_safe` | internal helper | 18 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 416 | `StickyCog._thread_contains_required_word` | internal helper | 29 | 14 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 446 | `StickyCog._find_thread_owner` | internal helper | 18 | 9 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 465 | `StickyCog._log_required_word_deletion` | internal helper | 32 | 11 | 3 | 0 | 2 | 2 broad / 0 silent | **Routine**: 2 Discord operations; 2 broad catches |
| 498 | `StickyCog._enforce_required_word` | internal helper | 52 | 19 | 9 | 0 | 3 | 4 broad / 1 silent | **Focused review**: 3 Discord operations; 4 broad catches; 1 silent recovery path |
| 551 | `StickyCog._forum_first_message_flow` | internal helper | 64 | 17 | 6 | 0 | 0 | 3 broad / 1 silent | **Focused review**: 3 broad catches; 1 silent recovery path |
| 617 | `StickyCog.on_thread_create` | event listener | 14 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 633 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Tracking.py`

67 definitions: 49 routine, 15 focused, 3 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 46 | `TrackingCog.__init__` | internal helper | 16 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 63 | `TrackingCog._respond_interaction` | internal helper | 4 | 2 | 2 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 71 | `TrackingCog._cfg_int` | internal helper | 17 | 6 | 0 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |
| 89 | `TrackingCog._cfg_int_list` | internal helper | 21 | 6 | 0 | 0 | 0 | 3 broad / 1 silent | **Focused review**: 3 broad catches; 1 silent recovery path |
| 111 | `TrackingCog._format_template` | internal helper | 9 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 113 | `TrackingCog._format_template._SafeDict.__missing__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 121 | `TrackingCog._embed_from_template` | internal helper | 39 | 18 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 161 | `TrackingCog._weekly_request_review_data` | internal helper | 62 | 29 | 0 | 0 | 0 | none | **Focused review**: split candidate |
| 224 | `TrackingCog._weekly_request_missing_fields` | internal helper | 10 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 235 | `TrackingCog._weekly_request_max_chars` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 241 | `TrackingCog._cached_member_for_persisted_id` | internal helper | 15 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 257 | `TrackingCog._resolve_member` | internal helper | 28 | 8 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 286 | `TrackingCog._configured_channel` | internal helper | 8 | 6 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 295 | `TrackingCog._resolve_dm_user` | internal helper | 19 | 9 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 315 | `TrackingCog._weekly_offer_dm_channel` | internal helper | 5 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 321 | `TrackingCog._weekly_offer_message_matches` | internal helper | 36 | 23 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 358 | `TrackingCog._find_existing_weekly_offer` | internal helper | 20 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 379 | `TrackingCog._send_weekly_offer_message` | internal helper | 22 | 4 | 3 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 402 | `TrackingCog._log_background_error` | internal helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 409 | `TrackingCog._dm_user` | internal helper | 9 | 2 | 3 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 419 | `TrackingCog._validate_weekly_request_for_review` | internal helper | 49 | 12 | 8 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |
| 472 | `TrackingCog.start_background` | helper | 24 | 14 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 497 | `TrackingCog.cog_unload` | helper | 9 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 507 | `TrackingCog._recover_contacting_claims` | internal helper | 127 | 25 | 11 | 3 | 0 | 2 broad / 0 silent | **Focused review**: 3 persistence calls; split candidate; 2 broad catches |
| 635 | `TrackingCog.on_config_reload` | helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 642 | `TrackingCog.user_in_weekly_process` | helper | 10 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 653 | `TrackingCog.weekly_reward_disabled` | helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 660 | `TrackingCog.disable_weekly_reward_for_current_week` | helper | 21 | 1 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 682 | `TrackingCog.enable_weekly_reward_for_current_week` | helper | 53 | 4 | 5 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 736 | `TrackingCog._notify_reenabled_weekly_claims` | internal helper | 27 | 4 | 4 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 767 | `TrackingCog._weekly_log_meta` | internal helper | 32 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 800 | `TrackingCog._weekly_detail_lines` | internal helper | 14 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 815 | `TrackingCog._log_weekly` | internal helper | 41 | 10 | 5 | 1 | 2 | 3 broad / 0 silent | **Routine**: 1 persistence call; 2 Discord operations; 3 broad catches |
| 857 | `TrackingCog._anti_farm_cfg` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 861 | `TrackingCog._anti_farm_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 864 | `TrackingCog._message_signature` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 869 | `TrackingCog._anti_farm_reason` | internal helper | 53 | 24 | 0 | 0 | 0 | 4 broad / 0 silent | **Focused review**: 4 broad catches |
| 923 | `TrackingCog._record_anti_farm_event` | internal helper | 52 | 19 | 5 | 1 | 2 | 4 broad / 0 silent | **Focused review**: 1 persistence call; 2 Discord operations; 4 broad catches |
| 980 | `TrackingCog.on_message` | event listener | 62 | 17 | 5 | 1 | 0 | none | **Focused review**: 1 persistence call |
| 1043 | `TrackingCog._activity_flush_loop` | internal helper | 10 | 5 | 4 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1054 | `TrackingCog.flush_activity_counts` | helper | 41 | 12 | 4 | 2 | 0 | 2 broad / 0 silent | **Routine**: 2 persistence calls; 2 broad catches |
| 1099 | `TrackingCog._handle_dm` | internal helper | 102 | 21 | 12 | 2 | 5 | 6 broad / 4 silent | **High attention**: 2 persistence calls; 5 Discord operations; split candidate; 6 broad catches; 4 silent recovery paths |
| 1202 | `TrackingCog._record_request` | internal helper | 133 | 20 | 23 | 2 | 7 | 8 broad / 4 silent | **High attention**: 2 persistence calls; 7 Discord operations; split candidate; 8 broad catches; 4 silent recovery paths |
| 1337 | `TrackingCog.handle_decline_confirm` | workflow handler | 68 | 9 | 12 | 3 | 2 | 2 broad / 2 silent | **Focused review**: 3 persistence calls; 2 Discord operations; 2 broad catches; 2 silent recovery paths |
| 1409 | `TrackingCog._weekly_loop` | internal helper | 43 | 8 | 8 | 3 | 0 | 2 broad / 1 silent | **Focused review**: 3 persistence calls; 2 broad catches; 1 silent recovery path |
| 1453 | `TrackingCog._weekly_recap_due` | internal helper | 12 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1466 | `TrackingCog._weekly_recap_loop` | internal helper | 11 | 4 | 5 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1478 | `TrackingCog._timeout_loop` | internal helper | 12 | 4 | 6 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1491 | `TrackingCog._process_timeouts` | internal helper | 49 | 6 | 9 | 3 | 1 | 1 broad / 0 silent | **Routine**: 3 persistence calls; 1 Discord operation; 1 broad catch |
| 1541 | `TrackingCog._update_weekly_streaks` | internal helper | 45 | 21 | 4 | 4 | 0 | 2 broad / 0 silent | **Focused review**: 4 persistence calls; 2 broad catches |
| 1587 | `TrackingCog._send_weekly_recap` | internal helper | 112 | 38 | 12 | 7 | 3 | 6 broad / 1 silent | **High attention**: 7 persistence calls; 3 Discord operations; split candidate; 6 broad catches; 1 silent recovery path |
| 1700 | `TrackingCog._ranked_rows_for_week` | internal helper | 46 | 11 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1747 | `TrackingCog._send_missing_weekly_recap_once` | internal helper | 28 | 7 | 6 | 2 | 0 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 broad catch |
| 1779 | `TrackingCog.run_weekly_job` | helper | 43 | 9 | 10 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1823 | `TrackingCog._contact_user_for_week` | internal helper | 149 | 23 | 19 | 5 | 2 | 5 broad / 0 silent | **Focused review**: 5 persistence calls; 2 Discord operations; split candidate; 5 broad catches |
| 1973 | `TrackingCog._contact_next_eligible` | internal helper | 62 | 10 | 8 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 2039 | `TrackingCog._format_deadline` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2042 | `TrackingCog._build_request_dm_text` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2046 | `TrackingCog._build_request_dm_message` | internal helper | 15 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2062 | `TrackingCog._build_reminder_text` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2066 | `TrackingCog._build_reminder_message` | internal helper | 14 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2081 | `TrackingCog._process_reminders` | internal helper | 129 | 23 | 13 | 5 | 1 | 5 broad / 0 silent | **Focused review**: 5 persistence calls; 1 Discord operation; split candidate; 5 broad catches |
| 2214 | `TrackingCog.get_top` | helper | 22 | 4 | 3 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2237 | `TrackingCog.get_member_stats` | helper | 50 | 20 | 3 | 1 | 0 | none | **Focused review**: 1 persistence call |
| 2288 | `TrackingCog.force_dm_for_user` | helper | 62 | 16 | 14 | 3 | 0 | none | **Focused review**: 3 persistence calls |
| 2351 | `TrackingCog.reset_current_week` | helper | 20 | 8 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2373 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `main.py`

21 definitions: 14 routine, 6 focused, 1 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 39 | `startup_log` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 43 | `_discord_login_retry_seconds` | internal helper | 8 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 53 | `_startup_error_retry_seconds` | internal helper | 8 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 63 | `_prepare_fresh_event_loop` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 69 | `_close_event_loop` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 76 | `_compact_startup_exception` | internal helper | 12 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 90 | `_is_discord_startup_rate_limit` | internal helper | 6 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 98 | `_run_preflight_database_check` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 106 | `_repair_legacy_turso_snowflakes` | internal helper | 57 | 15 | 2 | 0 | 0 | 1 broad / 0 silent | **Focused review**: 1 broad catch |
| 165 | `_close_runtime_storage` | internal helper | 45 | 11 | 6 | 1 | 0 | 5 broad / 0 silent | **Focused review**: 1 persistence call; 5 broad catches |
| 212 | `_install_storage_close_hook` | internal helper | 13 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 216 | `_install_storage_close_hook.close_with_storage_flush` | helper | 7 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 227 | `_database_path_usable` | internal helper | 13 | 3 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 242 | `resolve_db_path` | helper | 68 | 24 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 311 | `create_bot` | helper | 214 | 37 | 25 | 0 | 0 | 13 broad / 1 silent | **High attention**: split candidate; 13 broad catches; 1 silent recovery path |
| 339 | `create_bot._load_cogs` | internal helper | 10 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 351 | `create_bot.on_ready` | helper | 98 | 21 | 17 | 0 | 0 | 8 broad / 1 silent | **Focused review**: 8 broad catches; 1 silent recovery path |
| 451 | `create_bot.on_disconnect` | helper | 31 | 8 | 4 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 484 | `create_bot.on_resumed` | helper | 19 | 7 | 4 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 504 | `create_bot.register_persistent_views` | helper | 10 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 526 | `run_bot_with_startup_backoff` | helper | 92 | 15 | 0 | 0 | 0 | 3 broad / 1 silent | **Focused review**: 3 broad catches; 1 silent recovery path |

### `utils/checks.py`

5 definitions: 5 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 6 | `member_has_any_role` | helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 10 | `is_admin_or_owner` | helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 13 | `is_mod` | helper | 4 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 18 | `ensure_allowed_guild_id` | helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 21 | `basic_color` | helper | 22 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |

### `utils/config.py`

7 definitions: 6 routine, 1 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 15 | `Config.__init__` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 20 | `Config.reload` | helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 24 | `Config.save` | helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 30 | `Config.get` | helper | 9 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 40 | `Config.get_str` | helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 46 | `Config.get_int` | helper | 11 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 58 | `Config.get_int_list` | helper | 23 | 9 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |

### `utils/db.py`

66 definitions: 50 routine, 14 focused, 2 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 24 | `DictRow.__init__` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 28 | `DictRow.__getitem__` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 34 | `_row_get` | internal helper | 11 | 4 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 47 | `_normalize_row` | internal helper | 14 | 8 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 63 | `_normalize_rows` | internal helper | 2 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 67 | `_fetchall` | internal helper | 2 | 2 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 71 | `_jwt_payload` | internal helper | 12 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 85 | `_token_scope_names` | internal helper | 9 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 96 | `_looks_like_turso_platform_token` | internal helper | 12 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 110 | `_is_recoverable_remote_error` | internal helper | 22 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 134 | `_is_replica_corruption_error` | internal helper | 10 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 146 | `_requires_libsql_integer_workaround` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 152 | `_exact_libsql_params` | internal helper | 2 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 156 | `_legacy_libsql_value` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 162 | `_legacy_where_params` | internal helper | 24 | 10 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 257 | `Database.__init__` | internal helper | 18 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 276 | `Database._close_connection_sync` | internal helper | 9 | 3 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 286 | `Database._reopen_connection_sync` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 292 | `Database._adapt_params` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 296 | `Database._execute_sync` | internal helper | 3 | 1 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 300 | `Database._execute_write_compat_sync` | internal helper | 9 | 6 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 310 | `Database._quarantine_replica_files_sync` | internal helper | 25 | 6 | 0 | 0 | 0 | 0 broad / 2 silent | **Focused review**: 2 silent recovery paths |
| 336 | `Database._rebuild_remote_replica_sync` | internal helper | 22 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 359 | `Database._open_connection_sync` | internal helper | 25 | 7 | 0 | 3 | 0 | 2 broad / 2 silent | **Focused review**: 3 persistence calls; 2 broad catches; 2 silent recovery paths |
| 385 | `Database._sync_remote_sync` | internal helper | 6 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 392 | `Database._sync_remote_with_retry_sync` | internal helper | 16 | 7 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 409 | `Database._try_pending_remote_sync_sync` | internal helper | 31 | 9 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 441 | `Database._commit_and_sync_sync` | internal helper | 11 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 453 | `Database._run_locked_with_retry` | internal helper | 35 | 12 | 6 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 489 | `Database.connect` | helper | 28 | 8 | 1 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 494 | `Database.connect._connect_and_migrate` | internal helper | 20 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 518 | `Database.close` | helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 525 | `Database.backup_to` | helper | 51 | 9 | 4 | 3 | 0 | 1 broad / 1 silent | **Focused review**: 3 persistence calls; 1 broad catch; 1 silent recovery path |
| 532 | `Database.backup_to._backup` | internal helper | 36 | 6 | 0 | 3 | 0 | 0 broad / 1 silent | **Focused review**: 3 persistence calls; 1 silent recovery path |
| 577 | `Database.restore_from` | helper | 91 | 15 | 1 | 8 | 0 | 2 broad / 3 silent | **Focused review**: 8 persistence calls; 2 broad catches; 3 silent recovery paths |
| 594 | `Database.restore_from._unlink_sidecars` | internal helper | 6 | 3 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 601 | `Database.restore_from._connect_current` | internal helper | 9 | 1 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 611 | `Database.restore_from._restore` | internal helper | 55 | 10 | 0 | 6 | 0 | 2 broad / 2 silent | **Focused review**: 6 persistence calls; 2 broad catches; 2 silent recovery paths |
| 669 | `Database._migrate_sync` | internal helper | 528 | 7 | 0 | 7 | 0 | none | **High attention**: 7 persistence calls; split candidate |
| 1198 | `Database._ensure_column_sync` | internal helper | 7 | 3 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1206 | `Database._normalize_weekly_dm_log_sync` | internal helper | 33 | 8 | 0 | 6 | 0 | none | **Routine**: 6 persistence calls |
| 1240 | `Database._init_ticket_sequences_sync` | internal helper | 27 | 8 | 0 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1268 | `Database.next_ticket_id` | helper | 25 | 3 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1269 | `Database.next_ticket_id._run` | internal helper | 22 | 3 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1294 | `Database.execute` | helper | 7 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1295 | `Database.execute._run` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1302 | `Database.execute_insert` | helper | 14 | 3 | 1 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1305 | `Database.execute_insert._run` | internal helper | 9 | 3 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1317 | `Database.execute_transaction` | helper | 26 | 6 | 1 | 1 | 0 | 2 broad / 1 silent | **Focused review**: 1 persistence call; 2 broad catches; 1 silent recovery path |
| 1328 | `Database.execute_transaction._run` | internal helper | 13 | 4 | 0 | 1 | 0 | 2 broad / 1 silent | **Focused review**: 1 persistence call; 2 broad catches; 1 silent recovery path |
| 1344 | `Database.set_runtime_setting` | helper | 7 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1352 | `Database.get_runtime_setting` | helper | 11 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1364 | `Database.repair_legacy_snowflake_precision` | helper | 134 | 31 | 1 | 8 | 0 | 2 broad / 3 silent | **High attention**: 8 persistence calls; split candidate; 2 broad catches; 3 silent recovery paths |
| 1385 | `Database.repair_legacy_snowflake_precision._mapping` | internal helper | 18 | 9 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 1408 | `Database.repair_legacy_snowflake_precision._run` | internal helper | 88 | 22 | 0 | 8 | 0 | 2 broad / 2 silent | **Focused review**: 8 persistence calls; 2 broad catches; 2 silent recovery paths |
| 1499 | `Database.health_snapshot` | helper | 13 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1513 | `Database.sync_remote` | helper | 16 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1518 | `Database.sync_remote._run` | internal helper | 9 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1530 | `Database.executemany` | helper | 10 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1533 | `Database.executemany._run` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1541 | `Database.fetchone` | helper | 13 | 5 | 1 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1542 | `Database.fetchone._run` | internal helper | 10 | 5 | 0 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1555 | `Database.fetchone_local` | helper | 20 | 5 | 1 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1563 | `Database.fetchone_local._run` | internal helper | 10 | 5 | 0 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1576 | `Database.fetchall` | helper | 13 | 5 | 1 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1577 | `Database.fetchall._run` | internal helper | 10 | 5 | 0 | 3 | 0 | none | **Routine**: 3 persistence calls |

### `utils/discord_refs.py`

7 definitions: 5 routine, 2 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 13 | `legacy_rounded_snowflake` | helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 18 | `is_legacy_rounded_snowflake` | helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 26 | `snowflake_matches_legacy` | helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 33 | `fetch_persisted_message` | helper | 58 | 19 | 1 | 0 | 1 | none | **Focused review**: 1 Discord operation |
| 52 | `fetch_persisted_message._matches` | internal helper | 4 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 93 | `fetch_persisted_channel` | helper | 58 | 17 | 2 | 0 | 1 | 0 broad / 1 silent | **Focused review**: 1 Discord operation; 1 silent recovery path |
| 112 | `fetch_persisted_channel._matching_channels` | internal helper | 7 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/errors.py`

10 definitions: 6 routine, 4 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 15 | `_redact_secrets` | internal helper | 18 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 35 | `_compact_error_message` | internal helper | 26 | 18 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 63 | `_strip_trace_context` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 71 | `_dedupe_key` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 77 | `_unwrap_command_error` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 91 | `_command_error_record` | internal helper | 13 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 106 | `log_error` | helper | 68 | 17 | 3 | 0 | 3 | 7 broad / 3 silent | **Focused review**: 3 Discord operations; 7 broad catches; 3 silent recovery paths |
| 175 | `setup_global_error_handlers` | helper | 20 | 3 | 3 | 0 | 1 | 1 broad / 1 silent | **Focused review**: 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 177 | `setup_global_error_handlers.on_application_command_error` | helper | 14 | 3 | 2 | 0 | 1 | 1 broad / 1 silent | **Focused review**: 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 193 | `setup_global_error_handlers.on_error` | helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/gd_validation.py`

14 definitions: 11 routine, 2 focused, 1 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 16 | `_as_int` | internal helper | 7 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 25 | `_as_bool` | internal helper | 7 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 34 | `_kv_pairs` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 39 | `_boomlings_creator_map` | internal helper | 7 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 48 | `_demon_difficulty` | internal helper | 8 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 58 | `_classic_difficulty` | internal helper | 9 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 69 | `_length_name` | internal helper | 9 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 80 | `_provider_error` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 84 | `parse_gdbrowser_level` | helper | 35 | 20 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 121 | `parse_boomlings_level` | helper | 51 | 22 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 174 | `fetch_gdbrowser_level` | helper | 15 | 6 | 1 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 191 | `fetch_boomlings_level` | helper | 15 | 3 | 1 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 208 | `combine_level_validation` | helper | 74 | 42 | 0 | 0 | 0 | none | **High attention**: split candidate |
| 284 | `validation_notice` | helper | 20 | 10 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/keepalive.py`

17 definitions: 16 routine, 1 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 41 | `set_keepalive_status` | helper | 26 | 11 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 69 | `get_keepalive_status` | helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 74 | `set_public_bot_metrics` | helper | 35 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 111 | `set_public_release_data` | helper | 19 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 132 | `_public_state_label` | internal helper | 14 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 148 | `get_public_bot_payload` | helper | 43 | 15 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 193 | `get_public_releases_payload` | helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 203 | `_response_for_path` | internal helper | 27 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 233 | `_HealthHandler._health_response` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 236 | `_HealthHandler._send_health_headers` | internal helper | 15 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 252 | `_HealthHandler.do_GET` | helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 261 | `_HealthHandler.do_HEAD` | helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 265 | `_HealthHandler.log_message` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 269 | `start_keepalive_thread` | helper | 19 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 277 | `start_keepalive_thread._run` | internal helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 290 | `_handle` | internal helper | 14 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 305 | `start_keepalive` | helper | 18 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/mentions.py`

3 definitions: 3 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 6 | `no_mentions` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 10 | `user_mentions` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 14 | `user_and_role_mentions` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/releases.py`

7 definitions: 6 routine, 1 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 22 | `normalize_version` | helper | 8 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 32 | `compare_versions` | helper | 35 | 20 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 69 | `newest_version` | helper | 5 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 76 | `normalize_release_changes` | helper | 26 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 104 | `validate_release_payload` | helper | 21 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 127 | `load_release_manifest` | helper | 21 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 150 | `row_to_public_release` | helper | 19 | 11 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/runtime_config.py`

6 definitions: 4 routine, 2 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 11 | `_forum_entries` | internal helper | 10 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 23 | `collect_forum_required_rules` | helper | 16 | 5 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 41 | `apply_forum_required_rules` | helper | 20 | 7 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 63 | `load_runtime_config_overrides` | helper | 9 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 74 | `persist_server_icon_config` | helper | 2 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 78 | `persist_forum_required_rules` | helper | 2 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |

### `utils/server_icons.py`

7 definitions: 7 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 10 | `normalize_server_icon_mode` | helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 15 | `is_valid_icon_url` | helper | 17 | 11 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 34 | `is_expiring_discord_attachment_url` | helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 46 | `server_icon_url_warning` | helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 52 | `clean_icon_urls` | helper | 11 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 65 | `parse_server_icon_index` | helper | 8 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 75 | `ensure_server_icon_config` | helper | 26 | 10 | 0 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |

### `utils/timeutils.py`

5 definitions: 5 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 8 | `now_madrid` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 11 | `week_start_sunday` | helper | 7 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 19 | `next_sunday_midnight` | helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 26 | `iso` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 29 | `from_iso` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/transcript.py`

5 definitions: 4 routine, 1 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 8 | `_indented` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 13 | `_message_text` | internal helper | 21 | 19 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 36 | `_transcript_line` | internal helper | 12 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 50 | `build_text_transcript` | helper | 39 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 63 | `build_text_transcript.write_line` | helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/views.py`

25 definitions: 25 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 29 | `TranscriptRequestView.__init__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 33 | `TranscriptRequestView.approve` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 45 | `TranscriptRequestView.deny` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 58 | `ReleaseApprovalView.__init__` | internal helper | 18 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 77 | `ReleaseApprovalView.approve` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 88 | `ReleaseApprovalView.reject` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 101 | `TicketClosePromptView.__init__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 105 | `TicketClosePromptView.yes` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 117 | `TicketClosePromptView.no` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 130 | `_HelpMenuSelect.__init__` | internal helper | 62 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 193 | `_HelpMenuSelect.callback` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 206 | `HelpMenuView.__init__` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 212 | `_FormerMemberHelpSelect.__init__` | internal helper | 22 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 235 | `_FormerMemberHelpSelect.callback` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 248 | `FormerMemberHelpView.__init__` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 254 | `BanInfoGiveInfoView.__init__` | internal helper | 10 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 265 | `BanInfoGiveInfoView.give_info` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 278 | `TrackingDeclineConfirmView.__init__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 282 | `TrackingDeclineConfirmView.yes` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 294 | `TrackingDeclineConfirmView.no` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 307 | `LevelRequestButtonView.__init__` | internal helper | 10 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 318 | `LevelRequestButtonView.request` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 331 | `LevelRequestReviewView.__init__` | internal helper | 10 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 342 | `LevelRequestReviewView._make_callback` | internal helper | 12 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 343 | `LevelRequestReviewView._make_callback._callback` | internal helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |

## Residual Operational Risk

The static review cannot simulate Discord permissions, role hierarchy, deleted live messages, third-party API outages, or a process termination in the narrow interval between a Discord action and its database compensation. Those cases are contained by durable states, repair commands, idempotent checks, logging, and startup reconciliation, but should still be exercised after deployment.

The largest functions are concentrated in configuration diagnostics, impact aggregation, request repair, request form orchestration, daily summaries, and ticket closure. They are covered by focused checks and are valid today, but they are the best future refactoring targets because each coordinates several external boundaries.

## Verification Gate

```text
Python compileall                     PASS
Ruff correctness and bug checks      PASS
Pytest                                PASS (122 tests)
Bandit medium/high security scan     PASS
Production dependency audit          PASS
Discord modal serialization          PASS
Configuration JSON parse             PASS
```
