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
conversations = {}  # user_id -> list of messages


# ---------------- CONFIG MANAGEMENT ----------------
def load_token():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                return data.get("token")
        except (json.JSONDecodeError, KeyError):
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
    print(f"Bot connected as {client.user}")


@client.event
async def on_message(message):
    global unread_count
    if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
        await message_queue.put((message.author, message.content))
        unread_count += 1
        conversations.setdefault(message.author.id, []).append(f"{message.author}: {message.content}")


# ---------------- TERMINAL UI ----------------
async def terminal_ui(stdscr):
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
            uid = stdscr.getstr(2, 15).decode("utf-8").strip()
            try:
                uid = int(uid)
                msgs = conversations.get(uid, [])
                stdscr.clear()
                stdscr.addstr(0, 0, f"=== Conversation with {uid} ===", curses.A_BOLD)
                row = 2
                for msg in msgs[-10:]:  # show last 10 messages
                    stdscr.addstr(row, 0, msg)
                    row += 1
                stdscr.addstr(row+1, 0, "Type reply: ")
                stdscr.refresh()
                reply = stdscr.getstr(row+1, 12).decode("utf-8").strip()
                if reply:
                    user = await client.fetch_user(uid)
                    await user.send(reply)
                    conversations.setdefault(uid, []).append(f"You: {reply}")
                    stdscr.addstr(row+3, 0, "Reply sent. Press any key...")
                    stdscr.getch()
            except ValueError:
                stdscr.addstr(4, 0, "Invalid user ID. Press any key...")
                stdscr.getch()

        elif choice == "3":
            stdscr.clear()
            stdscr.addstr(0, 0, "=== New Conversation ===", curses.A_BOLD)
            stdscr.addstr(2, 0, "Enter user ID: ")
            stdscr.refresh()
            uid = stdscr.getstr(2, 15).decode("utf-8").strip()
            user = await client.fetch_user(int(uid))
            stdscr.addstr(3, 0, "Enter message: ")
            stdscr.refresh()
            msg = stdscr.getstr(3, 15).decode("utf-8").strip()
            await user.send(msg)
            conversations.setdefault(user.id, []).append(f"You: {msg}")
            stdscr.addstr(5, 0, "Message sent. Press any key to return...")
            stdscr.getch()

        elif choice == "4":
            stdscr.clear()
            stdscr.addstr(0, 0, "=== Change Token ===", curses.A_BOLD)
            stdscr.addstr(2, 0, "Enter new bot token: ")
            stdscr.refresh()
            new_token = stdscr.getstr(2, 22).decode("utf-8").strip()
            save_token(new_token)
            stdscr.addstr(4, 0, "Token updated. Restart required. Press any key...")
            stdscr.getch()
            return

        elif choice == "5":
            # Update option: refresh unread count and show any new messages
            stdscr.clear()
            stdscr.addstr(0, 0, "=== Update ===", curses.A_BOLD)
            stdscr.addstr(2, 0, f"Unread Messages: {unread_count}")
            if unread_count > 0:
                stdscr.addstr(4, 0, "New messages available. Check Conversations or Select Conversation.")
            else:
                stdscr.addstr(4, 0, "No new messages.")
            stdscr.addstr(6, 0, "Press any key to return...")
            stdscr.getch()

        elif choice == "6":
            break

        else:
            stdscr.addstr(13, 0, "Invalid choice. Press any key...")
            stdscr.getch()


# ---------------- MAIN ----------------
async def main():
    token = await get_valid_token()
    loop = asyncio.get_event_loop()
    await asyncio.gather(
        client.connect(),
        asyncio.to_thread(curses.wrapper, lambda stdscr: loop.run_until_complete(terminal_ui(stdscr)))
    )

asyncio.run(main())
