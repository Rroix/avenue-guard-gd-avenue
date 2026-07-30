# Avenue Guard Function-by-Function Diagnosis

**Audit date:** 2026-07-29  
**Runtime scope:** 25 Python modules, 872 definitions, 20,879 physical lines
**Method:** AST inventory, per-function control-flow scoring, interaction-order review, persistence/Discord I/O mapping, compile, tests, Ruff, Bandit, and dependency audit

## Reading This Report

Every runtime function, method, nested callback, and modal handler has one row below. The attention label is a review priority, not proof of a defect: orchestration code and schema declarations are naturally larger. Complexity is a deterministic branch score used to find code that deserves focused tests.

- **Routine:** compact control flow with no static risk signal.
- **Focused review:** a long path, broad recovery, interaction timing, or several I/O boundaries.
- **High attention:** very large/branch-heavy orchestration or several silent recovery paths.

## Executive Diagnosis

- All runtime modules parse and compile.
- The complete automated suite passes: 103 tests.
- Ruff's correctness and bug checks pass.
- Bandit reports no medium or high security findings.
- The production dependency set has no known published vulnerabilities.
- Slash commands and support component handlers acknowledge interactions before slow work, except modal-first commands that must query the local replica or open the modal as their initial response.
- Turso-backed workflow state, request waves, tickets, tracking, summaries, runtime settings, and help submissions remain restart-persistent.

## Current Review Fixes

- Pinned the Render runtime to Python 3.13 and refreshed production dependency bounds.
- Replaced implicit event-loop lookup during startup with explicit loop ownership and cleanup.
- Restored the Discord presence intent required for accurate daily online-member summaries.
- Added Turso connection recovery after a post-commit sync leaves a transaction invalid.
- Added a retry path and same-process lock for daily summaries without weakening database idempotency.
- Rebuilt missing weekly reward sessions and deadlines when an admin re-enables the current week.
- Corrected request edit grace, scheduled-close timestamps, DST validation, and HTTP-task cleanup.
- Serialized shared configuration writes across resync, forum, and server-icon operations.
- Added native Discord choices, lengths, and numeric bounds to ambiguous slash-command options.
- Moved backup compression and restore extraction off the event loop and streamed transcript generation.
- Explicitly closed temporary SQLite backup/validation handles and added strict resource/deprecation checks.
- Bounded long-lived caches and used monotonic clocks for cooldowns and error suppression.
- Serialized release decisions and help-ticket creation, and hardened review-access role failures.
- Allowed all configured auto-response matches to run when `first_match_only` is disabled.
- Tightened Discord color validation and silenced harmless keepalive client disconnect noise.
- Removed dead code and refreshed public documentation, maintenance scripts, and regression coverage.

## Attention Summary

| Classification | Definitions |
|---|---:|
| Routine | 702 |
| Focused review | 139 |
| High attention | 31 |

## Function Inventory

### `cogs/Background.py`

85 definitions: 70 routine, 13 focused, 2 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 32 | `_day_key` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 36 | `_parse_hhmm` | internal helper | 10 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 47 | `_fmt_minutes` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 55 | `_fmt_num` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 58 | `_fmt_delta` | internal helper | 8 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 67 | `_fmt_percent` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 101 | `BackgroundCog.__init__` | internal helper | 13 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 115 | `BackgroundCog.cog_unload` | helper | 18 | 5 | 0 | 0 | 0 | 2 broad / 2 silent | **Focused review**: 2 broad catches; 2 silent recovery paths |
| 134 | `BackgroundCog.start_background` | helper | 60 | 22 | 10 | 0 | 0 | 7 broad / 0 silent | **Focused review**: 7 broad catches |
| 195 | `BackgroundCog.on_config_reload` | helper | 45 | 21 | 0 | 0 | 0 | 8 broad / 4 silent | **High attention**: 8 broad catches; 4 silent recovery paths |
| 244 | `BackgroundCog._excluded_channels` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 252 | `BackgroundCog._status_rotation_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 255 | `BackgroundCog._status_rotation_interval` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 258 | `BackgroundCog._status_list` | internal helper | 14 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 273 | `BackgroundCog._server_icon_rotation_enabled` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 277 | `BackgroundCog._server_icon_interval` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 281 | `BackgroundCog._server_icon_urls` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 286 | `BackgroundCog._database_backup_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 289 | `BackgroundCog._database_backup_interval_seconds` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 293 | `BackgroundCog._server_icon_current_index` | internal helper | 8 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 302 | `BackgroundCog._server_icon_candidate_indices` | internal helper | 14 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 317 | `BackgroundCog._looks_like_server_icon_image` | internal helper | 10 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 328 | `BackgroundCog._assert_public_server_icon_url` | internal helper | 26 | 12 | 1 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 355 | `BackgroundCog._download_server_icon` | internal helper | 40 | 18 | 2 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 396 | `BackgroundCog._detect_current_server_icon_index` | internal helper | 18 | 8 | 2 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 415 | `BackgroundCog._persist_server_icon_state` | internal helper | 6 | 2 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 422 | `BackgroundCog._remember_server_icon_error` | internal helper | 7 | 3 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 430 | `BackgroundCog._config_write_lock` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 438 | `BackgroundCog.rotate_server_icon_once` | helper | 15 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 454 | `BackgroundCog._rotate_server_icon_once_locked` | internal helper | 70 | 19 | 6 | 0 | 1 | 2 broad / 0 silent | **Focused review**: 1 Discord operation; 2 broad catches |
| 525 | `BackgroundCog._render_status_text` | internal helper | 55 | 14 | 5 | 3 | 0 | 3 broad / 0 silent | **Routine**: 3 persistence calls; 3 broad catches |
| 528 | `BackgroundCog._render_status_text._SafeDict.__missing__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 581 | `BackgroundCog._daily_summary_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 584 | `BackgroundCog._daily_summary_channel_id` | internal helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 590 | `BackgroundCog._daily_reset_after_report` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 593 | `BackgroundCog._daily_summary_due` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 600 | `BackgroundCog._daily_summary_already_sent` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 607 | `BackgroundCog._record_daily_summary_sent` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 613 | `BackgroundCog._voice_sessions_from_guild` | internal helper | 9 | 6 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 623 | `BackgroundCog._stats_payload` | internal helper | 23 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 647 | `BackgroundCog._stats_from_payload` | internal helper | 16 | 12 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 664 | `BackgroundCog._load_daily_stats` | internal helper | 11 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 676 | `BackgroundCog._persist_daily_stats` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 683 | `BackgroundCog._persist_current_day` | internal helper | 9 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 693 | `BackgroundCog._rollover_boundary_ts` | internal helper | 6 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 700 | `BackgroundCog._add_voice_until` | internal helper | 8 | 4 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 709 | `BackgroundCog._track_background_persist` | internal helper | 18 | 5 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 712 | `BackgroundCog._track_background_persist._done` | internal helper | 13 | 5 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 728 | `BackgroundCog._rollover_if_needed` | internal helper | 28 | 7 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 761 | `BackgroundCog.on_message` | event listener | 14 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 777 | `BackgroundCog.on_message_edit` | event listener | 11 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 790 | `BackgroundCog.on_message_delete` | event listener | 11 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 803 | `BackgroundCog.on_reaction_add` | event listener | 11 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 816 | `BackgroundCog.on_member_join` | event listener | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 825 | `BackgroundCog.on_member_remove` | event listener | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 834 | `BackgroundCog.on_member_ban` | event listener | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 843 | `BackgroundCog.on_member_unban` | event listener | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 852 | `BackgroundCog.on_member_update` | event listener | 10 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 864 | `BackgroundCog.on_voice_state_update` | event listener | 23 | 14 | 1 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 889 | `BackgroundCog.on_application_command_completion` | event listener | 14 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 905 | `BackgroundCog.on_application_command_error` | event listener | 15 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 925 | `BackgroundCog.update_snapshot` | background loop | 22 | 11 | 5 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |
| 948 | `BackgroundCog._log_snapshot_failure` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 952 | `BackgroundCog._before_snapshot` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 956 | `BackgroundCog._snapshot_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 960 | `BackgroundCog.database_backup` | background loop | 33 | 13 | 5 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 995 | `BackgroundCog._before_database_backup` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 999 | `BackgroundCog._database_backup_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1003 | `BackgroundCog.rotate_status` | background loop | 35 | 10 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1040 | `BackgroundCog._before_rotate` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1044 | `BackgroundCog._rotate_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1048 | `BackgroundCog.rotate_server_icon` | background loop | 23 | 11 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1073 | `BackgroundCog._before_server_icon_rotate` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1077 | `BackgroundCog._server_icon_rotate_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1080 | `BackgroundCog._start_daily_report_loop` | internal helper | 14 | 5 | 0 | 0 | 0 | 2 broad / 2 silent | **Focused review**: 2 broad catches; 2 silent recovery paths |
| 1095 | `BackgroundCog._top_channel_lines` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1103 | `BackgroundCog._top_member_lines` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1111 | `BackgroundCog._top_command_lines` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1119 | `BackgroundCog._summary_color` | internal helper | 8 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1128 | `BackgroundCog._send_daily_summary_for_day` | internal helper | 7 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1136 | `BackgroundCog._send_daily_summary_for_day_locked` | internal helper | 173 | 33 | 11 | 0 | 3 | 7 broad / 3 silent | **High attention**: 3 Discord operations; split candidate; 7 broad catches; 3 silent recovery paths |
| 1311 | `BackgroundCog.daily_report` | background loop | 12 | 4 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1325 | `BackgroundCog._before_daily` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1329 | `BackgroundCog._daily_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1332 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Commands.py`

132 definitions: 96 routine, 29 focused, 7 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 74 | `_fmt_num` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 81 | `_fmt_percent` | internal helper | 8 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 91 | `_ticket_status_key` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 103 | `_ticket_status_label` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 108 | `AdminDashboardView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 114 | `AdminDashboardView._show` | internal helper | 17 | 5 | 7 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 133 | `AdminDashboardView.overview` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 137 | `AdminDashboardView.config` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 141 | `AdminDashboardView.repairs` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 145 | `AdminDashboardView.refresh` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 150 | `CommandsCog.__init__` | internal helper | 78 | 13 | 5 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 210 | `CommandsCog.__init__.resync` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 214 | `CommandsCog.__init__.restart` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 218 | `CommandsCog.__init__.dance` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 222 | `CommandsCog.__init__.rps` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 226 | `CommandsCog.__init__.gambling` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 229 | `CommandsCog._in_allowed_guild` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 232 | `CommandsCog._defer` | internal helper | 7 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 240 | `CommandsCog._send` | internal helper | 4 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 246 | `CommandsCog._claim_fun_cooldown` | internal helper | 28 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 275 | `CommandsCog._log_admin_action` | internal helper | 19 | 5 | 2 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 295 | `CommandsCog._impact_owner_ids` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 298 | `CommandsCog._is_impact_owner_ctx` | internal helper | 7 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 306 | `CommandsCog._is_release_owner_ctx` | internal helper | 8 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 315 | `CommandsCog._backup_channel_id` | internal helper | 7 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 323 | `CommandsCog._backup_local_dir` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 327 | `CommandsCog._restore_upload_dir` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 332 | `CommandsCog._backup_retention_count` | internal helper | 6 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 339 | `CommandsCog._prune_local_backups` | internal helper | 14 | 4 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 354 | `CommandsCog._database_path` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 357 | `CommandsCog._database_storage_note` | internal helper | 36 | 21 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 394 | `CommandsCog._zip_backup_file` | internal helper | 7 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 402 | `CommandsCog._post_database_backup` | internal helper | 75 | 13 | 11 | 2 | 3 | 2 broad / 1 silent | **Focused review**: 2 persistence calls; 3 Discord operations; 2 broad catches; 1 silent recovery path |
| 478 | `CommandsCog._restore_safe_filename` | internal helper | 4 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 483 | `CommandsCog._save_restore_attachment` | internal helper | 17 | 6 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 501 | `CommandsCog._extract_sqlite_restore_file` | internal helper | 26 | 12 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 528 | `CommandsCog._validate_restore_database` | internal helper | 23 | 7 | 1 | 4 | 0 | none | **Routine**: 4 persistence calls |
| 529 | `CommandsCog._validate_restore_database._run` | internal helper | 20 | 7 | 0 | 4 | 0 | none | **Routine**: 4 persistence calls |
| 552 | `CommandsCog._impact_scalar` | internal helper | 11 | 6 | 1 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 564 | `CommandsCog._impact_float` | internal helper | 11 | 6 | 1 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 576 | `CommandsCog._impact_group_counts` | internal helper | 43 | 5 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 620 | `CommandsCog._impact_daily_totals` | internal helper | 118 | 31 | 1 | 1 | 0 | 5 broad / 4 silent | **High attention**: 1 persistence call; split candidate; 5 broad catches; 4 silent recovery paths |
| 739 | `CommandsCog._impact_window_rows` | internal helper | 14 | 7 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 754 | `CommandsCog._impact_window_sum` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 758 | `CommandsCog._impact_window_average` | internal helper | 5 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 764 | `CommandsCog._impact_percent_change` | internal helper | 9 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 774 | `CommandsCog._impact_forecast` | internal helper | 61 | 17 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 836 | `CommandsCog._collect_impact_metrics` | internal helper | 325 | 39 | 47 | 3 | 0 | 1 broad / 0 silent | **High attention**: 3 persistence calls; split candidate; 1 broad catch |
| 1162 | `CommandsCog._impact_metric_rows` | internal helper | 46 | 19 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1165 | `CommandsCog._impact_metric_rows.add` | helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1209 | `CommandsCog._impact_csv` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1216 | `CommandsCog._impact_daily_csv` | internal helper | 23 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1240 | `CommandsCog._impact_breakdown_csv` | internal helper | 18 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1259 | `CommandsCog._impact_markdown` | internal helper | 97 | 4 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1357 | `CommandsCog._impact_report_embed` | internal helper | 62 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1420 | `CommandsCog._impact_files` | internal helper | 14 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1435 | `CommandsCog.bot_impact` | helper | 72 | 17 | 17 | 1 | 2 | 4 broad / 0 silent | **Focused review**: 1 persistence call; 2 Discord operations; 4 broad catches |
| 1508 | `CommandsCog.bot_release` | helper | 58 | 6 | 10 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1567 | `CommandsCog.bot_releases` | helper | 68 | 19 | 9 | 0 | 1 | 1 broad / 0 silent | **Focused review**: 1 Discord operation; 1 broad catch |
| 1636 | `CommandsCog.bot_backup` | helper | 18 | 5 | 10 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1655 | `CommandsCog.bot_restore` | helper | 104 | 24 | 19 | 2 | 1 | 4 broad / 0 silent | **Focused review**: 2 persistence calls; 1 Discord operation; split candidate; 4 broad catches |
| 1760 | `CommandsCog.bot_storage` | helper | 60 | 14 | 7 | 2 | 1 | none | **Routine**: 2 persistence calls; 1 Discord operation |
| 1821 | `CommandsCog._is_admin_ctx` | internal helper | 6 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1828 | `CommandsCog._is_mod_ctx` | internal helper | 7 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1836 | `CommandsCog._request_reviewer_role_ids` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1840 | `CommandsCog._is_request_staff_ctx` | internal helper | 13 | 7 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1854 | `CommandsCog._server_icon_status_embed` | internal helper | 47 | 20 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1902 | `CommandsCog._notify_background_config_reload` | internal helper | 8 | 3 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 1911 | `CommandsCog._server_icon_operation_lock` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1916 | `CommandsCog._config_write_lock` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1924 | `CommandsCog._save_server_icon_config` | internal helper | 21 | 5 | 5 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 1946 | `CommandsCog.server_icon_status` | helper | 7 | 3 | 5 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 1954 | `CommandsCog.server_icon_mode` | helper | 28 | 6 | 7 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 1983 | `CommandsCog.server_icon_add` | helper | 29 | 9 | 10 | 0 | 7 | none | **Routine**: 7 Discord operations |
| 2013 | `CommandsCog.server_icon_replace` | helper | 37 | 12 | 9 | 0 | 6 | none | **Routine**: 6 Discord operations |
| 2051 | `CommandsCog.server_icon_remove` | helper | 35 | 11 | 7 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 2087 | `CommandsCog.server_icon_set` | helper | 28 | 7 | 9 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2116 | `CommandsCog.server_icon_next` | helper | 14 | 5 | 8 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2131 | `CommandsCog._resolve_member` | internal helper | 15 | 5 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 2147 | `CommandsCog._task_state` | internal helper | 13 | 8 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2161 | `CommandsCog._count_db` | internal helper | 6 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 2168 | `CommandsCog._dashboard_issues` | internal helper | 86 | 32 | 1 | 0 | 0 | 1 broad / 0 silent | **High attention**: split candidate; 1 broad catch |
| 2255 | `CommandsCog._admin_dashboard_embed` | internal helper | 123 | 32 | 9 | 2 | 0 | 2 broad / 0 silent | **High attention**: 2 persistence calls; split candidate; 2 broad catches |
| 2379 | `CommandsCog.bot_dashboard` | helper | 8 | 3 | 6 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2390 | `CommandsCog.bot_health` | helper | 89 | 22 | 12 | 3 | 2 | 4 broad / 0 silent | **Focused review**: 3 persistence calls; 2 Discord operations; 4 broad catches |
| 2399 | `CommandsCog.bot_health._count` | internal helper | 6 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 2432 | `CommandsCog.bot_health._task_state` | internal helper | 13 | 8 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2480 | `CommandsCog.bot_doctor` | helper | 139 | 49 | 6 | 0 | 2 | none | **High attention**: 2 Discord operations; split candidate |
| 2495 | `CommandsCog.bot_doctor.channel_perm_report` | helper | 16 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2620 | `CommandsCog._template_variables` | internal helper | 12 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2633 | `CommandsCog._request_template_allowed_vars` | internal helper | 80 | 1 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 2714 | `CommandsCog._looks_like_color_value` | internal helper | 23 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2738 | `CommandsCog._validate_request_templates` | internal helper | 69 | 28 | 0 | 0 | 0 | none | **Focused review**: split candidate |
| 2759 | `CommandsCog._validate_request_templates.check_text` | helper | 8 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2768 | `CommandsCog._validate_request_templates.walk` | helper | 26 | 18 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 2808 | `CommandsCog.bot_config_check` | helper | 399 | 104 | 5 | 0 | 2 | 2 broad / 0 silent | **High attention**: 2 Discord operations; split candidate; 2 broad catches |
| 2819 | `CommandsCog.bot_config_check.check_channel` | helper | 13 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2833 | `CommandsCog.bot_config_check.check_role` | helper | 9 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2896 | `CommandsCog.bot_config_check.check_hhmm` | helper | 7 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2915 | `CommandsCog.bot_config_check.check_number` | helper | 22 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3208 | `CommandsCog._parse_snowflake_arg` | internal helper | 8 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 3217 | `CommandsCog._request_change_lines` | internal helper | 22 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3226 | `CommandsCog._request_change_lines.short` | helper | 5 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3240 | `CommandsCog.requests_history` | helper | 74 | 13 | 9 | 3 | 2 | 2 broad / 0 silent | **Focused review**: 3 persistence calls; 2 Discord operations; 2 broad catches |
| 3315 | `CommandsCog.requests_repair` | helper | 36 | 13 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3352 | `CommandsCog.requests_pending` | helper | 129 | 29 | 8 | 3 | 2 | 1 broad / 0 silent | **Focused review**: 3 persistence calls; 2 Discord operations; split candidate; 1 broad catch |
| 3436 | `CommandsCog.requests_pending.request_name` | helper | 8 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 3484 | `CommandsCog.tracking_top` | helper | 60 | 20 | 8 | 1 | 2 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 2 Discord operations; 1 broad catch; 1 silent recovery path |
| 3546 | `CommandsCog.tracking_me` | helper | 44 | 12 | 8 | 1 | 2 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 2 Discord operations; 1 broad catch; 1 silent recovery path |
| 3591 | `CommandsCog.tracking_force_dm` | helper | 22 | 5 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3614 | `CommandsCog.tracking_reset` | helper | 17 | 5 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3632 | `CommandsCog.tracking_disable_reward` | helper | 21 | 5 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3654 | `CommandsCog.tracking_enable_reward` | helper | 26 | 6 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3682 | `CommandsCog.ticket_close` | helper | 27 | 9 | 10 | 1 | 6 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 6 Discord operations; 1 broad catch; 1 silent recovery path |
| 3710 | `CommandsCog.ticket_status` | helper | 73 | 16 | 14 | 2 | 7 | 1 broad / 1 silent | **Focused review**: 2 persistence calls; 7 Discord operations; 1 broad catch; 1 silent recovery path |
| 3784 | `CommandsCog.ticket_transcripts` | helper | 65 | 19 | 7 | 1 | 4 | none | **Focused review**: 1 persistence call; 4 Discord operations |
| 3851 | `CommandsCog._parse_channel_id` | internal helper | 10 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 3862 | `CommandsCog._configured_forum_entries` | internal helper | 29 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3892 | `CommandsCog._resolve_forum_entry` | internal helper | 41 | 15 | 0 | 0 | 0 | 4 broad / 3 silent | **Focused review**: 4 broad catches; 3 silent recovery paths |
| 3934 | `CommandsCog.forum_required_word` | helper | 118 | 31 | 16 | 0 | 9 | 3 broad / 0 silent | **High attention**: 9 Discord operations; split candidate; 3 broad catches |
| 4054 | `CommandsCog._resync` | internal helper | 34 | 9 | 11 | 0 | 3 | 3 broad / 0 silent | **Routine**: 3 Discord operations; 3 broad catches |
| 4090 | `CommandsCog._restart` | internal helper | 16 | 4 | 7 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 4108 | `CommandsCog._dance` | internal helper | 7 | 3 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 4117 | `CommandsCog._rps` | internal helper | 97 | 23 | 15 | 0 | 8 | 3 broad / 1 silent | **Focused review**: 8 Discord operations; 3 broad catches; 1 silent recovery path |
| 4130 | `CommandsCog._rps.outcome` | helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 4137 | `CommandsCog._rps.RPSView.__init__` | internal helper | 12 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 4150 | `CommandsCog._rps.RPSView._make_callback` | internal helper | 62 | 18 | 12 | 0 | 5 | 3 broad / 1 silent | **Focused review**: 5 Discord operations; 3 broad catches; 1 silent recovery path |
| 4151 | `CommandsCog._rps.RPSView._make_callback._cb` | internal helper | 60 | 18 | 12 | 0 | 5 | 3 broad / 1 silent | **Focused review**: 5 Discord operations; 3 broad catches; 1 silent recovery path |
| 4215 | `CommandsCog._rps_get_streak` | internal helper | 8 | 2 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 4224 | `CommandsCog._rps_update_streak` | internal helper | 28 | 4 | 4 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 4254 | `CommandsCog._gambling` | internal helper | 58 | 21 | 9 | 0 | 6 | 3 broad / 2 silent | **Focused review**: 6 Discord operations; 3 broad catches; 2 silent recovery paths |
| 4313 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Help.py`

170 definitions: 143 routine, 21 focused, 6 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 27 | `_format_duration` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 48 | `_ticket_status_key` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 60 | `_ticket_status_label` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 65 | `HelpSessionControlView.__init__` | internal helper | 21 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 87 | `HelpSessionControlView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 94 | `HelpSessionControlView.start` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 100 | `HelpSessionControlView.back` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 106 | `HelpSessionControlView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 112 | `HelpSessionControlView.start_over` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 119 | `HelpSubmissionPreviewView.__init__` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 126 | `HelpSubmissionPreviewView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 133 | `HelpSubmissionPreviewView.submit` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 139 | `HelpSubmissionPreviewView.edit` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 145 | `HelpSubmissionPreviewView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 151 | `HelpSubmissionPreviewView.start_over` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 158 | `HelpTicketTopicView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 164 | `HelpTicketTopicView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 170 | `HelpTicketTopicView._make_topic_callback` | internal helper | 6 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 171 | `HelpTicketTopicView._make_topic_callback._callback` | internal helper | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 178 | `HelpTicketTopicView.moderation` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 182 | `HelpTicketTopicView.requests` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 186 | `HelpTicketTopicView.server` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 190 | `HelpTicketTopicView.other` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 194 | `HelpTicketTopicView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 200 | `HelpTicketTopicView.back` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 206 | `HelpTicketTopicView.start_over` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 213 | `FaqPageView.__init__` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 226 | `FaqPageView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 233 | `FaqPageView.previous` | UI callback | 7 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 242 | `FaqPageView.next` | UI callback | 7 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 251 | `FaqPageView.back` | UI callback | 3 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 257 | `PartnershipConfirmView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 263 | `PartnershipConfirmView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 270 | `PartnershipConfirmView.confirm` | UI callback | 3 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 275 | `PartnershipConfirmView.cancel` | UI callback | 3 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 281 | `BanInfoModal.__init__` | internal helper | 64 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 346 | `BanInfoModal.callback` | helper | 13 | 6 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 362 | `BanInfoConfirmView.__init__` | internal helper | 14 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 377 | `BanInfoConfirmView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 384 | `BanInfoConfirmView.confirm` | UI callback | 8 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 394 | `BanInfoConfirmView.cancel` | UI callback | 6 | 3 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 403 | `TicketSatisfactionView.__init__` | internal helper | 18 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 422 | `TicketSatisfactionView._make_callback` | internal helper | 6 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 423 | `TicketSatisfactionView._make_callback._callback` | internal helper | 4 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 433 | `HelpCog.__init__` | internal helper | 21 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 455 | `HelpCog.cog_unload` | helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 459 | `HelpCog.start_background` | helper | 19 | 7 | 6 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |
| 479 | `HelpCog.on_config_reload` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 482 | `HelpCog._member_from_actor` | internal helper | 12 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 495 | `HelpCog._resolve_member` | internal helper | 16 | 5 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 512 | `HelpCog._resolve_dm_recipient` | internal helper | 30 | 10 | 2 | 0 | 2 | 1 broad / 0 silent | **Routine**: 2 Discord operations; 1 broad catch |
| 543 | `HelpCog._help_color` | internal helper | 11 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 555 | `HelpCog._help_embed` | internal helper | 10 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 566 | `HelpCog._delete_interaction_source` | internal helper | 13 | 4 | 2 | 0 | 1 | 2 broad / 2 silent | **Focused review**: 1 Discord operation; 2 broad catches; 2 silent recovery paths |
| 580 | `HelpCog._ack_and_delete_source` | internal helper | 5 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 586 | `HelpCog._respond_interaction` | internal helper | 4 | 2 | 2 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 591 | `HelpCog._cooldowns` | internal helper | 7 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 599 | `HelpCog._submission_label` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 606 | `HelpCog._submission_prefix` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 613 | `HelpCog._submission_code` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 616 | `HelpCog._attachment_data` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 627 | `HelpCog._merge_attachments` | internal helper | 11 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 639 | `HelpCog._attachments_text` | internal helper | 13 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 653 | `HelpCog._has_attachments` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 657 | `HelpCog._short_text` | internal helper | 5 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 663 | `HelpCog._embed_char_count` | internal helper | 7 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 671 | `HelpCog._add_bounded_field` | internal helper | 15 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 687 | `HelpCog._normalize_duplicate_text` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 693 | `HelpCog._ticket_scan_loop` | internal helper | 9 | 4 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 703 | `HelpCog._log_background_error` | internal helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 710 | `HelpCog._load_active_ticket_channels` | internal helper | 12 | 3 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 723 | `HelpCog._reconcile_missing_ticket_channels` | internal helper | 37 | 11 | 4 | 2 | 1 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 Discord operation; 1 broad catch |
| 761 | `HelpCog._scan_tickets` | internal helper | 105 | 24 | 13 | 5 | 4 | 5 broad / 3 silent | **Focused review**: 5 persistence calls; 4 Discord operations; split candidate; 5 broad catches; 3 silent recovery paths |
| 871 | `HelpCog.on_message` | event listener | 77 | 25 | 15 | 2 | 2 | 3 broad / 1 silent | **Focused review**: 2 persistence calls; 2 Discord operations; split candidate; 3 broad catches; 1 silent recovery path |
| 952 | `HelpCog._remaining_help_cooldown` | internal helper | 9 | 2 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 962 | `HelpCog._touch_help_cooldown` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 969 | `HelpCog._cooldown_until` | internal helper | 9 | 3 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 979 | `HelpCog._cooldown_embed` | internal helper | 8 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 988 | `HelpCog._flow_start_limit_message` | internal helper | 58 | 17 | 0 | 0 | 0 | 2 broad / 0 silent | **Focused review**: 2 broad catches |
| 1047 | `HelpCog._weekly_status_text` | internal helper | 20 | 9 | 3 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1068 | `HelpCog._request_result_label` | internal helper | 11 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1080 | `HelpCog._request_state_text` | internal helper | 33 | 14 | 2 | 2 | 0 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 broad catch |
| 1114 | `HelpCog._active_ticket_text` | internal helper | 12 | 4 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1127 | `HelpCog._recent_help_status_text` | internal helper | 36 | 9 | 2 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1164 | `HelpCog._cooldown_status_text` | internal helper | 7 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1172 | `HelpCog._send_dm_dashboard` | internal helper | 21 | 5 | 7 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1194 | `HelpCog._send_former_member_dashboard` | internal helper | 8 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1203 | `HelpCog._home_menu_view` | internal helper | 5 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1209 | `HelpCog._faq_entries` | internal helper | 4 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1214 | `HelpCog._send_ticket_topics` | internal helper | 7 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1225 | `HelpCog.handle_help_selection` | workflow handler | 194 | 32 | 37 | 0 | 14 | none | **High attention**: 14 Discord operations; split candidate |
| 1420 | `HelpCog._send_faq` | internal helper | 8 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1429 | `HelpCog._faq_page_embed` | internal helper | 23 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1453 | `HelpCog._send_faq_page` | internal helper | 8 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1462 | `HelpCog.handle_faq_page` | workflow handler | 18 | 3 | 4 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1481 | `HelpCog._send_weekly_status` | internal helper | 41 | 16 | 7 | 2 | 2 | none | **Focused review**: 2 persistence calls; 2 Discord operations |
| 1526 | `HelpCog._help_session_cache_store` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1533 | `HelpCog._help_session_lock` | internal helper | 11 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1545 | `HelpCog._help_session_tombstone_store` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1552 | `HelpCog._claimed_dm_message_store` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1559 | `HelpCog._claim_dm_message` | internal helper | 14 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1574 | `HelpCog.should_yield_weekly_dm` | helper | 15 | 6 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1590 | `HelpCog._prune_help_session_memory` | internal helper | 25 | 14 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1616 | `HelpCog._help_session_lifetime` | internal helper | 8 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1625 | `HelpCog._log_help_session_storage_error` | internal helper | 8 | 2 | 1 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 1634 | `HelpCog._start_help_session` | internal helper | 20 | 2 | 2 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1655 | `HelpCog._clear_help_session` | internal helper | 14 | 2 | 2 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1670 | `HelpCog._get_help_session` | internal helper | 44 | 15 | 4 | 0 | 0 | 2 broad / 0 silent | **Focused review**: 2 broad catches |
| 1715 | `HelpCog.has_active_help_session` | helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1718 | `HelpCog._help_stage_prompt_embed` | internal helper | 49 | 10 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1768 | `HelpCog._preview_stage` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1771 | `HelpCog._edit_stage_for_kind` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1778 | `HelpCog._fresh_edit_data` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1786 | `HelpCog._submission_core_text` | internal helper | 8 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1795 | `HelpCog._submission_preview_embed` | internal helper | 19 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1815 | `HelpCog._show_submission_preview` | internal helper | 7 | 1 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1823 | `HelpCog._is_duplicate_help_submission` | internal helper | 36 | 9 | 1 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 1860 | `HelpCog._submission_log_channel` | internal helper | 16 | 7 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1877 | `HelpCog._submission_staff_embed` | internal helper | 28 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1906 | `HelpCog._insert_help_submission` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1913 | `HelpCog._submit_help_submission` | internal helper | 40 | 8 | 11 | 2 | 2 | 2 broad / 1 silent | **Focused review**: 2 persistence calls; 2 Discord operations; 2 broad catches; 1 silent recovery path |
| 1954 | `HelpCog._help_max_submission_chars` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1960 | `HelpCog._handle_help_session_message` | internal helper | 4 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1965 | `HelpCog._handle_help_session_message_locked` | internal helper | 174 | 26 | 27 | 2 | 10 | none | **High attention**: 2 persistence calls; 10 Discord operations; split candidate |
| 2140 | `HelpCog._handle_typed_back` | internal helper | 49 | 8 | 10 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 2190 | `HelpCog._edit_prompt_embed` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2203 | `HelpCog.handle_help_session_control` | workflow handler | 8 | 2 | 3 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 2212 | `HelpCog._handle_help_session_control_locked` | internal helper | 89 | 14 | 18 | 0 | 5 | none | **Focused review**: 5 Discord operations |
| 2302 | `HelpCog.handle_help_submission_preview` | workflow handler | 6 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2309 | `HelpCog._handle_help_submission_preview_locked` | internal helper | 59 | 12 | 18 | 0 | 6 | none | **Routine**: 6 Discord operations |
| 2369 | `HelpCog._parse_ticket_reference` | internal helper | 14 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2384 | `HelpCog._staff_log_embed` | internal helper | 14 | 3 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 2399 | `HelpCog._log_help_action` | internal helper | 27 | 9 | 3 | 0 | 2 | 2 broad / 0 silent | **Routine**: 2 Discord operations; 2 broad catches |
| 2427 | `HelpCog._ticket_label` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2430 | `HelpCog._requester_id_from_help_log_message` | internal helper | 23 | 14 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2454 | `HelpCog._handle_staff_help_reply` | internal helper | 9 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2464 | `HelpCog._handle_staff_help_reply_locked` | internal helper | 107 | 30 | 16 | 2 | 4 | 7 broad / 5 silent | **High attention**: 2 persistence calls; 4 Discord operations; split candidate; 7 broad catches; 5 silent recovery paths |
| 2575 | `HelpCog._submit_appeal` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2579 | `HelpCog._submit_report` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2583 | `HelpCog._submit_bot_issue` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2590 | `HelpCog._ban_info_code` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2593 | `HelpCog._known_user_history` | internal helper | 38 | 12 | 3 | 1 | 1 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 2632 | `HelpCog._ban_info_staff_embed` | internal helper | 43 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2676 | `HelpCog._create_ban_info_request` | internal helper | 89 | 8 | 16 | 5 | 6 | 2 broad / 1 silent | **Focused review**: 5 persistence calls; 6 Discord operations; 2 broad catches; 1 silent recovery path |
| 2766 | `HelpCog._can_handle_ban_info` | internal helper | 11 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2778 | `HelpCog.handle_ban_info_button` | workflow handler | 16 | 6 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 2795 | `HelpCog.handle_ban_info_modal` | workflow handler | 105 | 18 | 10 | 2 | 4 | 1 broad / 0 silent | **Focused review**: 2 persistence calls; 4 Discord operations; split candidate; 1 broad catch |
| 2901 | `HelpCog._ban_info_delivery_embed` | internal helper | 41 | 13 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2943 | `HelpCog._update_ban_info_staff_message` | internal helper | 59 | 19 | 4 | 0 | 3 | 2 broad / 0 silent | **Focused review**: 3 Discord operations; 2 broad catches |
| 3003 | `HelpCog.finalize_ban_info` | helper | 99 | 12 | 17 | 3 | 6 | 1 broad / 0 silent | **Focused review**: 3 persistence calls; 6 Discord operations; 1 broad catch |
| 3106 | `HelpCog._create_transcript_request` | internal helper | 8 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3115 | `HelpCog._create_transcript_request_locked` | internal helper | 76 | 13 | 6 | 3 | 3 | 3 broad / 1 silent | **Focused review**: 3 persistence calls; 3 Discord operations; 3 broad catches; 1 silent recovery path |
| 3192 | `HelpCog.handle_transcript_request_decision` | workflow handler | 5 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3198 | `HelpCog._handle_transcript_request_decision_locked` | internal helper | 168 | 21 | 30 | 5 | 5 | 5 broad / 0 silent | **High attention**: 5 persistence calls; 5 Discord operations; split candidate; 5 broad catches |
| 3367 | `HelpCog._dm_transcript` | internal helper | 97 | 17 | 14 | 1 | 5 | 7 broad / 0 silent | **Focused review**: 1 persistence call; 5 Discord operations; 7 broad catches |
| 3468 | `HelpCog.handle_ticket_topic` | workflow handler | 10 | 4 | 5 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 3479 | `HelpCog.handle_partnership_confirmation` | workflow handler | 25 | 3 | 4 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 3505 | `HelpCog.update_ticket_opening_status` | helper | 86 | 23 | 11 | 2 | 5 | 5 broad / 2 silent | **Focused review**: 2 persistence calls; 5 Discord operations; 5 broad catches; 2 silent recovery paths |
| 3592 | `HelpCog._create_staff_ticket` | internal helper | 19 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3612 | `HelpCog._create_staff_ticket_locked` | internal helper | 164 | 30 | 26 | 4 | 4 | 6 broad / 0 silent | **High attention**: 4 persistence calls; 4 Discord operations; split candidate; 6 broad catches |
| 3777 | `HelpCog._next_ticket_id` | internal helper | 2 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3780 | `HelpCog.handle_ticket_close_prompt` | workflow handler | 58 | 15 | 12 | 2 | 3 | 2 broad / 2 silent | **Focused review**: 2 persistence calls; 3 Discord operations; 2 broad catches; 2 silent recovery paths |
| 3839 | `HelpCog._send_ticket_satisfaction_prompt` | internal helper | 52 | 12 | 7 | 2 | 2 | 3 broad / 1 silent | **Focused review**: 2 persistence calls; 2 Discord operations; 3 broad catches; 1 silent recovery path |
| 3892 | `HelpCog._restore_ticket_satisfaction_views` | internal helper | 20 | 4 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3913 | `HelpCog.handle_ticket_satisfaction` | workflow handler | 44 | 8 | 9 | 2 | 6 | 2 broad / 0 silent | **Routine**: 2 persistence calls; 6 Discord operations; 2 broad catches |
| 3958 | `HelpCog.close_ticket_channel` | helper | 15 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3974 | `HelpCog._close_ticket_channel_locked` | internal helper | 142 | 35 | 32 | 6 | 8 | 14 broad / 4 silent | **High attention**: 6 persistence calls; 8 Discord operations; split candidate; 14 broad catches; 4 silent recovery paths |
| 3999 | `HelpCog._close_ticket_channel_locked._restore_open_status` | internal helper | 11 | 2 | 3 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 4056 | `HelpCog._close_ticket_channel_locked._cleanup_transcript_artifact` | internal helper | 21 | 5 | 4 | 1 | 1 | 2 broad / 1 silent | **Focused review**: 1 persistence call; 1 Discord operation; 2 broad catches; 1 silent recovery path |
| 4118 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

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

34 definitions: 28 routine, 5 focused, 1 high attention.

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
| 245 | `ReleaseCog._send_approval_dm` | internal helper | 35 | 4 | 6 | 3 | 1 | 1 broad / 0 silent | **Routine**: 3 persistence calls; 1 Discord operation; 1 broad catch |
| 281 | `ReleaseCog.propose_release` | helper | 19 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 301 | `ReleaseCog._propose_release_locked` | internal helper | 75 | 13 | 6 | 3 | 0 | none | **Focused review**: 3 persistence calls |
| 377 | `ReleaseCog._ensure_manifest_proposal` | internal helper | 31 | 10 | 5 | 1 | 0 | none | **Routine**: 1 persistence call |
| 409 | `ReleaseCog.refresh_public_release_cache` | helper | 8 | 2 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 419 | `ReleaseCog._uptime_snapshot_from_row` | internal helper | 30 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 450 | `ReleaseCog._initialize_uptime_tracker` | internal helper | 29 | 5 | 3 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 480 | `ReleaseCog.record_uptime_sample` | helper | 41 | 6 | 4 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 522 | `ReleaseCog.record_uptime_transition` | helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 525 | `ReleaseCog.uptime_snapshot` | helper | 16 | 1 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 542 | `ReleaseCog._allowed_guild_metrics` | internal helper | 62 | 22 | 2 | 0 | 0 | 1 broad / 0 silent | **Focused review**: 1 broad catch |
| 605 | `ReleaseCog.refresh_public_metrics` | helper | 35 | 9 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 641 | `ReleaseCog._metrics_loop` | internal helper | 13 | 5 | 4 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 655 | `ReleaseCog.start_background` | helper | 24 | 6 | 8 | 0 | 0 | 4 broad / 0 silent | **Focused review**: 4 broad catches |
| 680 | `ReleaseCog.release_overview` | helper | 25 | 5 | 2 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 706 | `ReleaseCog.handle_release_decision` | workflow handler | 19 | 2 | 3 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 726 | `ReleaseCog._handle_release_decision_locked` | internal helper | 161 | 25 | 21 | 6 | 9 | 3 broad / 0 silent | **High attention**: 6 persistence calls; 9 Discord operations; split candidate; 3 broad catches |
| 889 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/RequestLevels.py`

159 definitions: 134 routine, 17 focused, 8 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 89 | `_SafeDict.__missing__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 94 | `LevelRequestModal.__init__` | internal helper | 39 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 134 | `LevelRequestModal.callback` | helper | 15 | 8 | 3 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 152 | `ReviewModal.__init__` | internal helper | 13 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 166 | `ReviewModal.callback` | helper | 7 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 176 | `FirstRequestChoiceView.__init__` | internal helper | 8 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 185 | `FirstRequestChoiceView._will` | internal helper | 4 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 192 | `OtherReasonView.__init__` | internal helper | 9 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 202 | `OtherReasonView._make_callback` | internal helper | 4 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 203 | `OtherReasonView._make_callback._callback` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 209 | `ScheduledOpeningEditModal.__init__` | internal helper | 47 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 257 | `ScheduledOpeningEditModal.callback` | helper | 12 | 7 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 272 | `ScheduledOpeningsView.__init__` | internal helper | 33 | 10 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 306 | `ScheduledOpeningsView._allowed` | internal helper | 12 | 5 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 319 | `ScheduledOpeningsView._select` | internal helper | 8 | 3 | 2 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 328 | `ScheduledOpeningsView._refresh` | internal helper | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 334 | `ScheduledOpeningsView._edit` | internal helper | 15 | 6 | 4 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 350 | `ScheduledOpeningsView._delete` | internal helper | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 356 | `ScheduledOpeningsView._open_now` | internal helper | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 364 | `ScheduledOpenNowConfirmView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 370 | `ScheduledOpenNowConfirmView._allowed` | internal helper | 12 | 5 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 384 | `ScheduledOpenNowConfirmView.confirm` | UI callback | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 391 | `ScheduledOpenNowConfirmView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 398 | `RequestLevelsCog.__init__` | internal helper | 158 | 3 | 6 | 0 | 0 | none | **High attention**: split candidate |
| 422 | `RequestLevelsCog.__init__.refresh_request_button` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 426 | `RequestLevelsCog.__init__.open_requests` | slash command | 49 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 477 | `RequestLevelsCog.__init__.close_requests` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 481 | `RequestLevelsCog.__init__.requests_are` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 485 | `RequestLevelsCog.__init__.edit_request` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 489 | `RequestLevelsCog.__init__.pending_openings` | slash command | 67 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 557 | `RequestLevelsCog.cog_unload` | helper | 13 | 5 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 571 | `RequestLevelsCog.close_resources` | helper | 15 | 7 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 587 | `RequestLevelsCog.start_background` | helper | 11 | 8 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 599 | `RequestLevelsCog.on_config_reload` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 602 | `RequestLevelsCog._start_background_task` | internal helper | 13 | 3 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 603 | `RequestLevelsCog._start_background_task.runner` | helper | 7 | 3 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 616 | `RequestLevelsCog._cfg` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 619 | `RequestLevelsCog._cfg_int` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 622 | `RequestLevelsCog._cfg_int_list` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 625 | `RequestLevelsCog._reviewer_role_ids` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 629 | `RequestLevelsCog._post_close_edit_seconds` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 633 | `RequestLevelsCog._edit_deadline_ts_for_state` | internal helper | 19 | 7 | 0 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 653 | `RequestLevelsCog._edit_window_text` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 660 | `RequestLevelsCog._can_edit_submission` | internal helper | 32 | 14 | 0 | 0 | 0 | 3 broad / 1 silent | **Focused review**: 3 broad catches; 1 silent recovery path |
| 693 | `RequestLevelsCog._current_user_submission` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 699 | `RequestLevelsCog._current_user_submission_local` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 705 | `RequestLevelsCog._latest_editable_user_submission` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 712 | `RequestLevelsCog._editable_user_submission_for_modal` | internal helper | 20 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 733 | `RequestLevelsCog._state_after_timed_close_check` | internal helper | 12 | 6 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 746 | `RequestLevelsCog._request_initial_values` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 752 | `RequestLevelsCog._message` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 755 | `RequestLevelsCog._message_formatted` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 758 | `RequestLevelsCog._request_button_label` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 761 | `RequestLevelsCog._request_type_normalize_text` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 767 | `RequestLevelsCog._normalize_request_type` | internal helper | 9 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 777 | `RequestLevelsCog._request_type_label` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 783 | `RequestLevelsCog._request_type_help` | internal helper | 2 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 786 | `RequestLevelsCog._request_type_from_row` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 793 | `RequestLevelsCog._clean_open_message` | internal helper | 7 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 801 | `RequestLevelsCog._request_open_condition_text` | internal helper | 11 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 813 | `RequestLevelsCog._send_open_announcement` | internal helper | 55 | 23 | 4 | 0 | 1 | 3 broad / 0 silent | **Focused review**: 1 Discord operation; 3 broad catches |
| 869 | `RequestLevelsCog._color_name` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 872 | `RequestLevelsCog._format` | internal helper | 5 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 878 | `RequestLevelsCog._submitted_ago` | internal helper | 6 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 885 | `RequestLevelsCog._clean_level_id` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 888 | `RequestLevelsCog._normalize_level_id` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 891 | `RequestLevelsCog._valid_url` | internal helper | 12 | 7 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 904 | `RequestLevelsCog._validate_request_data` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 915 | `RequestLevelsCog._level_validation_cfg` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 919 | `RequestLevelsCog._level_validation_enabled` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 923 | `RequestLevelsCog._level_validation_cache_seconds` | internal helper | 5 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 929 | `RequestLevelsCog._level_validation_timeout_seconds` | internal helper | 5 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 935 | `RequestLevelsCog._level_validation_message` | internal helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 941 | `RequestLevelsCog._level_validation_providers` | internal helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 950 | `RequestLevelsCog._level_validation_rate_limit_message` | internal helper | 44 | 19 | 0 | 0 | 0 | 3 broad / 0 silent | **Focused review**: 3 broad catches |
| 995 | `RequestLevelsCog._provider_failure_cfg` | internal helper | 11 | 3 | 0 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 1007 | `RequestLevelsCog._provider_circuit_open` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1014 | `RequestLevelsCog._record_provider_validation_result` | internal helper | 9 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1024 | `RequestLevelsCog._provider_min_interval` | internal helper | 11 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1036 | `RequestLevelsCog._fetch_validation_provider` | internal helper | 20 | 5 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1057 | `RequestLevelsCog._get_level_validation_session` | internal helper | 17 | 7 | 1 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 1075 | `RequestLevelsCog._safe_json_loads` | internal helper | 5 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1081 | `RequestLevelsCog._cached_level_validation` | internal helper | 17 | 5 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1099 | `RequestLevelsCog._lookup_level_validation` | internal helper | 62 | 20 | 7 | 1 | 0 | 1 broad / 0 silent | **Focused review**: 1 persistence call; 1 broad catch |
| 1162 | `RequestLevelsCog._apply_level_validation_vars` | internal helper | 81 | 30 | 0 | 0 | 0 | 1 broad / 0 silent | **High attention**: split candidate; 1 broad catch |
| 1244 | `RequestLevelsCog._validate_level_external` | internal helper | 28 | 14 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1273 | `RequestLevelsCog._request_type_validation_error` | internal helper | 32 | 23 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1306 | `RequestLevelsCog._has_reviewer_role` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1310 | `RequestLevelsCog._embed_from_template` | internal helper | 50 | 21 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1361 | `RequestLevelsCog._reply_ephemeral` | internal helper | 5 | 2 | 2 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 1367 | `RequestLevelsCog._log_request_admin_action` | internal helper | 23 | 7 | 2 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1391 | `RequestLevelsCog._state_label` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1394 | `RequestLevelsCog._request_button_embed` | internal helper | 15 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1410 | `RequestLevelsCog._pct` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1415 | `RequestLevelsCog._wave_summary_vars` | internal helper | 47 | 20 | 2 | 1 | 0 | none | **Focused review**: 1 persistence call |
| 1463 | `RequestLevelsCog._reviewer_stats_lines` | internal helper | 21 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1485 | `RequestLevelsCog._wave_summary_embed` | internal helper | 32 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1518 | `RequestLevelsCog.update_wave_summary` | helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1522 | `RequestLevelsCog._update_wave_summary_unlocked` | internal helper | 77 | 19 | 15 | 4 | 8 | 6 broad / 3 silent | **Focused review**: 4 persistence calls; 8 Discord operations; 6 broad catches; 3 silent recovery paths |
| 1600 | `RequestLevelsCog._base_state_vars` | internal helper | 25 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1626 | `RequestLevelsCog._row_value` | internal helper | 7 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1634 | `RequestLevelsCog._duplicate_history_warning` | internal helper | 25 | 6 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1660 | `RequestLevelsCog._days_in_month` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1663 | `RequestLevelsCog._add_month` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1669 | `RequestLevelsCog._scheduled_local_time_exists` | internal helper | 15 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1685 | `RequestLevelsCog._parse_scheduled_open_ts` | internal helper | 41 | 16 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1727 | `RequestLevelsCog._scheduled_opening_rows` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1734 | `RequestLevelsCog.get_scheduled_opening` | helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1741 | `RequestLevelsCog._scheduled_openings_embed` | internal helper | 37 | 14 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1779 | `RequestLevelsCog.refresh_pending_openings_panel` | helper | 5 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1785 | `RequestLevelsCog.delete_scheduled_opening` | helper | 16 | 2 | 6 | 1 | 2 | none | **Routine**: 1 persistence call; 2 Discord operations |
| 1802 | `RequestLevelsCog.open_scheduled_opening_now` | helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1806 | `RequestLevelsCog._open_scheduled_opening_now_locked` | internal helper | 44 | 11 | 9 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 1851 | `RequestLevelsCog.handle_scheduled_opening_edit_modal` | workflow handler | 68 | 20 | 13 | 1 | 9 | 2 broad / 0 silent | **Focused review**: 1 persistence call; 9 Discord operations; 2 broad catches |
| 1875 | `RequestLevelsCog.handle_scheduled_opening_edit_modal.optional_positive` | helper | 11 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1920 | `RequestLevelsCog._data_vars` | internal helper | 60 | 35 | 0 | 0 | 0 | 1 broad / 0 silent | **High attention**: split candidate; 1 broad catch |
| 1981 | `RequestLevelsCog._weekly_data_vars` | internal helper | 27 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2009 | `RequestLevelsCog._result_label` | internal helper | 6 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2016 | `RequestLevelsCog._status_channel_id` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2021 | `RequestLevelsCog._result_template_key` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2028 | `RequestLevelsCog._get_state` | internal helper | 9 | 2 | 3 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 2038 | `RequestLevelsCog._get_state_local` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2044 | `RequestLevelsCog._set_state_closed` | internal helper | 36 | 9 | 6 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 2081 | `RequestLevelsCog._open_requests_now` | internal helper | 88 | 20 | 11 | 1 | 0 | 2 broad / 0 silent | **Focused review**: 1 persistence call; 2 broad catches |
| 2170 | `RequestLevelsCog._auto_close_loop` | internal helper | 17 | 9 | 5 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2188 | `RequestLevelsCog._scheduled_open_loop` | internal helper | 44 | 14 | 8 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 2233 | `RequestLevelsCog._in_allowed_guild` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2236 | `RequestLevelsCog._defer_command` | internal helper | 5 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2242 | `RequestLevelsCog._cached_interaction_member` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2249 | `RequestLevelsCog._resolve_member` | internal helper | 18 | 5 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 2268 | `RequestLevelsCog._is_admin` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2271 | `RequestLevelsCog._is_mod` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2279 | `RequestLevelsCog._configured_channel` | internal helper | 9 | 6 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 2289 | `RequestLevelsCog.refresh_or_create_request_button` | helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2293 | `RequestLevelsCog._refresh_or_create_request_button_unlocked` | internal helper | 63 | 19 | 13 | 2 | 8 | 6 broad / 3 silent | **Focused review**: 2 persistence calls; 8 Discord operations; 6 broad catches; 3 silent recovery paths |
| 2357 | `RequestLevelsCog.refresh_request_button` | helper | 13 | 5 | 8 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 2371 | `RequestLevelsCog.open_requests` | helper | 83 | 37 | 15 | 1 | 9 | none | **High attention**: 1 persistence call; 9 Discord operations; split candidate |
| 2455 | `RequestLevelsCog.pending_openings` | helper | 96 | 34 | 25 | 3 | 15 | none | **High attention**: 3 persistence calls; 15 Discord operations; split candidate |
| 2552 | `RequestLevelsCog.close_requests` | helper | 16 | 5 | 10 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 2569 | `RequestLevelsCog.requests_are` | helper | 15 | 7 | 4 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2585 | `RequestLevelsCog._requirements_ok` | internal helper | 8 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2594 | `RequestLevelsCog.handle_request_button` | workflow handler | 86 | 19 | 15 | 0 | 12 | none | **Focused review**: 12 Discord operations |
| 2681 | `RequestLevelsCog.edit_request` | helper | 18 | 3 | 4 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 2700 | `RequestLevelsCog.handle_first_choice` | workflow handler | 20 | 7 | 5 | 0 | 4 | 1 broad / 0 silent | **Routine**: 4 Discord operations; 1 broad catch |
| 2721 | `RequestLevelsCog.handle_request_form` | workflow handler | 212 | 33 | 40 | 7 | 3 | 8 broad / 1 silent | **High attention**: 7 persistence calls; 3 Discord operations; split candidate; 8 broad catches; 1 silent recovery path |
| 2934 | `RequestLevelsCog.handle_request_edit_form` | workflow handler | 181 | 33 | 32 | 4 | 2 | 4 broad / 0 silent | **High attention**: 4 persistence calls; 2 Discord operations; split candidate; 4 broad catches |
| 3116 | `RequestLevelsCog._refresh_closed_wave` | internal helper | 7 | 3 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 3124 | `RequestLevelsCog._submission_by_message` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3130 | `RequestLevelsCog._weekly_submission_by_message` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3136 | `RequestLevelsCog._review_target_by_message` | internal helper | 8 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3145 | `RequestLevelsCog._review_target_by_message_local` | internal helper | 24 | 4 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3170 | `RequestLevelsCog._channel_by_id` | internal helper | 8 | 6 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 3179 | `RequestLevelsCog._review_target_channel` | internal helper | 4 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3184 | `RequestLevelsCog.handle_review_button` | workflow handler | 16 | 8 | 7 | 0 | 6 | none | **Routine**: 6 Discord operations |
| 3201 | `RequestLevelsCog.handle_review_submission` | workflow handler | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3204 | `RequestLevelsCog.handle_other_reason` | workflow handler | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3207 | `RequestLevelsCog._finalize_review` | internal helper | 102 | 23 | 27 | 2 | 4 | 6 broad / 1 silent | **Focused review**: 2 persistence calls; 4 Discord operations; split candidate; 6 broad catches; 1 silent recovery path |
| 3310 | `RequestLevelsCog.repair_request_system` | helper | 266 | 68 | 39 | 11 | 11 | 15 broad / 5 silent | **High attention**: 11 persistence calls; 11 Discord operations; split candidate; 15 broad catches; 5 silent recovery paths |
| 3578 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Sticky.py`

28 definitions: 21 routine, 7 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 16 | `StickyCog.__init__` | internal helper | 20 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 37 | `StickyCog.cog_unload` | helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 44 | `StickyCog._start_background_task` | internal helper | 19 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 48 | `StickyCog._start_background_task._done` | internal helper | 12 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 64 | `StickyCog.reload_from_config` | helper | 32 | 15 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 97 | `StickyCog.on_config_reload` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 100 | `StickyCog._required_rule_from_config` | internal helper | 31 | 13 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 132 | `StickyCog._get_sticky_for_channel` | internal helper | 8 | 4 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 145 | `StickyCog.on_message` | event listener | 49 | 15 | 1 | 0 | 0 | 1 broad / 0 silent | **Focused review**: 1 broad catch |
| 189 | `StickyCog.on_message._remove` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 195 | `StickyCog._do_sticky` | internal helper | 21 | 5 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 217 | `StickyCog._replace_sticky` | internal helper | 4 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 222 | `StickyCog._replace_sticky_locked` | internal helper | 66 | 23 | 13 | 2 | 5 | 6 broad / 2 silent | **Focused review**: 2 persistence calls; 5 Discord operations; 6 broad catches; 2 silent recovery paths |
| 292 | `StickyCog._get_thread_lock` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 300 | `StickyCog._trim_forum_runtime_state` | internal helper | 9 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 310 | `StickyCog._forum_template_for_thread` | internal helper | 11 | 7 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 322 | `StickyCog._thread_has_bot_message` | internal helper | 19 | 13 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 342 | `StickyCog._send_forum_first_message` | internal helper | 16 | 8 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 359 | `StickyCog._schedule_required_word_check` | internal helper | 10 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 370 | `StickyCog._normalize_required_word_text` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 375 | `StickyCog._required_regex_is_safe` | internal helper | 18 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 394 | `StickyCog._thread_contains_required_word` | internal helper | 29 | 14 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 424 | `StickyCog._find_thread_owner` | internal helper | 18 | 9 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 443 | `StickyCog._log_required_word_deletion` | internal helper | 32 | 11 | 3 | 0 | 2 | 2 broad / 0 silent | **Routine**: 2 Discord operations; 2 broad catches |
| 476 | `StickyCog._enforce_required_word` | internal helper | 52 | 19 | 9 | 0 | 3 | 4 broad / 1 silent | **Focused review**: 3 Discord operations; 4 broad catches; 1 silent recovery path |
| 529 | `StickyCog._forum_first_message_flow` | internal helper | 64 | 17 | 6 | 0 | 0 | 3 broad / 1 silent | **Focused review**: 3 broad catches; 1 silent recovery path |
| 595 | `StickyCog.on_thread_create` | event listener | 14 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 611 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Tracking.py`

61 definitions: 44 routine, 14 focused, 3 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 45 | `TrackingCog.__init__` | internal helper | 16 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 62 | `TrackingCog._respond_interaction` | internal helper | 4 | 2 | 2 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 70 | `TrackingCog._cfg_int` | internal helper | 17 | 6 | 0 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |
| 88 | `TrackingCog._cfg_int_list` | internal helper | 21 | 6 | 0 | 0 | 0 | 3 broad / 1 silent | **Focused review**: 3 broad catches; 1 silent recovery path |
| 110 | `TrackingCog._format_template` | internal helper | 9 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 112 | `TrackingCog._format_template._SafeDict.__missing__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 120 | `TrackingCog._embed_from_template` | internal helper | 39 | 18 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 160 | `TrackingCog._weekly_request_review_data` | internal helper | 62 | 29 | 0 | 0 | 0 | none | **Focused review**: split candidate |
| 223 | `TrackingCog._weekly_request_missing_fields` | internal helper | 10 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 234 | `TrackingCog._weekly_request_max_chars` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 240 | `TrackingCog._resolve_member` | internal helper | 15 | 5 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 256 | `TrackingCog._configured_channel` | internal helper | 8 | 6 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 265 | `TrackingCog._log_background_error` | internal helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 272 | `TrackingCog._dm_user` | internal helper | 9 | 2 | 3 | 0 | 2 | 1 broad / 0 silent | **Routine**: 2 Discord operations; 1 broad catch |
| 282 | `TrackingCog._validate_weekly_request_for_review` | internal helper | 49 | 12 | 8 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |
| 335 | `TrackingCog.start_background` | helper | 24 | 14 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 360 | `TrackingCog.cog_unload` | helper | 8 | 5 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 369 | `TrackingCog._recover_contacting_claims` | internal helper | 61 | 9 | 10 | 4 | 2 | 2 broad / 0 silent | **Routine**: 4 persistence calls; 2 Discord operations; 2 broad catches |
| 431 | `TrackingCog.on_config_reload` | helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 438 | `TrackingCog.user_in_weekly_process` | helper | 10 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 449 | `TrackingCog.weekly_reward_disabled` | helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 456 | `TrackingCog.disable_weekly_reward_for_current_week` | helper | 21 | 1 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 478 | `TrackingCog.enable_weekly_reward_for_current_week` | helper | 53 | 4 | 5 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 532 | `TrackingCog._notify_reenabled_weekly_claims` | internal helper | 26 | 4 | 4 | 0 | 2 | 1 broad / 0 silent | **Routine**: 2 Discord operations; 1 broad catch |
| 562 | `TrackingCog._weekly_log_meta` | internal helper | 28 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 591 | `TrackingCog._weekly_detail_lines` | internal helper | 14 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 606 | `TrackingCog._log_weekly` | internal helper | 41 | 10 | 5 | 1 | 2 | 3 broad / 0 silent | **Routine**: 1 persistence call; 2 Discord operations; 3 broad catches |
| 648 | `TrackingCog._anti_farm_cfg` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 652 | `TrackingCog._anti_farm_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 655 | `TrackingCog._message_signature` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 660 | `TrackingCog._anti_farm_reason` | internal helper | 53 | 24 | 0 | 0 | 0 | 4 broad / 0 silent | **Focused review**: 4 broad catches |
| 714 | `TrackingCog._record_anti_farm_event` | internal helper | 52 | 19 | 5 | 1 | 2 | 4 broad / 0 silent | **Focused review**: 1 persistence call; 2 Discord operations; 4 broad catches |
| 771 | `TrackingCog.on_message` | event listener | 62 | 17 | 5 | 1 | 0 | none | **Focused review**: 1 persistence call |
| 834 | `TrackingCog._activity_flush_loop` | internal helper | 10 | 5 | 4 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 845 | `TrackingCog.flush_activity_counts` | helper | 41 | 12 | 4 | 2 | 0 | 2 broad / 0 silent | **Routine**: 2 persistence calls; 2 broad catches |
| 890 | `TrackingCog._handle_dm` | internal helper | 102 | 21 | 12 | 2 | 5 | 6 broad / 4 silent | **High attention**: 2 persistence calls; 5 Discord operations; split candidate; 6 broad catches; 4 silent recovery paths |
| 993 | `TrackingCog._record_request` | internal helper | 133 | 20 | 23 | 2 | 11 | 8 broad / 4 silent | **High attention**: 2 persistence calls; 11 Discord operations; split candidate; 8 broad catches; 4 silent recovery paths |
| 1128 | `TrackingCog.handle_decline_confirm` | workflow handler | 68 | 9 | 12 | 3 | 2 | 2 broad / 2 silent | **Focused review**: 3 persistence calls; 2 Discord operations; 2 broad catches; 2 silent recovery paths |
| 1200 | `TrackingCog._weekly_loop` | internal helper | 43 | 8 | 8 | 3 | 0 | 2 broad / 1 silent | **Focused review**: 3 persistence calls; 2 broad catches; 1 silent recovery path |
| 1244 | `TrackingCog._weekly_recap_due` | internal helper | 12 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1257 | `TrackingCog._weekly_recap_loop` | internal helper | 11 | 4 | 5 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1269 | `TrackingCog._timeout_loop` | internal helper | 12 | 4 | 6 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1282 | `TrackingCog._process_timeouts` | internal helper | 49 | 6 | 9 | 3 | 2 | 1 broad / 0 silent | **Routine**: 3 persistence calls; 2 Discord operations; 1 broad catch |
| 1332 | `TrackingCog._update_weekly_streaks` | internal helper | 45 | 21 | 4 | 4 | 0 | 2 broad / 0 silent | **Focused review**: 4 persistence calls; 2 broad catches |
| 1378 | `TrackingCog._send_weekly_recap` | internal helper | 112 | 38 | 12 | 7 | 3 | 6 broad / 1 silent | **High attention**: 7 persistence calls; 3 Discord operations; split candidate; 6 broad catches; 1 silent recovery path |
| 1491 | `TrackingCog._ranked_rows_for_week` | internal helper | 45 | 13 | 2 | 1 | 1 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 1537 | `TrackingCog._send_missing_weekly_recap_once` | internal helper | 28 | 7 | 6 | 2 | 0 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 broad catch |
| 1569 | `TrackingCog.run_weekly_job` | helper | 43 | 9 | 10 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1613 | `TrackingCog._contact_user_for_week` | internal helper | 104 | 19 | 18 | 4 | 4 | 4 broad / 0 silent | **Focused review**: 4 persistence calls; 4 Discord operations; split candidate; 4 broad catches |
| 1718 | `TrackingCog._contact_next_eligible` | internal helper | 61 | 10 | 8 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1783 | `TrackingCog._format_deadline` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1786 | `TrackingCog._build_request_dm_text` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1790 | `TrackingCog._build_request_dm_message` | internal helper | 15 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1806 | `TrackingCog._build_reminder_text` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1810 | `TrackingCog._build_reminder_message` | internal helper | 14 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1825 | `TrackingCog._process_reminders` | internal helper | 50 | 11 | 7 | 3 | 2 | 1 broad / 0 silent | **Routine**: 3 persistence calls; 2 Discord operations; 1 broad catch |
| 1879 | `TrackingCog.get_top` | helper | 8 | 2 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1888 | `TrackingCog.get_member_stats` | helper | 39 | 17 | 4 | 2 | 0 | none | **Focused review**: 2 persistence calls |
| 1928 | `TrackingCog.force_dm_for_user` | helper | 62 | 16 | 14 | 3 | 0 | none | **Focused review**: 3 persistence calls |
| 1991 | `TrackingCog.reset_current_week` | helper | 20 | 8 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2013 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `main.py`

20 definitions: 14 routine, 5 focused, 1 high attention.

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
| 106 | `_close_runtime_storage` | internal helper | 45 | 11 | 6 | 1 | 0 | 5 broad / 0 silent | **Focused review**: 1 persistence call; 5 broad catches |
| 153 | `_install_storage_close_hook` | internal helper | 13 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 157 | `_install_storage_close_hook.close_with_storage_flush` | helper | 7 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 168 | `_database_path_usable` | internal helper | 13 | 3 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 183 | `resolve_db_path` | helper | 68 | 24 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 252 | `create_bot` | helper | 205 | 36 | 23 | 0 | 0 | 12 broad / 1 silent | **High attention**: split candidate; 12 broad catches; 1 silent recovery path |
| 280 | `create_bot._load_cogs` | internal helper | 10 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 292 | `create_bot.on_ready` | helper | 89 | 20 | 15 | 0 | 0 | 7 broad / 1 silent | **Focused review**: 7 broad catches; 1 silent recovery path |
| 383 | `create_bot.on_disconnect` | helper | 31 | 8 | 4 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 416 | `create_bot.on_resumed` | helper | 19 | 7 | 4 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 436 | `create_bot.register_persistent_views` | helper | 10 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 458 | `run_bot_with_startup_backoff` | helper | 92 | 15 | 0 | 0 | 0 | 3 broad / 1 silent | **Focused review**: 3 broad catches; 1 silent recovery path |

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

52 definitions: 40 routine, 11 focused, 1 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 23 | `DictRow.__init__` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 27 | `DictRow.__getitem__` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 33 | `_row_get` | internal helper | 11 | 4 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 46 | `_normalize_row` | internal helper | 14 | 8 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 62 | `_normalize_rows` | internal helper | 2 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 66 | `_fetchall` | internal helper | 2 | 2 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 70 | `_jwt_payload` | internal helper | 12 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 84 | `_token_scope_names` | internal helper | 9 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 95 | `_looks_like_turso_platform_token` | internal helper | 12 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 109 | `_is_recoverable_remote_error` | internal helper | 19 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 138 | `Database.__init__` | internal helper | 14 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 153 | `Database._close_connection_sync` | internal helper | 9 | 3 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 163 | `Database._reopen_connection_sync` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 169 | `Database._open_connection_sync` | internal helper | 25 | 7 | 0 | 3 | 0 | 2 broad / 2 silent | **Focused review**: 3 persistence calls; 2 broad catches; 2 silent recovery paths |
| 195 | `Database._sync_remote_sync` | internal helper | 6 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 202 | `Database._sync_remote_with_retry_sync` | internal helper | 16 | 7 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 219 | `Database._try_pending_remote_sync_sync` | internal helper | 24 | 7 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 244 | `Database._commit_and_sync_sync` | internal helper | 25 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 270 | `Database._run_locked_with_retry` | internal helper | 32 | 11 | 5 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 303 | `Database.connect` | helper | 16 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 308 | `Database.connect._connect_and_migrate` | internal helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 320 | `Database.close` | helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 327 | `Database.backup_to` | helper | 45 | 6 | 2 | 3 | 0 | 0 broad / 1 silent | **Focused review**: 3 persistence calls; 1 silent recovery path |
| 334 | `Database.backup_to._backup` | internal helper | 36 | 6 | 0 | 3 | 0 | 0 broad / 1 silent | **Focused review**: 3 persistence calls; 1 silent recovery path |
| 373 | `Database.restore_from` | helper | 91 | 15 | 1 | 8 | 0 | 2 broad / 3 silent | **Focused review**: 8 persistence calls; 2 broad catches; 3 silent recovery paths |
| 390 | `Database.restore_from._unlink_sidecars` | internal helper | 6 | 3 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 397 | `Database.restore_from._connect_current` | internal helper | 9 | 1 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 407 | `Database.restore_from._restore` | internal helper | 55 | 10 | 0 | 6 | 0 | 2 broad / 2 silent | **Focused review**: 6 persistence calls; 2 broad catches; 2 silent recovery paths |
| 465 | `Database._migrate_sync` | internal helper | 466 | 7 | 0 | 3 | 0 | none | **High attention**: 3 persistence calls; split candidate |
| 932 | `Database._ensure_column_sync` | internal helper | 7 | 3 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 940 | `Database._normalize_weekly_dm_log_sync` | internal helper | 33 | 8 | 0 | 6 | 0 | none | **Routine**: 6 persistence calls |
| 974 | `Database._init_ticket_sequences_sync` | internal helper | 24 | 8 | 0 | 7 | 0 | none | **Routine**: 7 persistence calls |
| 999 | `Database.next_ticket_id` | helper | 16 | 3 | 1 | 4 | 0 | none | **Routine**: 4 persistence calls |
| 1000 | `Database.next_ticket_id._run` | internal helper | 13 | 3 | 0 | 4 | 0 | none | **Routine**: 4 persistence calls |
| 1016 | `Database.execute` | helper | 7 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1017 | `Database.execute._run` | internal helper | 4 | 1 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1024 | `Database.execute_insert` | helper | 14 | 3 | 1 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1027 | `Database.execute_insert._run` | internal helper | 9 | 3 | 0 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1039 | `Database.execute_transaction` | helper | 26 | 6 | 1 | 2 | 0 | 2 broad / 1 silent | **Focused review**: 2 persistence calls; 2 broad catches; 1 silent recovery path |
| 1050 | `Database.execute_transaction._run` | internal helper | 13 | 4 | 0 | 2 | 0 | 2 broad / 1 silent | **Focused review**: 2 persistence calls; 2 broad catches; 1 silent recovery path |
| 1066 | `Database.set_runtime_setting` | helper | 7 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1074 | `Database.get_runtime_setting` | helper | 11 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1086 | `Database.sync_remote` | helper | 15 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1091 | `Database.sync_remote._run` | internal helper | 8 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1102 | `Database.executemany` | helper | 9 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1105 | `Database.executemany._run` | internal helper | 4 | 1 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1112 | `Database.fetchone` | helper | 7 | 1 | 1 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1113 | `Database.fetchone._run` | internal helper | 4 | 1 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1120 | `Database.fetchone_local` | helper | 14 | 1 | 1 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1128 | `Database.fetchone_local._run` | internal helper | 4 | 1 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1135 | `Database.fetchall` | helper | 7 | 1 | 1 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1136 | `Database.fetchall._run` | internal helper | 4 | 1 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |

### `utils/errors.py`

10 definitions: 6 routine, 4 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 15 | `_redact_secrets` | internal helper | 18 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 35 | `_compact_error_message` | internal helper | 20 | 16 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 57 | `_strip_trace_context` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 65 | `_dedupe_key` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 71 | `_unwrap_command_error` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 85 | `_command_error_record` | internal helper | 13 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 100 | `log_error` | helper | 68 | 17 | 3 | 0 | 3 | 7 broad / 3 silent | **Focused review**: 3 Discord operations; 7 broad catches; 3 silent recovery paths |
| 169 | `setup_global_error_handlers` | helper | 20 | 3 | 3 | 0 | 1 | 1 broad / 1 silent | **Focused review**: 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 171 | `setup_global_error_handlers.on_application_command_error` | helper | 14 | 3 | 2 | 0 | 1 | 1 broad / 1 silent | **Focused review**: 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 187 | `setup_global_error_handlers.on_error` | helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |

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
Pytest                                PASS (103 tests)
Bandit medium/high security scan     PASS
Production dependency audit          PASS
Discord modal serialization          PASS
Configuration JSON parse             PASS
```
