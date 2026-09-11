import discord
import dateparser
import pytz
from discord import app_commands
from redbot.core import commands


class ScheduleCommands:
    """
    Commands for the Schedule cog.
    """

    @commands.hybrid_group(name="scheduleset", aliases=["ss"])
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def schedule_set(self, ctx: commands.Context):
        """
        Base command for schedule configuration.
        """
        pass

    @schedule_set.command(name="forum")
    @app_commands.describe(forum="The forum channel where schedules will be created.")
    async def set_forum(self, ctx: commands.Context, forum: discord.ForumChannel):
        """
        Sets the designated forum channel for creating new game schedules.
        """
        await self.config.guild(ctx.guild).target_forum_id.set(forum.id)
        await ctx.send(f"✅ The scheduling forum has been set to {forum.mention}.")

    @schedule_set.command(name="sharechannel")
    @app_commands.describe(
        channel="The text channel where shared schedules will be posted."
    )
    async def set_share_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Sets the designated text channel for sharing scheduled game announcements.
        """
        await self.config.guild(ctx.guild).share_channel_id.set(channel.id)
        await ctx.send(f"✅ The schedule sharing channel has been set to {channel.mention}.")

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
                "Find your timezone here: <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>",
                ephemeral=True,
            )

    @commands.hybrid_command()
    @commands.guild_only()
    @app_commands.describe(
        player_amount="The maximum number of players for the lobby.",
        time_input="When the game starts (e.g., 'in 2 hours', 'at 10pm', 'thursday at 10pm').",
        title="Optional: A custom title for the game session.",
        description="Optional: A description for the game session.",
    )
    async def schedule(
        self,
        ctx: commands.Context,
        player_amount: int,
        time_input: str,
        title: str = None,
        description: str = None,
    ):
        """
        Schedules a game session within a designated forum post.
        """
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

        if player_amount <= 0:
            return await ctx.send(
                "Player amount must be a positive number.", ephemeral=True
            )

        user_tz_str = await self.config.member(ctx.author).timezone()
        tz = user_tz_str or "Asia/Jakarta"

        settings = {
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": tz,
            "RETURN_AS_TIMEZONE_AWARE": True,
        }
        parsed_time = dateparser.parse(time_input, settings=settings)

        if not parsed_time:
            return await ctx.send(
                f"Sorry, I couldn't understand the time: `{time_input}`.",
                ephemeral=True,
            )

        unix_timestamp = int(parsed_time.timestamp())
        game_title = title if title else ctx.channel.name

        embed = discord.Embed(
            title=f"Game Session: {game_title}",
            description=f"Starts <t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)",
            color=discord.Color.blue(),
        )
        if description:
            embed.add_field(name="Description", value=description, inline=False)
        embed.add_field(name="Lobby", value=f"1 / {player_amount}", inline=True)
        embed.add_field(name="Organizer", value=ctx.author.mention, inline=True)
        embed.add_field(name="Players", value=ctx.author.mention, inline=False)
        embed.set_footer(text="✅ Join/Leave | Organizer: ❗ Remind | 📢 Share")

        msg = await ctx.channel.send(embed=embed)

        await ctx.send(
            f"✅ Your event for **{game_title}** has been scheduled!", ephemeral=True
        )

        thread_tags = (
            [f"{str(tag.emoji)}{tag.name}" if tag.emoji else f"{tag.name}" for tag in ctx.channel.applied_tags]
            if ctx.channel.applied_tags
            else []
        )

        event_data = {
            "organizer_id": ctx.author.id,
            "player_limit": player_amount,
            "game_title": game_title,
            "description": description,
            "start_timestamp": unix_timestamp,
            "channel_id": ctx.channel.id,
            "attendees": [ctx.author.id],
            "last_shared_timestamp": 0,
            "tags": thread_tags,
        }

        async with self.config.guild(ctx.guild).scheduled_events() as events:
            events[str(msg.id)] = event_data

        await msg.add_reaction("✅")
        await msg.add_reaction("❗")
        await msg.add_reaction("📢")

        await self._share_schedule(
            ctx.guild, ctx.author, msg, events[str(msg.id)], remove_reaction_after_action=False
        )
