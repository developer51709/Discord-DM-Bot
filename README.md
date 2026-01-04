# 📩 Discord DM Relay Bot  
A powerful, terminal‑driven Discord DM relay system that receives, stores, and manages direct messages from users.  
Designed for automation, message auditing, long‑term conversation storage, and interactive CLI‑based message handling.

This bot is built for reliability, persistence, and high‑volume DM workflows — not just simple broadcast messaging.

---

## 🚀 Key Features

### 📨 Real‑time DM Relay
- Captures all incoming direct messages to the bot  
- Queues messages for processing  
- Tracks unread message counts  
- Stores conversations per‑user

### 💾 Persistent Storage
Automatically saves:
- `known_users.json` — list of all users who have DM’d the bot  
- `conversations.json` — full message history per user  
- `config.json` — bot token and configuration  

Uses **atomic file writes** to prevent corruption.

### 🔄 History Reload System
A robust async task that:
- Fetches DM history for all known users  
- Uses concurrency limits  
- Handles timeouts  
- Reconstructs conversation logs  
- Merges queued messages  
- Recovers partial results on failure

### 🧵 Thread‑Safe Architecture
- Thread locks for shared state  
- Queues for incoming messages  
- Async + threading hybrid design  
- Safe concurrent writes to disk

### 🎨 Color‑Coded CLI
If `colorama` is installed, the terminal UI includes:
- Headers  
- Info messages  
- Warnings  
- Errors  
- Prompts  
- Success indicators  

Fallback mode works without color support.

### 🖥️ Interactive Terminal Interface
Includes utilities for:
- Clearing the screen  
- Pretty‑printing wrapped text  
- Displaying headers  
- Managing bot status  

---

## 📦 Installation

### 1. Clone the repository
```bash
git clone https://github.com/developer51709/Discord-DM-Bot.git
cd Discord-DM-Bot
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure your bot token
Create a `config.json` file or let the bot prompt you interactively:

```json
{
  "token": "YOUR_BOT_TOKEN"
}
```

---

## ▶️ Running the Bot
Start the bot with:

```bash
python3 main.py
```

On first launch, if no token is found, the bot will prompt:

```Code
Missing bot token.
Enter a valid bot token:
```

Once authenticated, the bot will:

- Connect to Discord

- Load conversation history

- Begin listening for DMs

- Start the relay loop

---

## 📁 Data Files
The bot automatically manages:

| File | Purpose |
|------|---------|
| `config.json` | Stores bot token |
| `known_users.json` | List of user IDs who have DM’d the bot |
| `conversations.json` | Full DM history per user |

All writes are **atomic** to prevent corruption.

---

## 🔧 Configuration
You can adjust behavior via constants in the code:

| Setting | Description |
|---------|-------------|
| `HISTORY_FETCH_LIMIT` | Max messages to fetch per DM channel |
| `HISTORY_CONCURRENCY` | Number of concurrent history fetch tasks |
| `HISTORY_FETCH_TIMEOUT` | Timeout for full reload operation |

---

## 🧠 How It Works (Technical Overview)
### 1. DM Capture
`on_message` intercepts all DMChannel messages and:

- Adds them to a queue

- Updates unread counters

- Appends to conversation logs

- Saves known users

### 2. History Reload
The `reload_all_histories()` coroutine:

- Iterates through all known users

- Fetches DM history

- Rebuilds conversation logs

- Merges queued messages

- Saves everything atomically

### 3. Thread‑Safe State
Locks protect:

- `unread_count`

- `conversations`

- `bot_status`

### 4. CLI Utilities
Functions like `clear_screen()`, `wrap_text()`, and `print_header()` provide a clean terminal UI.

---

## 🛡️ Required Discord Intents
Enable these in the Discord Developer Portal:

- Direct Messages

- Message Content (if needed for content processing)

---

## 🐛 Troubleshooting
### Bot doesn’t receive DMs
- Ensure DM intents are enabled

- Confirm the bot is not blocked by the user

- Check terminal logs for errors

### History reload fails
- Increase `HISTORY_FETCH_TIMEOUT`

- Reduce `HISTORY_CONCURRENCY`

- Check for rate limits in logs

### JSON files corrupted
This should never happen due to atomic writes, but if it does:

- Delete the affected file

- Restart the bot

---

## 🤝 Contributing
Pull requests are welcome!
If you’d like to add features (UI, commands, analytics, etc.), feel free to fork the repo.

---

## 📜 License
This project is licensed under the MIT License.

---

## ⭐ Support the Project
If this bot helps you, consider starring the repository!
