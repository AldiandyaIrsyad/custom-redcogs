"""Tests for the session EXP system and role rewards."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest


class FakeRole:
    """A minimal role supporting hierarchy comparison."""

    def __init__(self, role_id, name="Role", position=1):
        self.id = role_id
        self.name = name
        self.position = position
        self.mention = f"<@&{role_id}>"

    def __lt__(self, other):
        return self.position < getattr(other, "position", 0)


def event(**overrides):
    value = {
        "organizer_id": 1,
        "player_limit": 5,
        "game_title": "Test session",
        "start_timestamp": 9999999999,
        "channel_id": 10,
        "attendees": [1],
        "status": "active",
    }
    value.update(overrides)
    return value


def make_guild(members=None, roles=None, top_position=100, manage_roles=True):
    members = members or {}
    roles = roles or {}
    top_role = FakeRole(999, "Bot", position=top_position)
    guild = SimpleNamespace(
        id=5,
        me=SimpleNamespace(
            top_role=top_role,
            guild_permissions=SimpleNamespace(manage_roles=manage_roles),
        ),
        get_member=lambda uid: members.get(uid),
        get_role=lambda rid: roles.get(rid),
    )
    return guild


def make_member(member_id, roles=None):
    member = SimpleNamespace(
        id=member_id,
        roles=list(roles or []),
        display_name=f"User {member_id}",
        add_roles=AsyncMock(),
        remove_roles=AsyncMock(),
    )
    member.guild = None
    return member


async def enable_exp(cog, guild, per_session=1, bonus=0, roles=None, min_attendees=2):
    config = cog.config.guild(guild)
    await config.exp_enabled.set(True)
    await config.exp_per_session.set(per_session)
    await config.exp_organizer_bonus.set(bonus)
    await config.exp_min_attendees.set(min_attendees)
    if roles is not None:
        await config.exp_roles.set(roles)


async def member_data(cog, guild, member_id):
    return await cog.config.member_from_ids(guild.id, member_id).all()


async def test_exp_disabled_awards_nothing(cog):
    guild = make_guild()
    await cog.config.guild(guild).scheduled_events.set({"100": event(attendees=[1, 2])})

    summary = await cog._award_event_exp(guild, 100)

    assert summary["enabled"] is False
    assert summary["awarded"] == 0
    assert (await member_data(cog, guild, 1))["exp"] == 0


async def test_exp_flat_amount_and_organizer_bonus(cog):
    members = {1: make_member(1), 2: make_member(2), 3: make_member(3)}
    guild = make_guild(members=members)
    await enable_exp(cog, guild, per_session=2, bonus=1)
    data = event(attendees=[1, 2, 3], organizer_id=1)
    await cog.config.guild(guild).scheduled_events.set({"100": data})

    summary = await cog._award_event_exp(guild, 100)

    assert summary["awarded"] == 3
    assert (await member_data(cog, guild, 1))["exp"] == 3
    assert (await member_data(cog, guild, 1))["sessions"] == 1
    assert (await member_data(cog, guild, 2))["exp"] == 2
    assert (await member_data(cog, guild, 3))["exp"] == 2


async def test_exp_awards_only_once_per_event(cog):
    members = {1: make_member(1)}
    guild = make_guild(members=members)
    await enable_exp(cog, guild, per_session=5)
    data = event(attendees=[1, 2])
    await cog.config.guild(guild).scheduled_events.set({"100": data})

    first = await cog._award_event_exp(guild, 100)
    second = await cog._award_event_exp(guild, 100)

    assert first["awarded"] == 2
    assert second["awarded"] == 0
    assert (await member_data(cog, guild, 1))["exp"] == 5


async def test_exp_resumes_after_member_write_failure(cog, monkeypatch):
    """A failed member must not make earlier successful members unretryable."""

    members = {1: make_member(1), 2: make_member(2)}
    guild = make_guild(members=members)
    await enable_exp(cog, guild, per_session=2, bonus=1)
    await cog.config.guild(guild).scheduled_events.set(
        {"100": event(attendees=[1, 2], organizer_id=1)}
    )

    original_add_member = cog._exp_add_member
    failed = False

    async def fail_once(guild, member_id, amount, tiers):
        nonlocal failed
        if member_id == 2 and not failed:
            failed = True
            raise RuntimeError("simulated member write failure")
        return await original_add_member(guild, member_id, amount, tiers)

    monkeypatch.setattr(cog, "_exp_add_member", fail_once)
    with pytest.raises(RuntimeError, match="simulated member write failure"):
        await cog._award_event_exp(guild, 100)

    stored = (await cog.config.guild(guild).scheduled_events())["100"]
    assert stored["exp_award_pending"] is True
    assert stored["exp_awarded_members"] == [1]
    assert not stored.get("exp_awarded")
    assert (await member_data(cog, guild, 1))["exp"] == 3
    assert (await member_data(cog, guild, 2))["exp"] == 0

    # A retry resumes from the failed member and does not duplicate member 1.
    monkeypatch.setattr(cog, "_exp_add_member", original_add_member)
    retry = await cog._award_event_exp(guild, 100)

    assert retry["awarded"] == 1
    assert (await member_data(cog, guild, 1))["exp"] == 3
    assert (await member_data(cog, guild, 1))["sessions"] == 1
    assert (await member_data(cog, guild, 2))["exp"] == 2
    stored = (await cog.config.guild(guild).scheduled_events())["100"]
    assert stored["exp_awarded"] is True
    assert stored["exp_award_pending"] is False
    assert stored["exp_awarded_members"] == [1, 2]


async def test_exp_member_ledger_prevents_duplicate_after_progress_write_failure(
    cog, monkeypatch
):
    """A member commit followed by a crash must still be safe to retry."""

    members = {1: make_member(1), 2: make_member(2)}
    guild = make_guild(members=members)
    await enable_exp(cog, guild, per_session=2)
    await cog.config.guild(guild).scheduled_events.set(
        {"100": event(attendees=[1, 2], organizer_id=1)}
    )

    original_add_member = cog._exp_add_member
    failed = False

    async def fail_after_commit(guild, member_id, amount, tiers):
        nonlocal failed
        result = await original_add_member(guild, member_id, amount, tiers)
        if member_id == 1 and not failed:
            failed = True
            raise RuntimeError("simulated progress write failure")
        return result

    monkeypatch.setattr(cog, "_exp_add_member", fail_after_commit)
    with pytest.raises(RuntimeError, match="simulated progress write failure"):
        await cog._award_event_exp(guild, 100)

    assert (await member_data(cog, guild, 1))["exp"] == 2
    assert (await member_data(cog, guild, 1))["sessions"] == 1
    assert (await cog.config.guild(guild).scheduled_events())["100"][
        "exp_awarded_members"
    ] == []

    monkeypatch.setattr(cog, "_exp_add_member", original_add_member)
    retry = await cog._award_event_exp(guild, 100)

    assert retry["awarded"] == 1
    assert (await member_data(cog, guild, 1))["exp"] == 2
    assert (await member_data(cog, guild, 1))["sessions"] == 1
    assert (await member_data(cog, guild, 2))["exp"] == 2


async def test_exp_deduplicates_repeated_attendee_ids(cog):
    members = {1: make_member(1), 2: make_member(2)}
    guild = make_guild(members=members)
    await enable_exp(cog, guild, per_session=3)
    data = event(attendees=[1, 2, 1, 1], organizer_id=1)
    await cog.config.guild(guild).scheduled_events.set({"100": data})

    summary = await cog._award_event_exp(guild, 100)

    assert summary["awarded"] == 2
    assert (await member_data(cog, guild, 1))["exp"] == 3


async def test_exp_grants_highest_tier_and_swaps_lower_role(cog):
    tier_one = FakeRole(11, "Tier 1", position=1)
    tier_two = FakeRole(12, "Tier 2", position=2)
    members = {1: make_member(1, roles=[tier_one])}
    guild = make_guild(members=members, roles={11: tier_one, 12: tier_two})
    await enable_exp(cog, guild, per_session=4, roles={"11": 2, "12": 4})
    data = event(attendees=[1, 2])
    await cog.config.guild(guild).scheduled_events.set({"100": data})

    await cog._award_event_exp(guild, 100)

    members[1].remove_roles.assert_awaited_once_with(
        tier_one, reason="LFG EXP tier update"
    )
    members[1].add_roles.assert_awaited_once_with(
        tier_two, reason="LFG EXP reward"
    )


async def test_exp_role_grant_failure_keeps_lower_role(cog):
    tier_one = FakeRole(11, "Tier 1", position=1)
    tier_two = FakeRole(12, "Tier 2", position=2)
    member = make_member(1, roles=[tier_one])
    member.add_roles.side_effect = RuntimeError("simulated grant failure")
    guild = make_guild(
        members={1: member}, roles={11: tier_one, 12: tier_two}
    )
    await enable_exp(cog, guild, roles={"11": 2, "12": 4})

    warning = await cog._exp_sync_roles(
        guild, 1, 4, cog._exp_sort_tiers({"11": 2, "12": 4})
    )

    assert warning
    member.add_roles.assert_awaited_once_with(tier_two, reason="LFG EXP reward")
    member.remove_roles.assert_not_awaited()


async def test_exp_keeps_logging_warning_but_still_awards_for_ungrantable_role(cog):
    high_role = FakeRole(13, "Too High", position=200)
    members = {1: make_member(1)}
    guild = make_guild(members=members, roles={13: high_role}, top_position=100)
    await enable_exp(cog, guild, per_session=5, roles={"13": 1})
    data = event(attendees=[1, 2])
    await cog.config.guild(guild).scheduled_events.set({"100": data})

    summary = await cog._award_event_exp(guild, 100)

    assert (await member_data(cog, guild, 1))["exp"] == 5
    assert summary["warnings"]
    members[1].add_roles.assert_not_awaited()


async def test_exp_lowest_tier_grants_no_role(cog):
    tier_one = FakeRole(11, "Tier 1", position=1)
    members = {1: make_member(1)}
    guild = make_guild(members=members, roles={11: tier_one})
    await enable_exp(cog, guild, per_session=1, roles={"11": 100})
    data = event(attendees=[1, 2])
    await cog.config.guild(guild).scheduled_events.set({"100": data})

    await cog._award_event_exp(guild, 100)

    assert (await member_data(cog, guild, 1))["exp"] == 1
    members[1].add_roles.assert_not_awaited()
    members[1].remove_roles.assert_not_awaited()


async def test_exp_solo_session_awards_nothing(cog):
    members = {1: make_member(1)}
    guild = make_guild(members=members)
    await enable_exp(cog, guild, per_session=10)
    data = event(attendees=[1])
    await cog.config.guild(guild).scheduled_events.set({"100": data})

    summary = await cog._award_event_exp(guild, 100)

    assert summary["enabled"] is True
    assert summary["eligible"] is False
    assert summary["awarded"] == 0
    assert (await member_data(cog, guild, 1))["exp"] == 0
    # Not claimed, so the event does not look like an already-rewarded session.
    stored = (await cog.config.guild(guild).scheduled_events())["100"]
    assert not stored.get("exp_awarded")


async def test_exp_min_attendees_is_configurable(cog):
    members = {1: make_member(1), 2: make_member(2), 3: make_member(3)}
    guild = make_guild(members=members)
    await enable_exp(cog, guild, per_session=4, min_attendees=3)
    await cog.config.guild(guild).scheduled_events.set(
        {"100": event(attendees=[1, 2])}
    )

    below = await cog._award_event_exp(guild, 100)
    assert below["eligible"] is False
    assert below["awarded"] == 0

    await cog.config.guild(guild).scheduled_events.set(
        {"200": event(attendees=[1, 2, 3])}
    )
    above = await cog._award_event_exp(guild, 200)
    assert above["eligible"] is True
    assert above["awarded"] == 3


async def test_exp_min_attendees_counts_unique_members(cog):
    members = {1: make_member(1), 2: make_member(2)}
    guild = make_guild(members=members)
    await enable_exp(cog, guild, per_session=1, min_attendees=3)
    # Repeated ids must not be counted as separate participants.
    await cog.config.guild(guild).scheduled_events.set(
        {"100": event(attendees=[1, 1, 2])}
    )

    summary = await cog._award_event_exp(guild, 100)

    assert summary["eligible"] is False
    assert summary["awarded"] == 0


def test_tier_sorting_and_highest_selection(cog):
    from lfg.exp import LFGExp

    tiers = LFGExp._exp_sort_tiers({"12": 4, "11": 2, "13": "bad", "x": 3})
    assert tiers == [(2, 11), (4, 12)]

    assert LFGExp._exp_highest_role(0, tiers) is None
    assert LFGExp._exp_highest_role(2, tiers) == 11
    assert LFGExp._exp_highest_role(3, tiers) == 11
    assert LFGExp._exp_highest_role(4, tiers) == 12
    assert LFGExp._exp_next_tier(0, tiers) == (2, 11)
    assert LFGExp._exp_next_tier(3, tiers) == (4, 12)
    assert LFGExp._exp_next_tier(4, tiers) is None


async def test_finish_command_awards_exp(cog, monkeypatch):
    members = {1: make_member(1), 2: make_member(2)}
    guild = make_guild(members=members)
    await enable_exp(cog, guild, per_session=2, bonus=1)
    author = SimpleNamespace(
        id=1, send=AsyncMock(), guild_permissions=SimpleNamespace(manage_guild=False)
    )
    ctx = SimpleNamespace(guild=guild, author=author, interaction=None, send=AsyncMock())
    await cog.config.guild(guild).scheduled_events.set(
        {"100": event(attendees=[1, 2], organizer_id=1)}
    )
    cog._update_event_message = AsyncMock(return_value=True)

    await cog.finish.callback(cog, ctx, "100")

    assert (await member_data(cog, guild, 1))["exp"] == 3
    assert (await member_data(cog, guild, 2))["exp"] == 2
    assert "EXP was awarded to 2 participant(s)" in author.send.await_args.args[0]


async def test_finish_retries_payout_after_status_commit(cog):
    members = {1: make_member(1), 2: make_member(2)}
    guild = make_guild(members=members)
    await enable_exp(cog, guild, per_session=2, bonus=1)
    author = SimpleNamespace(
        id=1, send=AsyncMock(), guild_permissions=SimpleNamespace(manage_guild=False)
    )
    ctx = SimpleNamespace(guild=guild, author=author, interaction=None, send=AsyncMock())
    await cog.config.guild(guild).scheduled_events.set(
        {"100": event(attendees=[1, 2], organizer_id=1)}
    )
    cog._update_event_message = AsyncMock(return_value=True)

    # Simulate a process stopping after the status transaction but before
    # _award_event_exp gets its first opportunity to run.
    _, error, _ = await cog._change_event_status(ctx, 100, "finished")
    assert error is None
    stored = (await cog.config.guild(guild).scheduled_events())["100"]
    assert stored["exp_award_pending"] is True

    # The finish-time award policy survives a later administrative toggle.
    await cog.config.guild(guild).exp_enabled.set(False)
    await cog.config.guild(guild).exp_per_session.set(99)
    await cog.finish.callback(cog, ctx, "100")

    assert (await member_data(cog, guild, 1))["exp"] == 3
    assert (await member_data(cog, guild, 2))["exp"] == 2
    stored = (await cog.config.guild(guild).scheduled_events())["100"]
    assert stored["exp_awarded"] is True
    assert stored["exp_award_pending"] is False
    assert "pending EXP payout was retried" in author.send.await_args.args[0]


async def test_finish_command_explains_solo_session_gets_no_exp(cog):
    members = {1: make_member(1)}
    guild = make_guild(members=members)
    await enable_exp(cog, guild, per_session=5, min_attendees=2)
    author = SimpleNamespace(
        id=1, send=AsyncMock(), guild_permissions=SimpleNamespace(manage_guild=False)
    )
    ctx = SimpleNamespace(guild=guild, author=author, interaction=None, send=AsyncMock())
    await cog.config.guild(guild).scheduled_events.set({"100": event(attendees=[1])})
    cog._update_event_message = AsyncMock(return_value=True)

    await cog.finish.callback(cog, ctx, "100")

    assert (await member_data(cog, guild, 1))["exp"] == 0
    assert "No EXP was awarded" in author.send.await_args.args[0]
    assert "fewer than 2" in author.send.await_args.args[0]


async def test_cancel_command_does_not_award_exp(cog):
    members = {1: make_member(1)}
    guild = make_guild(members=members)
    await enable_exp(cog, guild, per_session=5)
    author = SimpleNamespace(
        id=1, send=AsyncMock(), guild_permissions=SimpleNamespace(manage_guild=False)
    )
    ctx = SimpleNamespace(guild=guild, author=author, interaction=None, send=AsyncMock())
    await cog.config.guild(guild).scheduled_events.set({"100": event(attendees=[1])})
    cog._update_event_message = AsyncMock(return_value=True)

    await cog.cancel.callback(cog, ctx, "100")

    assert (await member_data(cog, guild, 1))["exp"] == 0
    assert (await member_data(cog, guild, 1))["sessions"] == 0


async def test_exp_addrole_and_removerole(cog):
    tier = FakeRole(11, "Tier 1", position=1)
    guild = make_guild(roles={11: tier})
    ctx = SimpleNamespace(guild=guild, send=AsyncMock(), interaction=None)

    await cog.exp_addrole.callback(cog, ctx, tier, 3)

    assert (await cog.config.guild(guild).exp_roles()) == {"11": 3}
    ctx.send.assert_awaited()

    await cog.exp_removerole.callback(cog, ctx, tier)

    assert (await cog.config.guild(guild).exp_roles()) == {}


async def test_exp_addrole_rejects_ungrantable_role(cog):
    high_role = FakeRole(13, "Too High", position=200)
    guild = make_guild(roles={13: high_role}, top_position=100)
    ctx = SimpleNamespace(guild=guild, send=AsyncMock(), interaction=None)

    await cog.exp_addrole.callback(cog, ctx, high_role, 3)

    assert (await cog.config.guild(guild).exp_roles()) == {}
    assert "can't manage" in ctx.send.await_args.args[0]


async def test_exp_show_reports_configuration(cog):
    tier = FakeRole(11, "Tier 1", position=1)
    guild = make_guild(roles={11: tier})
    await enable_exp(cog, guild, per_session=4, bonus=2, roles={"11": 10})
    ctx = SimpleNamespace(guild=guild, send=AsyncMock(), interaction=None)

    await cog.exp_show.callback(cog, ctx)

    embed = ctx.send.await_args.kwargs["embed"]
    assert "enabled" in embed.description
    assert "4" in embed.description
    assert "Minimum participants:** 2" in embed.description
    assert "Tier 1" in embed.description


async def test_exp_minplayers_command_validates_and_saves(cog):
    guild = make_guild()
    ctx = SimpleNamespace(guild=guild, send=AsyncMock(), interaction=None)

    await cog.exp_minplayers.callback(cog, ctx, 0)
    assert ctx.send.await_args.kwargs.get("ephemeral") is True
    assert await cog.config.guild(guild).exp_min_attendees() == 2

    await cog.exp_minplayers.callback(cog, ctx, 4)
    assert await cog.config.guild(guild).exp_min_attendees() == 4


async def test_lfg_exp_command_reports_progress(cog):
    tier_one = FakeRole(11, "Tier 1", position=1)
    tier_two = FakeRole(12, "Tier 2", position=2)
    guild = make_guild(roles={11: tier_one, 12: tier_two})
    await enable_exp(cog, guild, roles={"11": 2, "12": 5})
    await cog.config.member_from_ids(5, 1).set({"exp": 3, "sessions": 2})
    author = make_member(1)

    ctx = SimpleNamespace(
        guild=guild, author=author, send=AsyncMock(), interaction=None
    )

    await cog.lfg_exp.callback(cog, ctx, None)

    embed = ctx.send.await_args.kwargs["embed"]
    assert "Total EXP:** 3" in embed.description
    assert "Tier 1" in embed.description
    assert "2 to go" in embed.description

async def test_lfg_exp_reports_disabled_notice(cog):
    guild = make_guild(roles={})
    author = make_member(1)
    ctx = SimpleNamespace(
        guild=guild, author=author, send=AsyncMock(), interaction=None
    )

    await cog.lfg_exp.callback(cog, ctx, None)

    embed = ctx.send.await_args.kwargs["embed"]
    assert "disabled" in embed.description


async def test_leaderboard_sorts_and_caps_entries(cog):
    guild = make_guild()
    await cog.config.member_from_ids(5, 1).set({"exp": 5, "sessions": 1})
    await cog.config.member_from_ids(5, 2).set({"exp": 9, "sessions": 3})
    await cog.config.member_from_ids(5, 3).set({"exp": 0, "sessions": 0})
    ctx = SimpleNamespace(guild=guild, send=AsyncMock(), interaction=None)

    await cog.lfg_top.callback(cog, ctx)

    embed = ctx.send.await_args.kwargs["embed"]
    assert embed.description.index("<@2>") < embed.description.index("<@1>")
    assert "<@3>" not in embed.description


async def test_leaderboard_reports_empty(cog):
    guild = make_guild()
    ctx = SimpleNamespace(guild=guild, send=AsyncMock(), interaction=None)

    await cog.lfg_top.callback(cog, ctx)

    assert ctx.send.await_args.kwargs.get("ephemeral") is True
