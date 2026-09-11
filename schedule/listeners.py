import discord
from redbot.core import commands
from datetime import datetime, timezone


class ScheduleListeners:
    """
    Listeners and reaction handling for the Schedule cog.
    """

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """
        Handles raw reaction add events to manage schedule interactions.
        """
        await self._handle_reaction(payload, "add")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """
        Handles raw reaction remove events to manage schedule interactions.
        """
        await self._handle_reaction(payload, "remove")

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent, action: str):
        """
        Core logic for handling reactions on schedule messages.
        """
        if payload.guild_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        try:
            user = await guild.fetch_member(payload.user_id)
            channel = await self.bot.fetch_channel(payload.channel_id)
        except discord.NotFound:
            return

        if not user or user.bot:
            return

        async with self.config.guild(guild).scheduled_events() as events:
            event_data = events.get(str(payload.message_id))
            if not event_data:
                return

            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound:
                if str(payload.message_id) in events:
                    del events[str(payload.message_id)]
                return

            if str(payload.emoji) == "✅":
                attendees = event_data["attendees"]
                limit = event_data["player_limit"]

                if action == "add":
                    if user.id not in attendees and len(attendees) < limit:
                        attendees.append(user.id)
                    elif user.id not in attendees:
                        try:
                            await message.remove_reaction(payload.emoji, user)
                        except (discord.Forbidden, discord.NotFound):
                            pass
                        return
                elif action == "remove":
                    if user.id in attendees and user.id != event_data["organizer_id"]:
                        attendees.remove(user.id)
                    elif user.id == event_data["organizer_id"]:
                        try:
                            await message.add_reaction(payload.emoji)
                        except (discord.Forbidden, discord.NotFound):
                            pass
                        return

                await self._update_embed(message, event_data)

            elif str(payload.emoji) == "❗" and action == "add":
                if user.id == event_data["organizer_id"]:
                    now_ts = datetime.now(timezone.utc).timestamp()
                    start_time_ts = event_data["start_timestamp"]

                    if (start_time_ts - 1800) < now_ts:
                        game_title_for_reminder = event_data.get("game_title", "The Game")
                        start_timestamp = event_data["start_timestamp"]
                        reminder_message_text_dm = f"**Reminder for {game_title_for_reminder}!**\nThe game is starting <t:{start_timestamp}:F> (<t:{start_timestamp}:R>)!"

                        dms_sent_count = 0
                        dms_failed_count = 0

                        for attendee_id in event_data["attendees"]:
                            try:
                                member = await guild.fetch_member(attendee_id)
                                await member.send(reminder_message_text_dm)
                                dms_sent_count += 1
                            except discord.Forbidden:
                                dms_failed_count += 1
                            except discord.NotFound:
                                dms_failed_count += 1
                            except Exception as e:
                                self.bot.log.error(
                                    f"Failed to send reminder DM to {attendee_id}: {e}"
                                )
                                dms_failed_count += 1

                        channel_reminder_sent = False
                        if hasattr(channel, "send"):
                            try:
                                await channel.send(
                                    f"**Reminder for all participants of '{game_title_for_reminder}'!** The game starts <t:{start_timestamp}:R>."
                                )
                                channel_reminder_sent = True
                            except discord.Forbidden:
                                self.bot.log.error(
                                    f"Failed to send channel reminder for event {message.id} in channel {channel.id}: No permission."
                                )
                            except Exception as e:
                                self.bot.log.error(
                                    f"Failed to send channel reminder for event {message.id}: {e}"
                                )

                        organizer_confirmation_message = f"Reminder process for '{game_title_for_reminder}' complete.\n"
                        organizer_confirmation_message += (
                            f"- Sent {dms_sent_count} reminder DMs."
                        )
                        if dms_failed_count > 0:
                            organizer_confirmation_message += f" Failed to send {dms_failed_count} DMs (users may have DMs disabled or blocked)."
                        if channel_reminder_sent:
                            organizer_confirmation_message += (
                                "\n- A public reminder was sent to the event channel."
                            )
                        else:
                            organizer_confirmation_message += "\n- Failed to send a public reminder to the event channel."

                        try:
                            await user.send(organizer_confirmation_message)
                        except discord.Forbidden:
                            pass

                        try:
                            await message.remove_reaction(payload.emoji, user)
                        except (discord.Forbidden, discord.NotFound):
                            pass
                    else:
                        try:
                            await message.remove_reaction(payload.emoji, user)
                        except (discord.Forbidden, discord.NotFound):
                            pass
                        return
                else:
                    try:
                        await message.remove_reaction(payload.emoji, user)
                    except (discord.Forbidden, discord.NotFound):
                        pass
                    return

            elif str(payload.emoji) == "📢" and action == "add":
                await self._share_schedule(
                    guild, user, message, event_data, remove_reaction_after_action=True
                )
