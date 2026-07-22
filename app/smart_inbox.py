from __future__ import annotations

import logging
import uuid
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatAction, ParseMode, ReactionEmoji

from app import bot as bot_module
from app.smart_inbox_protocol import parse_marker
from app.smart_inbox_service import (
    InboxConflictError,
    InboxNeedsReviewError,
    InboxNotFoundError,
    InboxPermissionError,
)

log = logging.getLogger("homebot.smart_inbox.telegram")
def _callback(action: str, proposal: dict[str, Any]) -> str:
    return f"inbox_{action}:{proposal['id']}:{proposal['version']}"


def _buttons(proposal: dict[str, Any]) -> InlineKeyboardMarkup | None:
    status = str(proposal["status"])
    rows: list[list[InlineKeyboardButton]] = []
    if status == "pending":
        rows.append(
            [
                InlineKeyboardButton("✅ אישור", callback_data=_callback("approve", proposal)),
                InlineKeyboardButton("✏️ עריכה", callback_data=_callback("edit", proposal)),
            ]
        )
        rows.append([InlineKeyboardButton("ביטול", callback_data=_callback("cancel", proposal))])
    elif status == "failed" and proposal.get("can_retry", True):
        rows.append([InlineKeyboardButton("🔁 ניסיון חוזר", callback_data=_callback("retry", proposal))])
        rows.append([InlineKeyboardButton("ביטול", callback_data=_callback("cancel", proposal))])
    return InlineKeyboardMarkup(rows) if rows else None


def render_proposal(proposal: dict[str, Any]) -> str:
    summary = str(proposal.get("summary") or "").strip()
    status = str(proposal.get("status") or "")
    if status == "completed":
        return summary.replace("📥 ממתין לאישור", "✅ בוצע").replace(
            "⚙️ מבצע פעולה", "✅ בוצע"
        ).replace("\n\nהפעולות טרם בוצעו.", "")
    if status == "cancelled":
        return "❌ ההצעה בוטלה. לא בוצעה פעולה.\n\n" + summary.replace(
            "\n\nהפעולות טרם בוצעו.", ""
        )
    if status == "expired":
        return "⌛ ההצעה פגה. שלח את הבקשה מחדש.\n\n" + summary.replace(
            "\n\nהפעולות טרם בוצעו.", ""
        )
    if status == "editing":
        return (
            "✏️ ההצעה המקורית הוקפאה ולא תבוצע.\n\n"
            "שלח עכשיו את הנוסח המתוקן כהודעה חדשה.\n\n"
            + summary.replace("\n\nהפעולות טרם בוצעו.", "")
        )
    if status == "failed":
        error = str(proposal.get("last_error") or "אחת הפעולות נכשלה")
        return (
            "⚠️ הפעולה לא הושלמה.\n"
            f"{error}\n\n"
            "אפשר לנסות שוב; צעדים שהושלמו לא יבוצעו פעם נוספת.\n\n"
            + summary.replace("\n\nהפעולות טרם בוצעו.", "")
        )
    if status == "needs_review":
        return (
            "🛑 נדרשת בדיקה ידנית.\n"
            "התהליך נקטע אחרי שייתכן שנשלחה פעולה למערכת חיצונית. "
            "מטעמי בטיחות הבוט לא ינסה שוב אוטומטית.\n\n"
            + summary.replace("\n\nהפעולות טרם בוצעו.", "")
        )
    return summary


async def on_text(update: Update, context) -> None:
    platform = bot_module._platform(context)
    message = update.effective_message
    envelope = platform.envelope(update)
    if not envelope or not message or not envelope.text or not platform.is_authorized(envelope):
        return

    handler = bot_module.BUTTON_ACTIONS.get(envelope.text.strip())
    if handler:
        await handler(update, context)
        return

    await platform.register_message(update, envelope)
    if not await platform.should_respond(envelope):
        return

    await message.reply_chat_action(ChatAction.TYPING)
    await bot_module._set_reaction_safely(message, ReactionEmoji.EYES)
    progress: Message | None = None
    if envelope.is_private:
        progress = await message.reply_text("מטפל בזה…")
    elif envelope.is_group:
        await platform.raw_api.send_ephemeral_text(
            chat_id=envelope.chat_id,
            receiver_user_id=envelope.user_id,
            message_thread_id=envelope.thread_id,
            text="מטפל בזה…",
        )

    try:
        result = await platform.answer(update)
    except Exception:
        incident = uuid.uuid4().hex[:8]
        bot_module.log.exception("Telegram agent failed incident=%s", incident)
        await bot_module._set_reaction_safely(
            message, ReactionEmoji.THUMBS_DOWN, is_big=True
        )
        error_text = (
            "לא הצלחתי להשלים את הפעולה. אפשר לנסות שוב.\n"
            f"קוד תקלה: <code>{incident}</code>"
        )
        if progress:
            await progress.edit_text(error_text, parse_mode=ParseMode.HTML)
        else:
            await message.reply_text(error_text, parse_mode=ParseMode.HTML)
        return

    if not result:
        if progress:
            await progress.delete()
        return

    answer, _agent_id = result
    marker = parse_marker(answer)
    if marker:
        try:
            proposal = await platform.inbox.get(str(marker["id"]), envelope.user_id)
        except (InboxNotFoundError, InboxPermissionError):
            proposal = marker
        text = render_proposal(proposal)
        markup = _buttons(proposal)
        if progress:
            await progress.edit_text(text, reply_markup=markup)
        else:
            await message.reply_text(text, reply_markup=markup)
        reaction = (
            ReactionEmoji.THUMBS_UP
            if proposal.get("status") == "completed"
            else ReactionEmoji.EYES
        )
        await bot_module._set_reaction_safely(message, reaction, is_big=True)
        return

    if len(answer) <= 4000:
        if progress:
            await progress.edit_text(answer)
        else:
            await message.reply_text(answer)
        await bot_module._set_reaction_safely(
            message, ReactionEmoji.THUMBS_UP, is_big=True
        )
        return

    if progress:
        await progress.delete()
    for offset in range(0, len(answer), 4000):
        await message.reply_text(answer[offset:offset + 4000])
    await bot_module._set_reaction_safely(
        message, ReactionEmoji.THUMBS_UP, is_big=True
    )


def _parse_callback(data: str) -> tuple[str, str, int | None] | None:
    parts = data.split(":")
    if len(parts) not in {2, 3} or not parts[0].startswith("inbox_"):
        return None
    try:
        version = int(parts[2]) if len(parts) == 3 else None
    except ValueError:
        return None
    return parts[0].removeprefix("inbox_"), parts[1], version


async def handle_callback(update: Update, context) -> bool:
    query = update.callback_query
    parsed = _parse_callback((query.data if query else "") or "")
    if not parsed:
        return False

    platform = bot_module._platform(context)
    envelope = platform.envelope(update)
    user = update.effective_user
    if not query or not envelope or not user or not platform.is_authorized(envelope):
        return True

    action, proposal_id, version = parsed
    await query.answer("מטפל…")
    try:
        if action == "approve":
            proposal = await platform.inbox.approve(
                proposal_id, user.id, expected_version=version
            )
        elif action == "retry":
            proposal = await platform.inbox.retry(
                proposal_id, user.id, expected_version=version
            )
        elif action == "cancel":
            proposal = await platform.inbox.cancel(
                proposal_id, user.id, expected_version=version
            )
        elif action == "edit":
            proposal = await platform.inbox.mark_editing(
                proposal_id, user.id, expected_version=version
            )
        else:
            await query.answer("פעולה לא מוכרת", show_alert=True)
            return True
    except InboxConflictError as exc:
        await query.answer(str(exc), show_alert=True)
        try:
            proposal = await platform.inbox.get(proposal_id, user.id)
            await query.edit_message_text(
                render_proposal(proposal), reply_markup=_buttons(proposal)
            )
        except Exception:
            pass
        return True
    except InboxNeedsReviewError as exc:
        proposal = await platform.inbox.get(proposal_id, user.id)
        await query.answer(str(exc), show_alert=True)
        await query.edit_message_text(render_proposal(proposal))
        return True
    except (InboxNotFoundError, InboxPermissionError):
        await query.answer("ההצעה לא נמצאה או שאינה זמינה עבורך", show_alert=True)
        return True
    except Exception:
        incident = uuid.uuid4().hex[:8]
        log.exception("smart inbox callback failed incident=%s", incident)
        await query.answer(f"הפעולה נכשלה. קוד תקלה: {incident}", show_alert=True)
        return True

    await query.edit_message_text(
        render_proposal(proposal), reply_markup=_buttons(proposal)
    )
    return True
