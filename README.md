# taxedo-cat

A private Telegram bot for tracking receipts and expenses between two people filing separate German tax declarations (Steuerklasse 1, unmarried). You send a photo, PDF, or text description of an expense to a shared group chat. The bot extracts the text, categorizes it for German tax purposes using Claude Haiku and appends it to a CSV. Every week it sends you a summary and asks if you missed anything.

The tax category definitions (every deductible category, subcategory and description) were written by Claude.

## How it works

**Photos.** Tesseract OCR extracts the text from the image, which is then sent to Claude Haiku for analysis. Claude never sees the image itself, only the extracted text.

**PDFs.** PyMuPDF pulls embedded text directly. If the PDF has no embedded text (e.g. a scan), the pages are rasterized and run through Tesseract first.

**Text messages.** Sent to Claude as-is.

In all three cases, Claude returns structured JSON: store name, date, total, line items, and a German tax category with a short explanation of why it qualifies (or doesn't). 

Duplicate detection hashes the file bytes (SHA-256) before doing anything. If you send the same file twice, it gets rejected immediately with a reference to the original entry.

## Tax categories

The bot maps expenses to the standard German Einkommensteuererklärung structure.

For gray-zone items (Amazon orders, books, furniture, fuel) Claude marks them as conditionally deductible and explains what would need to be true for the deduction to apply.

Use `/categories` in the chat to see the full list at any time.

## Scheduled messages

**Every Monday at 9:00** — the bot sends a CSV of the past 7 days' receipts directly to the group chat, with a total and the deductible subtotal.

**Every Monday at 18:00** — a deduction reminder. If receipts were tracked that week, it shows a quick summary. If nothing was logged, it runs through a checklist of commonly missed deductions.

**Every Monday in March at 10:00** — a tax season reminder with last year's totals, a breakdown of what was tracked as deductible, and a checklist for filing (ELSTER, Lohnsteuerbescheinigung, deadline reminders).

## Commands

| Command | What it does |
|---|---|
| `/start` or `/help` | Instructions and command list |
| `/summary` | This month's expenses, grouped by category and by person |
| `/summary 2025 11` | A specific month |
| `/stats` | All-time totals, top stores, top deduction categories |
| `/export` | Download all receipts as CSV |
| `/export 2025` | Just that year |
| `/pin` | Send and pin the command reference in the group (bot must be admin) |
| `/receipt <id>` | Retrieve the original file for any receipt by ID |
| `/budget` | Claude API spending — monthly and all-time cost, average per receipt |
| `/categories` | Full German tax deduction category reference |
| `/myid` | Your Telegram user ID, for setting up the whitelist |

## Storage

Three things are written to disk:

- `data/all_receipts.csv` — every receipt, appended permanently
- `data/files/` — original photos and PDFs, saved by file hash
- `data/budget.db` — SQLite, tracks Claude API token usage and cost per call

Use `/receipt <id>` to have the bot send you any saved file. Weekly CSVs are sent directly to Telegram and not saved. Text-only entries have no file.

## Setup

### Create the bot

Message [@BotFather](https://t.me/BotFather) on Telegram, create a new bot, and copy the token.

### Install Tesseract

```bash
# Ubuntu/Debian
sudo apt install tesseract-ocr tesseract-ocr-deu

# macOS
brew install tesseract tesseract-lang
```

### Configure

```bash
cp .env.example .env
```

Fill in `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
GROUP_CHAT_ID=-100XXXXXXXXXX
ANTHROPIC_API_KEY=your_key
PARTNER_1_NAME=YourName
PARTNER_2_NAME=TheirName
ALLOWED_USER_IDS=123456789,987654321
```


### Run

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

### Docker

```bash
docker build -t taxedo-cat .
docker run -d --name taxedo-cat \
  --env-file .env \
  -v ./data:/app/data \
  taxedo-cat
```
