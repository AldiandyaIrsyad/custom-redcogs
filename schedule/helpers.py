import discord
from datetime import datetime, timezone
from redbot.core.bot import Red


class ScheduleHelpers:
    """
    Helper methods for the Schedule cog.
    """

    bot: Red

    async def _share_schedule(
        self,
        guild: discord.Guild,
        user_who_triggered: discord.User,
        original_schedule_message: discord.Message,
        event_data: dict,
        remove_reaction_after_action: bool = True,
    ):
        """
        Handles the logic for sharing a schedule to the designated share channel.
        """
        try:
            share_channel_id = await self.config.guild(guild).share_channel_id()
            if not share_channel_id:
                try:
                    await user_who_triggered.send(
                        "The share channel has not been set up for this server. Please ask an admin to use `[p]scheduleset sharechannel`."
                    )
                except discord.Forbidden:
                    pass  # Cannot DM user
                return

            share_channel = guild.get_channel(share_channel_id)
            if not share_channel or not isinstance(share_channel, discord.TextChannel):
                try:
                    await user_who_triggered.send(
                        "The configured share channel is invalid or no longer accessible. Please inform an admin."
                    )
                except discord.Forbidden:
                    pass
                return

            # Cooldown check for sharing: 1 hour
            last_shared = event_data.get("last_shared_timestamp", 0)
            current_time = int(datetime.now(timezone.utc).timestamp())

            # Only apply cooldown for manual shares
            if (
                remove_reaction_after_action
                and last_shared != 0
                and current_time - last_shared < 3600
            ):
                minutes_remaining = (3600 - (current_time - last_shared)) // 60
                try:
                    await user_who_triggered.send(
                        f"This schedule was shared recently. Please try again in about {minutes_remaining} minute(s)."
                    )
                except discord.Forbidden:
                    pass
                return

            # Proceed to share the event
            game_title_for_share = event_data.get("game_title", "A Game")
            player_limit = event_data.get("player_limit", 0)
            current_attendees_count = len(event_data.get("attendees", []))
            event_tags = event_data.get("tags", [])

            description_text = (
                f"**Organizer**: {user_who_triggered.mention}\n"
                f"**Title**: {game_title_for_share}\n"
                f"**Starts**: <t:{event_data['start_timestamp']}:F> (<t:{event_data['start_timestamp']}:R>)\n"
            )
            if event_tags:
                tags_formatted = " ".join([f"`{tag}`" for tag in event_tags])
                description_text += f"**Tags**: {tags_formatted}\n"

            if current_attendees_count < player_limit:
                description_text += (
                    f"**Slots**: {current_attendees_count} / {player_limit}\n"
                )
            else:
                description_text += "**Lobby is full!**\n"

            description_text += (
                f"\n[Click here to join the session]({original_schedule_message.jump_url})"
            )

            share_embed = discord.Embed(
                title=f"📢 Game Announcement: {game_title_for_share}",
                description=description_text,
                color=discord.Color.green(),
            )
            if event_data.get("description"):
                share_embed.add_field(
                    name="Description", value=event_data["description"], inline=False
                )

            try:
                await share_channel.send(embed=share_embed)
                # Update last_shared_timestamp in the config
                async with self.config.guild(guild).scheduled_events() as events_config:
                    if str(original_schedule_message.id) in events_config:
                        events_config[str(original_schedule_message.id)][
                            "last_shared_timestamp"
                        ] = current_time

                # Send confirmation only for manual shares
                if remove_reaction_after_action:
                    try:
                        await user_who_triggered.send(
                            f"✅ Successfully shared '{game_title_for_share}' to {share_channel.mention}!"
                        )
                    except discord.Forbidden:
                        pass
            except discord.Forbidden:
                if remove_reaction_after_action:
                    try:
                        await user_who_triggered.send(
                            f"I don't have permission to send messages in {share_channel.mention}. Please inform an admin."
                        )
                    except discord.Forbidden:
                        pass
            except Exception as e:
                self.bot.log.error(
                    f"Failed to share schedule (msg_id: {original_schedule_message.id}): {e}"
                )
                if remove_reaction_after_action:
                    try:
                        await user_who_triggered.send(
                            "An error occurred while trying to share the schedule."
                        )
                    except discord.Forbidden:
                        pass
        finally:
            if remove_reaction_after_action:
                try:
                    await original_schedule_message.remove_reaction(
                        "📢", user_who_triggered
                    )
                except (discord.Forbidden, discord.NotFound):
                    pass

    async def _update_embed(self, message: discord.Message, event_data: dict):
        """
        Updates the schedule message embed with the latest event data.
        """
        unix_timestamp = event_data["start_timestamp"]
        game_title_for_embed = event_data.get("game_title", "Unknown Game")
        description_for_embed = event_data.get("description")

        new_embed = discord.Embed(
            title=f"Game Session: {game_title_for_embed}",
            description=f"Starts <t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)",
            color=discord.Color.blue(),
        )
        if description_for_embed:
            new_embed.add_field(
                name="Description", value=description_for_embed, inline=False
            )

        new_embed.add_field(
            name="Lobby",
            value=f"{len(event_data['attendees'])} / {event_data['player_limit']}",
            inline=True,
        )

        organizer = message.guild.get_member(event_data["organizer_id"])
        new_embed.add_field(
            name="Organizer",
            value=organizer.mention if organizer else "Unknown",
            inline=True,
        )

        player_mentions = " ".join([f"<@{uid}>" for uid in event_data["attendees"]])
        new_embed.add_field(
            name="Players",
            value=player_mentions or "No one has joined yet.",
            inline=False,
        )

        new_embed.set_footer(
            text="✅ Join/Leave | Organizer: ❗ Remind (within 30 mins prior) | 📢 Share"
        )

        await message.edit(embed=new_embed)
