from __future__ import annotations

import asyncio
import json
import hashlib
import time
from dataclasses import asdict
from typing import Any

from utils.bayesian import (
    bayesian_settings,
    beta_quantile,
    beta_mean,
    beta_posterior,
    calibration_metrics,
    capacity_forecast,
    capacity_forecast_groups,
    credible_intervals,
    evidence_strength,
    json_compact,
    model_status,
    monte_carlo_product,
    ReadinessPolicy,
)
from utils.workflows import new_correlation_id


MODEL_KEYS = ("access_model_v1", "rating_model_v1")
PUBLIC_MODEL_STATUSES = {"active"}


def observed_rating_tier(row: Any) -> str | None:
    for key, tier in (
        ("current_mythic", "mythic"),
        ("current_legendary", "legendary"),
        ("current_epic", "epic"),
        ("current_featured", "feature"),
        ("current_rated", "rate"),
    ):
        try:
            if row[key] in (1, "1", True):
                return tier
        except (KeyError, TypeError):
            continue
    return None


class BayesianModelService:
    """Private evidence projection and shadow-model computation.

    The service observes durable PPS facts. It never chooses queue order, edits a
    review decision, or treats a planned outreach action as evidence.
    """

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    @property
    def settings(self):
        return bayesian_settings(self.bot.config.data)

    async def ensure_active_era(self, guild_id: int, actor_id: int = 0):
        row = await self.db.fetchone(
            "SELECT * FROM level_network_eras WHERE guild_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (guild_id,),
        )
        if row:
            return row
        now = int(time.time())
        correlation = new_correlation_id("network-era")
        await self.db.execute(
            "INSERT OR IGNORE INTO level_network_eras(guild_id,name,status,started_ts,started_by,prior_mode,carryover_effective_n,prior_alpha,prior_beta,correlation_id) "
            "VALUES(?,?,'active',?,?,'discounted_previous',?,?,?,?)",
            (
                guild_id, f"Network era {now}", now, actor_id,
                self.settings.carryover_effective_n, self.settings.prior_alpha,
                self.settings.prior_beta, correlation,
            ),
        )
        return await self.db.fetchone(
            "SELECT * FROM level_network_eras WHERE guild_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (guild_id,),
        )

    async def start_new_era(
        self,
        guild_id: int,
        actor_id: int,
        name: str,
        private_reason: str,
        public_reason: str = "",
    ):
        if not str(private_reason or "").strip():
            raise ValueError("A private network-era reason is required")
        now = int(time.time())
        current = await self.ensure_active_era(guild_id, actor_id)
        prior_alpha, prior_beta = await self._prior(guild_id, int(current["id"]) + 1, "access_model_v1")
        correlation = new_correlation_id("network-era")
        statements: list[tuple[str, tuple[Any, ...]]] = []
        if current:
            statements.append(
                (
                    "UPDATE level_network_eras SET status='completed',ended_ts=?,ended_by=? WHERE id=? AND status='active'",
                    (now, actor_id, int(current["id"])),
                )
            )
        statements.extend(
            [
                (
                    "INSERT INTO level_network_eras(guild_id,name,status,started_ts,started_by,prior_mode,carryover_effective_n,prior_alpha,prior_beta,public_reason,private_reason,notes,correlation_id) "
                    "VALUES(?,?,'active',?,?,'discounted_previous',?,?,?,?,?,?,?)",
                    (
                        guild_id, str(name or f"Network era {now}")[:120], now, actor_id,
                        self.settings.carryover_effective_n, prior_alpha, prior_beta,
                        str(public_reason or "")[:500], str(private_reason or "")[:2000],
                        str(private_reason or "")[:2000], correlation,
                    ),
                ),
                (
                    "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) "
                    "VALUES(?,'bayesian_models','network-era','network_era_started',?,?,?,?)",
                    (
                        correlation, guild_id, actor_id,
                        json_compact({"name": str(name or "")[:120], "prior_alpha": prior_alpha, "prior_beta": prior_beta}),
                        now,
                    ),
                ),
            ]
        )
        await self.db.execute_transaction(statements, retry_safe=True)
        await self._notify_network_era(guild_id, str(name or f"Network era {now}")[:120], correlation, now)
        return await self.ensure_active_era(guild_id, actor_id)

    async def _notify_network_era(self, guild_id: int, name: str, correlation: str, now: int) -> None:
        user_ids = set(self.bot.config.get_int_list("staff_portal", "owner_user_ids")) | set(
            self.bot.config.get_int_list("staff_portal", "dev_user_ids")
        )
        for user_id in user_ids:
            await self.db.execute(
                "INSERT OR IGNORE INTO discord_outbox(correlation_id,idempotency_key,action_type,guild_id,channel_id,user_id,payload_json,status,attempts,next_attempt_ts,created_ts,updated_ts) VALUES(?,?,'send_dm',?,0,?,?,'pending',0,?,?,?)",
                (
                    correlation, f"network-era:{guild_id}:{correlation}:{int(user_id)}", guild_id,
                    int(user_id), json_compact({"content": f"**{name}** has started. Public access estimates are collecting evidence for the new network era."}),
                    now, now, now,
                ),
            )

    async def project_attempt(self, attempt_id: int) -> int | None:
        attempt = await self.db.fetchone(
            "SELECT a.*,q.guild_id,q.level_id,q.current_rated,q.send_type,q.current_mythic,q.current_legendary,q.current_epic,q.current_featured "
            "FROM level_outreach_attempts a JOIN level_outreach_queue q ON q.id=a.queue_id WHERE a.id=?",
            (attempt_id,),
        )
        if not attempt or not attempt["episode_id"]:
            return None
        era = await self.ensure_active_era(int(attempt["guild_id"]), int(attempt["actor_id"] or 0))
        if not era:
            return None
        now = int(time.time())
        event_ts = int(attempt["event_ts"] or attempt["created_ts"] or now)
        target_key = str(attempt["private_target_key"] or "").strip()
        exclusion = None
        if not target_key:
            target_key = f"missing:{attempt_id}"
            exclusion = "target_identity_missing"
        status = str(attempt["status"])
        outcome = "success" if status in {"submitted_to_mod", "follow_up"} else "failure" if status == "failed" else "pending"
        await self.db.execute(
            "INSERT INTO level_outreach_targets(guild_id,private_target_key,private_label,created_ts,last_seen_ts) "
            "VALUES(?,?,?,?,?) ON CONFLICT(guild_id,private_target_key) DO UPDATE SET "
            "private_label=CASE WHEN excluded.private_label!='' THEN excluded.private_label ELSE private_label END,last_seen_ts=excluded.last_seen_ts",
            (
                int(attempt["guild_id"]), target_key, str(attempt["private_target_label"] or "")[:300],
                now, now,
            ),
        )
        target = await self.db.fetchone(
            "SELECT id FROM level_outreach_targets WHERE guild_id=? AND private_target_key=?",
            (int(attempt["guild_id"]), target_key),
        )
        target_id = int(target["id"]) if target else None
        existing = await self.db.fetchone(
            "SELECT * FROM level_outreach_opportunities WHERE episode_id=? AND queue_id=? AND private_target_key=?",
            (int(attempt["episode_id"]), int(attempt["queue_id"]), target_key),
        )
        if existing:
            opportunity_id = int(existing["id"])
            await self.db.execute(
                "UPDATE level_outreach_opportunities SET target_id=COALESCE(target_id,?),"
                "first_planned_ts=CASE WHEN ?='planned' THEN COALESCE(first_planned_ts,?) ELSE first_planned_ts END,"
                "first_attempt_ts=CASE WHEN ?!='planned' THEN COALESCE(first_attempt_ts,?) ELSE first_attempt_ts END,"
                "last_attempt_ts=CASE WHEN ?!='planned' THEN MAX(COALESCE(last_attempt_ts,0),?) ELSE last_attempt_ts END,"
                "current_route=?,route_type=?,"
                "confirmed_submission_ts=CASE WHEN ?='success' THEN COALESCE(confirmed_submission_ts,?) ELSE confirmed_submission_ts END,"
                "follow_up_count=follow_up_count+?,outcome=CASE WHEN outcome='success' THEN outcome WHEN ?='success' THEN 'success' WHEN ?='failure' THEN 'failure' ELSE outcome END,"
                "outcome_ts=CASE WHEN ? IN('success','failure') THEN COALESCE(outcome_ts,?) ELSE outcome_ts END,"
                "closed_ts=CASE WHEN ?='failure' THEN COALESCE(closed_ts,?) ELSE closed_ts END,"
                "failure_reason=CASE WHEN ?='failure' THEN ? ELSE failure_reason END,updated_ts=? WHERE id=?",
                (
                    target_id, status, event_ts, status, event_ts, status, event_ts,
                    str(attempt["route_type"]), str(attempt["route_type"]), outcome, event_ts,
                    int(status == "follow_up"), outcome, outcome, outcome, event_ts,
                    outcome, event_ts, outcome, str(attempt["private_notes"] or "")[:1000], now,
                    opportunity_id,
                ),
            )
        else:
            opportunity_id = await self.db.execute_insert(
                "INSERT INTO level_outreach_opportunities(guild_id,network_era_id,cycle_id,episode_id,queue_id,level_id,target_id,private_target_key,private_target_label,initial_route,current_route,route_type,first_planned_ts,first_attempt_ts,last_attempt_ts,confirmed_submission_ts,closed_ts,follow_up_count,outcome,outcome_ts,failure_reason,actor_id,dependency_group,exclusion_reason,source_attempt_id,updated_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    int(attempt["guild_id"]), int(era["id"]), int(attempt["cycle_id"]), int(attempt["episode_id"]), int(attempt["queue_id"]),
                    str(attempt["level_id"]), target_id, target_key, str(attempt["private_target_label"] or "")[:300],
                    str(attempt["route_type"]), str(attempt["route_type"]), str(attempt["route_type"]),
                    event_ts if status == "planned" else None,
                    event_ts if status != "planned" else None,
                    event_ts if status != "planned" else None,
                    event_ts if outcome == "success" else None,
                    event_ts if outcome == "failure" else None,
                    int(status == "follow_up"), outcome, event_ts if outcome in {"success", "failure"} else None,
                    str(attempt["private_notes"] or "")[:1000] if outcome == "failure" else None,
                    int(attempt["actor_id"] or 0), f"target:{target_id}" if target_id else None,
                    exclusion, attempt_id, now,
                ),
            )
        await self.db.execute(
            "UPDATE level_outreach_attempts SET opportunity_id=? WHERE id=? AND opportunity_id IS NULL",
            (opportunity_id, attempt_id),
        )
        if exclusion:
            await self._ensure_exclusion(
                int(attempt["guild_id"]), "opportunity", str(opportunity_id), "access_model_v1", exclusion, 0
            )
        if status in {"attempted", "submitted_to_mod"} and not exclusion:
            await self._create_prediction(
                int(attempt["guild_id"]),
                int(era["id"]),
                "access_model_v1",
                "opportunity",
                str(opportunity_id),
                f"route:{str(attempt['route_type'])}",
                event_ts,
            )
        if status == "submitted_to_mod":
            tier = observed_rating_tier(attempt)
            preexisting = int(attempt["current_rated"] in (1, "1", True))
            due = event_ts + 30 * 86400
            await self.db.execute(
                "UPDATE staff_outreach_episodes SET network_era_id=COALESCE(network_era_id,?),recommendation_type=COALESCE(recommendation_type,?),"
                "first_confirmed_submission_ts=COALESCE(first_confirmed_submission_ts,?),outcome_window_days=30,"
                "outcome_window_end_ts=COALESCE(outcome_window_end_ts,?),rated_before_first_submission=MAX(rated_before_first_submission,?),"
                "exclusion_reason=CASE WHEN ?=1 THEN 'rated_before_outreach' ELSE exclusion_reason END,observed_rating_tier=COALESCE(observed_rating_tier,?) WHERE id=?",
                (int(era["id"]), str(attempt["send_type"]), event_ts, due, preexisting, preexisting, tier, int(attempt["episode_id"])),
            )
            if preexisting:
                await self._ensure_exclusion(
                    int(attempt["guild_id"]), "episode", str(attempt["episode_id"]), "rating_model_v1", "rated_before_outreach", 0
                )
            else:
                await self._create_prediction(
                    int(attempt["guild_id"]),
                    int(era["id"]),
                    "rating_model_v1",
                    "episode",
                    str(attempt["episode_id"]),
                    f"tier:{str(attempt['send_type'])}",
                    event_ts,
                )
        await self.resolve_predictions(int(attempt["guild_id"]))
        return opportunity_id

    async def _create_prediction(
        self,
        guild_id: int,
        era_id: int,
        model_key: str,
        entity_type: str,
        entity_id: str,
        preferred_subgroup: str,
        predicted_ts: int,
    ) -> None:
        existing = await self.db.fetchone(
            "SELECT 1 FROM bayes_predictions WHERE guild_id=? AND model_key=? AND entity_type=? AND entity_id=? LIMIT 1",
            (guild_id, model_key, entity_type, entity_id),
        )
        if existing:
            return
        snapshot = await self.db.fetchone(
            "SELECT * FROM bayes_model_snapshots WHERE guild_id=? AND network_era_id=? AND model_key=? "
            "AND subgroup_key IN(?, 'global') ORDER BY CASE WHEN subgroup_key=? THEN 0 ELSE 1 END,generated_ts DESC LIMIT 1",
            (guild_id, era_id, model_key, preferred_subgroup, preferred_subgroup),
        )
        if not snapshot:
            return
        await self.db.execute(
            "INSERT OR IGNORE INTO bayes_predictions(guild_id,network_era_id,model_key,model_version,entity_type,entity_id,subgroup_key,probability,median,posterior_alpha,posterior_beta,intervals_json,data_cutoff_ts,status,evidence_strength,predicted_ts,metadata_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                guild_id, era_id, model_key, str(snapshot["model_version"]), entity_type, entity_id,
                str(snapshot["subgroup_key"]), float(snapshot["posterior_mean"]),
                beta_quantile(0.5, float(snapshot["alpha"]), float(snapshot["beta"])),
                float(snapshot["alpha"]), float(snapshot["beta"]), str(snapshot["intervals_json"]),
                int(snapshot["data_cutoff_ts"] or snapshot["generated_ts"]),
                str(snapshot["status"]), str(snapshot["evidence_strength"]), int(predicted_ts),
                json_compact({"preferred_subgroup": preferred_subgroup}),
            ),
        )

    async def _ensure_exclusion(self, guild_id: int, entity_type: str, entity_id: str, model_key: str, reason: str, actor_id: int) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO bayes_model_exclusions(guild_id,entity_type,entity_id,model_key,reason_code,created_by,created_ts) VALUES(?,?,?,?,?,?,?)",
            (guild_id, entity_type, entity_id, model_key, reason, actor_id, int(time.time())),
        )

    async def sync_episode_outcomes(self, guild_id: int) -> int:
        now = int(time.time())
        rows = await self.db.fetchall(
            "SELECT e.*,q.rated_observed_ts,q.current_rated,q.current_featured,q.current_epic,q.current_legendary,q.current_mythic "
            "FROM staff_outreach_episodes e JOIN level_outreach_queue q ON q.id=e.queue_id "
            "WHERE e.guild_id=? AND e.first_confirmed_submission_ts IS NOT NULL",
            (guild_id,),
        )
        changed = 0
        for row in rows:
            rated_ts = int(row["rated_observed_ts"] or 0) or None
            due = int(row["outcome_window_end_ts"] or (int(row["first_confirmed_submission_ts"]) + 30 * 86400))
            tier = observed_rating_tier(row)
            eventual = int(bool(rated_ts or row["current_rated"] in (1, "1", True)))
            within = 1 if rated_ts and rated_ts <= due else 0 if now >= due else None
            complete_ts = due if within == 0 else rated_ts if within == 1 else None
            affected = await self.db.execute_affected(
                "UPDATE staff_outreach_episodes SET outcome_window_end_ts=?,rated_within_window=?,outcome_completed_ts=?,"
                "eventual_rated=?,eventual_rated_ts=?,observed_rating_tier=COALESCE(?,observed_rating_tier),"
                "outcome=CASE WHEN ?=1 THEN 'rated_within_window' WHEN ?=0 THEN 'not_rated_within_window' ELSE outcome END "
                "WHERE id=? AND (COALESCE(rated_within_window,-1)!=COALESCE(?,-1) OR COALESCE(eventual_rated,-1)!=? OR COALESCE(observed_rating_tier,'')!=COALESCE(?,''))",
                (due, within, complete_ts, eventual, rated_ts, tier, within, within, int(row["id"]), within, eventual, tier),
            )
            changed += int(affected or 0)
        return changed

    async def _prior(self, guild_id: int, era_id: int, model_key: str) -> tuple[float, float]:
        settings = self.settings
        previous = await self.db.fetchone(
            "SELECT posterior_mean FROM bayes_model_snapshots WHERE guild_id=? AND network_era_id<? AND model_key=? "
            "AND subgroup_key='global' ORDER BY generated_ts DESC LIMIT 1",
            (guild_id, era_id, model_key),
        )
        if not previous or settings.carryover_effective_n <= 0:
            return settings.prior_alpha, settings.prior_beta
        mean = min(0.999, max(0.001, float(previous["posterior_mean"])))
        return (
            settings.prior_alpha + mean * settings.carryover_effective_n,
            settings.prior_beta + (1.0 - mean) * settings.carryover_effective_n,
        )

    async def _resolved_predictions(self, guild_id: int, model_key: str) -> dict[str, Any]:
        rows = await self.db.fetchall(
            "SELECT probability,resolved_outcome FROM bayes_predictions WHERE guild_id=? AND model_key=? AND resolved_outcome IS NOT NULL",
            (guild_id, model_key),
        )
        return calibration_metrics((float(row["probability"]), int(row["resolved_outcome"])) for row in rows)

    async def _paused(self, guild_id: int, model_key: str) -> bool:
        row = await self.db.fetchone(
            "SELECT publication_paused FROM bayes_model_controls WHERE guild_id=? AND model_key=?",
            (guild_id, model_key),
        )
        return bool(row and int(row["publication_paused"] or 0))

    async def _write_snapshot(
        self,
        *,
        guild_id: int,
        era_id: int,
        model_key: str,
        version: str,
        subgroup: str,
        successes: int,
        failures: int,
        prior: tuple[float, float],
        freshness_ts: int | None,
        policy,
        calibration: dict[str, Any],
        now: int,
    ) -> dict[str, Any]:
        alpha, beta = beta_posterior(successes, failures, *prior)
        intervals = await asyncio.to_thread(credible_intervals, alpha, beta)
        width = intervals["90"][1] - intervals["90"][0]
        degraded = (
            calibration.get("count", 0) >= self.settings.calibration_min_predictions
            and (
                float(calibration.get("ece") or 0) > self.settings.calibration_max_ece
                or float(calibration.get("brier") or 0) > self.settings.calibration_max_brier
            )
        )
        calibration = {**calibration, "degraded": degraded}
        status, reason = model_status(
            observations=successes + failures,
            successes=successes,
            failures=failures,
            interval_90_width=width,
            freshness_ts=freshness_ts,
            now_ts=now,
            policy=policy,
            calibration=calibration,
            paused=await self._paused(guild_id, model_key),
        )
        strength = evidence_strength(successes + failures, width)
        last = await self.db.fetchone(
            "SELECT MAX(generated_ts) AS generated_ts FROM bayes_model_snapshots WHERE guild_id=? AND network_era_id=? AND model_key=? AND model_version=? AND subgroup_key=?",
            (guild_id, era_id, model_key, version, subgroup),
        )
        generated_ts = max(now, int(last["generated_ts"] or 0) + 1) if last else now
        config_hash = hashlib.sha256(
            repr((self.settings, model_key, version, subgroup, prior)).encode("utf-8")
        ).hexdigest()
        snapshot_id = await self.db.execute_insert(
            "INSERT INTO bayes_model_snapshots(guild_id,network_era_id,model_key,model_version,subgroup_key,status,alpha,beta,observations,successes,failures,posterior_mean,intervals_json,evidence_strength,freshness_ts,calibration_json,reason,prior_alpha,prior_beta,data_cutoff_ts,config_hash,generated_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                guild_id, era_id, model_key, version, subgroup, status, alpha, beta,
                successes + failures, successes, failures, beta_mean(alpha, beta), json_compact(intervals),
                strength, freshness_ts, json_compact(calibration), reason, prior[0], prior[1],
                freshness_ts or now, config_hash, generated_ts,
            ),
        )
        return {
            "snapshot_id": snapshot_id,
            "model_key": model_key, "model_version": version, "subgroup": subgroup,
            "status": status, "alpha": alpha, "beta": beta, "observations": successes + failures,
            "successes": successes, "failures": failures, "mean": beta_mean(alpha, beta),
            "intervals": intervals, "evidence_strength": strength, "freshness_ts": freshness_ts,
            "calibration": calibration, "reason": reason, "generated_ts": generated_ts,
        }

    async def recompute(self, guild_id: int) -> dict[str, Any]:
        settings = self.settings
        era = await self.ensure_active_era(guild_id)
        if not settings.enabled or not era:
            return {"enabled": False, "models": []}
        era_id = int(era["id"])
        now = int(time.time())
        await self._record_versions(now)
        access_rows = await self.db.fetchall(
            "SELECT outcome,route_type,last_attempt_ts FROM level_outreach_opportunities o "
            "WHERE guild_id=? AND network_era_id=? AND outcome IN('success','failure') AND exclusion_reason IS NULL "
            "AND NOT EXISTS(SELECT 1 FROM bayes_model_exclusions x WHERE x.guild_id=o.guild_id AND x.entity_type='opportunity' AND x.entity_id=CAST(o.id AS TEXT) AND x.active=1 AND x.model_key IN('all','access_model_v1'))",
            (guild_id, era_id),
        )
        rating_rows = await self.db.fetchall(
            "SELECT rated_within_window,recommendation_type,outcome_completed_ts FROM staff_outreach_episodes e "
            "WHERE guild_id=? AND network_era_id=? AND rated_within_window IS NOT NULL AND rated_before_first_submission=0 AND exclusion_reason IS NULL "
            "AND NOT EXISTS(SELECT 1 FROM bayes_model_exclusions x WHERE x.guild_id=e.guild_id AND x.entity_type='episode' AND x.entity_id=CAST(e.id AS TEXT) AND x.active=1 AND x.model_key IN('all','rating_model_v1'))",
            (guild_id, era_id),
        )
        results: list[dict[str, Any]] = []
        access_prior = await self._prior(guild_id, era_id, "access_model_v1")
        rating_prior = await self._prior(guild_id, era_id, "rating_model_v1")
        access_cal = await self._resolved_predictions(guild_id, "access_model_v1")
        rating_cal = await self._resolved_predictions(guild_id, "rating_model_v1")
        access_subgroup_policy = ReadinessPolicy(
            min_observations=settings.subgroup_min_observations,
            min_successes=min(settings.access.min_successes, 3),
            min_failures=min(settings.access.min_failures, 3),
            max_interval_width=settings.access.max_interval_width,
            max_stale_seconds=settings.access.max_stale_seconds,
        )
        rating_subgroup_policy = ReadinessPolicy(
            min_observations=settings.subgroup_min_observations,
            min_successes=min(settings.rating.min_successes, 3),
            min_failures=min(settings.rating.min_failures, 3),
            max_interval_width=settings.rating.max_interval_width,
            max_stale_seconds=settings.rating.max_stale_seconds,
        )

        access_success = sum(str(row["outcome"]) == "success" for row in access_rows)
        access_failure = len(access_rows) - access_success
        access_fresh = max((int(row["last_attempt_ts"] or 0) for row in access_rows), default=0) or None
        access_global = await self._write_snapshot(
            guild_id=guild_id, era_id=era_id, model_key="access_model_v1", version=settings.access_version,
            subgroup="global", successes=access_success, failures=access_failure, prior=access_prior,
            freshness_ts=access_fresh, policy=settings.access, calibration=access_cal, now=now,
        )
        results.append(access_global)
        subgroup_prior = (
            settings.prior_alpha + access_global["mean"] * settings.subgroup_prior_effective_n,
            settings.prior_beta + (1.0 - access_global["mean"]) * settings.subgroup_prior_effective_n,
        )
        for route in sorted({str(row["route_type"]) for row in access_rows}):
            selected = [row for row in access_rows if str(row["route_type"]) == route]
            success = sum(str(row["outcome"]) == "success" for row in selected)
            results.append(await self._write_snapshot(
                guild_id=guild_id, era_id=era_id, model_key="access_model_v1", version=settings.access_version,
                subgroup=f"route:{route}", successes=success, failures=len(selected) - success, prior=subgroup_prior,
                freshness_ts=max((int(row["last_attempt_ts"] or 0) for row in selected), default=0) or None,
                policy=access_subgroup_policy, calibration=access_cal, now=now,
            ))

        rating_success = sum(int(row["rated_within_window"] or 0) == 1 for row in rating_rows)
        rating_failure = len(rating_rows) - rating_success
        rating_fresh = max((int(row["outcome_completed_ts"] or 0) for row in rating_rows), default=0) or None
        rating_global = await self._write_snapshot(
            guild_id=guild_id, era_id=era_id, model_key="rating_model_v1", version=settings.rating_version,
            subgroup="global", successes=rating_success, failures=rating_failure, prior=rating_prior,
            freshness_ts=rating_fresh, policy=settings.rating, calibration=rating_cal, now=now,
        )
        results.append(rating_global)
        rating_subgroup_prior = (
            settings.prior_alpha + rating_global["mean"] * settings.subgroup_prior_effective_n,
            settings.prior_beta + (1.0 - rating_global["mean"]) * settings.subgroup_prior_effective_n,
        )
        for tier in sorted({str(row["recommendation_type"]) for row in rating_rows if row["recommendation_type"]}):
            selected = [row for row in rating_rows if str(row["recommendation_type"]) == tier]
            success = sum(int(row["rated_within_window"] or 0) == 1 for row in selected)
            results.append(await self._write_snapshot(
                guild_id=guild_id, era_id=era_id, model_key="rating_model_v1", version=settings.rating_version,
                subgroup=f"tier:{tier}", successes=success, failures=len(selected) - success, prior=rating_subgroup_prior,
                freshness_ts=max((int(row["outcome_completed_ts"] or 0) for row in selected), default=0) or None,
                policy=rating_subgroup_policy, calibration=rating_cal, now=now,
            ))
        await self._audit_status_transitions(guild_id, era_id, results, now)
        return {"enabled": True, "era": dict(era), "models": results}

    async def _record_versions(self, now: int) -> None:
        settings = self.settings
        shared = asdict(settings)
        for key, version in (
            ("access_model_v1", settings.access_version),
            ("rating_model_v1", settings.rating_version),
            ("capacity_model_v1", settings.capacity_version),
        ):
            config_json = json_compact({"model_key": key, **shared})
            existing = await self.db.fetchone(
                "SELECT config_json FROM bayes_model_versions WHERE model_key=? AND version=?",
                (key, version),
            )
            if existing and str(existing["config_json"]) != config_json:
                raise ValueError(
                    f"{key} configuration changed without a new model version"
                )
            await self.db.execute(
                "INSERT OR IGNORE INTO bayes_model_versions(model_key,version,config_json,created_ts) VALUES(?,?,?,?)",
                (key, version, config_json, now),
            )

    async def _audit_status_transitions(self, guild_id: int, era_id: int, models: list[dict[str, Any]], now: int) -> None:
        for model in models:
            previous = await self.db.fetchone(
                "SELECT status FROM bayes_model_snapshots WHERE guild_id=? AND network_era_id=? AND model_key=? AND subgroup_key=? AND generated_ts<? ORDER BY generated_ts DESC LIMIT 1",
                (guild_id, era_id, model["model_key"], model["subgroup"], int(model["generated_ts"])),
            )
            old = str(previous["status"]) if previous else None
            if old == model["status"]:
                continue
            correlation = new_correlation_id("model-status")
            await self.db.execute(
                "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,payload_json,created_ts) VALUES(?,'bayesian_models',?,'model_status_changed',?,?,?)",
                (
                    correlation,
                    f"{model['model_key']}:{model['subgroup']}",
                    guild_id,
                    json_compact(
                        {
                            "from": old,
                            "to": model["status"],
                            "reason": model["reason"],
                            "snapshot_id": model["snapshot_id"],
                            "model_version": model["model_version"],
                            "observations": model["observations"],
                            "successes": model["successes"],
                            "failures": model["failures"],
                            "interval_90_width": model["intervals"]["90"][1] - model["intervals"]["90"][0],
                        }
                    ),
                    now,
                ),
            )
            if model["subgroup"] == "global" and old is not None:
                await self._notify_model_status(guild_id, model, correlation, now)

    async def _notify_model_status(self, guild_id: int, model: dict[str, Any], correlation: str, now: int) -> None:
        config = self.bot.config
        user_ids = set(config.get_int_list("staff_portal", "owner_user_ids")) | set(config.get_int_list("staff_portal", "dev_user_ids"))
        for user_id in user_ids:
            capacity_note = (
                " Capacity forecasts are now active." if model["model_key"] == "access_model_v1" and model["status"] == "active" else ""
            )
            await self.db.execute(
                "INSERT OR IGNORE INTO discord_outbox(correlation_id,idempotency_key,action_type,guild_id,channel_id,user_id,payload_json,status,attempts,next_attempt_ts,created_ts,updated_ts) VALUES(?,?,'send_dm',?,0,?,?,'pending',0,?,?,?)",
                (correlation, f"model-status:{guild_id}:{model['model_key']}:{model['status']}:{now}:{int(user_id)}", guild_id, int(user_id), json_compact({"content": f"{model['model_key']} is now **{model['status']}**. {model['reason']}{capacity_note}"}), now, now, now),
            )

    async def resolve_predictions(self, guild_id: int) -> int:
        now = int(time.time())
        access = await self.db.execute_affected(
            "UPDATE bayes_predictions SET resolved_outcome=(SELECT CASE o.outcome WHEN 'success' THEN 1 ELSE 0 END FROM level_outreach_opportunities o WHERE CAST(o.id AS TEXT)=bayes_predictions.entity_id),"
            "resolved_ts=?,brier_score=(probability-(SELECT CASE o.outcome WHEN 'success' THEN 1 ELSE 0 END FROM level_outreach_opportunities o WHERE CAST(o.id AS TEXT)=bayes_predictions.entity_id))*(probability-(SELECT CASE o.outcome WHEN 'success' THEN 1 ELSE 0 END FROM level_outreach_opportunities o WHERE CAST(o.id AS TEXT)=bayes_predictions.entity_id)) "
            "WHERE guild_id=? AND model_key='access_model_v1' AND entity_type='opportunity' AND resolved_outcome IS NULL AND EXISTS(SELECT 1 FROM level_outreach_opportunities o WHERE CAST(o.id AS TEXT)=bayes_predictions.entity_id AND o.outcome IN('success','failure'))",
            (now, guild_id),
        )
        rating = await self.db.execute_affected(
            "UPDATE bayes_predictions SET resolved_outcome=(SELECT rated_within_window FROM staff_outreach_episodes e WHERE CAST(e.id AS TEXT)=bayes_predictions.entity_id),"
            "resolved_ts=?,brier_score=(probability-(SELECT rated_within_window FROM staff_outreach_episodes e WHERE CAST(e.id AS TEXT)=bayes_predictions.entity_id))*(probability-(SELECT rated_within_window FROM staff_outreach_episodes e WHERE CAST(e.id AS TEXT)=bayes_predictions.entity_id)) "
            "WHERE guild_id=? AND model_key='rating_model_v1' AND entity_type='episode' AND resolved_outcome IS NULL AND EXISTS(SELECT 1 FROM staff_outreach_episodes e WHERE CAST(e.id AS TEXT)=bayes_predictions.entity_id AND e.rated_within_window IS NOT NULL)",
            (now, guild_id),
        )
        return int(access or 0) + int(rating or 0)

    async def maintenance_once(self, guild_id: int) -> dict[str, Any]:
        projected, migration_exclusions = await self._project_unlinked_attempts(guild_id)
        changed = await self.sync_episode_outcomes(guild_id)
        resolved = await self.resolve_predictions(guild_id)
        computed = await self.recompute(guild_id)
        return {
            "attempts_projected": projected,
            "migration_exclusions": migration_exclusions,
            "episodes_updated": changed,
            "predictions_resolved": resolved,
            **computed,
        }

    async def _project_unlinked_attempts(self, guild_id: int) -> tuple[int, int]:
        """Incrementally project trustworthy PPS attempts into the evidence ledger."""
        rows = await self.db.fetchall(
            "SELECT a.id,a.episode_id FROM level_outreach_attempts a "
            "JOIN level_outreach_queue q ON q.id=a.queue_id "
            "WHERE q.guild_id=? AND a.opportunity_id IS NULL "
            "AND NOT EXISTS(SELECT 1 FROM bayes_model_exclusions x WHERE x.guild_id=q.guild_id "
            "AND x.entity_type='attempt' AND x.entity_id=CAST(a.id AS TEXT) AND x.active=1) "
            "ORDER BY a.id LIMIT 250",
            (guild_id,),
        )
        projected = exclusions = 0
        for row in rows:
            attempt_id = int(row["id"])
            if row["episode_id"] is None:
                await self._ensure_exclusion(
                    guild_id,
                    "attempt",
                    str(attempt_id),
                    "all",
                    "malformed_legacy_data",
                    0,
                )
                exclusions += 1
                continue
            if await self.project_attempt(attempt_id) is not None:
                projected += 1
        return projected, exclusions

    async def latest_models(self, guild_id: int) -> dict[str, Any]:
        era = await self.ensure_active_era(guild_id)
        if not era:
            return {"era": None, "models": []}
        rows = await self.db.fetchall(
            "SELECT s.* FROM bayes_model_snapshots s JOIN (SELECT model_key,subgroup_key,MAX(generated_ts) generated_ts FROM bayes_model_snapshots WHERE guild_id=? AND network_era_id=? GROUP BY model_key,subgroup_key) latest "
            "ON latest.model_key=s.model_key AND latest.subgroup_key=s.subgroup_key AND latest.generated_ts=s.generated_ts WHERE s.guild_id=? AND s.network_era_id=? ORDER BY s.model_key,s.subgroup_key",
            (guild_id, int(era["id"]), guild_id, int(era["id"])),
        )
        models = []
        for row in rows:
            item = dict(row)
            item["intervals"] = json.loads(str(item.pop("intervals_json") or "{}"))
            item["calibration"] = json.loads(str(item.pop("calibration_json") or "{}"))
            models.append(item)
        return {"era": dict(era), "models": models}

    async def capacity(
        self,
        guild_id: int,
        opportunities: int,
        *,
        route_composition: dict[str, int] | None = None,
        cycle_id: int | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        latest = await self.latest_models(guild_id)
        access = next((row for row in latest["models"] if row["model_key"] == "access_model_v1" and row["subgroup_key"] == "global"), None)
        if not access:
            await self.recompute(guild_id)
            latest = await self.latest_models(guild_id)
            access = next((row for row in latest["models"] if row["model_key"] == "access_model_v1" and row["subgroup_key"] == "global"), None)
        if not access:
            raise ValueError("The access model has not produced a snapshot yet")
        seed = int(time.time() // max(60, self.settings.refresh_seconds))
        composition = {
            str(route): max(0, int(count))
            for route, count in (route_composition or {}).items()
            if str(route) in {"direct", "network", "stream", "event", "other"} and int(count) > 0
        }
        if composition:
            groups = []
            selected_models = []
            for route, count in composition.items():
                subgroup = next(
                    (
                        row for row in latest["models"]
                        if row["model_key"] == "access_model_v1"
                        and row["subgroup_key"] == f"route:{route}"
                        and row["status"] == "active"
                    ),
                    access,
                )
                groups.append((float(subgroup["alpha"]), float(subgroup["beta"]), count))
                selected_models.append({"route": route, "count": count, "snapshot_id": int(subgroup["id"])})
            result = await asyncio.to_thread(
                capacity_forecast_groups,
                groups,
                draws=self.settings.monte_carlo_draws,
                seed=seed,
            )
        else:
            selected_models = [{"route": "global", "count": int(opportunities), "snapshot_id": int(access["id"])}]
            result = await asyncio.to_thread(
                capacity_forecast,
                float(access["alpha"]),
                float(access["beta"]),
                opportunities,
                draws=self.settings.monte_carlo_draws,
                seed=seed,
            )
        result["model_inputs"] = selected_models
        if persist:
            now = int(time.time())
            await self.db.execute(
                "INSERT INTO bayes_capacity_forecasts(guild_id,network_era_id,cycle_id,model_version,queue_size,expected_successes,k80,k90,k95,draws,seed,input_json,generated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (guild_id, int(latest["era"]["id"]), cycle_id, self.settings.capacity_version, int(opportunities), result["expected_successes"], result["k80"], result["k90"], result["k95"], result["draws"], result["seed"], json_compact({"models": selected_models}), now),
            )
        return result

    async def public_projection(self, guild_id: int, send_type: str | None = None) -> dict[str, Any] | None:
        if not self.settings.public_auto_activate:
            return None
        latest = await self.latest_models(guild_id)
        access = next((row for row in latest["models"] if row["model_key"] == "access_model_v1" and row["subgroup_key"] == "global"), None)
        rating = next((row for row in latest["models"] if row["model_key"] == "rating_model_v1" and row["subgroup_key"] == f"tier:{send_type}"), None)
        if not rating or rating["status"] not in PUBLIC_MODEL_STATUSES:
            rating = next((row for row in latest["models"] if row["model_key"] == "rating_model_v1" and row["subgroup_key"] == "global"), None)
        access = access if access and access["status"] in PUBLIC_MODEL_STATUSES else None
        rating = rating if rating and rating["status"] in PUBLIC_MODEL_STATUSES else None
        if not access and not rating:
            return None
        projection: dict[str, Any] = {
            "status": "active",
            "model_versions": {},
            "data_cutoff": max(
                int(access.get("data_cutoff_ts") or 0) if access else 0,
                int(rating.get("data_cutoff_ts") or 0) if rating else 0,
            ),
            "generated_at": max(
                int(access["generated_ts"]) if access else 0,
                int(rating["generated_ts"]) if rating else 0,
            ),
        }
        if access:
            projection.update({
                "access_probability_percent": int(round(float(access["posterior_mean"]) * 100)),
                "access_credible_interval_90_percent": [
                    int(round(value * 100)) for value in (access.get("intervals") or {}).get("90", [0, 1])
                ],
                "access_evidence_strength": str(access["evidence_strength"]),
            })
            projection["model_versions"]["access"] = access["model_version"]
        if rating:
            projection.update({
                "rating_probability_percent": int(round(float(rating["posterior_mean"]) * 100)),
                "rating_credible_interval_90_percent": [
                    int(round(value * 100)) for value in (rating.get("intervals") or {}).get("90", [0, 1])
                ],
                "rating_evidence_strength": str(rating["evidence_strength"]),
            })
            projection["model_versions"]["rating"] = rating["model_version"]
        if access and rating:
            product = await asyncio.to_thread(
                monte_carlo_product,
                (float(access["alpha"]), float(access["beta"])),
                (float(rating["alpha"]), float(rating["beta"])),
                draws=self.settings.monte_carlo_draws,
                seed=int(latest["era"]["id"]),
            )
            strength = min(
                (access["evidence_strength"], rating["evidence_strength"]),
                key=lambda value: {"limited": 0, "moderate": 1, "strong": 2}.get(value, 0),
            )
            projection.update({
                "probability_percent": int(round(product["mean"] * 100)),
                "credible_interval_90_percent": [int(round(value * 100)) for value in product["intervals"]["90"]],
                "evidence_strength": strength,
            })
            await self._record_public_projection(guild_id, latest["era"], access, rating, product, strength, send_type)
        return projection

    async def _record_public_projection(
        self,
        guild_id: int,
        era: dict[str, Any],
        access: dict[str, Any],
        rating: dict[str, Any],
        product: dict[str, Any],
        strength: str,
        send_type: str | None,
    ) -> None:
        entity_id = f"{int(era['id'])}:{send_type or 'global'}:{int(access['id'])}:{int(rating['id'])}"
        predicted_ts = max(int(access["generated_ts"]), int(rating["generated_ts"]))
        await self.db.execute(
            "INSERT OR IGNORE INTO bayes_predictions(guild_id,network_era_id,model_key,model_version,entity_type,entity_id,subgroup_key,probability,median,intervals_json,data_cutoff_ts,status,evidence_strength,predicted_ts,metadata_json,access_snapshot_id,rating_snapshot_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                guild_id, int(era["id"]), "overall_path_v1",
                f"{access['model_version']}+{rating['model_version']}", "public_tier", entity_id,
                f"tier:{send_type or 'global'}", float(product["mean"]), float(product["median"]),
                json_compact(product["intervals"]),
                max(int(access.get("data_cutoff_ts") or 0), int(rating.get("data_cutoff_ts") or 0)),
                "active", strength, predicted_ts,
                json_compact({"public": True, "send_type": send_type or "global"}),
                int(access["id"]), int(rating["id"]),
            ),
        )

    async def set_publication_pause(self, guild_id: int, model_key: str, paused: bool, actor_id: int, reason: str) -> None:
        if model_key not in MODEL_KEYS:
            raise ValueError("Unknown model")
        now = int(time.time())
        await self.db.execute(
            "INSERT INTO bayes_model_controls(guild_id,model_key,publication_paused,pause_reason,updated_by,updated_ts) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(guild_id,model_key) DO UPDATE SET publication_paused=excluded.publication_paused,pause_reason=excluded.pause_reason,updated_by=excluded.updated_by,updated_ts=excluded.updated_ts",
            (guild_id, model_key, int(bool(paused)), str(reason or "")[:1000], actor_id, now),
        )
        await self.db.execute(
            "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) VALUES(?,'bayesian_models',?,'publication_control_changed',?,?,?,?)",
            (new_correlation_id("model-control"), model_key, guild_id, actor_id, json_compact({"paused": bool(paused), "reason": str(reason or "")[:1000]}), now),
        )
