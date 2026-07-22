from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.config import Settings
from app.services import HomeService
from app.store_v2 import Store
from app.telegram_models import TelegramEnvelope
from app.tools import run_tool

log = logging.getLogger("homebot.smart_inbox.service")

Executor = Callable[[str, dict[str, Any], int], Awaitable[str]]

ACTIVE_STATUSES = {"pending", "failed", "executing", "needs_review", "editing"}
FINAL_STATUSES = {"completed", "cancelled", "expired"}
VALID_STATUSES = ACTIVE_STATUSES | FINAL_STATUSES

READ_ONLY_TOOLS = {
    "recall", "search_home", "project_list", "todo_list", "shop_list", "note_get",
    "event_list", "inventory_get", "people_list", "setting_get", "remind_list",
    "gdoc_read", "gdoc_list", "gsheet_read", "gsheet_list", "site_list",
    "web_search", "web_read", "web_map",
}
AUTO_APPROVE_TOOLS = {"shop_add", "todo_add", "note_save", "inventory_set", "remember"}
HIGH_RISK_TOOLS = {
    "event_delete", "forget", "inventory_delete", "core_memory_replace",
    "remind_cancel", "shop_clear_done", "site_publish",
}
EXTERNAL_SIDE_EFFECT_TOOLS = {
    "event_add", "event_update", "event_delete", "todo_schedule",
    "todo_create_doc", "todo_create_sheet", "gdoc_create", "gdoc_append",
    "gsheet_create", "gsheet_append_row", "site_publish", "remind_add",
}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


SCHEMA = """
CREATE TABLE IF NOT EXISTS action_inbox_proposals (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    chat_id INTEGER,
    thread_id INTEGER,
    source_message_id INTEGER,
    source_update_id INTEGER,
    created_by INTEGER NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    source_text TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    risk_level TEXT NOT NULL DEFAULT 'medium',
    approval_policy TEXT NOT NULL DEFAULT 'creator_or_admin',
    status TEXT NOT NULL DEFAULT 'pending',
    version INTEGER NOT NULL DEFAULT 1,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    executing_at REAL,
    completed_at REAL,
    cancelled_at REAL,
    UNIQUE(household_id, source_kind, source_key)
);
CREATE INDEX IF NOT EXISTS idx_action_inbox_actor_status
    ON action_inbox_proposals(household_id, created_by, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_inbox_status_updated
    ON action_inbox_proposals(household_id, status, updated_at);

CREATE TABLE IF NOT EXISTS action_inbox_steps (
    proposal_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    requires_approval INTEGER NOT NULL DEFAULT 1,
    external_side_effect INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    started_at REAL,
    completed_at REAL,
    PRIMARY KEY(proposal_id, position),
    FOREIGN KEY(proposal_id) REFERENCES action_inbox_proposals(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_action_inbox_steps_status
    ON action_inbox_steps(proposal_id, status, position);

CREATE TABLE IF NOT EXISTS action_inbox_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL,
    actor_id INTEGER,
    action TEXT NOT NULL,
    from_status TEXT NOT NULL DEFAULT '',
    to_status TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    FOREIGN KEY(proposal_id) REFERENCES action_inbox_proposals(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_action_inbox_audit_proposal
    ON action_inbox_audit(proposal_id, id);
"""


@dataclass(frozen=True, slots=True)
class ActionDecision:
    mutating: bool
    requires_approval: bool
    risk_level: str
    reason: str
    external_side_effect: bool


class InboxNotFoundError(LookupError):
    pass


class InboxPermissionError(PermissionError):
    pass


class InboxConflictError(RuntimeError):
    pass


class InboxNeedsReviewError(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _parse_json(value: Any, default: Any) -> Any:
    if value in {None, ""}:
        return default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _fingerprint(name: str, arguments: dict[str, Any]) -> str:
    payload = f"{name}\0{_json(arguments)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def classify_action(name: str, arguments: dict[str, Any]) -> ActionDecision:
    if name in READ_ONLY_TOOLS:
        return ActionDecision(False, False, "low", "read_only", False)

    external = name in EXTERNAL_SIDE_EFFECT_TOOLS
    if name in HIGH_RISK_TOOLS:
        return ActionDecision(True, True, "high", "destructive_or_public", external)

    if name == "shop_add":
        return ActionDecision(True, False, "low", "single_shopping_item", False)

    if name == "todo_add":
        complex_fields = any(
            [
                str(arguments.get("description") or "").strip(),
                arguments.get("project_id"),
                arguments.get("parent_task_id"),
                arguments.get("assigned_to"),
                arguments.get("due_at"),
                (arguments.get("priority") or "normal") != "normal",
                (arguments.get("status") or "todo") != "todo",
                arguments.get("recurrence_rule"),
                arguments.get("estimate_minutes"),
            ]
        )
        return ActionDecision(
            True,
            bool(complex_fields),
            "medium" if complex_fields else "low",
            "structured_task" if complex_fields else "title_only_task",
            False,
        )

    if name == "remind_add":
        complex_reminder = bool(
            arguments.get("target_name")
            or arguments.get("recurrence")
            or arguments.get("target") == "all"
        )
        return ActionDecision(
            True,
            complex_reminder,
            "medium" if complex_reminder else "low",
            "relayed_or_recurring_reminder" if complex_reminder else "personal_one_off_reminder",
            True,
        )

    if name in AUTO_APPROVE_TOOLS:
        return ActionDecision(True, False, "low", "explicit_low_risk_capture", external)

    # Fail safe: a new mutation is never silently auto-approved until policy is defined.
    return ActionDecision(True, True, "medium", "unclassified_mutation", external)


class SmartInboxService:
    def __init__(self, settings: Settings, store: Store, service: HomeService):
        self.settings = settings
        self.store = store
        self.service = service

    @property
    def admin_ids(self) -> set[int]:
        return set(self.settings.telegram_admin_user_ids or self.settings.allowed_user_ids)

    async def ensure_schema(self) -> None:
        await self.store.db.executescript(SCHEMA)
        await self._migrate_legacy()
        await self.recover_stale()
        await self.expire_due()
        await self.store.db.commit()

    async def _migrate_legacy(self) -> None:
        migrated = await self.store.get_setting("smart_inbox_v2_migrated", "")
        if migrated == "1":
            return
        table = await (
            await self.store.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='smart_inbox_actions'"
            )
        ).fetchone()
        if not table:
            await self.store.set_setting("smart_inbox_v2_migrated", "1")
            return

        rows = await (
            await self.store.db.execute("SELECT * FROM smart_inbox_actions ORDER BY created_at")
        ).fetchall()
        for legacy in rows:
            row = dict(legacy)
            proposal_id = str(row.get("id") or uuid.uuid4().hex)
            created_at = float(row.get("created_at") or time.time())
            status = str(row.get("status") or "pending")
            if status not in VALID_STATUSES:
                status = "needs_review"
            source_key = f"legacy:{proposal_id}"
            actions = _parse_json(row.get("actions_json"), [])
            decisions = [
                classify_action(str(item.get("name") or ""), item.get("arguments") or {})
                for item in actions
                if isinstance(item, dict)
            ]
            risk = max((item.risk_level for item in decisions), key=RISK_ORDER.get, default="medium")
            await self.store.db.execute(
                """INSERT OR IGNORE INTO action_inbox_proposals(
                    id, household_id, source_kind, source_key, chat_id, thread_id,
                    created_by, agent_id, source_text, summary, risk_level, status,
                    version, retry_count, last_error, created_at, updated_at, expires_at
                ) VALUES(?, ?, 'legacy', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, '', ?, ?, ?)""",
                (
                    proposal_id,
                    self.settings.household_id,
                    source_key,
                    row.get("chat_id"),
                    row.get("thread_id"),
                    int(row.get("user_id") or 0),
                    str(row.get("agent_id") or ""),
                    str(row.get("source_text") or ""),
                    str(row.get("summary") or ""),
                    risk,
                    status,
                    created_at,
                    float(row.get("updated_at") or created_at),
                    float(row.get("expires_at") or created_at + 86400),
                ),
            )
            for position, action in enumerate(actions if isinstance(actions, list) else []):
                if not isinstance(action, dict):
                    continue
                name = str(action.get("name") or "")
                arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
                decision = classify_action(name, arguments)
                await self.store.db.execute(
                    """INSERT OR IGNORE INTO action_inbox_steps(
                        proposal_id, position, tool_name, arguments_json, fingerprint,
                        risk_level, requires_approval, external_side_effect, status
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                    (
                        proposal_id,
                        position,
                        name,
                        _json(arguments),
                        _fingerprint(name, arguments),
                        decision.risk_level,
                        int(decision.requires_approval),
                        int(decision.external_side_effect),
                    ),
                )
            await self._audit(
                proposal_id,
                int(row.get("user_id") or 0),
                "legacy_migrated",
                "",
                status,
                {"source_table": "smart_inbox_actions"},
            )
        await self.store.set_setting("smart_inbox_v2_migrated", "1")

    async def recover_stale(self, stale_after_seconds: int = 600) -> int:
        now = time.time()
        rows = await (
            await self.store.db.execute(
                """SELECT id FROM action_inbox_proposals
                   WHERE household_id=? AND status='executing'
                     AND COALESCE(executing_at, updated_at)<?""",
                (self.settings.household_id, now - stale_after_seconds),
            )
        ).fetchall()
        count = 0
        for row in rows:
            proposal_id = str(row["id"])
            uncertain = await (
                await self.store.db.execute(
                    """SELECT COUNT(*) AS count FROM action_inbox_steps
                       WHERE proposal_id=? AND status='executing'""",
                    (proposal_id,),
                )
            ).fetchone()
            next_status = "needs_review" if int(uncertain["count"] or 0) else "failed"
            await self.store.db.execute(
                """UPDATE action_inbox_proposals SET status=?, last_error=?,
                   version=version+1, updated_at=? WHERE id=? AND status='executing'""",
                (
                    next_status,
                    "Execution interrupted; manual review required"
                    if next_status == "needs_review"
                    else "Execution interrupted before a step was claimed",
                    now,
                    proposal_id,
                ),
            )
            await self._audit(
                proposal_id,
                None,
                "stale_recovered",
                "executing",
                next_status,
                {"stale_after_seconds": stale_after_seconds},
            )
            count += 1
        await self.store.db.commit()
        return count

    async def expire_due(self) -> int:
        now = time.time()
        rows = await (
            await self.store.db.execute(
                """SELECT id, status FROM action_inbox_proposals
                   WHERE household_id=? AND status IN ('pending','failed','editing')
                     AND expires_at<=?""",
                (self.settings.household_id, now),
            )
        ).fetchall()
        for row in rows:
            await self.store.db.execute(
                """UPDATE action_inbox_proposals SET status='expired',
                   version=version+1, updated_at=? WHERE id=?""",
                (now, row["id"]),
            )
            await self._audit(
                str(row["id"]), None, "expired", str(row["status"]), "expired", {}
            )
        await self.store.db.commit()
        return len(rows)

    def decision(self, name: str, arguments: dict[str, Any]) -> ActionDecision:
        return classify_action(name, arguments)

    def is_mutation(self, name: str, arguments: dict[str, Any]) -> bool:
        return self.decision(name, arguments).mutating

    async def create_from_telegram(
        self,
        *,
        envelope: TelegramEnvelope,
        agent_id: str,
        source_text: str,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await self.create_proposal(
            source_kind="telegram",
            source_key=f"{envelope.chat_id}:{envelope.message_id}:{envelope.update_id}",
            created_by=envelope.user_id,
            source_text=source_text,
            agent_id=agent_id,
            actions=actions,
            chat_id=envelope.chat_id,
            thread_id=envelope.topic_id,
            source_message_id=envelope.message_id,
            source_update_id=envelope.update_id,
        )

    async def create_proposal(
        self,
        *,
        source_kind: str,
        source_key: str,
        created_by: int,
        source_text: str,
        agent_id: str,
        actions: list[dict[str, Any]],
        chat_id: int | None = None,
        thread_id: int | None = None,
        source_message_id: int | None = None,
        source_update_id: int | None = None,
        ttl_seconds: int = 86400,
    ) -> dict[str, Any]:
        if not actions:
            raise ValueError("A proposal requires at least one action")
        normalized: list[dict[str, Any]] = []
        decisions: list[ActionDecision] = []
        seen: set[str] = set()
        for item in actions:
            name = str(item.get("name") or "").strip()
            arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            if not name:
                continue
            fingerprint = _fingerprint(name, arguments)
            # Repeated calls from the model after receiving a synthetic planning result
            # are duplicates, not distinct user intent.
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            decision = classify_action(name, arguments)
            if not decision.mutating:
                continue
            normalized.append({"name": name, "arguments": arguments})
            decisions.append(decision)
        if not normalized:
            raise ValueError("No mutating actions were supplied")

        multi_step = len(normalized) > 1
        requires_approval = multi_step or any(item.requires_approval for item in decisions)
        risk_level = max(
            (item.risk_level for item in decisions), key=RISK_ORDER.get, default="medium"
        )
        summary = await self.render_summary(normalized, pending=requires_approval)
        proposal_id = uuid.uuid4().hex
        now = time.time()
        expires_at = now + max(300, min(ttl_seconds, 7 * 86400))

        try:
            await self.store.db.execute("BEGIN IMMEDIATE")
            await self.store.db.execute(
                """INSERT INTO action_inbox_proposals(
                    id, household_id, source_kind, source_key, chat_id, thread_id,
                    source_message_id, source_update_id, created_by, agent_id,
                    source_text, summary, risk_level, status, version, created_at,
                    updated_at, expires_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?, ?)""",
                (
                    proposal_id,
                    self.settings.household_id,
                    source_kind,
                    source_key,
                    chat_id,
                    thread_id,
                    source_message_id,
                    source_update_id,
                    created_by,
                    agent_id,
                    source_text[:4000],
                    summary[:4000],
                    risk_level,
                    now,
                    now,
                    expires_at,
                ),
            )
            for position, (action, decision) in enumerate(zip(normalized, decisions)):
                await self.store.db.execute(
                    """INSERT INTO action_inbox_steps(
                        proposal_id, position, tool_name, arguments_json, fingerprint,
                        risk_level, requires_approval, external_side_effect, status
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                    (
                        proposal_id,
                        position,
                        action["name"],
                        _json(action["arguments"]),
                        _fingerprint(action["name"], action["arguments"]),
                        decision.risk_level,
                        int(decision.requires_approval or multi_step),
                        int(decision.external_side_effect),
                    ),
                )
            await self._audit(
                proposal_id,
                created_by,
                "created",
                "",
                "pending",
                {
                    "source_kind": source_kind,
                    "risk_level": risk_level,
                    "requires_approval": requires_approval,
                    "step_count": len(normalized),
                },
            )
            await self.store.db.commit()
        except Exception:
            await self.store.db.rollback()
            existing = await (
                await self.store.db.execute(
                    """SELECT id FROM action_inbox_proposals
                       WHERE household_id=? AND source_kind=? AND source_key=?""",
                    (self.settings.household_id, source_kind, source_key),
                )
            ).fetchone()
            if not existing:
                raise
            proposal_id = str(existing["id"])

        proposal = await self.get(proposal_id, created_by, allow_admin=True)
        proposal["requires_approval"] = requires_approval
        return proposal

    async def execute_auto_if_allowed(self, proposal_id: str, actor_id: int) -> dict[str, Any]:
        proposal = await self.get(proposal_id, actor_id, allow_admin=True)
        steps = proposal["steps"]
        if len(steps) != 1 or any(bool(step["requires_approval"]) for step in steps):
            return proposal
        return await self.approve(proposal_id, actor_id, expected_version=proposal["version"], auto=True)

    async def list_for_actor(
        self,
        actor_id: int,
        *,
        status: str | None = None,
        limit: int = 30,
        before: float | None = None,
    ) -> list[dict[str, Any]]:
        await self.expire_due()
        clauses = ["household_id=?"]
        params: list[Any] = [self.settings.household_id]
        if actor_id not in self.admin_ids:
            clauses.append("created_by=?")
            params.append(actor_id)
        if status:
            requested = [part.strip() for part in status.split(",") if part.strip()]
            invalid = [item for item in requested if item not in VALID_STATUSES]
            if invalid:
                raise ValueError(f"Invalid status: {', '.join(invalid)}")
            placeholders = ",".join("?" for _ in requested)
            clauses.append(f"status IN ({placeholders})")
            params.extend(requested)
        if before is not None:
            clauses.append("created_at<?")
            params.append(float(before))
        params.append(max(1, min(int(limit), 100)))
        rows = await (
            await self.store.db.execute(
                f"""SELECT * FROM action_inbox_proposals
                    WHERE {' AND '.join(clauses)}
                    ORDER BY created_at DESC LIMIT ?""",
                tuple(params),
            )
        ).fetchall()
        return [await self._hydrate(dict(row), include_audit=False) for row in rows]

    async def counts(self, actor_id: int) -> dict[str, int]:
        clauses = ["household_id=?"]
        params: list[Any] = [self.settings.household_id]
        if actor_id not in self.admin_ids:
            clauses.append("created_by=?")
            params.append(actor_id)
        rows = await (
            await self.store.db.execute(
                f"""SELECT status, COUNT(*) AS count FROM action_inbox_proposals
                    WHERE {' AND '.join(clauses)} GROUP BY status""",
                tuple(params),
            )
        ).fetchall()
        result = {status: 0 for status in VALID_STATUSES}
        for row in rows:
            result[str(row["status"])] = int(row["count"] or 0)
        result["attention"] = (
            result["pending"] + result["failed"] + result["needs_review"]
        )
        return result

    async def get(
        self,
        proposal_id: str,
        actor_id: int,
        *,
        allow_admin: bool = True,
    ) -> dict[str, Any]:
        row = await (
            await self.store.db.execute(
                """SELECT * FROM action_inbox_proposals
                   WHERE id=? AND household_id=?""",
                (proposal_id, self.settings.household_id),
            )
        ).fetchone()
        if not row:
            raise InboxNotFoundError("Proposal not found")
        proposal = dict(row)
        if int(proposal["created_by"]) != actor_id and not (
            allow_admin and actor_id in self.admin_ids
        ):
            raise InboxPermissionError("Not allowed to access this proposal")
        return await self._hydrate(proposal, include_audit=True)

    async def _hydrate(
        self, proposal: dict[str, Any], *, include_audit: bool
    ) -> dict[str, Any]:
        steps = await (
            await self.store.db.execute(
                """SELECT * FROM action_inbox_steps
                   WHERE proposal_id=? ORDER BY position""",
                (proposal["id"],),
            )
        ).fetchall()
        proposal["steps"] = []
        for raw in steps:
            item = dict(raw)
            item["arguments"] = _parse_json(item.pop("arguments_json", ""), {})
            item["result"] = _parse_json(item.pop("result_json", ""), None)
            item["requires_approval"] = bool(item["requires_approval"])
            item["external_side_effect"] = bool(item["external_side_effect"])
            proposal["steps"].append(item)
        proposal["can_approve"] = proposal["status"] in {"pending", "failed"}
        proposal["can_cancel"] = proposal["status"] in {"pending", "failed", "editing"}
        proposal["can_retry"] = proposal["status"] == "failed" and not any(
            step["status"] in {"executing", "uncertain"} for step in proposal["steps"]
        )
        if include_audit:
            audit_rows = await (
                await self.store.db.execute(
                    """SELECT * FROM action_inbox_audit
                       WHERE proposal_id=? ORDER BY id""",
                    (proposal["id"],),
                )
            ).fetchall()
            proposal["audit"] = []
            for raw in audit_rows:
                audit = dict(raw)
                audit["metadata"] = _parse_json(audit.pop("metadata_json", "{}"), {})
                proposal["audit"].append(audit)
        return proposal

    async def mark_editing(
        self, proposal_id: str, actor_id: int, *, expected_version: int | None = None
    ) -> dict[str, Any]:
        return await self._transition(
            proposal_id,
            actor_id,
            allowed_from={"pending", "failed"},
            to_status="editing",
            action="editing_requested",
            expected_version=expected_version,
        )

    async def cancel(
        self, proposal_id: str, actor_id: int, *, expected_version: int | None = None
    ) -> dict[str, Any]:
        return await self._transition(
            proposal_id,
            actor_id,
            allowed_from={"pending", "failed", "editing"},
            to_status="cancelled",
            action="cancelled",
            expected_version=expected_version,
            timestamp_field="cancelled_at",
        )

    async def _transition(
        self,
        proposal_id: str,
        actor_id: int,
        *,
        allowed_from: set[str],
        to_status: str,
        action: str,
        expected_version: int | None,
        timestamp_field: str | None = None,
    ) -> dict[str, Any]:
        proposal = await self.get(proposal_id, actor_id)
        if proposal["status"] not in allowed_from:
            raise InboxConflictError(f"Proposal is already {proposal['status']}")
        if expected_version is not None and int(proposal["version"]) != expected_version:
            raise InboxConflictError("Proposal changed; refresh before acting")
        now = time.time()
        fields = ["status=?", "version=version+1", "updated_at=?"]
        params: list[Any] = [to_status, now]
        if timestamp_field:
            fields.append(f"{timestamp_field}=?")
            params.append(now)
        params.extend([proposal_id, proposal["status"], proposal["version"]])
        cur = await self.store.db.execute(
            f"""UPDATE action_inbox_proposals SET {', '.join(fields)}
                WHERE id=? AND status=? AND version=?""",
            tuple(params),
        )
        if cur.rowcount != 1:
            await self.store.db.rollback()
            raise InboxConflictError("Proposal changed; refresh before acting")
        await self._audit(
            proposal_id, actor_id, action, proposal["status"], to_status, {}
        )
        await self.store.db.commit()
        return await self.get(proposal_id, actor_id)

    async def approve(
        self,
        proposal_id: str,
        actor_id: int,
        *,
        expected_version: int | None = None,
        auto: bool = False,
        executor: Executor | None = None,
    ) -> dict[str, Any]:
        await self.expire_due()
        proposal = await self.get(proposal_id, actor_id)
        if proposal["status"] == "needs_review":
            raise InboxNeedsReviewError("Manual review is required before retry")
        if proposal["status"] not in {"pending", "failed"}:
            raise InboxConflictError(f"Proposal is already {proposal['status']}")
        if expected_version is not None and int(proposal["version"]) != expected_version:
            raise InboxConflictError("Proposal changed; refresh before acting")
        if float(proposal["expires_at"]) <= time.time():
            await self._transition(
                proposal_id,
                actor_id,
                allowed_from={proposal["status"]},
                to_status="expired",
                action="expired_on_approval",
                expected_version=proposal["version"],
            )
            raise InboxConflictError("Proposal expired")

        if proposal["status"] == "failed":
            uncertain = [
                step for step in proposal["steps"] if step["status"] in {"executing", "uncertain"}
            ]
            if uncertain:
                raise InboxNeedsReviewError("A previous side effect may have completed")

        now = time.time()
        cur = await self.store.db.execute(
            """UPDATE action_inbox_proposals
               SET status='executing', executing_at=?, updated_at=?,
                   version=version+1, retry_count=retry_count+?
               WHERE id=? AND status=? AND version=?""",
            (
                now,
                now,
                int(proposal["status"] == "failed"),
                proposal_id,
                proposal["status"],
                proposal["version"],
            ),
        )
        if cur.rowcount != 1:
            await self.store.db.rollback()
            raise InboxConflictError("Proposal was claimed by another request")
        await self._audit(
            proposal_id,
            actor_id,
            "auto_approved" if auto else "approved",
            proposal["status"],
            "executing",
            {"expected_version": expected_version},
        )
        await self.store.db.commit()

        async def default_executor(name: str, arguments: dict[str, Any], user_id: int) -> str:
            return await run_tool(
                self.store,
                self.service,
                name,
                arguments,
                user_id,
                settings=self.settings,
            )

        run = executor or default_executor
        try:
            steps = (await self.get(proposal_id, actor_id))["steps"]
            for step in steps:
                if step["status"] == "completed":
                    continue
                if step["status"] in {"executing", "uncertain"}:
                    await self._mark_needs_review(
                        proposal_id,
                        actor_id,
                        "A step was already executing; refusing unsafe retry",
                        step_position=int(step["position"]),
                    )
                    raise InboxNeedsReviewError("Manual review is required")

                claim = await self.store.db.execute(
                    """UPDATE action_inbox_steps
                       SET status='executing', started_at=?, last_error=''
                       WHERE proposal_id=? AND position=?
                         AND status IN ('pending','failed')""",
                    (time.time(), proposal_id, step["position"]),
                )
                await self.store.db.commit()
                if claim.rowcount != 1:
                    await self._mark_needs_review(
                        proposal_id,
                        actor_id,
                        "Step claim conflict",
                        step_position=int(step["position"]),
                    )
                    raise InboxNeedsReviewError("Step claim conflict")

                try:
                    raw_result = await run(
                        str(step["tool_name"]),
                        dict(step["arguments"]),
                        actor_id,
                    )
                    parsed = _parse_json(raw_result, {"ok": False, "error": "Invalid tool response"})
                    if not isinstance(parsed, dict):
                        parsed = {"ok": False, "error": "Invalid tool response"}
                except Exception as exc:
                    error = str(exc)[:1000]
                    if bool(step.get("external_side_effect")):
                        # The remote call may have succeeded before the process observed
                        # the exception. Never retry that step automatically.
                        await self.store.db.execute(
                            """UPDATE action_inbox_steps SET status='uncertain',
                               last_error=?, completed_at=? WHERE proposal_id=? AND position=?""",
                            (error, time.time(), proposal_id, step["position"]),
                        )
                        await self.store.db.commit()
                        await self._mark_needs_review(
                            proposal_id,
                            actor_id,
                            error,
                            step_position=int(step["position"]),
                        )
                        raise InboxNeedsReviewError("Execution outcome is uncertain") from exc

                    # Local/database mutations can be retried safely when the tool failed
                    # before returning success.
                    await self.store.db.execute(
                        """UPDATE action_inbox_steps SET status='failed',
                           last_error=?, completed_at=? WHERE proposal_id=? AND position=?""",
                        (error, time.time(), proposal_id, step["position"]),
                    )
                    await self.store.db.execute(
                        """UPDATE action_inbox_proposals SET status='failed',
                           last_error=?, version=version+1, updated_at=?
                           WHERE id=? AND status='executing'""",
                        (error, time.time(), proposal_id),
                    )
                    await self._audit(
                        proposal_id,
                        actor_id,
                        "step_exception",
                        "executing",
                        "failed",
                        {"position": step["position"], "tool_name": step["tool_name"], "error": error},
                    )
                    await self.store.db.commit()
                    return await self.get(proposal_id, actor_id)

                if bool(parsed.get("ok")):
                    await self.store.db.execute(
                        """UPDATE action_inbox_steps SET status='completed',
                           result_json=?, last_error='', completed_at=?
                           WHERE proposal_id=? AND position=?""",
                        (_json(parsed)[:20000], time.time(), proposal_id, step["position"]),
                    )
                    await self.store.db.commit()
                    await self._audit(
                        proposal_id,
                        actor_id,
                        "step_completed",
                        "executing",
                        "completed",
                        {"position": step["position"], "tool_name": step["tool_name"]},
                    )
                    await self.store.db.commit()
                    continue

                error = str(parsed.get("error") or "Tool returned ok=false")[:1000]
                await self.store.db.execute(
                    """UPDATE action_inbox_steps SET status='failed',
                       result_json=?, last_error=?, completed_at=?
                       WHERE proposal_id=? AND position=?""",
                    (_json(parsed)[:20000], error, time.time(), proposal_id, step["position"]),
                )
                await self.store.db.execute(
                    """UPDATE action_inbox_proposals SET status='failed',
                       last_error=?, version=version+1, updated_at=?
                       WHERE id=? AND status='executing'""",
                    (error, time.time(), proposal_id),
                )
                await self._audit(
                    proposal_id,
                    actor_id,
                    "step_failed",
                    "executing",
                    "failed",
                    {"position": step["position"], "tool_name": step["tool_name"], "error": error},
                )
                await self.store.db.commit()
                return await self.get(proposal_id, actor_id)

            now = time.time()
            await self.store.db.execute(
                """UPDATE action_inbox_proposals SET status='completed',
                   completed_at=?, last_error='', version=version+1, updated_at=?
                   WHERE id=? AND status='executing'""",
                (now, now, proposal_id),
            )
            await self._audit(
                proposal_id,
                actor_id,
                "completed",
                "executing",
                "completed",
                {"step_count": len(steps)},
            )
            await self.store.db.commit()
            return await self.get(proposal_id, actor_id)
        except InboxNeedsReviewError:
            raise
        except Exception as exc:
            log.exception("smart inbox execution failed proposal=%s", proposal_id)
            await self._mark_needs_review(proposal_id, actor_id, str(exc))
            raise

    async def retry(
        self,
        proposal_id: str,
        actor_id: int,
        *,
        expected_version: int | None = None,
        executor: Executor | None = None,
    ) -> dict[str, Any]:
        proposal = await self.get(proposal_id, actor_id)
        if proposal["status"] != "failed":
            raise InboxConflictError("Only failed proposals can be retried")
        if any(step["status"] in {"executing", "uncertain"} for step in proposal["steps"]):
            raise InboxNeedsReviewError("Manual review is required")
        return await self.approve(
            proposal_id,
            actor_id,
            expected_version=expected_version,
            executor=executor,
        )

    async def _mark_needs_review(
        self,
        proposal_id: str,
        actor_id: int | None,
        error: str,
        *,
        step_position: int | None = None,
    ) -> None:
        now = time.time()
        await self.store.db.execute(
            """UPDATE action_inbox_proposals SET status='needs_review',
               last_error=?, version=version+1, updated_at=?
               WHERE id=? AND status='executing'""",
            (error[:1000], now, proposal_id),
        )
        await self._audit(
            proposal_id,
            actor_id,
            "needs_review",
            "executing",
            "needs_review",
            {"error": error[:1000], "step_position": step_position},
        )
        await self.store.db.commit()

    async def _audit(
        self,
        proposal_id: str,
        actor_id: int | None,
        action: str,
        from_status: str,
        to_status: str,
        metadata: dict[str, Any],
    ) -> None:
        await self.store.db.execute(
            """INSERT INTO action_inbox_audit(
                proposal_id, actor_id, action, from_status, to_status,
                metadata_json, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (
                proposal_id,
                actor_id,
                action,
                from_status,
                to_status,
                _json(metadata),
                time.time(),
            ),
        )

    async def health(self) -> dict[str, Any]:
        counts = await (
            await self.store.db.execute(
                """SELECT status, COUNT(*) AS count,
                          MIN(created_at) AS oldest
                   FROM action_inbox_proposals
                   WHERE household_id=? GROUP BY status""",
                (self.settings.household_id,),
            )
        ).fetchall()
        by_status = {str(row["status"]): int(row["count"] or 0) for row in counts}
        oldest_pending = next(
            (float(row["oldest"]) for row in counts if row["status"] == "pending" and row["oldest"]),
            None,
        )
        return {
            "status": "ok" if not by_status.get("needs_review") else "attention",
            "counts": by_status,
            "oldest_pending_age_seconds": (
                max(0, int(time.time() - oldest_pending)) if oldest_pending else 0
            ),
        }

    async def render_summary(
        self, actions: list[dict[str, Any]], *, pending: bool
    ) -> str:
        lines: list[str] = []
        for action in actions:
            name = str(action.get("name") or "")
            args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
            if name == "shop_add":
                lines.append(f"🛒 {args.get('item') or '(פריט)'} × {args.get('qty') or '1'}")
            elif name == "todo_add":
                details = [f"✅ {args.get('title') or '(משימה)'}"]
                if args.get("due_at"):
                    details.append(f"מועד: {args['due_at']}")
                if args.get("assigned_to"):
                    details.append(f"אחראי: {args['assigned_to']}")
                lines.append(" · ".join(details))
            elif name == "event_add":
                lines.append(
                    f"📅 {args.get('title') or '(אירוע)'} · "
                    f"{args.get('start_at') or ''}–{args.get('end_at') or ''}".strip(" ·–")
                )
            elif name == "remind_add":
                target = args.get("target_name") or args.get("target") or "me"
                lines.append(
                    f"⏰ {args.get('text') or '(תזכורת)'} · {args.get('due_at') or ''} · {target}"
                )
            else:
                subject = (
                    args.get("title")
                    or args.get("name")
                    or args.get("text")
                    or args.get("item")
                    or args.get("id")
                    or ""
                )
                lines.append(f"• {name.replace('_', ' ')}{f': {subject}' if subject != '' else ''}")
        title = "📥 ממתין לאישור" if pending else "⚙️ מבצע פעולה"
        suffix = "\n\nהפעולות טרם בוצעו." if pending else ""
        return (title + "\n\n" + "\n".join(f"• {line.lstrip('• ')}" for line in lines) + suffix)[:4000]
