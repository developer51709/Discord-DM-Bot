#!/usr/bin/env python3
import discord
import asyncio
import json
import os
import subprocess
import textwrap
import threading
import queue
import tempfile
import logging
from typing import List, Optional

# ---------------- Configuration ----------------
CONFIG_FILE = "config.json"
KNOWN_USERS_FILE = "known_users.json"
CONVERSATIONS_FILE = "conversations.json"

# Limits and throttles
HISTORY_FETCH_LIMIT = None  # None = fetch all; set to an int to limit messages per DM
HISTORY_CONCURRENCY = 3     # concurrent DM history fetches
HISTORY_FETCH_TIMEOUT = 120  # seconds for the whole reload task

# ---------------- Logging ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("dm-relay")

# ---------------- Discord client setup ----------------
intents = discord.Intents.default()
intents.messages = True
intents.dm_messages = True

client = discord.Client(intents=intents)

# ---------------- Shared state and synchronization ----------------
# Thread-safe queue for passing incoming messages from the discord event loop to the CLI thread
incoming_queue = queue.Queue()
unread_count = 0
unread_lock = threading.Lock()

# conversations persisted in memory and on disk: {user_id: ["Author: content", ...]}
conversations = {}
conversations_lock = threading.Lock()

# Shutdown coordination
shutdown_event = threading.Event()

# Bot status string (read-only for CLI)
bot_status = ""
bot_status_lock = threading.Lock()

# ---------------- Utility: atomic save ----------------
def atomic_save(path: str, data) -> None:
    """Write JSON data atomically to avoid corruption."""
    dirn = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dirn)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise

# ---------------- Persistence (thread-safe wrappers) ----------------
def load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load %s: %s", path, e)
            return default
    return default

def save_json_atomic(path: str, data):
    try:
        atomic_save(path, data)
    except Exception as e:
        logger.exception("Failed to save %s: %s", path, e)

def load_known_users() -> List[int]:
    data = load_json(KNOWN_USERS_FILE, [])
    try:
        return [int(x) for x in data]
    except Exception:
        return []

def save_known_users_sync(user_ids: List[int]):
    save_json_atomic(KNOWN_USERS_FILE, [int(x) for x in user_ids])

async def save_known_users(user_ids: List[int]):
    await asyncio.to_thread(save_known_users_sync, user_ids)

def load_conversations_sync():
    global conversations
    data = load_json(CONVERSATIONS_FILE, {})
    with conversations_lock:
        conversations = {int(k): v for k, v in data.items()}

def save_conversations_sync():
    with conversations_lock:
        save_json_atomic(CONVERSATIONS_FILE, conversations)

async def save_conversations():
    await asyncio.to_thread(save_conversations_sync)

# ---------------- Config management ----------------
def load_token() -> Optional[str]:
    cfg = load_json(CONFIG_FILE, {})
    return cfg.get("token")

def save_token(token: str):
    save_json_atomic(CONFIG_FILE, {"token": token})

def get_token_interactive() -> str:
    token = load_token()
    while not token:
        print("Missing bot token.")
        token = input("Enter a valid bot token: ").strip()
        if token:
            save_token(token)
    return token

# ---------------- Discord helpers ----------------
async def fetch_channel_history(channel: discord.DMChannel, limit: Optional[int] = HISTORY_FETCH_LIMIT) -> List[discord.Message]:
    """Fetch history for a DM channel (oldest first). Limit can be None or int."""
    msgs = []
    try:
        async for m in channel.history(limit=limit, oldest_first=True):
            msgs.append(m)
    except Exception as e:
        logger.exception("Error fetching history for channel %s: %s", getattr(channel, "id", "<dm>"), e)
    return msgs

async def send_reply(uid: int, reply: str) -> None:
    user = client.get_user(uid) or await client.fetch_user(uid)
    await user.send(reply)

# ---------------- Discord events ----------------
@client.event
async def on_ready():
    global bot_status
    with bot_status_lock:
        bot_status = f"Connected as {client.user}"
    logger.info("Bot ready: %s", client.user)
    # load persisted conversations
    await asyncio.to_thread(load_conversations_sync)

@client.event
async def on_message(message):
    global unread_count
    if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
        # Put into thread-safe queue for CLI thread to pick up
        incoming_queue.put((message.author, message.content))
        with unread_lock:
            unread_count += 1
        # Update in-memory conversations under lock
        with conversations_lock:
            conversations.setdefault(message.author.id, []).append(f"{message.author}: {message.content}")
        # Persist asynchronously (offload to thread)
        asyncio.create_task(save_conversations())
        # Persist known user asynchronously
        known = set(load_known_users())
        known.add(message.author.id)
        asyncio.create_task(save_known_users(sorted(list(known))))

# ---------------- Async reload task (concurrent, rate-friendly) ----------------
async def reload_all_histories(semaphore_limit: int = HISTORY_CONCURRENCY, limit_per_channel: Optional[int] = HISTORY_FETCH_LIMIT):
    """
    Fetch histories for known users and client.private_channels concurrently with a semaphore.
    Updates conversations under lock and persists at the end.
    """
    logger.info("Starting reload_all_histories (limit=%s, concurrency=%s)", limit_per_channel, semaphore_limit)
    sem = asyncio.Semaphore(semaphore_limit)
    known_ids = set(load_known_users())

    # Also include recipients from client.private_channels
    for ch in client.private_channels:
        if isinstance(ch, discord.DMChannel) and ch.recipient:
            known_ids.add(ch.recipient.id)

    async def fetch_for_uid(uid: int):
        async with sem:
            try:
                user = client.get_user(uid) or await client.fetch_user(uid)
                channel = user.dm_channel or await user.create_dm()
                if channel is None:
                    logger.debug("No DM channel for user %s", uid)
                    return uid, []
                history = await fetch_channel_history(channel, limit=limit_per_channel)
                texts = [f"{m.author}: {m.content}" for m in history]
                logger.info("Fetched %d messages for %s", len(texts), uid)
                return uid, texts
            except discord.HTTPException as e:
                logger.warning("HTTP error fetching history for %s: %s", uid, e)
                return uid, []
            except Exception as e:
                logger.exception("Unexpected error fetching history for %s: %s", uid, e)
                return uid, []

    # Schedule all fetches
    tasks = [asyncio.create_task(fetch_for_uid(uid)) for uid in sorted(known_ids)]
    results = []
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=HISTORY_FETCH_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Reload timed out; gathering partial results.")
        # cancel remaining tasks and gather what finished
        for t in tasks:
            if not t.done():
                t.cancel()
        done, _ = await asyncio.wait(tasks, timeout=5)
        for d in done:
            try:
                results.append(d.result())
            except Exception:
                pass

    # Update conversations under lock
    updated = 0
    with conversations_lock:
        for uid, texts in results:
            if texts:
                conversations[uid] = texts
                updated += 1

    # Drain incoming_queue into conversations as well
    drained = []
    while True:
        try:
            author, content = incoming_queue.get_nowait()
        except queue.Empty:
            break
        with unread_lock:
            global unread_count
            if unread_count > 0:
                unread_count -= 1
        with conversations_lock:
            conversations.setdefault(author.id, []).append(f"{author}: {content}")
        drained.append((author, content))
        # ensure known users persisted
        known = set(load_known_users())
        known.add(author.id)
        await save_known_users(sorted(list(known)))

    # Persist conversations
    await save_conversations()
    logger.info("Reload complete: updated %d conversations, drained %d queued messages", updated, len(drained))
    return {"updated": updated, "drained": len(drained)}

# ---------------- CLI (synchronous) ----------------
def wrap_text(text: str, width: int = 80) -> str:
    paragraphs = text.splitlines() or [""]
    wrapped = []
    for p in paragraphs:
        if not p:
            wrapped.append("")
        else:
            wrapped.extend(textwrap.wrap(p, width=width) or [""])
    return "\n".join(wrapped)

def print_header():
    print("=" * 80)
    print("Discord DM Relay Bot".center(80))
    print("=" * 80)

def show_menu():
    print_header()
    with bot_status_lock:
        status = bot_status
    print(f"Status: {status}")
    with unread_lock:
        uc = unread_count
    print(f"Unread messages: {uc}")
    print()
    print("Menu:")
    print("1) Conversations")
    print("2) Select Conversation")
    print("3) New Conversation")
    print("4) Change Token")
    print("5) Reload Messages (Full History) [runs in background]")
    print("6) GitHub Update")
    print("7) Exit")
    print()

def list_conversations():
    with conversations_lock:
        if not conversations:
            print("No conversations available.")
            return
        for uid, msgs in conversations.items():
            print(f"- User {uid}: {len(msgs)} messages")

def show_conversation(uid: int):
    with conversations_lock:
        msgs = conversations.get(uid, [])
    if not msgs:
        print("No messages in this conversation.")
        return
    for msg in msgs[-200:]:
        print(wrap_text(msg, width=80))
        print("-" * 40)

def drain_incoming_queue_to_conversations():
    drained = []
    while True:
        try:
            author, content = incoming_queue.get_nowait()
        except queue.Empty:
            break
        with unread_lock:
            global unread_count
            if unread_count > 0:
                unread_count -= 1
        with conversations_lock:
            conversations.setdefault(author.id, []).append(f"{author}: {content}")
        drained.append((author, content))
        # persist known user
        known = set(load_known_users())
        known.add(author.id)
        save_known_users_sync(sorted(list(known)))
    if drained:
        save_conversations_sync()
    return drained

def run_cli(loop: asyncio.AbstractEventLoop):
    """
    CLI runs in a separate thread. Long-running async tasks are scheduled on the asyncio loop.
    The reload operation is scheduled as a background task and returns immediately.
    """
    reload_future = None

    try:
        while not shutdown_event.is_set():
            # Drain any incoming messages first
            drained = drain_incoming_queue_to_conversations()
            if drained:
                print(f"Drained {len(drained)} incoming messages into conversations.")

            show_menu()
            choice = input("Select option: ").strip()

            if choice == "1":
                print("\n=== Conversations ===")
                list_conversations()
                input("\nPress Enter to return to menu...")

            elif choice == "2":
                uid_raw = input("Enter user ID: ").strip()
                try:
                    uid = int(uid_raw)
                except ValueError:
                    print("Invalid user ID.")
                    input("Press Enter to continue...")
                    continue
                print(f"\n=== Conversation with {uid} ===")
                show_conversation(uid)
                reply = input("\nType reply (leave blank to skip): ").strip()
                if reply:
                    fut = asyncio.run_coroutine_threadsafe(send_reply(uid, reply), loop)
                    try:
                        fut.result(timeout=20)
                        with conversations_lock:
                            conversations.setdefault(uid, []).append(f"You: {reply}")
                        # persist
                        asyncio.run_coroutine_threadsafe(save_conversations(), loop)
                        known = set(load_known_users())
                        known.add(uid)
                        asyncio.run_coroutine_threadsafe(save_known_users(sorted(list(known))), loop)
                        print("Reply sent.")
                    except Exception as e:
                        print(f"Failed to send reply: {e}")
                input("Press Enter to return to menu...")

            elif choice == "3":
                uid_raw = input("Enter user ID: ").strip()
                try:
                    uid = int(uid_raw)
                except ValueError:
                    print("Invalid user ID.")
                    input("Press Enter to continue...")
                    continue
                msg = input("Enter message: ").strip()
                if not msg:
                    print("No message entered.")
                    input("Press Enter to continue...")
                    continue
                fut = asyncio.run_coroutine_threadsafe(send_reply(uid, msg), loop)
                try:
                    fut.result(timeout=20)
                    with conversations_lock:
                        conversations.setdefault(uid, []).append(f"You: {msg}")
                    asyncio.run_coroutine_threadsafe(save_conversations(), loop)
                    known = set(load_known_users())
                    known.add(uid)
                    asyncio.run_coroutine_threadsafe(save_known_users(sorted(list(known))), loop)
                    print("Message sent.")
                except Exception as e:
                    print(f"Failed to send message: {e}")
                input("Press Enter to return to menu...")

            elif choice == "4":
                new_token = input("Enter new bot token: ").strip()
                if new_token:
                    save_token(new_token)
                    print("Token saved. Please restart the program to use the new token.")
                    input("Press Enter to exit...")
                    # signal shutdown
                    shutdown_event.set()
                    try:
                        asyncio.run_coroutine_threadsafe(client.close(), loop)
                    except Exception:
                        pass
                    break
                else:
                    print("No token entered.")
                    input("Press Enter to return to menu...")

            elif choice == "5":
                # Start reload in background if not already running
                if reload_future and not reload_future.done():
                    print("Reload already running in background. You can check logs for progress.")
                else:
                    print("Scheduling reload of full histories in background...")
                    reload_future = asyncio.run_coroutine_threadsafe(
                        reload_all_histories(semaphore_limit=HISTORY_CONCURRENCY, limit_per_channel=HISTORY_FETCH_LIMIT),
                        loop
                    )
                    # Do not block; user can continue using CLI. Optionally wait a short time to confirm it started.
                    try:
                        # quick check for immediate failure
                        reload_future.result(timeout=1)
                        print("Reload completed very quickly.")
                    except asyncio.TimeoutError:
                        print("Reload started; it will run in background. Check logs for progress.")
                    except Exception:
                        # If it raised immediately, show error
                        try:
                            res = reload_future.result(timeout=1)
                            print("Reload result:", res)
                        except Exception as e:
                            print("Reload failed to start:", e)
                input("Press Enter to return to menu...")

            elif choice == "6":
                print("\nRunning git pull in ~/Discord-DM-Bot ...")
                try:
                    result = subprocess.run(
                        ["git", "pull", "origin", "main"],
                        capture_output=True, text=True, cwd=os.path.expanduser("~/Discord-DM-Bot")
                    )
                    out = result.stdout.strip() or "(no output)"
                    err = result.stderr.strip()
                    print("\n--- git output ---")
                    print(out)
                    if err:
                        print("\n--- git errors ---")
                        print(err)
                    print("\nUpdate complete.")
                except Exception as e:
                    print(f"Git update failed: {e}")
                input("Press Enter to return to menu...")

            elif choice == "7":
                print("Exiting...")
                shutdown_event.set()
                try:
                    asyncio.run_coroutine_threadsafe(client.close(), loop)
                except Exception:
                    pass
                break

            else:
                print("Invalid choice.")
                input("Press Enter to continue...")

    except KeyboardInterrupt:
        logger.info("CLI interrupted by user.")
        shutdown_event.set()
        try:
            asyncio.run_coroutine_threadsafe(client.close(), loop)
        except Exception:
            pass

# ---------------- Main entrypoint ----------------
async def main():
    # Load persisted conversations synchronously in thread to avoid blocking event loop
    await asyncio.to_thread(load_conversations_sync)

    token = get_token_interactive()
    loop = asyncio.get_running_loop()

    # Start CLI in a separate thread
    cli_thread = threading.Thread(target=run_cli, args=(loop,), daemon=True)
    cli_thread.start()

    # Start the discord client (this will run until closed)
    try:
        await client.start(token)
    finally:
        # Signal CLI to exit and join thread
        shutdown_event.set()
        if cli_thread.is_alive():
            cli_thread.join(timeout=2)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user, shutting down.")
        try:
            asyncio.run(client.close())
        except Exception:
            pass
