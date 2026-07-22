"""One-shot debug dump for the proactive engine state. Run inside the container:
railway ssh -- python scripts/debug_reminders.py
"""
import json
import sqlite3

c = sqlite3.connect("/data/home.db")
c.row_factory = sqlite3.Row


def rows(sql):
    try:
        return [dict(r) for r in c.execute(sql)]
    except Exception as exc:  # table may not exist
        return [{"error": str(exc)}]


print(json.dumps({
    "reminders": rows("SELECT * FROM reminders ORDER BY id DESC LIMIT 10"),
    "proactive_log": rows("SELECT * FROM proactive_log ORDER BY sent_at DESC LIMIT 10"),
    "recent_messages": rows(
        "SELECT id, telegram_user_id, role, tool_name, substr(content,1,160) AS content, created_at "
        "FROM messages ORDER BY id DESC LIMIT 12"
    ),
    "tg_transcripts": rows(
        "SELECT scope_key, agent_id, role, tool_name, substr(content,1,160) AS content, created_at "
        "FROM telegram_messages ORDER BY id DESC LIMIT 12"
    ),
}, ensure_ascii=False, indent=1, default=str))
