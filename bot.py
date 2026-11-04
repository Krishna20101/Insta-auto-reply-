
"""Telegram media-copy bot.

Install requirements, then run ``python3 bot.py`` in a terminal. First run
asks for Telegram API ID/hash, phone, login code/optional 2FA, bot token, and
admin ID. The user session and settings are saved privately outside the repo.
An existing repo-root .env is read-only and is never deleted. The bot token
comes from BOT_TOKEN when available, otherwise it is entered privately.
"""

import asyncio
import getpass
import html as htmlmod
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

try:
    from pyrogram import Client, filters, enums, idle
    from pyrogram.errors import (
        FloodWait, RPCError,
    )
    from pyrogram.types import (
        Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    )
except ImportError:
    print("Install dependencies with: python -m pip install -r requirements.txt")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════
# PRIVATE SETUP — saved outside the repository
# ══════════════════════════════════════════════════════════════
CONFIG_DIR = Path.home() / ".config" / "telegram-copy-bot"
CONFIG_FILE = CONFIG_DIR / "credentials.json"
DATA = Path.home() / ".local" / "share" / "telegram-copy-bot"
DATA.mkdir(parents=True, exist_ok=True)


def _read_credentials() -> dict:
    # Read an existing repo-root .env without changing or deleting it. Real
    # environment variables (including GitHub Codespaces secrets) win.
    legacy = Path(__file__).resolve().parent / ".env"
    parsed = {}
    try:
        for line in legacy.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                parsed[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass

    for key in ("API_ID", "API_HASH", "SESSION_STRING", "PHONE", "ADMIN_IDS", "BOT_TOKEN"):
        if os.environ.get(key):
            parsed[key] = os.environ[key].strip()

    try:
        value = json.loads(CONFIG_FILE.read_text())
        if all(value.get(k) for k in ("api_id", "api_hash", "session_string")):
            # The externally saved session remains authoritative, but a token
            # supplied by .env/Codespaces does not need to be typed again.
            value["bot_token"] = parsed.get("BOT_TOKEN", "")
            return value
    except (OSError, ValueError, TypeError):
        pass

    try:
        if all(parsed.get(k) for k in ("API_ID", "API_HASH", "SESSION_STRING")):
            admin_ids = [
                int(part.strip()) for part in parsed.get("ADMIN_IDS", "").split(",")
                if part.strip().lstrip("-").isdigit()
            ]
            return {
                "api_id": int(parsed["API_ID"]),
                "api_hash": parsed["API_HASH"],
                "session_string": parsed["SESSION_STRING"],
                "phone": parsed.get("PHONE", ""),
                "admin_ids": admin_ids,
                "bot_token": parsed.get("BOT_TOKEN", ""),
            }
    except (ValueError, TypeError):
        pass
    return {}


def _save_credentials(value: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    # Never persist the bot token here; use BOT_TOKEN or enter it privately.
    stored = {k: value[k] for k in (
        "api_id", "api_hash", "phone", "session_string", "admin_ids"
    ) if k in value}
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(stored, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG_FILE)
    os.chmod(CONFIG_FILE, 0o600)


async def _login_user(api_id: int, api_hash: str, phone: str):
    client = Client(
        "copybot_login", api_id=api_id, api_hash=api_hash,
        in_memory=True, no_updates=True,
    )
    try:
        await client.connect()
        sent = await client.send_code(phone)
        code = getpass.getpass("Telegram login code: ").strip().replace(" ", "")
        try:
            user = await client.sign_in(phone, sent.phone_code_hash, code)
        except Exception as exc:
            if type(exc).__name__ != "SessionPasswordNeeded":
                raise
            password = getpass.getpass("Telegram 2FA password: ")
            user = await client.check_password(password)
        session_string = await client.export_session_string()
        if not session_string:
            raise RuntimeError("Telegram ne session export nahi kiya.")
        return session_string, user
    finally:
        if client.is_connected:
            try:
                await client.disconnect()
            except Exception:
                pass


async def _verify_user_session(value: dict):
    """Check the saved user key without starting update-handler workers."""
    client = Client(
        "copybot_session_check",
        api_id=int(value["api_id"]),
        api_hash=value["api_hash"],
        session_string=value["session_string"],
        in_memory=True,
        no_updates=True,
    )
    try:
        await client.connect()
        return await client.get_me()
    finally:
        if client.is_connected:
            await client.disconnect()


async def _terminal_setup() -> dict:
    value = _read_credentials()
    if value:
        try:
            user = await _verify_user_session(value)
            print(f"\n✅ Saved Telegram session works: {user.first_name} (ID {user.id})")
        except Exception as exc:
            print(f"\n⚠️ Saved session invalid/unavailable ({type(exc).__name__}); fresh login karte hain.")
            phone = input(f"Phone number with country code [{value.get('phone', '')}]: ").strip()
            phone = phone or value.get("phone", "")
            if not phone:
                raise ValueError("Phone number zaroori hai.")
            value["session_string"], user = await _login_user(
                int(value["api_id"]), value["api_hash"], phone
            )
            value["phone"] = phone
            _save_credentials(value)
    else:
        print("Telegram Copy Bot — first-time terminal setup\n")
        api_id = int(input("Telegram API ID: ").strip())
        api_hash = getpass.getpass("Telegram API Hash: ").strip()
        phone = input("Phone number with country code (e.g. +91...): ").strip()
        if not api_hash or not phone:
            raise ValueError("API Hash aur phone number zaroori hain.")
        session_string, user = await _login_user(api_id, api_hash, phone)
        value = {
            "api_id": api_id,
            "api_hash": api_hash,
            "phone": phone,
            "session_string": session_string,
        }
        _save_credentials(value)
        print(f"\n✅ Naya Telegram session ban gaya: {user.first_name} (ID {user.id})")

    token = os.environ.get("BOT_TOKEN", "").strip() or str(value.get("bot_token", "")).strip()
    if not token:
        token = getpass.getpass("BotFather bot token: ").strip()
    if not token:
        raise ValueError("Bot token khaali nahi ho sakta.")

    saved_admins = value.get("admin_ids") or [user.id]
    if isinstance(saved_admins, str):
        saved_admins = [part.strip() for part in saved_admins.split(",") if part.strip()]
    default_admin = ",".join(map(str, saved_admins))
    admin_text = input(f"Admin Telegram user ID(s) [{default_admin}]: ").strip()
    admin_text = admin_text or default_admin
    if not admin_text or not all(part.strip().isdigit() for part in admin_text.split(",")):
        raise ValueError("Admin ID comma-separated numeric Telegram IDs honi chahiye.")
    value["admin_ids"] = [int(part.strip()) for part in admin_text.split(",")]
    _save_credentials(value)
    # Never remove the user's .env/session. It may be their only workspace
    # backup; .gitignore keeps it out of GitHub commits.
    value["bot_token"] = token
    value["admin_ids"] = [int(x) for x in value["admin_ids"]]
    print("✅ Setup complete; starting the bot now.\n")
    return value


if __name__ == "__main__":
    try:
        CONFIG = asyncio.run(_terminal_setup())
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled.")
        raise SystemExit(1)
    except Exception as exc:
        print(f"Setup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
else:
    CONFIG = _read_credentials()

API_ID = int(CONFIG.get("api_id", 0) or 0)
API_HASH = str(CONFIG.get("api_hash", ""))
BOT_TOKEN = str(CONFIG.get("bot_token", ""))
SESSION_STRING = str(CONFIG.get("session_string", ""))
ADMIN_IDS = [int(x) for x in CONFIG.get("admin_ids", [])]


# ══════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════
SETTINGS = {
    "parallel": 5,          # continuous workers per task
    "batch_size": 10,       # queue lookahead; not a blocking batch anymore
    "max_tasks": 3,
    "global_concurrency": 10,
    "disk_reserve_gb": 1,
    "delay": 0.2,           # optional pacing after a file
    "retry_delay": 2.0,     # normal errors: 2s, then at most 3s
}

SETTINGS_FILE = DATA / "settings.json"

def load_settings():
    saved = {}
    if SETTINGS_FILE.exists():
        try:
            value = json.loads(SETTINGS_FILE.read_text())
            if isinstance(value, dict):
                saved = value
                SETTINGS.update(value)
        except Exception:
            pass

    # Migrate the old default (2 per task, 4 globally), otherwise the saved
    # settings would silently keep the new 5-worker engine capped at 2/4.
    if saved.get("parallel") == 2 and saved.get("global_concurrency", 4) == 4:
        SETTINGS["parallel"] = 5
        SETTINGS["global_concurrency"] = 10

    # Corrupt/old settings must not create unbounded workers or negative waits.
    SETTINGS["parallel"] = min(5, max(1, int(SETTINGS.get("parallel", 5))))
    SETTINGS["batch_size"] = min(100, max(SETTINGS["parallel"], int(SETTINGS.get("batch_size", 10))))
    SETTINGS["max_tasks"] = min(10, max(1, int(SETTINGS.get("max_tasks", 3))))
    SETTINGS["global_concurrency"] = min(
        50, max(SETTINGS["parallel"], int(SETTINGS.get("global_concurrency", 10)))
    )
    SETTINGS["delay"] = min(3.0, max(0.0, float(SETTINGS.get("delay", 0.2))))
    SETTINGS["retry_delay"] = min(3.0, max(2.0, float(SETTINGS.get("retry_delay", 2.0))))
    save_settings()

def save_settings():
    try:
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(SETTINGS, indent=1))
        tmp.replace(SETTINGS_FILE)
    except Exception as e:
        log.warning(f"settings save fail: {e}")

# ══════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
log = logging.getLogger("CB")

# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════
MEDIA = ("video", "document", "photo", "audio", "voice", "animation", "sticker")
WRITE_ERR = ("ChatWriteForbidden", "CHAT_WRITE_FORBIDDEN", "ChatAdminRequired",
             "CHAT_ADMIN_REQUIRED", "ChatRestricted", "ChannelPrivate", "ChatForbidden")

def esc(x) -> str:
    return htmlmod.escape(str(x if x is not None else ""), quote=False)

def hr(n) -> str:
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}PB"

def pbar(done, total, w=14) -> str:
    if not total:
        return "░" * w
    d = int(done / total * w)
    return "█" * d + "░" * (w - d)

def get_media(m):
    for a in MEDIA:
        o = getattr(m, a, None)
        if o:
            return a, o
    return None, None

def is_admin(uid: int) -> bool:
    return bool(ADMIN_IDS) and uid in ADMIN_IDS

def kb(*rows):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])

def disk_free() -> int:
    try:
        return shutil.disk_usage(str(DATA)).free
    except Exception:
        return 0

async def safe_edit(msg, text, markup=None):
    for kw in (dict(text=text, reply_markup=markup, parse_mode=enums.ParseMode.HTML),
               dict(text=text, reply_markup=markup)):
        try:
            return await msg.edit_text(**kw)
        except FloodWait as e:
            await asyncio.sleep(int(e.value) + 1)
        except Exception as e:
            nm = type(e).__name__
            if "NotModified" in nm:
                return None
            if "TooLong" in nm:
                try:
                    return await msg.edit_text(text[:3900] + "…", reply_markup=markup)
                except Exception:
                    return None
    return None

async def safe_send(client, chat_id, text, markup=None):
    for kw in (dict(parse_mode=enums.ParseMode.HTML),{}):
        try:
            return await client.send_message(
                chat_id, text, reply_markup=markup, **kw)
        except FloodWait as e:
            await asyncio.sleep(int(e.value) + 1)
        except Exception:
            pass
    return None

# ══════════════════════════════════════════════════════════════
# STATE (durable per src→dst)
# ══════════════════════════════════════════════════════════════
def _key(src, dst) -> str:
    return f"{str(src).replace('-','n')}__{str(dst).replace('-','n')}"

def state_path(src, dst) -> Path:
    return DATA / f"state_{_key(src, dst)}.json"

def state_load(src, dst) -> dict:
    st = {"src": str(src), "dst": str(dst), "files": {}, "created": time.time(), "updated": 0}
    p = state_path(src, dst)
    if p.exists():
        try:
            st.update(json.loads(p.read_text()))
        except Exception:
            pass
    return st

def state_save(st):
    st["updated"] = time.time()
    try:
        p = state_path(st["src"], st["dst"])
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1))
        tmp.replace(p)
    except Exception as e:
        log.warning(f"state save fail: {e}")

def summary(st) -> dict:
    c = {"done": 0, "pending": 0, "failed": 0}
    for v in (st.get("files") or {}).values():
        s = v.get("status", "pending")
        c[s if s in c else "pending"] += 1
    return c

# ══════════════════════════════════════════════════════════════
# CLIENTS
# ══════════════════════════════════════════════════════════════
bot = Client(
    "cb_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    workers=16,
    sleep_threshold=60,
)

_acc: Client | None = None
_acc_lock = asyncio.Lock()

async def acc_client() -> Client:
    global _acc
    async with _acc_lock:
        if _acc and _acc.is_connected:
            return _acc
        _acc = Client(
            "cb_user",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=SESSION_STRING,
            in_memory=True,
            no_updates=True,
            workers=32,
            # Surface FloodWait to the per-worker handler instead of letting
            # Pyrogram hide up to 60s of sleeping inside one transfer call.
            sleep_threshold=1,
        )
        await _acc.start()
        me = await _acc.get_me()
        log.info(f"user account: {me.first_name} ({me.id})")
        return _acc

# ══════════════════════════════════════════════════════════════
# TRANSFER ENGINE — stream-delete mode
# ══════════════════════════════════════════════════════════════
class StopTask(Exception):
    pass

async def transfer_one(acc: Client, dst, msg, mtype, mobj) -> tuple[bool, int]:
    """Download → Upload → Delete local. Returns (success, bytes_used)."""
    size = getattr(mobj, "file_size", 0) or 0
    
    # disk guard
    reserve = int(SETTINGS.get("disk_reserve_gb", 1)) * 1024**3
    free = disk_free()
    needed = int(size * 1.2) + reserve
    if free < needed:
        raise StopTask(f"disk kam hai — free {hr(free)}, chahiye {hr(needed)}")

    tmp_dir = tempfile.mkdtemp(prefix="cb_", dir=str(DATA))
    local_path = None
    try:
        ext = {
            "video": ".mp4", "audio": ".mp3", "voice": ".ogg",
            "animation": ".mp4", "photo": ".jpg", "sticker": ".webp",
        }.get(mtype, ".bin")
        fname = getattr(mobj, "file_name", None) or f"file_{msg.id}{ext}"
        local_path = os.path.join(tmp_dir, fname)

        # download
        got = await acc.download_media(msg, file_name=local_path)
        if not got or not os.path.exists(got):
            return False, 0

        # upload
        cap = msg.caption or ""
        cap_ent = msg.caption_entities or None
        kw = dict(chat_id=dst, caption=cap, caption_entities=cap_ent)

        if mtype == "video":
            await acc.send_video(
                **kw, video=got,
                duration=getattr(mobj, "duration", 0) or 0,
                width=getattr(mobj, "width", 0) or 0,
                height=getattr(mobj, "height", 0) or 0,
                supports_streaming=True,
            )
        elif mtype == "document":
            await acc.send_document(
                **kw, document=got,
                file_name=getattr(mobj, "file_name", None),
            )
        elif mtype == "photo":
            await acc.send_photo(**kw, photo=got)
        elif mtype == "audio":
            await acc.send_audio(
                **kw, audio=got,
                duration=getattr(mobj, "duration", 0) or 0,
            )
        elif mtype == "voice":
            await acc.send_voice(
                **kw, voice=got,
                duration=getattr(mobj, "duration", 0) or 0,
            )
        elif mtype == "animation":
            await acc.send_animation(**kw, animation=got)
        elif mtype == "sticker":
            await acc.send_sticker(chat_id=dst, sticker=got)
        else:
            await acc.send_document(**kw, document=got)

        return True, size

    except FloodWait:
        raise
    except Exception as e:
        if any(t in type(e).__name__ or t in str(e) for t in WRITE_ERR):
            raise
        log.warning(f"transfer_one fail mid={msg.id}: {type(e).__name__}: {e}")
        return False, 0
    finally:
        # DELETE local immediately after upload — stream-delete
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════
# TASK RUNNER
# ══════════════════════════════════════════════════════════════
TASKS: dict[int, dict] = {}
COUNTER = {"n": 0}


class DynamicLimiter:
    """Async concurrency limit whose ceiling can change between transfers."""

    def __init__(self, limit: int):
        self._limit = max(1, int(limit))
        self._active = 0
        self._condition = asyncio.Condition()

    async def set_limit(self, limit: int):
        async with self._condition:
            self._limit = max(1, int(limit))
            self._condition.notify_all()

    async def __aenter__(self):
        async with self._condition:
            await self._condition.wait_for(lambda: self._active < self._limit)
            self._active += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        async with self._condition:
            self._active -= 1
            self._condition.notify_all()


GLOBAL_SEM: DynamicLimiter  # initialized in main()
TASKS_FILE = DATA / "tasks.json"

def tasks_save():
    try:
        out = []
        for t in TASKS.values():
            out.append({k: t[k] for k in (
                "id", "src", "src_title", "dst", "dst_title",
                "want", "status", "done", "total", "failed",
                "bytes", "created", "elapsed",
            ) if k in t})
        TASKS_FILE.write_text(json.dumps(out, indent=1))
    except Exception:
        pass

def tasks_load():
    if not TASKS_FILE.exists():
        return
    try:
        for t in json.loads(TASKS_FILE.read_text()):
            if t.get("status") in ("running", "paused"):
                t["status"] = "stopped"
            t.setdefault("runner", None)
            TASKS[t["id"]] = t
        if TASKS:
            COUNTER["n"] = max(TASKS)
    except Exception:
        pass

async def run_task(tid: int):
    t = TASKS.get(tid)
    if not t:
        return
    t["status"] = "running"
    t["runner"] = asyncio.current_task()
    t["abort"] = False
    t["flood_waits"] = {}
    # Done records are replayed from durable state below. Reset counters so a
    # restart/resume cannot count already-copied files twice.
    t["done"] = 0
    t["failed"] = 0
    tasks_save()

    src = t["src"]
    dst = t["dst"]
    want = t.get("want", 0)
    st = state_load(src, dst)
    start = time.time()

    try:
        acc = await acc_client()
        parallel = min(5, max(1, int(SETTINGS.get("parallel", 5))))
        queue_size = max(parallel, int(SETTINGS.get("batch_size", 10)))
        queue = asyncio.Queue(maxsize=queue_size)
        t["active_parallel"] = parallel

        def stop_requested() -> bool:
            return bool(t.get("cancel") or t.get("abort"))

        async def wait_if_paused():
            while t.get("paused") and not stop_requested():
                await asyncio.sleep(0.2)

        async def copy_one(msg, worker_id: int):
            mid = str(msg.id)
            await wait_if_paused()
            if stop_requested():
                return

            rec = st["files"].get(mid, {})
            if rec.get("status") == "done":
                t["done"] = t.get("done", 0) + 1
                return

            mtype, mobj = get_media(msg)
            if not mtype:
                return

            attempt = 0
            while not stop_requested():
                await wait_if_paused()
                if stop_requested():
                    return
                try:
                    # Hold a global slot only for actual network work. It is
                    # released during FloodWait/retry/pacing sleeps.
                    async with GLOBAL_SEM:
                        if stop_requested():
                            return
                        ok, used = await transfer_one(acc, dst, msg, mtype, mobj)

                    if ok:
                        st["files"][mid] = {"status": "done", "ts": time.time()}
                        t["done"] = t.get("done", 0) + 1
                        t["bytes"] = t.get("bytes", 0) + used * 2
                    else:
                        st["files"][mid] = {"status": "failed"}
                        t["failed"] = t.get("failed", 0) + 1
                    state_save(st)
                    break

                except FloodWait as e:
                    # FloodWait is per worker and never consumes one of the 3
                    # normal error attempts. Other workers keep copying.
                    wait = max(1, int(getattr(e, "value", 1)) + 1)
                    t["flood_waits"][worker_id] = time.time() + wait
                    log.info(
                        f"task={tid} worker={worker_id} mid={mid} "
                        f"FloodWait={wait}s; other workers continue"
                    )
                    try:
                        await asyncio.sleep(wait)
                    finally:
                        t["flood_waits"].pop(worker_id, None)
                    continue

                except StopTask as e:
                    t["abort"] = True
                    t["status"] = f"stopped:{e}"
                    t["note"] = str(e)
                    tasks_save()
                    return

                except Exception as e:
                    if any(x in type(e).__name__ or x in str(e) for x in WRITE_ERR):
                        t["abort"] = True
                        t["status"] = "error: write forbidden on dst"
                        tasks_save()
                        return

                    attempt += 1
                    if attempt >= 3:
                        st["files"][mid] = {"status": "failed"}
                        t["failed"] = t.get("failed", 0) + 1
                        state_save(st)
                        log.warning(
                            f"task={tid} mid={mid} failed after 3 attempts: "
                            f"{type(e).__name__}: {e}"
                        )
                        break

                    # No long exponential backoff: normally 2s, then max 3s.
                    base = float(SETTINGS.get("retry_delay", 2.0))
                    retry_wait = min(3.0, max(2.0, base * attempt))
                    log.info(
                        f"task={tid} worker={worker_id} mid={mid} "
                        f"retry {attempt}/3 in {retry_wait:g}s"
                    )
                    await asyncio.sleep(retry_wait)

            t["elapsed"] = time.time() - start
            tasks_save()
            delay = float(SETTINGS.get("delay", 0.2))
            if delay > 0 and not stop_requested():
                await asyncio.sleep(delay)

        async def worker(worker_id: int):
            while True:
                msg = await queue.get()
                try:
                    if msg is None or stop_requested():
                        return
                    await copy_one(msg, worker_id)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error(f"task={tid} worker={worker_id} crashed: {e}", exc_info=True)
                    t["abort"] = True
                    t["status"] = f"error: {e}"
                    return
                finally:
                    queue.task_done()

        async def enqueue(msg) -> bool:
            # A full queue normally applies backpressure. The short timeout
            # also lets cancellation/abort break out instead of deadlocking.
            while not stop_requested():
                try:
                    queue.put_nowait(msg)
                    return True
                except asyncio.QueueFull:
                    await asyncio.sleep(0.05)
            return False

        async def history():
            """Resume history after a scan FloodWait without duplicating items."""
            offset_id = 0
            while not stop_requested():
                try:
                    async for m in acc.get_chat_history(src, offset_id=offset_id):
                        mid = int(getattr(m, "id", 0) or 0)
                        if mid and mid == offset_id:
                            continue
                        if mid:
                            offset_id = mid
                        yield m
                    return
                except FloodWait as e:
                    wait = max(1, int(getattr(e, "value", 1)) + 1)
                    t["flood_waits"]["scanner"] = time.time() + wait
                    log.info(f"task={tid} history FloodWait={wait}s; queued copies continue")
                    try:
                        await asyncio.sleep(wait)
                    finally:
                        t["flood_waits"].pop("scanner", None)

        async def produce():
            if want:
                # Keep exact totals for a requested count, then feed the same
                # continuous queue instead of waiting on fixed batches.
                found = []
                t["scanning"] = True
                async for m in history():
                    if stop_requested():
                        break
                    if not m or getattr(m, "empty", False) or not get_media(m)[0]:
                        continue
                    found.append(m)
                    if len(found) >= want:
                        break
                found.sort(key=lambda m: m.id)
                t["total"] = len(found)
                t["scanning"] = False
                tasks_save()
                for msg in found:
                    if not await enqueue(msg):
                        break
            else:
                # Scan and copy simultaneously; a full queue, not a completed
                # batch, is the only thing that briefly pauses the scanner.
                t["scanning"] = True
                t["total"] = 0
                async for m in history():
                    if stop_requested():
                        break
                    if not m or getattr(m, "empty", False) or not get_media(m)[0]:
                        continue
                    if not await enqueue(m):
                        break
                    t["total"] = t.get("total", 0) + 1
                t["scanning"] = False
                tasks_save()

        workers = [asyncio.create_task(worker(i + 1)) for i in range(parallel)]
        try:
            await produce()
            if stop_requested():
                for item in workers:
                    item.cancel()
            else:
                for _ in workers:
                    await queue.put(None)
            await asyncio.gather(*workers)
        finally:
            t["scanning"] = False
            for item in workers:
                if not item.done():
                    item.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            t["flood_waits"].clear()
            state_save(st)

        t["elapsed"] = time.time() - start
        if not stop_requested() and not str(t.get("status", "")).startswith(("error", "stopped")):
            t["status"] = "done"
        tasks_save()

    except asyncio.CancelledError:
        t["status"] = "cancelled"
        t["scanning"] = False
        tasks_save()
    except Exception as e:
        log.error(f"task {tid} crashed: {e}", exc_info=True)
        t["status"] = f"error: {e}"
        t["scanning"] = False
        tasks_save()

def start_task(tid: int):
    t = TASKS.get(tid)
    if not t:
        return
    if t.get("runner") and not t["runner"].done():
        return
    t["runner"] = asyncio.ensure_future(run_task(tid))

# ══════════════════════════════════════════════════════════════
# CONVERSATION STATE (per user)
# ══════════════════════════════════════════════════════════════
CONV: dict[int, dict] = {}

def conv_set(uid: int, **kw):
    if uid not in CONV:
        CONV[uid] = {}
    CONV[uid].update(kw)

def conv_get(uid: int) -> dict:
    return CONV.get(uid, {})

def conv_clear(uid: int):
    CONV.pop(uid, None)

# ══════════════════════════════════════════════════════════════
# MAIN MENU
# ══════════════════════════════════════════════════════════════
MAIN_KB = kb(
    [("➕ New Task", "menu_new"), ("📋 Tasks", "menu_tasks")],
    [("📊 Progress", "menu_progress"), ("⚙️ Settings", "menu_settings")],
    [("🩺 Diagnostics", "diag")],
)

def main_menu_text() -> str:
    running = sum(1 for t in TASKS.values() if t.get("status") == "running")
    total_done = sum(t.get("done", 0) for t in TASKS.values())
    return (
        f"🤖 <b>Copy Bot</b> — online\n\n"
        f"⚡ Running tasks: <b>{running}</b>\n"
        f"✅ Total copied: <b>{total_done}</b>\n"
        f"💾 Disk free: <b>{hr(disk_free())}</b>\n\n"
        f"<i>Restricted channel bhi support karta hai — direct download+upload mode</i>"
    )

@bot.on_message(filters.command("start") & filters.private)
async def cmd_start(c: Client, m: Message):
    if not is_admin(m.from_user.id):
        return
    conv_clear(m.from_user.id)
    await m.reply_text(main_menu_text(), reply_markup=MAIN_KB,
                       parse_mode=enums.ParseMode.HTML)

@bot.on_callback_query(filters.regex("^menu$"))
async def cb_menu(c: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        return
    conv_clear(q.from_user.id)
    await safe_edit(q.message, main_menu_text(), MAIN_KB)

# ══════════════════════════════════════════════════════════════
# NEW TASK FLOW
# ══════════════════════════════════════════════════════════════
@bot.on_callback_query(filters.regex("^menu_new$"))
async def cb_new(c: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        return
    conv_set(q.from_user.id, step="await_src")
    await safe_edit(
        q.message,
        "📤 <b>Source</b> — source channel/group ka ID ya username do\n\n"
        "<code>-100xxxxxxxxxx</code> ya <code>@username</code>",
        kb([("❌ Cancel", "menu")]),
    )

@bot.on_callback_query(filters.regex("^menu_tasks$"))
async def cb_tasks(c: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        return
    if not TASKS:
        await safe_edit(q.message, "📋 Koi task nahi. ➕ New Task se banao.",
                        kb([("➕ New Task", "menu_new"), ("◀ Menu", "menu")]))
        return
    lines = ["📋 <b>Tasks</b>\n"]
    for t in sorted(TASKS.values(), key=lambda x: -x["id"])[:10]:
        icon = {"running":"⚡","paused":"⏸","done":"✅","cancelled":"❌",
                "stopped":"⏹","error":"⚠️"}.get(
            (t.get("status") or "").split(":")[0], "🔄")
        total = t.get("total", 0)
        total_text = f"~{total}" if t.get("scanning") else str(total)
        lines.append(
            f"{icon} <b>#{t['id']}</b> — {esc(t.get('src_title', t['src']))} → "
            f"{esc(t.get('dst_title', t.get('dst','?')))}\n"
            f"   {t.get('done',0)}/{total_text} | {t.get('status','?')}"
        )
    rows = [[("🔍 Detail", f"t_detail_{t['id']}")] for t in
            sorted(TASKS.values(), key=lambda x: -x["id"])[:5]]
    rows.append([("🔄 Refresh", "menu_tasks"), ("◀ Menu", "menu")])
    await safe_edit(q.message, "\n".join(lines), kb(*rows))

@bot.on_callback_query(filters.regex(r"^t_detail_(\d+)$"))
async def cb_task_detail(c: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        return
    tid = int(q.matches[0].group(1))
    t = TASKS.get(tid)
    if not t:
        await q.answer("Task nahi mila", show_alert=True)
        return
    await safe_edit(q.message, _task_text(t), _task_kb(t))

def _task_text(t) -> str:
    total_count = t.get("total", 0)
    total = total_count or 1
    total_text = f"~{total_count}" if t.get("scanning") else str(total_count)
    done = t.get("done", 0)
    bar = pbar(done, total)
    pct = done * 100 // total if total_count else 0
    elapsed = t.get("elapsed", 0)
    speed = ""
    if elapsed > 0 and t.get("bytes", 0):
        bps = t["bytes"] / elapsed
        speed = f" | {hr(bps)}/s"
    icon = {"running":"⚡","paused":"⏸","done":"✅","cancelled":"❌",
            "stopped":"⏹","error":"⚠️"}.get(
        (t.get("status") or "").split(":")[0], "🔄")
    txt = (
        f"{icon} <b>Task #{t['id']}</b>\n\n"
        f"📤 {esc(t.get('src_title', t.get('src','?')))}\n"
        f"📥 {esc(t.get('dst_title', t.get('dst','?')))}\n\n"
        f"[{bar}] {done}/{total_text} ({pct}%)\n"
        f"✅ Done: {done} | ❌ Failed: {t.get('failed',0)}\n"
        f"📶 Data: {hr(t.get('bytes',0))}{speed}\n"
        f"⏱ {elapsed/60:.1f} min\n"
        f"⚡ Workers: <b>{t.get('active_parallel', SETTINGS['parallel'])}</b>\n"
        f"Status: <b>{esc(t.get('status','?'))}</b>"
    )
    flood_left = [
        max(0, int(until - time.time() + 0.999))
        for until in (t.get("flood_waits") or {}).values()
    ]
    if flood_left:
        txt += (
            f"\n🌊 FloodWait: {max(flood_left)}s — affected worker wait kar raha hai; "
            "baaki workers active hain."
        )
    if t.get("note"):
        txt += f"\n\n⚠️ {esc(t['note'])[:300]}"
    return txt

def _task_kb(t):
    tid = t["id"]
    st = t.get("status", "")
    rows = []
    if st == "running":
        rows.append([("⏸ Pause", f"t_pause_{tid}"), ("❌ Cancel", f"t_cancel_{tid}")])
    elif st in ("paused", "stopped", "cancelled", "error") or st.startswith("stopped"):
        rows.append([("▶️ Resume", f"t_resume_{tid}"), ("🗑 Delete", f"t_del_{tid}")])
    elif st == "done":
        rows.append([("🗑 Delete", f"t_del_{tid}")])
    if t.get("failed"):
        rows.append([("🔁 Retry failed", f"t_retry_{tid}")])
    rows.append([("🔄 Refresh", f"t_detail_{tid}"), ("◀ Tasks", "menu_tasks")])
    return kb(*rows)

@bot.on_callback_query(filters.regex(r"^t_(pause|resume|cancel|del|retry)_(\d+)$"))
async def cb_taction(c: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        return
    act = q.matches[0].group(1)
    tid = int(q.matches[0].group(2))
    t = TASKS.get(tid)
    if not t:
        await q.answer("Task nahi mila", show_alert=True)
        return

    if act == "pause":
        t["paused"] = True
        t["status"] = "paused"
    elif act == "resume":
        t["paused"] = False
        t["cancel"] = False
        if not t.get("runner") or t["runner"].done():
            start_task(tid)
        else:
            t["status"] = "running"
    elif act == "cancel":
        t["cancel"] = True
        t["status"] = "cancelled"
        if t.get("runner") and not t["runner"].done():
            t["runner"].cancel()
    elif act == "del":
        t["cancel"] = True
        if t.get("runner") and not t["runner"].done():
            t["runner"].cancel()
        TASKS.pop(tid, None)
        tasks_save()
        await cb_tasks(c, q)
        return
    elif act == "retry":
        st = state_load(t["src"], t["dst"])
        n = 0
        for v in st["files"].values():
            if v.get("status") == "failed":
                v["status"] = "pending"
                n += 1
        state_save(st)
        t["failed"] = 0
        t["cancel"] = False
        start_task(tid)
        await q.answer(f"{n} files retry queue mein", show_alert=True)

    tasks_save()
    await safe_edit(q.message, _task_text(TASKS.get(tid) or t),
                    _task_kb(TASKS.get(tid) or t))

# ══════════════════════════════════════════════════════════════
# PROGRESS
# ══════════════════════════════════════════════════════════════
@bot.on_callback_query(filters.regex("^menu_progress$"))
async def cb_progress(c: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        return
    if not TASKS:
        await safe_edit(q.message, "📊 Koi task nahi.",
                        kb([("◀ Menu", "menu")]))
        return
    lines = ["📊 <b>Live Progress</b>\n"]
    for t in sorted(TASKS.values(), key=lambda x: -x["id"])[:8]:
        total = t.get("total") or 0
        done = t.get("done", 0)
        total_text = f"~{total}" if t.get("scanning") else str(total)
        bar = pbar(done, total)
        pct = done * 100 // total if total else 0
        elapsed = t.get("elapsed", 0)
        eta = ""
        if not t.get("scanning") and done > 0 and total > done and elapsed > 0:
            rate = done / elapsed
            remaining = (total - done) / rate
            eta = f" | ETA {remaining/60:.0f}m"
        lines.append(
            f"<b>#{t['id']}</b> [{bar}] {pct}% {done}/{total_text}  {t.get('status','')}\n"
            f"   ✅{done} ❌{t.get('failed',0)} 📶{hr(t.get('bytes',0))}{eta}"
        )
    await safe_edit(q.message, "\n".join(lines),
                    kb([("🔄 Refresh", "menu_progress")], [("◀ Menu", "menu")]))

# ══════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════
@bot.on_callback_query(filters.regex("^menu_settings$"))
async def cb_settings(c: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        return
    txt = (
        f"⚙️ <b>Settings</b>\n\n"
        f"⚡ parallel workers (per task): <b>{SETTINGS['parallel']}</b>\n"
        f"📦 queue size: <b>{SETTINGS['batch_size']}</b>\n"
        f"🧵 max tasks: <b>{SETTINGS['max_tasks']}</b>\n"
        f"🌐 global parallel: <b>{SETTINGS['global_concurrency']}</b>\n"
        f"💾 disk reserve: <b>{SETTINGS['disk_reserve_gb']}GB</b> "
        f"(free now: {hr(disk_free())})\n"
        f"⏳ normal gap: <b>{SETTINGS['delay']}s</b> | retry: <b>2–3s</b>\n\n"
        f"<i>Continuous queue ON — slow file/FloodWait poora batch nahi rokta. "
        f"FloodWait wala worker poora wait karta hai, baaki kaam karte hain.</i>\n"
        f"<i>Stream-delete ON — upload ke baad local file turant delete.</i>\n"
        f"<i>Parallel change naye ya resumed task par apply hoga.</i>"
    )
    await safe_edit(q.message, txt, kb(
        [("⚡ par=1", "s_par_1"), ("⚡ par=2", "s_par_2"), ("⚡ par=3", "s_par_3")],
        [("⚡ par=4", "s_par_4"), ("⚡ par=5", "s_par_5")],
        [("📦 queue=5", "s_bat_5"), ("📦 queue=10", "s_bat_10"), ("📦 queue=20", "s_bat_20")],
        [("🧵 tasks=2", "s_tsk_2"), ("🧵 tasks=3", "s_tsk_3")],
        [("⏳ gap=0", "s_dly_0"), ("⏳ gap=0.2", "s_dly_2"),
         ("⏳ gap=2", "s_dly_20"), ("⏳ gap=3", "s_dly_30")],
        [("◀ Menu", "menu")],
    ))

@bot.on_callback_query(filters.regex(r"^s_(par|bat|tsk|dly)_(\d+)$"))
async def cb_setting(c: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        return
    kind = q.matches[0].group(1)
    val = int(q.matches[0].group(2))
    if kind == "par":
        SETTINGS["parallel"] = min(5, max(1, val))
        SETTINGS["global_concurrency"] = max(
            SETTINGS["parallel"], int(SETTINGS.get("global_concurrency", 10))
        )
        if "GLOBAL_SEM" in globals():
            await GLOBAL_SEM.set_limit(SETTINGS["global_concurrency"])
    elif kind == "bat":
        SETTINGS["batch_size"] = max(int(SETTINGS["parallel"]), val)
    elif kind == "tsk":
        SETTINGS["max_tasks"] = val
    elif kind == "dly":
        SETTINGS["delay"] = val / 10.0
    save_settings()
    await cb_settings(c, q)

# ══════════════════════════════════════════════════════════════
# DIAGNOSTICS
# ══════════════════════════════════════════════════════════════
@bot.on_callback_query(filters.regex("^diag$"))
async def cb_diag(c: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        return
    await safe_edit(q.message, "🩺 Test chal raha hai...", kb([("◀ Menu", "menu")]))
    lines = []
    ok_all = True

    def add(s, name, detail=""):
        nonlocal ok_all
        lines.append(f"{s} <b>{esc(name)}</b>"
                     + (f"\n   <code>{esc(detail)[:200]}</code>" if detail else ""))
        if s.startswith("❌"):
            ok_all = False

    miss = [k for k, v in [("API_ID", API_ID), ("API_HASH", API_HASH),
                            ("BOT_TOKEN", BOT_TOKEN), ("SESSION_STRING", SESSION_STRING)] if not v]
    add("✅" if not miss else "❌", "Env vars",
        "sab theek" if not miss else f"missing: {', '.join(miss)}")
    add("ℹ️", "Admin IDs", str(ADMIN_IDS) or "empty = sab allowed")

    try:
        me = await bot.get_me()
        add("✅", "Bot", f"@{me.username} (id {me.id})")
    except Exception as e:
        add("❌", "Bot", str(e))

    try:
        acc = await acc_client()
        me = await acc.get_me()
        add("✅", "User account", f"{me.first_name} (id {me.id})")
        try:
            tm = await acc.send_message("me", "🩺 test — delete kar do")
            await tm.delete()
            add("✅", "Saved Messages write", "OK")
        except Exception as e:
            add("⚠️", "Saved Messages write", str(e))
    except Exception as e:
        add("❌", "User account", f"{e} — SESSION_STRING check karo")

    free = disk_free()
    add("✅" if free > 2 * 1024**3 else "⚠️", "Disk free", hr(free))
    add("ℹ️", "Settings",
        f"parallel={SETTINGS['parallel']} batch={SETTINGS['batch_size']} "
        f"max_tasks={SETTINGS['max_tasks']} delay={SETTINGS['delay']}s")
    add("ℹ️", "Stream-delete", "ON — upload ke baad turant local delete")

    await safe_edit(
        q.message,
        "🩺 <b>Diagnostics</b>\n\n" + "\n".join(lines)
        + "\n\n" + ("✅ Sab theek — ready to go!" if ok_all else "❌ Kuch fix karo"),
        kb([("🔄 Retest", "diag")], [("◀ Menu", "menu")]),
    )

# ══════════════════════════════════════════════════════════════
# TEXT MESSAGE HANDLER — conversation flow
# ══════════════════════════════════════════════════════════════
@bot.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def on_text(c: Client, m: Message):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    state = conv_get(uid)
    step = state.get("step")

    if not step:
        await m.reply_text(
            "Use /start to open the menu.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    text = (m.text or "").strip()

    # ── STEP 1: source ──────────────────────────────────────
    if step == "await_src":
        try:
            acc = await acc_client()
            src_id = int(text) if text.lstrip("-").isdigit() else text
            chat = await acc.get_chat(src_id)
            title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(src_id)
            conv_set(uid, step="await_dst", src=chat.id, src_title=title)
            await m.reply_text(
                f"✅ Source: <b>{esc(title)}</b> (<code>{chat.id}</code>)\n\n"
                f"📥 <b>Target</b> — destination channel/group ka ID do\n"
                f"<i>Woh channel jahan files copy hongi</i>",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=kb([("❌ Cancel", "menu")]),
            )
        except Exception as e:
            await m.reply_text(
                f"❌ Source nahi mila: <code>{esc(e)}</code>\n\n"
                f"Dobara try karo ya channel ID check karo.",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=kb([("❌ Cancel", "menu")]),
            )

    # ── STEP 2: destination ─────────────────────────────────
    elif step == "await_dst":
        try:
            acc = await acc_client()
            dst_id = int(text) if text.lstrip("-").isdigit() else text
            chat = await acc.get_chat(dst_id)
            title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(dst_id)
            conv_set(uid, step="await_count", dst=chat.id, dst_title=title)
            await m.reply_text(
                f"✅ Target: <b>{esc(title)}</b> (<code>{chat.id}</code>)\n\n"
                f"🔢 <b>Kitni files?</b>\n"
                f"• Number dalo (e.g. <code>50</code>) — pehli N files\n"
                f"• <code>all</code> — sab files (1000+ bhi)",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=kb([("All files", "conv_all"), ("❌ Cancel", "menu")]),
            )
        except Exception as e:
            await m.reply_text(
                f"❌ Target nahi mila: <code>{esc(e)}</code>",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=kb([("❌ Cancel", "menu")]),
            )

    # ── STEP 3: count ───────────────────────────────────────
    elif step == "await_count":
        want = 0
        if text.lower() != "all":
            try:
                want = int(text)
                if want <= 0:
                    raise ValueError
            except ValueError:
                await m.reply_text(
                    "❌ Number ya 'all' dalo",
                    reply_markup=kb([("All files", "conv_all"), ("❌ Cancel", "menu")]),
                )
                return
        await _confirm_task(c, m, uid, want)

    else:
        conv_clear(uid)
        await m.reply_text("Use /start", parse_mode=enums.ParseMode.HTML)

async def _confirm_task(c, m_or_q, uid, want):
    state = conv_get(uid)
    src = state["src"]
    dst = state["dst"]
    src_title = state.get("src_title", str(src))
    dst_title = state.get("dst_title", str(dst))
    count_txt = str(want) if want else "All"

    running = sum(1 for t in TASKS.values() if t.get("status") == "running")
    max_t = int(SETTINGS.get("max_tasks", 3))
    warn = f"\n\n⚠️ <i>{running} task(s) already running</i>" if running >= max_t else ""

    txt = (
        f"📋 <b>Confirm Task</b>{warn}\n\n"
        f"📤 Source: <b>{esc(src_title)}</b>\n"
        f"📥 Target: <b>{esc(dst_title)}</b>\n"
        f"🔢 Files: <b>{count_txt}</b>\n"
        f"⚡ Parallel: <b>{SETTINGS['parallel']}</b>\n"
        f"💾 Mode: <b>stream-delete</b> (upload ke baad local delete)\n\n"
        f"Start karo?"
    )

    confirm_kb = kb(
        [("✅ Start", "conv_start"), ("❌ Cancel", "menu")],
    )
    conv_set(uid, step="confirm", want=want)

    if isinstance(m_or_q, Message):
        await m_or_q.reply_text(txt, parse_mode=enums.ParseMode.HTML, reply_markup=confirm_kb)
    else:
        await safe_edit(m_or_q.message, txt, confirm_kb)

@bot.on_callback_query(filters.regex("^conv_all$"))
async def cb_conv_all(c: Client, q: CallbackQuery):
    uid = q.from_user.id
    if not is_admin(uid):
        return
    state = conv_get(uid)
    if state.get("step") != "await_count":
        return
    conv_set(uid, want=0)
    await _confirm_task(c, q, uid, 0)

@bot.on_callback_query(filters.regex("^conv_start$"))
async def cb_conv_start(c: Client, q: CallbackQuery):
    uid = q.from_user.id
    if not is_admin(uid):
        return
    state = conv_get(uid)
    if state.get("step") != "confirm":
        return

    src = state["src"]
    dst = state["dst"]
    want = state.get("want", 0)

    COUNTER["n"] += 1
    tid = COUNTER["n"]
    TASKS[tid] = {
        "id": tid,
        "src": src,
        "src_title": state.get("src_title", str(src)),
        "dst": dst,
        "dst_title": state.get("dst_title", str(dst)),
        "want": want,
        "status": "queued",
        "done": 0,
        "total": 0,
        "failed": 0,
        "bytes": 0,
        "created": time.time(),
        "elapsed": 0,
        "runner": None,
        "note": "",
    }
    tasks_save()
    conv_clear(uid)

    start_task(tid)

    await safe_edit(
        q.message,
        f"⚡ <b>Task #{tid} started!</b>\n\n"
        f"📤 {esc(state.get('src_title', str(src)))}\n"
        f"📥 {esc(state.get('dst_title', str(dst)))}\n"
        f"🔢 {want or 'All files'}\n\n"
        f"<i>Scanning aur copying shuru...</i>",
        kb([("📊 Progress", "menu_progress"), ("📋 Tasks", "menu_tasks"), ("◀ Menu", "menu")]),
    )

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
async def main():
    global GLOBAL_SEM

    load_settings()
    GLOBAL_SEM = DynamicLimiter(int(SETTINGS.get("global_concurrency", 10)))

    if not all([API_ID, API_HASH, BOT_TOKEN, SESSION_STRING, ADMIN_IDS]):
        missing = [k for k, v in (
            ("API_ID", API_ID), ("API_HASH", API_HASH),
            ("BOT_TOKEN", BOT_TOKEN), ("SESSION_STRING", SESSION_STRING),
            ("ADMIN_IDS", ADMIN_IDS),
        ) if not v]
        log.error("❌ Missing credentials: " + ", ".join(missing))
        log.error("Run `python3 bot.py` in a terminal to configure Telegram login.")
        return

    tasks_load()

    # start user client early
    try:
        await acc_client()
    except Exception as e:
        log.error(f"user account fail: {e}")

    await bot.start()
    me = await bot.get_me()
    log.info(f"✅ Bot @{me.username} ready | admins={ADMIN_IDS or 'all'}")

    # notify admins
    for uid in ADMIN_IDS[:3]:
        try:
            await bot.send_message(
                uid,
                "🤖 <b>Copy Bot online!</b>\n\n"
                "/start dabao — menu aayega\n"
                f"💾 Disk: {hr(disk_free())} | Stream-delete: ON",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

    await idle()
    await bot.stop()

if __name__ == "__main__":
    # Pyrogram clients and dispatcher workers use the Client's event loop.
    # Running main() via asyncio.run() creates a second loop and can make
    # bot.stop() fail with "Future attached to a different loop".
    bot.run(main())
