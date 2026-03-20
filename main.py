from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
from datetime import datetime, time
from io import BytesIO

from telegram import Update, Message
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from store import ReceiptStore
from analyzer import ReceiptAnalyzer
from tax_categories import get_tax_info_message
from budget_watchdog import BudgetWatchdog, format_budget_alert
from config import Config

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

store = ReceiptStore()
analyzer = ReceiptAnalyzer()
config = Config()

_bot_instance = None


def is_authorized(user_id: int) -> bool:
    if not config.ALLOWED_USER_IDS:
        return True
    return user_id in config.ALLOWED_USER_IDS


def require_auth(func):
    @functools.wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_authorized(user.id):
            await update.message.reply_text(
                "Sorry, this bot is private.\n"
                "Only authorized users can use it.\n\n"
                f"Your user ID is: <code>{user.id}</code>\n"
                "Ask the bot owner to add you.",
                parse_mode="HTML",
            )
            logger.warning(
                f"Unauthorized access attempt: {user.first_name} "
                f"(ID: {user.id}, @{user.username})"
            )
            return
        return await func(update, ctx)
    return wrapper


def hash_file(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def phash_image(image_bytes: bytes) -> str:
    from PIL import Image
    import imagehash
    img = Image.open(BytesIO(image_bytes))
    return "phash:" + str(imagehash.phash(img))


def check_duplicate(file_hash: str) -> dict | None:
    return store.find_by_hash(file_hash)


def save_receipt_file(file_bytes: bytes, file_hash: str, ext: str) -> str:
    files_dir = config.DATA_DIR / "files"
    files_dir.mkdir(exist_ok=True)
    path = files_dir / f"{file_hash}{ext}"
    if not path.exists():
        path.write_bytes(file_bytes)
    return str(path)


async def _send_budget_alert(alert: dict):
    chat_id = config.GROUP_CHAT_ID
    if not chat_id or not _bot_instance:
        logger.warning(f"Budget alert triggered but can't send: {alert['type']}")
        return
    try:
        msg = format_budget_alert(alert)
        await _bot_instance.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
        logger.info(f"Budget alert sent: {alert['type']}")
    except Exception as e:
        logger.error(f"Failed to send budget alert: {e}")


@require_auth
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        " *Receipt Tracker Bot*\n\n"
        "I track receipts and expenses for tax deduction purposes\\.\n\n"
        "*How to use:*\n"
        " Send a photo of a receipt \\(with optional caption\\)\n"
        " Send a PDF of a receipt\n"
        " Send a text message describing an expense\n\n"
        "*Commands:*\n"
        "/summary — Monthly expense summary\n"
        "/categories — View all tax deduction categories\n"
        "/export — Export as CSV \\(/export 2025 for one year\\)\n"
        "/stats — Spending statistics\n"
        "/budget — API spending tracker\n"
        "/myid — Show your Telegram user ID\n"
        "/help — Show this help message\n\n"
        "Every Monday: weekly CSV report and deduction reminder \n"
        "Every March: tax season reminder ",
        parse_mode="MarkdownV2",
    )


@require_auth
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


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
        await update.message.reply_text(
            f" No receipts found for {year}-{month:02d}."
        )
        return

    total = sum(r["total_amount"] or 0 for r in receipts)
    by_category = {}
    by_person = {}
    for r in receipts:
        cat = r["tax_category"] or "Uncategorized"
        by_category[cat] = by_category.get(cat, 0) + (r["total_amount"] or 0)
        person = r["sender_name"] or "Unknown"
        by_person[person] = by_person.get(person, 0) + (r["total_amount"] or 0)

    lines = [f" <b>Summary for {year}-{month:02d}</b>\n"]
    lines.append(f" Total: <b>€{total:.2f}</b>")
    lines.append(f" Receipts: <b>{len(receipts)}</b>\n")

    lines.append("<b>By Category:</b>")
    for cat, amt in sorted(by_category.items(), key=lambda x: -x[1]):
        lines.append(f"  • {cat}: €{amt:.2f}")

    lines.append("\n<b>By Person:</b>")
    for person, amt in sorted(by_person.items(), key=lambda x: -x[1]):
        lines.append(f"  • {person}: €{amt:.2f}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@require_auth
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    stats = store.get_stats()
    if not stats["total_receipts"]:
        await update.message.reply_text(" No receipts recorded yet.")
        return

    lines = [
        "<b>Overall Statistics</b>\n",
        f"Total receipts: <b>{stats['total_receipts']}</b>",
        f"Total spent: <b>€{stats['total_amount']:.2f}</b>",
        f"First receipt: <b>{stats['first_date']}</b>",
        f"Last receipt: <b>{stats['last_date']}</b>",
    ]

    if stats["top_stores"]:
        lines.append("\n<b>Top Stores:</b>")
        for store_name, count in stats["top_stores"]:
            lines.append(f"  • {store_name}: {count} receipts")

    if stats["top_categories"]:
        lines.append("\n<b>Top Tax Categories:</b>")
        for cat, amt in stats["top_categories"]:
            lines.append(f"  • {cat}: €{amt:.2f}")

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
        receipts = [
            r for r in store.get_all_receipts()
            if r.get("created_at", "").startswith(str(year))
        ]
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
async def cmd_budget(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    stats = analyzer.watchdog.get_stats()
    lines = [
        "<b>API Budget Status</b>\n",
        f"This month: <b>${stats['monthly_spend']:.4f}</b> ({stats['monthly_calls']} calls)",
        f"All-time: <b>${stats['total_spend']:.4f}</b> ({stats['total_calls']} calls)",
        f"Monthly alert at: ${stats['monthly_threshold']:.2f}",
        f"Total alert at: ${stats['total_threshold']:.2f}",
    ]

    if stats['monthly_calls'] > 0:
        avg = stats['monthly_spend'] / stats['monthly_calls']
        lines.append(f"\n Average: <b>${avg:.5f}</b> per receipt")
        lines.append(f" At this rate: ~${avg * 520:.2f}/year (10 receipts/week)")

    lines.append(
        "\n<i>If credits run out, I'll alert you here. Receipt analysis will be unavailable until topped up.</i>"
    )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@require_auth
async def cmd_pin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>Receipt Tracker</b>\n"
        "Send a photo, PDF, or text message to log an expense.\n\n"
        "<b>Logging</b>\n"
        " Photo — receipt image (OCR + tax analysis)\n"
        " PDF — invoice or document\n"
        " Text — describe an expense in words\n\n"
        "<b>Commands</b>\n"
        "/summary — this month's expenses\n"
        "/summary 2025 11 — a specific month\n"
        "/stats — all-time totals and top categories\n"
        "/export — download all receipts as CSV\n"
        "/export 2025 — a specific year\n"
        "/receipt &lt;id&gt; — retrieve the original file\n"
        "/categories — full German tax deduction reference\n"
        "/budget — Claude API spending\n"
        "/myid — your Telegram user ID"
    )
    sent = await update.message.reply_text(text, parse_mode="HTML")
    try:
        await ctx.bot.pin_chat_message(
            chat_id=update.effective_chat.id,
            message_id=sent.message_id,
            disable_notification=True,
        )
    except Exception:
        pass  # bot may not be admin — message still sent


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

    file_path = receipt.get("file_path")
    if not file_path or not os.path.exists(file_path):
        await update.message.reply_text(
            f"Receipt #{receipt_id} has no file saved "
            f"(text-only entries and receipts added before file storage was enabled don't have one)."
        )
        return

    caption = f"Receipt #{receipt_id}"
    if receipt.get("store_name"):
        caption += f" — {receipt['store_name']}"
    if receipt.get("receipt_date"):
        caption += f" ({receipt['receipt_date']})"

    with open(file_path, "rb") as f:
        await update.message.reply_document(document=f, caption=caption)


async def cmd_myid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f" <b>Your Telegram info:</b>\n\n"
        f" User ID: <code>{user.id}</code>\n"
        f" Name: {user.first_name or ''} {user.last_name or ''}\n"
        f" Username: @{user.username or 'none'}\n\n"
        f"<i>Send this ID to the bot owner to get access.</i>",
        parse_mode="HTML",
    )


#  MESSAGE HANDLERS

@require_auth
async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg: Message = update.message
    sender = msg.from_user
    caption = msg.caption or ""

    photo = msg.photo[-1]
    file = await ctx.bot.get_file(photo.file_id)
    file_bytes = await file.download_as_bytearray()

    file_hash = phash_image(bytes(file_bytes))
    existing = check_duplicate(file_hash)
    if existing:
        amount_line = f"\nAmount: €{existing['total_amount']:.2f}" if existing.get('total_amount') else ""
        await msg.reply_text(
            f" <b>Duplicate detected!</b>\n\n"
            f"This receipt was already recorded as #{existing['id']} "
            f"on {existing['created_at'][:10]}.\n"
            f"Store: {existing.get('store_name') or '—'}"
            f"{amount_line}\n\n"
            f"Skipping to avoid double-counting.",
            parse_mode="HTML",
        )
        return

    file_path = save_receipt_file(file_bytes, file_hash, ".jpg")

    processing_msg = await msg.reply_text(" Analyzing receipt...")
    analysis = await analyzer.analyze_image(file_bytes, caption)

    receipt_id = store.insert_receipt(
        sender_id=sender.id,
        sender_name=sender.first_name or sender.username or "Unknown",
        message_type="photo",
        file_path=file_path,
        file_hash=file_hash,
        caption=caption,
        raw_text=analysis.get("extracted_text", ""),
        store_name=analysis.get("store_name", ""),
        items=json.dumps(analysis.get("items", []), ensure_ascii=False),
        total_amount=analysis.get("total_amount"),
        currency=analysis.get("currency", "EUR"),
        tax_category=analysis.get("tax_category", ""),
        tax_subcategory=analysis.get("tax_subcategory", ""),
        tax_deductible=analysis.get("tax_deductible", False),
        deduction_notes=analysis.get("deduction_notes", ""),
        receipt_date=analysis.get("receipt_date"),
    )

    response = format_analysis_response(analysis, sender.first_name, receipt_id)
    await processing_msg.edit_text(response, parse_mode="HTML")


@require_auth
async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg: Message = update.message
    sender = msg.from_user
    doc = msg.document
    caption = msg.caption or ""

    file = await ctx.bot.get_file(doc.file_id)
    file_bytes = await file.download_as_bytearray()

    file_hash = hash_file(file_bytes)
    existing = check_duplicate(file_hash)
    if existing:
        await msg.reply_text(
            f" <b>Duplicate detected!</b>\n\n"
            f"This document was already recorded as #{existing['id']} "
            f"on {existing['created_at'][:10]}.\n"
            f"Skipping to avoid double-counting.",
            parse_mode="HTML",
        )
        return

    ext = (os.path.splitext(doc.file_name)[1] if doc.file_name else ".pdf").lower()
    file_path = save_receipt_file(file_bytes, file_hash, ext)

    processing_msg = await msg.reply_text(" Analyzing document...")

    if ext == ".pdf":
        analysis = await analyzer.analyze_pdf(file_bytes, caption)
    else:
        analysis = await analyzer.analyze_text(
            f"[File: {doc.file_name}]\n{caption}"
        )

    receipt_id = store.insert_receipt(
        sender_id=sender.id,
        sender_name=sender.first_name or sender.username or "Unknown",
        message_type="document",
        file_path=file_path,
        file_hash=file_hash,
        caption=caption,
        raw_text=analysis.get("extracted_text", ""),
        store_name=analysis.get("store_name", ""),
        items=json.dumps(analysis.get("items", []), ensure_ascii=False),
        total_amount=analysis.get("total_amount"),
        currency=analysis.get("currency", "EUR"),
        tax_category=analysis.get("tax_category", ""),
        tax_subcategory=analysis.get("tax_subcategory", ""),
        tax_deductible=analysis.get("tax_deductible", False),
        deduction_notes=analysis.get("deduction_notes", ""),
        receipt_date=analysis.get("receipt_date"),
    )

    response = format_analysis_response(analysis, sender.first_name, receipt_id)
    await processing_msg.edit_text(response, parse_mode="HTML")


@require_auth
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg: Message = update.message
    sender = msg.from_user
    text = msg.text or ""

    if text.startswith("/"):
        return

    processing_msg = await msg.reply_text(" Analyzing expense...")
    analysis = await analyzer.analyze_text(text)

    receipt_id = store.insert_receipt(
        sender_id=sender.id,
        sender_name=sender.first_name or sender.username or "Unknown",
        message_type="text",
        file_path=None,
        file_hash=None,
        caption=text,
        raw_text=text,
        store_name=analysis.get("store_name", ""),
        items=json.dumps(analysis.get("items", []), ensure_ascii=False),
        total_amount=analysis.get("total_amount"),
        currency=analysis.get("currency", "EUR"),
        tax_category=analysis.get("tax_category", ""),
        tax_subcategory=analysis.get("tax_subcategory", ""),
        tax_deductible=analysis.get("tax_deductible", False),
        deduction_notes=analysis.get("deduction_notes", ""),
        receipt_date=analysis.get("receipt_date"),
    )

    response = format_analysis_response(analysis, sender.first_name, receipt_id)
    await processing_msg.edit_text(response, parse_mode="HTML")


#  HELPERS

def format_analysis_response(analysis: dict, sender_name: str, receipt_id: int) -> str:
    lines = [" <b>Receipt Recorded!</b>\n"]

    if analysis.get("store_name"):
        lines.append(f" <b>Store:</b> {analysis['store_name']}")
    if analysis.get("total_amount"):
        currency = analysis.get("currency", "EUR")
        symbol = "€" if currency == "EUR" else currency
        lines.append(f" <b>Total:</b> {symbol}{analysis['total_amount']:.2f}")
    if analysis.get("receipt_date"):
        lines.append(f" <b>Date:</b> {analysis['receipt_date']}")

    lines.append(f" <b>Recorded by:</b> {sender_name}")

    if analysis.get("items"):
        lines.append("\n <b>Items:</b>")
        for item in analysis["items"][:10]:
            name = item.get("name", "?")
            price = item.get("price")
            if price:
                lines.append(f"  • {name} — €{price:.2f}")
            else:
                lines.append(f"  • {name}")
        if len(analysis["items"]) > 10:
            lines.append(f"  ... and {len(analysis['items']) - 10} more")

    lines.append("")
    if analysis.get("tax_deductible"):
        lines.append(" <b>Tax Deductible:</b> Yes")
        lines.append(f" <b>Category:</b> {analysis.get('tax_category', 'N/A')}")
        if analysis.get("tax_subcategory"):
            lines.append(f" <b>Subcategory:</b> {analysis['tax_subcategory']}")
        if analysis.get("deduction_notes"):
            lines.append(f" <b>Note:</b> {analysis['deduction_notes']}")
    else:
        lines.append(" <b>Tax Deductible:</b> Likely not")
        if analysis.get("deduction_notes"):
            lines.append(f" <b>Note:</b> {analysis['deduction_notes']}")

    lines.append(f"\n Receipt #{receipt_id}")
    return "\n".join(lines)


#  SCHEDULED JOBS

async def march_tax_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = config.GROUP_CHAT_ID
    if not chat_id:
        return

    year = datetime.now().year
    stats = store.get_stats_for_year(year - 1)

    lines = [
        " <b>TAX SEASON REMINDER!</b> \n",
        f"It's March {year} — time to prepare your {year - 1} tax declarations!\n",
        " <b>Reminder — You both file separately (Steuerklasse 1)</b>\n",
    ]

    if stats["total_receipts"]:
        lines.append(" <b>Last year's summary:</b>")
        lines.append(f"   Total receipts: {stats['total_receipts']}")
        lines.append(f"   Total tracked: €{stats['total_amount']:.2f}")
        if stats["deductible_amount"]:
            lines.append(f"   Potentially deductible: €{stats['deductible_amount']:.2f}")

    lines.extend([
        "\n<b> Action items:</b>",
        "  1. Export your receipts with /export",
        "  2. Review each category for deductions",
        "  3. Gather Lohnsteuerbescheinigung from employer",
        "  4. File via ELSTER or your Steuerberater",
        "  5. Deadline: July 31st (or Feb 28 next year with Steuerberater)",
        "\nGood luck! ",
    ])

    await ctx.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")


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
            text=" <b>Weekly report</b>\n\nNo receipts this week. Nothing to export.",
            parse_mode="HTML",
        )
        return

    total = sum(r.get("total_amount") or 0 for r in week_receipts)
    deductible = sum(
        r.get("total_amount") or 0 for r in week_receipts if r.get("tax_deductible")
    )

    csv_str = store.receipts_to_csv_string(week_receipts)
    buf = BytesIO(csv_str.encode("utf-8"))
    buf.name = f"week{week_num}_{year}_receipts.csv"

    await ctx.bot.send_document(
        chat_id=chat_id,
        document=buf,
        caption=(
            f" <b>Weekly report — Week {week_num}, {year}</b>\n\n"
            f" Receipts: {len(week_receipts)}\n"
            f" Total: €{total:.2f}\n"
            f" Deductible: €{deductible:.2f}"
        ),
        parse_mode="HTML",
    )


async def weekly_deduction_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = config.GROUP_CHAT_ID
    if not chat_id:
        return

    week_receipts = store.get_receipts_last_n_days(7)
    deductible_count = sum(1 for r in week_receipts if r.get("tax_deductible"))
    total_deductible = sum(
        r.get("total_amount", 0) or 0
        for r in week_receipts
        if r.get("tax_deductible")
    )

    lines = [" <b>Weekly deduction check-in!</b>\n"]

    if week_receipts:
        lines.append(
            f"This week: {len(week_receipts)} receipts tracked, "
            f"{deductible_count} deductible (€{total_deductible:.2f}).\n"
        )
    else:
        lines.append("Nothing tracked this week — did anything slip through? Here's what's commonly missed:\n")

    lines.append(" <b>Did you buy or pay for anything deductible?</b>\n")

    lines.extend([
        " <b>Werbungskosten (work expenses)</b>",
        "  • Laptop, monitor, keyboard, mouse, headset",
        "  • Software: JetBrains, Adobe, GitHub Copilot, Office 365",
        "  • Online courses: Udemy, Coursera, LinkedIn Learning",
        "  • Deutschlandticket or train tickets (commute)",
        "",
        " <b>Außergewöhnliche Belastungen (medical)</b>",
        "  • Doctor/dentist co-pays, prescriptions, glasses, physio",
        "",
        " <b>Sonderausgaben</b>",
        "  • Insurance bills (Haftpflicht, BU, Unfall), donations",
        "",
        " <b>Haushaltsnahe Dienstleistungen</b>",
        "  • Cleaning, Handwerker, gardener, Schornsteinfeger",
        "",
        " <i>Just send a photo, PDF, or text and I'll handle the rest!</i>",
    ])

    await ctx.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")



def main():
    global _bot_instance

    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set! Check your .env file.")
        return

    store.init()

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    _bot_instance = app.bot
    analyzer._alert_callback = _send_budget_alert

    if config.ALLOWED_USER_IDS:
        logger.info(f" Bot restricted to user IDs: {config.ALLOWED_USER_IDS}")
    else:
        logger.warning("  No ALLOWED_USER_IDS set — bot is open to everyone!")

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("categories", cmd_categories))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("budget", cmd_budget))
    app.add_handler(CommandHandler("pin", cmd_pin))
    app.add_handler(CommandHandler("receipt", cmd_receipt))
    app.add_handler(CommandHandler("myid", cmd_myid))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    job_queue = app.job_queue
    if job_queue and config.GROUP_CHAT_ID:
        async def march_only_callback(ctx):
            if datetime.now().month == 3:
                await march_tax_reminder(ctx)

        job_queue.run_daily(
            march_only_callback,
            time=time(hour=10, minute=0),
            days=(0,),
            name="march_tax_reminder",
        )

        job_queue.run_daily(
            weekly_csv_report,
            time=time(hour=9, minute=0),
            days=(0,),
            name="weekly_csv_report",
        )

        job_queue.run_daily(
            weekly_deduction_reminder,
            time=time(hour=18, minute=0),
            days=(0,),
            name="weekly_deduction_reminder",
        )

        async def reset_budget_alerts(ctx):
            analyzer.watchdog.reset_monthly_alert()

        job_queue.run_monthly(
            reset_budget_alerts,
            when=time(hour=0, minute=1),
            day=1,
            name="budget_reset",
        )

    logger.info(" Receipt Tracker Bot is running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
