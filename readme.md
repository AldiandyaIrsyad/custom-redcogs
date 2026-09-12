# Schedule for Red

Schedule game sessions inside Discord forum posts. The cog posts a session card with a localized Discord timestamp, lets members join or leave with `✅`, and keeps the participant list in sync with the reactions. The organizer occupies one player slot and stays in the participant list.

Schedule is a cog for [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot), not a standalone bot. In the examples below, replace `[p]` with your Red command prefix. All commands are hybrid commands unless noted otherwise, so they can be used as prefix commands and, after Discord synchronizes them, as slash commands.

## Install

### Red Downloader

From the server where Red is running, use Downloader as an administrator or owner:

```text
[p]repo add custom-redcogs https://github.com/AldiandyaIrsyad/custom-redcogs
[p]cog install custom-redcogs schedule
[p]load schedule
```

The cog declares its runtime requirements in [`schedule/info.json`](schedule/info.json). Downloader installs `dateparser` and `pytz` for the cog when it installs dependencies.

### Local checkout

Install the requirements into the same Python environment that runs Red, then add the repository root to Red's cog path:

```text
python -m pip install "dateparser>=1.2,<2" "pytz>=2024.2"
```

```text
[p]addpath C:\path\to\custom-redcogs
[p]load schedule
```

Use the actual path to the directory that contains the `schedule` folder. Reload the cog after updating its source with `[p]reload schedule`.

## Configure the server

An administrator or a member with Manage Server must complete the forum setup:

1. Create or choose a Discord forum channel for sessions.
2. Run `[p]scheduleset forum <forum-channel>`.
3. Optionally choose a text channel for announcements with `[p]scheduleset sharechannel <text-channel>`.
4. Make sure the bot has the permissions listed in [Permissions](#permissions).
5. Members should set their own timezone before creating sessions. The default is `Asia/Jakarta` when no valid personal timezone is saved.

The forum setting is per server and applies to every new session. A schedule can only be created inside a thread belonging to that designated forum. Setting a share channel enables an automatic announcement when a session is created and allows organizers to request another announcement with `[p]scheduleshare` after the cooldown.

For prefix commands, quote arguments that contain spaces. For example:

```text
[p]settimezone America/New_York
[p]schedule 4 "tomorrow at 8pm" "Weekly session" "Bring your questions"
```

Slash command availability depends on the Red owner's slash-command settings and Discord command synchronization. The command names and options remain the same, and slash commands provide fields for arguments that need quoting in prefix form.

## Commands

| Command | Who can use it | Purpose |
| --- | --- | --- |
| `[p]scheduleset forum <forum-channel>` | Manage Server | Set the forum where schedule messages may be created. Group alias: `[p]ss forum`. |
| `[p]scheduleset sharechannel <text-channel>` | Manage Server | Set the text channel for automatic and manual announcement posts. Group alias: `[p]ss sharechannel`. |
| `[p]settimezone <IANA-timezone>` | Any server member | Save the timezone used to interpret that member's new schedules. Example: `[p]settimezone Asia/Jakarta`. |
| `[p]schedule <players> <time> [title] [description]` | Any member | Create a schedule in the current thread of the configured forum. Players must be from 1 to 100. A missing title uses the forum post's thread name. Titles are limited to 256 characters and descriptions to 1,024 characters. |
| `[p]schedulecontrol <message-id-or-url>` | Organizer or Manage Server | Send the organizer control instructions privately. Alias: `[p]controls`. |
| `[p]scheduleremind <message-id-or-url>` | Organizer or Manage Server | Remind attendees during the 30 minutes before an active schedule starts. Alias: `[p]remind`. |
| `[p]scheduleshare <message-id-or-url>` | Organizer or Manage Server | Post the active schedule to the configured announcement channel. Alias: `[p]share`. |
| `[p]schedulereschedule <message-id-or-url> <time>` | Organizer or Manage Server | Move an active schedule to a new future start time. Aliases: `[p]reschedule` and `[p]resched`. |
| `[p]schedulecancel <message-id-or-url>` | Organizer or Manage Server | Mark an active schedule as cancelled. The message remains visible for history and its reactions stop changing the event. |
| `[p]schedulefinish <message-id-or-url>` | Organizer or Manage Server | Mark an active schedule as finished. Alias: `[p]finish`. |
| `[p]upcoming` | Any server member | Show up to 10 future active schedules that the member can view. Aliases: `[p]schedulelist` and `[p]upcomingschedules`. |

For lifecycle commands, the identifier may be the schedule message's numeric ID or its full Discord message URL. Copy the link from the schedule card when possible.

## Using a schedule

After `[p]schedule` succeeds, the cog creates two surfaces:

- The public schedule card shows the event and only the `✅` join or leave reaction. The organizer counts toward capacity and cannot leave by removing their reaction.
- The private organizer message contains commands for reminder, sharing, rescheduling, cancellation, and completion. Slash-command creation returns this information ephemerally. Prefix-command creation sends it by DM. Run `[p]schedulecontrol <message-id-or-url>` to receive it again.

`[p]scheduleremind` works only during the 30 minutes before the start. It posts a reminder in the event thread and attempts a DM to every attendee. A schedule accepts at most one reminder per hour. A member with DMs disabled may not receive the DM, but the other deliveries still proceed.

`[p]scheduleshare` posts the current event snapshot to the configured share channel and has a one-hour per-event cooldown. The announcement links back to the original thread, so use that link for the current participant list and status. The cog also posts an automatic announcement when the share channel is configured.

The lobby includes the organizer. When it is full, a new `✅` reaction is removed and the member is told that no slot is available. New joins are also rejected after the start time. Cancelled and finished events remain visible but no longer accept joins, reminders, or shares. Deleting the schedule message or its forum post removes its matching stored event.

## Timezones and time input

Use a [TZ database](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) name with `[p]settimezone`, such as `Asia/Jakarta`, `America/New_York`, or `Europe/London`. A schedule's natural-language time is interpreted in the organizer's saved timezone. If none is saved, the cog uses `Asia/Jakarta` and says so in the confirmation. The timezone used for the event is saved with that event.

Examples of accepted input include:

```text
[p]schedule 4 "in 2 hours" "Quick match"
[p]schedule 8 "Thursday at 10pm" "Weekly raid"
[p]schedule 5 "2026-09-20 19:30" "Tournament"
```

The time must resolve to the future. Relative durations such as `in 2 hours` represent elapsed time. A reschedule interprets the new time in the event's saved timezone, so changing your personal timezone does not silently move an existing event.

Every event uses Discord's timestamp markup. Discord renders the same event in each viewer's local timezone, while the confirmation identifies the timezone used to parse the input. A local time that falls in a daylight-saving gap or repeated hour is rejected unless the input makes the offset unambiguous. Choose another local time or include an explicit UTC offset when appropriate.

## Permissions

The bot needs the following permissions in the designated forum and its threads:

| Permission | Used for |
| --- | --- |
| View Channel | Find and read the forum thread and its messages. |
| Read Message History | Fetch the schedule message when a reaction arrives. |
| Send Messages in Threads | Post and update the schedule card and public reminders. |
| Embed Links | Render schedule and announcement embeds. |
| Add Reactions | Add the public `✅` attendance control and restore the organizer's reaction when needed. |
| Manage Messages | Remove a rejected full-lobby reaction and clean up legacy organizer reactions. |

The configured announcement text channel needs View Channel, Send Messages, and Embed Links. Members need access to the forum thread and permission to add reactions. Reaction events must be enabled for the Red bot so the cog can receive join and leave actions.

If a required forum permission is missing, the cog reports it before creating a schedule. If a permission changes after creation, the event data may remain saved while message edits, reactions, or announcements fail until the permission is restored.

## Data and privacy

The cog stores its state with Red Config. Per-member data is the saved timezone. Per-server data includes the configured forum and share-channel IDs and event records keyed by schedule message ID. An event record can contain:

- organizer and attendee Discord user IDs;
- forum channel and schedule message IDs;
- title, description, forum tags, player limit, and start timestamp;
- the timezone used to parse the event and whether the default was used;
- active, cancelled, or finished status; and reminder/share timestamps used for cooldowns.

The cog does not send schedule data to a separate service. It sends event content to Discord when it creates the schedule embed, posts an announcement, or sends a reminder DM. Discord retains those messages under its own policies. Stored event data remains in Red Config while the record exists, including after cancellation or completion. Deleting the schedule message or its forum post lets the cog remove its matching event record. There is no separate export or per-member deletion command; server owners control the Red data directory and can unload the cog or manage its Config data according to their server's retention policy.

## Development and tests

The test suite targets Python 3.11. Create the included environment and run the tests from the repository root:

```powershell
py -3.11 -m venv .redvenv
.\.redvenv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.redvenv\Scripts\python.exe -m pytest -q
```

The tests use real Red Config storage in temporary directories and mock Discord HTTP calls. They cover command authorization, message-ID parsing, lifecycle transitions, capacity and reaction concurrency, reminder and share cooldowns, stale-message cleanup, embed limits, and timezone parsing around daylight-saving transitions. They do not send messages to Discord. See [`docs/discord-testing.md`](docs/discord-testing.md) for the live acceptance checklist and production-token precautions.
