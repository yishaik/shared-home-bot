#!/usr/bin/env python3
"""Smoke test for Step 2: core memory + rolling summary mechanics, and a real
summariser call against SUMMARY_MODEL. Run: OPENAI_API_KEY=... python scripts/mem2_smoke.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.store_v2 import Store  # noqa: E402


async def main() -> None:
    tmp = Path(tempfile.gettempdir()) / "mem2test.db"
    if tmp.exists():
        tmp.unlink()
    store = Store(tmp, "primary")
    await store.connect()

    # ── core memory ──
    await store.set_core_memory("בני הבית: יאיר ורעות.")
    await store.append_core_memory("צפויים לתינוק בספטמבר.")
    assert "תינוק" in await store.get_core_memory(), "append failed"
    assert await store.replace_core_memory("ספטמבר", "אוקטובר"), "replace returned False"
    assert "אוקטובר" in await store.get_core_memory(), "replace failed"
    assert not await store.replace_core_memory("does-not-exist", "x"), "replace should fail on missing"
    print("[PASS] core memory: set / append / replace")

    # ── messages_after + conv summary pointer ──
    for i in range(30):
        await store.add_message(role="user" if i % 2 == 0 else "assistant", content=f"msg {i}", user_id=1)
    msgs = await store.messages_after(0)
    assert len(msgs) == 30 and msgs[0]["id"] < msgs[-1]["id"], "messages_after ordering"
    await store.set_conv_summary("summary v1", msgs[9]["id"])
    assert await store.get_conv_summary_last_id() == msgs[9]["id"], "summary last_id"
    assert len(await store.messages_after(msgs[9]["id"])) == 20, "messages_after(pointer)"
    print("[PASS] messages_after + rolling-summary pointer")

    await store.close()
    tmp.unlink()

    # ── real summariser (SUMMARY_MODEL) ──
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=key)
        block = "יאיר: צריך לקבוע תור לרופא שיניים\nassistant: קבעתי ליום ראשון 10:00\nיאיר: מעולה, תוסיף גם לרשימת הקניות חלב"
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Summarise these household messages in one short Hebrew sentence:\n" + block}],
            temperature=0.2)
        out = (resp.choices[0].message.content or "").strip()
        assert len(out) > 5, "empty summary"
        print(f"[PASS] SUMMARY_MODEL gpt-4o-mini works ({len(out)} chars)")
    else:
        print("[skip] no OPENAI_API_KEY for summariser call")

    print("\nAll Step-2 mechanics OK")


if __name__ == "__main__":
    asyncio.run(main())
