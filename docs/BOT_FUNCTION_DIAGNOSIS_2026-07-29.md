# Avenue Guard Function-by-Function Diagnosis

**Audit date:** 2026-07-29  
**Runtime scope:** 23 Python modules, 786 definitions, 17,749 physical lines  
**Method:** AST inventory, per-function control-flow scoring, interaction-order review, persistence/Discord I/O mapping, compile, tests, Ruff, Bandit, and dependency audit

## Reading This Report

Every runtime function, method, nested callback, and modal handler has one row below. The attention label is a review priority, not proof of a defect: orchestration code and schema declarations are naturally larger. Complexity is a deterministic branch score used to find code that deserves focused tests.

- **Routine:** compact control flow with no static risk signal.
- **Focused review:** a long path, broad recovery, interaction timing, or several I/O boundaries.
- **High attention:** very large/branch-heavy orchestration or several silent recovery paths.

## Executive Diagnosis

- All runtime modules parse and compile.
- The complete automated suite passes: 57 tests.
- Ruff's correctness and bug checks pass.
- Bandit reports no medium or high security findings.
- The production dependency set has no known published vulnerabilities.
- Slash commands and support component handlers acknowledge interactions before slow work, except modal-first commands that must query the local replica or open the modal as their initial response.
- Turso-backed workflow state, request waves, tickets, tracking, summaries, runtime settings, and help submissions remain restart-persistent.

## Current Review Fixes

- Removed free-text FAQ interception and replaced the crowded FAQ with explicit pagination.
- Added a member/former-member split so banned users can reach support without guild membership.
- Corrected support component acknowledgement order to prevent expired interactions and missing continuation messages.
- Added a third appeal step for the behavior change expected after revocation.
- Added partnership confirmation and isolated role notification without pinging normal ticket staff.
- Limited request status to the user's current-wave submission and linked its review card.
- Added durable ban-information requests, staff modal controls, optional evidence files, confirmation, DM delivery, and retryable failure state.
- Hardened persistent controls so unavailable cogs return a clear response instead of timing out.

## Attention Summary

| Classification | Definitions |
|---|---:|
| Routine | 636 |
| Focused review | 124 |
| High attention | 26 |

## Function Inventory

### `cogs/Background.py`

82 definitions: 67 routine, 13 focused, 2 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 32 | `_day_key` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 36 | `_parse_hhmm` | internal helper | 10 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 47 | `_fmt_minutes` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 55 | `_fmt_num` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 58 | `_fmt_delta` | internal helper | 8 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 67 | `_fmt_percent` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 101 | `BackgroundCog.__init__` | internal helper | 12 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 114 | `BackgroundCog.cog_unload` | helper | 18 | 5 | 0 | 0 | 0 | 2 broad / 2 silent | **Focused review**: 2 broad catches; 2 silent recovery paths |
| 133 | `BackgroundCog.start_background` | helper | 60 | 22 | 10 | 0 | 0 | 7 broad / 0 silent | **Focused review**: 7 broad catches |
| 194 | `BackgroundCog.on_config_reload` | helper | 45 | 21 | 0 | 0 | 0 | 8 broad / 4 silent | **High attention**: 8 broad catches; 4 silent recovery paths |
| 243 | `BackgroundCog._excluded_channels` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 251 | `BackgroundCog._status_rotation_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 254 | `BackgroundCog._status_rotation_interval` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 257 | `BackgroundCog._status_list` | internal helper | 14 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 272 | `BackgroundCog._server_icon_rotation_enabled` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 276 | `BackgroundCog._server_icon_interval` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 280 | `BackgroundCog._server_icon_urls` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 285 | `BackgroundCog._database_backup_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 288 | `BackgroundCog._database_backup_interval_seconds` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 292 | `BackgroundCog._server_icon_current_index` | internal helper | 8 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 301 | `BackgroundCog._server_icon_candidate_indices` | internal helper | 14 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 316 | `BackgroundCog._looks_like_server_icon_image` | internal helper | 10 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 327 | `BackgroundCog._assert_public_server_icon_url` | internal helper | 26 | 12 | 1 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 354 | `BackgroundCog._download_server_icon` | internal helper | 40 | 18 | 2 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 395 | `BackgroundCog._detect_current_server_icon_index` | internal helper | 18 | 8 | 2 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 414 | `BackgroundCog._persist_server_icon_state` | internal helper | 6 | 2 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 421 | `BackgroundCog._remember_server_icon_error` | internal helper | 7 | 3 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 429 | `BackgroundCog.rotate_server_icon_once` | helper | 15 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 445 | `BackgroundCog._rotate_server_icon_once_locked` | internal helper | 70 | 19 | 6 | 0 | 1 | 2 broad / 0 silent | **Focused review**: 1 Discord operation; 2 broad catches |
| 516 | `BackgroundCog._render_status_text` | internal helper | 55 | 14 | 5 | 3 | 0 | 3 broad / 0 silent | **Routine**: 3 persistence calls; 3 broad catches |
| 519 | `BackgroundCog._render_status_text._SafeDict.__missing__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 572 | `BackgroundCog._daily_summary_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 575 | `BackgroundCog._daily_summary_channel_id` | internal helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 581 | `BackgroundCog._daily_reset_after_report` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 584 | `BackgroundCog._daily_summary_already_sent` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 591 | `BackgroundCog._record_daily_summary_sent` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 597 | `BackgroundCog._voice_sessions_from_guild` | internal helper | 9 | 6 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 607 | `BackgroundCog._stats_payload` | internal helper | 23 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 631 | `BackgroundCog._stats_from_payload` | internal helper | 16 | 12 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 648 | `BackgroundCog._load_daily_stats` | internal helper | 11 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 660 | `BackgroundCog._persist_daily_stats` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 667 | `BackgroundCog._persist_current_day` | internal helper | 9 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 677 | `BackgroundCog._rollover_boundary_ts` | internal helper | 6 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 684 | `BackgroundCog._add_voice_until` | internal helper | 8 | 4 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 693 | `BackgroundCog._track_background_persist` | internal helper | 18 | 5 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 696 | `BackgroundCog._track_background_persist._done` | internal helper | 13 | 5 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 712 | `BackgroundCog._rollover_if_needed` | internal helper | 28 | 7 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 745 | `BackgroundCog.on_message` | event listener | 14 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 761 | `BackgroundCog.on_message_edit` | event listener | 11 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 774 | `BackgroundCog.on_message_delete` | event listener | 11 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 787 | `BackgroundCog.on_reaction_add` | event listener | 11 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 800 | `BackgroundCog.on_member_join` | event listener | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 809 | `BackgroundCog.on_member_remove` | event listener | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 818 | `BackgroundCog.on_member_ban` | event listener | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 827 | `BackgroundCog.on_member_unban` | event listener | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 836 | `BackgroundCog.on_member_update` | event listener | 10 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 848 | `BackgroundCog.on_voice_state_update` | event listener | 23 | 14 | 1 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 873 | `BackgroundCog.on_application_command_completion` | event listener | 14 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 889 | `BackgroundCog.on_application_command_error` | event listener | 15 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 909 | `BackgroundCog.update_snapshot` | background loop | 16 | 8 | 3 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 926 | `BackgroundCog._log_snapshot_failure` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 930 | `BackgroundCog._before_snapshot` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 934 | `BackgroundCog._snapshot_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 938 | `BackgroundCog.database_backup` | background loop | 33 | 13 | 5 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 973 | `BackgroundCog._before_database_backup` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 977 | `BackgroundCog._database_backup_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 981 | `BackgroundCog.rotate_status` | background loop | 35 | 10 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1018 | `BackgroundCog._before_rotate` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1022 | `BackgroundCog._rotate_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1026 | `BackgroundCog.rotate_server_icon` | background loop | 23 | 11 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1051 | `BackgroundCog._before_server_icon_rotate` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1055 | `BackgroundCog._server_icon_rotate_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1058 | `BackgroundCog._start_daily_report_loop` | internal helper | 14 | 5 | 0 | 0 | 0 | 2 broad / 2 silent | **Focused review**: 2 broad catches; 2 silent recovery paths |
| 1073 | `BackgroundCog._top_channel_lines` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1081 | `BackgroundCog._top_member_lines` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1089 | `BackgroundCog._top_command_lines` | internal helper | 7 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1097 | `BackgroundCog._summary_color` | internal helper | 8 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1106 | `BackgroundCog._send_daily_summary_for_day` | internal helper | 169 | 33 | 11 | 0 | 3 | 7 broad / 3 silent | **High attention**: 3 Discord operations; split candidate; 7 broad catches; 3 silent recovery paths |
| 1277 | `BackgroundCog.daily_report` | background loop | 12 | 4 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1291 | `BackgroundCog._before_daily` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1295 | `BackgroundCog._daily_error` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1298 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Commands.py`

126 definitions: 93 routine, 26 focused, 7 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 66 | `_fmt_num` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 73 | `_fmt_percent` | internal helper | 8 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 83 | `_ticket_status_key` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 95 | `_ticket_status_label` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 100 | `AdminDashboardView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 106 | `AdminDashboardView._show` | internal helper | 17 | 5 | 7 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 125 | `AdminDashboardView.overview` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 129 | `AdminDashboardView.config` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 133 | `AdminDashboardView.repairs` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 137 | `AdminDashboardView.refresh` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 142 | `CommandsCog.__init__` | internal helper | 76 | 13 | 5 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 200 | `CommandsCog.__init__.resync` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 204 | `CommandsCog.__init__.restart` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 208 | `CommandsCog.__init__.dance` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 212 | `CommandsCog.__init__.rps` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 216 | `CommandsCog.__init__.gambling` | slash command | 2 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 219 | `CommandsCog._in_allowed_guild` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 222 | `CommandsCog._defer` | internal helper | 7 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 230 | `CommandsCog._send` | internal helper | 4 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 235 | `CommandsCog._log_admin_action` | internal helper | 19 | 5 | 2 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 255 | `CommandsCog._impact_owner_ids` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 258 | `CommandsCog._is_impact_owner_ctx` | internal helper | 7 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 266 | `CommandsCog._backup_channel_id` | internal helper | 7 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 274 | `CommandsCog._backup_local_dir` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 278 | `CommandsCog._restore_upload_dir` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 283 | `CommandsCog._backup_retention_count` | internal helper | 6 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 290 | `CommandsCog._prune_local_backups` | internal helper | 14 | 4 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 305 | `CommandsCog._database_path` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 308 | `CommandsCog._database_storage_note` | internal helper | 36 | 21 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 345 | `CommandsCog._zip_backup_file` | internal helper | 7 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 353 | `CommandsCog._post_database_backup` | internal helper | 75 | 13 | 9 | 2 | 3 | 2 broad / 1 silent | **Focused review**: 2 persistence calls; 3 Discord operations; 2 broad catches; 1 silent recovery path |
| 429 | `CommandsCog._restore_safe_filename` | internal helper | 4 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 434 | `CommandsCog._save_restore_attachment` | internal helper | 17 | 6 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 452 | `CommandsCog._extract_sqlite_restore_file` | internal helper | 26 | 12 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 479 | `CommandsCog._validate_restore_database` | internal helper | 21 | 7 | 1 | 4 | 0 | none | **Routine**: 4 persistence calls |
| 480 | `CommandsCog._validate_restore_database._run` | internal helper | 18 | 7 | 0 | 4 | 0 | none | **Routine**: 4 persistence calls |
| 501 | `CommandsCog._impact_scalar` | internal helper | 11 | 6 | 1 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 513 | `CommandsCog._impact_float` | internal helper | 11 | 6 | 1 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 525 | `CommandsCog._impact_group_counts` | internal helper | 43 | 5 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 569 | `CommandsCog._impact_daily_totals` | internal helper | 118 | 31 | 1 | 1 | 0 | 5 broad / 4 silent | **High attention**: 1 persistence call; split candidate; 5 broad catches; 4 silent recovery paths |
| 688 | `CommandsCog._impact_window_rows` | internal helper | 14 | 7 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 703 | `CommandsCog._impact_window_sum` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 707 | `CommandsCog._impact_window_average` | internal helper | 5 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 713 | `CommandsCog._impact_percent_change` | internal helper | 9 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 723 | `CommandsCog._impact_forecast` | internal helper | 61 | 17 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 785 | `CommandsCog._collect_impact_metrics` | internal helper | 325 | 39 | 47 | 3 | 0 | 1 broad / 0 silent | **High attention**: 3 persistence calls; split candidate; 1 broad catch |
| 1111 | `CommandsCog._impact_metric_rows` | internal helper | 46 | 19 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1114 | `CommandsCog._impact_metric_rows.add` | helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1158 | `CommandsCog._impact_csv` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1165 | `CommandsCog._impact_daily_csv` | internal helper | 23 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1189 | `CommandsCog._impact_breakdown_csv` | internal helper | 18 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1208 | `CommandsCog._impact_markdown` | internal helper | 97 | 4 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1306 | `CommandsCog._impact_report_embed` | internal helper | 62 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1369 | `CommandsCog._impact_files` | internal helper | 14 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1384 | `CommandsCog.bot_impact` | helper | 72 | 17 | 17 | 1 | 2 | 4 broad / 0 silent | **Focused review**: 1 persistence call; 2 Discord operations; 4 broad catches |
| 1457 | `CommandsCog.bot_backup` | helper | 18 | 5 | 10 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1476 | `CommandsCog.bot_restore` | helper | 101 | 24 | 18 | 2 | 1 | 4 broad / 0 silent | **Focused review**: 2 persistence calls; 1 Discord operation; split candidate; 4 broad catches |
| 1578 | `CommandsCog.bot_storage` | helper | 60 | 14 | 7 | 2 | 1 | none | **Routine**: 2 persistence calls; 1 Discord operation |
| 1639 | `CommandsCog._is_admin_ctx` | internal helper | 6 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1646 | `CommandsCog._is_mod_ctx` | internal helper | 7 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1654 | `CommandsCog._request_reviewer_role_ids` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1658 | `CommandsCog._is_request_staff_ctx` | internal helper | 13 | 7 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1672 | `CommandsCog._server_icon_status_embed` | internal helper | 47 | 20 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1720 | `CommandsCog._notify_background_config_reload` | internal helper | 8 | 3 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 1729 | `CommandsCog._server_icon_operation_lock` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1734 | `CommandsCog._save_server_icon_config` | internal helper | 21 | 5 | 5 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 1756 | `CommandsCog.server_icon_status` | helper | 7 | 3 | 5 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 1764 | `CommandsCog.server_icon_mode` | helper | 20 | 6 | 7 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 1785 | `CommandsCog.server_icon_add` | helper | 29 | 9 | 10 | 0 | 7 | none | **Routine**: 7 Discord operations |
| 1815 | `CommandsCog.server_icon_replace` | helper | 32 | 12 | 9 | 0 | 6 | none | **Routine**: 6 Discord operations |
| 1848 | `CommandsCog.server_icon_remove` | helper | 30 | 11 | 7 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 1879 | `CommandsCog.server_icon_set` | helper | 23 | 7 | 9 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 1903 | `CommandsCog.server_icon_next` | helper | 14 | 5 | 8 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 1918 | `CommandsCog._resolve_member` | internal helper | 15 | 5 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 1934 | `CommandsCog._task_state` | internal helper | 13 | 8 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1948 | `CommandsCog._count_db` | internal helper | 6 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1955 | `CommandsCog._dashboard_issues` | internal helper | 86 | 32 | 1 | 0 | 0 | 1 broad / 0 silent | **High attention**: split candidate; 1 broad catch |
| 2042 | `CommandsCog._admin_dashboard_embed` | internal helper | 122 | 32 | 9 | 2 | 0 | 2 broad / 0 silent | **High attention**: 2 persistence calls; split candidate; 2 broad catches |
| 2165 | `CommandsCog.bot_dashboard` | helper | 8 | 3 | 6 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2176 | `CommandsCog.bot_health` | helper | 88 | 22 | 12 | 3 | 2 | 4 broad / 0 silent | **Focused review**: 3 persistence calls; 2 Discord operations; 4 broad catches |
| 2185 | `CommandsCog.bot_health._count` | internal helper | 6 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 2218 | `CommandsCog.bot_health._task_state` | internal helper | 13 | 8 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2265 | `CommandsCog.bot_doctor` | helper | 139 | 49 | 6 | 0 | 2 | none | **High attention**: 2 Discord operations; split candidate |
| 2280 | `CommandsCog.bot_doctor.channel_perm_report` | helper | 16 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2405 | `CommandsCog._template_variables` | internal helper | 12 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2418 | `CommandsCog._request_template_allowed_vars` | internal helper | 80 | 1 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 2499 | `CommandsCog._looks_like_color_value` | internal helper | 23 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2523 | `CommandsCog._validate_request_templates` | internal helper | 69 | 28 | 0 | 0 | 0 | none | **Focused review**: split candidate |
| 2544 | `CommandsCog._validate_request_templates.check_text` | helper | 8 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2553 | `CommandsCog._validate_request_templates.walk` | helper | 26 | 18 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 2593 | `CommandsCog.bot_config_check` | helper | 252 | 86 | 5 | 0 | 2 | 2 broad / 0 silent | **High attention**: 2 Discord operations; split candidate; 2 broad catches |
| 2604 | `CommandsCog.bot_config_check.check_channel` | helper | 13 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2618 | `CommandsCog.bot_config_check.check_role` | helper | 9 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2681 | `CommandsCog.bot_config_check.check_hhmm` | helper | 7 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2846 | `CommandsCog._parse_snowflake_arg` | internal helper | 8 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2855 | `CommandsCog._request_change_lines` | internal helper | 22 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2864 | `CommandsCog._request_change_lines.short` | helper | 5 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2878 | `CommandsCog.requests_history` | helper | 56 | 13 | 9 | 3 | 2 | 2 broad / 0 silent | **Routine**: 3 persistence calls; 2 Discord operations; 2 broad catches |
| 2935 | `CommandsCog.requests_repair` | helper | 36 | 13 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 2972 | `CommandsCog.requests_pending` | helper | 103 | 29 | 8 | 3 | 2 | 1 broad / 0 silent | **Focused review**: 3 persistence calls; 2 Discord operations; split candidate; 1 broad catch |
| 3030 | `CommandsCog.requests_pending.request_name` | helper | 8 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 3078 | `CommandsCog.tracking_top` | helper | 60 | 20 | 8 | 1 | 2 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 2 Discord operations; 1 broad catch; 1 silent recovery path |
| 3140 | `CommandsCog.tracking_me` | helper | 44 | 12 | 8 | 1 | 2 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 2 Discord operations; 1 broad catch; 1 silent recovery path |
| 3185 | `CommandsCog.tracking_force_dm` | helper | 22 | 5 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3208 | `CommandsCog.tracking_reset` | helper | 17 | 5 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3226 | `CommandsCog.tracking_disable_reward` | helper | 21 | 5 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3248 | `CommandsCog.tracking_enable_reward` | helper | 26 | 6 | 8 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3276 | `CommandsCog.ticket_close` | helper | 27 | 9 | 10 | 1 | 6 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 6 Discord operations; 1 broad catch; 1 silent recovery path |
| 3304 | `CommandsCog.ticket_status` | helper | 46 | 12 | 11 | 2 | 5 | none | **Routine**: 2 persistence calls; 5 Discord operations |
| 3351 | `CommandsCog.ticket_transcripts` | helper | 59 | 19 | 7 | 1 | 4 | none | **Focused review**: 1 persistence call; 4 Discord operations |
| 3412 | `CommandsCog._parse_channel_id` | internal helper | 10 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 3423 | `CommandsCog._configured_forum_entries` | internal helper | 29 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3453 | `CommandsCog._resolve_forum_entry` | internal helper | 41 | 15 | 0 | 0 | 0 | 4 broad / 3 silent | **Focused review**: 4 broad catches; 3 silent recovery paths |
| 3495 | `CommandsCog.forum_required_word` | helper | 93 | 31 | 16 | 0 | 9 | 3 broad / 0 silent | **High attention**: 9 Discord operations; split candidate; 3 broad catches |
| 3590 | `CommandsCog._resync` | internal helper | 33 | 9 | 11 | 0 | 3 | 3 broad / 0 silent | **Routine**: 3 Discord operations; 3 broad catches |
| 3625 | `CommandsCog._restart` | internal helper | 16 | 4 | 7 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3643 | `CommandsCog._dance` | internal helper | 7 | 3 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 3652 | `CommandsCog._rps` | internal helper | 100 | 23 | 15 | 0 | 8 | 3 broad / 1 silent | **Focused review**: 8 Discord operations; split candidate; 3 broad catches; 1 silent recovery path |
| 3668 | `CommandsCog._rps.outcome` | helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3675 | `CommandsCog._rps.RPSView.__init__` | internal helper | 12 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3688 | `CommandsCog._rps.RPSView._make_callback` | internal helper | 62 | 18 | 12 | 0 | 5 | 3 broad / 1 silent | **Focused review**: 5 Discord operations; 3 broad catches; 1 silent recovery path |
| 3689 | `CommandsCog._rps.RPSView._make_callback._cb` | internal helper | 60 | 18 | 12 | 0 | 5 | 3 broad / 1 silent | **Focused review**: 5 Discord operations; 3 broad catches; 1 silent recovery path |
| 3753 | `CommandsCog._rps_get_streak` | internal helper | 8 | 2 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3762 | `CommandsCog._rps_update_streak` | internal helper | 28 | 4 | 4 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 3792 | `CommandsCog._gambling` | internal helper | 61 | 21 | 9 | 0 | 6 | 3 broad / 2 silent | **Focused review**: 6 Discord operations; 3 broad catches; 2 silent recovery paths |
| 3854 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Help.py`

152 definitions: 130 routine, 18 focused, 4 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 27 | `_format_duration` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 48 | `_ticket_status_key` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 60 | `_ticket_status_label` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 65 | `HelpSessionControlView.__init__` | internal helper | 8 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 74 | `HelpSessionControlView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 81 | `HelpSessionControlView.back` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 87 | `HelpSessionControlView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 93 | `HelpSessionControlView.start_over` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 100 | `HelpSubmissionPreviewView.__init__` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 107 | `HelpSubmissionPreviewView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 114 | `HelpSubmissionPreviewView.submit` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 120 | `HelpSubmissionPreviewView.edit` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 126 | `HelpSubmissionPreviewView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 132 | `HelpSubmissionPreviewView.start_over` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 139 | `HelpTicketTopicView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 145 | `HelpTicketTopicView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 151 | `HelpTicketTopicView._make_topic_callback` | internal helper | 6 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 152 | `HelpTicketTopicView._make_topic_callback._callback` | internal helper | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 159 | `HelpTicketTopicView.moderation` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 163 | `HelpTicketTopicView.requests` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 167 | `HelpTicketTopicView.server` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 171 | `HelpTicketTopicView.other` | UI callback | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 175 | `HelpTicketTopicView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 181 | `HelpTicketTopicView.back` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 187 | `HelpTicketTopicView.start_over` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 194 | `FaqPageView.__init__` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 207 | `FaqPageView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 214 | `FaqPageView.previous` | UI callback | 7 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 223 | `FaqPageView.next` | UI callback | 7 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 232 | `FaqPageView.back` | UI callback | 3 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 238 | `PartnershipConfirmView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 244 | `PartnershipConfirmView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 251 | `PartnershipConfirmView.confirm` | UI callback | 3 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 256 | `PartnershipConfirmView.cancel` | UI callback | 3 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 262 | `BanInfoModal.__init__` | internal helper | 63 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 326 | `BanInfoModal.callback` | helper | 12 | 6 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 341 | `BanInfoConfirmView.__init__` | internal helper | 14 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 356 | `BanInfoConfirmView._allowed` | internal helper | 5 | 2 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 363 | `BanInfoConfirmView.confirm` | UI callback | 8 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 373 | `BanInfoConfirmView.cancel` | UI callback | 6 | 3 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 382 | `TicketSatisfactionView.__init__` | internal helper | 18 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 401 | `TicketSatisfactionView._make_callback` | internal helper | 6 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 402 | `TicketSatisfactionView._make_callback._callback` | internal helper | 4 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 412 | `HelpCog.__init__` | internal helper | 17 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 430 | `HelpCog.cog_unload` | helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 434 | `HelpCog.start_background` | helper | 19 | 7 | 6 | 0 | 0 | 3 broad / 0 silent | **Routine**: 3 broad catches |
| 454 | `HelpCog.on_config_reload` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 457 | `HelpCog._resolve_member` | internal helper | 15 | 5 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 473 | `HelpCog._help_color` | internal helper | 11 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 485 | `HelpCog._help_embed` | internal helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 494 | `HelpCog._delete_interaction_source` | internal helper | 13 | 4 | 2 | 0 | 1 | 2 broad / 2 silent | **Focused review**: 1 Discord operation; 2 broad catches; 2 silent recovery paths |
| 508 | `HelpCog._ack_and_delete_source` | internal helper | 5 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 514 | `HelpCog._respond_interaction` | internal helper | 4 | 2 | 2 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 519 | `HelpCog._cooldowns` | internal helper | 7 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 527 | `HelpCog._submission_label` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 534 | `HelpCog._submission_prefix` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 541 | `HelpCog._submission_code` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 544 | `HelpCog._attachment_data` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 555 | `HelpCog._merge_attachments` | internal helper | 11 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 567 | `HelpCog._attachments_text` | internal helper | 13 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 581 | `HelpCog._has_attachments` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 585 | `HelpCog._short_text` | internal helper | 5 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 591 | `HelpCog._embed_char_count` | internal helper | 7 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 599 | `HelpCog._add_bounded_field` | internal helper | 15 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 615 | `HelpCog._normalize_duplicate_text` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 621 | `HelpCog._ticket_scan_loop` | internal helper | 9 | 4 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 631 | `HelpCog._log_background_error` | internal helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 638 | `HelpCog._load_active_ticket_channels` | internal helper | 12 | 3 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 651 | `HelpCog._reconcile_missing_ticket_channels` | internal helper | 37 | 11 | 4 | 2 | 1 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 Discord operation; 1 broad catch |
| 689 | `HelpCog._scan_tickets` | internal helper | 60 | 13 | 8 | 3 | 3 | 3 broad / 2 silent | **Focused review**: 3 persistence calls; 3 Discord operations; 3 broad catches; 2 silent recovery paths |
| 754 | `HelpCog.on_message` | event listener | 65 | 23 | 13 | 2 | 2 | 2 broad / 1 silent | **Focused review**: 2 persistence calls; 2 Discord operations; 2 broad catches; 1 silent recovery path |
| 823 | `HelpCog._remaining_help_cooldown` | internal helper | 9 | 2 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 833 | `HelpCog._touch_help_cooldown` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 840 | `HelpCog._cooldown_until` | internal helper | 9 | 3 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 850 | `HelpCog._cooldown_embed` | internal helper | 8 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 859 | `HelpCog._flow_start_limit_message` | internal helper | 22 | 13 | 0 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 882 | `HelpCog._weekly_status_text` | internal helper | 20 | 9 | 3 | 1 | 0 | none | **Routine**: 1 persistence call |
| 903 | `HelpCog._request_result_label` | internal helper | 11 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 915 | `HelpCog._request_state_text` | internal helper | 33 | 14 | 2 | 2 | 0 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 broad catch |
| 949 | `HelpCog._active_ticket_text` | internal helper | 12 | 4 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 962 | `HelpCog._recent_help_status_text` | internal helper | 18 | 6 | 2 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 981 | `HelpCog._cooldown_status_text` | internal helper | 7 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 989 | `HelpCog._send_dm_dashboard` | internal helper | 21 | 5 | 7 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1011 | `HelpCog._send_former_member_dashboard` | internal helper | 8 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1020 | `HelpCog._home_menu_view` | internal helper | 5 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1026 | `HelpCog._faq_entries` | internal helper | 4 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1031 | `HelpCog._send_ticket_topics` | internal helper | 7 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1042 | `HelpCog.handle_help_selection` | workflow handler | 165 | 32 | 37 | 0 | 14 | none | **High attention**: 14 Discord operations; split candidate |
| 1208 | `HelpCog._send_faq` | internal helper | 8 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1217 | `HelpCog._faq_page_embed` | internal helper | 23 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1241 | `HelpCog._send_faq_page` | internal helper | 8 | 1 | 1 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1250 | `HelpCog.handle_faq_page` | workflow handler | 18 | 3 | 4 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1269 | `HelpCog._send_weekly_status` | internal helper | 41 | 16 | 7 | 2 | 2 | none | **Focused review**: 2 persistence calls; 2 Discord operations |
| 1314 | `HelpCog._start_help_session` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1321 | `HelpCog._clear_help_session` | internal helper | 2 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1324 | `HelpCog._get_help_session` | internal helper | 22 | 8 | 2 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 1347 | `HelpCog._preview_stage` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1350 | `HelpCog._edit_stage_for_kind` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1357 | `HelpCog._submission_core_text` | internal helper | 8 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1366 | `HelpCog._submission_preview_embed` | internal helper | 19 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1386 | `HelpCog._show_submission_preview` | internal helper | 7 | 1 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1394 | `HelpCog._is_duplicate_help_submission` | internal helper | 23 | 9 | 1 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 1418 | `HelpCog._submission_log_channel` | internal helper | 16 | 7 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1435 | `HelpCog._submission_staff_embed` | internal helper | 28 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1464 | `HelpCog._insert_help_submission` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1471 | `HelpCog._submit_help_submission` | internal helper | 40 | 8 | 11 | 2 | 2 | 2 broad / 1 silent | **Focused review**: 2 persistence calls; 2 Discord operations; 2 broad catches; 1 silent recovery path |
| 1512 | `HelpCog._help_max_submission_chars` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1518 | `HelpCog._handle_help_session_message` | internal helper | 165 | 26 | 27 | 2 | 10 | none | **High attention**: 2 persistence calls; 10 Discord operations; split candidate |
| 1684 | `HelpCog._handle_typed_back` | internal helper | 44 | 8 | 10 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 1729 | `HelpCog._edit_prompt_embed` | internal helper | 12 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1742 | `HelpCog.handle_help_session_control` | workflow handler | 55 | 12 | 16 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 1798 | `HelpCog.handle_help_submission_preview` | workflow handler | 5 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1804 | `HelpCog._handle_help_submission_preview_locked` | internal helper | 54 | 12 | 18 | 0 | 6 | none | **Routine**: 6 Discord operations |
| 1859 | `HelpCog._parse_ticket_reference` | internal helper | 14 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1874 | `HelpCog._staff_log_embed` | internal helper | 14 | 3 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 1889 | `HelpCog._log_help_action` | internal helper | 27 | 9 | 3 | 0 | 2 | 2 broad / 0 silent | **Routine**: 2 Discord operations; 2 broad catches |
| 1917 | `HelpCog._ticket_label` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1920 | `HelpCog._handle_staff_help_reply` | internal helper | 9 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1930 | `HelpCog._handle_staff_help_reply_locked` | internal helper | 80 | 22 | 13 | 2 | 4 | 6 broad / 4 silent | **High attention**: 2 persistence calls; 4 Discord operations; 6 broad catches; 4 silent recovery paths |
| 2014 | `HelpCog._submit_appeal` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2018 | `HelpCog._submit_report` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2022 | `HelpCog._submit_bot_issue` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2029 | `HelpCog._ban_info_code` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2032 | `HelpCog._known_user_history` | internal helper | 38 | 12 | 3 | 1 | 1 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 2071 | `HelpCog._ban_info_staff_embed` | internal helper | 43 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2115 | `HelpCog._create_ban_info_request` | internal helper | 89 | 8 | 16 | 5 | 6 | 2 broad / 1 silent | **Focused review**: 5 persistence calls; 6 Discord operations; 2 broad catches; 1 silent recovery path |
| 2205 | `HelpCog._can_handle_ban_info` | internal helper | 11 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2217 | `HelpCog.handle_ban_info_button` | workflow handler | 13 | 6 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 2231 | `HelpCog.handle_ban_info_modal` | workflow handler | 90 | 16 | 9 | 2 | 4 | 1 broad / 0 silent | **Focused review**: 2 persistence calls; 4 Discord operations; 1 broad catch |
| 2322 | `HelpCog._ban_info_delivery_embed` | internal helper | 41 | 13 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2364 | `HelpCog._update_ban_info_staff_message` | internal helper | 59 | 19 | 4 | 0 | 3 | 2 broad / 0 silent | **Focused review**: 3 Discord operations; 2 broad catches |
| 2424 | `HelpCog.finalize_ban_info` | helper | 87 | 10 | 17 | 3 | 7 | 1 broad / 0 silent | **Focused review**: 3 persistence calls; 7 Discord operations; 1 broad catch |
| 2515 | `HelpCog._create_transcript_request` | internal helper | 8 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2524 | `HelpCog._create_transcript_request_locked` | internal helper | 66 | 14 | 6 | 3 | 3 | 3 broad / 1 silent | **Focused review**: 3 persistence calls; 3 Discord operations; 3 broad catches; 1 silent recovery path |
| 2591 | `HelpCog.handle_transcript_request_decision` | workflow handler | 5 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2597 | `HelpCog._handle_transcript_request_decision_locked` | internal helper | 94 | 16 | 23 | 4 | 5 | 4 broad / 1 silent | **Focused review**: 4 persistence calls; 5 Discord operations; 4 broad catches; 1 silent recovery path |
| 2692 | `HelpCog._dm_transcript` | internal helper | 66 | 14 | 11 | 1 | 6 | 5 broad / 0 silent | **Focused review**: 1 persistence call; 6 Discord operations; 5 broad catches |
| 2762 | `HelpCog.handle_ticket_topic` | workflow handler | 10 | 4 | 5 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 2773 | `HelpCog.handle_partnership_confirmation` | workflow handler | 25 | 3 | 4 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 2799 | `HelpCog.update_ticket_opening_status` | helper | 35 | 12 | 5 | 1 | 3 | 3 broad / 0 silent | **Routine**: 1 persistence call; 3 Discord operations; 3 broad catches |
| 2835 | `HelpCog._create_staff_ticket` | internal helper | 19 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2855 | `HelpCog._create_staff_ticket_locked` | internal helper | 135 | 25 | 25 | 4 | 4 | 6 broad / 0 silent | **Focused review**: 4 persistence calls; 4 Discord operations; split candidate; 6 broad catches |
| 2991 | `HelpCog._next_ticket_id` | internal helper | 2 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2994 | `HelpCog.handle_ticket_close_prompt` | workflow handler | 48 | 15 | 11 | 2 | 4 | 2 broad / 2 silent | **Focused review**: 2 persistence calls; 4 Discord operations; 2 broad catches; 2 silent recovery paths |
| 3043 | `HelpCog._send_ticket_satisfaction_prompt` | internal helper | 51 | 15 | 7 | 2 | 3 | 3 broad / 1 silent | **Focused review**: 2 persistence calls; 3 Discord operations; 3 broad catches; 1 silent recovery path |
| 3095 | `HelpCog._restore_ticket_satisfaction_views` | internal helper | 20 | 4 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 3116 | `HelpCog.handle_ticket_satisfaction` | workflow handler | 44 | 8 | 9 | 2 | 6 | 2 broad / 0 silent | **Routine**: 2 persistence calls; 6 Discord operations; 2 broad catches |
| 3161 | `HelpCog.close_ticket_channel` | helper | 5 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3167 | `HelpCog._close_ticket_channel_locked` | internal helper | 142 | 35 | 32 | 6 | 8 | 14 broad / 4 silent | **High attention**: 6 persistence calls; 8 Discord operations; split candidate; 14 broad catches; 4 silent recovery paths |
| 3192 | `HelpCog._close_ticket_channel_locked._restore_open_status` | internal helper | 11 | 2 | 3 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 3249 | `HelpCog._close_ticket_channel_locked._cleanup_transcript_artifact` | internal helper | 21 | 5 | 4 | 1 | 1 | 2 broad / 1 silent | **Focused review**: 1 persistence call; 1 Discord operation; 2 broad catches; 1 silent recovery path |
| 3311 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

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
| 88 | `MessageResponsesCog._cooldown_ok` | internal helper | 15 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 105 | `MessageResponsesCog.on_message` | event listener | 87 | 39 | 7 | 0 | 2 | 2 broad / 1 silent | **High attention**: 2 Discord operations; split candidate; 2 broad catches; 1 silent recovery path |
| 194 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/Mod.py`

11 definitions: 7 routine, 4 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 14 | `_review_access_text` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 18 | `_within_one_edit` | internal helper | 22 | 11 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 43 | `ModCog.__init__` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 49 | `ModCog.on_message` | event listener | 48 | 14 | 5 | 0 | 2 | 2 broad / 0 silent | **Routine**: 2 Discord operations; 2 broad catches |
| 98 | `ModCog._handle_review_access_message` | internal helper | 69 | 19 | 10 | 0 | 5 | 5 broad / 2 silent | **Focused review**: 5 Discord operations; 5 broad catches; 2 silent recovery paths |
| 168 | `ModCog._dm_templates_for_role` | internal helper | 24 | 11 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 193 | `ModCog._send_role_dm` | internal helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 197 | `ModCog._send_role_dm_locked` | internal helper | 28 | 7 | 2 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 227 | `ModCog.on_raw_reaction_add` | event listener | 52 | 15 | 5 | 0 | 2 | 2 broad / 0 silent | **Focused review**: 2 Discord operations; 2 broad catches |
| 281 | `ModCog.on_member_update` | event listener | 50 | 18 | 3 | 0 | 1 | 2 broad / 1 silent | **Focused review**: 1 Discord operation; 2 broad catches; 1 silent recovery path |
| 332 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `cogs/RequestLevels.py`

158 definitions: 135 routine, 16 focused, 7 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 80 | `_SafeDict.__missing__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 85 | `LevelRequestModal.__init__` | internal helper | 39 | 9 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 125 | `LevelRequestModal.callback` | helper | 15 | 8 | 3 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 143 | `ReviewModal.__init__` | internal helper | 13 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 157 | `ReviewModal.callback` | helper | 7 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 167 | `FirstRequestChoiceView.__init__` | internal helper | 8 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 176 | `FirstRequestChoiceView._will` | internal helper | 4 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 183 | `OtherReasonView.__init__` | internal helper | 9 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 193 | `OtherReasonView._make_callback` | internal helper | 4 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 194 | `OtherReasonView._make_callback._callback` | internal helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 200 | `ScheduledOpeningEditModal.__init__` | internal helper | 47 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 248 | `ScheduledOpeningEditModal.callback` | helper | 12 | 7 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 263 | `ScheduledOpeningsView.__init__` | internal helper | 33 | 10 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 297 | `ScheduledOpeningsView._allowed` | internal helper | 12 | 5 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 310 | `ScheduledOpeningsView._select` | internal helper | 8 | 3 | 2 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 319 | `ScheduledOpeningsView._refresh` | internal helper | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 325 | `ScheduledOpeningsView._edit` | internal helper | 15 | 6 | 4 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 341 | `ScheduledOpeningsView._delete` | internal helper | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 347 | `ScheduledOpeningsView._open_now` | internal helper | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 355 | `ScheduledOpenNowConfirmView.__init__` | internal helper | 5 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 361 | `ScheduledOpenNowConfirmView._allowed` | internal helper | 12 | 5 | 3 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 375 | `ScheduledOpenNowConfirmView.confirm` | UI callback | 5 | 2 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 382 | `ScheduledOpenNowConfirmView.cancel` | UI callback | 4 | 2 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 389 | `RequestLevelsCog.__init__` | internal helper | 64 | 3 | 6 | 0 | 0 | none | **Routine**: small, direct control flow |
| 413 | `RequestLevelsCog.__init__.refresh_request_button` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 417 | `RequestLevelsCog.__init__.open_requests` | slash command | 10 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 429 | `RequestLevelsCog.__init__.close_requests` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 433 | `RequestLevelsCog.__init__.requests_are` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 437 | `RequestLevelsCog.__init__.edit_request` | slash command | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 441 | `RequestLevelsCog.__init__.pending_openings` | slash command | 12 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 454 | `RequestLevelsCog.cog_unload` | helper | 14 | 7 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 469 | `RequestLevelsCog.start_background` | helper | 11 | 8 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 481 | `RequestLevelsCog.on_config_reload` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 484 | `RequestLevelsCog._start_background_task` | internal helper | 13 | 3 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 485 | `RequestLevelsCog._start_background_task.runner` | helper | 7 | 3 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 498 | `RequestLevelsCog._cfg` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 501 | `RequestLevelsCog._cfg_int` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 504 | `RequestLevelsCog._cfg_int_list` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 507 | `RequestLevelsCog._reviewer_role_ids` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 511 | `RequestLevelsCog._post_close_edit_seconds` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 515 | `RequestLevelsCog._edit_deadline_ts_for_state` | internal helper | 19 | 7 | 0 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 535 | `RequestLevelsCog._edit_window_text` | internal helper | 6 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 542 | `RequestLevelsCog._can_edit_submission` | internal helper | 27 | 13 | 0 | 0 | 0 | 3 broad / 1 silent | **Focused review**: 3 broad catches; 1 silent recovery path |
| 570 | `RequestLevelsCog._current_user_submission` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 576 | `RequestLevelsCog._current_user_submission_local` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 582 | `RequestLevelsCog._latest_editable_user_submission` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 589 | `RequestLevelsCog._editable_user_submission_for_modal` | internal helper | 13 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 603 | `RequestLevelsCog._state_after_timed_close_check` | internal helper | 12 | 6 | 2 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 616 | `RequestLevelsCog._request_initial_values` | internal helper | 5 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 622 | `RequestLevelsCog._message` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 625 | `RequestLevelsCog._message_formatted` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 628 | `RequestLevelsCog._request_button_label` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 631 | `RequestLevelsCog._request_type_normalize_text` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 637 | `RequestLevelsCog._normalize_request_type` | internal helper | 9 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 647 | `RequestLevelsCog._request_type_label` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 653 | `RequestLevelsCog._request_type_help` | internal helper | 2 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 656 | `RequestLevelsCog._request_type_from_row` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 663 | `RequestLevelsCog._clean_open_message` | internal helper | 7 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 671 | `RequestLevelsCog._request_open_condition_text` | internal helper | 11 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 683 | `RequestLevelsCog._send_open_announcement` | internal helper | 55 | 23 | 4 | 0 | 1 | 3 broad / 0 silent | **Focused review**: 1 Discord operation; 3 broad catches |
| 739 | `RequestLevelsCog._color_name` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 742 | `RequestLevelsCog._format` | internal helper | 5 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 748 | `RequestLevelsCog._submitted_ago` | internal helper | 6 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 755 | `RequestLevelsCog._clean_level_id` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 758 | `RequestLevelsCog._normalize_level_id` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 761 | `RequestLevelsCog._valid_url` | internal helper | 12 | 7 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 774 | `RequestLevelsCog._validate_request_data` | internal helper | 10 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 785 | `RequestLevelsCog._level_validation_cfg` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 789 | `RequestLevelsCog._level_validation_enabled` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 793 | `RequestLevelsCog._level_validation_cache_seconds` | internal helper | 5 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 799 | `RequestLevelsCog._level_validation_timeout_seconds` | internal helper | 5 | 2 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 805 | `RequestLevelsCog._level_validation_message` | internal helper | 5 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 811 | `RequestLevelsCog._level_validation_providers` | internal helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 820 | `RequestLevelsCog._level_validation_rate_limit_message` | internal helper | 36 | 17 | 0 | 0 | 0 | 3 broad / 0 silent | **Focused review**: 3 broad catches |
| 857 | `RequestLevelsCog._provider_failure_cfg` | internal helper | 11 | 3 | 0 | 0 | 0 | 2 broad / 0 silent | **Routine**: 2 broad catches |
| 869 | `RequestLevelsCog._provider_circuit_open` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 876 | `RequestLevelsCog._record_provider_validation_result` | internal helper | 9 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 886 | `RequestLevelsCog._provider_min_interval` | internal helper | 11 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 898 | `RequestLevelsCog._fetch_validation_provider` | internal helper | 20 | 5 | 3 | 0 | 0 | none | **Routine**: small, direct control flow |
| 919 | `RequestLevelsCog._get_level_validation_session` | internal helper | 17 | 7 | 1 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 937 | `RequestLevelsCog._safe_json_loads` | internal helper | 5 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 943 | `RequestLevelsCog._cached_level_validation` | internal helper | 17 | 5 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 961 | `RequestLevelsCog._lookup_level_validation` | internal helper | 62 | 20 | 7 | 1 | 0 | 1 broad / 0 silent | **Focused review**: 1 persistence call; 1 broad catch |
| 1024 | `RequestLevelsCog._apply_level_validation_vars` | internal helper | 81 | 30 | 0 | 0 | 0 | 1 broad / 0 silent | **High attention**: split candidate; 1 broad catch |
| 1106 | `RequestLevelsCog._validate_level_external` | internal helper | 28 | 14 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1135 | `RequestLevelsCog._request_type_validation_error` | internal helper | 32 | 23 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1168 | `RequestLevelsCog._has_reviewer_role` | internal helper | 3 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1172 | `RequestLevelsCog._is_reviewer_interaction` | internal helper | 5 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1178 | `RequestLevelsCog._embed_from_template` | internal helper | 50 | 21 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 1229 | `RequestLevelsCog._reply_ephemeral` | internal helper | 5 | 2 | 2 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 1235 | `RequestLevelsCog._log_request_admin_action` | internal helper | 23 | 7 | 2 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 1259 | `RequestLevelsCog._state_label` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1262 | `RequestLevelsCog._request_button_embed` | internal helper | 15 | 8 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1278 | `RequestLevelsCog._pct` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1283 | `RequestLevelsCog._wave_summary_vars` | internal helper | 47 | 20 | 2 | 1 | 0 | none | **Focused review**: 1 persistence call |
| 1331 | `RequestLevelsCog._reviewer_stats_lines` | internal helper | 21 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1353 | `RequestLevelsCog._wave_summary_embed` | internal helper | 32 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1386 | `RequestLevelsCog.update_wave_summary` | helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1390 | `RequestLevelsCog._update_wave_summary_unlocked` | internal helper | 77 | 19 | 15 | 4 | 8 | 6 broad / 3 silent | **Focused review**: 4 persistence calls; 8 Discord operations; 6 broad catches; 3 silent recovery paths |
| 1468 | `RequestLevelsCog._base_state_vars` | internal helper | 25 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1494 | `RequestLevelsCog._row_value` | internal helper | 7 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1502 | `RequestLevelsCog._duplicate_history_warning` | internal helper | 25 | 6 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1528 | `RequestLevelsCog._days_in_month` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1531 | `RequestLevelsCog._add_month` | internal helper | 5 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1537 | `RequestLevelsCog._parse_scheduled_open_ts` | internal helper | 31 | 14 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1569 | `RequestLevelsCog._scheduled_opening_rows` | internal helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1576 | `RequestLevelsCog.get_scheduled_opening` | helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1583 | `RequestLevelsCog._scheduled_openings_embed` | internal helper | 37 | 14 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1621 | `RequestLevelsCog.refresh_pending_openings_panel` | helper | 5 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 1627 | `RequestLevelsCog.delete_scheduled_opening` | helper | 16 | 2 | 6 | 1 | 2 | none | **Routine**: 1 persistence call; 2 Discord operations |
| 1644 | `RequestLevelsCog.open_scheduled_opening_now` | helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1648 | `RequestLevelsCog._open_scheduled_opening_now_locked` | internal helper | 44 | 11 | 9 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 1693 | `RequestLevelsCog.handle_scheduled_opening_edit_modal` | workflow handler | 68 | 20 | 13 | 1 | 9 | 2 broad / 0 silent | **Focused review**: 1 persistence call; 9 Discord operations; 2 broad catches |
| 1717 | `RequestLevelsCog.handle_scheduled_opening_edit_modal.optional_positive` | helper | 11 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1762 | `RequestLevelsCog._data_vars` | internal helper | 60 | 35 | 0 | 0 | 0 | 1 broad / 0 silent | **High attention**: split candidate; 1 broad catch |
| 1823 | `RequestLevelsCog._weekly_data_vars` | internal helper | 27 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1851 | `RequestLevelsCog._result_label` | internal helper | 6 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1858 | `RequestLevelsCog._status_channel_id` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1863 | `RequestLevelsCog._result_template_key` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1870 | `RequestLevelsCog._get_state` | internal helper | 9 | 2 | 3 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 1880 | `RequestLevelsCog._get_state_local` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1886 | `RequestLevelsCog._set_state_closed` | internal helper | 31 | 7 | 6 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 1918 | `RequestLevelsCog._open_requests_now` | internal helper | 88 | 20 | 11 | 1 | 0 | 2 broad / 0 silent | **Focused review**: 1 persistence call; 2 broad catches |
| 2007 | `RequestLevelsCog._auto_close_loop` | internal helper | 17 | 9 | 5 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2025 | `RequestLevelsCog._scheduled_open_loop` | internal helper | 44 | 14 | 8 | 1 | 0 | 2 broad / 0 silent | **Routine**: 1 persistence call; 2 broad catches |
| 2070 | `RequestLevelsCog._in_allowed_guild` | internal helper | 2 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2073 | `RequestLevelsCog._defer_command` | internal helper | 5 | 3 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2079 | `RequestLevelsCog._cached_interaction_member` | internal helper | 6 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2086 | `RequestLevelsCog._resolve_member` | internal helper | 18 | 5 | 1 | 0 | 1 | 2 broad / 0 silent | **Routine**: 1 Discord operation; 2 broad catches |
| 2105 | `RequestLevelsCog._is_admin` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2108 | `RequestLevelsCog._is_mod` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2116 | `RequestLevelsCog._configured_channel` | internal helper | 9 | 6 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 2126 | `RequestLevelsCog.refresh_or_create_request_button` | helper | 3 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2130 | `RequestLevelsCog._refresh_or_create_request_button_unlocked` | internal helper | 63 | 19 | 13 | 2 | 8 | 6 broad / 3 silent | **Focused review**: 2 persistence calls; 8 Discord operations; 6 broad catches; 3 silent recovery paths |
| 2194 | `RequestLevelsCog.refresh_request_button` | helper | 13 | 5 | 8 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 2208 | `RequestLevelsCog.open_requests` | helper | 83 | 37 | 15 | 1 | 9 | none | **High attention**: 1 persistence call; 9 Discord operations; split candidate |
| 2292 | `RequestLevelsCog.pending_openings` | helper | 96 | 34 | 25 | 3 | 15 | none | **High attention**: 3 persistence calls; 15 Discord operations; split candidate |
| 2389 | `RequestLevelsCog.close_requests` | helper | 16 | 5 | 10 | 0 | 4 | none | **Routine**: 4 Discord operations |
| 2406 | `RequestLevelsCog.requests_are` | helper | 15 | 7 | 4 | 0 | 2 | none | **Routine**: 2 Discord operations |
| 2422 | `RequestLevelsCog._requirements_ok` | internal helper | 8 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2431 | `RequestLevelsCog.handle_request_button` | workflow handler | 67 | 17 | 14 | 0 | 11 | none | **Focused review**: 11 Discord operations |
| 2499 | `RequestLevelsCog.edit_request` | helper | 18 | 3 | 4 | 0 | 3 | none | **Routine**: 3 Discord operations |
| 2518 | `RequestLevelsCog.handle_first_choice` | workflow handler | 20 | 7 | 5 | 0 | 4 | 1 broad / 0 silent | **Routine**: 4 Discord operations; 1 broad catch |
| 2539 | `RequestLevelsCog.handle_request_form` | workflow handler | 212 | 33 | 40 | 7 | 3 | 8 broad / 1 silent | **High attention**: 7 persistence calls; 3 Discord operations; split candidate; 8 broad catches; 1 silent recovery path |
| 2752 | `RequestLevelsCog.handle_request_edit_form` | workflow handler | 181 | 33 | 32 | 4 | 2 | 4 broad / 0 silent | **High attention**: 4 persistence calls; 2 Discord operations; split candidate; 4 broad catches |
| 2934 | `RequestLevelsCog._refresh_closed_wave` | internal helper | 7 | 3 | 3 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 2942 | `RequestLevelsCog._submission_by_message` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2948 | `RequestLevelsCog._weekly_submission_by_message` | internal helper | 5 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2954 | `RequestLevelsCog._review_target_by_message` | internal helper | 8 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 2963 | `RequestLevelsCog._review_target_by_message_local` | internal helper | 24 | 4 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 2988 | `RequestLevelsCog._channel_by_id` | internal helper | 8 | 6 | 1 | 0 | 1 | 1 broad / 0 silent | **Routine**: 1 Discord operation; 1 broad catch |
| 2997 | `RequestLevelsCog._review_target_channel` | internal helper | 4 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3002 | `RequestLevelsCog.handle_review_button` | workflow handler | 16 | 8 | 7 | 0 | 6 | none | **Routine**: 6 Discord operations |
| 3019 | `RequestLevelsCog.handle_review_submission` | workflow handler | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3022 | `RequestLevelsCog.handle_other_reason` | workflow handler | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 3025 | `RequestLevelsCog._finalize_review` | internal helper | 102 | 23 | 27 | 2 | 4 | 6 broad / 1 silent | **Focused review**: 2 persistence calls; 4 Discord operations; split candidate; 6 broad catches; 1 silent recovery path |
| 3128 | `RequestLevelsCog.repair_request_system` | helper | 266 | 68 | 39 | 11 | 11 | 15 broad / 5 silent | **High attention**: 11 persistence calls; 11 Discord operations; split candidate; 15 broad catches; 5 silent recovery paths |
| 3396 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

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

60 definitions: 43 routine, 14 focused, 3 high attention.

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
| 438 | `TrackingCog.user_in_weekly_process` | helper | 9 | 2 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 448 | `TrackingCog.weekly_reward_disabled` | helper | 6 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 455 | `TrackingCog.disable_weekly_reward_for_current_week` | helper | 21 | 1 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 477 | `TrackingCog.enable_weekly_reward_for_current_week` | helper | 31 | 1 | 3 | 1 | 0 | none | **Routine**: 1 persistence call |
| 512 | `TrackingCog._weekly_log_meta` | internal helper | 28 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 541 | `TrackingCog._weekly_detail_lines` | internal helper | 14 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 556 | `TrackingCog._log_weekly` | internal helper | 41 | 10 | 5 | 1 | 2 | 3 broad / 0 silent | **Routine**: 1 persistence call; 2 Discord operations; 3 broad catches |
| 598 | `TrackingCog._anti_farm_cfg` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 602 | `TrackingCog._anti_farm_enabled` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 605 | `TrackingCog._message_signature` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 610 | `TrackingCog._anti_farm_reason` | internal helper | 45 | 22 | 0 | 0 | 0 | 4 broad / 0 silent | **Focused review**: 4 broad catches |
| 656 | `TrackingCog._record_anti_farm_event` | internal helper | 49 | 18 | 5 | 1 | 2 | 4 broad / 0 silent | **Focused review**: 1 persistence call; 2 Discord operations; 4 broad catches |
| 710 | `TrackingCog.on_message` | event listener | 62 | 17 | 5 | 1 | 0 | none | **Focused review**: 1 persistence call |
| 773 | `TrackingCog._activity_flush_loop` | internal helper | 10 | 5 | 4 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 784 | `TrackingCog.flush_activity_counts` | helper | 41 | 12 | 4 | 2 | 0 | 2 broad / 0 silent | **Routine**: 2 persistence calls; 2 broad catches |
| 829 | `TrackingCog._handle_dm` | internal helper | 81 | 17 | 10 | 2 | 5 | 5 broad / 4 silent | **High attention**: 2 persistence calls; 5 Discord operations; 5 broad catches; 4 silent recovery paths |
| 911 | `TrackingCog._record_request` | internal helper | 133 | 20 | 23 | 2 | 11 | 8 broad / 4 silent | **High attention**: 2 persistence calls; 11 Discord operations; split candidate; 8 broad catches; 4 silent recovery paths |
| 1046 | `TrackingCog.handle_decline_confirm` | workflow handler | 68 | 9 | 12 | 3 | 2 | 2 broad / 2 silent | **Focused review**: 3 persistence calls; 2 Discord operations; 2 broad catches; 2 silent recovery paths |
| 1118 | `TrackingCog._weekly_loop` | internal helper | 43 | 8 | 8 | 3 | 0 | 2 broad / 1 silent | **Focused review**: 3 persistence calls; 2 broad catches; 1 silent recovery path |
| 1162 | `TrackingCog._weekly_recap_due` | internal helper | 12 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1175 | `TrackingCog._weekly_recap_loop` | internal helper | 11 | 4 | 5 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1187 | `TrackingCog._timeout_loop` | internal helper | 12 | 4 | 6 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1200 | `TrackingCog._process_timeouts` | internal helper | 49 | 6 | 9 | 3 | 2 | 1 broad / 0 silent | **Routine**: 3 persistence calls; 2 Discord operations; 1 broad catch |
| 1250 | `TrackingCog._update_weekly_streaks` | internal helper | 45 | 21 | 4 | 4 | 0 | 2 broad / 0 silent | **Focused review**: 4 persistence calls; 2 broad catches |
| 1296 | `TrackingCog._send_weekly_recap` | internal helper | 112 | 38 | 12 | 7 | 3 | 6 broad / 1 silent | **High attention**: 7 persistence calls; 3 Discord operations; split candidate; 6 broad catches; 1 silent recovery path |
| 1409 | `TrackingCog._ranked_rows_for_week` | internal helper | 45 | 13 | 2 | 1 | 1 | 1 broad / 1 silent | **Focused review**: 1 persistence call; 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 1455 | `TrackingCog._send_missing_weekly_recap_once` | internal helper | 28 | 7 | 6 | 2 | 0 | 1 broad / 0 silent | **Routine**: 2 persistence calls; 1 broad catch |
| 1487 | `TrackingCog.run_weekly_job` | helper | 43 | 9 | 10 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 1531 | `TrackingCog._contact_user_for_week` | internal helper | 104 | 19 | 18 | 4 | 4 | 4 broad / 0 silent | **Focused review**: 4 persistence calls; 4 Discord operations; split candidate; 4 broad catches |
| 1636 | `TrackingCog._contact_next_eligible` | internal helper | 61 | 10 | 8 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1701 | `TrackingCog._format_deadline` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1704 | `TrackingCog._build_request_dm_text` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1708 | `TrackingCog._build_request_dm_message` | internal helper | 15 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1724 | `TrackingCog._build_reminder_text` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1728 | `TrackingCog._build_reminder_message` | internal helper | 14 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1743 | `TrackingCog._process_reminders` | internal helper | 50 | 11 | 7 | 3 | 2 | 1 broad / 0 silent | **Routine**: 3 persistence calls; 2 Discord operations; 1 broad catch |
| 1797 | `TrackingCog.get_top` | helper | 8 | 2 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1806 | `TrackingCog.get_member_stats` | helper | 39 | 17 | 4 | 2 | 0 | none | **Focused review**: 2 persistence calls |
| 1846 | `TrackingCog.force_dm_for_user` | helper | 62 | 16 | 14 | 3 | 0 | none | **Focused review**: 3 persistence calls |
| 1909 | `TrackingCog.reset_current_week` | helper | 20 | 8 | 2 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1931 | `setup` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `main.py`

17 definitions: 12 routine, 5 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 38 | `startup_log` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 42 | `_discord_login_retry_seconds` | internal helper | 8 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 52 | `_startup_error_retry_seconds` | internal helper | 8 | 3 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 62 | `_prepare_fresh_event_loop` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 66 | `_compact_startup_exception` | internal helper | 12 | 7 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 80 | `_is_discord_startup_rate_limit` | internal helper | 6 | 6 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 88 | `_run_preflight_database_check` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 94 | `_close_runtime_storage` | internal helper | 24 | 6 | 4 | 1 | 0 | 3 broad / 0 silent | **Routine**: 1 persistence call; 3 broad catches |
| 120 | `_install_storage_close_hook` | internal helper | 13 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 124 | `_install_storage_close_hook.close_with_storage_flush` | helper | 7 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |
| 135 | `_database_path_usable` | internal helper | 13 | 3 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 150 | `resolve_db_path` | helper | 68 | 24 | 0 | 0 | 0 | none | **Focused review**: small, direct control flow |
| 219 | `create_bot` | helper | 120 | 17 | 11 | 0 | 0 | 6 broad / 1 silent | **Focused review**: split candidate; 6 broad catches; 1 silent recovery path |
| 246 | `create_bot._load_cogs` | internal helper | 9 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 257 | `create_bot.on_ready` | helper | 61 | 14 | 11 | 0 | 0 | 5 broad / 1 silent | **Focused review**: 5 broad catches; 1 silent recovery path |
| 319 | `create_bot.register_persistent_views` | helper | 9 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 340 | `run_bot_with_startup_backoff` | helper | 88 | 14 | 0 | 0 | 0 | 3 broad / 1 silent | **Focused review**: 3 broad catches; 1 silent recovery path |

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
| 22 | `DictRow.__init__` | internal helper | 3 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 26 | `DictRow.__getitem__` | internal helper | 4 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 32 | `_row_get` | internal helper | 11 | 4 | 0 | 0 | 0 | 2 broad / 1 silent | **Focused review**: 2 broad catches; 1 silent recovery path |
| 45 | `_normalize_row` | internal helper | 14 | 8 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 61 | `_normalize_rows` | internal helper | 2 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 65 | `_fetchall` | internal helper | 2 | 2 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 69 | `_jwt_payload` | internal helper | 12 | 5 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 83 | `_token_scope_names` | internal helper | 9 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 94 | `_looks_like_turso_platform_token` | internal helper | 12 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 108 | `_is_recoverable_remote_error` | internal helper | 19 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 137 | `Database.__init__` | internal helper | 13 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 151 | `Database._close_connection_sync` | internal helper | 9 | 3 | 0 | 0 | 0 | 1 broad / 1 silent | **Focused review**: 1 broad catch; 1 silent recovery path |
| 161 | `Database._reopen_connection_sync` | internal helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 166 | `Database._open_connection_sync` | internal helper | 25 | 7 | 0 | 3 | 0 | 2 broad / 2 silent | **Focused review**: 3 persistence calls; 2 broad catches; 2 silent recovery paths |
| 192 | `Database._sync_remote_sync` | internal helper | 6 | 4 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 199 | `Database._sync_remote_with_retry_sync` | internal helper | 16 | 7 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 216 | `Database._try_pending_remote_sync_sync` | internal helper | 18 | 6 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 235 | `Database._commit_and_sync_sync` | internal helper | 23 | 4 | 0 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 259 | `Database._run_locked_with_retry` | internal helper | 32 | 11 | 5 | 0 | 0 | 1 broad / 0 silent | **Routine**: 1 broad catch |
| 292 | `Database.connect` | helper | 15 | 4 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 297 | `Database.connect._connect_and_migrate` | internal helper | 7 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 308 | `Database.close` | helper | 6 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 315 | `Database.backup_to` | helper | 41 | 6 | 2 | 3 | 0 | 0 broad / 1 silent | **Focused review**: 3 persistence calls; 1 silent recovery path |
| 322 | `Database.backup_to._backup` | internal helper | 32 | 6 | 0 | 3 | 0 | 0 broad / 1 silent | **Focused review**: 3 persistence calls; 1 silent recovery path |
| 357 | `Database.restore_from` | helper | 91 | 15 | 1 | 8 | 0 | 2 broad / 3 silent | **Focused review**: 8 persistence calls; 2 broad catches; 3 silent recovery paths |
| 374 | `Database.restore_from._unlink_sidecars` | internal helper | 6 | 3 | 0 | 0 | 0 | 0 broad / 1 silent | **Focused review**: 1 silent recovery path |
| 381 | `Database.restore_from._connect_current` | internal helper | 9 | 1 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 391 | `Database.restore_from._restore` | internal helper | 55 | 10 | 0 | 6 | 0 | 2 broad / 2 silent | **Focused review**: 6 persistence calls; 2 broad catches; 2 silent recovery paths |
| 449 | `Database._migrate_sync` | internal helper | 429 | 7 | 0 | 2 | 0 | none | **High attention**: 2 persistence calls; split candidate |
| 879 | `Database._ensure_column_sync` | internal helper | 7 | 3 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 887 | `Database._normalize_weekly_dm_log_sync` | internal helper | 33 | 8 | 0 | 6 | 0 | none | **Routine**: 6 persistence calls |
| 921 | `Database._init_ticket_sequences_sync` | internal helper | 24 | 8 | 0 | 7 | 0 | none | **Routine**: 7 persistence calls |
| 946 | `Database.next_ticket_id` | helper | 16 | 3 | 1 | 4 | 0 | none | **Routine**: 4 persistence calls |
| 947 | `Database.next_ticket_id._run` | internal helper | 13 | 3 | 0 | 4 | 0 | none | **Routine**: 4 persistence calls |
| 963 | `Database.execute` | helper | 7 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 964 | `Database.execute._run` | internal helper | 4 | 1 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 971 | `Database.execute_insert` | helper | 14 | 3 | 1 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 974 | `Database.execute_insert._run` | internal helper | 9 | 3 | 0 | 3 | 0 | none | **Routine**: 3 persistence calls |
| 986 | `Database.execute_transaction` | helper | 26 | 6 | 1 | 2 | 0 | 2 broad / 1 silent | **Focused review**: 2 persistence calls; 2 broad catches; 1 silent recovery path |
| 997 | `Database.execute_transaction._run` | internal helper | 13 | 4 | 0 | 2 | 0 | 2 broad / 1 silent | **Focused review**: 2 persistence calls; 2 broad catches; 1 silent recovery path |
| 1013 | `Database.set_runtime_setting` | helper | 7 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1021 | `Database.get_runtime_setting` | helper | 11 | 4 | 1 | 1 | 0 | 1 broad / 0 silent | **Routine**: 1 persistence call; 1 broad catch |
| 1033 | `Database.sync_remote` | helper | 14 | 2 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1038 | `Database.sync_remote._run` | internal helper | 7 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 1048 | `Database.executemany` | helper | 9 | 1 | 1 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1051 | `Database.executemany._run` | internal helper | 4 | 1 | 0 | 1 | 0 | none | **Routine**: 1 persistence call |
| 1058 | `Database.fetchone` | helper | 7 | 1 | 1 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1059 | `Database.fetchone._run` | internal helper | 4 | 1 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1066 | `Database.fetchone_local` | helper | 14 | 1 | 1 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1074 | `Database.fetchone_local._run` | internal helper | 4 | 1 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1081 | `Database.fetchall` | helper | 7 | 1 | 1 | 2 | 0 | none | **Routine**: 2 persistence calls |
| 1082 | `Database.fetchall._run` | internal helper | 4 | 1 | 0 | 2 | 0 | none | **Routine**: 2 persistence calls |

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
| 100 | `log_error` | helper | 65 | 16 | 3 | 0 | 3 | 7 broad / 3 silent | **Focused review**: 3 Discord operations; 7 broad catches; 3 silent recovery paths |
| 166 | `setup_global_error_handlers` | helper | 20 | 3 | 3 | 0 | 1 | 1 broad / 1 silent | **Focused review**: 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 168 | `setup_global_error_handlers.on_application_command_error` | helper | 14 | 3 | 2 | 0 | 1 | 1 broad / 1 silent | **Focused review**: 1 Discord operation; 1 broad catch; 1 silent recovery path |
| 184 | `setup_global_error_handlers.on_error` | helper | 2 | 1 | 1 | 0 | 0 | none | **Routine**: small, direct control flow |

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

11 definitions: 11 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 26 | `set_keepalive_status` | helper | 17 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 45 | `get_keepalive_status` | helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 51 | `_HealthHandler._health_response` | internal helper | 13 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 65 | `_HealthHandler._send_health_headers` | internal helper | 6 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 72 | `_HealthHandler.do_GET` | helper | 4 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 77 | `_HealthHandler.do_HEAD` | helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 81 | `_HealthHandler.log_message` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 85 | `start_keepalive_thread` | helper | 19 | 3 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 93 | `start_keepalive_thread._run` | internal helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 106 | `_handle` | internal helper | 8 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 115 | `start_keepalive` | helper | 16 | 3 | 2 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/mentions.py`

3 definitions: 3 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 6 | `no_mentions` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 10 | `user_mentions` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 14 | `user_and_role_mentions` | helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

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

1 definitions: 1 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 7 | `build_text_transcript` | helper | 14 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |

### `utils/views.py`

22 definitions: 22 routine, 0 focused, 0 high attention.

| Line | Definition | Kind | LOC | CC | Await | DB | Discord | Recovery | Assessment |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 27 | `TranscriptRequestView.__init__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 31 | `TranscriptRequestView.approve` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 43 | `TranscriptRequestView.deny` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 56 | `TicketClosePromptView.__init__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 60 | `TicketClosePromptView.yes` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 72 | `TicketClosePromptView.no` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 85 | `_HelpMenuSelect.__init__` | internal helper | 62 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 148 | `_HelpMenuSelect.callback` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 161 | `HelpMenuView.__init__` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 167 | `_FormerMemberHelpSelect.__init__` | internal helper | 22 | 5 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 190 | `_FormerMemberHelpSelect.callback` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 203 | `FormerMemberHelpView.__init__` | internal helper | 3 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 209 | `BanInfoGiveInfoView.__init__` | internal helper | 10 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 220 | `BanInfoGiveInfoView.give_info` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 233 | `TrackingDeclineConfirmView.__init__` | internal helper | 2 | 1 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 237 | `TrackingDeclineConfirmView.yes` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 249 | `TrackingDeclineConfirmView.no` | UI callback | 10 | 2 | 2 | 0 | 1 | none | **Routine**: verify interaction deadline; 1 Discord operation |
| 262 | `LevelRequestButtonView.__init__` | internal helper | 10 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 273 | `LevelRequestButtonView.request` | helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 286 | `LevelRequestReviewView.__init__` | internal helper | 10 | 2 | 0 | 0 | 0 | none | **Routine**: small, direct control flow |
| 297 | `LevelRequestReviewView._make_callback` | internal helper | 12 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |
| 298 | `LevelRequestReviewView._make_callback._callback` | internal helper | 10 | 2 | 2 | 0 | 1 | none | **Routine**: 1 Discord operation |

## Residual Operational Risk

The static review cannot simulate Discord permissions, role hierarchy, deleted live messages, third-party API outages, or a process termination in the narrow interval between a Discord action and its database compensation. Those cases are contained by durable states, repair commands, idempotent checks, logging, and startup reconciliation, but should still be exercised after deployment.

The largest functions are concentrated in configuration diagnostics, impact aggregation, request repair, request form orchestration, daily summaries, and ticket closure. They are covered by focused checks and are valid today, but they are the best future refactoring targets because each coordinates several external boundaries.

## Verification Gate

```text
Python compileall                     PASS
Ruff correctness and bug checks      PASS
Pytest                                PASS (57 tests)
Bandit medium/high security scan     PASS
Production dependency audit          PASS
Discord modal serialization          PASS
Configuration JSON parse             PASS
```
