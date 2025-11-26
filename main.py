#!/usr/bin/env python3
import discord
import asyncio
import json
import os
import curses
import subprocess
import textwrap
from typing import List, Optional

CONFIG_FILE = "config.json"
KNOWN_USERS_FILE = "known_users.json"
CONVERSATIONS_FILE = "conversations.json"

intents = discord.Intents.default()
intents.messages = True
intents.dm_messages = True

client = discord.Client(intents=intents)

message_queue = asyncio.Queue()
unread_count = 0
conversations = {}  # {user_id: [ "Author: content", ... ]}
bot_status = ""


# ---------------- Persistence ----------------
def load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def load_known_users() -> List[int]:
    data = load_json(KNOWN_USERS_FILE, [])
    return [int(x) for x in data]


def save_known_users(user_ids: List[int]):
    save_json(KNOWN_USERS_FILE, [int(x) for x in user_ids])


def load_conversations():
    global conversations
    data = load_json(CONVERSATIONS_FILE, {})
    conversations = {int(k): v for k, v in data.items()}


def save_conversations():
    save_json(CONVERSATIONS_FILE, conversations)


# ---------------- CONFIG MANAGEMENT ----------------
def load_token() -> Optional[str]:
    cfg = load_json(CONFIG_FILE, {})
    return cfg.get("token")


def save_token(token: str):
    save_json(CONFIG_FILE, {"token": token})


def get_token_interactive() -> str:
    token = load_token()
    while not token:
        print("Missing bot token.")
        token = input("Enter a valid bot token: ").strip()
        if token:
            save_token(token)
    return token


# ---------------- DISCORD HELPERS ----------------
async def fetch_channel_history(channel: discord.DMChannel) -> List[discord.Message]:
    """Fetch full history for a DM channel (oldest first)."""
    msgs = []
    async for m in channel.history(limit=None, oldest_first=True):
        msgs.append(m)
    return msgs


async def send_reply(uid: int, reply: str) -> None:
    user = await client.fetch_user(uid)
    await user.send(reply)


# ---------------- DISCORD EVENTS ----------------
@client.event
async def on_ready():
    global bot_status
    bot_status = f"Connected as {client.user}"
    load_conversations()


@client.event
async def on_message(message):
    global unread_count
    if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
        await message_queue.put((message.author, message.content))
        unread_count += 1
        conversations.setdefault(message.author.id, []).append(f"{message.author}: {message.content}")
        save_conversations()
        known = set(load_known_users())
        known.add(message.author.id)
        save_known_users(sorted(list(known)))


# ---------------- UI HELPERS ----------------
def draw_wrapped(stdscr, start_row: int, start_col: int, text: str, max_width: int) -> int:
    """
    Draw `text` on `stdscr` starting at (start_row, start_col), wrapping to max_width.
    Returns the next row after the drawn text.
    """
    paragraphs = text.splitlines() or [""]
    row = start_row
    for para in paragraphs:
        if not para:
            row += 1
            continue
        wrapped = textwrap.wrap(para, width=max_width) or [""]
        for line in wrapped:
            try:
                stdscr.addstr(row, start_col, line)
            except Exception:
                stdscr.addstr(row, start_col, line[:max_width])
            row += 1
    return row


# ---------------- TERMINAL UI ----------------
def terminal_ui(stdscr, loop):
    global unread_count, bot_status, conversations

    curses.curs_set(0)
    stdscr.nodelay(False)

    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, "=== Discord DM Relay Bot ===", curses.A_BOLD)
        stdscr.addstr(1, 0, bot_status or "Not connected", curses.A_DIM)
        stdscr.addstr(2, 0, f"Unread Messages: {unread_count}", curses.A_REVERSE)

        stdscr.addstr(4, 0, "Menu:")
        stdscr.addstr(5, 2, "1. Conversations")
        stdscr.addstr(6, 2, "2. Select Conversation")
        stdscr.addstr(7, 2, "3. New Conversation")
        stdscr.addstr(8, 2, "4. Change Token")
        stdscr.addstr(9, 2, "5. Reload Messages (Full History)")
        stdscr.addstr(10, 2, "6. GitHub Update")
        stdscr.addstr(11, 2, "7. Exit")

        stdscr.addstr(13, 0, "Select option: ")
        stdscr.refresh()

        curses.echo()
        try:
            choice = stdscr.getstr(13, 15).decode("utf-8").strip()
        except Exception:
            choice = ""
        curses.noecho()

        if choice == "1":
            stdscr.clear()
            stdscr.addstr(0, 0, "=== Conversations ===", curses.A_BOLD)
            row = 2
            if not conversations:
                row = draw_wrapped(stdscr, row, 0, "No conversations available.", curses.COLS - 1)
            else:
                for uid, msgs in conversations.items():
                    row = draw_wrapped(stdscr, row, 0, f"User {uid}: {len(msgs)} messages", curses.COLS - 1)
            stdscr.addstr(row + 1, 0, "Press any key to return...")
            stdscr.getch()

        elif choice == "2":
            stdscr.clear()
            stdscr.addstr(0, 0, "=== Select Conversation ===", curses.A_BOLD)
            stdscr.addstr(2, 0, "Enter user ID: ")
            stdscr.refresh()
            curses.echo()
            uid_raw = stdscr.getstr(2, 15).decode("utf-8").strip()
            curses.noecho()
            try:
                uid = int(uid_raw)
                msgs = conversations.get(uid, [])
                stdscr.clear()
                stdscr.addstr(0, 0, f"=== Conversation with {uid} ===", curses.A_BOLD)
                row = 2
                if not msgs:
                    row = draw_wrapped(stdscr, row, 0, "No messages in this conversation.", curses.COLS - 1)
                else:
                    for msg in msgs[-200:]:
                        row = draw_wrapped(stdscr, row, 0, msg, curses.COLS - 1)
                stdscr.addstr(row + 1, 0, "Type reply: ")
                stdscr.refresh()
                curses.echo()
                reply = stdscr.getstr(row + 1, 12).decode("utf-8").strip()
                curses.noecho()
                if reply:
                    asyncio.run_coroutine_threadsafe(send_reply(uid, reply), loop)
                    conversations.setdefault(uid, []).append(f"You: {reply}")
                    save_conversations()
                    stdscr.addstr(row + 3, 0, "Reply scheduled. Press any key...")
                    stdscr.getch()
            except ValueError:
                stdscr.addstr(4, 0, "Invalid user ID. Press any key...")
                stdscr.getch()

        elif choice == "3":
            stdscr.clear()
            stdscr.addstr(0, 0, "=== New Conversation ===", curses.A_BOLD)
            stdscr.addstr(2, 0, "Enter user ID: ")
            stdscr.refresh()
            curses.echo()
            uid_raw = stdscr.getstr(2, 15).decode("utf-8").strip()
            curses.noecho()
            try:
                user_id = int(uid_raw)
                stdscr.addstr(3, 0, "Enter message: ")
                stdscr.refresh()
                curses.echo()
                msg = stdscr.getstr(3, 15).decode("utf-8").strip()
                curses.noecho()
                if msg:
                    asyncio.run_coroutine_threadsafe(send_reply(user_id, msg), loop)
                    conversations.setdefault(user_id, []).append(f"You: {msg}")
                    known = set(load_known_users())
                    known.add(user_id)
                    save_known_users(sorted(list(known)))
                    save_conversations()
                    stdscr.addstr(5, 0, "Message scheduled. Press any key...")
                    stdscr.getch()
            except ValueError:
                stdscr.addstr(4, 0, "Invalid user ID. Press any key...")
                stdscr.getch()

        elif choice == "4":
            stdscr.clear()
            stdscr.addstr(0, 0, "=== Change Token ===", curses.A_BOLD)
            stdscr.addstr(2, 0, "Enter new bot token: ")
            stdscr.refresh()
            curses.echo()
            new_token = stdscr.getstr(2, 22).decode("utf-8").strip()
            curses.noecho()
            if new_token:
                save_token(new_token)
                stdscr.addstr(4, 0, "Token updated. Restart required. Press any key...")
                stdscr.getch()
                asyncio.run_coroutine_threadsafe(client.close(), loop)
                break
            else:
                stdscr.addstr(4, 0, "No token entered. Press any key...")
                stdscr.getch()

        elif choice == "5":  # Reload Messages (Full History)
            stdscr.clear()
            stdscr.addstr(0, 0, "=== Reload Messages (Full History) ===", curses.A_BOLD)
            stdscr.refresh()
            try:
                known_ids = load_known_users()
                found_any = False
                for uid in known_ids:
                    try:
                        user = asyncio.run_coroutine_threadsafe(client.fetch_user(uid), loop).result()
                        channel = user.dm_channel or asyncio.run_coroutine_threadsafe(user.create_dm(), loop).result()
                        if channel is None:
                            continue
                        found_any = True
                        history_future = asyncio.run_coroutine_threadsafe(fetch_channel_history(channel), loop)
                        history = history_future.result()
                        conversations[uid] = []
                        for msg in history:
                            conversations[uid].append(f"{msg.author}: {msg.content}")
                    except Exception:
                        continue

                for channel in client.private_channels:
                    if isinstance(channel, discord.DMChannel):
                        recipient = channel.recipient
                        if recipient:
                            uid = recipient.id
                            if uid not in conversations:
                                history_future = asyncio.run_coroutine_threadsafe(fetch_channel_history(channel), loop)
                                history = history_future.result()
                                conversations[uid] = [f"{m.author}: {m.content}" for m in history]
                                found_any = True
                                known = set(load_known_users())
                                known.add(uid)
                                save_known_users(sorted(list(known)))

                drained = []
                while not message_queue.empty():
                    author, content = message_queue.get_nowait()
                    unread_count -= 1
                    conversations.setdefault(author.id, []).append(f"{author}: {content}")
                    drained.append((author, content))
                    known = set(load_known_users())
                    known.add(author.id)
                    save_known_users(sorted(list(known)))

                save_conversations()

                if found_any or drained:
                    stdscr.addstr(2, 0, "Reload complete. Full message history pulled.")
                else:
                    stdscr.addstr(2, 0, "No DM channels or known users to reload (no prior DMs).")
            except Exception as e:
                stdscr.addstr(2, 0, f"Error reloading: {e}")
            stdscr.addstr(4, 0, "Press any key to return...")
            stdscr.getch()

        elif choice == "6":  # GitHub Update
            stdscr.clear()
            stdscr.addstr(0, 0, "=== GitHub Update ===", curses.A_BOLD)
            stdscr.refresh()
            try:
                result = subprocess.run(
                    ["git", "pull", "origin", "main"],
                    capture_output=True, text=True, cwd=os.path.expanduser("~/Discord-DM-Bot")
                )
                out = result.stdout.strip() or "(no output)"
                err = result.stderr.strip()
                row = 2
                row = draw_wrapped(stdscr, row, 0, out, curses.COLS - 1)
                if err:
                    row = draw_wrapped(stdscr, row + 1, 0, err, curses.COLS - 1)
                stdscr.addstr(row + 1, 0, "Update complete. Press any key...")
            except Exception as e:
                stdscr.addstr(2, 0, f"Error: {e}")
                stdscr.addstr(4, 0, "Press any key...")
            stdscr.getch()

        elif choice == "7":
            asyncio.run_coroutine_threadsafe(client.close(), loop)
            break

        else:
            stdscr.addstr(15, 0, "Invalid choice. Press any key...")
            stdscr.getch()


def run_curses(loop):
    curses.wrapper(lambda stdscr: terminal_ui(stdscr, loop))


# ---------------- MAIN ----------------
async def main():
    load_conversations()
    token = get_token_interactive()
    loop = asyncio.get_running_loop()
    await asyncio.gather(
        asyncio.to_thread(run_curses, loop),
        client.start(token)
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        try:
            asyncio.run(client.close())
        except Exception:
            pass
