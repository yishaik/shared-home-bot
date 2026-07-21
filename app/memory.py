"""Hybrid retrieval engine for the shared brain.

Combines two rankers over the household's durable text (facts + notes, and any
other indexed kind) and fuses them with Reciprocal Rank Fusion:

  • lexical  — SQLite FTS5 (bm25), exact/near-exact term hits
  • semantic — OpenAI embeddings + cosine, meaning-level hits (robust to Hebrew
               morphology, synonyms, typos where lexical fails)

Everything lives in the same SQLite file. Vectors are stored as float32 BLOBs
and scored in Python — at household scale (hundreds–low thousands of items)
this is instant, and it avoids a fragile native vector extension. The *method*
(hybrid + RRF) is what drives quality; the index backend can be upgraded later.

Degrades gracefully: if embeddings are unavailable, index() still stores text
for FTS5 and hybrid_search() falls back to lexical-only.
"""

from __future__ import annotations

import array
import logging
import math
import time
from typing import Any, Iterable

log = logging.getLogger("homebot.memory")

RRF_K = 60          # reciprocal-rank-fusion constant (standard)
POOL = 40           # candidates pulled from each ranker before fusion


def pack_vec(vec: list[float]) -> bytes:
    return array.array("f", vec).tobytes()


def unpack_vec(blob: bytes) -> list[float]:
    a = array.array("f")
    a.frombytes(blob)
    return a.tolist()


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _fts_query(text: str) -> str:
    # OR the terms, each quoted, so FTS5 never chokes on punctuation/operators.
    words = [w.replace('"', "") for w in text.split() if len(w) > 1]
    return " OR ".join(f'"{w}"' for w in words[:24])


class MemoryIndex:
    def __init__(self, db, openai_client=None, model: str = "text-embedding-3-small"):
        self.db = db
        self.client = openai_client
        self.model = model

    async def ensure_schema(self) -> None:
        await self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS mem_index (
                kind TEXT NOT NULL,
                ref TEXT NOT NULL,
                text TEXT NOT NULL,
                vec BLOB,
                importance REAL NOT NULL DEFAULT 1.0,
                updated_at REAL NOT NULL,
                PRIMARY KEY (kind, ref)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
                kind, ref, text, tokenize = 'unicode61 remove_diacritics 2'
            );
            """
        )
        await self.db.commit()

    # ── embeddings ────────────────────────────────────────────────────────
    async def _embed(self, texts: list[str]) -> list[list[float]] | None:
        if not self.client or not texts:
            return None
        try:
            resp = await self.client.embeddings.create(model=self.model, input=texts)
            return [d.embedding for d in resp.data]
        except Exception as exc:  # noqa: BLE001
            log.warning("embedding failed (%s) — lexical only for now", exc)
            return None

    # ── write path ────────────────────────────────────────────────────────
    async def index(self, kind: str, ref: str, text: str, importance: float = 1.0) -> None:
        text = (text or "").strip()
        if not text:
            return await self.unindex(kind, ref)
        vecs = await self._embed([text])
        blob = pack_vec(vecs[0]) if vecs else None
        await self.db.execute(
            """INSERT INTO mem_index(kind, ref, text, vec, importance, updated_at)
               VALUES(?, ?, ?, ?, ?, ?)
               ON CONFLICT(kind, ref) DO UPDATE SET
                 text=excluded.text, vec=COALESCE(excluded.vec, mem_index.vec),
                 importance=excluded.importance, updated_at=excluded.updated_at""",
            (kind, ref, text, blob, importance, time.time()),
        )
        await self.db.execute("DELETE FROM mem_fts WHERE kind=? AND ref=?", (kind, ref))
        await self.db.execute("INSERT INTO mem_fts(kind, ref, text) VALUES(?, ?, ?)", (kind, ref, text))
        await self.db.commit()

    async def unindex(self, kind: str, ref: str) -> None:
        await self.db.execute("DELETE FROM mem_index WHERE kind=? AND ref=?", (kind, ref))
        await self.db.execute("DELETE FROM mem_fts WHERE kind=? AND ref=?", (kind, ref))
        await self.db.commit()

    async def backfill(self, items: Iterable[tuple[str, str, str]]) -> int:
        """Index any (kind, ref, text) whose text changed or is missing an embedding."""
        n = 0
        for kind, ref, text in items:
            row = await (await self.db.execute(
                "SELECT text, vec FROM mem_index WHERE kind=? AND ref=?", (kind, ref))).fetchone()
            if row and row["text"] == (text or "").strip() and (row["vec"] is not None or not self.client):
                continue
            await self.index(kind, ref, text)
            n += 1
        if n:
            log.info("memory backfill indexed %s item(s)", n)
        return n

    # ── read path ─────────────────────────────────────────────────────────
    async def _lexical(self, query: str, kinds: list[str] | None) -> list[tuple[str, str]]:
        match = _fts_query(query)
        if not match:
            return []
        sql = "SELECT kind, ref FROM mem_fts WHERE mem_fts MATCH ? "
        params: list[Any] = [match]
        if kinds:
            sql += "AND kind IN (%s) " % ",".join("?" * len(kinds))
            params += kinds
        sql += "ORDER BY bm25(mem_fts) LIMIT ?"
        params.append(POOL)
        try:
            rows = await (await self.db.execute(sql, tuple(params))).fetchall()
            return [(r["kind"], r["ref"]) for r in rows]
        except Exception as exc:  # noqa: BLE001 — malformed MATCH, etc.
            log.warning("fts search failed: %s", exc)
            return []

    async def _semantic(self, query: str, kinds: list[str] | None) -> list[tuple[str, str]]:
        vecs = await self._embed([query])
        if not vecs:
            return []
        qv = vecs[0]
        sql = "SELECT kind, ref, vec FROM mem_index WHERE vec IS NOT NULL "
        params: list[Any] = []
        if kinds:
            sql += "AND kind IN (%s) " % ",".join("?" * len(kinds))
            params += kinds
        rows = await (await self.db.execute(sql, tuple(params))).fetchall()
        scored = [((r["kind"], r["ref"]), cosine(qv, unpack_vec(r["vec"]))) for r in rows]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [k for k, _ in scored[:POOL]]

    async def all_items(self, kind: str) -> list[dict[str, Any]]:
        """All indexed items of a kind with decoded vectors (for consolidation)."""
        rows = await (await self.db.execute(
            "SELECT ref, text, vec FROM mem_index WHERE kind=? AND vec IS NOT NULL", (kind,))).fetchall()
        return [{"ref": r["ref"], "text": r["text"], "vec": unpack_vec(r["vec"])} for r in rows]

    def duplicate_groups(self, items: list[dict[str, Any]], threshold: float = 0.88) -> list[list[str]]:
        """Union near-duplicate items (cosine >= threshold) into groups of refs."""
        n = len(items)
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(n):
            for j in range(i + 1, n):
                if cosine(items[i]["vec"], items[j]["vec"]) >= threshold:
                    parent[find(i)] = find(j)
        groups: dict[int, list[str]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(items[i]["ref"])
        return [g for g in groups.values() if len(g) > 1]

    async def hybrid_search(self, query: str, k: int = 8, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        lex = await self._lexical(query, kinds)
        sem = await self._semantic(query, kinds)
        fused: dict[tuple[str, str], float] = {}
        for ranked in (lex, sem):
            for rank, key in enumerate(ranked):
                fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank)
        top = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:k]
        out: list[dict[str, Any]] = []
        for (kind, ref), score in top:
            row = await (await self.db.execute(
                "SELECT text, importance FROM mem_index WHERE kind=? AND ref=?", (kind, ref))).fetchone()
            if row:
                out.append({"kind": kind, "ref": ref, "text": row["text"],
                            "importance": row["importance"], "score": round(score, 5)})
        return out
