# Discord testing

## Environment

- Local repository: `D:\21_studies\ADA\repo`
- Local interpreter: `.redvenv\Scripts\python.exe` (Python 3.11)
- VPS SSH alias: `oracle-vps`
- VPS repository: `/home/ubuntu/custom-redcogs`
- Intended server: Indonesian Niche Gamers

Do not put bot tokens in this repository or chat. Use the existing Red instance's secret storage. Do not start a second bot process with the production token.

Before deploying, locate the live cog directory: Red Downloader may load an installed copy instead of the Git checkout. Back up the installed cog and Schedule Config, and record the current Git commit. Reload only the schedule cog after copying validated source files.

## Live acceptance checklist

Use a designated test forum post and test announcement channel. Changing `scheduleset forum` changes the setting for the entire server, so use its existing forum or a separate test server.

1. Load/reload `schedule`; confirm no import or command-registration error.
2. Create a clearly labeled test event 15 minutes ahead with capacity 2. Verify the confirmation, time, organizer, and event link.
3. Try an explicit past date, invalid timezone, capacity 0, and oversized description. Expect a clear error and no event.
4. Join from a second account; attempt a third join. Verify capacity stays 2. Remove/re-add the second account's reaction and confirm the participant list follows it.
5. Share once, then immediately again. Verify only one announcement appears, the true organizer is shown, and the bot remains responsive.
6. Send a reminder as organizer. Verify delivery counts, cooldown, and handling of a member with DMs disabled. Check that a non-organizer cannot send reminders.
7. Reschedule, cancel, and finish test events. Verify authorization, persisted status, and closed controls. Check the upcoming list.
8. Reload the cog and interact with an existing future test event; verify state persists.
9. Delete a test event message and verify stale storage is cleaned up.

Report live Discord verification separately from local mocked tests. Record tested message links and remove only test artifacts created for this session.
