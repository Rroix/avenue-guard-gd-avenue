import ast
import inspect
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.Commands import AdminDashboardView, CommandsCog
from cogs.RequestLevels import RequestLevelsCog, ScheduledOpeningsView
from main import create_bot


DEFERRED_COMMAND_METHODS = (
    "bot_health",
    "bot_config_check",
    "bot_doctor",
    "bot_dashboard",
    "bot_impact",
    "bot_backup",
    "bot_restore",
    "bot_storage",
    "bot_release",
    "bot_releases",
    "tracking_top",
    "tracking_reset",
    "tracking_me",
    "tracking_force_dm",
    "tracking_disable_reward",
    "tracking_enable_reward",
    "ticket_close",
    "ticket_status",
    "ticket_transcripts",
    "forum_required_word",
    "requests_pending",
    "requests_history",
    "requests_repair",
    "server_icon_status",
    "server_icon_mode",
    "server_icon_add",
    "server_icon_replace",
    "server_icon_remove",
    "server_icon_set",
    "server_icon_next",
    "_resync",
    "_restart",
)


def _leaf_command_data(command_data, prefix=""):
    name = f"{prefix} {command_data['name']}".strip()
    subcommands = [item for item in command_data.get("options", []) if item.get("type") in {1, 2}]
    if not subcommands:
        return [(name, command_data)]
    leaves = []
    for child in subcommands:
        leaves.extend(_leaf_command_data(child, name))
    return leaves


def _first_non_response_await(function):
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    awaits = sorted((node for node in ast.walk(tree) if isinstance(node, ast.Await)), key=lambda node: node.lineno)
    for node in awaits:
        call = ast.unparse(node.value)
        if call.startswith("ctx.respond("):
            continue
        return call
    return ""


def test_all_registered_slash_commands_serialize_with_descriptions(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_LOCAL_DATABASE_FALLBACK", "1")
    monkeypatch.setenv("AVENUE_GUARD_DB_PATH", str(tmp_path / "commands.db"))
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("LIBSQL_AUTH_TOKEN", raising=False)

    bot = create_bot()
    leaves = []
    for root in bot.pending_application_commands:
        leaves.extend(_leaf_command_data(root.to_dict()))

    names = {name for name, _ in leaves}
    assert len(bot.pending_application_commands) == 17
    assert len(names) == 41
    assert {
        "bot release",
        "bot releases",
        "tracking disable_reward",
        "tracking enable_reward",
        "open-requests",
        "edit-request",
    } <= names

    for name, data in leaves:
        description = str(data.get("description") or "")
        assert description, name
        assert len(description) <= 100, name
        assert not description.endswith("."), name
        for option in data.get("options", []):
            if option.get("type") not in {1, 2}:
                assert str(option.get("description") or ""), f"{name} {option.get('name')}"


def test_every_slow_slash_command_declares_early_defer_contract():
    for name in DEFERRED_COMMAND_METHODS:
        first_await = _first_non_response_await(getattr(CommandsCog, name))
        assert first_await.startswith("self._defer(ctx"), f"{name}: {first_await}"

    for name in ("refresh_request_button", "open_requests", "pending_openings", "close_requests", "requests_are"):
        first_await = _first_non_response_await(getattr(RequestLevelsCog, name))
        assert first_await == "self._defer_command(ctx)", f"{name}: {first_await}"

    edit_source = inspect.getsource(RequestLevelsCog.edit_request)
    assert "_editable_user_submission_for_modal" in edit_source
    assert "_defer_command" not in edit_source


def test_slash_command_views_acknowledge_before_database_work():
    dashboard_source = inspect.getsource(AdminDashboardView._show)
    assert dashboard_source.index("await interaction.response.defer()") < dashboard_source.index("_admin_dashboard_embed")

    rps_source = inspect.getsource(CommandsCog._rps)
    assert rps_source.index("await interaction.response.defer()") < rps_source.index("_rps_update_streak")

    for name in ("_refresh", "_delete", "_open_now"):
        source = inspect.getsource(getattr(ScheduledOpeningsView, name))
        assert source.index("await interaction.response.defer()") < source.index("await self.cog.")

    edit_source = inspect.getsource(ScheduledOpeningsView._edit)
    assert "local_only=True" in edit_source


class _ResponseState:
    def __init__(self):
        self.done = False

    def is_done(self):
        return self.done


class _Context:
    def __init__(self, events):
        self.events = events
        self.guild = SimpleNamespace(id=123)
        self.user = SimpleNamespace(id=456)
        self.interaction = SimpleNamespace(response=_ResponseState())

    async def defer(self, *, ephemeral):
        self.events.append(("defer", ephemeral))
        self.interaction.response.done = True

    async def respond(self, content=None, **kwargs):
        self.events.append(("respond", content, kwargs))


class _Config:
    def get_int_list(self, *path):
        return [999] if path == ("roles", "admin_owner_role_ids") else []


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ("tracking_disable_reward", "tracking_enable_reward"))
async def test_reward_toggle_defers_before_database_and_log_work(method_name):
    events = []

    class Tracking:
        async def disable_weekly_reward_for_current_week(self, guild, user_id):
            assert events[0] == ("defer", True)
            events.append(("database", "disable"))
            return "2026-07-12T00:00:00+02:00"

        async def enable_weekly_reward_for_current_week(self, guild, user_id):
            assert events[0] == ("defer", True)
            events.append(("database", "enable"))
            return "2026-07-12T00:00:00+02:00", True

    tracking = Tracking()
    bot = SimpleNamespace(config=_Config(), get_cog=lambda name: tracking if name == "TrackingCog" else None)
    cog = object.__new__(CommandsCog)
    cog.bot = bot
    cog.allowed_guild_id = 123

    member = SimpleNamespace(
        roles=[SimpleNamespace(id=999)],
        guild_permissions=SimpleNamespace(administrator=False),
    )

    async def resolve_member(guild, user):
        events.append(("permission", user.id))
        return member

    async def log_admin_action(guild, user_id, action, detail=""):
        events.append(("log", action))

    cog._resolve_member = resolve_member
    cog._log_admin_action = log_admin_action
    ctx = _Context(events)

    await getattr(cog, method_name)(ctx)

    assert events[0] == ("defer", True)
    assert any(event[0] == "database" for event in events)
    assert events[-1][0] == "respond"
    assert "Weekly request reward" in events[-1][1]


@pytest.mark.asyncio
async def test_ticket_status_disables_an_existing_inactivity_prompt():
    class Config:
        def get_int(self, *path, default=0):
            return 123 if path == ("roles", "MOD_ROLE_ID") else default

        def get(self, *path, default=None):
            if path == ("permissions", "manage_guild_counts_as_mod"):
                return True
            return default

    row = {
        "ticket_id": 3,
        "creator_id": 42,
        "status": "closing_prompted",
        "closing_prompt_message_id": 1234,
    }
    db = SimpleNamespace(
        fetchone=AsyncMock(return_value=row),
        execute=AsyncMock(),
    )
    opening_status = AsyncMock()
    help_cog = SimpleNamespace(update_ticket_opening_status=opening_status)
    bot = SimpleNamespace(
        config=Config(),
        db=db,
        get_cog=lambda name: help_cog if name == "HelpCog" else None,
    )
    cog = object.__new__(CommandsCog)
    cog.bot = bot
    cog.allowed_guild_id = 717
    cog._resolve_member = AsyncMock(
        return_value=SimpleNamespace(
            roles=[SimpleNamespace(id=123)],
            guild_permissions=SimpleNamespace(manage_guild=False),
        )
    )
    cog._log_admin_action = AsyncMock()

    prompt = SimpleNamespace(edit=AsyncMock())

    class Context:
        def __init__(self):
            self.guild = SimpleNamespace(id=717)
            self.user = SimpleNamespace(id=77, mention="<@77>")
            self.channel_id = 999
            self.channel = SimpleNamespace(fetch_message=AsyncMock(return_value=prompt))
            self.interaction = SimpleNamespace(response=_ResponseState())
            self.responses = []

        async def defer(self, *, ephemeral):
            self.interaction.response.done = True

        async def respond(self, content=None, **kwargs):
            self.responses.append((content, kwargs))

    ctx = Context()
    await cog.ticket_status(ctx, "waiting_user")

    sql, params = db.execute.await_args.args
    assert "closing_prompt_message_id=NULL" in sql
    assert params[-1] == ctx.channel_id
    assert prompt.edit.await_args.kwargs["view"] is None
    opening_status.assert_awaited_once_with(ctx.guild, ctx.channel_id, "waiting_user")
