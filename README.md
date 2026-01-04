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
