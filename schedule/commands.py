"""Commands for creating and managing scheduled game sessions."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import re
from types import SimpleNamespace

import dateparser
import discord
import pytz
from discord import app_commands
from redbot.core import commands


DEFAULT_TIMEZONE = "Asia/Jakarta"
MAX_PLAYER_AMOUNT = 100
MAX_TITLE_LENGTH = 256
MAX_DESCRIPTION_LENGTH = 1024
MAX_UPCOMING_EVENTS = 10
_EXPLICIT_TIMEZONE_RE = re.compile(
    r"(?:\b(?:UTC|GMT|[A-Z]{2,5})\b|[+-]\d{2}:?\d{2}\b|\b[A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?\b)"
)
_RELATIVE_DURATION_RE = re.compile(
    r"^\s*in\s+.+\b(?:seconds?|minutes?|hours?)\b", re.IGNORECASE
)


class ScheduleTimeError(ValueError):
    """A user-facing error raised when a schedule time cannot be accepted."""


def parse_schedule_time(
    time_input: str,
    timezone_name: str,
    *,
    now: datetime | None = None,
) -> tuple[int, datetime]:
    """Parse a future time in ``timezone_name`` and return its Unix timestamp.

    ``dateparser`` is used for the natural-language input accepted by the
    original command. The result is localized again with ``is_dst=None`` so
    pytz can reject ambiguous and nonexistent wall-clock times around DST
    transitions instead of silently choosing one.
    """

    if not isinstance(time_input, str) or not time_input.strip():
        raise ScheduleTimeError("Please provide a time, such as `tomorrow at 8pm`.")

    try:
        target_tz = pytz.timezone(timezone_name)
    except (pytz.UnknownTimeZoneError, AttributeError, TypeError):
        raise ScheduleTimeError(f"`{timezone_name}` is not a valid timezone.") from None

    if now is None:
        now_utc = datetime.now(timezone.utc)
    elif isinstance(now, datetime):
        if now.tzinfo is None:
            now_utc = pytz.UTC.localize(now)
        else:
            now_utc = now.astimezone(timezone.utc)
    else:
        raise TypeError("now must be a datetime or None")

    # An aware relative base keeps phrases such as "in 2 hours" anchored to
    # the selected local timezone. dateparser's future preference handles
    # underspecified dates; the timestamp check catches explicit past dates.
    text = time_input.strip()
    settings = {
        "PREFER_DATES_FROM": "future",
        "TIMEZONE": timezone_name,
        "RELATIVE_BASE": now_utc.astimezone(target_tz),
        "RETURN_AS_TIMEZONE_AWARE": True,
    }
    naive_settings = dict(settings)
    naive_settings["RETURN_AS_TIMEZONE_AWARE"] = False
    try:
        parsed = dateparser.parse(text, settings=settings)
        parsed_wall = dateparser.parse(text, settings=naive_settings)
    except (OverflowError, TypeError, ValueError):
        parsed = None

    if parsed is None:
        raise ScheduleTimeError(
            f"I couldn't understand `{time_input}`. Try `tomorrow at 8pm`."
        )

    try:
        # Use the naive parse for wall-clock input. Re-running astimezone on a
        # pytz value can normalize a spring-forward gap before pytz gets a
        # chance to reject it.
        has_explicit_timezone = bool(_EXPLICIT_TIMEZONE_RE.search(text))
        is_relative_duration = bool(_RELATIVE_DURATION_RE.search(text))
        wall_time = parsed_wall
        if wall_time is None:
            wall_time = parsed.replace(tzinfo=None)

        if is_relative_duration and parsed.tzinfo is not None:
            # "In two hours" describes elapsed time. Re-localizing its wall
            # clock result around a DST boundary can turn that into one or
            # three hours, so retain dateparser's absolute instant.
            localized = parsed.astimezone(target_tz)
        elif has_explicit_timezone and parsed.tzinfo is not None:
            # An explicit offset disambiguates a repeated fall-back hour. A
            # nonexistent spring-forward wall time remains invalid.
            iso_value = text.replace("Z", "+00:00")
            try:
                explicit = datetime.fromisoformat(iso_value)
            except ValueError:
                explicit = None
            if explicit is not None and explicit.tzinfo is not None:
                wall_time = explicit.replace(tzinfo=None)
            try:
                target_tz.localize(wall_time, is_dst=None)
            except pytz.AmbiguousTimeError:
                pass
            except pytz.NonExistentTimeError:
                raise ScheduleTimeError(
                    f"`{time_input}` does not exist in `{timezone_name}` because daylight-saving time changes then. "
                    "Please choose another time."
                ) from None
            localized = (explicit or parsed).astimezone(target_tz)
            if localized.replace(tzinfo=None) != wall_time:
                raise ScheduleTimeError(
                    f"The UTC offset in `{time_input}` does not match `{timezone_name}` at that local time."
                )
        else:
            localized = target_tz.localize(wall_time, is_dst=None)
    except ScheduleTimeError:
        raise
    except pytz.AmbiguousTimeError:
        raise ScheduleTimeError(
            f"`{time_input}` is ambiguous in `{timezone_name}` because daylight-saving time changes then. "
            "Please include an unambiguous time."
        ) from None
    except pytz.NonExistentTimeError:
        raise ScheduleTimeError(
            f"`{time_input}` does not exist in `{timezone_name}` because daylight-saving time changes then. "
            "Please choose another time."
        ) from None
    except (OverflowError, TypeError, ValueError):
        raise ScheduleTimeError(
            f"I couldn't understand `{time_input}`. Try `tomorrow at 8pm`."
        ) from None

    if localized.astimezone(timezone.utc) <= now_utc:
        raise ScheduleTimeError("The schedule time must be in the future.")

    return int(localized.timestamp()), localized


def normalize_message_id(value) -> int | None:
    """Accept a decimal snowflake or a Discord message URL safely."""

    if value is None:
        return None
    text = str(value).strip().rstrip("/")
    if "/" in text:
        text = text.split("?", 1)[0].rsplit("/", 1)[-1]
    if not text.isdigit():
        return None
    try:
        message_id = int(text)
    except (TypeError, ValueError, OverflowError):
        return None
    return message_id if message_id > 0 else None


class ScheduleCommands:
    """Commands for the Schedule cog."""

    def _organizer_controls_text(self, ctx: commands.Context, message) -> str:
        """Describe private controls for the organizer of a new schedule.

        Reactions are intentionally kept on the public card for attendance only.
        The action commands below remain hybrid commands so they work for both
        slash and prefix users, while their callbacks continue to authorize the
        organizer (or a member with Manage Server) from Config.
        """

        if ctx.interaction:
            command_names = (
                "`/scheduleremind`",
                "`/scheduleshare`",
                "`/schedulereschedule`",
                "`/schedulecancel`",
                "`/schedulefinish`",
            )
        else:
            prefix = getattr(ctx, "clean_prefix", None) or "[p]"
            command_names = tuple(
                f"`{prefix}{name}`"
                for name in (
                    "scheduleremind",
                    "scheduleshare",
                    "schedulereschedule",
                    "schedulecancel",
                    "schedulefinish",
                )
            )
        message_id = getattr(message, "id", "the schedule message")
        return (
            "Organizer controls (only you or a member with Manage Server can use them):\n"
            f"- {command_names[0]} `{message_id}` to remind attendees when the event starts within 30 minutes.\n"
            f"- {command_names[1]} `{message_id}` to post an announcement.\n"
            f"- Use {command_names[2]}, {command_names[3]}, or {command_names[4]} with the message ID to manage its lifecycle."
        )

    async def _send_private_organizer_message(
        self,
        ctx: commands.Context,
        content: str,
        fallback: str,
    ) -> bool:
        """Send organizer details privately for either command invocation."""

        if ctx.interaction:
            await ctx.send(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

        author = getattr(ctx, "author", None)
        send_dm = getattr(author, "send", None)
        if callable(send_dm):
            try:
                await send_dm(
                    content,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return True
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
            except Exception as exc:
                logger = getattr(self.bot, "log", None)
                if logger:
                    logger.error("Failed to DM schedule organizer controls: %s", exc)

        # Prefix responses are public. Keep the fallback limited to recovery
        # guidance and do not print the private action details in the channel.
        try:
            await ctx.send(
                fallback,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
        return False

    async def _send_prefix_organizer_controls(
        self, ctx: commands.Context, message
    ) -> bool:
        """DM prefix-command users, with a non-sensitive fallback if DMs fail."""

        return await self._send_private_organizer_message(
            ctx,
            self._organizer_controls_text(ctx, message),
            "I couldn't DM your private organizer controls. Use the organizer-only schedule commands with the schedule message ID.",
        )

    @asynccontextmanager
    async def _event_mutation_lock(self, guild_id: int):
        """Use the cog's per-guild event lock when the main cog provides it."""

        async with self._event_lock(guild_id):
            yield

    @staticmethod
    def _event_copy(event_data: dict) -> dict:
        """Copy mutable event values before leaving a Config transaction."""

        copied = dict(event_data)
        copied["attendees"] = list(event_data.get("attendees", []))
        copied["tags"] = list(event_data.get("tags", []))
        return copied

    @staticmethod
    def _event_status(event_data: dict) -> str:
        """Treat events written by older versions as active."""

        status = str(event_data.get("status") or "active").lower()
        return status if status in {"active", "cancelled", "finished"} else "active"

    @staticmethod
    def _has_manage_guild(member: discord.Member) -> bool:
        permissions = getattr(member, "guild_permissions", None)
        return bool(
            getattr(permissions, "manage_guild", False)
            or getattr(permissions, "administrator", False)
        )

    async def _load_authorized_event(
        self, ctx: commands.Context, message_id: int
    ) -> tuple[dict | None, str | None]:
        """Read and authorize an event without doing network work in the lock."""

        async with self._event_mutation_lock(ctx.guild.id):
            async with self.config.guild(ctx.guild).scheduled_events() as events:
                event_data = events.get(str(message_id))
                if not isinstance(event_data, dict):
                    return None, "I couldn't find a schedule with that message ID."

                is_organizer = self._same_id(
                    event_data.get("organizer_id"), ctx.author.id
                )
                if not (is_organizer or self._has_manage_guild(ctx.author)):
                    return (
                        None,
                        "Only the event organizer or a member with Manage Server can do that.",
                    )
                return self._event_copy(event_data), None

    async def _fetch_event_message(
        self, guild: discord.Guild, message_id: int, event_data: dict
    ) -> discord.Message | None:
        """Fetch an event message after its Config transaction has completed."""

        channel_id = event_data.get("channel_id")
        if not channel_id:
            return None
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return None
        fetch_message = getattr(channel, "fetch_message", None)
        if not callable(fetch_message):
            return None
        try:
            return await fetch_message(message_id)
        except Exception:
            return None

    async def _update_event_message(
        self, guild: discord.Guild, message_id: int, event_data: dict
    ) -> bool:
        """Refresh the Discord embed, if its message is still available."""

        message = await self._fetch_event_message(guild, message_id, event_data)
        if message is None:
            return False
        try:
            await self._update_embed(message, event_data)
            return True
        except Exception as exc:
            logger = getattr(self.bot, "log", None)
            if logger:
                logger.error(
                    "Failed to update schedule message %s after a command: %s",
                    message_id,
                    exc,
                )
            return False

    async def _member_timezone(self, member: discord.Member) -> tuple[str, bool]:
        """Return a valid personal timezone, or the documented default."""

        configured = await self.config.member(member).timezone()
        if configured:
            try:
                pytz.timezone(configured)
            except (pytz.UnknownTimeZoneError, AttributeError, TypeError):
                configured = None
            else:
                return str(configured), False
        return DEFAULT_TIMEZONE, True

    @staticmethod
    def _missing_bot_permissions(ctx: commands.Context) -> list[str]:
        """Check permissions needed for a posted event and its reactions."""

        channel = ctx.channel
        permissions_for = getattr(channel, "permissions_for", None)
        guild = getattr(ctx, "guild", None)
        bot_member = getattr(guild, "me", None)
        if bot_member is None and guild is not None:
            get_member = getattr(guild, "get_member", None)
            bot = getattr(getattr(ctx, "bot", None), "user", None)
            if bot is not None and callable(get_member):
                bot_member = get_member(getattr(bot, "id", None))
        if not callable(permissions_for) or bot_member is None:
            return []
        permissions = permissions_for(bot_member)
        required = {
            "view_channel": "View Channel",
            "read_message_history": "Read Message History",
            "send_messages": "Send Messages",
            "embed_links": "Embed Links",
            "add_reactions": "Add Reactions",
            "manage_messages": "Manage Messages",
        }
        return [label for attr, label in required.items() if not getattr(permissions, attr, False)]

    @staticmethod
    def _validate_event_details(
        player_amount: int, title: str | None, description: str | None
    ) -> tuple[str | None, str | None, str | None]:
        if player_amount < 1 or player_amount > MAX_PLAYER_AMOUNT:
            return (
                None,
                None,
                f"Player amount must be between 1 and {MAX_PLAYER_AMOUNT}.",
            )

        clean_title = None
        if title is not None:
            clean_title = title.strip()
            if not clean_title:
                return None, None, "The title cannot be blank."
            if len(clean_title) > MAX_TITLE_LENGTH:
                return (
                    None,
                    None,
                    f"The title must be {MAX_TITLE_LENGTH} characters or fewer.",
                )

        clean_description = None
        if description is not None:
            clean_description = description.strip()
            if not clean_description:
                return None, None, "The description cannot be blank."
            if len(clean_description) > MAX_DESCRIPTION_LENGTH:
                return (
                    None,
                    None,
                    f"The description must be {MAX_DESCRIPTION_LENGTH} characters or fewer.",
                )

        return clean_title, clean_description, None

    @commands.hybrid_group(name="scheduleset", aliases=["ss"])
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def schedule_set(self, ctx: commands.Context):
        """Base command for schedule configuration."""

        pass

    @schedule_set.command(name="forum")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @app_commands.describe(forum="The forum channel where schedules will be created.")
    async def set_forum(self, ctx: commands.Context, forum: discord.ForumChannel):
        """Set the designated forum channel for new game schedules."""

        async with self._event_mutation_lock(ctx.guild.id):
            await self.config.guild(ctx.guild).target_forum_id.set(forum.id)
        await ctx.send(f"✅ The scheduling forum has been set to {forum.mention}.")

    @schedule_set.command(name="sharechannel")
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @app_commands.describe(
        channel="The text channel where shared schedules will be posted."
    )
    async def set_share_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the designated text channel for shared announcements."""

        async with self._event_mutation_lock(ctx.guild.id):
            await self.config.guild(ctx.guild).share_channel_id.set(channel.id)
        await ctx.send(f"✅ The schedule sharing channel has been set to {channel.mention}.")

    @commands.hybrid_command()
    @commands.guild_only()
    @app_commands.describe(
        timezone_str="Your timezone name (e.g., 'Asia/Jakarta' or 'America/New_York')."
    )
    async def settimezone(self, ctx: commands.Context, timezone_str: str):
        """Set your personal timezone for scheduling."""

        try:
            tz = pytz.timezone(timezone_str.strip())
        except (pytz.UnknownTimeZoneError, AttributeError, TypeError):
            await ctx.send(
                f"❌ Invalid timezone: `{timezone_str}`. Please use a valid TZ database name.\n"
                "Find your timezone here: <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await self.config.member(ctx.author).timezone.set(str(tz))
        await ctx.send(f"✅ Your timezone has been set to `{tz}`.")

    @commands.hybrid_command()
    @commands.guild_only()
    @app_commands.describe(
        player_amount="The maximum number of players for the lobby (1-100).",
        time_input="When the game starts (for example, 'in 2 hours' or 'thursday at 10pm').",
        title="Optional: A custom title for the game session (up to 256 characters).",
        description="Optional: A description for the game session (up to 1024 characters).",
    )
    async def schedule(
        self,
        ctx: commands.Context,
        player_amount: int,
        time_input: str,
        title: str = None,
        description: str = None,
    ):
        """Schedule a game session inside the designated forum thread."""

        if ctx.interaction:
            await ctx.defer(ephemeral=True)

        target_forum_id = await self.config.guild(ctx.guild).target_forum_id()
        if not target_forum_id:
            return await ctx.send(
                "The scheduling forum has not been set. An admin must use `[p]scheduleset forum`.",
                ephemeral=True,
            )

        if not isinstance(ctx.channel, discord.Thread) or ctx.channel.parent_id != target_forum_id:
            return await ctx.send(
                "This command can only be used inside a thread of the designated scheduling forum.",
                ephemeral=True,
            )

        missing_permissions = self._missing_bot_permissions(ctx)
        if missing_permissions:
            return await ctx.send(
                "❌ I need these permissions in the scheduling thread: "
                + ", ".join(missing_permissions)
                + ".",
                ephemeral=True,
            )

        clean_title, clean_description, validation_error = self._validate_event_details(
            player_amount, title, description
        )
        if validation_error:
            return await ctx.send(f"❌ {validation_error}", ephemeral=True)

        timezone_name, used_default_timezone = await self._member_timezone(ctx.author)
        try:
            unix_timestamp, _ = parse_schedule_time(time_input, timezone_name)
        except ScheduleTimeError as exc:
            default_note = (
                f" Your personal timezone is not set, so the default `{DEFAULT_TIMEZONE}` was used."
                if used_default_timezone
                else ""
            )
            return await ctx.send(
                f"❌ {exc}{default_note}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        game_title = clean_title or ctx.channel.name
        thread_tags = (
            [
                f"{str(tag.emoji)}{tag.name}" if tag.emoji else f"{tag.name}"
                for tag in ctx.channel.applied_tags
            ]
            if ctx.channel.applied_tags
            else []
        )
        event_data = {
            "organizer_id": ctx.author.id,
            "player_limit": player_amount,
            "game_title": game_title,
            "description": clean_description,
            "start_timestamp": unix_timestamp,
            "timezone": timezone_name,
            "timezone_defaulted": used_default_timezone,
            "channel_id": ctx.channel.id,
            "attendees": [ctx.author.id],
            "last_shared_timestamp": 0,
            "tags": thread_tags,
            "status": "active",
            "private_controls": True,
        }

        try:
            embed = self._build_embed(event_data, ctx.author.mention)
            msg = await ctx.channel.send(embed=embed)
        except discord.Forbidden:
            return await ctx.send(
                "❌ I don't have permission to post the schedule in this thread.",
                ephemeral=True,
            )
        except discord.HTTPException:
            return await ctx.send(
                "❌ I couldn't post the schedule message. Please try again.",
                ephemeral=True,
            )

        # Persist before telling the user that the event exists. The lock only
        # covers the short Config transaction; Discord HTTP calls stay outside.
        try:
            async with self._event_mutation_lock(ctx.guild.id):
                async with self.config.guild(ctx.guild).scheduled_events() as events:
                    events[str(msg.id)] = event_data
        except Exception:
            try:
                await msg.delete()
            except Exception:
                pass
            return await ctx.send(
                "❌ I couldn't save the schedule. Please try again.", ephemeral=True
            )

        try:
            await msg.add_reaction("✅")
        except (discord.Forbidden, discord.HTTPException):
            # The event is already persisted. The confirmation below makes
            # that clear and tells the organizer why reactions may be absent.
            reaction_note = " Reactions could not be added; ask an admin to check my permissions."
        else:
            reaction_note = ""

        timezone_note = (
            f" I used the default timezone `{DEFAULT_TIMEZONE}` because you have not set a personal timezone."
            if used_default_timezone
            else f" Parsed in `{timezone_name}`."
        )
        confirmation = (
            f"✅ **{game_title}** is scheduled for <t:{unix_timestamp}:F> "
            f"(<t:{unix_timestamp}:R>). [Open the event]({msg.jump_url})."
            f"{timezone_note}{reaction_note}"
        )
        controls = self._organizer_controls_text(ctx, msg)
        if ctx.interaction:
            await ctx.send(
                f"{confirmation}\n\n{controls}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await ctx.send(
                confirmation,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await self._send_prefix_organizer_controls(ctx, msg)

        # Sharing reads the latest event snapshot and is serialized with
        # reaction updates. The helper intentionally performs its Discord HTTP
        # calls while this lock is held, but never while its Config context is
        # open.
        async with self._event_mutation_lock(ctx.guild.id):
            async with self.config.guild(ctx.guild).scheduled_events() as events:
                latest_event = events.get(str(msg.id))
                latest_event = (
                    self._event_copy(latest_event)
                    if isinstance(latest_event, dict)
                    else None
                )
            if latest_event is not None:
                await self._share_schedule(
                    ctx.guild,
                    ctx.author,
                    msg,
                    latest_event,
                    remove_reaction_after_action=False,
                )

    async def _run_organizer_action(
        self, ctx: commands.Context, message_id: int, action: str
    ) -> tuple[dict | None, str | None]:
        """Run a private organizer action against the current event snapshot.

        Authorization and the action are serialized with reaction updates. This
        keeps the direct commands safe for new events while still accepting old
        Config records and old reaction events.
        """

        if action not in {"remind", "share"}:
            raise ValueError(f"Unknown organizer action: {action}")

        async with self._event_mutation_lock(ctx.guild.id):
            async with self.config.guild(ctx.guild).scheduled_events() as events:
                current = events.get(str(message_id))
                if not isinstance(current, dict):
                    return None, "I couldn't find a schedule with that message ID."
                if not (
                    self._same_id(current.get("organizer_id"), ctx.author.id)
                    or self._has_manage_guild(ctx.author)
                ):
                    return (
                        None,
                        "Only the event organizer or a member with Manage Server can do that.",
                    )
                if self._event_status(current) != "active":
                    return (
                        None,
                        f"This schedule is already {self._event_status(current)}.",
                    )
                event_data = self._event_copy(current)

            message = await self._fetch_event_message(
                ctx.guild, message_id, event_data
            )
            if message is None:
                return None, "I couldn't find the schedule message in Discord."

            if action == "remind":
                now = int(datetime.now(timezone.utc).timestamp())
                until_start = self._as_int(event_data.get("start_timestamp"), 0) - now
                if not 0 <= until_start <= self.REMINDER_WINDOW_SECONDS:
                    return None, "Reminders are available only during the 30 minutes before the event starts."
                last_reminder = self._as_int(
                    event_data.get(
                        "last_reminder_timestamp",
                        event_data.get("last_reminded_timestamp", 0),
                    ),
                    0,
                )
                if last_reminder and now - last_reminder < self.REMINDER_COOLDOWN_SECONDS:
                    remaining = max(1, (self.REMINDER_COOLDOWN_SECONDS - (now - last_reminder) + 59) // 60)
                    return None, f"A reminder was sent recently. Try again in about {remaining} minute(s)."
                await self._handle_reminder(
                    ctx.guild,
                    message,
                    event_data,
                    str(message_id),
                    ctx.author,
                    "❗",
                )
            else:
                if not await self.config.guild(ctx.guild).share_channel_id():
                    return None, "An administrator must configure the announcement channel first."
                last_shared = self._as_int(event_data.get("last_shared_timestamp"), 0)
                now = int(datetime.now(timezone.utc).timestamp())
                if last_shared and now - last_shared < 3600:
                    remaining = max(1, (3600 - (now - last_shared) + 59) // 60)
                    return None, f"This schedule was shared recently. Try again in about {remaining} minute(s)."
                await self._share_schedule(
                    ctx.guild,
                    ctx.author,
                    message,
                    event_data,
                    # This is a manual action. Enforce the per-event cooldown;
                    # removing a legacy reaction is harmless when none exists.
                    remove_reaction_after_action=True,
                )
            return event_data, None

    @commands.hybrid_command(name="schedulecontrol", aliases=["controls"])
    @commands.guild_only()
    @app_commands.describe(
        message_id="The schedule message ID or its Discord message URL."
    )
    async def controls(self, ctx: commands.Context, message_id: str):
        """Reopen the private organizer controls for an existing schedule."""

        message_id = normalize_message_id(message_id)
        if message_id is None:
            return await self._send_private_organizer_message(
                ctx,
                "❌ Please provide a valid Discord message ID or message URL.",
                "I couldn't process the organizer control request. Please use a valid schedule message ID.",
            )
        if ctx.interaction:
            await ctx.defer(ephemeral=True)
        event_data, error = await self._load_authorized_event(ctx, message_id)
        if error:
            return await self._send_private_organizer_message(
                ctx,
                f"❌ {error}",
                "I couldn't verify your organizer controls. Check the schedule message ID and your permissions.",
            )
        control_text = self._organizer_controls_text(
            ctx, SimpleNamespace(id=message_id)
        )
        return await self._send_private_organizer_message(
            ctx,
            control_text,
            "I couldn't DM your private organizer controls. Use the organizer-only schedule commands with the schedule message ID.",
        )

    @commands.hybrid_command(name="scheduleremind", aliases=["remind"])
    @commands.guild_only()
    @app_commands.describe(
        message_id="The schedule message ID or its Discord message URL."
    )
    async def remind(self, ctx: commands.Context, message_id: str):
        """Privately notify attendees of an active schedule near its start."""

        message_id = normalize_message_id(message_id)
        if message_id is None:
            return await self._send_private_organizer_message(
                ctx,
                "❌ Please provide a valid Discord message ID or message URL.",
                "I couldn't process the reminder request. Please use a valid schedule message ID.",
            )
        if ctx.interaction:
            await ctx.defer(ephemeral=True)
        _, error = await self._run_organizer_action(ctx, message_id, "remind")
        if error:
            return await self._send_private_organizer_message(
                ctx,
                f"❌ {error}",
                "I couldn't process the reminder request. Check the schedule ID and your permissions.",
            )
        await self._send_private_organizer_message(
            ctx,
            "✅ The reminder action was processed. Check your DM for delivery details.",
            "The reminder action was processed. Check your DMs for delivery details.",
        )

    @commands.hybrid_command(name="scheduleshare", aliases=["share"])
    @commands.guild_only()
    @app_commands.describe(
        message_id="The schedule message ID or its Discord message URL."
    )
    async def share(self, ctx: commands.Context, message_id: str):
        """Post an announcement for an active schedule."""

        message_id = normalize_message_id(message_id)
        if message_id is None:
            return await self._send_private_organizer_message(
                ctx,
                "❌ Please provide a valid Discord message ID or message URL.",
                "I couldn't process the announcement request. Please use a valid schedule message ID.",
            )
        if ctx.interaction:
            await ctx.defer(ephemeral=True)
        _, error = await self._run_organizer_action(ctx, message_id, "share")
        if error:
            return await self._send_private_organizer_message(
                ctx,
                f"❌ {error}",
                "I couldn't process the announcement request. Check the schedule ID and your permissions.",
            )
        await self._send_private_organizer_message(
            ctx,
            "✅ The schedule announcement action was processed.",
            "The schedule announcement action was processed.",
        )

    # ``cancel`` is reserved by Red's command framework. Keep explicit
    # schedule-prefixed names for slash/prefix registration and add the short
    # aliases that Red permits.
    @commands.hybrid_command(name="schedulereschedule", aliases=["reschedule", "resched"])
    @commands.guild_only()
    @app_commands.describe(
        message_id="The schedule message ID or its Discord message URL.",
        time_input="The new future start time, interpreted in the event timezone.",
    )
    async def reschedule(
        self, ctx: commands.Context, message_id: str, time_input: str
    ):
        """Change an active schedule's start time."""

        message_id = normalize_message_id(message_id)
        if message_id is None:
            return await ctx.send(
                "❌ Please provide a valid Discord message ID or message URL.",
                ephemeral=True,
            )
        if ctx.interaction:
            await ctx.defer(ephemeral=True)

        event_data, error = await self._load_authorized_event(ctx, message_id)
        if error:
            return await ctx.send(f"❌ {error}", ephemeral=True)
        if self._event_status(event_data) != "active":
            return await ctx.send(
                f"❌ This schedule is already {self._event_status(event_data)} and cannot be rescheduled.",
                ephemeral=True,
            )

        used_default_timezone = False
        timezone_name = event_data.get("timezone")
        if not timezone_name:
            timezone_name, used_default_timezone = await self._member_timezone(ctx.author)
        else:
            try:
                pytz.timezone(timezone_name)
            except (pytz.UnknownTimeZoneError, AttributeError, TypeError):
                timezone_name, used_default_timezone = await self._member_timezone(ctx.author)

        try:
            unix_timestamp, _ = parse_schedule_time(time_input, timezone_name)
        except ScheduleTimeError as exc:
            default_note = (
                f" The default timezone `{DEFAULT_TIMEZONE}` was used because no valid personal/event timezone was available."
                if used_default_timezone
                else ""
            )
            return await ctx.send(
                f"❌ {exc}{default_note}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        # Re-read and authorize under the lock so a concurrent cancel or finish
        # cannot be overwritten by this reschedule operation.
        update_error = None
        updated = None
        message_updated = False
        async with self._event_mutation_lock(ctx.guild.id):
            async with self.config.guild(ctx.guild).scheduled_events() as events:
                current = events.get(str(message_id))
                if not isinstance(current, dict):
                    update_error = "I couldn't find a schedule with that message ID."
                elif not (
                    self._same_id(current.get("organizer_id"), ctx.author.id)
                    or self._has_manage_guild(ctx.author)
                ):
                    update_error = "Only the event organizer or a member with Manage Server can do that."
                elif self._event_status(current) != "active":
                    update_error = (
                        f"This schedule is already {self._event_status(current)} and cannot be rescheduled."
                    )
                else:
                    updated = self._event_copy(current)
                    updated["start_timestamp"] = unix_timestamp
                    updated["timezone"] = timezone_name
                    updated["timezone_defaulted"] = used_default_timezone
                    updated["last_reminder_timestamp"] = 0
                    events[str(message_id)] = updated
            if updated is not None:
                message_updated = await self._update_event_message(
                    ctx.guild, message_id, updated
                )

        if update_error:
            return await ctx.send(f"❌ {update_error}", ephemeral=True)
        update_note = "" if message_updated else " The event was saved, but I couldn't update its Discord message."
        timezone_note = (
            f" The default timezone `{DEFAULT_TIMEZONE}` was used."
            if used_default_timezone
            else f" Parsed in `{timezone_name}`."
        )
        await ctx.send(
            f"✅ Schedule **{updated.get('game_title', 'Game session')}** rescheduled for <t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>).{timezone_note}{update_note}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _change_event_status(
        self, ctx: commands.Context, message_id: int, new_status: str
    ):
        """Persist cancellation/completion and then refresh the message."""

        async with self._event_mutation_lock(ctx.guild.id):
            async with self.config.guild(ctx.guild).scheduled_events() as events:
                current = events.get(str(message_id))
                if not isinstance(current, dict):
                    return None, "I couldn't find a schedule with that message ID.", False
                if not (
                    self._same_id(current.get("organizer_id"), ctx.author.id)
                    or self._has_manage_guild(ctx.author)
                ):
                    return (
                        None,
                        "Only the event organizer or a member with Manage Server can do that.",
                        False,
                    )
                old_status = self._event_status(current)
                if old_status != "active":
                    return None, f"This schedule is already {old_status}.", False
                updated = self._event_copy(current)
                updated["status"] = new_status
                updated["status_timestamp"] = int(datetime.now(timezone.utc).timestamp())
                events[str(message_id)] = updated
            message_updated = await self._update_event_message(
                ctx.guild, message_id, updated
            )
            return updated, None, message_updated

    @commands.hybrid_command(name="schedulecancel")
    @commands.guild_only()
    @app_commands.describe(message_id="The schedule message ID or its Discord message URL.")
    async def cancel(self, ctx: commands.Context, message_id: str):
        """Cancel an active schedule."""

        message_id = normalize_message_id(message_id)
        if message_id is None:
            return await ctx.send(
                "❌ Please provide a valid Discord message ID or message URL.",
                ephemeral=True,
            )
        if ctx.interaction:
            await ctx.defer(ephemeral=True)
        event_data, error, message_updated = await self._change_event_status(
            ctx, message_id, "cancelled"
        )
        if error:
            return await ctx.send(f"❌ {error}", ephemeral=True)
        update_note = "" if message_updated else " The event was saved, but its message could not be updated."
        await ctx.send(
            f"✅ Schedule **{event_data.get('game_title', 'Game session')}** cancelled.{update_note}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.hybrid_command(name="schedulefinish", aliases=["finish"])
    @commands.guild_only()
    @app_commands.describe(message_id="The schedule message ID or its Discord message URL.")
    async def finish(self, ctx: commands.Context, message_id: str):
        """Mark an active schedule as finished."""

        message_id = normalize_message_id(message_id)
        if message_id is None:
            return await ctx.send(
                "❌ Please provide a valid Discord message ID or message URL.",
                ephemeral=True,
            )
        if ctx.interaction:
            await ctx.defer(ephemeral=True)
        event_data, error, message_updated = await self._change_event_status(
            ctx, message_id, "finished"
        )
        if error:
            return await ctx.send(f"❌ {error}", ephemeral=True)
        update_note = "" if message_updated else " The event was saved, but its message could not be updated."
        await ctx.send(
            f"✅ Schedule **{event_data.get('game_title', 'Game session')}** marked finished.{update_note}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.hybrid_command(name="upcoming", aliases=["schedulelist", "upcomingschedules"])
    @commands.guild_only()
    async def upcoming(self, ctx: commands.Context):
        """List active schedules whose start time is still in the future."""

        if ctx.interaction:
            await ctx.defer(ephemeral=True)

        async with self.config.guild(ctx.guild).scheduled_events() as events:
            candidates = []
            for message_id, raw_event in events.items():
                if not isinstance(raw_event, dict) or self._event_status(raw_event) != "active":
                    continue
                channel_id = raw_event.get("channel_id")
                get_channel = getattr(ctx.guild, "get_channel_or_thread", None)
                channel = get_channel(channel_id) if callable(get_channel) else None
                if channel is None:
                    channel = ctx.guild.get_channel(channel_id)
                permissions_for = getattr(channel, "permissions_for", None)
                if channel is None or not callable(permissions_for):
                    continue
                if not permissions_for(ctx.author).view_channel:
                    continue
                try:
                    start_timestamp = int(raw_event["start_timestamp"])
                    numeric_message_id = int(message_id)
                except (KeyError, TypeError, ValueError):
                    continue
                candidates.append((start_timestamp, numeric_message_id, self._event_copy(raw_event)))

        now_timestamp = int(datetime.now(timezone.utc).timestamp())
        candidates = [item for item in candidates if item[0] > now_timestamp]
        candidates.sort(key=lambda item: item[0])
        candidates = candidates[:MAX_UPCOMING_EVENTS]

        if not candidates:
            return await ctx.send("There are no upcoming active schedules.", ephemeral=True)

        lines = []
        for start_timestamp, message_id, event_data in candidates:
            title = str(event_data.get("game_title") or "Game session")
            safe_title = title.replace("\\", "\\\\").replace("]", "\\]")
            organizer_id = event_data.get("organizer_id")
            organizer = f"<@{organizer_id}>" if organizer_id else "Unknown organizer"
            channel_id = event_data.get("channel_id")
            jump_url = (
                f"https://discord.com/channels/{ctx.guild.id}/{channel_id}/{message_id}"
                if channel_id
                else None
            )
            title_text = f"[{safe_title}]({jump_url})" if jump_url else safe_title
            lines.append(
                f"**{title_text}** — <t:{start_timestamp}:F> (<t:{start_timestamp}:R>)\n"
                f"Organizer: {organizer} • {len(event_data.get('attendees', []))}/{event_data.get('player_limit', '?')} players"
            )

        embed = discord.Embed(
            title="Upcoming game schedules",
            description="\n\n".join(lines)[:4096],
            color=discord.Color.blue(),
        )
        await ctx.send(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
