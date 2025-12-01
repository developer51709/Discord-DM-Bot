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
import sys
from typing import List, Optional

# ---------------- Color support ----------------
try:
    from colorama import init as colorama_init, Fore, Style
    colorama_init(autoreset=True)
    COLORAMA_AVAILABLE = True
except Exception:
    COLORAMA_AVAILABLE = False
    # Fallback no-op values
    class _F:
        RESET = ""
        RED = ""
        GREEN = ""
        YELLOW = ""
        CYAN = ""
        MAGENTA = ""
        BLUE = ""
        WHITE = ""
    Fore = _F()
    Style = _F()

# ---------------- Configuration ----------------
CONFIG_FILE = "config.json"
KNOWN_USERS_FILE = "known_users.json"
CONVERSATIONS_FILE = "conversations.json"

HISTORY_FETCH_LIMIT = None
HISTORY_CONCURRENCY = 3
HISTORY_FETCH_TIMEOUT = 120

# ---------------- Logging (to stderr) ----------------
handler = logging.StreamHandler(sys.stderr)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[handler]
)
logger = logging.getLogger("dm-relay")

# ---------------- Discord client setup ----------------
intents = discord.Intents.default()
intents.messages = True
intents.dm_messages = True

client = discord.Client(intents=intents)

# ---------------- Shared state and synchronization ----------------
incoming_queue = queue.Queue()
unread_count = 0
unread_lock = threading.Lock()

conversations = {}
conversations_lock = threading.Lock()

shutdown_event = threading.Event()

bot_status = ""
bot_status_lock = threading.Lock()

bot_ready_event: Optional[asyncio.Event] = None

# ---------------- Utility: atomic save ----------------
def atomic_save(path: str, data) -> None:
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

# ---------------- Persistence ----------------
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
async def fetch_channel_history(channel: discord.DMChannel, limit: Optional[int] = HISTORY_FETCH_LIMIT):
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
    global bot_status, bot_ready_event
    with bot_status_lock:
        bot_status = f"Connected as {client.user}"
    logger.info("Bot ready: %s", client.user)
    await asyncio.to_thread(load_conversations_sync)
    if bot_ready_event is not None and not bot_ready_event.is_set():
        bot_ready_event.set()

@client.event
async def on_message(message):
    global unread_count
    if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
        incoming_queue.put((message.author, message.content))
        with unread_lock:
            unread_count += 1
        with conversations_lock:
            conversations.setdefault(message.author.id, []).append(f"{message.author}: {message.content}")
        asyncio.create_task(save_conversations())
        known = set(load_known_users())
        known.add(message.author.id)
        asyncio.create_task(save_known_users(sorted(list(known))))

# ---------------- Reload task ----------------
async def reload_all_histories(semaphore_limit: int = HISTORY_CONCURRENCY, limit_per_channel: Optional[int] = HISTORY_FETCH_LIMIT):
    logger.info("Starting reload_all_histories (limit=%s, concurrency=%s)", limit_per_channel, semaphore_limit)
    sem = asyncio.Semaphore(semaphore_limit)
    known_ids = set(load_known_users())
    for ch in client.private_channels:
        if isinstance(ch, discord.DMChannel) and ch.recipient:
            known_ids.add(ch.recipient.id)

    async def fetch_for_uid(uid: int):
        async with sem:
            try:
                user = client.get_user(uid) or await client.fetch_user(uid)
                channel = user.dm_channel or await user.create_dm()
                if channel is None:
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

    tasks = [asyncio.create_task(fetch_for_uid(uid)) for uid in sorted(known_ids)]
    results = []
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=HISTORY_FETCH_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Reload timed out; gathering partial results.")
        for t in tasks:
            if not t.done():
                t.cancel()
        done, _ = await asyncio.wait(tasks, timeout=5)
        for d in done:
            try:
                results.append(d.result())
            except Exception:
                pass

    updated = 0
    with conversations_lock:
        for uid, texts in results:
            if texts:
                conversations[uid] = texts
                updated += 1

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
        known = set(load_known_users())
        known.add(author.id)
        await save_known_users(sorted(list(known)))

    await save_conversations()
    logger.info("Reload complete: updated %d conversations, drained %d queued messages", updated, len(drained))
    return {"updated": updated, "drained": len(drained)}

# ---------------- Color helpers ----------------
def c_header(text: str):
    if COLORAMA_AVAILABLE:
        return f"{Style.BRIGHT}{Fore.CYAN}{text}{Style.RESET_ALL}"
    return text

def c_info(text: str):
    if COLORAMA_AVAILABLE:
        return f"{Fore.BLUE}{text}{Style.RESET_ALL}"
    return text

def c_success(text: str):
    if COLORAMA_AVAILABLE:
        return f"{Fore.GREEN}{text}{Style.RESET_ALL}"
    return text

def c_warn(text: str):
    if COLORAMA_AVAILABLE:
        return f"{Fore.YELLOW}{text}{Style.RESET_ALL}"
    return text

def c_error(text: str):
    if COLORAMA_AVAILABLE:
        return f"{Fore.RED}{Style.BRIGHT}{text}{Style.RESET_ALL}"
    return text

def c_prompt(text: str):
    if COLORAMA_AVAILABLE:
        return f"{Fore.MAGENTA}{text}{Style.RESET_ALL}"
    return text

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

def clear_screen():
    if os.name == "nt":
        os.system("cls")
    else:
        os.system("clear")

def print_header():
    print(c_header("=" * 50))
    print(c_header("Discord DM Relay Bot".center(50)))
    print(c_header("=" * 50))

def show_menu():
    print_header()
    with bot_status_lock:
        status = bot_status
    print(c_info(f"Status: {status}"))
    with unread_lock:
        uc = unread_count
    print(c_warn(f"Unread messages: {uc}"))
    print()
    print(c_prompt("Menu:"))
    print(c_info("1) Conversations"))
    print(c_info("2) Select Conversation"))
    print(c_info("3) New Conversation"))
    print(c_info("4) Change Token"))
    print(c_info("5) Reload Messages (Full History)"))
    print(c_info("6) GitHub Update"))
    print(c_info("7) Exit"))
    print()

def list_conversations():
    with conversations_lock:
        if not conversations:
            print(c_warn("No conversations available."))
            return
        for uid, msgs in conversations.items():
            print(c_info(f"- User {uid}: {len(msgs)} messages"))

def show_conversation(uid: int):
    with conversations_lock:
        msgs = conversations.get(uid, [])
    if not msgs:
        print(c_warn("No messages in this conversation."))
        return
    for msg in msgs[-200:]:
        print(wrap_text(msg, width=80))
        print(c_info("-" * 40))

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
        known = set(load_known_users())
        known.add(author.id)
        save_known_users_sync(sorted(list(known)))
    if drained:
        save_conversations_sync()
    return drained

def run_cli(loop: asyncio.AbstractEventLoop):
    reload_future = None
    try:
        if bot_ready_event is not None and not bot_ready_event.is_set():
            clear_screen()
            print(c_info("Waiting for bot to connect... (you can wait or press Enter to continue)"))
            bot_ready_event.wait(timeout=10)

        while not shutdown_event.is_set():
            clear_screen()
            drained = drain_incoming_queue_to_conversations()
            if drained:
                print(c_success(f"Drained {len(drained)} incoming messages into conversations."))

            show_menu()
            choice = input(c_prompt("Select option: ")).strip()

            if choice == "1":
                clear_screen()
                print(c_header("=== Conversations ==="))
                list_conversations()
                input(c_prompt("\nPress Enter to return to menu..."))

            elif choice == "2":
                uid_raw = input(c_prompt("Enter user ID: ")).strip()
                try:
                    uid = int(uid_raw)
                except ValueError:
                    print(c_error("Invalid user ID."))
                    input(c_prompt("Press Enter to continue..."))
                    continue
                clear_screen()
                print(c_header(f"=== Conversation with {uid} ==="))
                show_conversation(uid)
                reply = input(c_prompt("\nType reply (leave blank to skip): ")).strip()
                if reply:
                    fut = asyncio.run_coroutine_threadsafe(send_reply(uid, reply), loop)
                    try:
                        fut.result(timeout=20)
                        with conversations_lock:
                            conversations.setdefault(uid, []).append(f"You: {reply}")
                        asyncio.run_coroutine_threadsafe(save_conversations(), loop)
                        known = set(load_known_users())
                        known.add(uid)
                        asyncio.run_coroutine_threadsafe(save_known_users(sorted(list(known))), loop)
                        print(c_success("Reply sent."))
                    except Exception as e:
                        print(c_error(f"Failed to send reply: {e}"))
                input(c_prompt("Press Enter to return to menu..."))

            elif choice == "3":
                uid_raw = input(c_prompt("Enter user ID: ")).strip()
                try:
                    uid = int(uid_raw)
                except ValueError:
                    print(c_error("Invalid user ID."))
                    input(c_prompt("Press Enter to continue..."))
                    continue
                msg = input(c_prompt("Enter message: ")).strip()
                if not msg:
                    print(c_warn("No message entered."))
                    input(c_prompt("Press Enter to continue..."))
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
                    print(c_success("Message sent."))
                except Exception as e:
                    print(c_error(f"Failed to send message: {e}"))
                input(c_prompt("Press Enter to return to menu..."))

            elif choice == "4":
                new_token = input(c_prompt("Enter new bot token: ")).strip()
                if new_token:
                    save_token(new_token)
                    print(c_success("Token saved. Please restart the program to use the new token."))
                    input(c_prompt("Press Enter to exit..."))
                    shutdown_event.set()
                    try:
                        asyncio.run_coroutine_threadsafe(client.close(), loop)
                    except Exception:
                        pass
                    break
                else:
                    print(c_warn("No token entered."))
                    input(c_prompt("Press Enter to return to menu..."))

            elif choice == "5":
                if reload_future and not reload_future.done():
                    print(c_warn("Reload already running in background. Check logs for progress."))
                else:
                    print(c_info("Scheduling reload of full histories in background..."))
                    reload_future = asyncio.run_coroutine_threadsafe(
                        reload_all_histories(semaphore_limit=HISTORY_CONCURRENCY, limit_per_channel=HISTORY_FETCH_LIMIT),
                        loop
                    )
                    try:
                        reload_future.result(timeout=1)
                        print(c_success("Reload completed very quickly."))
                    except asyncio.TimeoutError:
                        print(c_info("Reload started; it will run in background. Check logs for progress."))
                    except Exception:
                        try:
                            res = reload_future.result(timeout=1)
                            print(c_info(f"Reload result: {res}"))
                        except Exception as e:
                            print(c_error(f"Reload failed to start: {e}"))
                input(c_prompt("Press Enter to return to menu..."))

            elif choice == "6":
                print(c_info("\nRunning git pull in ~/Discord-DM-Bot ..."))
                try:
                    result = subprocess.run(
                        ["git", "pull", "origin", "main"],
                        capture_output=True, text=True, cwd=os.path.expanduser("~/Discord-DM-Bot")
                    )
                    out = result.stdout.strip() or "(no output)"
                    err = result.stderr.strip()
                    print(c_info("\n--- git output ---"))
                    print(out)
                    if err:
                        print(c_error("\n--- git errors ---"))
                        print(err)
                    print(c_success("\nUpdate complete."))
                except Exception as e:
                    print(c_error(f"Git update failed: {e}"))
                input(c_prompt("Press Enter to return to menu..."))

            elif choice == "7":
                print(c_info("Exiting..."))
                shutdown_event.set()
                try:
                    asyncio.run_coroutine_threadsafe(client.close(), loop)
                except Exception:
                    pass
                break

            else:
                print(c_error("Invalid choice."))
                input(c_prompt("Press Enter to continue..."))

    except KeyboardInterrupt:
        logger.info("CLI interrupted by user.")
        shutdown_event.set()
        try:
            asyncio.run_coroutine_threadsafe(client.close(), loop)
        except Exception:
            pass

# ---------------- Main entrypoint ----------------
async def main():
    global bot_ready_event
    bot_ready_event = asyncio.Event()

    await asyncio.to_thread(load_conversations_sync)

    token = get_token_interactive()
    loop = asyncio.get_running_loop()

    client_task = asyncio.create_task(client.start(token))

    try:
        await asyncio.wait_for(bot_ready_event.wait(), timeout=15)
        logger.info("Bot signaled ready; starting CLI.")
    except asyncio.TimeoutError:
        logger.warning("Bot did not become ready within timeout; starting CLI anyway. Logs will appear on stderr.")

    cli_thread = threading.Thread(target=run_cli, args=(loop,), daemon=True)
    cli_thread.start()

    try:
        await client_task
    finally:
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
