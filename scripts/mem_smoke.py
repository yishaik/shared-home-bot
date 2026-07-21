#!/usr/bin/env python3
"""Smoke test for the hybrid memory engine — proves semantic Hebrew retrieval.

Refs (keys) are ASCII so correctness is verifiable without a Hebrew-capable
console. Run: OPENAI_API_KEY=... python scripts/mem_smoke.py
"""
import asyncio
import os
import sys

import aiosqlite
from openai import AsyncOpenAI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.memory import MemoryIndex  # noqa: E402

FACTS = [
    ("memory", "gate", "קוד השער של הבניין הוא 4271"),
    ("memory", "vet", "הכלב שלנו מטופל אצל דוקטור כהן הווטרינר ברחוב הרצל"),
    ("memory", "wifi", "סיסמת הרשת האלחוטית בבית היא home2026"),
    ("memory", "anniv", "יום הנישואין שלנו הוא ב-12 באוגוסט"),
    ("note", "insurance", "ביטוח דירה\nפוליסת ביטוח הדירה במגדל מספר 88123 מתחדשת בינואר"),
    ("memory", "milk", "צריך לקנות חלב ביצים ולחם"),
]
# query -> expected top ref (semantic cases share few/zero words with the fact)
CASES = [
    ("מתי האירוע הזוגי שלנו?", "anniv"),          # zero lexical overlap → pure semantic
    ("מה הקוד להיכנס לבניין?", "gate"),
    ("איפה מטפלים בכלב?", "vet"),
    ("פרטי הביטוח של הדירה", "insurance"),
]


async def main() -> None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("no OPENAI_API_KEY"); sys.exit(1)
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    mi = MemoryIndex(db, AsyncOpenAI(api_key=key), "text-embedding-3-small")
    await mi.ensure_schema()
    for kind, ref, text in FACTS:
        await mi.index(kind, ref, text)

    passed = 0
    for q, expected in CASES:
        hits = await mi.hybrid_search(q, k=3)
        top = hits[0]["ref"] if hits else "(none)"
        ok = top == expected
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] expect={expected:10s} top={top:10s} "
              f"ranked={[h['ref'] for h in hits]}")
    print(f"\n{passed}/{len(CASES)} cases passed")
    await db.close()
    sys.exit(0 if passed == len(CASES) else 2)


if __name__ == "__main__":
    asyncio.run(main())
