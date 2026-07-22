from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger("homebot.proactive")

PROACTIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,
    target_user_id INTEGER,
    created_by INTEGER,
    recurrence TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    last_fired_at REAL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at);

CREATE TABLE IF NOT EXISTS proactive_log (
    kind TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    sent_at REAL NOT NULL,
    PRIMARY KEY (kind, dedup_key)
);
"""

# Settings keys (DB `settings` table overrides env defaults; editable via setting_set)
KEY_ENABLED = "proactive_enabled"          # on | off
KEY_BRIEF_TIME = "brief_time"              # HH:MM local
KEY_BRIEF_ALWAYS = "brief_always"          # on | off — send brief even when empty
KEY_QUIET_HOURS = "quiet_hours"            # HH:MM-HH:MM local
KEY_NUDGE_MINUTES = "calendar_nudge_minutes"


def parse_hhmm(value: str) -> dt.time | None:
    try:
        hours, _, minutes = value.strip().partition(":")
        return dt.time(int(hours), int(minutes or 0))
    except (ValueError, AttributeError):
        return None


def parse_quiet_hours(value: str) -> tuple[dt.time, dt.time] | None:
    start_raw, _, end_raw = (value or "").partition("-")
    start, end = parse_hhmm(start_raw), parse_hhmm(end_raw)
    if start is None or end is None:
        return None
    return start, end


def in_quiet_hours(now_local: dt.datetime, window: tuple[dt.time, dt.time] | None) -> bool:
    if not window:
        return False
    start, end = window
    current = now_local.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end  # crosses midnight


def to_utc_iso(value: str, tz: ZoneInfo) -> str:
    """Parse an ISO datetime (naive = household-local) and return aware UTC ISO."""
    parsed = dt.datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def to_local(value: str, tz: ZoneInfo) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(tz)


def next_occurrence(due_local: dt.datetime, recurrence: str) -> dt.datetime | None:
    rule = (recurrence or "").strip().lower()
    if rule == "daily":
        return due_local + dt.timedelta(days=1)
    if rule.startswith("weekly"):
        return due_local + dt.timedelta(days=7)
    return None


# ── reminder CRUD (used by agent tools; schema-guarded so tools work pre-tick) ──

async def ensure_schema(store) -> None:
    await store.db.executescript(PROACTIVE_SCHEMA)
    await store.db.commit()


async def member_names(store) -> list[str]:
    try:
        members = await store.list_members()
    except Exception:
        return []
    return [str(m.get("display_name") or m.get("username") or "").strip()
            for m in members if (m.get("display_name") or m.get("username"))]


async def resolve_member(store, query: str) -> dict[str, Any] | None:
    """Map a spoken name to a household member (case-insensitive substring match)."""
    q = (query or "").strip().lower()
    if not q:
        return None
    try:
        members = await store.list_members()
    except Exception:
        return None
    for member in members:
        display = str(member.get("display_name") or "")
        username = str(member.get("username") or "")
        haystacks = [display.lower(), username.lower()]
        if any(h and (q in h or h in q) for h in haystacks):
            return {"user_id": int(member["telegram_user_id"]), "name": display or username or str(member["telegram_user_id"])}
    return None


async def reminder_add(store, settings, *, text: str, due_at: str, created_by: int,
                       target_user_id: int | None = None, recurrence: str = "") -> dict[str, Any]:
    await ensure_schema(store)
    tz = ZoneInfo(settings.household_timezone)
    due_utc = to_utc_iso(due_at, tz)
    cursor = await store.db.execute(
        "INSERT INTO reminders(text, due_at, target_user_id, created_by, recurrence, status, created_at) "
        "VALUES(?,?,?,?,?,'pending',?)",
        (text.strip(), due_utc, target_user_id, created_by, (recurrence or "").strip().lower(), time.time()),
    )
    await store.db.commit()
    return {"id": cursor.lastrowid, "text": text.strip(), "due_at_local": to_local(due_utc, tz).isoformat(timespec="minutes"), "recurrence": recurrence or ""}


async def reminder_list(store, settings) -> list[dict[str, Any]]:
    await ensure_schema(store)
    tz = ZoneInfo(settings.household_timezone)
    rows = await (await store.db.execute(
        "SELECT * FROM reminders WHERE status='pending' ORDER BY due_at ASC LIMIT 50"
    )).fetchall()
    return [{
        "id": row["id"], "text": row["text"],
        "due_at_local": to_local(row["due_at"], tz).isoformat(timespec="minutes"),
        "recurrence": row["recurrence"], "target_user_id": row["target_user_id"],
    } for row in rows]


async def reminder_cancel(store, reminder_id: int) -> bool:
    await ensure_schema(store)
    cursor = await store.db.execute(
        "UPDATE reminders SET status='cancelled' WHERE id=? AND status='pending'", (reminder_id,)
    )
    await store.db.commit()
    return cursor.rowcount > 0


class ProactiveEngine:
    """Heartbeat that lets the bot initiate: reminders, calendar nudges,
    task due-nudges and a morning household brief. One asyncio task, 60s ticks."""

    def __init__(self, settings, store, service, bot, *, tick_seconds: int = 60):
        self.settings = settings
        self.store = store
        self.service = service
        self.bot = bot
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task | None = None
        self._last_calendar_sync = 0.0
        self._schema_ready = False

    # ── lifecycle ──────────────────────────────────────────────────────────
    async def _ensure_schemas(self) -> None:
        """Own tables + the calendar cache tables tick() reads (fresh DBs)."""
        if self._schema_ready:
            return
        await ensure_schema(self.store)
        from app.calendar_service import CalendarService
        await CalendarService(self.settings, self.store).ensure_schema()
        self._schema_ready = True

    async def start(self) -> None:
        await self._ensure_schemas()
        self._task = asyncio.create_task(self._run(), name="proactive-engine")
        log.info("proactive engine started (tick=%ss)", self.tick_seconds)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(15)  # let webhook/bot finish booting
        while True:
            try:
                if await self._setting(KEY_ENABLED, self._env_default(KEY_ENABLED)) != "off":
                    await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("proactive tick failed — continuing")
            await asyncio.sleep(self.tick_seconds)

    # ── settings: DB overrides env defaults ────────────────────────────────
    def _env_default(self, key: str) -> str:
        defaults = {
            KEY_ENABLED: "on" if self.settings.proactive_enabled else "off",
            KEY_BRIEF_TIME: self.settings.brief_time,
            KEY_BRIEF_ALWAYS: "off",
            KEY_QUIET_HOURS: self.settings.quiet_hours,
            KEY_NUDGE_MINUTES: str(self.settings.calendar_nudge_minutes),
        }
        return defaults.get(key, "")

    async def _setting(self, key: str, default: str) -> str:
        return (await self.store.get_setting(key, default)) or default

    def _tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.settings.household_timezone)
        except Exception:
            return ZoneInfo("UTC")

    # ── one tick ───────────────────────────────────────────────────────────
    async def tick(self, now_utc: dt.datetime | None = None) -> None:
        await self._ensure_schemas()
        now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
        now_local = now_utc.astimezone(self._tz())
        quiet = in_quiet_hours(now_local, parse_quiet_hours(await self._setting(KEY_QUIET_HOURS, self._env_default(KEY_QUIET_HOURS))))

        await self._fire_due_reminders(now_utc)
        await self._sync_calendar_if_due()
        await self._calendar_nudges(now_utc)
        if not quiet:
            await self._task_nudges(now_utc)
            await self._morning_brief(now_local)

    # ── dedup log ──────────────────────────────────────────────────────────
    async def _already_sent(self, kind: str, dedup_key: str) -> bool:
        row = await (await self.store.db.execute(
            "SELECT 1 FROM proactive_log WHERE kind=? AND dedup_key=?", (kind, dedup_key)
        )).fetchone()
        return row is not None

    async def _mark_sent(self, kind: str, dedup_key: str) -> None:
        await self.store.db.execute(
            "INSERT OR IGNORE INTO proactive_log(kind, dedup_key, sent_at) VALUES(?,?,?)",
            (kind, dedup_key, time.time()),
        )
        await self.store.db.commit()

    # ── delivery ───────────────────────────────────────────────────────────
    async def send(self, text: str, *, target_user_id: int | None = None) -> int:
        recipients = [target_user_id] if target_user_id else list(self.settings.allowed_user_ids)
        delivered = 0
        for chat_id in recipients:
            try:
                await self.bot.send_message(chat_id=chat_id, text=text)
                delivered += 1
            except Exception:
                log.warning("proactive send failed chat_id=%s", chat_id, exc_info=True)
        if delivered:
            # keep the agent's conversation context aware of what the household was told
            # (user_id=0 = system sender; messages.telegram_user_id is NOT NULL)
            await self.store.add_message(role="assistant", content=f"[הודעה יזומה] {text}", user_id=0)
        return delivered

    # ── reminders ──────────────────────────────────────────────────────────
    async def _fire_due_reminders(self, now_utc: dt.datetime) -> None:
        rows = await (await self.store.db.execute(
            "SELECT * FROM reminders WHERE status='pending' AND due_at<=? ORDER BY due_at ASC LIMIT 20",
            (now_utc.isoformat(timespec="seconds"),),
        )).fetchall()
        tz = self._tz()
        for raw in rows:
            row = dict(raw)
            delivered = await self.send(f"⏰ תזכורת: {row['text']}", target_user_id=row["target_user_id"])
            # If a reminder aimed at a specific person could not be delivered (they
            # never opened the bot), tell whoever set it instead of failing silently.
            if row["target_user_id"] and delivered == 0 and row["created_by"] and row["created_by"] != row["target_user_id"]:
                await self.send(
                    f"⚠️ לא הצלחתי לשלוח את התזכורת ({row['text']}) — הנמען עדיין לא פתח את הבוט. "
                    f"בקש/י ממנו/ה לשלוח /start פעם אחת.",
                    target_user_id=row["created_by"],
                )
            follow_up = None
            if row["recurrence"]:
                follow_up = next_occurrence(to_local(row["due_at"], tz), row["recurrence"])
                while follow_up is not None and follow_up.astimezone(dt.timezone.utc) <= now_utc:
                    follow_up = next_occurrence(follow_up, row["recurrence"])  # catch up after downtime
            if follow_up:
                await self.store.db.execute(
                    "UPDATE reminders SET due_at=?, last_fired_at=? WHERE id=?",
                    (follow_up.astimezone(dt.timezone.utc).isoformat(timespec="seconds"), time.time(), row["id"]),
                )
            else:
                await self.store.db.execute(
                    "UPDATE reminders SET status='sent', last_fired_at=? WHERE id=?", (time.time(), row["id"]),
                )
            await self.store.db.commit()

    # ── calendar ───────────────────────────────────────────────────────────
    async def _sync_calendar_if_due(self, every_seconds: int = 900) -> None:
        if not getattr(self.settings, "google_enabled", False):
            return
        if time.time() - self._last_calendar_sync < every_seconds:
            return
        self._last_calendar_sync = time.time()
        from app.calendar_service import CalendarService
        try:
            await CalendarService(self.settings, self.store).incremental_sync()
        except Exception:
            log.exception("periodic calendar sync failed")

    async def _calendar_nudges(self, now_utc: dt.datetime) -> None:
        minutes_raw = await self._setting(KEY_NUDGE_MINUTES, self._env_default(KEY_NUDGE_MINUTES))
        try:
            lead = max(5, int(minutes_raw))
        except ValueError:
            lead = 30
        tz = self._tz()
        rows = await (await self.store.db.execute(
            "SELECT google_event_id, title, location, start_at FROM calendar_events "
            "WHERE status!='cancelled' AND all_day=0 AND start_at IS NOT NULL LIMIT 500"
        )).fetchall()
        for raw in rows:
            row = dict(raw)
            try:
                start = dt.datetime.fromisoformat(row["start_at"])
            except ValueError:
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=tz)
            delta = (start - now_utc).total_seconds()
            if not (0 < delta <= lead * 60):
                continue
            key = f"{row['google_event_id']}|{row['start_at']}"
            if await self._already_sent("event", key):
                continue
            when = start.astimezone(tz).strftime("%H:%M")
            where = f" · {row['location']}" if row["location"] else ""
            await self.send(f"📅 בקרוב ({when}): {row['title']}{where}")
            await self._mark_sent("event", key)

    # ── tasks ──────────────────────────────────────────────────────────────
    async def _task_nudges(self, now_utc: dt.datetime) -> None:
        rows = await (await self.store.db.execute(
            "SELECT id, title, due_at FROM todos WHERE done=0 AND due_at IS NOT NULL AND due_at!='' LIMIT 200"
        )).fetchall()
        tz = self._tz()
        for raw in rows:
            row = dict(raw)
            if "T" not in str(row["due_at"]):
                continue  # date-only tasks belong to the morning brief
            try:
                due = dt.datetime.fromisoformat(str(row["due_at"]))
            except ValueError:
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=tz)
            delta = (due - now_utc).total_seconds()
            if not (-6 * 3600 <= delta <= 45 * 60):
                continue
            key = f"{row['id']}|{row['due_at']}"
            if await self._already_sent("todo", key):
                continue
            when = due.astimezone(tz).strftime("%H:%M")
            await self.send(f"✅ משימה מתקרבת ({when}): {row['title']}")
            await self._mark_sent("todo", key)

    # ── morning brief ──────────────────────────────────────────────────────
    async def _morning_brief(self, now_local: dt.datetime) -> None:
        brief_at = parse_hhmm(await self._setting(KEY_BRIEF_TIME, self._env_default(KEY_BRIEF_TIME)))
        if brief_at is None or now_local.time() < brief_at:
            return
        day_key = now_local.strftime("%Y-%m-%d")
        if await self._already_sent("brief", day_key):
            return
        text = await self._compose_brief(now_local)
        always = (await self._setting(KEY_BRIEF_ALWAYS, "off")) == "on"
        if text or always:
            await self.send(text or f"☀️ בוקר טוב! אין אירועים או משימות להיום ({day_key}).")
        await self._mark_sent("brief", day_key)

    async def _compose_brief(self, now_local: dt.datetime) -> str:
        tz = self._tz()
        day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + dt.timedelta(days=1)
        lines: list[str] = []

        rows = await (await self.store.db.execute(
            "SELECT title, location, start_at, all_day FROM calendar_events "
            "WHERE status!='cancelled' AND start_at IS NOT NULL ORDER BY start_at LIMIT 500"
        )).fetchall()
        events: list[str] = []
        for raw in rows:
            row = dict(raw)
            try:
                start = dt.datetime.fromisoformat(row["start_at"])
            except ValueError:
                continue
            if row["all_day"]:
                if row["start_at"] == day_start.strftime("%Y-%m-%d"):
                    events.append(f"• כל היום — {row['title']}")
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=tz)
            local = start.astimezone(tz)
            if day_start <= local < day_end:
                where = f" · {row['location']}" if row["location"] else ""
                events.append(f"• {local.strftime('%H:%M')} — {row['title']}{where}")
        if events:
            lines.append("📅 היום:\n" + "\n".join(events[:8]))

        todo_rows = await (await self.store.db.execute(
            "SELECT title, due_at FROM todos WHERE done=0 AND due_at IS NOT NULL AND due_at!='' ORDER BY due_at LIMIT 100"
        )).fetchall()
        due_today: list[str] = []
        for raw in todo_rows:
            row = dict(raw)
            try:
                due = dt.datetime.fromisoformat(str(row["due_at"]))
            except ValueError:
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=tz)
            if due.astimezone(tz) < day_end:
                marker = "⚠️ " if due.astimezone(tz) < day_start else ""
                due_today.append(f"• {marker}{row['title']}")
        if due_today:
            lines.append("✅ משימות להיום:\n" + "\n".join(due_today[:8]))

        shop = await (await self.store.db.execute(
            "SELECT COUNT(*) AS n FROM shopping WHERE done=0"
        )).fetchone()
        open_items = int(shop["n"] if shop else 0)
        if open_items:
            lines.append(f"🛒 {open_items} פריטים ברשימת הקניות")

        if not lines:
            return ""
        return "☀️ בוקר טוב! הנה מה שמחכה היום:\n\n" + "\n\n".join(lines)
