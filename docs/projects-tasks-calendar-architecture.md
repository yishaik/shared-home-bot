# Projects, tasks, calendar, Telegram and Drive

## Sources of truth

| Domain | Source of truth |
| --- | --- |
| Calendar events | Shared Google Calendar owned by the bot account |
| Projects and tasks | Shared Home SQLite database |
| Scheduled work blocks | Google Calendar events linked to local tasks |
| Household identity | Telegram interactions |
| Documents and files | Managed Google Drive owned by the bot account |

No household member authorizes a personal Google account.

## Projects and tasks

Projects group tasks and may own a managed Drive folder. Tasks can belong to a
project or parent task and use `todo`, `in_progress`, `waiting`, `completed`, or
`cancelled` status. Existing todo rows are migrated in place.

## Dependencies

Tasks support `blocks`, `follows`, `related`, and `duplicates` relationships.
Directed dependency cycles are rejected. Blocked state is computed from open
blocking tasks rather than entered manually.

## Calendar

Events use Google Calendar as the source of truth and require start and end.
A task deadline remains local metadata. One task may have several scheduled work
blocks in Google Calendar. Moving a block in Google updates the linked local block
through incremental synchronization.

## Telegram

Only household members who interacted with the bot or Mini App are exposed in
assignee selectors. Telegram IDs and private chat IDs remain server-side. Task
assignment notifications use a durable outbox with bounded retry and quick-action
buttons.

## Drive resources

Projects can create folders beneath the managed Shared Home root. Tasks can create
or link Docs, Sheets, Drive files, and external resources. Generated Docs and
Sheets are moved into the project folder when applicable.
