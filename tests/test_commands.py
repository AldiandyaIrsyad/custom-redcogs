import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from schedule import Schedule
from schedule.commands import ScheduleCommands, normalize_message_id


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
    assert ScheduleCommands._validate_event_details(capacity, title, description)[2]


def test_slash_event_identifiers_use_strings():
    for name in ("schedulereschedule", "schedulecancel", "schedulefinish"):
        command = next(c for c in Schedule.__cog_commands__ if c.name == name)
        parameter = next(p for p in command.app_command.parameters if p.name == "message_id")
        assert parameter.type is discord.AppCommandOptionType.string

