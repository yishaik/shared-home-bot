"""Reflection / consolidation for the shared household memory.

Automatic reflection is opt-out, audited, and best-effort. It extracts durable
facts and consolidates true duplicates without blocking live bot replies.
"""

from __future__ import annotations

import json
import logging

from app.memory_control import auto_memory_enabled, record_memory_audit

log = logging.getLogger("homebot.reflect")


def _parse_json(raw: str) -> dict | None:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
    a, b = s.find("{"), s.rfind("}")
    if a >= 0 and b > a:
        s = s[a:b + 1]
    try:
        return json.loads(s)
    except Exception:
        return None


class Reflector:
    def __init__(self, store, client, model: str):
        self.store = store
        self.client = client
        self.model = model

    async def _chat_json(self, prompt: str) -> dict | None:
        if not self.client:
            return None
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        return _parse_json(resp.choices[0].message.content or "")

    async def reflect(self) -> dict[str, int | bool]:
        if not await auto_memory_enabled(self.store):
            log.info("automatic memory reflection is disabled")
            return {"enabled": False, "facts": 0, "core": 0, "merged": 0}
        facts, core = await self._distill()
        merged = await self._consolidate()
        return {"enabled": True, "facts": facts, "core": core, "merged": merged}

    async def _distill(self) -> tuple[int, int]:
        msgs = await self.store.recent_messages(24)
        convo = "\n".join(
            f"{m['role']}: {(m['content'] or '')[:300]}"
            for m in msgs if m["role"] in ("user", "assistant")
        )
        if not convo.strip():
            return 0, 0
        existing = await self.store.list_memories()
        known = "; ".join(f"{m['key']}={m['value']}" for m in existing[:60])
        prompt = (
            "You curate a shared household assistant's long-term memory. From the RECENT CONVERSATION, "
            "extract ONLY NEW durable facts worth remembering (logistics, preferences, decisions, people, "
            "dates, home details). Never store passwords, authentication secrets, payment-card details, "
            "government identifiers, exact medical records, or intimate/private content unless the user "
            "explicitly asked the assistant to remember it. Skip anything already covered by KNOWN FACTS, "
            "and skip small talk or one-off requests. Separately, list only non-sensitive facts essential "
            "enough to pin to always-on core memory.\n\n"
            f"KNOWN FACTS: {known or '(none)'}\n\nRECENT CONVERSATION:\n{convo}\n\n"
            'Return JSON: {"facts":[{"key":"short-slug","value":"...","category":"general"}],"core":["..."]}. '
            "Use empty arrays if nothing is new."
        )
        v = await self._chat_json(prompt)
        if not v:
            return 0, 0
        n = 0
        for f in (v.get("facts") or [])[:8]:
            key = (f.get("key") or "").strip()
            val = (f.get("value") or "").strip()
            if not key or not val:
                continue
            old = await self.store.get_memory(key)
            await self.store.set_memory(key, val, (f.get("category") or "general"))
            await record_memory_audit(
                self.store,
                action="created" if not old else "updated",
                memory_key=key,
                old_value=(old or {}).get("value", ""),
                new_value=val,
                source="reflection",
            )
            n += 1
        core_items = [c.strip() for c in (v.get("core") or []) if (c or "").strip()][:4]
        for item in core_items:
            await self.store.append_core_memory(item)
            await record_memory_audit(
                self.store,
                action="core_appended",
                new_value=item,
                source="reflection",
            )
        if n or core_items:
            log.info("reflect distilled %s new fact(s), %s core add(s)", n, len(core_items))
        return n, len(core_items)

    async def _consolidate(self, max_groups: int = 3) -> int:
        mem = getattr(self.store, "memory", None)
        if not mem:
            return 0
        items = await mem.all_items("memory")
        by_ref = {it["ref"]: it["text"] for it in items}
        groups = mem.duplicate_groups(items, threshold=0.70)
        merged = 0
        for group in groups[:max_groups]:
            texts = [by_ref.get(r, "") for r in group if by_ref.get(r)]
            if len(texts) < 2:
                continue
            prompt = (
                "These household memory entries were flagged as possibly duplicate (each is 'key: value'). "
                "If they describe the SAME thing, merge them into ONE canonical entry keeping every distinct "
                "detail. If they are actually about DIFFERENT things, do not merge.\n\n"
                + "\n".join(f"- {t}" for t in texts)
                + '\n\nReturn JSON: {"merge":true,"key":"short-slug","value":"merged value"} '
                'or {"merge":false}.'
            )
            v = await self._chat_json(prompt)
            if not v or not v.get("merge"):
                continue
            key = (v.get("key") or "").strip()
            val = (v.get("value") or "").strip()
            if not key or not val:
                continue
            old_rows = [await self.store.get_memory(ref) for ref in group]
            await self.store.set_memory(key, val, "general")
            canon = key.strip().lower()
            deleted: list[dict] = []
            for ref, old in zip(group, old_rows):
                if ref != canon and old and await self.store.delete_memory(ref):
                    deleted.append(old)
            await record_memory_audit(
                self.store,
                action="consolidated",
                memory_key=canon,
                new_value=val,
                source="reflection",
                metadata={"merged_keys": group, "deleted": deleted},
            )
            merged += 1
            log.info("reflect merged %s -> %s", group, canon)
        return merged
