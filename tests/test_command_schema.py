import main


def _option(command: dict, name: str) -> dict:
    return next(option for option in command.get("options", []) if option["name"] == name)


def _subcommand(group: dict, name: str) -> dict:
    return next(option for option in group.get("options", []) if option["name"] == name)


def _walk_descriptions(command: dict):
    yield str(command.get("description") or "")
    for option in command.get("options", []):
        yield from _walk_descriptions(option)


def test_slash_command_schema_stays_constrained_and_readable(monkeypatch):
    monkeypatch.setenv("ALLOW_LOCAL_DATABASE_FALLBACK", "1")
    loop = main._prepare_fresh_event_loop()
    bot = None
    try:
        bot = main.create_bot()
        commands = {
            command["name"]: command
            for command in (item.to_dict() for item in bot.pending_application_commands)
        }

        assert len(commands) == 17
        assert all(
            description
            and len(description) <= 100
            and not description.endswith(".")
            and "only)" not in description.casefold()
            for command in commands.values()
            for description in _walk_descriptions(command)
        )

        open_requests = commands["open-requests"]
        assert len(_option(open_requests, "type")["choices"]) == 8
        assert _option(open_requests, "number")["min_value"] == 0
        assert _option(open_requests, "number")["max_value"] == 10000
        assert _option(open_requests, "day")["max_value"] == 31

        ticket_status = _subcommand(commands["ticket"], "status")
        assert {
            choice["value"]
            for choice in _option(ticket_status, "status")["choices"]
        } == {"waiting_user", "waiting_staff", "resolved"}

        icon_mode = _subcommand(commands["server_icon"], "mode")
        assert {
            choice["value"]
            for choice in _option(icon_mode, "mode")["choices"]
        } == {"random", "linear", "disabled"}
    finally:
        if bot is not None:
            loop.run_until_complete(bot.close())
        main._close_event_loop(loop)
