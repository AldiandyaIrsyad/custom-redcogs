import discord
import dateparser
from redbot.core import commands
from redbot.core.bot import Red

class Schedule(commands.Cog):
    """
    A cog to schedule games in forum posts.
    """

    def __init__(self, bot: Red):
        self.bot = bot

    @commands.command()
    @commands.guild_only()
    async def schedule(self, ctx: commands.Context, player_amount: int, *, time_input: str):
        """
        Schedules a game session within a forum post.

        The game name is taken from the forum post's title.
        The time can be in various formats (e.g., "10pm", "in 2 hours").

        Example:
        `!schedule 5 tomorrow at 8pm`
        """
        if not isinstance(ctx.channel, discord.Thread) or not isinstance(ctx.channel.parent, discord.ForumChannel):
            await ctx.send("This command can only be used inside a forum post thread.")
            return

        if player_amount <= 0:
            await ctx.send("Player amount must be a positive number.")
            return

        # Use settings to prefer dates in the future
        settings = {'PREFER_DATES_FROM': 'future', 'RETURN_AS_TIMEZONE_AWARE': True}
        parsed_time = dateparser.parse(time_input, settings=settings)

        if not parsed_time:
            error_message = (
                f"Sorry, I couldn't understand the time: `{time_input}`.\n\n"
                "**Try formats like:**\n"
                "- `in 30 minutes`\n"
                "- `at 10:30pm`\n"
                "- `tomorrow 18:00`"
            )
            await ctx.send(error_message)
            return

        game_title = ctx.channel.name
        unix_timestamp = int(parsed_time.timestamp())

        message = (
            f"Playing **{game_title}**\n"
            f"Lobby: 1 / {player_amount}\n"
            f"Starts <t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)"
        )

        await ctx.send(message)
        try:
            await ctx.message.add_reaction("✅")
        except discord.Forbidden:
            # Bot might not have permission to add reactions
            pass