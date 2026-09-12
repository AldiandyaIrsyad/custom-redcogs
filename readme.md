# LFG for Red

[![Tests](https://github.com/AldiandyaIrsyad/custom-redcogs/actions/workflows/tests.yml/badge.svg)](https://github.com/AldiandyaIrsyad/custom-redcogs/actions/workflows/tests.yml)
[![Python 3.10-3.11](https://img.shields.io/badge/python-3.10--3.11-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Looking For Group (LFG): organize game sessions inside Discord forum posts. The cog posts a session card with a localized Discord timestamp, lets members join or leave with `✅`, and keeps the participant list in sync with the reactions. The organizer occupies one player slot and stays in the participant list.

The cog can optionally track session EXP and grant Discord roles when members reach EXP thresholds, rewarding people who show up to play.

LFG is a cog for [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot), not a standalone bot. In the examples below, replace `[p]` with your Red command prefix. The commands are hybrid commands, so they work as prefix commands and, after Discord synchronizes them, as slash commands.

## Install

### Red Downloader

From the server where Red is running, use Downloader as an administrator or owner:

```text
[p]repo add custom-redcogs https://github.com/AldiandyaIrsyad/custom-redcogs
[p]cog install custom-redcogs lfg
[p]load lfg
```

The cog declares its runtime requirements in [`lfg/info.json`](lfg/info.json). Downloader installs `dateparser` and `pytz` for the cog when it installs dependencies.

### Local checkout

Install the requirements into the same Python environment that runs Red, then add the repository root to Red's cog path:

```text
python -m pip install "dateparser>=1.2,<2" "pytz>=2024.2"
```

```text
[p]addpath C:\path\to\custom-redcogs
[p]load lfg
```

Use the actual path to the directory that contains the `lfg` folder. Reload the cog after updating its source with `[p]reload lfg`.

## Configure the server

An administrator or a member with Manage Server must complete the forum setup:

1. Create or choose a Discord forum channel for sessions.
2. Run `[p]lfgset forum <forum-channel>`.
3. Optionally choose a text channel for announcements with `[p]lfgset sharechannel <text-channel>`.
4. Make sure the bot has the permissions listed in [Permissions](#permissions).
5. Members should set their own timezone before creating sessions. The default is `Asia/Jakarta` when no valid personal timezone is saved.
6. Optionally enable session EXP and reward roles with `[p]lfgset exp toggle`; see [Session EXP and role rewards](#session-exp-and-role-rewards).

The forum setting is per server and applies to every new session. A session can only be created inside a thread belonging to that designated forum. Setting a share channel enables an automatic announcement when a session is created and allows organizers to request another announcement with `[p]lfg share` after the cooldown.

For prefix commands, quote arguments that contain spaces. For example:

```text
[p]lfgtimezone America/New_York
[p]lfg 4 "tomorrow at 8pm" "Weekly session" "Bring your questions"
```

Slash command availability depends on the Red owner's slash-command settings and Discord command synchronization. Because Discord does not allow options and subcommands on the same command, slash users start a session with `/lfg start <players> <time> [title] [description]`. Prefix users can use either `[p]lfg <players> <time>` or `[p]lfg start <players> <time>`.

## Commands

| Command | Who can use it | Purpose |
| --- | --- | --- |
| `[p]lfg <players> <time> [title] [description]` | Any member | Create a session in the current thread of the configured forum. Shorthand for `[p]lfg start`. |
| `[p]lfg start <players> <time> [title] [description]` | Any member | Create a session in the current thread of the configured forum. Players must be from 1 to 100. A missing title uses the forum post's thread name. Titles are limited to 256 characters and descriptions to 1,024 characters. |
| `[p]lfg control <message-id-or-url>` | Organizer or Manage Server | Send the organizer control instructions privately. Alias: `[p]lfg controls`. |
| `[p]lfg remind <message-id-or-url>` | Organizer or Manage Server | Remind attendees during the 30 minutes before an active session starts. Alias: `[p]lfg reminder`. |
| `[p]lfg share <message-id-or-url>` | Organizer or Manage Server | Post the active session to the configured announcement channel. Alias: `[p]lfg announce`. |
| `[p]lfg reschedule <message-id-or-url> <time>` | Organizer or Manage Server | Move an active session to a new future start time. Aliases: `[p]lfg resched` and `[p]lfg move`. |
| `[p]lfg cancel <message-id-or-url>` | Organizer or Manage Server | Mark an active session as cancelled. The message remains visible for history and its reactions stop changing the event. |
| `[p]lfg finish <message-id-or-url>` | Organizer or Manage Server | Mark an active session as finished and award session EXP. Aliases: `[p]lfg end` and `[p]lfg complete`. |
| `[p]lfg upcoming` | Any server member | Show up to 10 future active sessions that the member can view. Aliases: `[p]lfg list` and `[p]lfg sessions`. |
| `[p]lfg exp [member]` | Any server member | Show a member's total session EXP, successful-session count, current reward role, and progress to the next tier. Defaults to yourself. Aliases: `[p]lfg xp` and `[p]lfg stats`. |
| `[p]lfg top` | Any server member | Show the top 10 members by session EXP. Aliases: `[p]lfg leaderboard` and `[p]lfg rank`. |
| `[p]lfgtimezone <IANA-timezone>` | Any server member | Save the timezone used to interpret that member's new sessions. Example: `[p]lfgtimezone Asia/Jakarta`. |
| `[p]lfgset forum <forum-channel>` | Manage Server | Set the forum where session messages may be created. |
| `[p]lfgset sharechannel <text-channel>` | Manage Server | Set the text channel for automatic and manual announcement posts. |
| `[p]lfgset exp toggle` | Manage Server | Enable or disable the session EXP system. |
| `[p]lfgset exp persession <amount>` | Manage Server | Set the EXP each attendee earns from a finished session. |
| `[p]lfgset exp organizerbonus <amount>` | Manage Server | Set extra EXP the organizer earns on top of the per-session amount. |
| `[p]lfgset exp minplayers <amount>` | Manage Server | Set how many unique participants a session needs before it awards EXP. Defaults to 2. |
| `[p]lfgset exp addrole <role> <threshold>` | Manage Server | Grant a role once a member reaches an EXP threshold. |
| `[p]lfgset exp removerole <role>` | Manage Server | Remove a role from the EXP rewards list. |
| `[p]lfgset exp show` | Manage Server | Show the current EXP configuration and reward roles. |

For lifecycle commands, the identifier may be the session message's numeric ID or its full Discord message URL. Copy the link from the session card when possible.

## Using a session

After `[p]lfg` succeeds, the cog creates two surfaces:

- The public session card shows the event and only the `✅` join or leave reaction. The organizer counts toward capacity and cannot leave by removing their reaction.
- The private organizer message contains commands for reminder, sharing, rescheduling, cancellation, and completion. Slash-command creation returns this information ephemerally. Prefix-command creation sends it by DM. Run `[p]lfg control <message-id-or-url>` to receive it again.

Organizer command results are also private: ephemeral for slash commands and DM for prefix commands. If the bot cannot DM you, it sends a brief recovery note in the thread.

`[p]lfg remind` works only during the 30 minutes before the start. It posts a reminder in the event thread and attempts a DM to every attendee. A session accepts at most one reminder per hour. A member with DMs disabled may not receive the DM, but the other deliveries still proceed.

`[p]lfg share` posts the current event snapshot to the configured share channel and has a one-hour per-event cooldown. The announcement links back to the original thread, so use that link for the current participant list and status. The cog also attempts an automatic announcement when the share channel is configured.

The lobby includes the organizer. When it is full, a new `✅` reaction is removed and the cog attempts to DM the member that no slot is available. New joins are also rejected after the start time. Cancelled and finished events remain visible but no longer accept joins, reminders, or shares. Deleting the session message or its forum post removes its matching stored session.

## Session EXP and role rewards

The EXP system is **off by default**. An administrator enables it with `[p]lfgset exp toggle`. When it is enabled, marking a session as finished with `[p]lfg finish` awards EXP to every unique attendee of that session. Cancelling a session awards nothing. The organizer occupies a player slot, so the organizer receives the per-session amount plus the organizer bonus.

A session only counts as successful when it reached the minimum number of unique participants, set with `[p]lfgset exp minplayers` and defaulting to **2**. A session with fewer participants awards no EXP and is reported as ineligible in the finish confirmation. This prevents creating and immediately finishing solo events to farm EXP.

Each finished session awards EXP only once per participant, even if the same member joins or leaves repeatedly, or if the finish command is invoked more than once. Awarding happens only after the event moves from active to finished. If a payout is interrupted, the organizer can run `[p]lfg finish <message-id-or-url>` again to resume it without duplicating EXP already granted.

Administrators map Discord roles to EXP thresholds with `[p]lfgset exp addrole <role> <threshold>`. Reward roles form tiers by threshold. A member always holds the single highest role they qualify for: when they cross into a higher tier, the lower configured tier roles are removed and the new highest role is granted. Roles that are not registered as EXP rewards are never touched.

Configure the system with:

```text
[p]lfgset exp toggle
[p]lfgset exp persession 2
[p]lfgset exp organizerbonus 1
[p]lfgset exp minplayers 3
[p]lfgset exp addrole @Regular 5
[p]lfgset exp addrole @Veteran 20
[p]lfgset exp show
```

Members check progress with `[p]lfg exp [member]`, which reports total EXP, successful sessions, the current tier role, and how much is left to the next tier. `[p]lfg top` lists the top 10 members by EXP.

The bot needs **Manage Roles** to grant reward roles, and the reward roles must sit below the bot's highest role. If a role cannot be granted or removed, the cog logs the problem, tells the organizer, and still saves the earned EXP so progress is never lost.

## Timezones and time input

Use a [TZ database](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) name with `[p]lfgtimezone`, such as `Asia/Jakarta`, `America/New_York`, or `Europe/London`. A session's natural-language time is interpreted in the organizer's saved timezone. If none is saved, the cog uses `Asia/Jakarta` and says so in the confirmation. The timezone used for the event is saved with that event.

Examples of accepted input include:

```text
[p]lfg 4 "in 2 hours" "Quick match"
[p]lfg 8 "Thursday at 10pm" "Weekly raid"
[p]lfg 5 "2026-09-20 19:30" "Tournament"
```

The time must resolve to the future. Relative durations such as `in 2 hours` represent elapsed time. A reschedule interprets the new time in the event's saved timezone, so changing your personal timezone does not silently move an existing event. Older records without a saved timezone fall back to the acting member's timezone or the server default.

Every event uses Discord's timestamp markup. Discord renders the same event in each viewer's local timezone, while the confirmation identifies the timezone used to parse the input. A local time that falls in a daylight-saving gap or repeated hour is rejected unless the input makes the offset unambiguous. Choose another local time or include an explicit UTC offset when appropriate.

## Permissions

The bot needs the following permissions in the designated forum and its threads:

| Permission | Used for |
| --- | --- |
| View Channel | Find and read the forum thread and its messages. |
| Read Message History | Fetch the session message when a reaction arrives. |
| Send Messages in Threads | Post and update the session card and public reminders. |
| Embed Links | Render session and announcement embeds. |
| Add Reactions | Add the public `✅` attendance control and restore the organizer's reaction when needed. |
| Manage Messages | Remove a rejected full-lobby reaction and clean up legacy organizer reactions. |

The configured announcement text channel needs View Channel, Send Messages, and Embed Links. Members need access to the forum thread; the bot pre-adds `✅`, so members can select it even if they cannot add a new reaction. Reaction events must be enabled for the Red bot so the cog can receive join and leave actions. If you enable session EXP, the bot also needs **Manage Roles**, and every reward role must be positioned below the bot's highest role.

If a required forum permission is missing, the cog reports it before creating a session. If a permission changes after creation, the event data may remain saved while message edits, reactions, or announcements fail until the permission is restored.

## Data and privacy

The cog stores its state with Red Config. Per-member data is the saved timezone, the session EXP total, the number of successful sessions counted for reward roles, and a record of session message IDs already credited with EXP so interrupted payouts can be resumed safely. Per-server data includes the configured forum and share-channel IDs, the EXP settings and role thresholds, and session records keyed by message ID. A session record can contain:

- organizer and attendee Discord user IDs;
- the session channel (forum thread) ID, with the message ID as the record key;
- title, description, forum tags, player limit, and start timestamp;
- the timezone used to parse the event and whether the default was used;
- active, cancelled, or finished status; reminder/share timestamps used for cooldowns; and EXP payout progress for finished sessions.

When session EXP is enabled, a finished session also records that its EXP was awarded so it cannot be paid out twice. Discord role assignments made by the EXP system are visible to everyone who can see the member's roles.

The cog does not send session data to a separate service. It sends event content to Discord when it creates the session embed, posts an announcement, or sends a reminder DM. Discord retains those messages under its own policies. Stored session data remains in Red Config while the record exists, including after cancellation or completion. Deleting the session message or its forum post lets the cog remove its matching record. There is no separate export or per-member deletion command; server owners control the Red data directory and can unload the cog or manage its Config data according to their server's retention policy.

## Development and tests

The test suite targets Python 3.10 and 3.11 (the versions supported by Red-DiscordBot 3.5.24). Create a virtual environment and run the tests from the repository root.

Linux and macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Windows (PowerShell):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

`requirements-dev.txt` installs Red-DiscordBot, the cog runtime dependencies, and the test tools; `requirements.txt` lists the runtime dependencies alone.

The tests use real Red Config storage in temporary directories and mock Discord HTTP calls. They cover command authorization, message-ID parsing, lifecycle transitions, capacity and reaction concurrency, reminder and share cooldowns, session EXP awards and role tier swaps, stale-message cleanup, embed limits, and timezone parsing around daylight-saving transitions. They do not send messages to Discord.
