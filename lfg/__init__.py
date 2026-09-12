import asyncio

from redbot.core import commands as red_commands, Config
from redbot.core.bot import Red

from .commands import LFGCommands
from .listeners import LFGListeners
from .helpers import LFGHelpers
from .exp import (
    LFGExp,
    DEFAULT_EXP_PER_SESSION,
    DEFAULT_EXP_ORGANIZER_BONUS,
    DEFAULT_EXP_MIN_ATTENDEES,
)


class LFG(
    LFGExp,
    LFGCommands,
    LFGListeners,
    LFGHelpers,
    red_commands.Cog,
):
    """
    Looking For Group: organize game sessions in forum posts with timezone support and reaction-based sign-ups.

    This cog provides commands to:
    - Set a target forum channel for sessions.
    - Set a target channel for sharing scheduled sessions.
    - Allow users to set their personal timezone.
    - Create play sessions with details like player count, time, title, and description.
    - Handle attendance reactions and private organizer actions.
    - Track session EXP and grant reward roles to active members.
    """

    def __init__(self, bot: Red):
        """
        Initializes the LFG cog.

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
            "exp_enabled": False,  # Opt-in session EXP system
            "exp_per_session": DEFAULT_EXP_PER_SESSION,
            "exp_organizer_bonus": DEFAULT_EXP_ORGANIZER_BONUS,
            "exp_min_attendees": DEFAULT_EXP_MIN_ATTENDEES,
            "exp_roles": {},  # {role_id: exp threshold}
        }
        default_member = {"timezone": None, "exp": 0, "sessions": 0, "exp_awards": {}}

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
    await bot.add_cog(LFG(bot))
