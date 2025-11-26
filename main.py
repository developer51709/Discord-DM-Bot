import discord
import asyncio
import json
import os
import curses

CONFIG_FILE = "config.json"

intents = discord.Intents.default()
intents.messages = True
intents.dm_messages = True

client = discord.Client(intents=intents)

message_queue = asyncio.Queue()
unread_count = 0
conversations = {}

# ---------------- CONFIG ----------------
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

# ---------------- EVENTS ----------------
@client.event
async def on_ready():
    print(f"Bot connected as {client.user}")

@client.event
async def on_message(message):
    global unread_count
    if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
        await message_queue.put((message.author, message.content))
        unread_count += 1
        conversations.setdefault(message.author.id, []).append(f"{message.author}: {message.content}")

# ---------------- TERMINAL UI ----------------
def terminal_ui(stdscr, loop):
    global unread_count
    curses.curs_set(0)
    stdscr.nodelay(False)

    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, "=== Discord DM Relay Bot ===", curses.A_BOLD)
        stdscr.addstr(1, 0, f"Unread Messages: {unread_count}", curses.A_REVERSE)

        stdscr.addstr(3, 0, "Menu:")
        stdscr.addstr(4, 2, "1. Conversations")
        stdscr.addstr(5, 2, "2. Select Conversation")
        stdscr.addstr(6, 2, "3. New Conversation")
        stdscr.addstr(7, 2, "4. Change Token")
        stdscr.addstr(8, 2, "5. Update")
        stdscr.addstr(9, 2, "6. Exit")

        stdscr.addstr(11, 0, "Select option: ")
        stdscr.refresh()

        choice = stdscr.getstr(11, 15).decode("utf-8").strip()

        if choice == "6":
            break

        elif choice == "2":  # Select Conversation
            stdscr.clear()
            stdscr.addstr(0, 0, "Enter user ID: ")
            stdscr.refresh()
            uid = stdscr.getstr(0, 15).decode("utf-8").strip()
            try:
                uid = int(uid)
                msgs = conversations.get(uid, [])
                stdscr.clear()
                stdscr.addstr(0, 0, f"Conversation with {uid}", curses.A_BOLD)
                row = 2
                for msg in msgs[-10:]:
                    stdscr.addstr(row, 0, msg)
                    row += 1
                stdscr.addstr(row+1, 0, "Type reply: ")
                stdscr.refresh()
                reply = stdscr.getstr(row+1, 12).decode("utf-8").strip()
                if reply:
                    # Schedule async send safely
                    asyncio.run_coroutine_threadsafe(send_reply(uid, reply), loop)
                    conversations.setdefault(uid, []).append(f"You: {reply}")
                    stdscr.addstr(row+3, 0, "Reply scheduled. Press any key...")
                    stdscr.getch()
            except ValueError:
                stdscr.addstr(2, 0, "Invalid user ID. Press any key...")
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
    # Run curses UI in a separate thread
    asyncio.to_thread(run_curses, loop)
    await client.connect()

asyncio.run(main())
