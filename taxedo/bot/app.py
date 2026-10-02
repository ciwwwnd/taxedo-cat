from __future__ import annotations

import functools
import asyncio
from pathlib import Path
import hashlib
import html
import json
import logging
import os
from datetime import datetime, time
from io import BytesIO

from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from taxedo.storage.receipts import ReceiptStore
from taxedo.agent.analyzer import ReceiptAnalyzer
from taxedo.storage.clarifications import ClarificationStore
from taxedo.tax.categories import (
    TAX_CATEGORIES,
    can_be_deductible,
    canonical_categories,
    english_label,
    get_tax_info_message,
)
from taxedo.agent.credits import format_credit_alert
from zoneinfo import ZoneInfo
from taxedo.config import Config
from taxedo.security import (
    authorized,
    check_upload,
    check_text,
    escape,
    receipt_path,
    UserInputError,
)
from taxedo.ingestion.documents import SUPPORTED_EXTENSIONS
from taxedo.agent.explanations import WhyAgent
from taxedo.bot.reporting import (
    currency_totals,
    grouped_totals,
    format_amount,
    format_totals,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

config = Config()
store = None
analyzer = None
clarifications = None


def initialize_services():
    global store, analyzer, clarifications
    store = ReceiptStore()
    store.init()
    analyzer = ReceiptAnalyzer()
    clarifications = ClarificationStore(
        config.DATA_DIR / "receipts.db",
        ttl_minutes=config.CLARIFICATION_TTL_MINUTES,
        max_questions=config.CLARIFICATION_MAX_QUESTIONS,
    )


_bot_instance = None


def is_authorized(user_id: int, chat_id: int) -> bool:
    return authorized(
        user_id, chat_id, config.ALLOWED_USER_IDS, config.GROUP_CHAT_ID
    )


def require_auth(func):
    @functools.wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user, chat, message = (
            update.effective_user,
            update.effective_chat,
            update.message,
        )
        if user is None or chat is None or message is None:
            return
        if not is_authorized(user.id, chat.id):
            await message.reply_text(
                "This bot is private. This user and chat must both be approved."
            )
            return
        try:
            check_text(message.text or message.caption or "")
            if func.__name__.startswith("handle_"):
                recorded = store.find_by_message(chat.id, message.message_id)
                if recorded:
                    await message.reply_text(
                        format_analysis_response(recorded, recorded["id"]),
                        parse_mode="HTML",
                    )
                    return
                pending = clarifications.get(chat.id, user.id)
                if pending and pending.payload.get("message_id") == message.message_id:
                    await message.reply_text(pending.current_question)
                    return
            return await func(update, ctx)
        except UserInputError as error:
            await message.reply_text(str(error))
        except (ValueError, asyncio.TimeoutError):
            await message.reply_text(
                "The request could not be processed within its safety limits. Check the file or try a shorter expense."
            )

    return wrapper


def require_callback_auth(func):
    @functools.wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user, chat = update.effective_user, update.effective_chat
        if not query or not user or not chat:
            return
        if not is_authorized(user.id, chat.id):
            await query.answer("Access required.", show_alert=True)
            return
        return await func(update, ctx)

    return wrapper


def hash_file(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def save_receipt_file(file_bytes: bytes, file_hash: str, ext: str) -> str:
    files_dir = config.DATA_DIR / "files"
    files_dir.mkdir(exist_ok=True)
    path = files_dir / f"{file_hash}{ext}"
    if not path.exists():
        path.write_bytes(file_bytes)
    return str(path.relative_to(config.DATA_DIR))


async def _send_credit_alert(alert: dict):
    chat_id = config.GROUP_CHAT_ID
    if not chat_id or not _bot_instance:
        raise RuntimeError("Credit notification destination unavailable")
    await _bot_instance.send_message(
        chat_id=chat_id, text=format_credit_alert(), parse_mode="HTML"
    )
    logger.info("Credit notification sent: %s", alert["type"])


HELP_TEXT = (
    "<b>Receipt Tracker</b>\n"
    "I track receipts and expenses for German tax deductions. Send a receipt "
    "photo, a document (PDF, Office, image, CSV or text) or a text message "
    "describing an expense.\n\n"
    "<b>Commands</b>\n"
    "/summary [year month] — monthly expense summary\n"
    "/stats — all-time totals and top categories\n"
    "/export [year] — export receipts as CSV\n"
    "/categories — tax deduction categories\n"
    "/why [id]; — explain a stored classification\n"
    "/review [id] — review queue or one receipt\n"
    "/edit [id]; [correction]; — update an expense\n"
    "/approve, /correct, /reject [id] — record a review decision\n"
    "/retry — retry a failed analysis with your saved answer\n"
    "/cancel [id] — stop a clarification, or cancel a receipt's review\n"
    "/receipt [id]; — retrieve the original file\n"
    "/forget [id]; — permanently delete a receipt and its file\n"
    "/pin — pin this reference in the chat\n"
    "/myid — your Telegram user ID\n\n"
    "Every Monday: weekly CSV report and deduction reminder. "
    )


@require_auth
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


@require_auth
async def cmd_categories(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_tax_info_message(), parse_mode="HTML")


@require_auth
async def cmd_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    month = now.month
    year = now.year

    if ctx.args:
        try:
            if len(ctx.args) >= 2:
                year = int(ctx.args[0])
                month = int(ctx.args[1])
            elif len(ctx.args) == 1:
                month = int(ctx.args[0])
        except ValueError:
            pass

    receipts = store.get_receipts_by_month(year, month)
    if not receipts:
        await update.message.reply_text(f"No receipts found for {year}-{month:02d}.")
        return

    total = currency_totals(receipts)
    by_category = grouped_totals(receipts, "tax_category", "Uncategorized")
    by_person = grouped_totals(receipts, "sender_name", "Unknown")

    lines = [f"<b>Summary for {year}-{month:02d}</b>\n"]
    lines.append(f"Total: <b>{escape(format_totals(total))}</b>")
    lines.append(f"Receipts: <b>{len(receipts)}</b>\n")

    lines.append("<b>By Category:</b>")
    for cat, totals in by_category.items():
        lines.append(f"  • {escape(cat)}: {escape(format_totals(totals))}")

    lines.append("\n<b>By Person:</b>")
    for person, totals in by_person.items():
        lines.append(f"  • {escape(person)}: {escape(format_totals(totals))}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@require_auth
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    stats = store.get_stats()
    if not stats["total_receipts"]:
        await update.message.reply_text("No receipts recorded yet.")
        return

    lines = [
        "<b>Overall Statistics</b>\n",
        f"Total receipts: <b>{stats['total_receipts']}</b>",
        f"Total spent: <b>{escape(format_totals(stats['totals_by_currency']))}</b>",
        f"First receipt: <b>{escape(stats['first_date'])}</b>",
        f"Last receipt: <b>{escape(stats['last_date'])}</b>",
    ]

    if stats["top_stores"]:
        lines.append("\n<b>Top Stores:</b>")
        for store_name, count in stats["top_stores"]:
            lines.append(f"  • {escape(store_name)}: {count} receipts")

    if stats["top_categories"]:
        lines.append("\n<b>Top Tax Categories:</b>")
        for cat, currency, amount in stats["top_categories"]:
            lines.append(
                f"  • {escape(cat)}: {escape(format_amount(amount, currency))}"
            )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@require_auth
async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    year = None
    if ctx.args:
        try:
            year = int(ctx.args[0])
        except ValueError:
            pass

    if year:
        receipts = store.get_receipts_by_year(year)
        label = str(year)
    else:
        receipts = store.get_all_receipts()
        label = "all"

    if not receipts:
        await update.message.reply_text("No receipts to export.")
        return

    csv_str = store.receipts_to_csv_string(receipts)
    buf = BytesIO(csv_str.encode("utf-8"))
    buf.name = f"receipts_{label}_{datetime.now():%Y%m%d}.csv"

    await update.message.reply_document(
        document=buf,
        caption=f"{len(receipts)} receipts exported as CSV ({label}).",
    )


@require_auth
async def cmd_pin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sent = await update.message.reply_text(HELP_TEXT, parse_mode="HTML")
    try:
        await ctx.bot.pin_chat_message(
            chat_id=update.effective_chat.id,
            message_id=sent.message_id,
            disable_notification=True,
        )
    except Exception:
        pass


@require_auth
async def cmd_receipt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /receipt <id>")
        return
    try:
        receipt_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("Usage: /receipt <id>")
        return

    receipt = store.get_receipt_by_id(receipt_id)
    if not receipt:
        await update.message.reply_text(f"Receipt #{receipt_id} not found.")
        return

    stored_path = receipt.get("file_path")
    file_path = receipt_path(config.DATA_DIR, stored_path) if stored_path else None
    if file_path is None or not file_path.is_file():
        await update.message.reply_text(
            f"Receipt #{receipt_id} has no file saved "
        )
        return

    caption = f"Receipt #{receipt_id}"
    if receipt.get("store_name"):
        caption += f" — {receipt['store_name']}"
    if receipt.get("receipt_date"):
        caption += f" ({receipt['receipt_date']})"

    with open(file_path, "rb") as f:
        await update.message.reply_document(document=f, caption=caption)


@require_auth
async def cmd_forget(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Usage: /forget <id>")
        return
    receipt_id = int(ctx.args[0])
    receipt = store.get_receipt_by_id(receipt_id)
    if not receipt:
        await update.message.reply_text(f"Receipt #{receipt_id} not found.")
        return
    stored_path = store.delete_receipt(receipt_id)
    if stored_path:
        try:
            receipt_path(config.DATA_DIR, stored_path).unlink(missing_ok=True)
        except ValueError:
            pass
    await update.message.reply_text(
        f"Receipt #{receipt_id} and its file have been permanently deleted."
    )


def can_confirm_deductible(receipt):
    return can_be_deductible(receipt.get("tax_category"), receipt.get("tax_subcategory"))


def review_keyboard(receipt, receipt_id):
    rows = []
    if receipt.get("needs_review") and can_confirm_deductible(receipt):
        rows.append([InlineKeyboardButton(
            "Confirm as deductible", callback_data=f"deductible:{receipt_id}"
        )])
    rows.append([InlineKeyboardButton(
        "Add context / edit details", callback_data=f"edit:{receipt_id}"
    )])
    if receipt.get("needs_review"):
        rows.append([InlineKeyboardButton(
            "Set category manually", callback_data=f"manual_review:{receipt_id}"
        )])
    return InlineKeyboardMarkup(rows)


def link_result(receipt_id, message):
    if isinstance(getattr(message, "message_id", None), int) and isinstance(getattr(message, "chat_id", None), int):
        store.link_message(receipt_id, message.chat_id, message.message_id)


@require_callback_auth
async def prompt_edit(update, ctx):
    query = update.callback_query
    user, chat = update.effective_user, update.effective_chat
    receipt_id = int(query.data.split(":", 1)[1])
    if not store.get_receipt_by_id(receipt_id):
        await query.answer("Receipt not found.", show_alert=True)
        return
    if clarifications.get(chat.id, user.id):
        await query.answer("Answer or /cancel your pending clarification first.", show_alert=True)
        return
    await query.answer()
    prompt = await query.message.reply_text(
        f"What should I change or consider for receipt #{receipt_id}? "
        f"Reply here, or use /edit {receipt_id} followed by your correction. "
        "The updated tax assessment will need confirmation.",
        reply_markup=ForceReply(selective=True),
    )
    link_result(receipt_id, prompt)


async def edit_from_text(msg, receipt, text):
    if not text.strip():
        await msg.reply_text("Add the correction or context after the receipt ID.")
        return
    context = receipt.get("raw_text") or receipt.get("caption") or json.dumps({
        key: receipt.get(key) for key in ("items", "total_amount", "currency", "receipt_date")
    }, ensure_ascii=False)
    context += "\n\nUser correction/additional context (use this to update earlier facts):\n" + text
    check_text(context)
    progress = await msg.reply_text(f"Updating receipt #{receipt['id']}...")
    analysis = await analyzer.analyze_text(context, allow_clarification=False)
    if analysis.get("analysis_error"):
        await progress.edit_text(analysis.get("deduction_notes", "Analysis failed") + "\nYour saved receipt has not changed.")
        return
    try:
        updated = store.edit_analysis(receipt["id"], msg.from_user.id, analysis, context, receipt)
    except ValueError as error:
        await progress.edit_text(str(error))
        return
    store.link_message(updated["id"], msg.chat_id, msg.message_id)
    await progress.edit_text(format_analysis_response(updated, updated["id"]),
                             parse_mode="HTML", reply_markup=review_keyboard(updated, updated["id"]))
    link_result(updated["id"], progress)


@require_auth
async def cmd_edit(update, ctx):
    msg = update.message
    if clarifications.get(msg.chat_id, msg.from_user.id):
        await msg.reply_text("Answer or /cancel your pending clarification first.")
        return
    parts = (msg.text or "").split(None, 2)
    if len(parts) != 3 or not parts[1].isdigit():
        await msg.reply_text("Usage: /edit <id> <correction or additional context>")
        return
    receipt = store.get_receipt_by_id(int(parts[1]))
    if not receipt:
        await msg.reply_text("Receipt not found.")
        return
    await edit_from_text(msg, receipt, parts[2])


@require_callback_auth
async def manual_review(update, ctx):
    query = update.callback_query
    try:
        receipt_id = int(query.data.split(":", 1)[1])
        receipt = store.get_receipt_by_id(receipt_id)
        if not receipt:
            raise ValueError("Receipt not found.")
        if not receipt.get("needs_review"):
            await query.answer("This receipt has already been reviewed.")
            return
    except (ValueError, IndexError) as error:
        await query.answer(str(error), show_alert=True)
        return
    await query.answer()
    lines = [
        f"<b>Manually review receipt #{receipt_id}</b>",
        "This receipt is already saved. To classify it, copy the command below, "
        "replace the placeholders, and send it:",
        f"<code>/correct {receipt_id} CATEGORY | SUBCATEGORY | yes/no | reason</code>",
        "Choose yes only if you have verified deductibility; otherwise choose no. "
        "Include your reason for the decision.",
        "\n<b>Available categories and subcategories:</b>",
    ]
    for category, details in TAX_CATEGORIES.items():
        lines.append(f"<b>{escape(english_label(category))}</b>: " + "; ".join(
            escape(english_label(name)) for name in details["subcategories"]
        ))
    lines.append(
        f"\nTo mark it non-deductible without choosing a category, send "
        f"<code>/reject {receipt_id} | reason</code> with your reason."
    )
    await query.message.reply_text("\n\n".join(lines), parse_mode="HTML")


@require_callback_auth
async def confirm_deductible(update, ctx):
    query, user = update.callback_query, update.effective_user
    try:
        receipt_id = int(query.data.split(":", 1)[1])
        receipt = store.get_receipt_by_id(receipt_id)
        if not receipt:
            raise ValueError("Receipt not found.")
        if not receipt.get("needs_review"):
            await query.answer("This receipt has already been reviewed.")
            return
        if not can_confirm_deductible(receipt):
            raise ValueError("Set a valid category with /correct first.")
        reviewed = store.review_receipt(
            receipt_id,
            user.id,
            "approved",
            "Confirmed the description, category and deductibility using the confirmation button.",
            deductible=True,
        )
    except (ValueError, IndexError) as error:
        await query.answer(str(error), show_alert=True)
        return
    await query.answer("Confirmed as deductible.")
    await query.edit_message_text(
        format_analysis_response(reviewed, receipt_id),
        parse_mode="HTML",
        reply_markup=review_keyboard(reviewed, receipt_id),
    )
    link_result(receipt_id, getattr(query, "message", None))


@require_auth
async def cmd_review(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.args:
        try:
            receipt_id = int(ctx.args[0])
        except ValueError:
            await update.message.reply_text("Usage: /review [id]")
            return
        receipt = store.get_receipt_by_id(receipt_id)
        if not receipt:
            await update.message.reply_text("Receipt not found.")
            return
        response = await update.message.reply_text(
            format_analysis_response(receipt, receipt_id),
            parse_mode="HTML",
            reply_markup=review_keyboard(receipt, receipt_id),
        )
        link_result(receipt_id, response)
        return
    pending = store.pending_reviews()
    await update.message.reply_text(
        "Pending reviews: "
        + (", ".join(f"#{r['id']}" for r in pending) if pending else "none")
        + "\nUse /review <id>, then /approve, /correct, or /reject."
    )


@require_auth
async def cmd_review_decision(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    command = update.message.text.split(None, 1)[0].split("@")[0].lower()
    body = update.message.text.partition(" ")[2].strip()
    parts = [part.strip() for part in body.split("|")]
    try:
        if command == "/correct":
            if len(parts) != 4:
                raise ValueError(
                    "Usage: /correct <id> <category> | <subcategory> | yes/no | reason"
                )
            first = parts[0].split(None, 1)
            if len(first) != 2:
                raise ValueError(
                    "Usage: /correct <id> <category> | <subcategory> | yes/no | reason"
                )
            receipt_id, category = int(first[0]), first[1]
            subcategory, answer, reason = parts[1:]
            category, subcategory = canonical_categories(category, subcategory)
            action = "corrected"
        elif command == "/approve":
            if len(parts) != 2:
                raise ValueError("Usage: /approve <id> yes/no | reason")
            first = parts[0].split()
            if len(first) != 2:
                raise ValueError("Usage: /approve <id> yes/no | reason")
            receipt_id, answer = int(first[0]), first[1]
            reason, category, subcategory, action = parts[1], None, None, "approved"
        else:
            if len(parts) != 2:
                raise ValueError("Usage: /reject <id> | reason")
            receipt_id, reason = int(parts[0]), parts[1]
            answer, category, subcategory, action = "no", None, None, "rejected"
        if answer.lower() not in {"yes", "no"}:
            raise ValueError("Deductibility must be yes or no")
        reviewed = store.review_receipt(
            receipt_id,
            update.effective_user.id,
            action,
            reason,
            deductible=answer.lower() == "yes",
            category=category,
            subcategory=subcategory,
        )
    except ValueError as error:
        await update.message.reply_text(str(error))
        return
    response = await update.message.reply_text(
        format_analysis_response(reviewed, receipt_id),
        parse_mode="HTML", reply_markup=review_keyboard(reviewed, receipt_id),
    )
    link_result(receipt_id, response)


@require_auth
async def cmd_why(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not ctx.args and update.message.reply_to_message:
            receipt = store.find_by_message(
                update.effective_chat.id, update.message.reply_to_message.message_id
            )
            if not receipt:
                raise ValueError(
                    "That message has no saved receipt. Send the expense again if its analysis failed."
                )
            receipt_id = receipt["id"]
        elif len(ctx.args) == 1:
            receipt_id = int(ctx.args[0])
        else:
            raise ValueError(
                "Use /why <receipt_id>, or reply to the original expense with /why."
            )
        if receipt_id < 1:
            raise ValueError("Receipt ID must be positive")
        explanation = await WhyAgent(store, analyzer).explain(receipt_id)
    except (ValueError, RuntimeError) as error:
        await update.message.reply_text(str(error))
        return
    await update.message.reply_text(explanation)


async def cmd_myid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"<b>Your Telegram info:</b>\n\n"
        f"User ID: <code>{user.id}</code>\n"
        f"Name: {escape(user.first_name or '')} {escape(user.last_name or '')}\n"
        f"Username: @{escape(user.username or 'none')}\n\n"
        f"<i>Send this ID to the bot owner to get access.</i>",
        parse_mode="HTML",
    )


@require_auth
async def cmd_retry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    pending = clarifications.get(msg.chat_id, msg.from_user.id)
    if pending is None:
        await msg.reply_text(
            "No pending expense. Please send the receipt or description again."
        )
        return
    if not pending.answers:
        progress = await msg.reply_text("Re-reading your saved expense...")
        payload = dict(pending.payload)
        stored_path = payload.get("file_path")
        if stored_path:
            path = receipt_path(config.DATA_DIR, stored_path)
            if not path.is_file():
                await progress.edit_text("The saved attachment is unavailable. Use /cancel, then send it again.")
                return
            analysis = await analyzer.analyze_document(
                path.read_bytes(), path.name, payload.get("caption", "")
            )
        else:
            analysis = await analyzer.analyze_text(pending.original_text)
        payload["pending_key"] = (msg.chat_id, msg.from_user.id)
        payload["answer_message_ids"] = [*payload.get("answer_message_ids", []), msg.message_id]
        payload["raw_text"] = analysis.get("_source_text", pending.original_text)
        await _request_clarification_or_save(msg, progress, analysis, payload)
        return
    progress = await msg.reply_text("Retrying with your saved receipt and answer...")
    await _resume_clarification(msg, progress, retry=True)


@require_auth
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if ctx.args:
        try:
            if len(ctx.args) != 1 or not ctx.args[0].isdigit() or int(ctx.args[0]) < 1:
                raise ValueError(
                    "Usage: /cancel <receipt_id>, or /cancel to stop a clarification."
                )
            receipt_id = int(ctx.args[0])
            receipt = store.get_receipt_by_id(receipt_id)
            if receipt and receipt.get("review_status") == "cancelled":
                await update.message.reply_text(
                    f"Receipt #{receipt_id}: review already cancelled."
                )
                return
            store.review_receipt(
                receipt_id,
                user.id,
                "cancelled",
                "Pending review cancelled.",
                deductible=False,
            )
        except ValueError as error:
            await update.message.reply_text(str(error))
            return
        await update.message.reply_text(
            f"Receipt #{receipt_id}: review cancelled and removed from the review queue. "
            "The receipt remains in your expense records and is excluded from deductible totals."
        )
        return
    if clarifications.clear(chat.id, user.id):
        await update.message.reply_text(
            "Pending expense cancelled. Send a new receipt or description anytime."
        )
    else:
        await update.message.reply_text(
            "There is no unanswered clarification. To cancel a saved receipt’s pending review, use /cancel <receipt_id>."
        )


def _save_final_receipt(sender, analysis: dict, payload: dict) -> int:
    if analysis.get("analysis_error") or analysis.get("needs_clarification"):
        raise ValueError("Cannot save failed or pending analysis")
    message_keys = [(payload["chat_id"], payload["message_id"])]
    message_keys.extend(
        (payload["chat_id"], item) for item in payload.get("answer_message_ids", [])
    )
    return store.insert_receipt(
        message_keys=message_keys,
        pending_key=payload.get("pending_key"),
        needs_review=analysis.get("needs_review", False),
        rag_sources=analysis.get("rag_sources", []),
        evidence=analysis.get("evidence", []),
        analysis_model=analysis.get("analysis_model"),
        clarification_policy=analysis.get("clarification_policy"),
        knowledge_version=analysis.get("knowledge_version"),
        source_build_date=analysis.get("source_build_date"),
        tax_year=analysis.get("tax_year"),
        sender_id=sender.id,
        sender_name=sender.first_name or sender.username or "Unknown",
        message_type=payload["message_type"],
        file_path=payload.get("file_path"),
        file_hash=payload.get("file_hash"),
        caption=payload.get("caption", ""),
        raw_text=payload.get("raw_text", analysis.get("extracted_text", "")),
        store_name=analysis.get("store_name", ""),
        items=analysis.get("items", []),
        total_amount=analysis.get("total_amount"),
        currency=analysis.get("currency", "EUR"),
        tax_category=analysis.get("tax_category", ""),
        tax_subcategory=analysis.get("tax_subcategory", ""),
        tax_deductible=analysis.get("tax_deductible", False),
        deduction_notes=analysis.get("deduction_notes", ""),
        receipt_date=analysis.get("receipt_date"),
    )


def format_clarification_response(question: str) -> str:
    return (
        "<b>I need one detail before recording this expense.</b>\n\n"
        f"{html.escape(question)}\n\n"
        "<i>Reply with your answer, or use /cancel.</i>"
    )


async def _request_clarification_or_save(
    msg: Message,
    processing_msg: Message,
    analysis: dict,
    payload: dict,
) -> None:
    sender = msg.from_user
    payload.setdefault("chat_id", msg.chat_id)
    payload.setdefault("message_id", msg.message_id)
    if analysis.get("analysis_error"):
        instruction = "Your expense is kept. Use /retry to try again, or /cancel to discard it."
        payload["retry_only"] = True
        clarifications.start(
            msg.chat_id, sender.id,
            str(analysis.get("_source_text") or payload.get("raw_text") or ""),
            instruction, payload,
        )
        await processing_msg.edit_text(analysis["deduction_notes"] + "\n\n" + instruction)
        return
    payload.pop("retry_only", None)
    if config.AGENTIC_CLARIFICATION and analysis.get("needs_clarification"):
        question = str(analysis.get("clarification_question") or "").strip()
        if question:
            source_text = str(
                analysis.get("_source_text") or payload.get("raw_text") or ""
            )
            state = clarifications.start(
                msg.chat_id, sender.id, source_text, question, payload
            )
            await processing_msg.edit_text(
                format_clarification_response(state.current_question),
                parse_mode="HTML",
            )
            return

    receipt_id = _save_final_receipt(sender, analysis, payload)
    response = format_analysis_response(analysis, receipt_id)
    await processing_msg.edit_text(
        response,
        parse_mode="HTML",
        reply_markup=review_keyboard(analysis, receipt_id),
    )
    link_result(receipt_id, processing_msg)


async def _resume_clarification(
    msg: Message, processing_msg: Message, *, retry: bool = False
) -> None:
    pending = clarifications.get(msg.chat_id, msg.from_user.id)
    if pending and pending.is_previous_answer(msg.message_id):
        await processing_msg.edit_text(
            format_clarification_response(pending.current_question),
            parse_mode="HTML",
        )
        return
    updated = (
        pending
        if retry
        else clarifications.record_answer(
            msg.chat_id, msg.from_user.id, msg.text or "", msg.message_id
        )
    )
    if updated is None:
        await processing_msg.edit_text(
            "That clarification expired. Please send the complete expense again."
        )
        return

    can_ask_again = updated.question_count < clarifications.max_questions
    analysis = await analyzer.analyze_text(
        updated.original_text,
        answers=updated.answers,
        allow_clarification=can_ask_again,
    )
    if analysis.get("analysis_error"):
        await processing_msg.edit_text(
            analysis["deduction_notes"]
            + "\n\nYour receipt and answer are kept. Use /retry to try again, "
            "or /cancel to start a different expense."
        )
        return
    if can_ask_again and analysis.get("needs_clarification"):
        question = str(analysis.get("clarification_question") or "").strip()
        if question:
            next_state = clarifications.set_next_question(
                msg.chat_id, msg.from_user.id, question
            )
            await processing_msg.edit_text(
                format_clarification_response(next_state.current_question),
                parse_mode="HTML",
            )
            return

    payload = dict(updated.payload)
    payload["raw_text"] = updated.combined_text()
    payload["pending_key"] = (msg.chat_id, msg.from_user.id)
    payload["answer_message_ids"] = payload.get("answer_message_ids", []) + [
        item["message_id"]
        for item in updated.answers
        if item.get("message_id") is not None
    ]
    payload.setdefault("chat_id", msg.chat_id)
    payload.setdefault("message_id", msg.message_id)
    receipt_id = _save_final_receipt(msg.from_user, analysis, payload)
    response = format_analysis_response(analysis, receipt_id)
    await processing_msg.edit_text(
        response,
        parse_mode="HTML",
        reply_markup=review_keyboard(analysis, receipt_id),
    )
    link_result(receipt_id, processing_msg)


async def _reject_new_attachment_during_clarification(msg: Message) -> bool:
    if clarifications.get(msg.chat_id, msg.from_user.id) is None:
        return False
    await msg.reply_text(
        "Please answer my pending clarification first, or use /cancel before "
        "sending another receipt."
    )
    return True


async def download_attachment(attachment, bot) -> bytes:
    if attachment.file_size is not None:
        check_upload(attachment.file_size)
    try:
        file = await asyncio.wait_for(bot.get_file(attachment.file_id), 60)
    except BadRequest as error:
        if "file is too big" in str(error).lower():
            raise UserInputError(
                "Cannot download this file (20 MB limit). "
            ) from error
        raise
    if file.file_size is not None:
        check_upload(file.file_size)
    data = await asyncio.wait_for(file.download_as_bytearray(read_timeout=120), 180)
    check_upload(len(data))
    return bytes(data)


@require_auth
async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg: Message = update.message
    caption = msg.caption or ""

    if await _reject_new_attachment_during_clarification(msg):
        return

    photo = msg.photo[-1]
    file_bytes = await download_attachment(photo, ctx.bot)
    file_hash = hash_file(file_bytes)
    existing = store.find_by_hash(file_hash)
    if existing:
        amount_line = (
            f"\nAmount: {escape(format_amount(existing['total_amount'], existing.get('currency') or 'EUR'))}"
            if existing.get("total_amount") is not None
            else ""
        )
        await msg.reply_text(
            f"<b>Duplicate detected!</b>\n\n"
            f"This receipt was already recorded as #{existing['id']} "
            f"on {existing['created_at'][:10]}.\n"
            f"Store: {escape(existing.get('store_name') or '—')}"
            f"{amount_line}\n\n",
            parse_mode="HTML",
        )
        return

    processing_msg = await msg.reply_text("Analyzing receipt...")
    analysis = await analyzer.analyze_image(file_bytes, caption)
    file_path = save_receipt_file(file_bytes, file_hash, ".jpg")
    await _request_clarification_or_save(
        msg,
        processing_msg,
        analysis,
        {
            "message_type": "photo",
            "file_path": file_path,
            "file_hash": file_hash,
            "caption": caption,
            "raw_text": analysis.get(
                "_source_text", analysis.get("extracted_text", "")
            ),
        },
    )


@require_auth
async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg: Message = update.message
    doc = msg.document
    caption = msg.caption or ""

    if await _reject_new_attachment_during_clarification(msg):
        return

    ext = Path(doc.file_name or "").suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UserInputError(
            f"Unsupported document format: {ext or 'no file extension'}. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )
    file_bytes = await download_attachment(doc, ctx.bot)

    file_hash = hash_file(file_bytes)
    existing = store.find_by_hash(file_hash)
    if existing:
        await msg.reply_text(
            f"<b>Duplicate detected!</b>\n\n"
            f"This document was already recorded as #{existing['id']} "
            f"on {existing['created_at'][:10]}.\n",
            parse_mode="HTML",
        )
        return

    processing_msg = await msg.reply_text("Analyzing document...")

    analysis = await analyzer.analyze_document(
        file_bytes, doc.file_name or "receipt.pdf", caption
    )
    file_path = save_receipt_file(file_bytes, file_hash, ext)

    await _request_clarification_or_save(
        msg,
        processing_msg,
        analysis,
        {
            "message_type": "document",
            "file_path": file_path,
            "file_hash": file_hash,
            "caption": caption,
            "raw_text": analysis.get(
                "_source_text", analysis.get("extracted_text", "")
            ),
        },
    )


@require_auth
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg: Message = update.message
    sender = msg.from_user
    text = msg.text or ""

    pending = clarifications.get(msg.chat_id, sender.id)
    if pending is not None:
        if pending.payload.get("retry_only"):
            await msg.reply_text(pending.current_question)
            return
        processing_msg = await msg.reply_text("Reviewing your answer...")
        await _resume_clarification(msg, processing_msg)
        return

    reply = getattr(msg, "reply_to_message", None)
    if reply:
        receipt = store.find_by_message(msg.chat_id, reply.message_id)
        if receipt:
            await edit_from_text(msg, receipt, text)
            return

    processing_msg = await msg.reply_text("Analyzing expense...")
    analysis = await analyzer.analyze_text(text)
    await _request_clarification_or_save(
        msg,
        processing_msg,
        analysis,
        {
            "message_type": "text",
            "file_path": None,
            "file_hash": None,
            "caption": text,
            "raw_text": text,
        },
    )


def deduction_explanation(analysis: dict) -> str:
    notes = str(analysis.get("deduction_notes") or "").strip()
    review = str(analysis.get("review_reason") or "").strip()
    if analysis.get("review_status") and not analysis.get("needs_review"):
        return "\n".join(part for part in (notes, f"Your review: {review}" if review else "") if part) or "Reviewed; no explanation recorded."
    return notes or "The purchase is saved, but its tax treatment has not been established."


def format_analysis_response(analysis: dict, receipt_id: int) -> str:
    lines = [f"<b>Expense #{receipt_id} · saved</b>\n"]

    if analysis.get("store_name"):
        lines.append(f"<b>Store:</b> {escape(analysis['store_name'][:100])}")
    currency = analysis.get("currency") or "EUR"
    if analysis.get("total_amount") is not None:
        lines.append(
            f"<b>Total:</b> {escape(format_amount(analysis['total_amount'], currency))}"
        )
    if analysis.get("receipt_date"):
        lines.append(f"<b>Date:</b> {escape(analysis['receipt_date'])}")


    items = analysis.get("items", [])
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (ValueError, TypeError):
            items = []
    if isinstance(items, list) and items:
        lines.append("\n<b>Items:</b>")
        for item in items[:5]:
            name = escape(item.get("name", "?")[:80])
            price = item.get("price")
            lines.append(
                f"  • {name}"
                + (
                    f" — {escape(format_amount(price, currency))}"
                    if isinstance(price, (int, float))
                    else ""
                )
            )
        if len(items) > 5:
            lines.append(f"  ... and {len(items) - 5} more")

    lines.append("")
    category = english_label(analysis.get("tax_category") or "Unklassifiziert")
    subcategory = english_label(analysis.get("tax_subcategory") or "")
    lines.append(f"<b>Category:</b> {escape(category)}" + (f" / {escape(subcategory)}" if subcategory else ""))
    if analysis.get("needs_review"):
        status = "Pending review · not included in deductible totals"
    elif analysis.get("review_status"):
        status = "Confirmed deductible" if analysis.get("tax_deductible") else "Reviewed · not deductible"
    else:
        status = "Potentially deductible" if analysis.get("tax_deductible") else "Not deductible based on current information"
    lines.append(f"<b>Tax status:</b> {status}")
    lines.append(f"\n<b>Reasoning:</b> {escape(deduction_explanation(analysis)[:1500])}")

    sources = analysis.get("rag_sources", [])
    unique_sources = {}
    for source in sources:
        unique_sources.setdefault(source.get("id"), source)
    if unique_sources:
        links = []
        for source in list(unique_sources.values())[:3]:
            title = source.get("title", "")[:100]
            kind = (
                "Official law"
                if source.get("document_type") == "official_law"
                else "Curated reference"
            )
            label = html.escape(
                f"{kind}: {source.get('section', '')} EStG — {title}"
                if title
                else f"{source.get('section', '')} EStG"
            )
            raw_url = source.get("url", "")
            if not raw_url.startswith("https://www.gesetze-im-internet.de/"):
                links.append(label)
                continue
            url = html.escape(raw_url, quote=True)
            links.append(f'<a href="{url}">{label}</a>')
        lines.append(f"<b>Cited references:</b> {', '.join(links)}")

    lines.append(f"\nReply to this message with a correction, or use /edit {receipt_id} followed by your changes.")
    return "\n".join(lines)


async def march_tax_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = config.GROUP_CHAT_ID
    if not chat_id:
        return

    year = datetime.now().year
    stats = store.get_stats_for_year(year - 1)

    lines = [
        "<b>TAX SEASON REMINDER!</b>\n",
        f"It's March {year} — time to prepare your {year - 1} tax declarations!\n",
    ]

    if stats["total_receipts"]:
        lines.append("<b>Last year's summary:</b>")
        lines.append(f"  • Total receipts: {stats['total_receipts']}")
        lines.append(
            f"  • Total tracked: {escape(format_totals(stats['totals_by_currency']))}"
        )
        if stats["deductible_by_currency"]:
            lines.append(
                f"  • Potentially deductible: {escape(format_totals(stats['deductible_by_currency']))}"
            )

    lines.extend(
        [
            "\n<b> Action items:</b>",
            "  1. Export your receipts with /export",
            "  2. Review each category for deductions",
            "  3. Gather Lohnsteuerbescheinigung from employer",
            "  4. File via ELSTER or your Steuerberater",
            "  5. Deadline: July 31st (or Feb 28 next year with Steuerberater)",
            "\nGood luck!",
        ]
    )

    await ctx.bot.send_message(
        chat_id=chat_id, text="\n".join(lines), parse_mode="HTML"
    )


async def weekly_csv_report(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = config.GROUP_CHAT_ID
    if not chat_id:
        return

    week_receipts = store.get_receipts_last_n_days(7)
    now = datetime.now()
    year = now.year
    week_num = now.isocalendar()[1]

    if not week_receipts:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text="<b>Weekly report</b>\n\nNo receipts this week. Nothing to export.",
            parse_mode="HTML",
        )
        return

    total = currency_totals(week_receipts)
    deductible = currency_totals(r for r in week_receipts if r.get("tax_deductible"))

    csv_str = store.receipts_to_csv_string(week_receipts)
    buf = BytesIO(csv_str.encode("utf-8"))
    buf.name = f"week{week_num}_{year}_receipts.csv"

    await ctx.bot.send_document(
        chat_id=chat_id,
        document=buf,
        caption=(
            f"<b>Weekly report — Week {week_num}, {year}</b>\n\n"
            f"Receipts: {len(week_receipts)}\n"
            f"Total: {escape(format_totals(total))}\n"
            f"Deductible: {escape(format_totals(deductible))}"
        ),
        parse_mode="HTML",
    )


REMINDER_CATEGORIES = (
    "Werbungskosten",
    "Außergewöhnliche Belastungen",
    "Sonderausgaben",
    "Haushaltsnahe Dienstleistungen",
)


async def weekly_deduction_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = config.GROUP_CHAT_ID
    if not chat_id:
        return

    week_receipts = store.get_receipts_last_n_days(7)
    deductible_count = sum(1 for r in week_receipts if r.get("tax_deductible"))
    total_deductible = currency_totals(
        r for r in week_receipts if r.get("tax_deductible")
    )

    lines = ["<b>Weekly deduction check-in!</b>\n"]

    if week_receipts:
        lines.append(
            f"This week: {len(week_receipts)} receipts tracked, "
            f"{deductible_count} deductible ({escape(format_totals(total_deductible))}).\n"
        )
    else:
        lines.append(
            "Nothing tracked this week — did anything slip through? Here's what's commonly missed:\n"
        )

    lines.append("<b>Did you buy or pay for anything deductible?</b>\n")

    for category in REMINDER_CATEGORIES:
        examples = [
            subcategory["examples"][0]
            for subcategory in TAX_CATEGORIES[category]["subcategories"].values()
            if subcategory.get("examples")
        ][:5]
        lines.append(f"<b>{escape(english_label(category))}</b>")
        lines.append(f"  • {escape(', '.join(examples))}")
        lines.append("")
    lines.append("<i>Just send a photo, PDF, or text and I'll handle the rest!</i>")

    await ctx.bot.send_message(
        chat_id=chat_id, text="\n".join(lines), parse_mode="HTML"
    )


async def report_error(update, context):
    logger.error("Telegram operation failed: %s", type(context.error).__name__)
    if update is not None and update.effective_message:
        try:
            await update.effective_message.reply_text(
                str(context.error) if isinstance(context.error, UserInputError) else
                "The operation could not finish. Use /summary to check whether it was recorded before retrying."
            )
        except Exception:
            logger.warning("Could not deliver failure notice")


async def purge_pending(context):
    clarifications.purge_expired()


async def update_heartbeat(context):
    heartbeat = config.DATA_DIR / "heartbeat"
    heartbeat.touch()


def schedule_group_jobs(job_queue):
    if not job_queue or not config.GROUP_CHAT_ID:
        return

    async def march_only_callback(ctx):
        if datetime.now(ZoneInfo("Europe/Berlin")).month == 3:
            await march_tax_reminder(ctx)

    job_queue.run_daily(
        march_only_callback,
        time=time(hour=10, minute=0, tzinfo=ZoneInfo("Europe/Berlin")),
        days=(1,),
        name="march_tax_reminder",
    )

    job_queue.run_daily(
        weekly_csv_report,
        time=time(hour=9, minute=0, tzinfo=ZoneInfo("Europe/Berlin")),
        days=(1,),
        name="weekly_csv_report",
    )

    job_queue.run_daily(
        weekly_deduction_reminder,
        time=time(hour=18, minute=0, tzinfo=ZoneInfo("Europe/Berlin")),
        days=(1,),
        name="weekly_deduction_reminder",
    )


def main():
    global _bot_instance
    os.umask(0o077)
    config.validate_startup()
    initialize_services()

    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .concurrent_updates(False)
        .build()
    )
    _bot_instance = app.bot
    analyzer._alert_callback = _send_credit_alert

    logger.info("Bot access restrictions enabled")

    app.add_error_handler(report_error)
    app.add_handler(
        CallbackQueryHandler(confirm_deductible, pattern=r"^deductible:[0-9]+$")
    )
    app.add_handler(CommandHandler(("start", "help"), cmd_start))
    app.add_handler(CommandHandler("categories", cmd_categories))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("pin", cmd_pin))
    app.add_handler(CommandHandler("receipt", cmd_receipt))
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CommandHandler("why", cmd_why))
    app.add_handler(CommandHandler("edit", cmd_edit))
    app.add_handler(CallbackQueryHandler(prompt_edit, pattern=r"^edit:[0-9]+$"))
    app.add_handler(CommandHandler("review", cmd_review))
    app.add_handler(
        CallbackQueryHandler(manual_review, pattern=r"^manual_review:[0-9]+$")
    )
    for name in ("approve", "correct", "reject"):
        app.add_handler(CommandHandler(name, cmd_review_decision))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("retry", cmd_retry))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(update_heartbeat, interval=30, first=1)
        job_queue.run_repeating(purge_pending, interval=3600, first=10)
    schedule_group_jobs(job_queue)

    logger.info("Receipt Tracker Bot is running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
