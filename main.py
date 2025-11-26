import discord
import asyncio
import json
import os
import curses
import subprocess

CONFIG_FILE = "config.json"

intents = discord.Intents.default()
intents.messages = True
intents.dm_messages = True

client = discord.Client(intents=intents)

message_queue = asyncio.Queue()
unread_count = 0
conversations = {}
bot_status = ""


# ---------------- CONFIG MANAGEMENT ----------------
def load_token():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f).get("token")
        except Exception:
            return None
    return None


def save_token(token):
    with open(CONFIG_FILE, "w") as f:
        json.dump({"token": token}, f, indent=4)


async def validate_token(token):
    try:
        await client.login(token)
        return True
    except discord.LoginFailure:
        return False


async def get_valid_token():
    token = load_token()
    while not token or not await validate_token(token):
        print("Missing or invalid token.")
        token = input("Enter a valid bot token: ").strip()
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
    if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
        await message_queue.put((message.author, message.content))
        unread_count += 1
        conversations.setdefault(message.author.id, []).append(f"{message.author}: {message.content}")


# ---------------- TERMINAL UI ----------------
def terminal_ui(stdscr, loop):
    global unread_count, bot_status
    curses.curs_set(0)
    stdscr.nodelay(False)

    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, "=== Discord DM Relay Bot ===", curses.A_BOLD)
        stdscr.addstr(1, 0, bot_status, curses.A_DIM)
        stdscr.addstr(2, 0, f"Unread Messages: {unread_count}", curses.A_REVERSE)

        stdscr.addstr(4, 0, "Menu:")
        stdscr.addstr(5, 2, "1. Conversations")
        stdscr.addstr(6, 2, "2. Select Conversation")
        stdscr.addstr(7, 2, "3. New Conversation")
        stdscr.addstr(8, 2, "4. Change Token")
        stdscr.addstr(9, 2, "5. Update (refresh messages)")
        stdscr.addstr(10, 2, "6. GitHub Update")
        stdscr.addstr(11, 2, "7. Exit")

        stdscr.addstr(13, 0, "Select option: ")
        stdscr.refresh()

        curses.echo()
        choice = stdscr.getstr(13, 15).decode("utf-8").strip()
        curses.noecho()
        
        if choice == "1":
            stdscr.clear()
            stdscr.addstr(0, 0, "=== Conversations ===", curses.A_BOLD)
            row = 2
            for uid, msgs in conversations.items():
                stdscr.addstr(row, 0, f"User {uid}: {len(msgs)} messages")
                row += 1
            stdscr.addstr(row+1, 0, "Press any key to return...")
            stdscr.getch()

        elif choice == "2":
            stdscr.clear()
            stdscr.addstr(0, 0, "=== Select Conversation ===", curses.A_BOLD)
            stdscr.addstr(2, 0, "Enter user ID: ")
            stdscr.refresh()
            curses.echo()
            uid = stdscr.getstr(2, 15).decode("utf-8").strip()
            curses.noecho()
            try:
                uid = int(uid)
                msgs = conversations.get(uid, [])
                stdscr.clear()
                stdscr.addstr(0, 0, f"=== Conversation with {uid} ===", curses.A_BOLD)
                row = 2
                for msg in msgs[-10:]:
                    stdscr.addstr(row, 0, msg)
                    row += 1
                stdscr.addstr(row+1, 0, "Type reply: ")
                stdscr.refresh()
                curses.echo()
                reply = stdscr.getstr(row+1, 12).decode("utf-8").strip()
                curses.noecho()
                if reply:
                    asyncio.run_coroutine_threadsafe(send_reply(uid, reply), loop)
                    conversations.setdefault(uid, []).append(f"You: {reply}")
                    stdscr.addstr(row+3, 0, "Reply scheduled. Press any key...")
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
            uid = stdscr.getstr(2, 15).decode("utf-8").strip()
            curses.noecho()
            try:
                user_id = int(uid)
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
            save_token(new_token)
            stdscr.addstr(4, 0, "Token updated. Restart required. Press any key...")
            stdscr.getch()
            asyncio.run_coroutine_threadsafe(client.close(), loop)
            break

        elif choice == "5":
            stdscr.clear()
            stdscr.addstr(0, 0, "=== Update (refresh messages) ===", curses.A_BOLD)
            new_msgs = []
            while not message_queue.empty():
                author, content = message_queue.get_nowait()
                unread_count -= 1
                conversations.setdefault(author.id, []).append(f"{author}: {content}")
                new_msgs.append((author, content))

            if new_msgs:
                row = 2
                for author, content in new_msgs:
                    stdscr.addstr(row, 0, f"From {author}: {content}")
                    row += 1
            else:
                stdscr.addstr(2, 0, f"Unread Messages: {unread_count}")
                stdscr.addstr(4, 0, "No new messages.")

            stdscr.addstr(6, 0, "Press any key to return...")
            stdscr.getch()

        elif choice == "6":
            stdscr.clear()
            stdscr.addstr(0, 0, "=== GitHub Update ===", curses.A_BOLD)
            try:
                result = subprocess.run(
                    ["git", "pull", "origin", "main"],
                    capture_output=True, text=True, cwd=os.path.expanduser("~/Discord-DM-Bot")
                )
                stdscr.addstr(2, 0, result.stdout)
                if result.stderr:
                    stdscr.addstr(4, 0, result.stderr)
                stdscr.addstr(6, 0, "Update complete. Press any key...")
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


# ---------------- HELPERS ----------------
async def send_reply(uid, reply):
    user = await client.fetch_user(uid)
    await user.send(reply)


# ---------------- MAIN ----------------
async def main():
    token = await get_valid_token()
    loop = asyncio.get_running_loop()
    await asyncio.gather(
        client.connect(),
        asyncio.to_thread(run_curses, loop)
    )

asyncio.run(main())
