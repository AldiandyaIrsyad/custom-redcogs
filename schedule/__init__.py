import asyncio

from redbot.core import commands as red_commands, Config
from redbot.core.bot import Red

from .commands import ScheduleCommands
from .listeners import ScheduleListeners
from .helpers import ScheduleHelpers


class Schedule(
    ScheduleCommands, ScheduleListeners, ScheduleHelpers, red_commands.Cog
):
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
        self._event_locks = {}
        self.config = Config.get_conf(
            self, identifier=1234567890, force_registration=True
        )

        default_guild = {
            "target_forum_id": None,
            "scheduled_events": {},  # {message_id: {data}}
            "share_channel_id": None,  # Added for share feature
        }
        default_member = {"timezone": None}

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

    def _event_lock(self, guild_id: int):
        """Serialize event changes without nesting Red Config transactions."""
        return self._event_locks.setdefault(guild_id, asyncio.Lock())


async def setup(bot: Red):
    """
    Standard setup function for Redbot cogs.

    Args:
        bot: The Redbot instance.
    """
    await bot.add_cog(Schedule(bot))
