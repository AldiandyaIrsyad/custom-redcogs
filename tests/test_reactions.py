import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord


def event(**overrides):
    value = dict(organizer_id=1, player_limit=2, game_title="Test session",
                 description=None, start_timestamp=int(time.time()) + 900,
                 channel_id=10, attendees=[1], last_shared_timestamp=0, tags=[])
    value.update(overrides)
    return value


async def prepare(cog, data):
    users = {}
    for uid in (1, 2, 3):
        users[uid] = SimpleNamespace(id=uid, bot=False, mention=f"<@{uid}>", send=AsyncMock())
    message = SimpleNamespace(id=100, edit=AsyncMock(), remove_reaction=AsyncMock(),
                              add_reaction=AsyncMock(), jump_url="https://discord.com/channels/5/10/100")
    channel = SimpleNamespace(id=10, fetch_message=AsyncMock(return_value=message), send=AsyncMock())
    message.channel = channel
    share = Mock(spec=discord.TextChannel)
    share.id = 20
    share.mention = "<#20>"
    share.send = AsyncMock()
    guild = SimpleNamespace(id=5, get_member=lambda uid: users.get(uid),
                            fetch_member=AsyncMock(side_effect=lambda uid: users[uid]),
                            get_channel=lambda cid: share if cid == 20 else channel,
                            get_channel_or_thread=lambda cid: channel)
    message.guild = guild
    cog.bot.get_guild = lambda _: guild
    cog.bot.get_channel = lambda _: channel
    cog.bot.fetch_channel = AsyncMock(return_value=channel)
    cog.bot.user = SimpleNamespace(id=99)
    await cog.config.guild(guild).scheduled_events.set({"100": data})
    await cog.config.guild(guild).share_channel_id.set(20)
    return guild, channel, message, users, share


def payload(uid=2, emoji="✅"):
    return SimpleNamespace(guild_id=5, channel_id=10, message_id=100,
                           user_id=uid, emoji=emoji, member=None)


async def test_concurrent_join_never_overfills(cog):
    guild, _, message, _, _ = await prepare(cog, event())
    await asyncio.gather(*(cog._handle_reaction(payload(uid), "add") for uid in (2, 3)))
    stored = (await cog.config.guild(guild).scheduled_events())["100"]
    assert len(stored["attendees"]) == 2
    assert stored["attendees"][0] == 1
    assert message.edit.await_count >= 1


async def test_duplicate_join_and_leave_are_idempotent(cog):
    guild, _, _, _, _ = await prepare(cog, event())
    for _ in range(2):
        await cog._handle_reaction(payload(), "add")
    assert (await cog.config.guild(guild).scheduled_events())["100"]["attendees"] == [1, 2]
    for _ in range(2):
        await cog._handle_reaction(payload(), "remove")
    assert (await cog.config.guild(guild).scheduled_events())["100"]["attendees"] == [1]


async def test_irrelevant_reaction_does_no_http(cog):
    guild, channel, _, _, _ = await prepare(cog, event())
    await cog._handle_reaction(payload(emoji="🦆"), "add")
    guild.fetch_member.assert_not_awaited()
    channel.fetch_message.assert_not_awaited()
    cog.bot.fetch_channel.assert_not_awaited()


async def test_started_event_cannot_join_or_remind(cog):
    guild, channel, _, users, _ = await prepare(cog, event(start_timestamp=int(time.time()) - 10))
    await cog._handle_reaction(payload(), "add")
    await cog._handle_reaction(payload(1, "❗"), "add")
    assert (await cog.config.guild(guild).scheduled_events())["100"]["attendees"] == [1]
    channel.send.assert_not_awaited()
    users[2].send.assert_not_awaited()


async def test_manual_share_completes_without_nested_config_deadlock(cog):
    guild, _, _, _, share = await prepare(cog, event())
    await asyncio.wait_for(cog._handle_reaction(payload(1, "📢"), "add"), timeout=2)
    share.send.assert_awaited_once()
    stored = (await cog.config.guild(guild).scheduled_events())["100"]
    assert stored["last_shared_timestamp"] > 0


async def test_cancelled_event_is_closed(cog):
    guild, _, _, _, share = await prepare(cog, event(status="cancelled"))
    await cog._handle_reaction(payload(), "add")
    await cog._handle_reaction(payload(1, "📢"), "add")
    assert (await cog.config.guild(guild).scheduled_events())["100"]["attendees"] == [1]
    share.send.assert_not_awaited()


async def test_reminder_cooldown_survives_repeated_reactions(cog):
    guild, channel, _, users, _ = await prepare(cog, event(attendees=[1, 2]))
    await cog._handle_reaction(payload(1, "❗"), "add")
    await cog._handle_reaction(payload(1, "❗"), "add")
    users[2].send.assert_awaited_once()
    channel.send.assert_awaited_once()
    assert (await cog.config.guild(guild).scheduled_events())["100"]["last_reminder_timestamp"] > 0


async def test_concurrent_reminder_reactions_send_only_one_reminder(cog):
    guild, channel, _, users, _ = await prepare(
        cog, event(attendees=[1, 2], start_timestamp=int(time.time()) + 60)
    )

    await asyncio.gather(
        cog._handle_reaction(payload(1, "❗"), "add"),
        cog._handle_reaction(payload(1, "❗"), "add"),
    )

    users[2].send.assert_awaited_once()
    channel.send.assert_awaited_once()
    assert (await cog.config.guild(guild).scheduled_events())["100"][
        "last_reminder_timestamp"
    ] > 0


async def test_share_attribution_is_organizer_and_cooldown_is_persisted(cog):
    _, _, _, _, share = await prepare(cog, event())
    await cog._handle_reaction(payload(2, "📢"), "add")
    await cog._handle_reaction(payload(3, "📢"), "add")
    share.send.assert_awaited_once()
    embed = share.send.await_args.kwargs["embed"]
    assert "**Organizer**: <@1>" in embed.description


async def test_share_suppresses_mentions_from_event_content(cog):
    _, _, _, _, share = await prepare(
        cog, event(game_title="@everyone <@&123>", description="@everyone")
    )

    await cog._handle_reaction(payload(2, "📢"), "add")

    allowed_mentions = share.send.await_args.kwargs["allowed_mentions"]
    assert allowed_mentions.everyone is False
    assert allowed_mentions.roles is False
    assert allowed_mentions.users is False
    assert allowed_mentions.replied_user is False


async def test_concurrent_share_reactions_consume_only_one_cooldown(cog):
    guild, _, _, _, share = await prepare(cog, event())

    await asyncio.gather(
        cog._handle_reaction(payload(2, "📢"), "add"),
        cog._handle_reaction(payload(3, "📢"), "add"),
    )

    share.send.assert_awaited_once()
    assert (await cog.config.guild(guild).scheduled_events())["100"][
        "last_shared_timestamp"
    ] > 0


async def test_legacy_reminder_timestamp_still_enforces_cooldown(cog):
    guild, channel, _, users, _ = await prepare(
        cog,
        event(
            attendees=[1, 2],
            start_timestamp=int(time.time()) + 60,
            last_reminded_timestamp=int(time.time()),
        ),
    )

    await cog._handle_reaction(payload(1, "❗"), "add")

    users[2].send.assert_not_awaited()
    channel.send.assert_not_awaited()
    assert "last_reminded_timestamp" in (await cog.config.guild(guild).scheduled_events())["100"]


async def test_raw_message_delete_prunes_only_matching_event(cog):
    guild = SimpleNamespace(id=5)
    await cog.config.guild(guild).scheduled_events.set(
        {
            "100": event(),
            "200": event(game_title="Keep me"),
        }
    )
    cog.bot.get_guild = lambda guild_id: guild

    await cog.on_raw_message_delete(SimpleNamespace(guild_id=5, message_id=100))

    assert set((await cog.config.guild(guild).scheduled_events())) == {"200"}


async def test_raw_bulk_message_delete_prunes_selected_events(cog):
    guild = SimpleNamespace(id=5)
    await cog.config.guild(guild).scheduled_events.set(
        {
            "100": event(),
            "200": event(game_title="Keep me"),
            "300": event(game_title="Also remove"),
        }
    )
    cog.bot.get_guild = lambda guild_id: guild

    await cog.on_raw_bulk_message_delete(
        SimpleNamespace(guild_id=5, message_ids={100, 300})
    )

    assert set((await cog.config.guild(guild).scheduled_events())) == {"200"}


async def test_raw_thread_delete_prunes_only_events_in_deleted_post(cog):
    guild = SimpleNamespace(id=5)
    await cog.config.guild(guild).scheduled_events.set(
        {"100": event(channel_id=10), "200": event(channel_id=20)}
    )
    cog.bot.get_guild = lambda guild_id: guild

    await cog.on_raw_thread_delete(SimpleNamespace(guild_id=5, thread_id=10))

    assert set(await cog.config.guild(guild).scheduled_events()) == {"200"}


async def test_reaction_for_deleted_message_prunes_legacy_storage(cog):
    guild, channel, _, _, _ = await prepare(cog, event())
    channel.fetch_message.side_effect = discord.NotFound(Mock(), "gone")

    await cog._handle_reaction(payload(), "add")

    assert await cog.config.guild(guild).scheduled_events() == {}


def test_legacy_large_event_embeds_fit_discord_limits(cog):
    data = event(game_title="x" * 1000, description="y" * 5000,
                 attendees=list(range(100000000000000000, 100000000000000200)))
    embed = cog._build_embed(data)
    assert len(embed.title) <= 256
    assert len(embed) <= 6000
    assert all(len(field.value) <= 1024 for field in embed.fields)
