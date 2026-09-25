import json
import time
from types import SimpleNamespace

import pytest

from services.bayesian_models import BayesianModelService
from services.level_notifications import LevelNotificationService
from services.priority_system import PrioritySystemService
from utils.bayesian import (
    ReadinessPolicy,
    beta_posterior,
    calibration_metrics,
    capacity_forecast,
    credible_intervals,
    model_status,
)
from utils.db import Database


GUILD_ID = 717
ACTOR_ID = 11


class ModelConfig:
    def __init__(self):
        self.data = {
            "guild": {"allowed_guild_id": GUILD_ID},
            "staff_portal": {"owner_user_ids": [ACTOR_ID], "dev_user_ids": []},
            "priority_system": {
                "model_version": "pps_v1",
                "prestige_base": 1.8,
                "prestige_x": {"rate": 0, "feature": 1.25, "epic": 2.5, "legendary": 3.75, "mythic": 5},
                "creator_opportunity": {"numerator": 0.25, "slope": 0.06, "offset": 0.01, "zero_from_cp": 4},
                "waiting": {"multiplier": 1.5, "exponent": 1.5, "score_cap_cycles": 4},
                "outcome_window_days": 30,
                "bayesian": {
                    "enabled": True,
                    "public_probability_auto_activate": True,
                    "model_versions": {"access": "access_model_v1", "rating": "rating_model_v1", "capacity": "capacity_model_v1"},
                    "prior_alpha": 1,
                    "prior_beta": 1,
                    "carryover_effective_n": 4,
                    "subgroup_prior_effective_n": 4,
                    "subgroup_min_observations": 15,
                    "monte_carlo_draws": 1000,
                    "access": {"min_observations": 5, "min_successes": 1, "min_failures": 1, "max_90_interval_width": 1, "max_stale_days": 45},
                    "rating": {"min_observations": 5, "min_successes": 1, "min_failures": 1, "max_90_interval_width": 1, "max_stale_days": 60},
                    "calibration": {"min_resolved_predictions": 5, "max_ece": 0.5, "max_brier": 0.5},
                },
            },
        }

    def get_int_list(self, *path, default=None):
        value = self.data
        for key in path:
            if not isinstance(value, dict) or key not in value:
                return list(default or [])
            value = value[key]
        return [int(item) for item in value]

    def get(self, *path, default=None):
        value = self.data
        for key in path:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


def make_bot(db):
    return SimpleNamespace(db=db, config=ModelConfig(), get_cog=lambda _name: None)


async def insert_cycle_queue(db, *, current_rated=0):
    now = int(time.time())
    queue_id = await db.execute_insert(
        "INSERT INTO level_outreach_queue(guild_id,wave_id,requester_id,request_message_id,level_id,send_type,queued_ts,prestige_t,prestige_component_f,current_creator_points,creator_component_g,waiting_cycles,waiting_component_h,priority_points,priority_complete,model_version,queue_state,current_rated,correlation_id,updated_ts) "
        "VALUES(?,?,?,?,?,'epic',?,2.5,3.346,0,3.219,0,0,6.565,1,'pps_v1','queued',?,'queue-test',?)",
        (GUILD_ID, 1, 22, 33, "101935961", now, current_rated, now),
    )
    cycle_id = await db.execute_insert(
        "INSERT INTO level_outreach_cycles(guild_id,status,started_by,started_ts,correlation_id) VALUES(?,'active',?,?,?)",
        (GUILD_ID, ACTOR_ID, now, "cycle-test"),
    )
    await db.execute(
        "INSERT INTO level_outreach_cycle_entries(cycle_id,queue_id,priority_complete_snapshot,prestige_component_f_snapshot,waiting_component_h_snapshot,waiting_cycles_snapshot,model_version_snapshot) VALUES(?,?,1,3.346,0,0,'pps_v1')",
        (cycle_id, queue_id),
    )
    await db.execute("UPDATE level_outreach_queue SET queue_state='in_cycle' WHERE id=?", (queue_id,))
    return queue_id, cycle_id


def test_beta_posterior_intervals_calibration_and_capacity_are_deterministic():
    assert beta_posterior(3, 2) == (4, 3)
    intervals = credible_intervals(4, 3)
    assert set(intervals) == {"50", "80", "90", "95"}
    assert intervals["90"][0] < 4 / 7 < intervals["90"][1]
    metrics = calibration_metrics([(0.2, 0), (0.8, 1), (0.6, 1), (0.4, 0)])
    assert metrics["brier"] == pytest.approx(0.1)
    assert sum(item["count"] for item in metrics["bins"]) == 4
    assert capacity_forecast(4, 3, 12, draws=2000, seed=91) == capacity_forecast(4, 3, 12, draws=2000, seed=91)


def test_readiness_covers_collecting_provisional_active_degraded_and_paused():
    policy = ReadinessPolicy(10, 2, 2, 0.5, 100)
    common = dict(interval_90_width=0.4, freshness_ts=1000, now_ts=1050, policy=policy)
    assert model_status(observations=2, successes=1, failures=1, **common)[0] == "collecting"
    assert model_status(observations=5, successes=3, failures=2, **common)[0] == "provisional"
    assert model_status(observations=10, successes=6, failures=4, **common)[0] == "active"
    assert model_status(observations=10, successes=6, failures=4, **{**common, "now_ts": 1200})[0] == "degraded"
    assert model_status(observations=10, successes=6, failures=4, paused=True, **common)[0] == "paused"


@pytest.mark.asyncio
async def test_opportunities_deduplicate_followups_and_separate_targets(tmp_path):
    db = Database(str(tmp_path / "opportunities.db"))
    await db.connect()
    service = PrioritySystemService(make_bot(db))
    queue_id, cycle_id = await insert_cycle_queue(db)
    for status, target, key in (
        ("planned", "Moderator A", "a-plan"),
        ("attempted", "Moderator A", "a-attempt"),
        ("attempted", "Moderator B", "b-attempt"),
        ("failed", "Moderator B", "b-failed"),
        ("submitted_to_mod", "Moderator A", "a-submit"),
        ("follow_up", "Moderator A", "a-follow"),
    ):
        await service.record_attempt(
            GUILD_ID, cycle_id, queue_id, ACTOR_ID, status=status, route_type="direct",
            target_label=target, idempotency_key=key,
        )
    rows = await db.fetchall("SELECT * FROM level_outreach_opportunities ORDER BY private_target_key")
    assert len(rows) == 2
    by_target = {row["private_target_key"]: row for row in rows}
    assert by_target["moderatora"]["outcome"] == "success"
    assert by_target["moderatora"]["follow_up_count"] == 1
    assert by_target["moderatora"]["first_planned_ts"] is not None
    assert by_target["moderatorb"]["outcome"] == "failure"
    assert (await db.fetchone("SELECT COUNT(*) AS c FROM level_outreach_targets"))["c"] == 2
    episodes = await db.fetchone("SELECT COUNT(*) AS c FROM staff_outreach_episodes WHERE queue_id=?", (queue_id,))
    assert episodes["c"] == 1
    await db.close()


@pytest.mark.asyncio
async def test_prerated_episode_is_excluded_and_does_not_change_pps(tmp_path):
    db = Database(str(tmp_path / "prerated.db"))
    await db.connect()
    bot = make_bot(db)
    service = PrioritySystemService(bot)
    queue_id, cycle_id = await insert_cycle_queue(db, current_rated=1)
    before = await db.fetchone("SELECT priority_points,waiting_cycles,send_type FROM level_outreach_queue WHERE id=?", (queue_id,))
    await service.record_attempt(
        GUILD_ID, cycle_id, queue_id, ACTOR_ID, status="submitted_to_mod", route_type="direct",
        target_label="Moderator A", idempotency_key="pre-rated-submit",
    )
    episode = await db.fetchone("SELECT * FROM staff_outreach_episodes WHERE queue_id=?", (queue_id,))
    assert episode["rated_before_first_submission"] == 1
    assert episode["exclusion_reason"] == "rated_before_outreach"
    after = await db.fetchone("SELECT priority_points,waiting_cycles,send_type FROM level_outreach_queue WHERE id=?", (queue_id,))
    assert dict(after) == dict(before)
    await db.close()


@pytest.mark.asyncio
async def test_model_recompute_exact_posterior_era_carryover_and_public_gate(tmp_path):
    db = Database(str(tmp_path / "models.db"))
    await db.connect()
    bot = make_bot(db)
    models = BayesianModelService(bot)
    era = await models.ensure_active_era(GUILD_ID, ACTOR_ID)
    now = int(time.time())
    for index, outcome in enumerate(("success", "success", "success", "failure", "failure"), start=1):
        await db.execute(
            "INSERT INTO level_outreach_opportunities(guild_id,network_era_id,episode_id,queue_id,level_id,private_target_key,route_type,initial_route,current_route,first_attempt_ts,last_attempt_ts,outcome,outcome_ts,source_attempt_id,updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (GUILD_ID, era["id"], index, index, str(index), f"target-{index}", "direct", "direct", "direct", now, now, outcome, now, index, now),
        )
    result = await models.recompute(GUILD_ID)
    access = next(item for item in result["models"] if item["model_key"] == "access_model_v1" and item["subgroup"] == "global")
    assert (access["alpha"], access["beta"]) == (4, 3)
    assert access["status"] == "active"
    projection = await models.public_projection(GUILD_ID, "epic")
    assert projection is not None
    assert projection["access_probability_percent"] == 57
    assert "rating_probability_percent" not in projection
    assert "probability_percent" not in projection
    for index, rated in enumerate((1, 1, 1, 0, 0), start=1):
        await db.execute(
            "INSERT INTO staff_outreach_episodes(guild_id,queue_id,episode_number,status,started_by,started_ts,network_era_id,recommendation_type,first_confirmed_submission_ts,outcome_window_end_ts,rated_within_window,outcome_completed_ts) "
            "VALUES(?,?,1,'completed',?,?,?,?,?,?,?,?)",
            (GUILD_ID, 100 + index, ACTOR_ID, now, era["id"], "epic", now - 31 * 86400, now - 86400, rated, now),
        )
    await models.recompute(GUILD_ID)
    complete_projection = await models.public_projection(GUILD_ID, "epic")
    assert complete_projection is not None
    assert complete_projection["rating_probability_percent"] == 57
    assert "probability_percent" in complete_projection
    overall = await db.fetchone(
        "SELECT access_snapshot_id,rating_snapshot_id FROM bayes_predictions WHERE model_key='overall_path_v1'"
    )
    assert overall["access_snapshot_id"] is not None
    assert overall["rating_snapshot_id"] is not None
    next_era = await models.start_new_era(GUILD_ID, ACTOR_ID, "Era 2", "Network structure changed", "Network recalibration")
    assert next_era["prior_alpha"] == pytest.approx(1 + (4 / 7) * 4)
    assert next_era["prior_beta"] == pytest.approx(1 + (3 / 7) * 4)
    await db.close()


@pytest.mark.asyncio
async def test_level_notifications_respect_opt_out_and_are_idempotent(tmp_path):
    db = Database(str(tmp_path / "notifications.db"))
    await db.connect()
    service = LevelNotificationService(make_bot(db))
    assert await service.subscribe_requester(GUILD_ID, "101935961", 22)
    first = await service.emit(GUILD_ID, "101935961", "reached_moderator", "event-1", "The level reached a moderator.")
    second = await service.emit(GUILD_ID, "101935961", "reached_moderator", "event-1", "The level reached a moderator.")
    assert (first, second) == (1, 0)
    assert (await db.fetchone("SELECT COUNT(*) AS c FROM discord_outbox"))["c"] == 1
    assert (await db.fetchone("SELECT COUNT(*) AS c FROM level_notification_deliveries"))["c"] == 1
    await db.execute(
        "INSERT INTO user_notification_preferences(guild_id,user_id,request_result_mode,updated_ts) VALUES(?,?,'none',?) ON CONFLICT(guild_id,user_id) DO UPDATE SET request_result_mode='none',updated_ts=excluded.updated_ts",
        (GUILD_ID, 23, int(time.time())),
    )
    assert not await service.subscribe_requester(GUILD_ID, "101935961", 23)
    await db.close()


@pytest.mark.asyncio
async def test_routine_outreach_attempt_is_not_a_public_notification_event(tmp_path):
    db = Database(str(tmp_path / "quiet-attempt.db"))
    await db.connect()
    service = LevelNotificationService(make_bot(db))
    await service.subscribe_requester(GUILD_ID, "101935961", 22)
    with pytest.raises(ValueError, match="Unknown level notification event"):
        await service.emit(
            GUILD_ID,
            "101935961",
            "outreach_started",
            "routine-attempt",
            "Private outreach started.",
        )
    assert (await db.fetchone("SELECT COUNT(*) AS c FROM discord_outbox"))["c"] == 0
    await db.close()
