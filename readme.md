# Schedule for Red

Schedule sessions inside Discord forum posts. Members see start times in their own Discord timezone and join by adding ✅; remove ✅ to leave. The organizer occupies one slot and remains in the participant list.

## Setup

This is a Red-DiscordBot cog, not a standalone bot. Replace `[p]` with your bot prefix.

1. For a local checkout, install `schedule/info.json` requirements in Red's environment, then use `[p]addpath /home/ubuntu/custom-redcogs` and `[p]load schedule`. With Downloader, use `[p]repo add custom-redcogs https://github.com/AldiandyaIrsyad/custom-redcogs` and `[p]cog install custom-redcogs schedule` instead.
2. An administrator sets `[p]scheduleset forum <forum-channel>`.
3. Optionally set `[p]scheduleset sharechannel <text-channel>` for announcements.
4. Set `[p]settimezone Asia/Jakarta` (or your IANA timezone).
5. Inside a forum post, use `[p]schedule 4 "tomorrow at 8pm" "Weekly session" "Bring your questions"`.

For prefix commands, quote arguments containing spaces. Slash command availability depends on the Red owner's slash-command configuration and synchronization.

The bot needs View Channel, Read Message History, Send Messages in Threads, Embed Links, Add Reactions, and Manage Messages in the scheduling forum. Manage Messages lets it reset rejected or one-shot reactions. Announcement channels need View Channel, Send Messages, and Embed Links. Discord reaction events must be enabled.

## Behavior and limits

- Times must be in the future. The default scheduling timezone is Asia/Jakarta; confirmations show the timezone used. Discord renders timestamps in each viewer's local time.
- Capacity includes the organizer. A full event rejects additional participants.
- Organizers can send a manual ❗ reminder during the 30 minutes before the start, with a cooldown. There is no automatic reminder worker or recurring-task engine.
- 📢 shares are rate limited. An announcement is a snapshot; follow its link for the current participant list and event status.
- Closed and started events do not accept sign-ups, reminders, or shares. Existing stored events remain compatible.
- Event data and per-server timezone preferences persist in Red Config. Discord posts persist independently.

## Development tests

Use Python 3.11 and a virtual environment:

```powershell
py -3.11 -m venv .redvenv
.\.redvenv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.redvenv\Scripts\python.exe -m pytest -q
```

Tests use real Red Config storage in temporary directories and mocked Discord HTTP calls. They do not send Discord messages. See [Discord testing](docs/discord-testing.md) for live checks.
