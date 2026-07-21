#!/usr/bin/env python3
"""Smoke test for Step 3 reflection: duplicate consolidation + episode distillation.
Run: OPENAI_API_KEY=... python scripts/mem3_smoke.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.store_v2 import Store  # noqa: E402


async def main() -> None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("no OPENAI_API_KEY"); sys.exit(1)
    settings = SimpleNamespace(openai_api_key=key, openai_base_url=None,
                               embedding_model="text-embedding-3-small", summary_model="gpt-4o-mini")
    tmp = Path(tempfile.gettempdir()) / "mem3test.db"
    if tmp.exists():
        tmp.unlink()
    store = Store(tmp, "primary")
    await store.connect()
    await store.attach_memory(settings)

    # two near-duplicate wifi facts + an unrelated one
    await store.set_memory("wifi", "סיסמת הרשת האלחוטית בבית היא home2026")
    await store.set_memory("wifi-password", "הסיסמה לוויי-פיי בדירה: home2026, הראוטר בסלון")
    await store.set_memory("dentist", "רופא השיניים של המשפחה הוא דוקטור לוי")

    # a conversation episode to distill from
    for role, text in [
        ("user", "בואו נתכנן טיול משפחתי לצפון ב-15 באוגוסט 2026"),
        ("assistant", "רעיון נהדר, אשמור את זה"),
        ("user", "וגם תזכור שאשתי אלרגית לבוטנים"),
    ]:
        await store.add_message(role=role, content=text, user_id=1)

    before = await store.list_memories()
    await store.reflector.reflect()
    after = await store.list_memories()

    home2026 = [m for m in after if "home2026" in m["value"]]
    trip = any("trip" in m["key"] or "צפון" in m["value"] or "אוגוסט" in m["value"] for m in after)
    allergy = any("בוטנים" in m["value"] or "אלרג" in m["value"] for m in after)

    print(f"memories before={len(before)} after={len(after)}")
    print(f"[{'PASS' if len(home2026)==1 else 'FAIL'}] wifi duplicates merged -> home2026 in {len(home2026)} entry")
    print(f"[{'PASS' if trip else 'FAIL'}] distilled the trip fact (צפון)")
    print(f"[{'PASS' if allergy else 'FAIL'}] distilled the allergy fact (בוטנים)")
    print("keys after:", sorted(m["key"] for m in after))

    await store.close(); tmp.unlink()
    ok = len(home2026) == 1 and trip and allergy
    print("\n" + ("All Step-3 checks passed" if ok else "SOME CHECKS FAILED"))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    asyncio.run(main())
