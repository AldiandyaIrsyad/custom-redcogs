# Schedule review findings

This note records the original Schedule cog assumptions and the read-only
deployment inspection used to plan Discord testing. It intentionally omits bot
tokens and raw guild, channel, user, and message IDs.

## Original behavior assumptions

- An administrator configures one forum channel with `scheduleset forum` and
  one text channel with `scheduleset sharechannel`.
- `schedule` is issued inside a thread whose parent is the configured forum.
  The thread name supplies the fallback game title.
- A member can save a TZ database timezone with `settimezone`; unset members
  use `Asia/Jakarta`.
- Natural-language time is parsed with `dateparser`, and the original code
  accepted any successfully parsed timestamp, including explicit past times.
- The organizer is stored as the first attendee. `✅` joins or leaves while
  respecting the player limit; the organizer is kept in the attendee list.
- `❗` is intended for the organizer during the 30 minutes before start and
  sends attendee DMs plus a public reminder.
- `📢` publishes an announcement to the configured text channel. The original
  implementation used the reacting member for the organizer line and stored a
  one-hour per-event share timestamp.
- Event data is persisted under `scheduled_events` keyed by the schedule
  message ID. Reactions are raw Discord reaction events, so the bot must fetch
  the reacting member and schedule message.

The original implementation had no bounds for title, description, or player
mentions before building embeds; no stale-event status migration; and held a
Config transaction while performing reaction or share work. These assumptions
make the live checks around old records, permissions, and reload persistence
necessary.

## Oracle deployment findings

- PM2 application `ING-1` is online. Its script is
  `/home/ubuntu/red-env/bin/python3`, with Red launched as
  `/home/ubuntu/red-env/bin/redbot IndonesianNicheGamers` from `/home/ubuntu`.
- The live runtime reports Red-DiscordBot 3.5.20, discord.py 2.5.2,
  dateparser 1.2.1, and pytz 2025.2.
- The Git checkout `/home/ubuntu/custom-redcogs` is clean at commit `6853b9f`
  (`currentstate`). The active downloader source is instead
  `/home/ubuntu/.local/share/Red-DiscordBot/data/IndonesianNicheGamers/cogs/RepoManager/repos/ing/schedule/__init__.py`
  at commit `5bc46b2`, the older monolithic cog. Updating only the checkout
  does not update the source currently loaded by Red.
- The Red instance data directory is
  `/home/ubuntu/.local/share/Red-DiscordBot/data/IndonesianNicheGamers`.
  Schedule Config is stored in its `cogs/Schedule/settings.json`; PM2 logs are
  in `/home/ubuntu/.pm2/logs/ING-1-out.log` and `ING-1-error.log`, with Red
  logs under the instance `core/logs` directory.

### Schedule Config shape and migration evidence

The Config file is wrapped as `config identifier → GUILD → guild ID → guild
fields`. It contains one guild record. That record has integer values for both
`target_forum_id` and `share_channel_id`, plus a `scheduled_events` mapping of
46 events. The raw values are intentionally not copied here.

The 46 events are all historical and past relative to the inspection time.
They are still represented as `active` by the legacy data because none stores a
`status` field. Event records were written by several versions:

- All records include organizer, player limit, title, start timestamp, channel,
  and attendee fields.
- Early records omit description, share timestamp, and tags; later records
  add those fields incrementally.
- Attendee counts and player limits vary, so migration and embed rendering must
  tolerate old records rather than assuming the newest schema.

The data confirms that a reload test must interact with an existing future
event created by the current code, while old records should remain readable and
should not appear as upcoming events solely because their absent status defaults
to active.

## Live test prerequisites

Use a designated test forum post and announcement text channel. The bot needs
View Channel, Send Messages, Embed Links, Add Reactions, and Read Message
History; Manage Messages is needed when the bot removes another member's
reaction. Test slash and prefix forms separately, because ephemeral responses
only have interaction semantics.

Do not start a second process with the production token. Before any live test,
record the deployed commit and Config backup, validate the new source locally,
then reload only the Schedule cog from the actual downloader/load path. Keep
test events clearly labeled and remove only artifacts created for the test.
