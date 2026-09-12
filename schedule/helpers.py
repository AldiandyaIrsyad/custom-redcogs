import logging
from datetime import datetime, timezone

import discord
from redbot.core.bot import Red


schedule_logger = logging.getLogger("red.custom.schedule")


class ScheduleHelpers:
    """Helper methods for sharing and rendering scheduled events."""

    bot: Red

    MAX_TITLE_LENGTH = 256
    MAX_DESCRIPTION_LENGTH = 4096
    MAX_FIELD_LENGTH = 1024
    MAX_FOOTER_LENGTH = 2048

    async def _share_schedule(
        self,
        guild: discord.Guild,
        user_who_triggered: discord.User,
        original_schedule_message: discord.Message,
        event_data: dict,
        remove_reaction_after_action: bool = True,
    ):
        """
        Share a schedule to the configured channel.

        The caller owns the per-guild event lock. This helper never acquires
        that lock, and it does not keep a Config context open across Discord
        HTTP calls. The event's organizer is always used for attribution;
        ``user_who_triggered`` is only the person requesting the share.
        """
        try:
            if self._event_status(event_data) != "active":
                if remove_reaction_after_action:
                    await self._safe_dm(
                        user_who_triggered,
                        "This schedule is no longer active and cannot be shared.",
                        "send inactive-share DM",
                    )
                return

            share_channel_id = await self.config.guild(guild).share_channel_id()
            if not share_channel_id:
                if remove_reaction_after_action:
                    await self._safe_dm(
                        user_who_triggered,
                        "The share channel has not been set up for this server. Please ask an admin to use `[p]scheduleset sharechannel`.",
                        "send missing-share-channel DM",
                    )
                return

            share_channel = await self._resolve_share_channel(guild, share_channel_id)
            if share_channel is None or not hasattr(share_channel, "send"):
                if remove_reaction_after_action:
                    await self._safe_dm(
                        user_who_triggered,
                        "The configured share channel is invalid or no longer accessible. Please inform an admin.",
                        "send invalid-share-channel DM",
                    )
                return

            # Cooldown applies to manual reactions only. Automatic sharing at
            # creation time remains available to publish the initial event.
            last_shared = self._as_int(event_data.get("last_shared_timestamp"), 0)
            current_time = int(datetime.now(timezone.utc).timestamp())
            if (
                remove_reaction_after_action
                and last_shared
                and current_time - last_shared < 3600
            ):
                minutes_remaining = max(1, (3600 - (current_time - last_shared) + 59) // 60)
                if remove_reaction_after_action:
                    await self._safe_dm(
                        user_who_triggered,
                        f"This schedule was shared recently. Please try again in about {minutes_remaining} minute(s).",
                        "send share cooldown DM",
                    )
                return

            share_embed = self._build_share_embed(
                event_data, original_schedule_message
            )
            try:
                await share_channel.send(
                    embed=share_embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.Forbidden as exc:
                self._log_http_error(
                    "share schedule",
                    exc,
                    guild_id=getattr(guild, "id", None),
                    channel_id=share_channel_id,
                    message_id=getattr(original_schedule_message, "id", None),
                )
                if remove_reaction_after_action:
                    await self._safe_dm(
                        user_who_triggered,
                        f"I don't have permission to send messages in {getattr(share_channel, 'mention', 'the share channel')}. Please inform an admin.",
                        "send share permission DM",
                    )
                return
            except discord.NotFound as exc:
                self._log_http_error(
                    "share schedule",
                    exc,
                    guild_id=getattr(guild, "id", None),
                    channel_id=share_channel_id,
                    message_id=getattr(original_schedule_message, "id", None),
                )
                if remove_reaction_after_action:
                    await self._safe_dm(
                        user_who_triggered,
                        "The configured share channel could not be found. Please inform an admin.",
                        "send share missing-channel DM",
                    )
                return
            except discord.HTTPException as exc:
                self._log_http_error(
                    "share schedule",
                    exc,
                    guild_id=getattr(guild, "id", None),
                    channel_id=share_channel_id,
                    message_id=getattr(original_schedule_message, "id", None),
                )
                if remove_reaction_after_action:
                    await self._safe_dm(
                        user_who_triggered,
                        "An error occurred while trying to share the schedule.",
                        "send share failure DM",
                    )
                return

            # No Config context is held while share_channel.send runs. Save
            # only after Discord accepted the share so failed sends don't
            # consume the manual cooldown.
            event_key = str(original_schedule_message.id)
            event_data["last_shared_timestamp"] = current_time
            async with self.config.guild(guild).scheduled_events() as events:
                current_event = events.get(event_key)
                if current_event is not None:
                    current_event["last_shared_timestamp"] = current_time

            if remove_reaction_after_action:
                await self._safe_dm(
                    user_who_triggered,
                    f"✅ Successfully shared '{self._event_title(event_data)}' to {getattr(share_channel, 'mention', 'the share channel')}!",
                    "send share confirmation DM",
                )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            # Covers channel resolution and any Discord API call that was not
            # handled at its narrow call site. Keep the listener alive.
            self._log_http_error(
                "share schedule",
                exc,
                guild_id=getattr(guild, "id", None),
                message_id=getattr(original_schedule_message, "id", None),
            )
            if remove_reaction_after_action:
                if remove_reaction_after_action:
                    await self._safe_dm(
                        user_who_triggered,
                        "An error occurred while trying to share the schedule.",
                        "send share unexpected-failure DM",
                    )
        except Exception as exc:
            self._log_exception(
                "Unexpected error while sharing schedule",
                exc,
                guild_id=getattr(guild, "id", None),
                message_id=getattr(original_schedule_message, "id", None),
            )
            if remove_reaction_after_action:
                if remove_reaction_after_action:
                    await self._safe_dm(
                        user_who_triggered,
                        "An error occurred while trying to share the schedule.",
                        "send share unexpected-failure DM",
                    )
        finally:
            if remove_reaction_after_action:
                await self._remove_reaction(
                    original_schedule_message, "📢", user_who_triggered
                )

    async def _resolve_share_channel(self, guild: discord.Guild, channel_id: int):
        """Resolve a configured share channel from cache or via Discord."""
        get_channel = getattr(guild, "get_channel", None)
        share_channel = get_channel(channel_id) if get_channel is not None else None
        if share_channel is not None:
            return share_channel

        fetch_channel = getattr(guild, "fetch_channel", None)
        if fetch_channel is None:
            fetch_channel = getattr(self.bot, "fetch_channel", None)
        if fetch_channel is None:
            return None
        try:
            return await fetch_channel(channel_id)
        except discord.NotFound as exc:
            self._log_http_error("resolve share channel", exc, channel_id=channel_id)
            return None
        except discord.Forbidden as exc:
            self._log_http_error("resolve share channel", exc, channel_id=channel_id)
            return None
        except discord.HTTPException as exc:
            self._log_http_error("resolve share channel", exc, channel_id=channel_id)
            return None

    def _build_share_embed(self, event_data: dict, original_schedule_message):
        """Build a bounded announcement embed for the configured share channel."""
        title = self._event_title(event_data)
        organizer_id = event_data.get("organizer_id")
        organizer = self._mention(organizer_id)
        start_timestamp = self._as_int(event_data.get("start_timestamp"), 0)
        attendees = list(event_data.get("attendees") or [])
        player_limit = self._as_int(event_data.get("player_limit"), 0)
        lines = [
            f"**Organizer**: {organizer}",
            f"**Title**: {title}",
            f"**Starts**: <t:{start_timestamp}:F> (<t:{start_timestamp}:R>)",
        ]

        tags = event_data.get("tags") or []
        if tags:
            tags_formatted = " ".join(f"`{str(tag)}`" for tag in tags)
            lines.append(f"**Tags**: {tags_formatted}")

        if len(attendees) < player_limit:
            lines.append(f"**Slots**: {len(attendees)} / {player_limit}")
        else:
            lines.append("**Lobby is full!**")

        jump_url = getattr(original_schedule_message, "jump_url", "")
        if jump_url:
            lines.append(f"\n[Click here to join the session]({jump_url})")
        description = self._truncate("\n".join(lines), self.MAX_DESCRIPTION_LENGTH)
        share_embed = discord.Embed(
            title=self._truncate(f"📢 Game Announcement: {title}", self.MAX_TITLE_LENGTH),
            description=description,
            color=discord.Color.green(),
        )
        status = self._event_status(event_data)
        if status != "active":
            share_embed.add_field(
                name="Status",
                value=status.capitalize(),
                inline=True,
            )
        event_description = event_data.get("description")
        if event_description:
            share_embed.add_field(
                name="Description",
                value=self._truncate(str(event_description), self.MAX_FIELD_LENGTH),
                inline=False,
            )
        return share_embed

    def _build_embed(self, event_data: dict, organizer_mention: str = None):
        """Build the event message embed within Discord's size limits."""
        start_timestamp = self._as_int(event_data.get("start_timestamp"), 0)
        title = self._event_title(event_data)
        description = f"Starts <t:{start_timestamp}:F> (<t:{start_timestamp}:R>)"
        status = self._event_status(event_data)
        embed_color = discord.Color.blue()
        if status == "cancelled":
            embed_color = discord.Color.red()
        elif status == "finished":
            embed_color = discord.Color.greyple()
        new_embed = discord.Embed(
            title=self._truncate(f"Game Session: {title}", self.MAX_TITLE_LENGTH),
            description=self._truncate(description, self.MAX_DESCRIPTION_LENGTH),
            color=embed_color,
        )

        event_description = event_data.get("description")
        if event_description:
            new_embed.add_field(
                name="Description",
                value=self._truncate(str(event_description), self.MAX_FIELD_LENGTH),
                inline=False,
            )

        attendees = list(event_data.get("attendees") or [])
        limit = self._as_int(event_data.get("player_limit"), 0)
        new_embed.add_field(
            name="Lobby",
            value=self._truncate(f"{len(attendees)} / {limit}", self.MAX_FIELD_LENGTH),
            inline=True,
        )

        if organizer_mention is None:
            organizer_mention = self._mention(event_data.get("organizer_id"))
        new_embed.add_field(
            name="Organizer",
            value=self._truncate(organizer_mention or "Unknown", self.MAX_FIELD_LENGTH),
            inline=True,
        )

        if status != "active":
            new_embed.add_field(
                name="Status",
                value=status.capitalize(),
                inline=True,
            )

        player_mentions = " ".join(self._mention(uid) for uid in attendees)
        new_embed.add_field(
            name="Players",
            value=self._truncate(player_mentions or "No one has joined yet.", self.MAX_FIELD_LENGTH),
            inline=False,
        )
        if status == "active":
            footer = "✅ Join/Leave | Organizer: ❗ Remind (within 30 mins prior) | 📢 Share"
        else:
            footer = f"{status.capitalize()} | Reactions are disabled for this schedule"
        new_embed.set_footer(text=self._truncate(footer, self.MAX_FOOTER_LENGTH))
        return new_embed

    async def _update_embed(self, message: discord.Message, event_data: dict):
        """Update the schedule message with bounded event data."""
        organizer_mention = None
        guild = getattr(message, "guild", None)
        organizer_id = event_data.get("organizer_id")
        if guild is not None:
            get_member = getattr(guild, "get_member", None)
            organizer = get_member(organizer_id) if get_member is not None else None
            if organizer is not None:
                organizer_mention = getattr(organizer, "mention", None)
        await message.edit(embed=self._build_embed(event_data, organizer_mention))

    async def _remove_reaction(self, message, emoji, user):
        if user is None:
            return
        target = user
        if isinstance(user, int):
            target = discord.Object(id=user)
        try:
            await message.remove_reaction(emoji, target)
        except discord.NotFound:
            return
        except discord.Forbidden as exc:
            self._log_http_error(
                "remove schedule reaction", exc, message_id=getattr(message, "id", None)
            )
        except discord.HTTPException as exc:
            self._log_http_error(
                "remove schedule reaction", exc, message_id=getattr(message, "id", None)
            )

    async def _safe_dm(self, user, content: str, operation: str):
        if user is None or not hasattr(user, "send"):
            return
        try:
            await user.send(content)
        except discord.Forbidden as exc:
            self._log_http_error(operation, exc, user_id=getattr(user, "id", None))
        except discord.NotFound as exc:
            self._log_http_error(operation, exc, user_id=getattr(user, "id", None))
        except discord.HTTPException as exc:
            self._log_http_error(operation, exc, user_id=getattr(user, "id", None))
        except Exception as exc:
            self._log_exception(operation, exc, user_id=getattr(user, "id", None))

    def _log_http_error(self, operation: str, error, **context):
        details = ", ".join(f"{key}={value}" for key, value in context.items())
        status = getattr(error, "status", None)
        code = getattr(error, "code", None)
        if status is not None:
            details = f"{details}, " if details else ""
            details += f"status={status}"
        if code is not None:
            details = f"{details}, " if details else ""
            details += f"code={code}"
        message = f"Discord HTTP error during {operation}"
        if details:
            message += f" ({details})"
        schedule_logger.error(message)

    def _log_exception(self, operation: str, error, **context):
        details = ", ".join(f"{key}={value}" for key, value in context.items())
        message = f"{operation}: {error}"
        if details:
            message += f" ({details})"
        schedule_logger.exception(message)

    @staticmethod
    def _event_title(event_data: dict) -> str:
        return str(event_data.get("game_title") or "A Game")

    @staticmethod
    def _mention(user_id) -> str:
        return f"<@{user_id}>" if user_id is not None else "Unknown"

    @staticmethod
    def _truncate(value, limit: int) -> str:
        value = str(value)
        if len(value) <= limit:
            return value
        if limit <= 1:
            return value[:limit]
        return value[: limit - 1] + "…"

    @staticmethod
    def _as_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _same_id(left, right) -> bool:
        return left is not None and right is not None and str(left) == str(right)

    @staticmethod
    def _event_status(event_data: dict) -> str:
        status = str(event_data.get("status") or "active").lower()
        return status if status in {"active", "cancelled", "finished"} else "active"
