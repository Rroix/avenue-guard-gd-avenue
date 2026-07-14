# Test Checklist (Local + Server)

## 0) Before you test
- Set `DISCORD_TOKEN` environment variable.
- Update `config.json` with real IDs (guild/roles/channels).
- Ensure the bot's top role is above:
  - restriction role
  - gambling reward role
- Invite bot with permissions:
  - Manage Roles, Manage Channels, Manage Messages
  - Read Message History, Send Messages, Embed Links, Attach Files

---

## 1) Startup sanity checks
1. Run `python main.py`
2. Confirm bot shows "Logged in as ..." in console.

---

## 2) Guild restriction
1. If bot is in multiple servers, confirm it only responds in the configured `allowed_guild_id`.
2. Commands should only appear in that server (guild-scoped).

---

## 3) Mod: Autodeletion + restriction
**Setup:** autodelete channel ID + whitelist roles + restriction role in config.
1. Post in autodelete channel as a user *without* whitelisted roles.
   - Expected: message deleted, restriction role assigned.
2. Post in autodelete channel as a whitelisted user.
   - Expected: message stays, no role added.
3. Add a reaction as a non-whitelisted user in autodelete channel.
   - Expected: reaction removed, restriction role assigned.

---

## 4) Mod: Auto-DM on role gain
**Setup:** `roles.autoDM_watched_role_id` and `autoDM.message`.
1. Give yourself the watched role.
   - Expected: DM arrives with configured template.

---

## 5) Tracking: Message counting rules
**Setup:** excluded role + excluded channels + bot command channels.
1. Send 5 messages rapidly in a counted channel.
   - Expected: only ~1 increments per `tracking.count_cooldown_seconds`.
2. Send messages in an excluded channel.
   - Expected: no increments.
3. Give yourself the excluded tracking role; send messages.
   - Expected: no increments.

---

## 6) Commands: `/tracking top`
1. Run `/tracking top`
   - Expected: shows top list with counts.
2. Run `/tracking disable_reward` as an admin.
   - Expected: Discord immediately shows that the bot is working, then confirms the current tracking week reward is disabled without "application did not respond".
   - Expected: the weekly log records `weekly_reward_disabled`; pending claims become disabled and active weekly sessions stop.
3. Run `/tracking force_dm` for the same week after disabling reward.
   - Expected: bot still sends the manual weekly request DM unless the user already has a claim status.
   - Expected: the weekly request log embed has a clear title, event field, week, member context, and readable details.
4. Run `/tracking enable_reward` as an admin.
   - Expected: Discord immediately shows that the bot is working, then confirms the current tracking week reward is enabled again without "application did not respond".
   - Expected: the disable marker is removed; claims disabled by the command return to pending and their request sessions become active.
5. Run `/tracking force_dm` again for an eligible user.
   - Expected: bot allows the manual weekly request DM again unless the user already has a claim status.
6. Run `/tracking force_dm` for a member with an excluded tracking role.
   - Expected: bot still sends the manual weekly request DM unless that member already has a claim status.
7. Submit a weekly request DM with an invalid level ID or bad showcase URL.
   - Expected: bot DMs a validation error and does not post a staff review embed.
8. Submit a weekly request DM with a missing level ID that both validation providers agree is missing.
   - Expected: bot DMs a validation error and keeps the weekly claim/session active for correction.

---

## 7) Help: DM menu gating
1. DM the bot.
   - Expected: help dashboard embed + select menu.
   - Expected: dashboard shows active ticket status, weekly activity, live request state, recent help submissions, and cooldowns.
   - Expected: the dashboard menu does not include a Dashboard option.
2. If you are currently in weekly request DM flow (pending), DM the bot.
   - Expected: help menu should NOT interrupt.
3. Start any help flow and press `Cancel`.
   - Expected: the previous help prompt is deleted, the session is cleared, and the menu controls return.
4. Start any help flow and press `Start over`.
   - Expected: the previous help prompt is deleted, the session is cleared, and the dashboard is shown again.

---

## 8) Help: FAQ
1. DM bot → select `FAQ`
   - Expected: embed with FAQ entries.
   - Expected: the previous dashboard message is deleted and the FAQ menu does not include FAQ.
2. DM bot → select `Search FAQ`, then type `request`.
   - Expected: matching FAQ entries appear with Back/Cancel/Start Over controls.
3. DM bot outside a flow with `faq collab`.
   - Expected: matching FAQ entries appear directly and the menu does not include Search FAQ.

---

## 9) Help: Appeal punishment
1. DM bot → select `Appeal punishment`
2. Reply with your punishment details.
   - Expected: the next appeal step appears with Back/Cancel/Start Over controls.
3. Reply with why it should be lifted.
   - Expected: preview embed appears with Submit/Edit/Cancel/Start Over buttons.
4. Press Edit.
   - Expected: bot lets you rewrite the appeal reason before staff sees it.
5. Press Submit.
   - Expected: confirmation DM includes a tracked ID like `A-12`.
   - Expected: structured staff-log embed posted to `channels.appeals_log_channel_id` with the same ID and attachment links if included.
6. As staff, reply to the staff-log embed.
   - Expected: bot DMs the response to the submitter and marks the log as responded.

---

## 10) Help: Report user/message
1. DM bot → select `Report a user/message`
2. Reply with message link or user ID + reason.
   - Expected: preview embed appears before staff sees it.
3. Press Submit.
   - Expected: confirmation DM includes a tracked ID like `R-12`.
   - Expected: structured staff-log embed posted to `channels.reports_log_channel_id`.
4. Submit the exact same report again within `help.duplicate_window_hours`.
   - Expected: bot blocks it as a duplicate.

---

## 11) Help: Report bot issue
1. DM bot → select `Report a bot issue`
2. Reply with the issue details and attach a screenshot if useful.
   - Expected: preview embed appears before staff sees it and includes attachment links.
3. Press Submit.
   - Expected: confirmation DM includes a tracked ID like `B-12`.
   - Expected: structured staff-log embed posted to `channels.bot_issues_log_channel_id`.
4. Submit the exact same bot issue again within `help.duplicate_window_hours`.
   - Expected: bot blocks it as a duplicate.

---

## 12) Help: Check my weekly status
1. DM bot → select `Check my weekly status`
   - Expected: shows your weekly count and top-20 rank (or not in top 20).

---

## 13) Tickets: Mod contact + cooldown
**Setup:** ticket category + mod role + logging channel.
1. DM bot → select `Contact staff`
   - Expected: a compact FAQ suggestion embed appears with topic buttons for Moderation, Level requests, Server help, Other, and Cancel.
2. Choose `Level requests`.
   - Expected: ticket channel created under category with the topic in the channel name/opening message.
   - Expected: the opening message shows `Status: Waiting for staff`.
   - Expected: perms only requester + mods
   - Expected: ticket creation is audit-logged.
3. Run `/ticket status status:waiting_user`, then `/ticket status status:waiting_staff`.
   - Expected: the current ticket status updates and the channel receives a clean status embed.
   - Expected: the original ticket opening message edits to the new status.
4. Send a staff message, then send a requester message.
   - Expected: staff message changes the opening message to `Waiting for user`; requester message changes it to `Waiting for staff`.
5. Try creating another ticket within 24h
   - Expected: blocked with a Discord timestamp cooldown message.

---

## 14) Tickets: inactivity prompt + close
1. Temporarily set `tickets.ticket_inactivity_hours` to 0.01 for fast testing (optional)
2. Wait until prompt appears: "Do you want to close the ticket?"
3. Press No
   - Expected: ticket remains open
4. Press Yes
   - Expected: transcript file and structured ticket transcript embed posted to `channels.general_logging_channel_id`
   - Expected: the ticket opening message is marked `Resolved` before the transcript is saved.
   - Expected: channel deleted
   - Expected: ticket creator receives a 1-5 satisfaction prompt by DM if DMs are open.
5. Run `/ticket transcripts ticket_id:<id>` and `/ticket transcripts user:<creator>`.
   - Expected: staff get an ephemeral transcript search embed with jump links to saved transcript messages.

---

## 15) Help: Transcript requests (staff approval)
1. Create a ticket (so you have a ticket channel)
2. DM bot → select `Request transcript`
3. Reply with the ticket channel mention or ID.
   - Expected: structured transcript request embed posted in `channels.transcript_requests_channel_id` with Approve/Deny buttons.
4. As a mod, press Approve
   - Expected: user receives transcript file in DM
   - Expected: request message updates to approved
   - Expected: approval is audit-logged.
5. Press Deny (on another request)
   - Expected: user receives denial DM
   - Expected: the request message updates without creating duplicate transcript requests.
   - Expected: denial is audit-logged.

---

## 16) Sticky messages
**Setup:** `sticky.entries` contains the channel.
1. Send a message in the sticky channel.
   - Expected: after delay, bot posts sticky message.
2. Send another message.
   - Expected: previous sticky deleted, new sticky posted at bottom.

---

## 17) Forum first-message embeds by tag
**Setup:** forum channel ID and templates (default + tag IDs).
1. Create a new forum post with a tag that has a template configured.
   - Expected: bot sends the tag-specific embed inside the thread.
2. Create one without matching tag.
   - Expected: bot sends the default embed.
3. If `required_word` is configured for that forum, create a post without that word in the title/body.
   - Expected: bot DMs the thread owner using `missing_required_word_dm`, then deletes the thread.
4. Create a post that includes the configured `required_word`.
   - Expected: the thread remains open.
5. Run `/forum required_word word:<word> match_mode:whole_word` and create a post where the word only appears inside another word.
   - Expected: bot treats it as missing and deletes after the configured delay.

---

## 18) Commands: `/resync` and `/restart`
1. As admin/owner, edit config FAQ text or `responses.json`.
2. Run `/resync`
   - Expected: bot picks up changes without restart.
3. Run `/bot doctor`
   - Expected: bot reports channel permissions, ticket category permissions, managed role hierarchy, and request-button state.
4. Run `/bot config_check`
   - Expected: bot checks configured channels, roles, request templates, and `responses.json` rules.
5. Run `/restart`
   - Expected: bot exits; Render restarts service.

---

## 19) Fun commands
1. `/dance`
   - Expected: posts the configured GIF URL.
2. `/rock-paper-scissors`
   - Expected: buttons; only you can press; message updates with result.
3. `/gambling`
   - Expected: message edits every 0.5s; ends with final combo; rare win grants role if configured.

---

## 20) Live level request waves
**Setup:** fill `level_requests.request_channel`, `level_requests.level_requested`, `level_requests.sent_channel`, `level_requests.rejected_channel`, roles, messages, colors, and embeds in `config.json`.

1. Run `/refresh-request-button` as a mod.
   - Expected: request embed appears in `request_channel`, or the existing one is edited.
2. Press the request button while requests are closed.
   - Expected: ephemeral `Requests are closed :/`.
3. Run `/open-requests number:2 time:5` as an admin.
   - Expected: new wave opens, request embed updates to open, `/requests-are` shows open state, limit, timer, and count.
   - Expected: request_channel receives the default opening announcement pinging `<@&786245470636605440>`.
4. Press the button as a user without required roles.
   - Expected: ephemeral no-requirements message.
5. Press the button as an eligible user without `has_requested_role_id`.
   - Expected: concise ephemeral rules acknowledgement with one grey `I will` button.
6. Press `I will`.
   - Expected: user receives `has_requested_role_id` and the request modal opens.
7. Press the request button in a later wave.
   - Expected: the acknowledgement is skipped and the request modal opens directly.
8. Submit a valid modal.
   - Expected: staff embed appears in `level_requested`; user sees success; `/requests-are` count increases by 1.
   - Expected: staff embed shows how long ago it was submitted.
   - Expected: success message says the request can be edited with `/edit-request` or by pressing the request button.
9. Submit an invalid modal with a non-numeric ID or a showcase that is not a URL.
   - Expected: ephemeral validation message; no staff embed; wave count does not increase.
10. Submit a valid-looking but nonexistent level ID.
    - Expected: if GDBrowser and the GD/Boomlings check both agree it is missing, the modal is rejected and wave count does not increase.
11. Submit a known rated level ID.
    - Expected: request is allowed unless it is otherwise invalid; staff embed includes a validation warning that the level seems rated.
12. Submit a known demon or platformer without a showcase URL.
    - Expected: modal is rejected with the configured showcase-required message.
13. Submit the same demon or platformer with a valid showcase URL.
    - Expected: request is accepted; staff embed includes a compact GD Info field with difficulty, length, stars/status, detected flags, plus the validation source summary and refresh timing.
14. Submit several different IDs quickly from the same user.
    - Expected: validation rate-limit message appears once the configured limit/cooldown is reached.
15. Run `/edit-request` while the wave is still open.
    - Expected: modal opens and editing updates the original staff embed instead of creating a new request.
16. Press the request button again while the wave is still open.
    - Expected: the same prefilled edit modal opens instead of starting a second request.
17. Run `/requests history message_id:<request message id>` as a judge, head judge, mod, or admin after an edit.
    - Expected: an ephemeral audit embed shows the changed form fields with old and new values.
18. Close the wave, then run `/edit-request` or press the request button within 5 minutes.
    - Expected: the prefilled edit modal still opens and updates the original staff embed.
19. Run `/edit-request` or press the request button more than 5 minutes after requests close.
    - Expected: edit is refused.
20. Try submitting again in the same wave.
   - Expected: duplicate-user message; no new staff embed.
21. Have another user submit the same level ID in the same wave.
    - Expected: duplicate-level message; no new staff embed.
22. Submit a level ID that was used in an earlier wave but not in the current wave.
    - Expected: request is allowed and the staff embed includes a history warning.
23. Submit enough successful requests to hit the `number` limit.
    - Expected: requests close automatically and the request embed changes to closed.
    - Expected: one wave summary embed appears in `level_requested`.
24. Open requests with only `time:1`.
    - Expected: requests close automatically after the timer expires.
25. Open requests without `number` or `time`, then run `/close-requests`.
    - Expected: requests close manually and the embed changes to closed.
26. Run `/open-requests when:18:30 day:0` as an admin.
    - Expected: bot schedules the opening and replies with Discord absolute and relative timestamps.
    - Expected: command options explain that `when` is `HH:MM`, `day` is optional, and `time` is the close timer in minutes.
    - Expected: the scheduled opening uses the default announcement unless `message` is provided.
27. Run `/open-requests type:only demons` as an admin, then submit a known non-demon.
    - Expected: the modal is rejected before the request counts, and `/requests-are` shows the active type.
28. Submit a known demon during that wave.
    - Expected: the request is accepted and the staff embed shows the wave type.
29. Run `/pending-openings action:list`.
    - Expected: scheduled openings list includes ID, time, limit, close timer, and creator, plus selector/buttons for refresh, edit, delete, and open now.
30. Use the `/pending-openings` panel `Edit` button.
    - Expected: modal opens with the scheduled time, day, limit, close timer, and request type prefilled.
31. Press `Open now` while another request wave is already open.
    - Expected: bot asks for confirmation before creating a new wave.
32. Run `/pending-openings action:edit opening_id:<id> number:3 time:10 when:19:00 type:long level message:Custom open text`.
    - Expected: the scheduled opening updates and shows a custom announcement.
33. Run `/pending-openings action:delete opening_id:<id>`.
    - Expected: the scheduled opening is removed from the pending list.
34. Run `/requests pending scope:current_wave status:pending` as a judge or head judge.
    - Expected: current-wave unreviewed requests are listed with jump links and submission age.
35. On a pending staff request embed, press `Send` as a judge/head judge.
    - Expected: review modal opens; submitted review edits the staff embed, disables all buttons, and posts a pinged result in `sent_channel`.
    - Expected: the wave summary updates reviewed/sent/left-to-review counts, percentages, and reviewer stats.
36. Press `Send`, `Reject`, or `Other` as someone without a reviewer role.
    - Expected: ephemeral permission denial and no request update.
37. On another pending staff request embed, press `Reject`.
    - Expected: review modal opens; submitted review edits the staff embed, disables all buttons, and posts a pinged result in `rejected_channel`.
    - Expected: the wave summary updates reviewed/not-sent/left-to-review counts, percentages, and reviewer stats.
38. On another pending staff request embed, press `Other`.
    - Expected: ephemeral options appear for `Level doesn't exist`, `Stolen level`, and `Already rated`.
39. Choose each `Other` reason on separate requests.
    - Expected: staff embed color/result updates, buttons disable, and a pinged result appears in `rejected_channel`.
    - Expected: the wave summary updates the not-sent breakdown.

---

## 21) Weekly request embed flow
**Setup:** `channels.weekly_request_channel_ID` and the `level_requests.weekly_request_*_embed` templates.

1. Run `/tracking force_dm` for a test user.
   - Expected: user receives the configurable weekly request DM embed.
   - Expected: the weekly log records a `force_dm_sent` event, or the matching blocked/failed force-DM event if the DM cannot be sent.
2. Wait until reminder timing or temporarily lower `tracking.reminder_after_hours`.
   - Expected: user receives the configurable weekly reminder embed.
3. Reply in DM with a valid request containing name, creator, and ID.
   - Expected: `weekly_request_channel_ID` receives the configurable weekly submitted embed with `Send`, `Reject`, and `Other` buttons.
   - Expected: the weekly submitted embed shows how long ago it was submitted.
4. Press `Send` or `Reject`.
   - Expected: the optional review modal appears, the weekly submitted embed changes to the reviewed template, buttons become disabled, and the requester is pinged in the configured sent/rejected result channel.
   - Expected: no live request wave count or wave summary changes.
5. Press `Other` on a separate weekly submitted request.
   - Expected: the three reason buttons appear, the original weekly embed is finalized, buttons become disabled, and the requester is pinged in `rejected_channel`.
   - Expected: no live request wave count or wave summary changes.

---

## 22) Daily server summary
**Setup:** `background.daily_summary.enabled` is true and `background.daily_summary.channel_id` points to a staff-visible channel.

1. Let the daily summary run, or temporarily set `background.daily_summary.time` to a near-future configured server time and restart.
   - Expected: summary embed includes activity, community, voice/presence, commands, highlights, top channels, top members, and top commands.
2. Compare with the previous day if data exists.
   - Expected: message and command lines show day-over-day change.

---

## 23) Server icon rotation
**Setup:** use an admin/owner account and make sure the bot has Manage Server.

1. Run `/server_icon status`.
   - Expected: status shows mode, interval, current image, last change, next automatic change, configured URLs, and the last error if one exists.
2. Run `/server_icon mode mode:linear`.
   - Expected: mode changes to `linear` and the background rotation task is enabled after config reload.
3. Run `/server_icon next`.
   - Expected: the server icon changes to the next configured image and `current_index`, `current_url`, and `last_changed_ts` are saved.
4. Run `/server_icon set number:2`.
   - Expected: the server icon changes specifically to configured image #2.
5. Run `/server_icon mode mode:random`, then `/server_icon next`.
   - Expected: the server icon changes to a configured image without repeating the current image when more than one URL exists.
6. Run `/server_icon add url:<direct image URL>`, then `/server_icon replace number:<n> url:<direct image URL>`, then `/server_icon remove number:<n>`.
   - Expected: each command updates `config.json` and `/server_icon status` reflects the new list.
7. Temporarily set one configured URL to a broken image URL, then run `/server_icon next`.
   - Expected: the bot tries the next usable configured URL and records the failed URL in the last-error field if all candidates fail.
8. Run `/server_icon mode mode:disabled`.
   - Expected: automatic changes stop, but `/server_icon next` can still force a manual change.

---

## 24) Bot diagnostics and performance safety
**Setup:** use an admin/owner account for `/bot` commands and a mod, judge, or head judge account for `/requests pending`.

1. Run `/bot dashboard`.
   - Expected: an ephemeral dashboard embed shows database status, storage status, latency, loaded cogs, request state, tracking state, ticket count, server icon rotation, DB backup task, and background tasks.
   - Expected: Config, Repair Tips, and Refresh buttons update the same dashboard message.
2. Run `/bot health`.
   - Expected: an ephemeral health embed still works for backwards compatibility and shows storage/backup task status.
3. Run `/bot config_check`.
   - Expected: configured channels and roles are reported as OK or listed as issues.
   - Expected: request embed template variables, field shapes, and suspicious color values are reported as OK or listed as issues.
   - Expected: server icon rotation mode, interval, and configured URLs are reported as OK or listed as issues.
   - Expected: database path and backup channel are reported as OK or listed as issues.
4. Temporarily add an invalid request template variable such as `{bad_variable}` to a request embed template, run `/bot config_check`, then revert it.
   - Expected: config check reports the unknown template variable.
5. Run `/bot storage` as the configured impact owner.
   - Expected: an ephemeral embed shows the running database path, whether it looks persistent, backup interval, backup channel, and latest backup record.
6. Run `/bot backup` as the configured impact owner.
   - Expected: the configured backup/log channel receives a database backup embed with a zipped SQLite attachment.
   - Expected: `database_backups` stores the backup channel, message ID, size, reason, and filename.
7. Run `/bot impact` as the configured impact owner.
   - Expected: the configured impact/log channel receives an impact report embed with Markdown, summary CSV, daily trend CSV, breakdown CSV, and JSON attachments.
   - Expected: `impact_snapshots` stores a database copy of the same report payload.
   - Expected: the summary CSV opens cleanly in a spreadsheet with `section`, `metric`, and `value` columns.
   - Expected: the daily trend CSV includes daily rows useful for charts and forecasting.
8. Run `/requests pending scope:all status:pending`.
   - Expected: pending live request reviews and weekly request reviews are listed separately with jump links when message IDs are available.
9. Run `/requests repair` as an admin.
   - Expected: recovery embed reports the request button refresh, wave summary refresh, recreated/refreshed pending messages, stale validations refreshed, and both live and weekly reviewed messages relocked.
10. Send several normal chat messages.
   - Expected: tracking still counts activity, but writes are flushed according to `tracking.activity_flush_seconds`.
11. Run `/restart` after sending a counted message.
   - Expected: buffered tracking counts and current daily stats are flushed before the bot exits.
12. Create two tickets quickly with two users.
   - Expected: ticket IDs do not duplicate.
13. Temporarily misconfigure `channels.weekly_request_channel_ID`, restart or resync, then submit a weekly request in DM.
   - Expected: the user is told the request could not be recorded, the weekly log records `request_record_failed`, and the claim is not silently closed as successfully claimed.
14. Reply to a weekly request DM with text missing the actual level ID field.
   - Expected: the request is not recorded, and the bot tells the user which required field is missing.
15. Disable this week's reward with `/tracking disable_reward`, then run `/tracking force_dm`.
   - Expected: the manual force DM still sends if the user has no active/past non-resettable claim, and the override is logged.
14. Temporarily misconfigure an appeal/report/bot-issue log channel, then complete that DM flow.
   - Expected: the user is told the submission could not be sent instead of receiving a false success message.
