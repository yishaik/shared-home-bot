# Projects, tasks, calendar, Telegram and Drive

## Sources of truth

| Domain | Source of truth |
| --- | --- |
| Calendar events | Shared Google Calendar owned by the bot account |
| Projects and tasks | Shared Home SQLite database |
| Scheduled work blocks | Google Calendar, linked to a local task |
| Household identity | Telegram interactions |
| Documents and files | Google Drive owned by the bot account |

No household member needs to authorize a personal Google account.

## Projects

Projects group tasks and may own a Google Drive folder. A project has a status,
owner, priority, optional dates and computed progress based on its tasks.

## Tasks

Tasks may belong to a project or parent task. They use a multi-state workflow:
`todo`, `in_progress`, `waiting`, `completed`, and `cancelled`.

A task deadline is local task metadata. Scheduled working time is represented by
one or more linked Google Calendar events.

## Relationships

Tasks can be linked as:

- `blocks`
- `follows`
- `related`
- `duplicates`

Directed relationships reject dependency cycles. A task's blocked state is
computed from incomplete blocking tasks rather than entered manually.

## Calendar synchronization

The shared calendar is synchronized into a local cache using Google's sync
tokens. Incremental synchronization runs periodically and on demand. Changes to
a linked work-block event in Google update the local task block. Expired sync
tokens trigger a full synchronization outside the incremental lock.

## Telegram identity and notifications

Only members who have interacted with the bot or Mini App are returned by the
household API. Telegram user IDs and private chat IDs remain server-side.

Task assignment messages are written to a durable outbox and delivered in a
private chat when the member has started the bot. Failed deliveries use bounded
retry with backoff.

## Drive resources

A project can create a Drive folder. A task can create or link:

- Google Docs
- Google Sheets
- Drive files or external links

Generated Docs and Sheets are moved into the project folder when one exists.
