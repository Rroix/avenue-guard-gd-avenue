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
   - Expected: bot confirms the current tracking week reward is disabled.
3. Run `/tracking force_dm` for the same week after disabling reward.
   - Expected: bot refuses because weekly reward DMs are disabled for that tracking week.
   - Expected: the weekly request log embed has a clear title, event field, week, member context, and readable details.
4. Run `/tracking enable_reward` as an admin.
   - Expected: bot confirms the current tracking week reward is enabled again.
5. Run `/tracking force_dm` again for an eligible user.
   - Expected: bot allows the manual weekly request DM again unless the user already has a claim status.
6. Run `/tracking force_dm` for a member with an excluded tracking role.
   - Expected: bot still sends the manual weekly request DM unless that member already has a claim status.

---

## 7) Help: DM menu gating
1. DM the bot.
   - Expected: help menu embed + select menu.
2. If you are currently in weekly request DM flow (pending), DM the bot.
   - Expected: help menu should NOT interrupt.

---

## 8) Help: FAQ
1. DM bot → select `FAQ`
   - Expected: embed with FAQ entries.

---

## 9) Help: Appeal punishment
1. DM bot → select `Appeal punishment`
2. Reply with your punishment details.
3. Reply with why it should be lifted.
   - Expected: confirmation DM.
   - Expected: structured staff-log embed posted to `channels.appeals_log_channel_id`.

---

## 10) Help: Report user/message
1. DM bot → select `Report a user/message`
2. Reply with message link or user ID + reason.
   - Expected: confirmation DM.
   - Expected: structured staff-log embed posted to `channels.reports_log_channel_id`.

---

## 11) Help: Report bot issue
1. DM bot → select `Report a bot issue`
2. Reply with the issue details.
   - Expected: confirmation DM.
   - Expected: structured staff-log embed posted to `channels.bot_issues_log_channel_id`.

---

## 12) Help: Check my weekly status
1. DM bot → select `Check my weekly status`
   - Expected: shows your weekly count and top-20 rank (or not in top 20).

---

## 13) Tickets: Mod contact + cooldown
**Setup:** ticket category + mod role + logging channel.
1. DM bot → select `Mod contact` → confirm Yes
   - Expected: ticket channel created under category
   - Expected: perms only requester + mods
2. Try creating another ticket within 24h
   - Expected: blocked with cooldown message

---

## 14) Tickets: inactivity prompt + close
1. Temporarily set `tickets.ticket_inactivity_hours` to 0.01 for fast testing (optional)
2. Wait until prompt appears: "Do you want to close the ticket?"
3. Press No
   - Expected: ticket remains open
4. Press Yes
   - Expected: transcript file and structured ticket transcript embed posted to `channels.general_logging_channel_id`
   - Expected: channel deleted

---

## 15) Help: Transcript requests (staff approval)
1. Create a ticket (so you have a ticket channel)
2. DM bot → select `Request transcript`
3. Reply with the ticket channel mention or ID.
   - Expected: structured transcript request embed posted in `channels.transcript_requests_channel_id` with Approve/Deny buttons.
4. As a mod, press Approve
   - Expected: user receives transcript file in DM
   - Expected: request message updates to approved
5. Press Deny (on another request)
   - Expected: user receives denial DM
   - Expected: the request message updates without creating duplicate transcript requests.

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

---

## 18) Commands: `/resync` and `/restart`
1. As admin/owner, edit config FAQ text or `responses.json`.
2. Run `/resync`
   - Expected: bot picks up changes without restart.
3. Run `/restart`
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
4. Press the button as a user without required roles.
   - Expected: ephemeral no-requirements message.
5. Press the button as an eligible user without `has_requested_role_id`.
   - Expected: ephemeral first-time prompt with `I will` and `I won't`.
6. Press `I will`.
   - Expected: user receives `request_banned_role_id`; future button presses are blocked by requirements.
7. Remove the banned role, press again, then press `I won't`.
   - Expected: user receives `has_requested_role_id` and the request modal opens.
8. Submit a valid modal.
   - Expected: staff embed appears in `level_requested`; user sees success; `/requests-are` count increases by 1.
   - Expected: staff embed shows how long ago it was submitted.
   - Expected: success message says the request can be edited with `/edit-request` or by pressing the request button.
9. Submit an invalid modal with a non-numeric ID or a showcase that is not a URL.
   - Expected: ephemeral validation message; no staff embed; wave count does not increase.
10. Run `/edit-request` while the wave is still open.
    - Expected: modal opens and editing updates the original staff embed instead of creating a new request.
11. Press the request button again while the wave is still open.
    - Expected: the same prefilled edit modal opens instead of starting a second request.
12. Close the wave, then run `/edit-request` or press the request button within 5 minutes.
    - Expected: the prefilled edit modal still opens and updates the original staff embed.
13. Run `/edit-request` or press the request button more than 5 minutes after requests close.
    - Expected: edit is refused.
14. Try submitting again in the same wave.
   - Expected: duplicate-user message; no new staff embed.
15. Have another user submit the same level ID in the same wave.
    - Expected: duplicate-level message; no new staff embed.
16. Submit a level ID that was used in an earlier wave but not in the current wave.
    - Expected: request is allowed and the staff embed includes a history warning.
17. Submit enough successful requests to hit the `number` limit.
    - Expected: requests close automatically and the request embed changes to closed.
    - Expected: one wave summary embed appears in `level_requested`.
18. Open requests with only `time:1`.
    - Expected: requests close automatically after the timer expires.
19. Open requests without `number` or `time`, then run `/close-requests`.
    - Expected: requests close manually and the embed changes to closed.
20. Run `/open-requests when:18:30 day:0` as an admin.
    - Expected: bot schedules the opening and replies with Discord absolute and relative timestamps.
21. Run `/pending-openings action:list`.
    - Expected: scheduled openings list includes ID, time, limit, close timer, and creator.
22. Run `/pending-openings action:edit opening_id:<id> number:3 time:10 when:19:00`.
    - Expected: the scheduled opening updates.
23. Run `/pending-openings action:delete opening_id:<id>`.
    - Expected: the scheduled opening is removed from the pending list.
24. Run `/requests pending scope:current_wave status:pending` as a judge or head judge.
    - Expected: current-wave unreviewed requests are listed with jump links and submission age.
25. On a pending staff request embed, press `Send` as a judge/head judge.
    - Expected: review modal opens; submitted review edits the staff embed, disables all buttons, and posts a pinged result in `sent_channel`.
    - Expected: the wave summary updates reviewed/sent/left-to-review counts, percentages, and reviewer stats.
26. Press `Send`, `Reject`, or `Other` as someone without a reviewer role.
    - Expected: ephemeral permission denial and no request update.
27. On another pending staff request embed, press `Reject`.
    - Expected: review modal opens; submitted review edits the staff embed, disables all buttons, and posts a pinged result in `rejected_channel`.
    - Expected: the wave summary updates reviewed/not-sent/left-to-review counts, percentages, and reviewer stats.
28. On another pending staff request embed, press `Other`.
    - Expected: ephemeral options appear for `Level doesn't exist`, `Stolen level`, and `Already rated`.
29. Choose each `Other` reason on separate requests.
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

1. Let the daily summary run, or temporarily set `background.daily_summary.time` to a near-future Madrid time and restart.
   - Expected: summary embed includes activity, community, voice/presence, commands, highlights, top channels, top members, and top commands.
2. Compare with the previous day if data exists.
   - Expected: message and command lines show day-over-day change.

---

## 23) Bot diagnostics and performance safety
**Setup:** use an admin/owner account for `/bot` commands and a mod, judge, or head judge account for `/requests pending`.

1. Run `/bot health`.
   - Expected: an ephemeral health embed shows database status, latency, loaded cogs, background task states, open tickets, weekly sessions, pending requests, and request state.
2. Run `/bot config_check`.
   - Expected: configured channels and roles are reported as OK or listed as issues.
3. Run `/requests pending scope:all status:pending`.
   - Expected: pending live request reviews and weekly request reviews are listed separately with jump links when message IDs are available.
4. Send several normal chat messages.
   - Expected: tracking still counts activity, but writes are flushed according to `tracking.activity_flush_seconds`.
5. Run `/restart` after sending a counted message.
   - Expected: buffered tracking counts and current daily stats are flushed before the bot exits.
6. Create two tickets quickly with two users.
   - Expected: ticket IDs do not duplicate.
7. Temporarily misconfigure `channels.weekly_request_channel_ID`, restart or resync, then submit a weekly request in DM.
   - Expected: the user is told the request could not be recorded, the weekly log records `request_record_failed`, and the claim is not silently closed as successfully claimed.
8. Reply to a weekly request DM with text missing the actual level ID field.
   - Expected: the request is not recorded, and the bot tells the user which required field is missing.
9. Disable this week's reward with `/tracking disable_reward`, then run `/tracking force_dm`.
   - Expected: the manual force DM still sends if the user has no active/past non-resettable claim, and the override is logged.
10. Temporarily misconfigure an appeal/report/bot-issue log channel, then complete that DM flow.
   - Expected: the user is told the submission could not be sent instead of receiving a false success message.
