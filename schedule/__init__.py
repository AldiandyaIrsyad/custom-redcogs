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
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)

        default_guild = {
            "target_forum_id": None,
            "scheduled_events": {}, # {message_id: {data}}
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
        """Configuration commands for the scheduler."""
        pass

    @schedule_set.command(name="forum")
    @app_commands.describe(forum="The forum channel where schedules will be created.")
    async def set_forum(self, ctx: commands.Context, forum: discord.ForumChannel):
        """Sets the forum channel for scheduling games."""
        await self.config.guild(ctx.guild).target_forum_id.set(forum.id)
        await ctx.send(f"✅ The scheduling forum has been set to {forum.mention}.")

    # Corrected with the app_commands.describe decorator
    @commands.hybrid_command()
    @commands.guild_only()
    @app_commands.describe(
        timezone_str="Your timezone name (e.g., 'Asia/Jakarta' or 'America/New_York')."
    )
    async def settimezone(self, ctx: commands.Context, timezone_str: str):
        """
        Sets your personal timezone for scheduling.
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
        time_input="When the game starts (e.g., 'in 2 hours', 'at 10pm', 'thursday at 10pm')."
    )
    async def schedule(self, ctx: commands.Context, player_amount: int, *, time_input: str):
        """
        Schedules a game session within a designated forum post.
        """
        # When used as a slash command, defer the response to avoid "thinking..." messages
        if ctx.interaction:
            await ctx.defer(ephemeral=True)

        target_forum_id = await self.config.guild(ctx.guild).target_forum_id()
        if not target_forum_id:
            return await ctx.send("The scheduling forum has not been set. An admin must use `[p]scheduleset forum`.", ephemeral=True)

        if not isinstance(ctx.channel, discord.Thread) or ctx.channel.parent_id != target_forum_id:
            return await ctx.send("This command can only be used inside a thread of the designated scheduling forum.", ephemeral=True)

        if player_amount <= 0:
            return await ctx.send("Player amount must be a positive number.", ephemeral=True)

        user_tz_str = await self.config.member(ctx.author).timezone()
        tz = user_tz_str or "Asia/Jakarta"
        
        settings = {'PREFER_DATES_FROM': 'future', 'TIMEZONE': tz, 'RETURN_AS_TIMEZONE_AWARE': True}
        parsed_time = dateparser.parse(time_input, settings=settings)

        if not parsed_time:
            return await ctx.send(f"Sorry, I couldn't understand the time: `{time_input}`.", ephemeral=True)
        
        unix_timestamp = int(parsed_time.timestamp())
        game_title = ctx.channel.name
        
        embed = discord.Embed(
            title=f"Game Session: {game_title}",
            description=f"Starts <t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)",
            color=discord.Color.blue()
        )
        embed.add_field(name="Lobby", value=f"1 / {player_amount}", inline=True)
        embed.add_field(name="Organizer", value=ctx.author.mention, inline=True)
        embed.add_field(name="Players", value=ctx.author.mention, inline=False)
        embed.set_footer(text="✅ Join/Leave | Organizer: ❗ to Remind (within 30 mins prior)")

        # For hybrid commands, we send the public message to the channel, not as a reply
        msg = await ctx.channel.send(embed=embed) 
        
        # Then, we send a confirmation to the user who ran the command
        await ctx.send(f"✅ Your event for **{game_title}** has been scheduled!", ephemeral=True)
        
        event_data = {
            "organizer_id": ctx.author.id,
            "player_limit": player_amount,
            "game_title": game_title,
            "start_timestamp": unix_timestamp,
            "channel_id": ctx.channel.id,
            "attendees": [ctx.author.id],
        }

        async with self.config.guild(ctx.guild).scheduled_events() as events:
            events[str(msg.id)] = event_data

        await msg.add_reaction("✅")
        await msg.add_reaction("❗") # ADDED reaction for reminder

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload, "add")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload, "remove")

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent, action: str):
        # MODIFIED: Removed initial emoji check, will check emoji later
        if payload.guild_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        try:
            # Fetch user who reacted
            user = await guild.fetch_member(payload.user_id)
            # Fetch channel where reaction happened
            channel = await self.bot.fetch_channel(payload.channel_id)
        except discord.NotFound:
            return # User or channel not found
        
        if not user or user.bot: # Ignore reactions from bots or if user is somehow None
            return

        async with self.config.guild(guild).scheduled_events() as events:
            event_data = events.get(str(payload.message_id))
            if not event_data: # Not a schedule message we are tracking
                return
            
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound: # Original message deleted
                if str(payload.message_id) in events:
                    del events[str(payload.message_id)] # Clean up stale event
                return

            # Handling ✅ emoji for sign-ups/leaving
            if str(payload.emoji) == "✅":
                attendees = event_data["attendees"] # This is a list
                limit = event_data["player_limit"]

                if action == "add":
                    if user.id not in attendees and len(attendees) < limit:
                        attendees.append(user.id)
                    elif user.id in attendees:
                        # User already in attendees, reaction is redundant, do nothing.
                        return
                    else: # User not in attendees but lobby is full
                        try:
                            await message.remove_reaction(payload.emoji, user)
                        except (discord.Forbidden, discord.NotFound):
                            pass # Bot lacks permission or reaction already gone
                        return 
                
                elif action == "remove":
                    if user.id in attendees:
                        if user.id == event_data["organizer_id"]:
                            # Organizer trying to un-react "✅", prevent this by re-adding.
                            # They should use a command to cancel/manage the event.
                            try:
                                await message.add_reaction(payload.emoji)
                            except (discord.Forbidden, discord.NotFound):
                                pass
                            return
                        attendees.remove(user.id)
                    else:
                        # User was not in attendees, so un-reacting does nothing to our list.
                        return

                event_data["attendees"] = attendees # Update the list in event_data
                # events[str(payload.message_id)] = event_data # This is saved by async with
                await self._update_embed(message, event_data)

            # Handling ❗ emoji for reminders by organizer
            elif str(payload.emoji) == "❗" and action == "add": # Only trigger on adding the reaction
                if user.id == event_data["organizer_id"]:
                    now_ts = datetime.now(timezone.utc).timestamp()
                    start_time_ts = event_data["start_timestamp"]
                    
                    # Reminder can be sent if current time is within 30 minutes before event start
                    # and the event has not started yet.
                    if (start_time_ts - 1800) < now_ts < start_time_ts:
                        attendee_pings = " ".join([f"<@{uid}>" for uid in event_data["attendees"]])
                        game_title = event_data['game_title']
                        
                        if hasattr(channel, 'send'): # Check if channel can send messages
                             await channel.send(f"**Reminder for {game_title}!**\\nGame is starting in less than 30 minutes!\\n{attendee_pings}")
                        
                        # Remove the ❗ reaction after sending reminder to prevent spam / indicate action taken
                        try:
                            await message.remove_reaction(payload.emoji, user) # Organizer's own reaction
                        except (discord.Forbidden, discord.NotFound):
                            pass 
                    else:
                        # Time condition not met, remove reaction to prevent confusion
                        # This also serves as feedback to the organizer that it's not time yet.
                        try:
                            await message.remove_reaction(payload.emoji, user)
                        except (discord.Forbidden, discord.NotFound):
                            pass
                        return 
                else:
                    # Non-organizer reacted with ❗, remove their reaction
                    try:
                        await message.remove_reaction(payload.emoji, user)
                    except (discord.Forbidden, discord.NotFound):
                        pass
                    return
            # Other emojis or "remove" action for "❗" are ignored by this custom logic.

    async def _update_embed(self, message: discord.Message, event_data: dict):
        unix_timestamp = event_data['start_timestamp']
        
        new_embed = discord.Embed(
            title=f"Game Session: {event_data['game_title']}",
            description=f"Starts <t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)",
            color=discord.Color.blue()
        )
        new_embed.add_field(name="Lobby", value=f"{len(event_data['attendees'])} / {event_data['player_limit']}", inline=True)
        organizer = message.guild.get_member(event_data['organizer_id'])
        new_embed.add_field(name="Organizer", value=organizer.mention if organizer else "Unknown", inline=True)
        
        player_mentions = " ".join([f"<@{uid}>" for uid in event_data["attendees"]])
        new_embed.add_field(name="Players", value=player_mentions or "No one has joined yet.", inline=False)
        new_embed.set_footer(text="✅ Join/Leave | Organizer: ❗ to Remind (within 30 mins prior)")
        
        await message.edit(embed=new_embed)

async def setup(bot: Red):
    await bot.add_cog(Schedule(bot))