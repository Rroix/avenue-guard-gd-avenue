# Avenue Guard Function-by-Function Diagnosis

**Audit date:** 2026-09-14  
**Runtime scope:** 37 Python modules, 1006 definitions, 26,048 physical lines
**Method:** AST inventory, per-function control-flow scoring, interaction-order review, persistence/Discord I/O mapping, compile, tests, Ruff, Bandit, and dependency audit

## Reading This Report

Every runtime function, method, nested callback, and modal handler has one row below. The attention label is a review priority, not proof of a defect: orchestration code and schema declarations are naturally larger. Complexity is a deterministic branch score used to find code that deserves focused tests.

- **Routine:** compact control flow with no static risk signal.
- **Focused review:** a long path, broad recovery, interaction timing, or several I/O boundaries.
- **High attention:** very large/branch-heavy orchestration or several silent recovery paths.

## Executive Diagnosis

- All runtime modules parse and compile.
- The complete automated suite passes: 140 tests.
- Ruff's correctness and bug checks pass.
- Bandit reports no medium or high security findings.
- The production dependency set has no known published vulnerabilities.
- Slash commands and support component handlers acknowledge interactions before slow work, except modal-first commands that must query the local replica or open the modal as their initial response.
- Turso-backed workflow state, request waves, tickets, tracking, summaries, runtime settings, and help submissions remain restart-persistent.

## Current Review Fixes

- Added a durable Discord action outbox with idempotency keys, atomic work claims, backoff, stale-worker recovery, and dead-letter visibility.
- Added an operations supervisor that distinguishes disabled jobs from failures and restarts stopped tracking, help, request, release, summary, status, icon, and backup work.
- Added persistent health samples, database query timing, external-provider latency, grouped incidents, permission drift history, and workflow correlation IDs.
- Added typed config and embed-template validation with explicit config, runtime, embed, and database schema versions.
- Added post-deployment smoke checks, non-destructive restore drills, retention controls, and automated monthly impact reports.
- Added request result-notification preferences, queue SLA indicators, reviewer validation rechecks, wave comparison, and structured rejection analytics.
- Added weekday-aware forecasts with confidence levels, rolling trends, and anomaly detection.
- Extracted request validation, scheduling, review analytics, diagnostics, backups, and forecasting into focused service modules.
- Retained the hardened GD/Boomlings provider circuits, bounded payload parsing, shared HTTP sessions, CA trust, and external-failure fallback behavior.
- Expanded migration, outbox, workflow, config-schema, restore-drill, forecast, request analytics, and operational recovery regression coverage.

## Attention Summary

| Classification | Definitions |
|---|---:|
| Routine | 812 |
| Focused review | 158 |
| High attention | 36 |

## Function Inventory

### `cogs/Background.py`

89 definitions: 74 routine, 13 focused, 2 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 34 | `_day_key` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 38 | `_parse_hhmm` | internal helper | 10 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 49 | `_fmt_minutes` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 57 | `_fmt_num` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 60 | `_fmt_delta` | internal helper | 8 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 69 | `_fmt_percent` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 103 | `BackgroundCog.__init__` | internal helper | 15 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 119 | `BackgroundCog.cog_unload` | helper | 19 | 5 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 139 | `BackgroundCog._cleanup_resources` | internal helper | 3 | 1 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 143 | `BackgroundCog.close_resources` | helper | 6 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 150 | `BackgroundCog._get_icon_http_session` | internal helper | 16 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 167 | `BackgroundCog.start_background` | helper | 60 | 22 | 10 | 0 | 0 | 7 broad / 0 silent | **Focused review**: 7 broad catches |
| 228 | `BackgroundCog.on_config_reload` | helper | 45 | 21 | 0 | 0 | 0 | 8 broad / 4 silent | **High attention**: 8 broad catches; 4 silent recovery paths |
| 277 | `BackgroundCog._excluded_channels` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 285 | `BackgroundCog._status_rotation_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 288 | `BackgroundCog._status_rotation_interval` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 291 | `BackgroundCog._status_list` | internal helper | 14 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 306 | `BackgroundCog._presence_transport_is_closing` | internal helper | 13 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 320 | `BackgroundCog._server_icon_rotation_enabled` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 324 | `BackgroundCog._server_icon_interval` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 328 | `BackgroundCog._server_icon_urls` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 333 | `BackgroundCog._database_backup_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 336 | `BackgroundCog._database_backup_interval_seconds` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 340 | `BackgroundCog._server_icon_current_index` | internal helper | 8 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 349 | `BackgroundCog._server_icon_candidate_indices` | internal helper | 14 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 364 | `BackgroundCog._looks_like_server_icon_image` | internal helper | 10 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 375 | `BackgroundCog._assert_public_server_icon_url` | internal helper | 26 | 12 | 1 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 402 | `BackgroundCog._download_server_icon` | internal helper | 52 | 21 | 3 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 455 | `BackgroundCog._detect_current_server_icon_index` | internal helper | 18 | 8 | 2 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 474 | `BackgroundCog._persist_server_icon_state` | internal helper | 6 | 2 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 481 | `BackgroundCog._remember_server_icon_error` | internal helper | 7 | 3 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 489 | `BackgroundCog._config_write_lock` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 497 | `BackgroundCog.rotate_server_icon_once` | helper | 15 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 513 | `BackgroundCog._rotate_server_icon_once_locked` | internal helper | 70 | 19 | 6 | 0 | 1 | 2 broad / 0 silent | **Focused review**: 1 Discord operation; 2 broad catches |
| 584 | `BackgroundCog._render_status_text` | internal helper | 55 | 14 | 5 | 3 | 0 | 3 broad / 0 silent | **Routine**: 3 persistence calls; 3 broad catches |
| 587 | `BackgroundCog._render_status_text._SafeDict.__missing__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 640 | `BackgroundCog._daily_summary_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 643 | `BackgroundCog._daily_summary_channel_id` | internal helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 649 | `BackgroundCog._daily_reset_after_report` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 652 | `BackgroundCog._daily_summary_due` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 659 | `BackgroundCog._daily_summary_already_sent` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 666 | `BackgroundCog._record_daily_summary_sent` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 672 | `BackgroundCog._voice_sessions_from_guild` | internal helper | 9 | 6 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 682 | `BackgroundCog._stats_payload` | internal helper | 23 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 706 | `BackgroundCog._stats_from_payload` | internal helper | 16 | 12 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 723 | `BackgroundCog._load_daily_stats` | internal helper | 11 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 735 | `BackgroundCog._persist_daily_stats` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 742 | `BackgroundCog._persist_current_day` | internal helper | 9 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 752 | `BackgroundCog._rollover_boundary_ts` | internal helper | 6 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 759 | `BackgroundCog._add_voice_until` | internal helper | 8 | 4 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 768 | `BackgroundCog._track_background_persist` | internal helper | 18 | 5 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 771 | `BackgroundCog._track_background_persist._done` | internal helper | 13 | 5 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 787 | `BackgroundCog._rollover_if_needed` | internal helper | 28 | 7 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 820 | `BackgroundCog.on_message` | event listener | 14 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 836 | `BackgroundCog.on_message_edit` | event listener | 11 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 849 | `BackgroundCog.on_message_delete` | event listener | 11 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 862 | `BackgroundCog.on_reaction_add` | event listener | 11 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 875 | `BackgroundCog.on_member_join` | event listener | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 884 | `BackgroundCog.on_member_remove` | event listener | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 893 | `BackgroundCog.on_member_ban` | event listener | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 902 | `BackgroundCog.on_member_unban` | event listener | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 911 | `BackgroundCog.on_member_update` | event listener | 10 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 923 | `BackgroundCog.on_voice_state_update` | event listener | 23 | 14 | 1 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 948 | `BackgroundCog.on_application_command_completion` | event listener | 14 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 964 | `BackgroundCog.on_application_command_error` | event listener | 15 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 984 | `BackgroundCog.update_snapshot` | background loop | 22 | 11 | 5 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |
| 1007 | `BackgroundCog._log_snapshot_failure` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1011 | `BackgroundCog._before_snapshot` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1015 | `BackgroundCog._snapshot_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1019 | `BackgroundCog.database_backup` | background loop | 33 | 13 | 5 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 1054 | `BackgroundCog._before_database_backup` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1058 | `BackgroundCog._database_backup_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1062 | `BackgroundCog.rotate_status` | background loop | 40 | 11 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1104 | `BackgroundCog._before_rotate` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1108 | `BackgroundCog._rotate_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1112 | `BackgroundCog.rotate_server_icon` | background loop | 23 | 11 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1137 | `BackgroundCog._before_server_icon_rotate` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1141 | `BackgroundCog._server_icon_rotate_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1144 | `BackgroundCog._start_daily_report_loop` | internal helper | 14 | 5 | 0 | 0 | 0 | 2 broad / 2 silent | **Focused review**: 2 broad catches; 2 silent recovery paths |
| 1159 | `BackgroundCog._top_channel_lines` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1167 | `BackgroundCog._top_member_lines` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1175 | `BackgroundCog._top_command_lines` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1183 | `BackgroundCog._summary_color` | internal helper | 8 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1192 | `BackgroundCog._send_daily_summary_for_day` | internal helper | 7 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1200 | `BackgroundCog._send_daily_summary_for_day_locked` | internal helper | 173 | 33 | 11 | 0 | 3 | 7 broad / 3 silent | **High attention**: 3 Discord operations; split candidate; 7 broad catches; 3 silent recovery paths |
| 1375 | `BackgroundCog.daily_report` | background loop | 12 | 4 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1389 | `BackgroundCog._before_daily` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1393 | `BackgroundCog._daily_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1396 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Commands.py`

142 definitions: 102 routine, 32 focused, 8 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 77 | `_fmt_num` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 84 | `_fmt_percent` | internal helper | 8 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 94 | `_ticket_status_key` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 106 | `_ticket_status_label` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 111 | `AdminDashboardView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 117 | `AdminDashboardView._show` | internal helper | 17 | 5 | 7 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 136 | `AdminDashboardView.overview` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 140 | `AdminDashboardView.config` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 144 | `AdminDashboardView.repairs` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 148 | `AdminDashboardView.incidents` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 152 | `AdminDashboardView.refresh` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 155 | `AdminDashboardView._run_action` | internal helper | 40 | 16 | 14 | 1 | 4 | 1 broad / 0 silent | **Focused review**: 1 persistence call; 4 Discord operations; 1 broad catch |
| 197 | `AdminDashboardView.restart_tasks` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 201 | `AdminDashboardView.repair_requests` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 205 | `AdminDashboardView.retry_deliveries` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 209 | `AdminDashboardView.scan_permissions` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 213 | `AdminDashboardView.backup_drill` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 218 | `CommandsCog.__init__` | internal helper | 81 | 13 | 5 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 281 | `CommandsCog.__init__.resync` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 285 | `CommandsCog.__init__.restart` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 289 | `CommandsCog.__init__.dance` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 293 | `CommandsCog.__init__.rps` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 297 | `CommandsCog.__init__.gambling` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 300 | `CommandsCog._in_allowed_guild` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 303 | `CommandsCog._defer` | internal helper | 7 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 311 | `CommandsCog._send` | internal helper | 4 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 317 | `CommandsCog._claim_fun_cooldown` | internal helper | 28 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 346 | `CommandsCog._log_admin_action` | internal helper | 19 | 5 | 2 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 366 | `CommandsCog._impact_owner_ids` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 369 | `CommandsCog._is_impact_owner_ctx` | internal helper | 7 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 377 | `CommandsCog._is_release_owner_ctx` | internal helper | 8 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 386 | `CommandsCog._backup_channel_id` | internal helper | 7 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 394 | `CommandsCog._backup_local_dir` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 398 | `CommandsCog._restore_upload_dir` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 403 | `CommandsCog._backup_retention_count` | internal helper | 6 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 410 | `CommandsCog._prune_local_backups` | internal helper | 14 | 4 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 425 | `CommandsCog._database_path` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 428 | `CommandsCog._database_storage_note` | internal helper | 36 | 21 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 465 | `CommandsCog._zip_backup_file` | internal helper | 7 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 473 | `CommandsCog._post_database_backup` | internal helper | 75 | 13 | 11 | 2 | 3 | 2 broad / 1 silent | **Focused review**: 2 persistence calls; 3 Discord operations; 2 broad catches; 1 silent recovery path |
| 549 | `CommandsCog._restore_safe_filename` | internal helper | 4 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 554 | `CommandsCog._save_restore_attachment` | internal helper | 17 | 6 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 572 | `CommandsCog._extract_sqlite_restore_file` | internal helper | 26 | 12 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 599 | `CommandsCog._validate_restore_database` | internal helper | 23 | 7 | 1 | 4 | 0 | none | **Routine**: 4 persistence calls |
| 600 | `CommandsCog._validate_restore_database._run` | internal helper | 20 | 7 | 0 | 4 | 0 | none | **Routine**: 4 persistence calls |
| 623 | `CommandsCog._impact_scalar` | internal helper | 11 | 6 | 1 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 635 | `CommandsCog._impact_float` | internal helper | 11 | 6 | 1 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 647 | `CommandsCog._impact_group_counts` | internal helper | 43 | 5 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 691 | `CommandsCog._impact_daily_totals` | internal helper | 118 | 31 | 1 | 1 | 0 | 5 broad / 4 silent | **High attention**: 1 persistence call; split candidate; 5 broad catches; 4 silent recovery paths |
| 810 | `CommandsCog._impact_window_rows` | internal helper | 14 | 7 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 825 | `CommandsCog._impact_window_sum` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 829 | `CommandsCog._impact_window_average` | internal helper | 5 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 835 | `CommandsCog._impact_percent_change` | internal helper | 9 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 845 | `CommandsCog._impact_forecast` | internal helper | 70 | 21 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 916 | `CommandsCog._collect_impact_metrics` | internal helper | 325 | 39 | 47 | 3 | 0 | 1 broad / 0 silent | **High attention**: 3 persistence calls; split candidate; 1 broad catch |
| 1242 | `CommandsCog._impact_metric_rows` | internal helper | 46 | 19 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1245 | `CommandsCog._impact_metric_rows.add` | helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1289 | `CommandsCog._impact_csv` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1296 | `CommandsCog._impact_daily_csv` | internal helper | 23 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1320 | `CommandsCog._impact_breakdown_csv` | internal helper | 18 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1339 | `CommandsCog._impact_markdown` | internal helper | 97 | 4 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1437 | `CommandsCog._impact_report_embed` | internal helper | 71 | 5 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1509 | `CommandsCog._impact_files` | internal helper | 14 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1524 | `CommandsCog.bot_impact` | helper | 72 | 17 | 17 | 1 | 2 | 4 broad / 0 silent | **Focused review**: 1 persistence call; 2 Discord operations; 4 broad catches |
| 1597 | `CommandsCog.bot_release` | helper | 58 | 6 | 10 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1656 | `CommandsCog.bot_releases` | helper | 68 | 19 | 9 | 0 | 1 | 1 broad / 0 silent | **Focused review**: 1 Discord operation; 1 broad catch |
| 1725 | `CommandsCog.bot_backup` | helper | 18 | 5 | 10 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1744 | `CommandsCog.bot_retention` | helper | 66 | 11 | 8 | 1 | 1 | none | **Routine**: 1 persistence call; 1 Discord operation |
| 1811 | `CommandsCog.bot_restore` | helper | 104 | 24 | 19 | 2 | 1 | 4 broad / 0 silent | **Focused review**: 2 persistence calls; 1 Discord operation; split candidate; 4 broad catches |
| 1916 | `CommandsCog.bot_storage` | helper | 60 | 14 | 7 | 2 | 1 | none | **Routine**: 2 persistence calls; 1 Discord operation |
| 1977 | `CommandsCog._is_admin_ctx` | internal helper | 6 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1984 | `CommandsCog._is_mod_ctx` | internal helper | 7 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1992 | `CommandsCog._request_reviewer_role_ids` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1996 | `CommandsCog._is_request_staff_ctx` | internal helper | 13 | 7 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2010 | `CommandsCog._server_icon_status_embed` | internal helper | 47 | 20 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 2058 | `CommandsCog._notify_background_config_reload` | internal helper | 8 | 3 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 2067 | `CommandsCog._server_icon_operation_lock` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2072 | `CommandsCog._config_write_lock` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2080 | `CommandsCog._save_server_icon_config` | internal helper | 21 | 5 | 5 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 2102 | `CommandsCog.server_icon_status` | helper | 7 | 3 | 5 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 2110 | `CommandsCog.server_icon_mode` | helper | 28 | 6 | 7 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 2139 | `CommandsCog.server_icon_add` | helper | 29 | 9 | 10 | 0 | 7 | none | **Routine**: 7 Discord operations |
| 2169 | `CommandsCog.server_icon_replace` | helper | 37 | 12 | 9 | 0 | 6 | none | **Routine**: 6 Discord operations |
| 2207 | `CommandsCog.server_icon_remove` | helper | 35 | 11 | 7 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 2243 | `CommandsCog.server_icon_set` | helper | 28 | 7 | 9 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2272 | `CommandsCog.server_icon_next` | helper | 14 | 5 | 8 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2287 | `CommandsCog._resolve_member` | internal helper | 15 | 5 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 2303 | `CommandsCog._task_state` | internal helper | 13 | 8 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2317 | `CommandsCog._count_db` | internal helper | 6 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 2324 | `CommandsCog._dashboard_issues` | internal helper | 155 | 55 | 3 | 0 | 0 | 1 broad / 0 silent | **High attention**: split candidate; 1 broad catch |
| 2480 | `CommandsCog._admin_dashboard_embed` | internal helper | 243 | 72 | 12 | 5 | 0 | 3 broad / 0 silent | **High attention**: 5 persistence calls; split candidate; 3 broad catches |
| 2724 | `CommandsCog.bot_dashboard` | helper | 8 | 3 | 6 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2735 | `CommandsCog.bot_health` | helper | 89 | 22 | 12 | 3 | 2 | 4 broad / 0 silent | **Focused review**: 3 persistence calls; 2 Discord operations; 4 broad catches |
| 2744 | `CommandsCog.bot_health._count` | internal helper | 6 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 2777 | `CommandsCog.bot_health._task_state` | internal helper | 13 | 8 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2825 | `CommandsCog.bot_doctor` | helper | 139 | 49 | 6 | 0 | 2 | none | **High attention**: 2 Discord operations; split candidate |
| 2840 | `CommandsCog.bot_doctor.channel_perm_report` | helper | 16 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2965 | `CommandsCog._template_variables` | internal helper | 12 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2978 | `CommandsCog._request_template_allowed_vars` | internal helper | 80 | 1 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 3059 | `CommandsCog._looks_like_color_value` | internal helper | 23 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3083 | `CommandsCog._validate_request_templates` | internal helper | 69 | 28 | 0 | 0 | 0 | none | **Focused review**: split candidate |
| 3104 | `CommandsCog._validate_request_templates.check_text` | helper | 8 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3113 | `CommandsCog._validate_request_templates.walk` | helper | 26 | 18 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 3153 | `CommandsCog.bot_config_check` | helper | 402 | 104 | 5 | 0 | 2 | 2 broad / 0 silent | **High attention**: 2 Discord operations; split candidate; 2 broad catches |
| 3164 | `CommandsCog.bot_config_check.check_channel` | helper | 13 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3178 | `CommandsCog.bot_config_check.check_role` | helper | 9 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3241 | `CommandsCog.bot_config_check.check_hhmm` | helper | 7 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3260 | `CommandsCog.bot_config_check.check_number` | helper | 22 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3556 | `CommandsCog._parse_snowflake_arg` | internal helper | 8 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 3565 | `CommandsCog._request_change_lines` | internal helper | 22 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3574 | `CommandsCog._request_change_lines.short` | helper | 5 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3588 | `CommandsCog.requests_notifications` | helper | 42 | 5 | 5 | 2 | 1 | none | **Routine**: 2 persistence calls; 1 Discord operation |
| 3631 | `CommandsCog.requests_analytics` | helper | 63 | 23 | 7 | 2 | 1 | none | **Focused review**: 2 persistence calls; 1 Discord operation |
| 3695 | `CommandsCog.requests_history` | helper | 74 | 13 | 9 | 3 | 2 | 2 broad / 0 silent | **Focused review**: 3 persistence calls; 2 Discord operations; 2 broad catches |
| 3770 | `CommandsCog.requests_repair` | helper | 36 | 13 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3807 | `CommandsCog.requests_pending` | helper | 143 | 30 | 8 | 3 | 2 | 1 broad / 0 silent | **High attention**: 3 persistence calls; 2 Discord operations; split candidate; 1 broad catch |
| 3891 | `CommandsCog.requests_pending.request_name` | helper | 8 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 3953 | `CommandsCog.tracking_top` | helper | 60 | 20 | 8 | 1 | 2 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 2 Discord operations; 1 broad catch; 1 silent recovery path |
| 4015 | `CommandsCog.tracking_me` | helper | 44 | 12 | 8 | 1 | 2 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 2 Discord operations; 1 broad catch; 1 silent recovery path |
| 4060 | `CommandsCog.tracking_force_dm` | helper | 22 | 5 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 4083 | `CommandsCog.tracking_reset` | helper | 17 | 5 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 4101 | `CommandsCog.tracking_disable_reward` | helper | 21 | 5 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 4123 | `CommandsCog.tracking_enable_reward` | helper | 26 | 6 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 4151 | `CommandsCog.ticket_close` | helper | 27 | 9 | 10 | 1 | 6 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 6 Discord operations; 1 broad catch; 1 silent recovery path |
| 4179 | `CommandsCog.ticket_status` | helper | 78 | 17 | 14 | 2 | 6 | 1 broad / 0 silent | **Focused review**: 2 persistence calls; 6 Discord operations; 1 broad catch |
| 4258 | `CommandsCog.ticket_transcripts` | helper | 65 | 19 | 7 | 1 | 4 | none | **Focused review**: 1 persistence call; 4 Discord operations |
| 4325 | `CommandsCog._parse_channel_id` | internal helper | 10 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 4336 | `CommandsCog._configured_forum_entries` | internal helper | 29 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 4366 | `CommandsCog._resolve_forum_entry` | internal helper | 41 | 15 | 0 | 0 | 0 | 4 broad / 3 silent | **Focused review**: 4 broad catches; 3 silent recovery paths |
| 4408 | `CommandsCog.forum_required_word` | helper | 118 | 31 | 16 | 0 | 9 | 3 broad / 0 silent | **High attention**: 9 Discord operations; split candidate; 3 broad catches |
| 4528 | `CommandsCog._resync` | internal helper | 49 | 11 | 13 | 1 | 3 | 4 broad / 0 silent | **Focused review**: 1 persistence call; 3 Discord operations; 4 broad catches |
| 4579 | `CommandsCog._restart` | internal helper | 16 | 4 | 7 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 4597 | `CommandsCog._dance` | internal helper | 7 | 3 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 4606 | `CommandsCog._rps` | internal helper | 97 | 23 | 15 | 0 | 8 | 3 broad / 1 silent | **Focused review**: 8 Discord operations; 3 broad catches; 1 silent recovery path |
| 4619 | `CommandsCog._rps.outcome` | helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 4626 | `CommandsCog._rps.RPSView.__init__` | internal helper | 12 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 4639 | `CommandsCog._rps.RPSView._make_callback` | internal helper | 62 | 18 | 12 | 0 | 5 | 3 broad / 1 silent | **Focused review**: 5 Discord operations; 3 broad catches; 1 silent recovery path |
| 4640 | `CommandsCog._rps.RPSView._make_callback._cb` | internal helper | 60 | 18 | 12 | 0 | 5 | 3 broad / 1 silent | **Focused review**: 5 Discord operations; 3 broad catches; 1 silent recovery path |
| 4704 | `CommandsCog._rps_get_streak` | internal helper | 8 | 2 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 4713 | `CommandsCog._rps_update_streak` | internal helper | 28 | 4 | 4 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 4743 | `CommandsCog._gambling` | internal helper | 58 | 21 | 9 | 0 | 6 | 3 broad / 2 silent | **Focused review**: 6 Discord operations; 3 broad catches; 2 silent recovery paths |
| 4802 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Help.py`

177 definitions: 146 routine, 25 focused, 6 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 33 | `_format_duration` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 58 | `DMRecipientUnavailable.__init__` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 63 | `_ticket_status_key` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 75 | `_ticket_status_label` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 80 | `HelpSessionControlView.__init__` | internal helper | 21 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 102 | `HelpSessionControlView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 109 | `HelpSessionControlView.start` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 115 | `HelpSessionControlView.back` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 121 | `HelpSessionControlView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 127 | `HelpSessionControlView.start_over` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 134 | `HelpSubmissionPreviewView.__init__` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 141 | `HelpSubmissionPreviewView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 148 | `HelpSubmissionPreviewView.submit` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 154 | `HelpSubmissionPreviewView.edit` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 160 | `HelpSubmissionPreviewView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 166 | `HelpSubmissionPreviewView.start_over` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 173 | `HelpTicketTopicView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 179 | `HelpTicketTopicView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 185 | `HelpTicketTopicView._make_topic_callback` | internal helper | 6 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 186 | `HelpTicketTopicView._make_topic_callback._callback` | internal helper | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 193 | `HelpTicketTopicView.moderation` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 197 | `HelpTicketTopicView.requests` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 201 | `HelpTicketTopicView.server` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 205 | `HelpTicketTopicView.other` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 209 | `HelpTicketTopicView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 215 | `HelpTicketTopicView.back` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 221 | `HelpTicketTopicView.start_over` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 228 | `FaqPageView.__init__` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 241 | `FaqPageView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 248 | `FaqPageView.previous` | UI callback | 7 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 257 | `FaqPageView.next` | UI callback | 7 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 266 | `FaqPageView.back` | UI callback | 3 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 272 | `PartnershipConfirmView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 278 | `PartnershipConfirmView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 285 | `PartnershipConfirmView.confirm` | UI callback | 3 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 290 | `PartnershipConfirmView.cancel` | UI callback | 3 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 296 | `BanInfoModal.__init__` | internal helper | 64 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 361 | `BanInfoModal.callback` | helper | 13 | 6 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 377 | `BanInfoConfirmView.__init__` | internal helper | 14 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 392 | `BanInfoConfirmView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 399 | `BanInfoConfirmView.confirm` | UI callback | 8 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 409 | `BanInfoConfirmView.cancel` | UI callback | 6 | 3 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 418 | `TicketSatisfactionView.__init__` | internal helper | 18 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 437 | `TicketSatisfactionView._make_callback` | internal helper | 6 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 438 | `TicketSatisfactionView._make_callback._callback` | internal helper | 4 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 448 | `HelpCog.__init__` | internal helper | 21 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 470 | `HelpCog.cog_unload` | helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 474 | `HelpCog.start_background` | helper | 19 | 7 | 6 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |
| 494 | `HelpCog.on_config_reload` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 497 | `HelpCog._member_from_actor` | internal helper | 12 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 510 | `HelpCog._resolve_member` | internal helper | 16 | 5 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 527 | `HelpCog._resolve_dm_recipient` | internal helper | 52 | 21 | 2 | 0 | 2 | 1 broad / 0 silent | **Focused review**: 2 Discord operations; 1 broad catch |
| 580 | `HelpCog._help_color` | internal helper | 11 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 592 | `HelpCog._help_embed` | internal helper | 10 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 603 | `HelpCog._delete_interaction_source` | internal helper | 13 | 4 | 2 | 0 | 1 | 2 broad / 2 silent | **Focused review**: 1 Discord operation; 2 broad catches; 2 silent recovery paths |
| 617 | `HelpCog._ack_and_delete_source` | internal helper | 5 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 623 | `HelpCog._respond_interaction` | internal helper | 4 | 2 | 2 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 628 | `HelpCog._cooldowns` | internal helper | 7 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 636 | `HelpCog._submission_label` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 643 | `HelpCog._submission_prefix` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 650 | `HelpCog._submission_code` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 653 | `HelpCog._attachment_data` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 664 | `HelpCog._merge_attachments` | internal helper | 11 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 676 | `HelpCog._attachments_text` | internal helper | 13 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 690 | `HelpCog._has_attachments` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 694 | `HelpCog._short_text` | internal helper | 5 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 700 | `HelpCog._embed_char_count` | internal helper | 7 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 708 | `HelpCog._add_bounded_field` | internal helper | 15 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 724 | `HelpCog._normalize_duplicate_text` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 730 | `HelpCog._ticket_scan_loop` | internal helper | 9 | 4 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 740 | `HelpCog._log_background_error` | internal helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 747 | `HelpCog._load_active_ticket_channels` | internal helper | 28 | 5 | 3 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 776 | `HelpCog._ticket_channel_from_stored_id` | internal helper | 27 | 4 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 804 | `HelpCog._reconcile_missing_ticket_channels` | internal helper | 33 | 9 | 4 | 2 | 0 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 broad catch |
| 838 | `HelpCog._scan_tickets` | internal helper | 103 | 23 | 13 | 5 | 3 | 5 broad / 3 silent | **Focused review**: 5 persistence calls; 3 Discord operations; split candidate; 5 broad catches; 3 silent recovery paths |
| 946 | `HelpCog.on_message` | event listener | 92 | 27 | 15 | 2 | 1 | 3 broad / 0 silent | **Focused review**: 2 persistence calls; 1 Discord operation; split candidate; 3 broad catches |
| 1042 | `HelpCog._remaining_help_cooldown` | internal helper | 9 | 2 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1052 | `HelpCog._touch_help_cooldown` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1059 | `HelpCog._cooldown_until` | internal helper | 9 | 3 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1069 | `HelpCog._cooldown_embed` | internal helper | 8 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1078 | `HelpCog._flow_start_limit_message` | internal helper | 58 | 17 | 0 | 0 | 0 | 2 broad / 0 silent | **Focused review**: 2 broad catches |
| 1137 | `HelpCog._weekly_status_text` | internal helper | 20 | 9 | 3 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1158 | `HelpCog._request_result_label` | internal helper | 11 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1170 | `HelpCog._request_state_text` | internal helper | 33 | 14 | 2 | 2 | 0 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 broad catch |
| 1204 | `HelpCog._active_ticket_text` | internal helper | 12 | 4 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1217 | `HelpCog._recent_help_status_text` | internal helper | 36 | 9 | 2 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1254 | `HelpCog._cooldown_status_text` | internal helper | 7 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1262 | `HelpCog._send_dm_dashboard` | internal helper | 21 | 5 | 7 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1284 | `HelpCog._send_former_member_dashboard` | internal helper | 8 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1293 | `HelpCog._home_menu_view` | internal helper | 5 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1299 | `HelpCog._faq_entries` | internal helper | 4 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1304 | `HelpCog._send_ticket_topics` | internal helper | 7 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1315 | `HelpCog.handle_help_selection` | workflow handler | 194 | 32 | 37 | 0 | 14 | none | **High attention**: 14 Discord operations; split candidate |
| 1510 | `HelpCog._send_faq` | internal helper | 8 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1519 | `HelpCog._faq_page_embed` | internal helper | 23 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1543 | `HelpCog._send_faq_page` | internal helper | 8 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1552 | `HelpCog.handle_faq_page` | workflow handler | 18 | 3 | 4 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1571 | `HelpCog._send_weekly_status` | internal helper | 41 | 16 | 7 | 2 | 2 | none | **Focused review**: 2 persistence calls; 2 Discord operations |
| 1616 | `HelpCog._help_session_cache_store` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1623 | `HelpCog._help_session_lock` | internal helper | 11 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1635 | `HelpCog._help_session_tombstone_store` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1642 | `HelpCog._claimed_dm_message_store` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1649 | `HelpCog._claim_dm_message` | internal helper | 14 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1664 | `HelpCog.should_yield_weekly_dm` | helper | 15 | 6 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1680 | `HelpCog._prune_help_session_memory` | internal helper | 25 | 14 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1706 | `HelpCog._help_session_lifetime` | internal helper | 8 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1715 | `HelpCog._log_help_session_storage_error` | internal helper | 8 | 2 | 1 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 1724 | `HelpCog._start_help_session` | internal helper | 20 | 2 | 2 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1745 | `HelpCog._clear_help_session` | internal helper | 14 | 2 | 2 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1760 | `HelpCog._get_help_session` | internal helper | 44 | 15 | 4 | 0 | 0 | 2 broad / 0 silent | **Focused review**: 2 broad catches |
| 1805 | `HelpCog.has_active_help_session` | helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1808 | `HelpCog._help_stage_prompt_embed` | internal helper | 49 | 10 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1858 | `HelpCog._preview_stage` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1861 | `HelpCog._edit_stage_for_kind` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1868 | `HelpCog._fresh_edit_data` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1876 | `HelpCog._submission_core_text` | internal helper | 8 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1885 | `HelpCog._submission_preview_embed` | internal helper | 19 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1905 | `HelpCog._show_submission_preview` | internal helper | 7 | 1 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1913 | `HelpCog._is_duplicate_help_submission` | internal helper | 36 | 9 | 1 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 1950 | `HelpCog._submission_log_channel` | internal helper | 16 | 7 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1967 | `HelpCog._submission_staff_embed` | internal helper | 28 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1996 | `HelpCog._insert_help_submission` | internal helper | 8 | 2 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2005 | `HelpCog._submit_help_submission` | internal helper | 40 | 8 | 11 | 2 | 2 | 2 broad / 1 silent | **Focused review**: 2 persistence calls; 2 Discord operations; 2 broad catches; 1 silent recovery path |
| 2046 | `HelpCog._help_max_submission_chars` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2052 | `HelpCog._handle_help_session_message` | internal helper | 4 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2057 | `HelpCog._handle_help_session_message_locked` | internal helper | 181 | 27 | 28 | 3 | 10 | none | **High attention**: 3 persistence calls; 10 Discord operations; split candidate |
| 2239 | `HelpCog._handle_typed_back` | internal helper | 49 | 8 | 10 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 2289 | `HelpCog._edit_prompt_embed` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2302 | `HelpCog.handle_help_session_control` | workflow handler | 8 | 2 | 3 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 2311 | `HelpCog._handle_help_session_control_locked` | internal helper | 89 | 14 | 18 | 0 | 5 | none | **Focused review**: 5 Discord operations |
| 2401 | `HelpCog.handle_help_submission_preview` | workflow handler | 6 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2408 | `HelpCog._handle_help_submission_preview_locked` | internal helper | 59 | 12 | 18 | 0 | 6 | none | **Routine**: 6 Discord operations |
| 2468 | `HelpCog._parse_ticket_reference` | internal helper | 14 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2483 | `HelpCog._staff_log_embed` | internal helper | 14 | 3 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 2498 | `HelpCog._log_help_action` | internal helper | 27 | 9 | 3 | 0 | 2 | 2 broad / 0 silent | **Routine**: 2 Discord operations; 2 broad catches |
| 2526 | `HelpCog._ticket_label` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2529 | `HelpCog._requester_id_from_help_log_message` | internal helper | 26 | 14 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2556 | `HelpCog._is_ticket_transcript_message` | internal helper | 21 | 14 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2578 | `HelpCog._reconcile_requester_id` | internal helper | 17 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2596 | `HelpCog._requester_id_from_message_content` | internal helper | 4 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2601 | `HelpCog._handle_staff_help_reply` | internal helper | 9 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2611 | `HelpCog._handle_staff_help_reply_locked` | internal helper | 133 | 37 | 18 | 3 | 4 | 7 broad / 5 silent | **High attention**: 3 persistence calls; 4 Discord operations; split candidate; 7 broad catches; 5 silent recovery paths |
| 2748 | `HelpCog._submit_appeal` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2752 | `HelpCog._submit_report` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2756 | `HelpCog._submit_bot_issue` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2763 | `HelpCog._ban_info_code` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2766 | `HelpCog._known_user_history` | internal helper | 38 | 12 | 3 | 1 | 1 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 2805 | `HelpCog._ban_info_staff_embed` | internal helper | 43 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2849 | `HelpCog._create_ban_info_request` | internal helper | 89 | 8 | 16 | 5 | 6 | 2 broad / 1 silent | **Focused review**: 5 persistence calls; 6 Discord operations; 2 broad catches; 1 silent recovery path |
| 2939 | `HelpCog._can_handle_ban_info` | internal helper | 11 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2951 | `HelpCog.handle_ban_info_button` | workflow handler | 16 | 6 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 2968 | `HelpCog.handle_ban_info_modal` | workflow handler | 115 | 18 | 11 | 2 | 4 | 1 broad / 0 silent | **Focused review**: 2 persistence calls; 4 Discord operations; split candidate; 1 broad catch |
| 3084 | `HelpCog._ban_info_delivery_embed` | internal helper | 41 | 13 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3126 | `HelpCog._update_ban_info_staff_message` | internal helper | 75 | 20 | 6 | 1 | 1 | 2 broad / 0 silent | **Focused review**: 1 persistence call; 1 Discord operation; 2 broad catches |
| 3202 | `HelpCog.finalize_ban_info` | helper | 106 | 15 | 17 | 3 | 6 | 1 broad / 0 silent | **Focused review**: 3 persistence calls; 6 Discord operations; split candidate; 1 broad catch |
| 3312 | `HelpCog._create_transcript_request` | internal helper | 8 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3321 | `HelpCog._create_transcript_request_locked` | internal helper | 76 | 13 | 6 | 3 | 3 | 3 broad / 1 silent | **Focused review**: 3 persistence calls; 3 Discord operations; 3 broad catches; 1 silent recovery path |
| 3398 | `HelpCog.handle_transcript_request_decision` | workflow handler | 5 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3404 | `HelpCog._handle_transcript_request_decision_locked` | internal helper | 200 | 25 | 32 | 7 | 5 | 5 broad / 0 silent | **High attention**: 7 persistence calls; 5 Discord operations; split candidate; 5 broad catches |
| 3605 | `HelpCog._dm_transcript` | internal helper | 124 | 20 | 16 | 2 | 2 | 7 broad / 0 silent | **Focused review**: 2 persistence calls; 2 Discord operations; split candidate; 7 broad catches |
| 3733 | `HelpCog.handle_ticket_topic` | workflow handler | 10 | 4 | 5 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 3744 | `HelpCog.handle_partnership_confirmation` | workflow handler | 25 | 3 | 4 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 3770 | `HelpCog._recover_ticket_creator_id` | internal helper | 68 | 23 | 3 | 1 | 0 | 1 broad / 0 silent | **Focused review**: 1 persistence call; 1 broad catch |
| 3839 | `HelpCog._recover_closed_ticket_creator_id` | internal helper | 99 | 22 | 6 | 2 | 0 | 1 broad / 0 silent | **Focused review**: 2 persistence calls; 1 broad catch |
| 3939 | `HelpCog.update_ticket_opening_status` | helper | 97 | 25 | 12 | 3 | 4 | 5 broad / 2 silent | **Focused review**: 3 persistence calls; 4 Discord operations; split candidate; 5 broad catches; 2 silent recovery paths |
| 4037 | `HelpCog._create_staff_ticket` | internal helper | 19 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 4057 | `HelpCog._create_staff_ticket_locked` | internal helper | 165 | 30 | 26 | 4 | 4 | 6 broad / 0 silent | **High attention**: 4 persistence calls; 4 Discord operations; split candidate; 6 broad catches |
| 4223 | `HelpCog._next_ticket_id` | internal helper | 2 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 4226 | `HelpCog.handle_ticket_close_prompt` | workflow handler | 61 | 15 | 12 | 2 | 3 | 2 broad / 2 silent | **Focused review**: 2 persistence calls; 3 Discord operations; 2 broad catches; 2 silent recovery paths |
| 4288 | `HelpCog._send_ticket_satisfaction_prompt` | internal helper | 143 | 24 | 16 | 8 | 3 | 4 broad / 2 silent | **Focused review**: 8 persistence calls; 3 Discord operations; split candidate; 4 broad catches; 2 silent recovery paths |
| 4432 | `HelpCog._restore_ticket_satisfaction_views` | internal helper | 71 | 8 | 6 | 3 | 0 | 2 broad / 0 silent | **Focused review**: 3 persistence calls; 2 broad catches |
| 4504 | `HelpCog.handle_ticket_satisfaction` | workflow handler | 57 | 10 | 9 | 2 | 6 | 2 broad / 0 silent | **Routine**: 2 persistence calls; 6 Discord operations; 2 broad catches |
| 4562 | `HelpCog.close_ticket_channel` | helper | 15 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 4578 | `HelpCog._close_ticket_channel_locked` | internal helper | 153 | 38 | 33 | 6 | 8 | 14 broad / 4 silent | **High attention**: 6 persistence calls; 8 Discord operations; split candidate; 14 broad catches; 4 silent recovery paths |
| 4614 | `HelpCog._close_ticket_channel_locked._restore_open_status` | internal helper | 11 | 2 | 3 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 4671 | `HelpCog._close_ticket_channel_locked._cleanup_transcript_artifact` | internal helper | 21 | 5 | 4 | 1 | 1 | 2 broad / 1 silent | **Focused review**: 1 persistence call; 1 Discord operation; 2 broad catches; 1 silent recovery path |
| 4733 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

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

### `cogs/Operations.py`

22 definitions: 20 routine, 2 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 38 | `OperationsCog.__init__` | internal helper | 11 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 50 | `OperationsCog.cog_unload` | helper | 4 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 55 | `OperationsCog.close_resources` | helper | 12 | 6 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 68 | `OperationsCog.start_background` | helper | 34 | 13 | 4 | 1 | 0 | none | **Routine**: 1 persistence call |
| 103 | `OperationsCog.task_snapshot` | helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 109 | `OperationsCog._external_task_specs` | internal helper | 16 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 126 | `OperationsCog._task_expected` | internal helper | 17 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 145 | `OperationsCog._task_state` | internal helper | 19 | 9 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 165 | `OperationsCog.restart_stopped_tasks` | helper | 36 | 16 | 4 | 0 | 0 | 1 broad / 0 silent | **Focused review**: 1 broad catch |
| 202 | `OperationsCog._outbox_loop` | internal helper | 16 | 5 | 4 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 219 | `OperationsCog._supervisor_loop` | internal helper | 51 | 17 | 4 | 0 | 0 | 1 broad / 0 silent | **Focused review**: 1 broad catch |
| 271 | `OperationsCog.collect_health_sample` | helper | 56 | 10 | 3 | 3 | 0 | 1 broad / 0 silent | **Routine**: 3 persistence calls; 1 broad catch |
| 328 | `OperationsCog._health_loop` | internal helper | 11 | 4 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 340 | `OperationsCog.scan_permissions` | helper | 12 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 353 | `OperationsCog._persist_maintenance_timestamps` | internal helper | 8 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 362 | `OperationsCog.run_retention` | helper | 21 | 3 | 3 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 384 | `OperationsCog.run_restore_drill` | helper | 9 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 394 | `OperationsCog.generate_monthly_report` | helper | 50 | 9 | 4 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 445 | `OperationsCog.reconcile_monthly_reports` | helper | 27 | 9 | 3 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 473 | `OperationsCog._maintenance_loop` | internal helper | 29 | 10 | 7 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 503 | `OperationsCog._post_deploy_smoke_test` | internal helper | 61 | 13 | 5 | 2 | 0 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 broad catch |
| 566 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

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
| 700 | `ReleaseCog.start_background` | helper | 27 | 9 | 8 | 0 | 0 | 4 broad / 0 silent | **Focused review**: 4 broad catches |
| 728 | `ReleaseCog.release_overview` | helper | 25 | 5 | 2 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 754 | `ReleaseCog.handle_release_decision` | workflow handler | 19 | 2 | 3 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 774 | `ReleaseCog._handle_release_decision_locked` | internal helper | 164 | 25 | 21 | 6 | 9 | 3 broad / 0 silent | **High attention**: 6 persistence calls; 9 Discord operations; split candidate; 3 broad catches |
| 940 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/RequestLevels.py`

167 definitions: 143 routine, 15 focused, 9 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 101 | `_SafeDict.__missing__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 106 | `LevelRequestModal.__init__` | internal helper | 39 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 146 | `LevelRequestModal.callback` | helper | 15 | 8 | 3 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 164 | `ReviewModal.__init__` | internal helper | 13 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 178 | `ReviewModal.callback` | helper | 7 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 188 | `FirstRequestChoiceView.__init__` | internal helper | 8 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 197 | `FirstRequestChoiceView._will` | internal helper | 4 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 204 | `OtherReasonView.__init__` | internal helper | 9 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 214 | `OtherReasonView._make_callback` | internal helper | 4 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 215 | `OtherReasonView._make_callback._callback` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 221 | `ScheduledOpeningEditModal.__init__` | internal helper | 47 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 269 | `ScheduledOpeningEditModal.callback` | helper | 12 | 7 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 284 | `ScheduledOpeningsView.__init__` | internal helper | 33 | 10 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 318 | `ScheduledOpeningsView._allowed` | internal helper | 12 | 5 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 331 | `ScheduledOpeningsView._select` | internal helper | 8 | 3 | 2 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 340 | `ScheduledOpeningsView._refresh` | internal helper | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 346 | `ScheduledOpeningsView._edit` | internal helper | 15 | 6 | 4 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 362 | `ScheduledOpeningsView._delete` | internal helper | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 368 | `ScheduledOpeningsView._open_now` | internal helper | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 376 | `ScheduledOpenNowConfirmView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 382 | `ScheduledOpenNowConfirmView._allowed` | internal helper | 12 | 5 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 396 | `ScheduledOpenNowConfirmView.confirm` | UI callback | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 403 | `ScheduledOpenNowConfirmView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 410 | `RequestLevelsCog.__init__` | internal helper | 162 | 3 | 6 | 0 | 0 | none | **High attention**: split candidate |
| 438 | `RequestLevelsCog.__init__.refresh_request_button` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 442 | `RequestLevelsCog.__init__.open_requests` | slash command | 49 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 493 | `RequestLevelsCog.__init__.close_requests` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 497 | `RequestLevelsCog.__init__.requests_are` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 501 | `RequestLevelsCog.__init__.edit_request` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 505 | `RequestLevelsCog.__init__.pending_openings` | slash command | 67 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 573 | `RequestLevelsCog.cog_unload` | helper | 14 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 588 | `RequestLevelsCog.close_resources` | helper | 15 | 7 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 604 | `RequestLevelsCog.start_background` | helper | 18 | 9 | 3 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 623 | `RequestLevelsCog.on_config_reload` | helper | 9 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 633 | `RequestLevelsCog._start_background_task` | internal helper | 13 | 3 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 634 | `RequestLevelsCog._start_background_task.runner` | helper | 7 | 3 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 647 | `RequestLevelsCog._cfg` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 650 | `RequestLevelsCog._cfg_int` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 653 | `RequestLevelsCog._cfg_int_list` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 656 | `RequestLevelsCog._reviewer_role_ids` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 660 | `RequestLevelsCog._post_close_edit_seconds` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 664 | `RequestLevelsCog._edit_deadline_ts_for_state` | internal helper | 19 | 7 | 0 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 684 | `RequestLevelsCog._edit_window_text` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 691 | `RequestLevelsCog._can_edit_submission` | internal helper | 32 | 14 | 0 | 0 | 0 | 3 broad / 1 silent | **Focused review**: 3 broad catches; 1 silent recovery path |
| 724 | `RequestLevelsCog._current_user_submission` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 730 | `RequestLevelsCog._current_user_submission_local` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 736 | `RequestLevelsCog._latest_editable_user_submission` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 743 | `RequestLevelsCog._editable_user_submission_for_modal` | internal helper | 20 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 764 | `RequestLevelsCog._state_after_timed_close_check` | internal helper | 12 | 6 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 777 | `RequestLevelsCog._request_initial_values` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 783 | `RequestLevelsCog._message` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 786 | `RequestLevelsCog._message_formatted` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 789 | `RequestLevelsCog._request_button_label` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 792 | `RequestLevelsCog._request_type_normalize_text` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 798 | `RequestLevelsCog._normalize_request_type` | internal helper | 9 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 808 | `RequestLevelsCog._request_type_label` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 814 | `RequestLevelsCog._request_type_help` | internal helper | 2 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 817 | `RequestLevelsCog._request_type_from_row` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 824 | `RequestLevelsCog._clean_open_message` | internal helper | 7 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 832 | `RequestLevelsCog._request_open_condition_text` | internal helper | 11 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 844 | `RequestLevelsCog._send_open_announcement` | internal helper | 55 | 23 | 4 | 0 | 1 | 3 broad / 0 silent | **Focused review**: 1 Discord operation; 3 broad catches |
| 900 | `RequestLevelsCog._color_name` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 903 | `RequestLevelsCog._format` | internal helper | 5 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 909 | `RequestLevelsCog._submitted_ago` | internal helper | 6 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 916 | `RequestLevelsCog._clean_level_id` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 919 | `RequestLevelsCog._normalize_level_id` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 922 | `RequestLevelsCog._valid_url` | internal helper | 12 | 7 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 935 | `RequestLevelsCog._validate_request_data` | internal helper | 10 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 946 | `RequestLevelsCog._level_validation_cfg` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 950 | `RequestLevelsCog._level_validation_enabled` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 954 | `RequestLevelsCog._level_validation_cache_seconds` | internal helper | 5 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 960 | `RequestLevelsCog._level_validation_timeout_seconds` | internal helper | 5 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 966 | `RequestLevelsCog._level_validation_message` | internal helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 972 | `RequestLevelsCog._level_validation_providers` | internal helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 981 | `RequestLevelsCog._level_validation_rate_limit_message` | internal helper | 44 | 19 | 0 | 0 | 0 | 3 broad / 0 silent | **Focused review**: 3 broad catches |
| 1026 | `RequestLevelsCog._provider_failure_cfg` | internal helper | 11 | 3 | 0 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 1038 | `RequestLevelsCog._provider_access_denied_backoff_seconds` | internal helper | 16 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1055 | `RequestLevelsCog._provider_retry_attempts` | internal helper | 11 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1067 | `RequestLevelsCog._level_validation_failure_cache_seconds` | internal helper | 11 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1079 | `RequestLevelsCog._provider_circuit_result` | internal helper | 15 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1095 | `RequestLevelsCog._provider_circuit_open` | internal helper | 17 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1113 | `RequestLevelsCog._record_provider_validation_result` | internal helper | 34 | 10 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1148 | `RequestLevelsCog.validation_provider_snapshot` | helper | 19 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1168 | `RequestLevelsCog._provider_telemetry` | internal helper | 14 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1183 | `RequestLevelsCog.reset_validation_providers` | helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1190 | `RequestLevelsCog._provider_min_interval` | internal helper | 11 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1202 | `RequestLevelsCog._fetch_validation_provider` | internal helper | 52 | 12 | 4 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1255 | `RequestLevelsCog._get_level_validation_session` | internal helper | 29 | 7 | 1 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 1285 | `RequestLevelsCog._safe_json_loads` | internal helper | 5 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1291 | `RequestLevelsCog._cached_level_validation` | internal helper | 17 | 5 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1309 | `RequestLevelsCog._lookup_level_validation` | internal helper | 73 | 23 | 7 | 1 | 0 | 1 broad / 0 silent | **Focused review**: 1 persistence call; 1 broad catch |
| 1383 | `RequestLevelsCog._apply_level_validation_vars` | internal helper | 81 | 30 | 0 | 0 | 0 | 1 broad / 0 silent | **High attention**: split candidate; 1 broad catch |
| 1465 | `RequestLevelsCog._validate_level_external` | internal helper | 28 | 14 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1494 | `RequestLevelsCog._request_type_validation_error` | internal helper | 32 | 23 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1527 | `RequestLevelsCog._has_reviewer_role` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1531 | `RequestLevelsCog._embed_from_template` | internal helper | 50 | 21 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1582 | `RequestLevelsCog._reply_ephemeral` | internal helper | 5 | 2 | 2 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 1588 | `RequestLevelsCog._log_request_admin_action` | internal helper | 23 | 7 | 2 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1612 | `RequestLevelsCog._state_label` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1615 | `RequestLevelsCog._request_button_embed` | internal helper | 15 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1631 | `RequestLevelsCog._pct` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1636 | `RequestLevelsCog._wave_summary_vars` | internal helper | 67 | 27 | 4 | 3 | 0 | none | **Focused review**: 3 persistence calls; split candidate |
| 1704 | `RequestLevelsCog._reviewer_stats_lines` | internal helper | 29 | 12 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1734 | `RequestLevelsCog._wave_summary_embed` | internal helper | 37 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1772 | `RequestLevelsCog.update_wave_summary` | helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1776 | `RequestLevelsCog._update_wave_summary_unlocked` | internal helper | 79 | 17 | 15 | 4 | 6 | 6 broad / 3 silent | **Focused review**: 4 persistence calls; 6 Discord operations; 6 broad catches; 3 silent recovery paths |
| 1856 | `RequestLevelsCog._base_state_vars` | internal helper | 25 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1882 | `RequestLevelsCog._row_value` | internal helper | 7 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1890 | `RequestLevelsCog._duplicate_history_warning` | internal helper | 25 | 6 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1916 | `RequestLevelsCog._days_in_month` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1919 | `RequestLevelsCog._add_month` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1925 | `RequestLevelsCog._scheduled_local_time_exists` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1928 | `RequestLevelsCog._parse_scheduled_open_ts` | internal helper | 41 | 16 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1970 | `RequestLevelsCog._scheduled_opening_rows` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1977 | `RequestLevelsCog.get_scheduled_opening` | helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1984 | `RequestLevelsCog._scheduled_openings_embed` | internal helper | 37 | 14 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2022 | `RequestLevelsCog.refresh_pending_openings_panel` | helper | 5 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 2028 | `RequestLevelsCog.delete_scheduled_opening` | helper | 16 | 2 | 6 | 1 | 2 | none | **Routine**: 1 persistence call; 2 Discord operations |
| 2045 | `RequestLevelsCog.open_scheduled_opening_now` | helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2049 | `RequestLevelsCog._open_scheduled_opening_now_locked` | internal helper | 44 | 11 | 9 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 2094 | `RequestLevelsCog.handle_scheduled_opening_edit_modal` | workflow handler | 68 | 20 | 13 | 1 | 9 | 2 broad / 0 silent | **Focused review**: 1 persistence call; 9 Discord operations; 2 broad catches |
| 2118 | `RequestLevelsCog.handle_scheduled_opening_edit_modal.optional_positive` | helper | 11 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2163 | `RequestLevelsCog._data_vars` | internal helper | 68 | 38 | 0 | 0 | 0 | 1 broad / 0 silent | **High attention**: split candidate; 1 broad catch |
| 2232 | `RequestLevelsCog._weekly_data_vars` | internal helper | 27 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2260 | `RequestLevelsCog._result_label` | internal helper | 6 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2267 | `RequestLevelsCog._status_channel_id` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2272 | `RequestLevelsCog._result_template_key` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2279 | `RequestLevelsCog._get_state` | internal helper | 9 | 2 | 3 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 2289 | `RequestLevelsCog._get_state_local` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2295 | `RequestLevelsCog._set_state_closed` | internal helper | 50 | 10 | 7 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 2346 | `RequestLevelsCog._open_requests_now` | internal helper | 110 | 23 | 12 | 1 | 0 | 2 broad / 0 silent | **Focused review**: 1 persistence call; split candidate; 2 broad catches |
| 2457 | `RequestLevelsCog._auto_close_loop` | internal helper | 17 | 9 | 5 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2475 | `RequestLevelsCog._scheduled_open_loop` | internal helper | 44 | 14 | 8 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 2520 | `RequestLevelsCog._in_allowed_guild` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2523 | `RequestLevelsCog._defer_command` | internal helper | 5 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2529 | `RequestLevelsCog._cached_interaction_member` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2536 | `RequestLevelsCog._resolve_member` | internal helper | 18 | 5 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 2555 | `RequestLevelsCog._is_admin` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2558 | `RequestLevelsCog._is_mod` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2566 | `RequestLevelsCog._configured_channel` | internal helper | 9 | 6 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 2576 | `RequestLevelsCog.refresh_or_create_request_button` | helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2580 | `RequestLevelsCog._refresh_or_create_request_button_unlocked` | internal helper | 70 | 19 | 14 | 3 | 6 | 6 broad / 3 silent | **Focused review**: 3 persistence calls; 6 Discord operations; 6 broad catches; 3 silent recovery paths |
| 2651 | `RequestLevelsCog.refresh_request_button` | helper | 13 | 5 | 8 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 2665 | `RequestLevelsCog.open_requests` | helper | 94 | 37 | 15 | 1 | 9 | none | **High attention**: 1 persistence call; 9 Discord operations; split candidate |
| 2760 | `RequestLevelsCog.pending_openings` | helper | 96 | 34 | 25 | 3 | 15 | none | **High attention**: 3 persistence calls; 15 Discord operations; split candidate |
| 2857 | `RequestLevelsCog.close_requests` | helper | 16 | 5 | 10 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 2874 | `RequestLevelsCog.requests_are` | helper | 15 | 7 | 4 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2890 | `RequestLevelsCog._requirements_ok` | internal helper | 8 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2899 | `RequestLevelsCog.handle_request_button` | workflow handler | 86 | 19 | 15 | 0 | 12 | none | **Focused review**: 12 Discord operations |
| 2986 | `RequestLevelsCog.edit_request` | helper | 18 | 3 | 4 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3005 | `RequestLevelsCog.handle_first_choice` | workflow handler | 20 | 7 | 5 | 0 | 4 | 1 broad / 0 silent | **Routine**: 4 Discord operations; 1 broad catch |
| 3026 | `RequestLevelsCog.handle_request_form` | workflow handler | 224 | 33 | 41 | 7 | 3 | 8 broad / 1 silent | **High attention**: 7 persistence calls; 3 Discord operations; split candidate; 8 broad catches; 1 silent recovery path |
| 3251 | `RequestLevelsCog.handle_request_edit_form` | workflow handler | 267 | 44 | 41 | 5 | 5 | 8 broad / 3 silent | **High attention**: 5 persistence calls; 5 Discord operations; split candidate; 8 broad catches; 3 silent recovery paths |
| 3519 | `RequestLevelsCog._refresh_closed_wave` | internal helper | 7 | 3 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 3527 | `RequestLevelsCog._submission_by_message` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3533 | `RequestLevelsCog._weekly_submission_by_message` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3539 | `RequestLevelsCog._review_target_by_message` | internal helper | 8 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3548 | `RequestLevelsCog._review_target_by_message_local` | internal helper | 24 | 4 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3573 | `RequestLevelsCog._channel_by_id` | internal helper | 8 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3582 | `RequestLevelsCog._review_target_channel` | internal helper | 16 | 6 | 3 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3599 | `RequestLevelsCog.handle_review_button` | workflow handler | 19 | 9 | 9 | 0 | 6 | none | **Routine**: 6 Discord operations |
| 3619 | `RequestLevelsCog._recheck_review_validation` | internal helper | 49 | 12 | 8 | 1 | 1 | none | **Routine**: 1 persistence call; 1 Discord operation |
| 3669 | `RequestLevelsCog.handle_review_submission` | workflow handler | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3672 | `RequestLevelsCog.handle_other_reason` | workflow handler | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3675 | `RequestLevelsCog._finalize_review` | internal helper | 185 | 28 | 22 | 2 | 2 | 4 broad / 0 silent | **High attention**: 2 persistence calls; 2 Discord operations; split candidate; 4 broad catches |
| 3861 | `RequestLevelsCog.repair_request_system` | helper | 332 | 83 | 44 | 15 | 7 | 15 broad / 4 silent | **High attention**: 15 persistence calls; 7 Discord operations; split candidate; 15 broad catches; 4 silent recovery paths |
| 4195 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

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

67 definitions: 48 routine, 16 focused, 3 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 47 | `TrackingCog.__init__` | internal helper | 16 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 64 | `TrackingCog._respond_interaction` | internal helper | 4 | 2 | 2 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 72 | `TrackingCog._cfg_int` | internal helper | 17 | 6 | 0 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |
| 90 | `TrackingCog._cfg_int_list` | internal helper | 21 | 6 | 0 | 0 | 0 | 3 broad / 1 silent | **Focused review**: 3 broad catches; 1 silent recovery path |
| 112 | `TrackingCog._format_template` | internal helper | 9 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 114 | `TrackingCog._format_template._SafeDict.__missing__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 122 | `TrackingCog._embed_from_template` | internal helper | 39 | 18 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 162 | `TrackingCog._weekly_request_review_data` | internal helper | 62 | 29 | 0 | 0 | 0 | none | **Focused review**: split candidate |
| 225 | `TrackingCog._weekly_request_missing_fields` | internal helper | 10 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 236 | `TrackingCog._weekly_request_max_chars` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 242 | `TrackingCog._cached_member_for_persisted_id` | internal helper | 15 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 258 | `TrackingCog._resolve_member` | internal helper | 28 | 8 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 287 | `TrackingCog._configured_channel` | internal helper | 8 | 6 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 296 | `TrackingCog._resolve_dm_user` | internal helper | 19 | 9 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 316 | `TrackingCog._weekly_offer_dm_channel` | internal helper | 5 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 322 | `TrackingCog._weekly_offer_message_matches` | internal helper | 36 | 23 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 359 | `TrackingCog._find_existing_weekly_offer` | internal helper | 20 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 380 | `TrackingCog._send_weekly_offer_message` | internal helper | 22 | 4 | 3 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 403 | `TrackingCog._log_background_error` | internal helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 410 | `TrackingCog._dm_user` | internal helper | 9 | 2 | 3 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 420 | `TrackingCog._validate_weekly_request_for_review` | internal helper | 49 | 12 | 8 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |
| 473 | `TrackingCog.start_background` | helper | 25 | 15 | 3 | 0 | 0 | 1 broad / 0 silent | **Focused review**: 1 broad catch |
| 499 | `TrackingCog.cog_unload` | helper | 9 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 509 | `TrackingCog._recover_contacting_claims` | internal helper | 127 | 25 | 11 | 3 | 0 | 2 broad / 0 silent | **Focused review**: 3 persistence calls; split candidate; 2 broad catches |
| 637 | `TrackingCog.on_config_reload` | helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 644 | `TrackingCog.user_in_weekly_process` | helper | 10 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 655 | `TrackingCog.weekly_reward_disabled` | helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 662 | `TrackingCog.disable_weekly_reward_for_current_week` | helper | 21 | 1 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 684 | `TrackingCog.enable_weekly_reward_for_current_week` | helper | 53 | 4 | 5 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 738 | `TrackingCog._notify_reenabled_weekly_claims` | internal helper | 27 | 4 | 4 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 769 | `TrackingCog._weekly_log_meta` | internal helper | 32 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 802 | `TrackingCog._weekly_detail_lines` | internal helper | 14 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 817 | `TrackingCog._log_weekly` | internal helper | 41 | 10 | 5 | 1 | 2 | 3 broad / 0 silent | **Routine**: 1 persistence call; 2 Discord operations; 3 broad catches |
| 859 | `TrackingCog._anti_farm_cfg` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 863 | `TrackingCog._anti_farm_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 866 | `TrackingCog._message_signature` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 871 | `TrackingCog._anti_farm_reason` | internal helper | 53 | 24 | 0 | 0 | 0 | 4 broad / 0 silent | **Focused review**: 4 broad catches |
| 925 | `TrackingCog._record_anti_farm_event` | internal helper | 52 | 19 | 5 | 1 | 2 | 4 broad / 0 silent | **Focused review**: 1 persistence call; 2 Discord operations; 4 broad catches |
| 982 | `TrackingCog.on_message` | event listener | 62 | 17 | 5 | 1 | 0 | none | **Focused review**: 1 persistence call |
| 1045 | `TrackingCog._activity_flush_loop` | internal helper | 10 | 5 | 4 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1056 | `TrackingCog.flush_activity_counts` | helper | 41 | 12 | 4 | 2 | 0 | 2 broad / 0 silent | **Routine**: 2 persistence calls; 2 broad catches |
| 1101 | `TrackingCog._handle_dm` | internal helper | 102 | 21 | 12 | 2 | 5 | 6 broad / 4 silent | **High attention**: 2 persistence calls; 5 Discord operations; split candidate; 6 broad catches; 4 silent recovery paths |
| 1204 | `TrackingCog._record_request` | internal helper | 136 | 20 | 23 | 2 | 7 | 8 broad / 4 silent | **High attention**: 2 persistence calls; 7 Discord operations; split candidate; 8 broad catches; 4 silent recovery paths |
| 1342 | `TrackingCog.handle_decline_confirm` | workflow handler | 68 | 9 | 12 | 3 | 2 | 2 broad / 2 silent | **Focused review**: 3 persistence calls; 2 Discord operations; 2 broad catches; 2 silent recovery paths |
| 1414 | `TrackingCog._weekly_loop` | internal helper | 43 | 8 | 8 | 3 | 0 | 2 broad / 1 silent | **Focused review**: 3 persistence calls; 2 broad catches; 1 silent recovery path |
| 1458 | `TrackingCog._weekly_recap_due` | internal helper | 12 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1471 | `TrackingCog._weekly_recap_loop` | internal helper | 11 | 4 | 5 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1483 | `TrackingCog._timeout_loop` | internal helper | 12 | 4 | 6 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1496 | `TrackingCog._process_timeouts` | internal helper | 49 | 6 | 9 | 3 | 1 | 1 broad / 0 silent | **Routine**: 3 persistence calls; 1 Discord operation; 1 broad catch |
| 1546 | `TrackingCog._update_weekly_streaks` | internal helper | 45 | 21 | 4 | 4 | 0 | 2 broad / 0 silent | **Focused review**: 4 persistence calls; 2 broad catches |
| 1592 | `TrackingCog._send_weekly_recap` | internal helper | 112 | 38 | 12 | 7 | 3 | 6 broad / 1 silent | **High attention**: 7 persistence calls; 3 Discord operations; split candidate; 6 broad catches; 1 silent recovery path |
| 1705 | `TrackingCog._ranked_rows_for_week` | internal helper | 46 | 11 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1752 | `TrackingCog._send_missing_weekly_recap_once` | internal helper | 28 | 7 | 6 | 2 | 0 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 broad catch |
| 1784 | `TrackingCog.run_weekly_job` | helper | 43 | 9 | 10 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1828 | `TrackingCog._contact_user_for_week` | internal helper | 149 | 23 | 19 | 5 | 2 | 5 broad / 0 silent | **Focused review**: 5 persistence calls; 2 Discord operations; split candidate; 5 broad catches |
| 1978 | `TrackingCog._contact_next_eligible` | internal helper | 62 | 10 | 8 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 2044 | `TrackingCog._format_deadline` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2047 | `TrackingCog._build_request_dm_text` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2051 | `TrackingCog._build_request_dm_message` | internal helper | 15 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2067 | `TrackingCog._build_reminder_text` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2071 | `TrackingCog._build_reminder_message` | internal helper | 14 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2086 | `TrackingCog._process_reminders` | internal helper | 129 | 23 | 13 | 5 | 1 | 5 broad / 0 silent | **Focused review**: 5 persistence calls; 1 Discord operation; split candidate; 5 broad catches |
| 2219 | `TrackingCog.get_top` | helper | 22 | 4 | 3 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2242 | `TrackingCog.get_member_stats` | helper | 50 | 20 | 3 | 1 | 0 | none | **Focused review**: 1 persistence call |
| 2293 | `TrackingCog.force_dm_for_user` | helper | 62 | 16 | 14 | 3 | 0 | none | **Focused review**: 3 persistence calls |
| 2356 | `TrackingCog.reset_current_week` | helper | 20 | 8 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2378 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `main.py`

23 definitions: 16 routine, 6 focused, 1 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 41 | `startup_log` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 45 | `_discord_login_retry_seconds` | internal helper | 8 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 55 | `_startup_error_retry_seconds` | internal helper | 8 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 65 | `_prepare_fresh_event_loop` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 71 | `_close_event_loop` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 78 | `_compact_startup_exception` | internal helper | 12 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 92 | `_is_discord_startup_rate_limit` | internal helper | 6 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 100 | `_run_preflight_database_check` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 108 | `_repair_legacy_turso_snowflakes` | internal helper | 57 | 15 | 2 | 0 | 0 | 1 broad / 0 silent | **Focused review**: 1 broad catch |
| 167 | `_close_runtime_storage` | internal helper | 59 | 15 | 8 | 1 | 0 | 7 broad / 0 silent | **Focused review**: 1 persistence call; 7 broad catches |
| 228 | `_install_storage_close_hook` | internal helper | 13 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 232 | `_install_storage_close_hook.close_with_storage_flush` | helper | 7 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 243 | `_database_path_usable` | internal helper | 13 | 3 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 258 | `resolve_db_path` | helper | 68 | 24 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 327 | `create_bot` | helper | 234 | 39 | 25 | 0 | 0 | 13 broad / 1 silent | **High attention**: split candidate; 13 broad catches; 1 silent recovery path |
| 355 | `create_bot.attach_command_correlation` | helper | 9 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 366 | `create_bot.on_application_command_completion` | helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 373 | `create_bot._load_cogs` | internal helper | 11 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 386 | `create_bot.on_ready` | helper | 99 | 21 | 17 | 0 | 0 | 8 broad / 1 silent | **Focused review**: 8 broad catches; 1 silent recovery path |
| 487 | `create_bot.on_disconnect` | helper | 31 | 8 | 4 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 520 | `create_bot.on_resumed` | helper | 19 | 7 | 4 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 540 | `create_bot.register_persistent_views` | helper | 10 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 562 | `run_bot_with_startup_backoff` | helper | 92 | 15 | 0 | 0 | 0 | 3 broad / 1 silent | **Focused review**: 3 broad catches; 1 silent recovery path |

### `services/backups.py`

2 definitions: 2 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 22 | `run_restore_drill` | helper | 61 | 7 | 3 | 6 | 0 | 1 broad / 0 silent | **Routine**: 6 persistence calls; 1 broad catch |
| 36 | `run_restore_drill._inspect` | internal helper | 12 | 4 | 0 | 4 | 0 | none | **Routine**: 4 persistence calls |

### `services/diagnostics.py`

2 definitions: 1 routine, 1 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 56 | `scan_permission_drift` | helper | 77 | 25 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: split candidate; 1 silent recovery path |
| 135 | `persist_permission_drift` | helper | 35 | 8 | 2 | 2 | 0 | none | **Routine**: 2 persistence calls |

### `services/impact.py`

2 definitions: 1 routine, 1 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 20 | `EngagementForecast.as_dict` | helper | 9 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 31 | `forecast_engagement` | helper | 47 | 20 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |

### `services/request_reviews.py`

4 definitions: 4 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 13 | `normalize_notification_mode` | helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 27 | `request_age` | helper | 29 | 13 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 58 | `rejection_breakdown` | helper | 10 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 70 | `compare_waves` | helper | 31 | 12 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `services/request_scheduling.py`

4 definitions: 4 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 18 | `ScheduledOpening.from_row` | helper | 16 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 35 | `ScheduledOpening.discord_time` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 39 | `opening_is_due` | helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 46 | `local_time_round_trip` | helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `services/request_validation.py`

3 definitions: 3 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 10 | `validate_level_id_shape` | helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 15 | `validate_showcase_url` | helper | 11 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 28 | `validation_requires_showcase` | helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

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
| 17 | `Config.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 23 | `Config.reload` | helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 28 | `Config.save` | helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 35 | `Config.get` | helper | 9 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 45 | `Config.get_str` | helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 51 | `Config.get_int` | helper | 11 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 63 | `Config.get_int_list` | helper | 23 | 9 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |

### `utils/config_schema.py`

7 definitions: 6 routine, 0 focused, 1 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 19 | `ConfigIssue.render` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 36 | `_integer` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 44 | `operations_settings` | helper | 31 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 77 | `_template_variables` | internal helper | 16 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 204 | `_is_discord_id` | internal helper | 9 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 215 | `_validate_id_list` | internal helper | 6 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 223 | `validate_config` | helper | 172 | 45 | 0 | 0 | 0 | none | **High attention**: split candidate |

### `utils/db.py`

70 definitions: 54 routine, 14 focused, 2 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 31 | `DictRow.__init__` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 35 | `DictRow.__getitem__` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 41 | `_row_get` | internal helper | 11 | 4 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 54 | `_normalize_row` | internal helper | 14 | 8 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 70 | `_normalize_rows` | internal helper | 2 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 74 | `_fetchall` | internal helper | 2 | 2 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 78 | `_jwt_payload` | internal helper | 12 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 92 | `_token_scope_names` | internal helper | 9 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 103 | `_looks_like_turso_platform_token` | internal helper | 12 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 117 | `_is_recoverable_remote_error` | internal helper | 22 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 141 | `_is_replica_corruption_error` | internal helper | 10 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 153 | `_requires_libsql_integer_workaround` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 159 | `_exact_libsql_params` | internal helper | 2 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 163 | `_legacy_libsql_value` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 169 | `_legacy_where_params` | internal helper | 24 | 10 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 268 | `Database.__init__` | internal helper | 19 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 288 | `Database._record_query_timing` | internal helper | 10 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 299 | `Database.query_timing_snapshot` | helper | 14 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 314 | `Database._close_connection_sync` | internal helper | 9 | 3 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 324 | `Database._reopen_connection_sync` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 330 | `Database._adapt_params` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 334 | `Database._execute_sync` | internal helper | 3 | 1 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 338 | `Database._execute_write_compat_sync` | internal helper | 9 | 6 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 348 | `Database._quarantine_replica_files_sync` | internal helper | 25 | 6 | 0 | 0 | 0 | 0 broad / 2 silent | **Focused review**: 2 silent recovery paths |
| 374 | `Database._rebuild_remote_replica_sync` | internal helper | 22 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 397 | `Database._open_connection_sync` | internal helper | 25 | 7 | 0 | 3 | 0 | 2 broad / 2 silent | **Focused review**: 3 persistence calls; 2 broad catches; 2 silent recovery paths |
| 423 | `Database._sync_remote_sync` | internal helper | 6 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 430 | `Database._sync_remote_with_retry_sync` | internal helper | 16 | 7 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 447 | `Database._try_pending_remote_sync_sync` | internal helper | 31 | 9 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 479 | `Database._commit_and_sync_sync` | internal helper | 11 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 491 | `Database._run_locked_with_retry` | internal helper | 48 | 12 | 6 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 540 | `Database.connect` | helper | 28 | 8 | 1 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 545 | `Database.connect._connect_and_migrate` | internal helper | 20 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 569 | `Database.close` | helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 576 | `Database.backup_to` | helper | 51 | 9 | 4 | 3 | 0 | 1 broad / 1 silent | **Focused review**: 3 persistence calls; 1 broad catch; 1 silent recovery path |
| 583 | `Database.backup_to._backup` | internal helper | 36 | 6 | 0 | 3 | 0 | 0 broad / 1 silent | **Focused review**: 3 persistence calls; 1 silent recovery path |
| 628 | `Database.restore_from` | helper | 91 | 15 | 1 | 8 | 0 | 2 broad / 3 silent | **Focused review**: 8 persistence calls; 2 broad catches; 3 silent recovery paths |
| 645 | `Database.restore_from._unlink_sidecars` | internal helper | 6 | 3 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 652 | `Database.restore_from._connect_current` | internal helper | 9 | 1 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 662 | `Database.restore_from._restore` | internal helper | 55 | 10 | 0 | 6 | 0 | 2 broad / 2 silent | **Focused review**: 6 persistence calls; 2 broad catches; 2 silent recovery paths |
| 720 | `Database._migrate_sync` | internal helper | 650 | 8 | 0 | 8 | 0 | none | **High attention**: 8 persistence calls; split candidate |
| 1371 | `Database._ensure_column_sync` | internal helper | 7 | 3 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1379 | `Database._normalize_weekly_dm_log_sync` | internal helper | 33 | 8 | 0 | 6 | 0 | none | **Routine**: 6 persistence calls |
| 1413 | `Database._init_ticket_sequences_sync` | internal helper | 27 | 8 | 0 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1441 | `Database.next_ticket_id` | helper | 25 | 3 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1442 | `Database.next_ticket_id._run` | internal helper | 22 | 3 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1467 | `Database.execute` | helper | 7 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1468 | `Database.execute._run` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1475 | `Database.execute_affected` | helper | 16 | 2 | 1 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1478 | `Database.execute_affected._run` | internal helper | 7 | 2 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1492 | `Database.execute_insert` | helper | 14 | 3 | 1 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1495 | `Database.execute_insert._run` | internal helper | 9 | 3 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1507 | `Database.execute_transaction` | helper | 26 | 6 | 1 | 1 | 0 | 2 broad / 1 silent | **Focused review**: 1 persistence call; 2 broad catches; 1 silent recovery path |
| 1518 | `Database.execute_transaction._run` | internal helper | 13 | 4 | 0 | 1 | 0 | 2 broad / 1 silent | **Focused review**: 1 persistence call; 2 broad catches; 1 silent recovery path |
| 1534 | `Database.set_runtime_setting` | helper | 7 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1542 | `Database.get_runtime_setting` | helper | 11 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1554 | `Database.repair_legacy_snowflake_precision` | helper | 134 | 31 | 1 | 8 | 0 | 2 broad / 3 silent | **High attention**: 8 persistence calls; split candidate; 2 broad catches; 3 silent recovery paths |
| 1575 | `Database.repair_legacy_snowflake_precision._mapping` | internal helper | 18 | 9 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 1598 | `Database.repair_legacy_snowflake_precision._run` | internal helper | 88 | 22 | 0 | 8 | 0 | 2 broad / 2 silent | **Focused review**: 8 persistence calls; 2 broad catches; 2 silent recovery paths |
| 1689 | `Database.health_snapshot` | helper | 14 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1704 | `Database.sync_remote` | helper | 16 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1709 | `Database.sync_remote._run` | internal helper | 9 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1721 | `Database.executemany` | helper | 10 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1724 | `Database.executemany._run` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1732 | `Database.fetchone` | helper | 13 | 5 | 1 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1733 | `Database.fetchone._run` | internal helper | 10 | 5 | 0 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1746 | `Database.fetchone_local` | helper | 20 | 5 | 1 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1754 | `Database.fetchone_local._run` | internal helper | 10 | 5 | 0 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1767 | `Database.fetchall` | helper | 13 | 5 | 1 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1768 | `Database.fetchall._run` | internal helper | 10 | 5 | 0 | 3 | 0 | none | **Routine**: 3 persistence calls |

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

11 definitions: 7 routine, 3 focused, 1 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 17 | `_redact_secrets` | internal helper | 18 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 37 | `_compact_error_message` | internal helper | 26 | 18 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 65 | `_strip_trace_context` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 73 | `_dedupe_key` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 79 | `_persist_incident` | internal helper | 21 | 6 | 2 | 2 | 0 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 broad catch |
| 102 | `_unwrap_command_error` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 116 | `_command_error_record` | internal helper | 13 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 131 | `log_error` | helper | 81 | 19 | 5 | 1 | 3 | 8 broad / 4 silent | **High attention**: 1 persistence call; 3 Discord operations; 8 broad catches; 4 silent recovery paths |
| 213 | `setup_global_error_handlers` | helper | 23 | 3 | 3 | 0 | 1 | 1 broad / 1 silent | **Focused review**: 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 215 | `setup_global_error_handlers.on_application_command_error` | helper | 17 | 3 | 2 | 0 | 1 | 1 broad / 1 silent | **Focused review**: 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 234 | `setup_global_error_handlers.on_error` | helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/gd_validation.py`

17 definitions: 14 routine, 2 focused, 1 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 17 | `_as_int` | internal helper | 7 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 26 | `_as_bool` | internal helper | 7 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 35 | `_kv_pairs` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 40 | `_boomlings_creator_map` | internal helper | 7 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 49 | `_demon_difficulty` | internal helper | 8 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 59 | `_classic_difficulty` | internal helper | 9 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 70 | `_length_name` | internal helper | 9 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 81 | `_provider_error` | internal helper | 22 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 105 | `_retry_after_seconds` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 112 | `_read_provider_text` | internal helper | 27 | 7 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 141 | `_http_error` | internal helper | 22 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 165 | `parse_gdbrowser_level` | helper | 41 | 20 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 208 | `parse_boomlings_level` | helper | 61 | 23 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 271 | `fetch_gdbrowser_level` | helper | 27 | 8 | 1 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 300 | `fetch_boomlings_level` | helper | 37 | 5 | 1 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 339 | `combine_level_validation` | helper | 80 | 45 | 0 | 0 | 0 | none | **High attention**: split candidate |
| 421 | `validation_notice` | helper | 20 | 10 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/keepalive.py`

17 definitions: 16 routine, 1 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 41 | `set_keepalive_status` | helper | 26 | 11 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 69 | `get_keepalive_status` | helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 74 | `set_public_bot_metrics` | helper | 35 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 111 | `set_public_release_data` | helper | 19 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 132 | `_public_state_label` | internal helper | 14 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 148 | `get_public_bot_payload` | helper | 52 | 15 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 202 | `get_public_releases_payload` | helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 212 | `_response_for_path` | internal helper | 27 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 242 | `_HealthHandler._health_response` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 245 | `_HealthHandler._send_health_headers` | internal helper | 15 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 261 | `_HealthHandler.do_GET` | helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 270 | `_HealthHandler.do_HEAD` | helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 274 | `_HealthHandler.log_message` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 278 | `start_keepalive_thread` | helper | 19 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 286 | `start_keepalive_thread._run` | internal helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 299 | `_handle` | internal helper | 14 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 314 | `start_keepalive` | helper | 18 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/mentions.py`

3 definitions: 3 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 6 | `no_mentions` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 10 | `user_mentions` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 14 | `user_and_role_mentions` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/outbox.py`

12 definitions: 10 routine, 2 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 30 | `DiscordOutbox.__init__` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 35 | `DiscordOutbox.enqueue` | helper | 42 | 11 | 2 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 78 | `DiscordOutbox.recover_stale` | helper | 7 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 86 | `DiscordOutbox.process_once` | helper | 15 | 3 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 102 | `DiscordOutbox._process_row` | internal helper | 71 | 15 | 7 | 4 | 0 | 1 broad / 0 silent | **Focused review**: 4 persistence calls; 1 broad catch |
| 175 | `DiscordOutbox._terminal_failure` | internal helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 182 | `DiscordOutbox._payload` | internal helper | 6 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 190 | `DiscordOutbox._embed` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 195 | `DiscordOutbox._mentions` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 202 | `DiscordOutbox._channel` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 208 | `DiscordOutbox._guild` | internal helper | 5 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 214 | `DiscordOutbox._deliver` | internal helper | 52 | 22 | 12 | 0 | 9 | none | **Focused review**: 9 Discord operations |

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
| 13 | `_forum_entries` | internal helper | 10 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 25 | `collect_forum_required_rules` | helper | 16 | 5 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 43 | `apply_forum_required_rules` | helper | 20 | 7 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 65 | `load_runtime_config_overrides` | helper | 10 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 77 | `persist_server_icon_config` | helper | 2 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 81 | `persist_forum_required_rules` | helper | 2 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |

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
| 30 | `TranscriptRequestView.__init__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 34 | `TranscriptRequestView.approve` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 46 | `TranscriptRequestView.deny` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 59 | `ReleaseApprovalView.__init__` | internal helper | 18 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 78 | `ReleaseApprovalView.approve` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 89 | `ReleaseApprovalView.reject` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 102 | `TicketClosePromptView.__init__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 106 | `TicketClosePromptView.yes` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 118 | `TicketClosePromptView.no` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 131 | `_HelpMenuSelect.__init__` | internal helper | 62 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 194 | `_HelpMenuSelect.callback` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 207 | `HelpMenuView.__init__` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 213 | `_FormerMemberHelpSelect.__init__` | internal helper | 22 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 236 | `_FormerMemberHelpSelect.callback` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 249 | `FormerMemberHelpView.__init__` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 255 | `BanInfoGiveInfoView.__init__` | internal helper | 10 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 266 | `BanInfoGiveInfoView.give_info` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 279 | `TrackingDeclineConfirmView.__init__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 283 | `TrackingDeclineConfirmView.yes` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 295 | `TrackingDeclineConfirmView.no` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 308 | `LevelRequestButtonView.__init__` | internal helper | 10 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 319 | `LevelRequestButtonView.request` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 332 | `LevelRequestReviewView.__init__` | internal helper | 11 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 344 | `LevelRequestReviewView._make_callback` | internal helper | 12 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 345 | `LevelRequestReviewView._make_callback._callback` | internal helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |

### `utils/workflows.py`

9 definitions: 9 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 15 | `new_correlation_id` | helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 22 | `current_correlation_id` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 26 | `begin_workflow_context` | helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 33 | `clear_workflow_context` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 38 | `workflow_context` | helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 54 | `WorkflowStateMachine.__init__` | internal helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 60 | `WorkflowStateMachine.allows` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 63 | `WorkflowStateMachine.require` | helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 88 | `record_workflow_event` | helper | 29 | 6 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |

## Residual Operational Risk

The static review cannot simulate Discord permissions, role hierarchy, deleted live messages, third-party API outages, or a process termination in the narrow interval between a Discord action and its database compensation. Those cases are contained by durable states, repair commands, idempotent checks, logging, and startup reconciliation, but should still be exercised after deployment.

The largest functions are concentrated in configuration diagnostics, impact aggregation, request repair, request form orchestration, daily summaries, and ticket closure. They are covered by focused checks and are valid today, but they are the best future refactoring targets because each coordinates several external boundaries.

## Verification Gate

```text
Python compileall                     PASS
Ruff correctness and bug checks      PASS
Pytest                                PASS (140 tests)
Bandit medium/high security scan     PASS
Production dependency audit          PASS
Discord modal serialization          PASS
Configuration JSON parse             PASS
```
