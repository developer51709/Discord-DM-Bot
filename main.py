#!/usr/bin/env python3
import discord
import asyncio
import json
import os
import curses
import subprocess
from typing import List

CONFIG_FILE = "config.json"

intents = discord.Intents.default()
intents.messages = True
intents.dm_messages = True

client = discord.Client(intents=intents)

message_queue = asyncio.Queue()
unread_count = 0
conversations = {}  # {user_id: [ "Author: content", ... ]}
bot_status = ""


# ---------------- CONFIG MANAGEMENT ----------------
def load_token() -> str | None:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f).get("token")
        except Exception:
            return None
    return None


def save_token(token: str) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump({"token": token}, f, indent=4)


def get_token_interactive() -> str:
    token = load_token()
    while not token:
        print("Missing bot token.")
        token = input("Enter a valid bot token: ").strip()
        if token:
            save_token(token)
    return token


# ---------------- DISCORD EVENTS ----------------
@client.event
async def on_ready():
    global bot_status
    bot_status = f"Connected as {client.user}"


@client.event
async def on_message(message):
    global unread_count
    # Only handle direct messages from users (not bots)
    if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
        await message_queue.put((message.author, message.content))
        unread_count += 1
        conversations.setdefault(message.author.id, []).append(f"{message.author}: {message.content}")


# ---------------- ASYNC HELPERS ----------------
async def fetch_channel_history(channel: discord.DMChannel) -> List[discord.Message]:
    """Fetch full history for a DM channel (oldest first)."""
    msgs = []
    async for m in channel.history(limit=None, oldest_first=True):
        msgs.append(m)
    return msgs


async def send_reply(uid: int, reply: str) -> None:
    user = await client.fetch_user(uid)
    await user.send(reply)


# ---------------- TERMINAL UI ----------------
def terminal_ui(stdscr, loop):
    """
    Synchronous curses UI. Any async actions are scheduled back onto the event loop
    using asyncio.run_coroutine_threadsafe(..., loop).
    """
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
                stdscr.addstr(row, 0, "No conversations available.")
                row += 1
            else:
                for uid, msgs in conversations.items():
                    stdscr.addstr(row, 0, f"User {uid}: {len(msgs)} messages")
                    row += 1
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
                    stdscr.addstr(row, 0, "No messages in this conversation.")
                    row += 1
                else:
                    for msg in msgs[-20:]:
                        # Truncate long lines to fit screen width
                        try:
                            stdscr.addstr(row, 0, msg[:curses.COLS - 1])
                        except Exception:
                            stdscr.addstr(row, 0, msg[:80])
                        row += 1
                stdscr.addstr(row + 1, 0, "Type reply: ")
                stdscr.refresh()
                curses.echo()
                reply = stdscr.getstr(row + 1, 12).decode("utf-8").strip()
                curses.noecho()
                if reply:
                    asyncio.run_coroutine_threadsafe(send_reply(uid, reply), loop)
                    conversations.setdefault(uid, []).append(f"You: {reply}")
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
                # Close client and exit UI so user can restart with new token
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
                # Build conversations from all DM channels the bot knows about
                # client.private_channels is populated after the bot is ready
                found_any = False
                for channel in client.private_channels:
                    if isinstance(channel, discord.DMChannel):
                        found_any = True
                        recipient = channel.recipient
                        if recipient is None:
                            continue
                        uid = recipient.id
                        # Fetch history asynchronously and wait for result
                        future = asyncio.run_coroutine_threadsafe(fetch_channel_history(channel), loop)
                        history = future.result()
                        # Populate conversation with oldest-first messages
                        conversations[uid] = []
                        for msg in history:
                            # Use a simple text representation
                            conversations[uid].append(f"{msg.author}: {msg.content}")
                # Also include any queued messages that arrived while running
                # Drain message_queue into conversations
                drained = []
                while not message_queue.empty():
                    author, content = message_queue.get_nowait()
                    unread_count -= 1
                    conversations.setdefault(author.id, []).append(f"{author}: {content}")
                    drained.append((author, content))

                if found_any or drained:
                    stdscr.addstr(2, 0, "Reload complete. Full message history pulled.")
                else:
                    stdscr.addstr(2, 0, "No DM channels available to reload (no prior DMs).")
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
                # Show stdout and stderr (truncated if too long)
                out = result.stdout.strip() or "(no output)"
                err = result.stderr.strip()
                try:
                    stdscr.addstr(2, 0, out[:curses.COLS - 1])
                except Exception:
                    stdscr.addstr(2, 0, out[:80])
                if err:
                    try:
                        stdscr.addstr(4, 0, err[:curses.COLS - 1])
                    except Exception:
                        stdscr.addstr(4, 0, err[:80])
                stdscr.addstr(6, 0, "Update complete. Press any key...")
            except Exception as e:
                stdscr.addstr(2, 0, f"Error: {e}")
                stdscr.addstr(4, 0, "Press any key...")
            stdscr.getch()

        elif choice == "7":
            # Clean shutdown
            asyncio.run_coroutine_threadsafe(client.close(), loop)
            break

        else:
            stdscr.addstr(15, 0, "Invalid choice. Press any key...")
            stdscr.getch()


def run_curses(loop):
    curses.wrapper(lambda stdscr: terminal_ui(stdscr, loop))


# ---------------- MAIN ----------------
async def main():
    # Ensure token exists (interactive prompt if missing)
    token = get_token_interactive()
    loop = asyncio.get_running_loop()

    # Start the curses UI in a separate thread and the bot concurrently.
    # client.start(token) will log in and connect the client.
    await asyncio.gather(
        asyncio.to_thread(run_curses, loop),
        client.start(token)
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Ensure client is closed on Ctrl+C
        try:
            asyncio.run(client.close())
        except Exception:
            pass
