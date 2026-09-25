"""Durable, restartable side-development backfill using the shared DB and providers."""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from utils.gd_profile import fetch_creator_profile
from utils.historical_audit import (
    audit_settings, build_dataset, normalize_level_id, render_exports, summarize,
)


class HistoricalAuditService:
    # SQL identifiers below are fixed internal constants; values are bound parameters.
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self._start_lock = asyncio.Lock()
        self._lease_owner = uuid.uuid4().hex

    async def _write(self, statements):
        await self.db.execute_transaction(statements, retry_safe=True, queue_timeout=1,
                                          operation_label="history.audit")

    async def get_run(self, run_id: str | None = None):
        if run_id:
            row = await self.db.fetchone("SELECT * FROM historical_audit_runs WHERE run_id=?", (run_id,))
        else:
            row = await self.db.fetchone("SELECT * FROM historical_audit_runs ORDER BY started_ts DESC,rowid DESC LIMIT 1")
        return dict(row) if row else None

    async def active_run(self):
        row = await self.db.fetchone("SELECT * FROM historical_audit_runs WHERE status IN ('queued','running') LIMIT 1")
        return dict(row) if row else None

    async def start(self, guild_id: int, owner_id: int, *, refresh_external=True, force=False,
                    labels=None, import_errors=None) -> str:
        settings = audit_settings(self.bot.config.data)
        if not settings["enabled"]:
            raise ValueError("Historical audit is disabled in config.json")
        if force and not refresh_external:
            raise ValueError("force requires refresh_external=true")
        async with self._start_lock:
            active = await self.active_run()
            if active:
                raise ValueError(f"Audit {active['run_id']} is already {active['status']}; inspect or cancel it first")
            prior_labels = [dict(row) for row in await self.db.fetchall("SELECT * FROM historical_prestige_labels ORDER BY imported_ts,evidence_id")]
            supplied = labels or []
            combined = {row["evidence_id"]: row for row in [*prior_labels, *supplied]}
            settings.update({"refresh_external": bool(refresh_external), "force": bool(force),
                             "labels": list(combined.values()), "import_errors": import_errors or []})
            now = int(time.time())
            run_id = uuid.uuid4().hex
            statements = [(
                "INSERT INTO historical_audit_runs(run_id,guild_id,owner_id,started_ts,status,config_json) VALUES(?,?,?,?,?,?)",
                (run_id, guild_id, str(owner_id), now, "queued", json.dumps(settings, separators=(",", ":"))),
            ), (
                "INSERT INTO historical_audit_requests(run_id,guild_id,wave_id,requester_id,level_id,request_message_id,created_ts,reviewed_ts,reviewed_by,status,historical_result,review_text) "
                "SELECT ?,guild_id,wave_id,CAST(user_id AS TEXT),level_id,CAST(request_message_id AS TEXT),created_ts,reviewed_ts,CAST(reviewed_by AS TEXT),status,result,review_text "
                "FROM level_request_submissions WHERE guild_id=?",
                (run_id, guild_id),
            )]
            # Input capture must happen once. Unknown commits are reconciled by this
            # UUID, not replayed against a potentially changed live history table.
            try:
                await self.db.execute_transaction(statements, queue_timeout=1, operation_label="history.capture")
            except Exception:
                if not await self.get_run(run_id):
                    raise
            return run_id

    async def progress(self, run_id: str) -> dict:
        result = {}
        for table, key in (("historical_audit_levels", "levels"), ("historical_audit_creators", "creators")):
            row = await self.db.fetchone(f"SELECT COUNT(*) AS total,SUM(snapshot_json IS NOT NULL) AS done FROM {table} WHERE run_id=?", (run_id,))  # nosec B608
            result[key] = {"total": int(row["total"]), "done": int(row["done"] or 0)}
        row = await self.db.fetchone("SELECT COUNT(*) AS total FROM historical_audit_requests WHERE run_id=?", (run_id,))
        result["requests"] = int(row["total"])
        return result

    async def cancel(self, run_id: str):
        await self._write([("UPDATE historical_audit_runs SET status='cancelled',completed_ts=? WHERE run_id=? AND status IN ('queued','running')", (int(time.time()), run_id))])

    async def _running(self, run_id: str) -> bool:
        if not audit_settings(self.bot.config.data)["enabled"]:
            return False
        row = await self.db.fetchone("SELECT status,lease_owner,lease_until_ts FROM historical_audit_runs WHERE run_id=?", (run_id,))
        now = int(time.time())
        if not row or row["status"] not in {"queued", "running"} or row["lease_owner"] != self._lease_owner or int(row["lease_until_ts"]) <= now:
            return False
        if int(row["lease_until_ts"]) - now < 60:
            await self._write([("UPDATE historical_audit_runs SET lease_until_ts=? WHERE run_id=? AND lease_owner=? AND lease_until_ts>? AND status IN ('queued','running')", (now + 120, run_id, self._lease_owner, now))])
            return await self._running(run_id)
        return True

    async def _claim_run(self, run_id: str) -> bool:
        now = int(time.time())
        await self._write([("UPDATE historical_audit_runs SET lease_owner=?,lease_until_ts=? WHERE run_id=? AND status IN ('queued','running') AND (lease_until_ts<=? OR lease_owner=?)", (self._lease_owner, now + 120, run_id, now, self._lease_owner))])
        row = await self.db.fetchone("SELECT status,lease_owner,lease_until_ts FROM historical_audit_runs WHERE run_id=?", (run_id,))
        return bool(row and row["status"] in {"queued", "running"} and row["lease_owner"] == self._lease_owner and int(row["lease_until_ts"]) > now)

    async def _cached(self, kind: str, identity: str, config: dict) -> dict | None:
        table, key = ("historical_level_audit_snapshots", "level_id") if kind == "level" else ("historical_creator_audit_snapshots", "account_id")
        row = await self.db.fetchone(f"SELECT * FROM {table} WHERE {key}=?", (identity,))  # nosec B608
        if not row or config["force"]:
            return None
        if config["refresh_external"] and int(row["expires_ts"]) <= int(time.time()):
            return None
        result = json.loads(row["data_json"])
        result["audit_cache_hit"] = True
        result["audit_snapshot_stale"] = int(row["expires_ts"]) <= int(time.time())
        return result

    async def lookup_level(self, level_id: str, config: dict) -> dict:
        cached = await self._cached("level", level_id, config)
        if cached is not None:
            return cached
        if not config["refresh_external"]:
            return {"level_id": level_id, "gd_lookup_status": "not_requested", "current_exists": None, "current_rated": None}
        cog = self.bot.get_cog("RequestLevelsCog")
        providers = cog._level_validation_providers()
        session = await cog._get_level_validation_session()
        results = {}
        # Intentionally sequential: an exploratory job never creates a fan-out of
        # concurrent live validation calls. Existing provider locks/circuits apply.
        for provider in ("gdhistory", "gdrateplus", "boomlings", "gdbrowser"):
            if providers.get(provider):
                try:
                    results[provider] = await cog._fetch_validation_provider(provider, session, level_id)
                except Exception as exc:
                    results[provider] = {"ok": False, "exists": None, "error": type(exc).__name__}
        return normalize_level_snapshot(level_id, results)

    async def lookup_creator(self, account_id: str, config: dict) -> dict:
        cached = await self._cached("creator", account_id, config)
        if cached is not None:
            return cached
        if not config["refresh_external"]:
            return {"account_id": account_id, "current_creator_points": None, "cp_lookup_status": "not_requested"}
        cog = self.bot.get_cog("RequestLevelsCog")
        result = {"ok": False, "error": "Direct Boomlings provider disabled"}
        if cog._level_validation_providers().get("boomlings"):
            lock = cog._validation_provider_locks.setdefault("boomlings", asyncio.Lock())
            async with lock:
                if cog._provider_circuit_open("boomlings"):
                    result = cog._provider_circuit_result("boomlings")
                else:
                    session = await cog._get_level_validation_session()
                    for attempt in range(2):
                        elapsed = time.monotonic() - cog._validation_provider_last_call.get("boomlings", 0)
                        await asyncio.sleep(max(0, max(config["lookup_interval_seconds"], cog._provider_min_interval("boomlings")) - elapsed))
                        try:
                            result = await fetch_creator_profile(session, account_id)
                        except Exception as exc:
                            result = {"ok": False, "error": type(exc).__name__, "failure_kind": "invalid_response"}
                        cog._validation_provider_last_call["boomlings"] = time.monotonic()
                        if result.get("ok") or not result.get("retryable") or result.get("failure_kind") == "rate_limited":
                            break
                        if attempt == 0:
                            await asyncio.sleep(2)
                    cog._record_provider_validation_result("boomlings", result)
        now = int(time.time())
        return {"account_id": account_id, "cp_checked_ts": now,
                "current_creator_points": result.get("current_creator_points") if result.get("ok") else None,
                "user_id": result.get("user_id"), "name": result.get("name"),
                "cp_lookup_status": "ok" if result.get("ok") else str(result.get("failure_kind", "unavailable")),
                "cp_lookup_error": None if result.get("ok") else str(result.get("error", "Profile unavailable"))[:500]}

    async def _save_snapshot(self, run_id, kind, identity, snapshot, config):
        item_table, cache_table, key, status_key, ts_key = (
            ("historical_audit_levels", "historical_level_audit_snapshots", "level_id", "gd_lookup_status", "gd_checked_ts")
            if kind == "level" else
            ("historical_audit_creators", "historical_creator_audit_snapshots", "account_id", "cp_lookup_status", "cp_checked_ts")
        )
        serialized = json.dumps(snapshot, separators=(",", ":"))
        statements = [(f"UPDATE {item_table} SET snapshot_json=? WHERE run_id=? AND {key}=? AND snapshot_json IS NULL "  # nosec B608
                       "AND EXISTS(SELECT 1 FROM historical_audit_runs WHERE run_id=? AND lease_owner=? AND lease_until_ts>? AND status IN ('queued','running'))",
                       (serialized, run_id, identity, run_id, self._lease_owner, int(time.time())))]
        if not snapshot.get("audit_cache_hit") and snapshot.get(ts_key):
            checked = int(snapshot[ts_key])
            ttl = config["success_cache_seconds"] if snapshot.get(status_key) == "ok" else config["failure_cache_seconds"]
            statements.append((
                f"INSERT INTO {cache_table}({key},checked_ts,expires_ts,data_json) VALUES(?,?,?,?) "  # nosec B608
                f"ON CONFLICT({key}) DO UPDATE SET checked_ts=excluded.checked_ts,expires_ts=excluded.expires_ts,data_json=excluded.data_json WHERE excluded.checked_ts >= {cache_table}.checked_ts",
                (identity, checked, int(checked + ttl), serialized),
            ))
        await self._write(statements)

    async def process(self, run: dict):
        if not await self._claim_run(run["run_id"]):
            return
        try:
            await self._process_owned(run)
        finally:
            try:
                await self._write([("UPDATE historical_audit_runs SET lease_owner=NULL,lease_until_ts=0 WHERE run_id=? AND lease_owner=?", (run["run_id"], self._lease_owner))])
            except Exception:
                pass  # Crash/failed release is bounded by the durable lease expiry.

    async def _process_owned(self, run: dict):
        run_id = run["run_id"]
        config = json.loads(run["config_json"])
        await self._write([("UPDATE historical_audit_runs SET status='running',error_text=NULL WHERE run_id=? AND status='queued'", (run_id,))])
        # Import evidence in short retry-safe batches, outside the one-time live
        # history capture. The run's frozen evidence already survives restarts.
        saved_evidence = {row["evidence_id"] for row in await self.db.fetchall("SELECT evidence_id FROM historical_prestige_labels")}
        new_evidence = [row for row in config["labels"] if row["evidence_id"] not in saved_evidence]
        for offset in range(0, len(new_evidence), 50):
            if not await self._running(run_id):
                return
            await self._write([(
                "INSERT OR IGNORE INTO historical_prestige_labels(evidence_id,imported_ts,level_id,wave_id,request_date,prestige,source,source_detail) VALUES(?,?,?,?,?,?,?,?)",
                (row["evidence_id"], run["started_ts"], row["level_id"], row.get("wave_id"), row.get("request_date"), row["prestige"], row["source"], row["source_detail"]),
            ) for row in new_evidence[offset:offset + 50]])
        rows = await self.db.fetchall("SELECT DISTINCT level_id FROM historical_audit_requests WHERE run_id=?", (run_id,))
        unique = sorted({value for row in rows if (value := normalize_level_id(row["level_id"]))})
        prepared = {row["level_id"] for row in await self.db.fetchall("SELECT level_id FROM historical_audit_levels WHERE run_id=?", (run_id,))}
        unique = [identity for identity in unique if identity not in prepared]
        for offset in range(0, len(unique), 50):
            if not await self._running(run_id):
                return
            await self._write([("INSERT OR IGNORE INTO historical_audit_levels(run_id,level_id) VALUES(?,?)", (run_id, identity)) for identity in unique[offset:offset + 50]])
        for kind, table, key in (("level", "historical_audit_levels", "level_id"), ("creator", "historical_audit_creators", "account_id")):
            while await self._running(run_id):
                item = await self.db.fetchone(f"SELECT {key} FROM {table} WHERE run_id=? AND snapshot_json IS NULL ORDER BY {key} LIMIT 1", (run_id,))  # nosec B608
                if not item:
                    break
                identity = item[key]
                # Cache/database failures propagate to supervised retries instead
                # of being mislabeled as an API failure or poisoning the cache.
                snapshot = await (self.lookup_level(identity, config) if kind == "level" else self.lookup_creator(identity, config))
                await self._save_snapshot(run_id, kind, identity, snapshot, config)
                await asyncio.sleep(config["lookup_interval_seconds"])
            if not await self._running(run_id):
                return
            if kind == "level":
                level_items = await self.db.fetchall("SELECT snapshot_json FROM historical_audit_levels WHERE run_id=?", (run_id,))
                accounts = sorted({str(account) for row in level_items if (account := json.loads(row["snapshot_json"]).get("current_uploader_account_id"))})
                prepared_accounts = {row["account_id"] for row in await self.db.fetchall("SELECT account_id FROM historical_audit_creators WHERE run_id=?", (run_id,))}
                accounts = [account for account in accounts if account not in prepared_accounts]
                for offset in range(0, len(accounts), 50):
                    if not await self._running(run_id):
                        return
                    await self._write([("INSERT OR IGNORE INTO historical_audit_creators(run_id,account_id) VALUES(?,?)", (run_id, account)) for account in accounts[offset:offset + 50]])
        _exports, summary = await self.report(run_id, include_exports=False)
        summary["run_status_at_export"] = "completed"
        now = int(time.time())
        await self._write([("UPDATE historical_audit_runs SET status='completed',completed_ts=?,summary_json=?,error_text=NULL WHERE run_id=? AND status='running' AND lease_owner=? AND lease_until_ts>?",
                            (now, json.dumps(summary, separators=(",", ":")), run_id, self._lease_owner, now))])

    async def report(self, run_id: str, *, include_exports=True):
        run = await self.get_run(run_id)
        if not run:
            raise ValueError("Audit run was not found")
        requests = [dict(row) for row in await self.db.fetchall("SELECT * FROM historical_audit_requests WHERE run_id=?", (run_id,))]
        levels, creators = {}, {}
        for table, key, target in (("historical_audit_levels", "level_id", levels), ("historical_audit_creators", "account_id", creators)):
            for row in await self.db.fetchall(f"SELECT * FROM {table} WHERE run_id=? AND snapshot_json IS NOT NULL", (run_id,)):  # nosec B608
                target[str(row[key])] = json.loads(row["snapshot_json"])
        config = json.loads(run["config_json"])
        def build():
            dataset, enrichment = build_dataset(requests, levels, creators, config["labels"], config, int(run["started_ts"]))
            summary = summarize(dataset, levels, creators, enrichment, run_id)
            summary["import_errors"] = config["import_errors"]
            summary["candidate_configuration"] = {key: config[key] for key in ("prestige_x", "cp_formula", "candidate_model_version")}
            summary["run_status_at_export"] = run["status"]
            return render_exports(dataset, levels, creators, enrichment, summary) if include_exports else None, summary
        return await asyncio.to_thread(build)


def normalize_level_snapshot(level_id: str, results: dict) -> dict:
    existing = [row for row in results.values() if row.get("ok") and row.get("exists") is True]
    missing = [row for row in results.values() if row.get("ok") and row.get("exists") is False]
    success = bool(existing) or bool(results) and len(missing) == len(results)
    snapshot = {"level_id": level_id, "gd_checked_ts": int(time.time()),
                "current_exists": True if existing else False if success else None,
                "current_rated": None, "current_level_name": None, "current_uploader_name": None,
                "current_uploader_user_id": None, "current_uploader_account_id": None,
                "current_stars": None, "current_featured": None, "current_epic": None, "current_epic_tier_raw": None,
                "current_legendary": None, "current_mythic": None,
                "gd_lookup_status": "ok" if success else "unavailable",
                "gd_lookup_error": None, "gd_provider_status": {name: {key: row.get(key) for key in ("ok", "exists", "failure_kind", "error")} for name, row in results.items()}}
    if existing:
        chosen = next((row for row in existing if row.get("provider") == "boomlings"), existing[0])
        metadata = chosen.get("audit_metadata", {})
        snapshot.update({"current_level_name": chosen.get("name"), "current_uploader_name": chosen.get("creator"),
                         "current_difficulty": chosen.get("difficulty"), "current_length": chosen.get("length")})
        for key in ("rated", "stars", "featured", "epic", "epic_tier_raw", "legendary", "mythic"):
            snapshot[f"current_{key}"] = metadata.get(key)
        for key in ("uploader_user_id", "uploader_account_id"):
            values = {str(value) for row in existing if (value := row.get("audit_metadata", {}).get(key)) and int(value) > 0}
            snapshot[f"current_{key}"] = next(iter(values)) if len(values) == 1 else None
            if len(values) > 1:
                snapshot["gd_lookup_error"] = "Providers disagree on uploader identity; CP association withheld"
        if snapshot["gd_lookup_error"]:
            snapshot["current_uploader_account_id"] = None
        if missing:
            snapshot["gd_lookup_error"] = "Providers disagree on current existence"
        ratings = {row.get("audit_metadata", {}).get("rated") for row in existing} - {None}
        if len(ratings) > 1:
            snapshot["current_rated"] = None
            snapshot["gd_lookup_error"] = "Providers disagree on current rating"
    if not success:
        snapshot["gd_lookup_error"] = "; ".join(f"{name}: {row.get('error', 'missing/uncertain')}" for name, row in results.items())[:500] or "No enabled providers"
    return snapshot
