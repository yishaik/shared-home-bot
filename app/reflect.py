"""Reflection / consolidation — the self-maintaining layer of the shared brain.

Runs opportunistically in the background (every ~20 messages) and does two
things, inspired by generative-agents reflection:

  • distill    — read the recent conversation and save NEW durable facts the
                 household will want later (proactive memory, no need to be told)
  • consolidate — find near-duplicate facts via the embedding index and merge
                 each cluster into one canonical entry (kills fragmentation)

Everything is best-effort and guarded: a reflection failure never affects a
reply, and unique facts are never deleted — only merged duplicates are removed.
"""

from __future__ import annotations

import json
import logging

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
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            return _parse_json(resp.choices[0].message.content or "")
        except Exception:
            log.exception("reflect llm call failed")
            return None

    async def reflect(self) -> None:
        await self._distill()
        await self._consolidate()

    async def _distill(self) -> None:
        msgs = await self.store.recent_messages(24)
        convo = "\n".join(
            f"{m['role']}: {(m['content'] or '')[:300]}"
            for m in msgs if m["role"] in ("user", "assistant"))
        if not convo.strip():
            return
        existing = await self.store.list_memories()
        known = "; ".join(f"{m['key']}={m['value']}" for m in existing[:60])
        prompt = (
            "You curate a shared household assistant's long-term memory. From the RECENT CONVERSATION, "
            "extract ONLY NEW durable facts worth remembering (logistics, preferences, decisions, people, "
            "dates, home details). Skip anything already covered by KNOWN FACTS, and skip small talk / one-off "
            "requests. Separately, list any fact essential enough to pin to always-on core memory.\n\n"
            f"KNOWN FACTS: {known or '(none)'}\n\nRECENT CONVERSATION:\n{convo}\n\n"
            'Return JSON: {"facts":[{"key":"short-slug","value":"...","category":"general"}],"core":["..."]}. '
            "Use empty arrays if nothing is new."
        )
        v = await self._chat_json(prompt)
        if not v:
            return
        n = 0
        for f in (v.get("facts") or [])[:8]:
            key = (f.get("key") or "").strip()
            val = (f.get("value") or "").strip()
            if key and val:
                await self.store.set_memory(key, val, (f.get("category") or "general"))
                n += 1
        core = [c.strip() for c in (v.get("core") or []) if (c or "").strip()][:4]
        for c in core:
            await self.store.append_core_memory(c)
        if n or core:
            log.info("reflect distilled %s new fact(s), %s core add(s)", n, len(core))

    async def _consolidate(self, max_groups: int = 3) -> None:
        mem = getattr(self.store, "memory", None)
        if not mem:
            return
        items = await mem.all_items("memory")
        by_ref = {it["ref"]: it["text"] for it in items}
        # cast a wide net on similarity (recall) — the LLM is the precision gate
        # below. Hebrew embeddings sit lower than English, so ~0.7 catches true
        # duplicates while staying far above unrelated facts (~0.2).
        groups = mem.duplicate_groups(items, threshold=0.70)
        for group in groups[:max_groups]:
            texts = [by_ref.get(r, "") for r in group if by_ref.get(r)]
            if len(texts) < 2:
                continue
            prompt = (
                "These household memory entries were flagged as possibly duplicate (each is 'key: value'). "
                "If they describe the SAME thing, merge them into ONE canonical entry keeping every distinct "
                "detail (prefer the most specific/recent value). If they are actually about DIFFERENT things, "
                "do not merge.\n\n"
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
            await self.store.set_memory(key, val, "general")
            canon = key.strip().lower()
            for r in group:
                if r != canon:
                    await self.store.delete_memory(r)
            log.info("reflect merged %s -> %s", group, canon)
