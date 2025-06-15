import discord
import asyncio
import dateparser
import pytz
from datetime import datetime, timezone
from discord import app_commands
from redbot.core import commands, Config
from redbot.core.bot import Red

class Schedule(commands.Cog):
    """
    A cog to schedule games in forum posts with timezone support and reaction-based sign-ups.

    This cog provides commands to:
    - Set a target forum channel for scheduling.
    - Set a target channel for sharing scheduled events.
    - Allow users to set their personal timezone.
    - Schedule new game events with details like player count, time, title, and description.
    - Handle reactions on event messages for joining/leaving, reminding, and sharing.
    """

    def __init__(self, bot: Red):
        """
        Initializes the Schedule cog.

        Args:
            bot: The Redbot instance.
        """
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)

        default_guild = {
            "target_forum_id": None,
            "scheduled_events": {}, # {message_id: {data}}
            "share_channel_id": None, # Added for share feature
        }
        default_member = {
            "timezone": None
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

    @commands.hybrid_group(name="scheduleset", aliases=["ss"])
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def schedule_set(self, ctx: commands.Context):
        """
        Base command for schedule configuration.

        This command group contains subcommands for setting up
        the scheduling system within a guild.
        """
        pass

    @schedule_set.command(name="forum")
    @app_commands.describe(forum="The forum channel where schedules will be created.")
    async def set_forum(self, ctx: commands.Context, forum: discord.ForumChannel):
        """
        Sets the designated forum channel for creating new game schedules.

        Args:
            ctx: The command context.
            forum: The forum channel to be used for scheduling.
        """
        await self.config.guild(ctx.guild).target_forum_id.set(forum.id)
        await ctx.send(f"✅ The scheduling forum has been set to {forum.mention}.")

    @schedule_set.command(name="sharechannel")
    @app_commands.describe(channel="The text channel where shared schedules will be posted.")
    async def set_share_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Sets the designated text channel for sharing scheduled game announcements.

        Args:
            ctx: The command context.
            channel: The text channel to be used for sharing.
        """
        await self.config.guild(ctx.guild).share_channel_id.set(channel.id)
        await ctx.send(f"✅ The schedule sharing channel has been set to {channel.mention}.")

    # Corrected with the app_commands.describe decorator
    @commands.hybrid_command()
    @commands.guild_only()
    @app_commands.describe(
        timezone_str="Your timezone name (e.g., 'Asia/Jakarta' or 'America/New_York')."
    )
    async def settimezone(self, ctx: commands.Context, timezone_str: str):
        """
        Sets your personal timezone for scheduling.

        This timezone will be used to correctly parse time inputs when you schedule an event.
        It uses TZ database names (e.g., 'Asia/Jakarta', 'America/New_York').

        Args:
            ctx: The command context.
            timezone_str: The TZ database name of the user's timezone.
        """
        try:
            tz = pytz.timezone(timezone_str)
            await self.config.member(ctx.author).timezone.set(str(tz))
            await ctx.send(f"✅ Your timezone has been set to `{tz}`.")
        except pytz.UnknownTimeZoneError:
            await ctx.send(
                f"❌ Invalid timezone: `{timezone_str}`. Please use a valid TZ database name.\n"
                "Find your timezone here: <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>"
            )

    @commands.hybrid_command()
    @commands.guild_only()
    @app_commands.describe(
        player_amount="The maximum number of players for the lobby.",
        time_input="When the game starts (e.g., 'in 2 hours', 'at 10pm', 'thursday at 10pm').",
        title="Optional: A custom title for the game session.",
        description="Optional: A description for the game session."
    )
    async def schedule(self, ctx: commands.Context, player_amount: int, time_input: str, title: str = None, description: str = None):
        """
        Schedules a game session within a designated forum post.

        This command allows users to create a new game event.
        It requires the number of players and a time input (e.g., "in 2 hours", "tomorrow at 5pm").
        An optional title and description can also be provided.
        The event is posted as an embed in the current thread (if it's within the configured forum).

        Args:
            ctx: The command context.
            player_amount: The maximum number of players for the game.
            time_input: A natural language string describing when the game starts.
            title: An optional custom title for the game session.
            description: An optional description for the game session.
        """
        # When used as a slash command, defer the response to avoid "thinking..." messages
        if ctx.interaction:
            await ctx.defer(ephemeral=True)

        target_forum_id = await self.config.guild(ctx.guild).target_forum_id()
        if not target_forum_id:
            return await ctx.send("The scheduling forum has not been set. An admin must use `[p]scheduleset forum`.", ephemeral=True)

        # Ensure the command is used within a thread of the target forum
        if not isinstance(ctx.channel, discord.Thread) or ctx.channel.parent_id != target_forum_id:
            return await ctx.send("This command can only be used inside a thread of the designated scheduling forum.", ephemeral=True)

        if player_amount <= 0:
            return await ctx.send("Player amount must be a positive number.", ephemeral=True)

        # Get user's timezone, default to "Asia/Jakarta" if not set
        user_tz_str = await self.config.member(ctx.author).timezone()
        tz = user_tz_str or "Asia/Jakarta" # Default timezone
        
        # Configure dateparser settings
        settings = {'PREFER_DATES_FROM': 'future', 'TIMEZONE': tz, 'RETURN_AS_TIMEZONE_AWARE': True}
        parsed_time = dateparser.parse(time_input, settings=settings)

        if not parsed_time:
            return await ctx.send(f"Sorry, I couldn't understand the time: `{time_input}`.", ephemeral=True)
        
        # Convert parsed time to a Unix timestamp
        unix_timestamp = int(parsed_time.timestamp())
        # Use provided title or fall back to the thread's name
        game_title = title if title else ctx.channel.name # Use provided title or channel name
        
        # Create the initial embed for the schedule
        embed = discord.Embed(
            title=f"Game Session: {game_title}",
            description=f"Starts <t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)",
            color=discord.Color.blue()
        )
        if description: # Add description field if provided
            embed.add_field(name="Description", value=description, inline=False)
        embed.add_field(name="Lobby", value=f"1 / {player_amount}", inline=True)
        embed.add_field(name="Organizer", value=ctx.author.mention, inline=True)
        embed.add_field(name="Players", value=ctx.author.mention, inline=False)
        embed.set_footer(text="✅ Join/Leave | Organizer: ❗ Remind | 📢 Share") # Updated footer

        # For hybrid commands, we send the public message to the channel, not as a reply
        msg = await ctx.channel.send(embed=embed) 
        
        # Then, we send a confirmation to the user who ran the command
        await ctx.send(f"✅ Your event for **{game_title}** has been scheduled!", ephemeral=True)
        
        # Get applied tags from the thread
        # thread_tags = [tag.name for tag in ctx.channel.applied_tags]
        # add emoji to the thread tags
        thread_tags = [f'{str(tag.emoji)}{tag.name}' if tag.emoji else f'{tag.name}' for tag in ctx.channel.applied_tags] if ctx.channel.applied_tags else [] # Get applied tags from the thread, default to empty list if none
        

        event_data = {
            "organizer_id": ctx.author.id,
            "player_limit": player_amount,
            "game_title": game_title, # Store the potentially custom title
            "description": description, # Store the description
            "start_timestamp": unix_timestamp,
            "channel_id": ctx.channel.id,
            "attendees": [ctx.author.id],
            "last_shared_timestamp": 0, # Added for share feature
            "tags": thread_tags, # Store the thread tags
        }

        # Store event data in config, keyed by the message ID
        async with self.config.guild(ctx.guild).scheduled_events() as events:
            events[str(msg.id)] = event_data

        # Add initial reactions for user interaction
        await msg.add_reaction("✅") # Join/Leave
        await msg.add_reaction("❗") # ADDED reaction for reminder
        await msg.add_reaction("📢") # Added reaction for sharing

        # Automatically share the event for the first time
        # The event_data in config will be updated by _share_schedule if successful
        await self._share_schedule(ctx.guild, ctx.author, msg, events[str(msg.id)], remove_reaction_after_action=False)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """
        Handles raw reaction add events to manage schedule interactions.

        Args:
            payload: The raw reaction event payload from discord.py.
        """
        await self._handle_reaction(payload, "add")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """
        Handles raw reaction remove events to manage schedule interactions.

        Args:
            payload: The raw reaction event payload from discord.py.
        """
        await self._handle_reaction(payload, "remove")

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent, action: str):
        """
        Core logic for handling reactions on schedule messages.

        This method is called by on_raw_reaction_add and on_raw_reaction_remove.
        It processes reactions for joining/leaving (✅), reminding (❗), and sharing (📢).

        Args:
            payload: The raw reaction event payload.
            action: A string indicating the action type ("add" or "remove").
        """
        # MODIFIED: Removed initial emoji check, will check emoji later
        # Ignore reactions outside of guilds
        if payload.guild_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return # Bot is not in the guild

        try:
            # Fetch user who reacted
            user = await guild.fetch_member(payload.user_id)
            # Fetch channel where reaction happened
            channel = await self.bot.fetch_channel(payload.channel_id)
        except discord.NotFound:
            return # User or channel not found, cannot proceed
        
        # Ignore reactions from bots or if user object is None
        if not user or user.bot: # Ignore reactions from bots or if user is somehow None
            return

        async with self.config.guild(guild).scheduled_events() as events:
            event_data = events.get(str(payload.message_id))
            # If the message is not a tracked schedule, ignore the reaction
            if not event_data: # Not a schedule message we are tracking
                return
            
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound: # Original message deleted
                if str(payload.message_id) in events:
                    del events[str(payload.message_id)] # Clean up stale event data
                return

            # Handling ✅ emoji for sign-ups/leaving
            if str(payload.emoji) == "✅":
                attendees = event_data["attendees"] # This is a list of user IDs
                limit = event_data["player_limit"]

                if action == "add":
                    if user.id not in attendees and len(attendees) < limit:
                        attendees.append(user.id)
                    elif user.id in attendees:
                        # User already in attendees, reaction is redundant, do nothing.
                        return
                    else: # User not in attendees but lobby is full
                        try:
                            # Remove reaction if lobby is full and user is not already in
                            await message.remove_reaction(payload.emoji, user)
                        except (discord.Forbidden, discord.NotFound):
                            pass # Bot lacks permission or reaction already gone
                        return 
                
                elif action == "remove":
                    if user.id in attendees:
                        # Prevent organizer from leaving via reaction; they must use a command.
                        if user.id == event_data["organizer_id"]:
                            # Organizer trying to un-react "✅", prevent this by re-adding.
                            # They should use a command to cancel/manage the event.
                            try:
                                await message.add_reaction(payload.emoji) # Re-add organizer's reaction
                            except (discord.Forbidden, discord.NotFound):
                                pass
                            return
                        attendees.remove(user.id)
                    else:
                        # User was not in attendees, so un-reacting does nothing to our list.
                        return

                event_data["attendees"] = attendees # Update the list in event_data
                # events[str(payload.message_id)] = event_data # This is saved by async with
                await self._update_embed(message, event_data) # Update the schedule message embed

            # Handling ❗ emoji for reminders by organizer
            elif str(payload.emoji) == "❗" and action == "add": # Only trigger on adding the reaction
                # Only the organizer can trigger a reminder
                if user.id == event_data["organizer_id"]:
                    now_ts = datetime.now(timezone.utc).timestamp()
                    start_time_ts = event_data["start_timestamp"]
                    
                    # Reminder can be sent if current time is within 30 minutes before event start
                    # or if the event has already started (to catch late reminders).
                    if (start_time_ts - 1800) < now_ts: # Reminder window: 30 mins before start up to any time after start
                        game_title_for_reminder = event_data.get('game_title', "The Game") # Use stored game_title
                        start_timestamp = event_data['start_timestamp']
                        reminder_message_text_dm = f"**Reminder for {game_title_for_reminder}!**\nThe game is starting <t:{start_timestamp}:F> (<t:{start_timestamp}:R>)!"
                        
                        dms_sent_count = 0
                        dms_failed_count = 0

                        for attendee_id in event_data["attendees"]:
                            try:
                                attendee_user = await self.bot.fetch_user(attendee_id)
                                if attendee_user: # Make sure user object was fetched
                                    await attendee_user.send(reminder_message_text_dm)
                                    dms_sent_count += 1
                            except discord.Forbidden:
                                dms_failed_count += 1
                                self.bot.log.info(f"Could not send reminder DM to user {attendee_id} for event {payload.message_id} (DMs disabled or bot blocked).")
                            except discord.NotFound:
                                self.bot.log.info(f"Could not find user {attendee_id} to send reminder DM for event {payload.message_id}.")
                            except Exception as e:
                                dms_failed_count += 1
                                self.bot.log.error(f"Failed to send reminder DM to user {attendee_id} for event {payload.message_id}: {e}")
                        
                        # Send reminder to the original channel as well
                        channel_reminder_sent = False
                        if hasattr(channel, 'send'):
                            try:
                                attendee_pings = " ".join([f"<@{uid}>" for uid in event_data["attendees"]])
                                channel_reminder_message = f"**Reminder for {game_title_for_reminder}!**\nGame is starting <t:{start_timestamp}:F> (<t:{start_timestamp}:R>)!\n{attendee_pings}"
                                await channel.send(channel_reminder_message)
                                channel_reminder_sent = True
                            except discord.Forbidden:
                                self.bot.log.warning(f"Could not send reminder to channel {channel.id} for event {payload.message_id} (missing permissions).")
                            except Exception as e:
                                self.bot.log.error(f"Failed to send reminder to channel {channel.id} for event {payload.message_id}: {e}")

                        # Confirmation to the organizer
                        organizer_confirmation_message = f"Reminder process for '{game_title_for_reminder}' complete.\n"
                        organizer_confirmation_message += f"- Sent {dms_sent_count} reminder DMs."
                        if dms_failed_count > 0:
                            organizer_confirmation_message += f" Failed to send {dms_failed_count} DMs (users may have DMs disabled or blocked)."
                        if channel_reminder_sent:
                            organizer_confirmation_message += f"\n- Sent a reminder to the event channel ({channel.mention})."
                        else:
                            organizer_confirmation_message += f"\n- Failed to send reminder to the event channel ({channel.mention})."
                        
                        try:
                            await user.send(organizer_confirmation_message) # DM the organizer
                        except discord.Forbidden:
                            if hasattr(channel, 'send'):
                                try:
                                    await channel.send(f"{user.mention}, {organizer_confirmation_message}")
                                except discord.Forbidden:
                                    pass

                        # Remove the ❗ reaction after sending reminder to prevent spam / indicate action taken
                        try:
                            await message.remove_reaction(payload.emoji, user) # Organizer's own reaction
                        except (discord.Forbidden, discord.NotFound):
                            pass 
                    else:
                        # Time condition not met (too early for reminder)
                        # Remove reaction to prevent confusion and provide feedback to organizer.
                        try:
                            await message.remove_reaction(payload.emoji, user)
                        except (discord.Forbidden, discord.NotFound):
                            pass
                        # Optionally, DM organizer that it's too early (not implemented here to avoid DM spam)
                        return 
                else:
                    # Non-organizer reacted with ❗, remove their reaction as it's organizer-only
                    try:
                        await message.remove_reaction(payload.emoji, user)
                    except (discord.Forbidden, discord.NotFound):
                        pass
                    return
            # Other emojis or "remove" action for "❗" are ignored by this custom logic.

            # Handling 📢 emoji for sharing
            elif str(payload.emoji) == "📢" and action == "add": # Only trigger on adding the reaction
                # Call the dedicated share function
                # The event_data in config will be updated by _share_schedule if successful
                await self._share_schedule(guild, user, message, event_data, remove_reaction_after_action=True)
            # No need for `finally` to remove reaction here, as _share_schedule handles it if needed.


    async def _share_schedule(self, guild: discord.Guild, user_who_triggered: discord.User, original_schedule_message: discord.Message, event_data: dict, remove_reaction_after_action: bool = True):
        """
        Handles the logic for sharing a schedule to the designated share channel.

        Args:
            guild: The guild where the action is happening.
            user_who_triggered: The user who initiated this share action.
            original_schedule_message: The message object of the schedule being shared.
            event_data: The dictionary containing data for the event.
            remove_reaction_after_action: If True, attempts to remove the '📢' reaction 
                                          from user_who_triggered on original_schedule_message.
        """
        share_channel_id = await self.config.guild(guild).share_channel_id()
        if not share_channel_id:
            try:
                await user_who_triggered.send("The share channel has not been set up for this server. Please ask an admin to use `[p]scheduleset sharechannel`.")
            except discord.Forbidden:
                pass # Cannot DM user
            if remove_reaction_after_action:
                try:
                    await original_schedule_message.remove_reaction("📢", user_who_triggered)
                except (discord.Forbidden, discord.NotFound):
                    pass
            return

        share_channel = guild.get_channel(share_channel_id)
        if not share_channel or not isinstance(share_channel, discord.TextChannel):
            try:
                await user_who_triggered.send("The configured share channel is invalid or no longer accessible. Please inform an admin.")
            except discord.Forbidden:
                pass
            if remove_reaction_after_action:
                try:
                    await original_schedule_message.remove_reaction("📢", user_who_triggered)
                except (discord.Forbidden, discord.NotFound):
                    pass
            return

        # Cooldown check for sharing: 1 hour
        # For automatic first share, last_shared_timestamp is 0, so this check passes.
        last_shared = event_data.get("last_shared_timestamp", 0)
        current_time = int(datetime.now(timezone.utc).timestamp())

        # Only apply cooldown if it's not the very first share (i.e., last_shared is not 0)
        # And if the share is triggered by a reaction (remove_reaction_after_action is True)
        # The automatic first share should bypass this specific user-facing cooldown message.
        if last_shared != 0 and current_time - last_shared < 3600:
            minutes_remaining = (3600 - (current_time - last_shared)) // 60
            try:
                await user_who_triggered.send(f"This schedule was shared recently. Please try again in about {minutes_remaining} minute(s).")
            except discord.Forbidden:
                pass
            if remove_reaction_after_action:
                try:
                    await original_schedule_message.remove_reaction("📢", user_who_triggered)
                except (discord.Forbidden, discord.NotFound):
                    pass
            return

        # Proceed to share the event
        game_title_for_share = event_data.get('game_title', "A Game")
        player_limit = event_data.get("player_limit", 0)
        current_attendees_count = len(event_data.get("attendees", []))
        players_needed = player_limit - current_attendees_count
        event_tags = event_data.get("tags", [])

        description_text = (
            f"**Organizer:** {user_who_triggered.mention}!\n"
            f"**Title:** {game_title_for_share}\n"
            f"**Starts:** <t:{event_data['start_timestamp']}:F> (<t:{event_data['start_timestamp']}:R>)\n"
        )
        if event_tags:
            tags_formatted = " ".join([f"`{tag}`" for tag in event_tags])
            description_text += f"**Tags:** {tags_formatted}\n"

        if players_needed > 0:
            description_text += f"**Slots:** {current_attendees_count} / {player_limit}\n"
        elif players_needed == 0:
            description_text += "**Lobby is full!**\n"
        else:
            description_text += "**Lobby is overfull!**\n"
        
        description_text += f"\n[Click here to view the schedule]({original_schedule_message.jump_url})"

        share_embed = discord.Embed(
            title=f"📢 Game Announcement: {game_title_for_share}",
            description=description_text,
            color=discord.Color.green()
        )
        if event_data.get("description"):
            share_embed.add_field(name="Description", value=event_data["description"], inline=False)

        try:
            await share_channel.send(embed=share_embed)
            # Update last_shared_timestamp in the config
            async with self.config.guild(guild).scheduled_events() as events_config:
                if str(original_schedule_message.id) in events_config:
                    events_config[str(original_schedule_message.id)]["last_shared_timestamp"] = current_time
            
            # Send confirmation only for manual shares (reaction-triggered)
            if remove_reaction_after_action: # Implies it's a manual share by reaction
                try:
                    await user_who_triggered.send(f"✅ Successfully shared '{game_title_for_share}' to {share_channel.mention}!")
                except discord.Forbidden:
                    pass 
        except discord.Forbidden:
            if remove_reaction_after_action: # Only send error DM for manual shares
                try:
                    await user_who_triggered.send(f"I don't have permission to send messages in {share_channel.mention}. Please inform an admin.")
                except discord.Forbidden:
                    pass
        except Exception as e:
            self.bot.log.error(f"Failed to share schedule (msg_id: {original_schedule_message.id}): {e}")
            if remove_reaction_after_action: # Only send error DM for manual shares
                try:
                    await user_who_triggered.send("An error occurred while trying to share the schedule.")
                except discord.Forbidden:
                    pass
        finally:
            if remove_reaction_after_action:
                try:
                    await original_schedule_message.remove_reaction("📢", user_who_triggered)
                except (discord.Forbidden, discord.NotFound):
                    pass

    async def _update_embed(self, message: discord.Message, event_data: dict):
        """
        Updates the schedule message embed with the latest event data.

        This is called after actions like joining, leaving, or when event details change.

        Args:
            message: The discord.Message object of the schedule to update.
            event_data: A dictionary containing the current data for the event.
        """
        unix_timestamp = event_data['start_timestamp']
        game_title_for_embed = event_data.get('game_title', "Unknown Game") # Use stored game_title or a default
        description_for_embed = event_data.get('description') # Get stored description
        
        # Reconstruct the embed with updated information
        new_embed = discord.Embed(
            title=f"Game Session: {game_title_for_embed}",
            description=f"Starts <t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)", # Dynamic time formatting
            color=discord.Color.blue()
        )
        if description_for_embed: # Add description field if it exists
            new_embed.add_field(name="Description", value=description_for_embed, inline=False)
        
        # Update lobby count
        new_embed.add_field(name="Lobby", value=f"{len(event_data['attendees'])} / {event_data['player_limit']}", inline=True)
        
        # Update organizer mention
        organizer = message.guild.get_member(event_data['organizer_id'])
        new_embed.add_field(name="Organizer", value=organizer.mention if organizer else "Unknown", inline=True)
        
        # Update player list
        player_mentions = " ".join([f"<@{uid}>" for uid in event_data["attendees"]])
        new_embed.add_field(name="Players", value=player_mentions or "No one has joined yet.", inline=False)
        
        # Update footer text (can include dynamic info if needed in future)
        new_embed.set_footer(text="✅ Join/Leave | Organizer: ❗ Remind (within 30 mins prior) | 📢 Share") # Updated footer
        
        await message.edit(embed=new_embed) # Edit the original message with the new embed

async def setup(bot: Red):
    """
    Standard setup function for Redbot cogs.

    Args:
        bot: The Redbot instance.
    """
    await bot.add_cog(Schedule(bot))