# taxedo-cat

A Telegram bot for tracking expenses for German income tax returns.
Send a receipt photo, PDF, Office document or text message; the bot extracts
text locally and cleans it from your personal information, uses Claude with a local tax knowledge base to suggest a category
and deductibility, and saves the expense in SQLite. It can ask follow-up
questions and flag uncertain results for review.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather).
2. Add the bot to a Telegram group. Make it an admin.
3. Fill in these required values in `.env`:

   | Variable | Value |
   | --- | --- |
   | `TELEGRAM_BOT_TOKEN` | BotFather token |
   | `GROUP_CHAT_ID` | Numeric ID of your Telegram group |
   | `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs |
   | `ANTHROPIC_API_KEY` | Anthropic API key |

   To find the IDs, send a message in the group and check
   `https://api.telegram.org/bot<TOKEN>/getUpdates` while the bot is stopped.
   Use `message.chat.id` for the group and `message.from.id` for the sender.
   Once running, `/myid` also returns your user ID. 

5. Build and run:

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    python -m taxedo
    ```

   ```bash
   docker compose up -d --build
   docker compose logs -f bot
   ```

## Use

| Command | Purpose |
| --- | --- |
| `/help` | Show all commands |
| `/summary [year month]` | Monthly expense totals |
| `/stats` | All-time totals |
| `/export [year]` | Export receipts as CSV |
| `/review [id]` | Review uncertain classifications |
| `/why <id>` | Explain a classification |
| `/receipt <id>` | Retrieve the original upload |
| `/retry` | Retry a failed analysis |
| `/cancel` | Cancel a pending expense |

