import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from lfg import LFG
from lfg.commands import LFGCommands, normalize_message_id


def event(**overrides):
    value = {
        "organizer_id": 1,
        "player_limit": 2,
        "game_title": "Test session",
        "start_timestamp": 9999999999,
        "channel_id": 10,
        "attendees": [1],
    }
    value.update(overrides)
    return value


async def test_status_change_requires_organizer_or_admin(cog):
    cog._update_event_message = AsyncMock(return_value=True)
    guild = SimpleNamespace(id=5)
    author = SimpleNamespace(id=2, guild_permissions=SimpleNamespace(manage_guild=False))
    ctx = SimpleNamespace(guild=guild, author=author)
    await cog.config.guild(guild).scheduled_events.set({"100": {
        "organizer_id": 1, "attendees": [1], "start_timestamp": 9999999999,
        "channel_id": 10, "player_limit": 2, "game_title": "Test",
    }})
    _, error, _ = await cog._change_event_status(ctx, 100, "cancelled")
    assert error
    assert "status" not in (await cog.config.guild(guild).scheduled_events())["100"]
    ctx.author.id = 1
    result, error, _ = await cog._change_event_status(ctx, 100, "cancelled")
    assert error is None
    assert result["status"] == "cancelled"
    _, error, _ = await cog._change_event_status(ctx, 100, "finished")
    assert error


@pytest.mark.parametrize(
    "value",
    [
        "123456789",
        " https://discord.com/channels/5/10/123456789/ ",
        "https://discord.com/channels/5/10/123456789?foo=bar",
    ],
)
def test_message_id_parser_accepts_decimal_ids_and_discord_urls(value):
    assert normalize_message_id(value) == 123456789


@pytest.mark.parametrize("value", [None, "", "0", "-1", "not-a-message", "https://discord.com/channels/5/10/not-a-message"])
def test_message_id_parser_rejects_invalid_ids(value):
    assert normalize_message_id(value) is None


async def test_concurrent_lifecycle_changes_allow_only_one_transition(cog):
    cog._update_event_message = AsyncMock(return_value=True)
    guild = SimpleNamespace(id=5)
    canceler = SimpleNamespace(
        id=1, guild_permissions=SimpleNamespace(manage_guild=False)
    )
    finisher = SimpleNamespace(
        id=2, guild_permissions=SimpleNamespace(manage_guild=True)
    )
    await cog.config.guild(guild).scheduled_events.set(
        {
            "100": {
                "organizer_id": 1,
                "attendees": [1],
                "start_timestamp": 9999999999,
                "channel_id": 10,
                "player_limit": 2,
                "game_title": "Test",
            }
        }
    )

    cancel_result, finish_result = await asyncio.gather(
        cog._change_event_status(
            SimpleNamespace(guild=guild, author=canceler), 100, "cancelled"
        ),
        cog._change_event_status(
            SimpleNamespace(guild=guild, author=finisher), 100, "finished"
        ),
    )

    successful = [result for result, error, _ in (cancel_result, finish_result) if error is None]
    rejected = [error for result, error, _ in (cancel_result, finish_result) if error is not None]
    assert len(successful) == 1
    assert len(rejected) == 1
    stored = (await cog.config.guild(guild).scheduled_events())["100"]
    assert stored["status"] in {"cancelled", "finished"}


async def test_reschedule_handles_event_removed_after_initial_authorization(cog):
    guild = SimpleNamespace(id=5)
    author = SimpleNamespace(
        id=1,
        send=AsyncMock(),
        guild_permissions=SimpleNamespace(manage_guild=False),
    )
    ctx = SimpleNamespace(
        guild=guild,
        author=author,
        interaction=None,
        send=AsyncMock(),
    )
    authorized_event = event(timezone="Asia/Jakarta")
    cog._load_authorized_event = AsyncMock(return_value=(authorized_event, None))
    await cog.config.guild(guild).scheduled_events.set({})

    await cog.reschedule.callback(cog, ctx, "100", "in 10 minutes")

    author.send.assert_awaited_once()
    assert "couldn't find" in author.send.await_args.args[0]
    ctx.send.assert_not_awaited()
    assert await cog.config.guild(guild).scheduled_events() == {}


async def test_private_actions_reject_unauthorized_and_cooldown_requests(cog):
    import time

    guild = SimpleNamespace(id=5)
    author = SimpleNamespace(id=2, guild_permissions=SimpleNamespace(manage_guild=False))
    ctx = SimpleNamespace(guild=guild, author=author)
    await cog.config.guild(guild).scheduled_events.set(
        {"100": event(last_shared_timestamp=int(time.time()))}
    )
    await cog.config.guild(guild).share_channel_id.set(20)
    cog._fetch_event_message = AsyncMock(return_value=SimpleNamespace(id=100))
    cog._share_schedule = AsyncMock()

    _, error = await cog._run_organizer_action(ctx, 100, "share")
    assert "organizer" in error
    author.id = 1
    _, error = await cog._run_organizer_action(ctx, 100, "share")
    assert "recently" in error
    cog._share_schedule.assert_not_awaited()


async def test_manual_share_reports_send_failure_and_success(cog):
    guild = SimpleNamespace(id=5)
    author = SimpleNamespace(id=1, guild_permissions=SimpleNamespace(manage_guild=False))
    ctx = SimpleNamespace(guild=guild, author=author)
    await cog.config.guild(guild).scheduled_events.set({"100": event()})
    await cog.config.guild(guild).share_channel_id.set(20)
    cog._fetch_event_message = AsyncMock(return_value=SimpleNamespace(id=100))
    cog._share_schedule = AsyncMock(side_effect=[False, True])

    _, error = await cog._run_organizer_action(ctx, 100, "share")
    assert "couldn't post" in error
    result, error = await cog._run_organizer_action(ctx, 100, "share")
    assert error is None
    assert result is not None
    assert all(
        call.kwargs["remove_reaction_after_action"] is False
        for call in cog._share_schedule.await_args_list
    )


@pytest.mark.parametrize(
    ("command_name", "arguments", "confirmation"),
    [
        ("reschedule", ("100", "in 10 minutes"), "rescheduled"),
        ("cancel", ("100",), "cancelled"),
        ("finish", ("100",), "marked finished"),
    ],
)
async def test_prefix_lifecycle_success_is_private(cog, monkeypatch, command_name, arguments, confirmation):
    if command_name == "reschedule":
        import lfg.commands as lfg_commands

        monkeypatch.setattr(
            lfg_commands,
            "parse_session_time",
            lambda *args, **kwargs: (2000000000, None),
        )

    guild = SimpleNamespace(id=5)
    author = SimpleNamespace(
        id=1,
        send=AsyncMock(),
        guild_permissions=SimpleNamespace(manage_guild=False),
    )
    ctx = SimpleNamespace(
        guild=guild,
        author=author,
        interaction=None,
        send=AsyncMock(),
    )
    await cog.config.guild(guild).scheduled_events.set(
        {"100": event(timezone="Asia/Jakarta")}
    )
    cog._update_event_message = AsyncMock(return_value=True)

    await getattr(cog, command_name).callback(cog, ctx, *arguments)

    author.send.assert_awaited_once()
    assert confirmation in author.send.await_args.args[0]
    ctx.send.assert_not_awaited()


async def test_prefix_lifecycle_uses_public_fallback_when_dm_fails(cog):
    guild = SimpleNamespace(id=5)
    author = SimpleNamespace(
        id=1,
        send=AsyncMock(side_effect=discord.Forbidden(Mock(), "forbidden")),
        guild_permissions=SimpleNamespace(manage_guild=False),
    )
    ctx = SimpleNamespace(
        guild=guild,
        author=author,
        interaction=None,
        send=AsyncMock(),
    )
    await cog.config.guild(guild).scheduled_events.set(
        {"100": event(timezone="Asia/Jakarta")}
    )
    cog._update_event_message = AsyncMock(return_value=True)

    await cog.cancel.callback(cog, ctx, "100")

    author.send.assert_awaited_once()
    ctx.send.assert_awaited_once()
    assert ctx.send.await_args.args[0] == (
        "The session was cancelled, but I couldn't DM the private confirmation."
    )
    allowed_mentions = ctx.send.await_args.kwargs["allowed_mentions"]
    assert allowed_mentions.everyone is False
    assert allowed_mentions.roles is False
    assert allowed_mentions.users is False
    assert allowed_mentions.replied_user is False


@pytest.mark.parametrize(
    ("command_name", "arguments"),
    [
        ("reschedule", ("invalid", "in 10 minutes")),
        ("cancel", ("invalid",)),
        ("finish", ("invalid",)),
    ],
)
async def test_prefix_lifecycle_error_is_private(cog, command_name, arguments):
    guild = SimpleNamespace(id=5)
    author = SimpleNamespace(
        id=1,
        send=AsyncMock(),
        guild_permissions=SimpleNamespace(manage_guild=False),
    )
    ctx = SimpleNamespace(
        guild=guild,
        author=author,
        interaction=None,
        send=AsyncMock(),
    )

    await getattr(cog, command_name).callback(cog, ctx, *arguments)

    author.send.assert_awaited_once()
    assert "valid" in author.send.await_args.args[0]
    ctx.send.assert_not_awaited()


@pytest.mark.parametrize(
    ("command_name", "arguments", "confirmation"),
    [
        ("reschedule", ("100", "in 10 minutes"), "rescheduled"),
        ("cancel", ("100",), "cancelled"),
        ("finish", ("100",), "marked finished"),
    ],
)
async def test_slash_lifecycle_success_is_ephemeral(cog, monkeypatch, command_name, arguments, confirmation):
    if command_name == "reschedule":
        import lfg.commands as lfg_commands

        monkeypatch.setattr(
            lfg_commands,
            "parse_session_time",
            lambda *args, **kwargs: (2000000000, None),
        )

    guild = SimpleNamespace(id=5)
    author = SimpleNamespace(
        id=1,
        send=AsyncMock(),
        guild_permissions=SimpleNamespace(manage_guild=False),
    )
    ctx = SimpleNamespace(
        guild=guild,
        author=author,
        interaction=object(),
        defer=AsyncMock(),
        send=AsyncMock(),
    )
    await cog.config.guild(guild).scheduled_events.set(
        {"100": event(timezone="Asia/Jakarta")}
    )
    cog._update_event_message = AsyncMock(return_value=True)

    await getattr(cog, command_name).callback(cog, ctx, *arguments)

    ctx.defer.assert_awaited_once_with(ephemeral=True)
    ctx.send.assert_awaited_once()
    assert confirmation in ctx.send.await_args.args[0]
    assert ctx.send.await_args.kwargs["ephemeral"] is True
    author.send.assert_not_awaited()


@pytest.mark.parametrize(
    ("command_name", "arguments"),
    [
        ("reschedule", ("invalid", "in 10 minutes")),
        ("cancel", ("invalid",)),
        ("finish", ("invalid",)),
    ],
)
async def test_slash_lifecycle_error_is_ephemeral(cog, command_name, arguments):
    guild = SimpleNamespace(id=5)
    author = SimpleNamespace(
        id=1,
        send=AsyncMock(),
        guild_permissions=SimpleNamespace(manage_guild=False),
    )
    ctx = SimpleNamespace(
        guild=guild,
        author=author,
        interaction=object(),
        send=AsyncMock(),
    )

    await getattr(cog, command_name).callback(cog, ctx, *arguments)

    ctx.send.assert_awaited_once()
    assert "valid" in ctx.send.await_args.args[0]
    assert ctx.send.await_args.kwargs["ephemeral"] is True
    author.send.assert_not_awaited()


async def test_private_reminder_rejects_outside_window(cog):
    guild = SimpleNamespace(id=5)
    author = SimpleNamespace(id=1, guild_permissions=SimpleNamespace(manage_guild=False))
    ctx = SimpleNamespace(guild=guild, author=author)
    await cog.config.guild(guild).scheduled_events.set({"100": event()})
    cog._fetch_event_message = AsyncMock(return_value=SimpleNamespace(id=100))
    cog._handle_reminder = AsyncMock()

    _, error = await cog._run_organizer_action(ctx, 100, "remind")

    assert "30 minutes" in error
    cog._handle_reminder.assert_not_awaited()


def test_public_embed_does_not_advertise_organizer_controls(cog):
    embed = cog._build_embed(event())

    assert "❗" not in embed.footer.text
    assert "📢" not in embed.footer.text
    assert embed.footer.text == "✅ Join/Leave"


def test_thread_permission_preflight_uses_send_messages_in_threads(cog):
    permissions = SimpleNamespace(
        view_channel=True,
        read_message_history=True,
        send_messages=True,
        send_messages_in_threads=False,
        embed_links=True,
        add_reactions=True,
        manage_messages=True,
    )
    ctx = SimpleNamespace(
        channel=SimpleNamespace(permissions_for=lambda member: permissions),
        guild=SimpleNamespace(me=object()),
    )

    assert LFGCommands._missing_bot_permissions(ctx) == [
        "Send Messages in Threads"
    ]

    permissions.send_messages = False
    permissions.send_messages_in_threads = True
    assert LFGCommands._missing_bot_permissions(ctx) == []


async def test_public_event_edit_suppresses_mentions(cog):
    message = SimpleNamespace(edit=AsyncMock(), guild=None)

    await cog._update_embed(message, event())

    allowed_mentions = message.edit.await_args.kwargs["allowed_mentions"]
    assert allowed_mentions.everyone is False
    assert allowed_mentions.roles is False
    assert allowed_mentions.users is False
    assert allowed_mentions.replied_user is False


async def test_prefix_play_organizer_controls_are_sent_by_dm(cog, monkeypatch):
    import lfg.commands as lfg_commands

    monkeypatch.setattr(
        lfg_commands,
        "parse_session_time",
        lambda *args, **kwargs: (2000000000, None),
    )
    await cog.config.guild(SimpleNamespace(id=5)).target_forum_id.set(20)

    # A Mock with a Thread spec satisfies the runtime channel check while
    # retaining the small surface this command uses.
    channel = Mock(spec=discord.Thread)
    channel.id = 10
    channel.parent_id = 20
    channel.name = "Test thread"
    channel.applied_tags = []
    channel.send = AsyncMock(
        return_value=SimpleNamespace(
            id=100,
            jump_url="https://discord.com/channels/5/10/100",
            add_reaction=AsyncMock(),
            delete=AsyncMock(),
        )
    )
    guild = SimpleNamespace(id=5, me=None)
    author = SimpleNamespace(
        id=1,
        mention="<@1>",
        send=AsyncMock(),
        guild_permissions=SimpleNamespace(manage_guild=False),
    )
    author.guild = guild
    ctx = SimpleNamespace(
        guild=guild,
        channel=channel,
        author=author,
        bot=cog.bot,
        interaction=None,
        clean_prefix="!",
        send=AsyncMock(),
    )

    await cog.lfg.callback(cog, ctx, 2, "tomorrow at 8pm")

    allowed_mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert allowed_mentions.everyone is False
    assert allowed_mentions.roles is False
    assert allowed_mentions.users is False
    assert allowed_mentions.replied_user is False
    channel.send.return_value.add_reaction.assert_awaited_once_with("✅")
    author.send.assert_awaited_once()
    assert "lfg remind" in author.send.await_args.args[0]
    assert "lfg share" in author.send.await_args.args[0]
    stored = await cog.config.guild(guild).scheduled_events()
    assert stored["100"]["private_controls"] is True


async def test_slash_play_keeps_organizer_controls_ephemeral(cog, monkeypatch):
    import lfg.commands as lfg_commands

    monkeypatch.setattr(
        lfg_commands,
        "parse_session_time",
        lambda *args, **kwargs: (2000000000, None),
    )
    guild = SimpleNamespace(id=5, me=None)
    await cog.config.guild(guild).target_forum_id.set(20)
    channel = Mock(spec=discord.Thread)
    channel.id = 10
    channel.parent_id = 20
    channel.name = "Test thread"
    channel.applied_tags = []
    channel.send = AsyncMock(
        return_value=SimpleNamespace(
            id=100,
            jump_url="https://discord.com/channels/5/10/100",
            add_reaction=AsyncMock(),
            delete=AsyncMock(),
        )
    )
    author = SimpleNamespace(
        id=1,
        mention="<@1>",
        send=AsyncMock(),
        guild_permissions=SimpleNamespace(manage_guild=False),
    )
    author.guild = guild
    ctx = SimpleNamespace(
        guild=guild,
        channel=channel,
        author=author,
        bot=cog.bot,
        interaction=object(),
        defer=AsyncMock(),
        send=AsyncMock(),
    )

    await cog.lfg.callback(cog, ctx, 2, "tomorrow at 8pm")

    channel.send.return_value.add_reaction.assert_awaited_once_with("✅")
    author.send.assert_not_awaited()
    response = ctx.send.await_args
    assert response.kwargs["ephemeral"] is True
    assert "/lfg remind" in response.args[0]
    assert "/lfg share" in response.args[0]


async def test_legacy_event_without_status_is_authorized_and_can_transition(cog):
    cog._update_event_message = AsyncMock(return_value=True)
    guild = SimpleNamespace(id=5)
    organizer = SimpleNamespace(
        id=1, guild_permissions=SimpleNamespace(manage_guild=False)
    )
    await cog.config.guild(guild).scheduled_events.set(
        {
            "100": {
                "organizer_id": 1,
                "attendees": [1],
                "start_timestamp": 9999999999,
                "channel_id": 10,
                "player_limit": 2,
                "game_title": "Legacy",
            }
        }
    )

    result, error, _ = await cog._change_event_status(
        SimpleNamespace(guild=guild, author=organizer), 100, "finished"
    )

    assert error is None
    assert result["status"] == "finished"


async def test_legacy_string_organizer_id_does_not_lock_out_the_organizer(cog):
    cog._update_event_message = AsyncMock(return_value=True)
    guild = SimpleNamespace(id=5)
    organizer = SimpleNamespace(
        id=1, guild_permissions=SimpleNamespace(manage_guild=False)
    )
    await cog.config.guild(guild).scheduled_events.set(
        {
            "100": {
                "organizer_id": "1",
                "attendees": ["1"],
                "start_timestamp": 9999999999,
                "channel_id": "10",
                "player_limit": 2,
                "game_title": "Legacy string IDs",
            }
        }
    )

    result, error, _ = await cog._change_event_status(
        SimpleNamespace(guild=guild, author=organizer), 100, "cancelled"
    )

    assert error is None
    assert result["status"] == "cancelled"


async def test_upcoming_suppresses_mentions_in_event_titles(cog):
    visible_channel = SimpleNamespace(
        permissions_for=lambda member: SimpleNamespace(view_channel=True)
    )
    guild = SimpleNamespace(
        id=5,
        get_channel_or_thread=lambda channel_id: visible_channel,
        get_channel=lambda channel_id: visible_channel,
    )
    await cog.config.guild(guild).scheduled_events.set(
        {"100": event(game_title="@everyone <@&123>")}
    )
    ctx = SimpleNamespace(
        guild=guild,
        author=SimpleNamespace(id=2),
        interaction=None,
        send=AsyncMock(),
    )

    await cog.upcoming.callback(cog, ctx)

    allowed_mentions = ctx.send.await_args.kwargs["allowed_mentions"]
    assert allowed_mentions.everyone is False
    assert allowed_mentions.roles is False
    assert allowed_mentions.users is False
    assert allowed_mentions.replied_user is False


@pytest.mark.parametrize("capacity,title,description", [(0, None, None), (101, None, None), (2, " ", None), (2, None, "x" * 1025)])
def test_invalid_event_details_are_rejected(capacity, title, description):
    assert LFGCommands._validate_event_details(capacity, title, description)[2]


def test_slash_event_identifiers_use_strings():
    for name in (
        "control",
        "remind",
        "share",
        "reschedule",
        "cancel",
        "finish",
    ):
        command = next(c for c in LFG.__cog_commands__ if c.name == name)
        parameter = next(p for p in command.app_command.parameters if p.name == "message_id")
        assert parameter.type is discord.AppCommandOptionType.string


def test_no_collision_prone_global_command_names():
    """Only lfg-prefixed names may be global, so no common command collides.

    A generic global like `play` collides with music cogs and prevents Red
    from loading the cog, so the global command surface must stay minimal.
    """

    global_names = {c.name for c in LFG.__cog_commands__ if c.parent is None}
    assert global_names == {"lfg", "lfgset", "lfgtimezone"}
    assert "play" not in global_names
    assert "playset" not in global_names
    assert "settimezone" not in global_names


def test_no_schedule_prefixed_commands_remain():
    """The earlier Schedule naming must not leave any command behind."""

    names = [c.qualified_name for c in LFG.__cog_commands__]
    assert not [n for n in names if n.startswith("schedule")]


def test_lfg_group_exposes_expected_subcommands():
    subcommands = {
        c.name for c in LFG.__cog_commands__ if c.parent is not None
        and c.parent.name == "lfg"
    }
    assert subcommands == {
        "start",
        "control",
        "remind",
        "share",
        "reschedule",
        "cancel",
        "finish",
        "upcoming",
        "exp",
        "top",
    }

