"""Experience tracking and role rewards for finished sessions.

EXP is awarded when an organizer marks a session as finished. A session only
counts as successful when it has at least a configurable minimum number of
unique participants, which prevents creating and immediately finishing empty
events to farm EXP. Every unique attendee earns the configured per-session
amount, and the organizer earns an additional configurable bonus. Admins map
Discord roles to EXP thresholds and members automatically receive the highest
qualifying role; lower configured tier roles are swapped out. The whole feature
is opt-in and disabled until an admin enables it.
"""

from contextvars import ContextVar

import discord
from discord import app_commands
from redbot.core import commands

from .commands import LFGCommands


DEFAULT_EXP_PER_SESSION = 1
DEFAULT_EXP_ORGANIZER_BONUS = 0
DEFAULT_EXP_MIN_ATTENDEES = 2
MAX_EXP_PER_SESSION = 1000
MAX_EXP_MIN_ATTENDEES = 100
MAX_EXP_THRESHOLD = 1_000_000
MAX_LEADERBOARD_ENTRIES = 10


# ``_award_event_exp`` keeps the old four-argument ``_exp_add_member`` call
# shape so extensions/tests that wrap that helper continue to work.  The
# context-local event id still lets the helper record an idempotency marker in
# the member's Config transaction.
_EXP_EVENT_KEY = ContextVar("lfg_exp_event_key", default=None)


class LFGExp:
    """EXP award engine and member-facing progress commands."""

    @staticmethod
    def _exp_coerce_id(value) -> int | None:
        """Return a positive integer id, or ``None`` for unusable values."""

        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @classmethod
    def _exp_unique_ids(cls, values) -> list[int]:
        """De-duplicate attendee ids while preserving their order."""

        unique = []
        for value in values or []:
            number = cls._exp_coerce_id(value)
            if number is not None and number not in unique:
                unique.append(number)
        return unique

    @staticmethod
    def _exp_sort_tiers(exp_roles) -> list[tuple[int, int]]:
        """Return ``(threshold, role_id)`` pairs sorted ascending by threshold."""

        tiers = []
        if not isinstance(exp_roles, dict):
            return tiers
        for role_id, threshold in exp_roles.items():
            number = LFGExp._exp_coerce_id(role_id)
            try:
                required = int(threshold)
            except (TypeError, ValueError):
                continue
            if number is not None and required >= 0:
                tiers.append((required, number))
        tiers.sort(key=lambda item: (item[0], item[1]))
        return tiers

    @staticmethod
    def _exp_highest_role(exp: int, tiers) -> int | None:
        """Return the role id for the highest threshold met by ``exp``."""

        target = None
        for threshold, role_id in tiers:
            if exp >= threshold:
                target = role_id
            else:
                break
        return target

    @staticmethod
    def _exp_next_tier(exp: int, tiers) -> tuple[int, int] | None:
        """Return the lowest ``(threshold, role_id)`` above ``exp``, if any."""

        for threshold, role_id in tiers:
            if exp < threshold:
                return threshold, role_id
        return None

    @staticmethod
    def _exp_can_manage_role(guild, role) -> bool:
        """Check whether the bot may add or remove ``role``."""

        # ``@everyone`` and integration-managed roles cannot be assigned.
        if getattr(role, "is_default", lambda: False)():
            return False
        if getattr(role, "managed", False):
            return False
        bot_member = getattr(guild, "me", None)
        if bot_member is None:
            return False
        top_role = getattr(bot_member, "top_role", None)
        if top_role is not None and not role < top_role:
            return False
        permissions = getattr(bot_member, "guild_permissions", None)
        if permissions is not None and hasattr(permissions, "manage_roles"):
            if not permissions.manage_roles:
                return False
        return True

    async def _exp_settings(self, guild) -> dict:
        """Read the guild's EXP configuration with safe defaults."""

        config = self.config.guild(guild)
        return {
            "enabled": bool(await config.exp_enabled()),
            "per_session": max(
                0, self._as_int(await config.exp_per_session(), DEFAULT_EXP_PER_SESSION)
            ),
            "organizer_bonus": max(
                0,
                self._as_int(
                    await config.exp_organizer_bonus(), DEFAULT_EXP_ORGANIZER_BONUS
                ),
            ),
            "min_attendees": max(
                1,
                self._as_int(
                    await config.exp_min_attendees(), DEFAULT_EXP_MIN_ATTENDEES
                ),
            ),
            "roles": await config.exp_roles(),
        }

    async def _exp_change_role(
        self, member, role, *, add: bool, member_id: int
    ) -> str | None:
        """Add or remove one role and translate failures into a warning.

        Returns a user-facing warning string, or ``None`` on success.
        """

        operation = "grant" if add else "remove"
        try:
            if add:
                await member.add_roles(role, reason="LFG EXP reward")
            else:
                await member.remove_roles(role, reason="LFG EXP tier update")
        except discord.Forbidden as exc:
            self._log_http_error(
                f"{operation} exp role",
                exc,
                role_id=getattr(role, "id", None),
                member_id=member_id,
            )
            if add:
                return (
                    f"I couldn't grant the role '{role.name}'. Check my Manage Roles "
                    "permission and role position."
                )
            return (
                f"I couldn't remove the role '{role.name}'. Check my Manage Roles "
                "permission and role position."
            )
        except discord.HTTPException as exc:
            self._log_http_error(
                f"{operation} exp role",
                exc,
                role_id=getattr(role, "id", None),
                member_id=member_id,
            )
            return f"I couldn't {operation} the role '{role.name}'."
        except Exception as exc:  # pragma: no cover - defensive
            self._log_exception(
                f"{operation} exp role", exc, role_id=getattr(role, "id", None)
            )
            return f"I couldn't {operation} the role '{role.name}'."
        return None

    async def _exp_sync_roles(
        self, guild, member_id: int, total_exp: int, tiers=None
    ) -> str | None:
        """Grant the highest earned tier role and swap out lower tiers.

        Returns a warning string when a role could not be changed. Role
        problems never prevent the member's EXP from being saved.
        """

        if tiers is None:
            tiers = self._exp_sort_tiers(
                await self.config.guild(guild).exp_roles()
            )
        if not tiers:
            return None

        target_role_id = self._exp_highest_role(total_exp, tiers)
        configured = {role_id for _, role_id in tiers}

        get_member = getattr(guild, "get_member", None)
        member = get_member(member_id) if callable(get_member) else None
        if member is None:
            # The member left or is not cached; EXP is still stored.
            return None

        held = {
            role.id
            for role in getattr(member, "roles", []) or []
            if getattr(role, "id", None) in configured
        }
        to_keep = {target_role_id} if target_role_id is not None else set()
        get_role = getattr(guild, "get_role", None)

        # A failed grant must not strand a member without any configured tier
        # role.  Resolve and preflight the target before removing lower roles;
        # after a successful grant, a failed lower-role removal merely leaves
        # the member with both roles, which is safe to repair on the next sync.
        target_role = None
        if target_role_id is not None:
            target_role = get_role(target_role_id) if callable(get_role) else None
            if target_role is None:
                return (
                    f"Reward role 'role {target_role_id}' could not be granted "
                    "because it no longer exists."
                )
            if target_role_id not in held and not self._exp_can_manage_role(
                guild, target_role
            ):
                return (
                    f"Reward role '{target_role.name}' could not be granted because it is "
                    "above my highest role or I lack Manage Roles."
                )

            if target_role_id not in held:
                warning = await self._exp_change_role(
                    member, target_role, add=True, member_id=member_id
                )
                if warning:
                    return warning

        for role_id in sorted(held - to_keep):
            role = get_role(role_id) if callable(get_role) else None
            if role is None:
                continue
            warning = await self._exp_change_role(
                member, role, add=False, member_id=member_id
            )
            if warning:
                return warning

        return None

    async def _exp_add_member(
        self, guild, member_id: int, amount: int, tiers
    ) -> dict:
        """Add EXP and a finished session to one member, then sync roles.

        When called from :meth:`_award_event_exp`, the context-local event id
        makes this transaction idempotent.  This closes the small window where
        a member write can commit but the event's progress marker cannot be
        persisted before a process crash.
        """

        event_key = _EXP_EVENT_KEY.get()
        group = self.config.member_from_ids(guild.id, member_id)
        awarded = True
        async with group.all() as data:
            if event_key is not None:
                event_awards = data.get("exp_awards")
                if not isinstance(event_awards, dict):
                    event_awards = {}
                if event_key in event_awards:
                    awarded = False
                else:
                    data["exp"] = max(0, self._as_int(data.get("exp"), 0) + amount)
                    data["sessions"] = max(
                        0, self._as_int(data.get("sessions"), 0) + 1
                    )
                    event_awards[event_key] = amount
                    data["exp_awards"] = event_awards
            else:
                data["exp"] = max(0, self._as_int(data.get("exp"), 0) + amount)
                data["sessions"] = max(
                    0, self._as_int(data.get("sessions"), 0) + 1
                )
            total_exp = data["exp"]
        try:
            warning = await self._exp_sync_roles(guild, member_id, total_exp, tiers)
        except Exception as exc:  # pragma: no cover - defensive
            # The member transaction above is already committed.  Keep the
            # event resumable even if an unexpected role/cache failure slips
            # past the normal role-operation guards.
            self._log_exception(
                "sync exp roles", exc, member_id=member_id
            )
            warning = "I couldn't update this member's EXP reward role."
        return {"exp": total_exp, "warning": warning, "awarded": awarded}

    async def _award_event_exp(self, guild, message_id) -> dict:
        """Award EXP for one finished event, claiming it exactly once.

        A session only counts when it reached the configured minimum number
        of unique participants. Below that, nothing is awarded and the event
        is not marked as claimed, so a later state change cannot be mistaken
        for an already-rewarded session.
        """

        summary = {
            "awarded": 0,
            "warnings": [],
            "enabled": False,
            "eligible": True,
            "min_attendees": DEFAULT_EXP_MIN_ATTENDEES,
        }
        settings = await self._exp_settings(guild)
        summary["enabled"] = settings["enabled"]
        summary["min_attendees"] = settings["min_attendees"]

        event_key = str(message_id)
        # Read and initialize the event's resumable progress under the event
        # lock.  The event is only marked fully claimed after every member has
        # completed an idempotent member-level write below.
        async with self._event_mutation_lock(guild.id):
            async with self.config.guild(guild).scheduled_events() as events:
                current = events.get(event_key)
                if not isinstance(current, dict) or current.get("exp_awarded"):
                    return summary

                pending_intent = bool(current.get("exp_award_pending"))
                if not settings["enabled"] and not pending_intent:
                    # A finished event with no persisted award intent retains
                    # the historical disabled behavior.  A pending event is
                    # different: it was enabled when finishing began and must
                    # survive an admin toggle or process restart.
                    return summary

                snapshot = current.get("exp_award_settings")
                if isinstance(snapshot, dict):
                    settings["per_session"] = max(
                        0,
                        self._as_int(
                            snapshot.get("per_session"), settings["per_session"]
                        ),
                    )
                    settings["organizer_bonus"] = max(
                        0,
                        self._as_int(
                            snapshot.get("organizer_bonus"),
                            settings["organizer_bonus"],
                        ),
                    )
                    settings["min_attendees"] = max(
                        1,
                        self._as_int(
                            snapshot.get("min_attendees"), settings["min_attendees"]
                        ),
                    )
                if pending_intent:
                    settings["enabled"] = True
                summary["enabled"] = settings["enabled"]
                summary["min_attendees"] = settings["min_attendees"]

                attendees = self._exp_unique_ids(current.get("attendees"))
                if len(attendees) < settings["min_attendees"]:
                    # Too few participants to count as a successful session.
                    summary["eligible"] = False
                    if pending_intent:
                        current["exp_award_pending"] = False
                    return summary
                organizer_id = self._exp_coerce_id(current.get("organizer_id"))
                completed = set(
                    self._exp_unique_ids(current.get("exp_awarded_members"))
                )
                completed.intersection_update(attendees)
                current["exp_award_pending"] = True
                current["exp_award_settings"] = {
                    "per_session": settings["per_session"],
                    "organizer_bonus": settings["organizer_bonus"],
                    "min_attendees": settings["min_attendees"],
                }
                current["exp_awarded_members"] = [
                    member_id for member_id in attendees if member_id in completed
                ]

            base = settings["per_session"]
            bonus = settings["organizer_bonus"]
            tiers = self._exp_sort_tiers(settings["roles"])
            event_token = _EXP_EVENT_KEY.set(event_key)
            try:
                for member_id in attendees:
                    if member_id in completed:
                        continue
                    amount = base + (bonus if member_id == organizer_id else 0)
                    result = await self._exp_add_member(guild, member_id, amount, tiers)
                    if not isinstance(result, dict):
                        result = {}
                    if result.get("awarded", True):
                        summary["awarded"] += 1
                    if result.get("warning"):
                        summary["warnings"].append(result["warning"])

                    # Persist each completed member separately.  If the next
                    # member fails, a retry can resume from this point without
                    # duplicating this member's EXP.
                    async with self.config.guild(guild).scheduled_events() as events:
                        current = events.get(event_key)
                        if not isinstance(current, dict):
                            continue
                        progress = self._exp_unique_ids(
                            current.get("exp_awarded_members")
                        )
                        if member_id not in progress:
                            progress.append(member_id)
                        current["exp_award_pending"] = True
                        current["exp_awarded_members"] = progress
                        if all(attendee_id in progress for attendee_id in attendees):
                            current["exp_awarded"] = True
                            current["exp_award_pending"] = False
                            completed.update(attendees)

                # This also finalizes an event whose progress was already
                # complete when an earlier process exited before the final
                # transaction, while preserving the once-only guard.
                async with self.config.guild(guild).scheduled_events() as events:
                    current = events.get(event_key)
                    if isinstance(current, dict):
                        progress = set(
                            self._exp_unique_ids(current.get("exp_awarded_members"))
                        )
                        if all(attendee_id in progress for attendee_id in attendees):
                            current["exp_awarded"] = True
                            current["exp_award_pending"] = False
            finally:
                _EXP_EVENT_KEY.reset(event_token)
        return summary

    def _exp_role_name(self, guild, role_id) -> str:
        """Return a display name for a configured role id."""

        get_role = getattr(guild, "get_role", None)
        role = get_role(role_id) if callable(get_role) else None
        return role.name if role is not None else f"role {role_id}"

    def _exp_progress_lines(self, guild, exp: int, sessions: int, tiers) -> list[str]:
        """Describe a member's totals and progress toward the next tier."""

        target_role_id = self._exp_highest_role(exp, tiers)
        lines = [
            f"**Total EXP:** {exp}",
            f"**Successful sessions:** {sessions}",
        ]
        if target_role_id is not None:
            lines.append(
                f"**Current tier:** {self._exp_role_name(guild, target_role_id)}"
            )
        else:
            lines.append("**Current tier:** None")

        next_tier = self._exp_next_tier(exp, tiers)
        if next_tier is None:
            lines.append("**Next tier:** Max tier reached")
        else:
            threshold, role_id = next_tier
            remaining = threshold - exp
            lines.append(
                f"**Next tier:** {self._exp_role_name(guild, role_id)} at "
                f"{threshold} EXP ({remaining} to go)"
            )
        return lines

    @LFGCommands.lfg_set.group(name="exp")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def exp_group(self, ctx: commands.Context):
        """Configure session EXP and role rewards."""

        pass

    @exp_group.command(name="toggle")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def exp_toggle(self, ctx: commands.Context):
        """Enable or disable the EXP system on this server."""

        async with self._event_mutation_lock(ctx.guild.id):
            config = self.config.guild(ctx.guild)
            enabled = not bool(await config.exp_enabled())
            await config.exp_enabled.set(enabled)
        state = "enabled" if enabled else "disabled"
        await ctx.send(f"✅ Session EXP is now **{state}**.")

    @exp_group.command(name="persession")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @app_commands.describe(amount="EXP awarded to each attendee of a finished session.")
    async def exp_persession(self, ctx: commands.Context, amount: int):
        """Set the EXP granted to each attendee of a finished session."""

        if amount < 0 or amount > MAX_EXP_PER_SESSION:
            return await ctx.send(
                f"❌ The amount must be between 0 and {MAX_EXP_PER_SESSION}.",
                ephemeral=True,
            )
        async with self._event_mutation_lock(ctx.guild.id):
            await self.config.guild(ctx.guild).exp_per_session.set(amount)
        await ctx.send(f"✅ Each attendee now earns **{amount}** EXP per session.")

    @exp_group.command(name="organizerbonus")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @app_commands.describe(amount="Extra EXP the organizer earns on top of the per-session amount.")
    async def exp_organizerbonus(self, ctx: commands.Context, amount: int):
        """Set the extra EXP the organizer earns for running a session."""

        if amount < 0 or amount > MAX_EXP_PER_SESSION:
            return await ctx.send(
                f"❌ The amount must be between 0 and {MAX_EXP_PER_SESSION}.",
                ephemeral=True,
            )
        async with self._event_mutation_lock(ctx.guild.id):
            await self.config.guild(ctx.guild).exp_organizer_bonus.set(amount)
        await ctx.send(
            f"✅ Organizers now earn an extra **{amount}** EXP per finished session."
        )

    @exp_group.command(name="minplayers")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @app_commands.describe(
        amount="Unique participants required for a session to award EXP (1-100)."
    )
    async def exp_minplayers(self, ctx: commands.Context, amount: int):
        """Set how many participants a session needs before it awards EXP."""

        if amount < 1 or amount > MAX_EXP_MIN_ATTENDEES:
            return await ctx.send(
                f"❌ The minimum must be between 1 and {MAX_EXP_MIN_ATTENDEES}.",
                ephemeral=True,
            )
        async with self._event_mutation_lock(ctx.guild.id):
            await self.config.guild(ctx.guild).exp_min_attendees.set(amount)
        await ctx.send(
            f"✅ A session now needs at least **{amount}** unique participant(s) "
            "to award EXP."
        )

    @exp_group.command(name="addrole")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @app_commands.describe(
        role="The role to grant.", threshold="The EXP required to earn the role."
    )
    async def exp_addrole(self, ctx: commands.Context, role: discord.Role, threshold: int):
        """Map a role to an EXP threshold."""

        if threshold <= 0 or threshold > MAX_EXP_THRESHOLD:
            return await ctx.send(
                f"❌ The threshold must be between 1 and {MAX_EXP_THRESHOLD}.",
                ephemeral=True,
            )
        if not self._exp_can_manage_role(ctx.guild, role):
            return await ctx.send(
                f"❌ I can't manage {role.mention}. Move my highest role above it "
                "and make sure I have Manage Roles.",
                ephemeral=True,
            )
        async with self._event_mutation_lock(ctx.guild.id):
            async with self.config.guild(ctx.guild).exp_roles() as roles:
                roles[str(role.id)] = threshold
        await ctx.send(f"✅ {role.mention} will be granted at **{threshold}** EXP.")

    @exp_group.command(name="removerole")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @app_commands.describe(role="The role to remove from the rewards list.")
    async def exp_removerole(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from the EXP rewards list."""

        async with self._event_mutation_lock(ctx.guild.id):
            async with self.config.guild(ctx.guild).exp_roles() as roles:
                existed = roles.pop(str(role.id), None) is not None
        if not existed:
            return await ctx.send(
                f"❌ {role.mention} is not a configured EXP reward role.", ephemeral=True
            )
        await ctx.send(f"✅ {role.mention} was removed from the EXP rewards list.")

    @exp_group.command(name="show")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def exp_show(self, ctx: commands.Context):
        """Show the current EXP configuration."""

        settings = await self._exp_settings(ctx.guild)
        tiers = self._exp_sort_tiers(settings["roles"])
        if tiers:
            role_lines = "\n".join(
                f"- {self._exp_role_name(ctx.guild, role_id)} — {threshold} EXP"
                for threshold, role_id in tiers
            )
        else:
            role_lines = "_No reward roles configured._"

        embed = discord.Embed(
            title="Session EXP configuration",
            color=discord.Color.blurple(),
            description=(
                f"**Status:** {'enabled' if settings['enabled'] else 'disabled'}\n"
                f"**EXP per session:** {settings['per_session']}\n"
                f"**Organizer bonus:** {settings['organizer_bonus']}\n"
                f"**Minimum participants:** {settings['min_attendees']}\n\n"
                f"**Reward roles (lowest to highest):**\n{role_lines}"
            ),
        )
        await ctx.send(embed=embed)

    @LFGCommands.lfg.command(name="exp", aliases=["xp", "stats"])
    @commands.guild_only()
    @app_commands.describe(member="The member to look up. Defaults to you.")
    async def lfg_exp(self, ctx: commands.Context, member: discord.Member = None):
        """Show a member's session EXP and progress toward reward roles."""

        target = member or ctx.author
        group = self.config.member_from_ids(ctx.guild.id, target.id)
        data = await group.all()
        exp = self._as_int(data.get("exp"), 0)
        sessions = self._as_int(data.get("sessions"), 0)
        settings = await self._exp_settings(ctx.guild)
        tiers = self._exp_sort_tiers(settings["roles"])

        lines = self._exp_progress_lines(ctx.guild, exp, sessions, tiers)
        if not settings["enabled"]:
            lines.append(
                "\n_EXP rewards are currently disabled on this server._"
            )

        embed = discord.Embed(
            title=f"Session EXP for {target.display_name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @LFGCommands.lfg.command(name="top", aliases=["leaderboard", "rank"])
    @commands.guild_only()
    async def lfg_top(self, ctx: commands.Context):
        """Show the members with the most session EXP."""

        all_members = await self.config.all_members(ctx.guild)
        rows = []
        for member_id, data in all_members.items():
            if not isinstance(data, dict):
                continue
            exp = self._as_int(data.get("exp"), 0)
            if exp <= 0:
                continue
            rows.append((exp, self._as_int(data.get("sessions"), 0), member_id))
        rows.sort(key=lambda item: (-item[0], -item[1], item[2]))
        rows = rows[:MAX_LEADERBOARD_ENTRIES]

        if not rows:
            return await ctx.send(
                "No session EXP has been recorded yet.", ephemeral=True
            )

        lines = [
            f"**{index}.** <@{member_id}> — {exp} EXP ({sessions} sessions)"
            for index, (exp, sessions, member_id) in enumerate(rows, start=1)
        ]
        embed = discord.Embed(
            title="Session EXP leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await ctx.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )
