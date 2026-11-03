
"""
╔══════════════════════════════════════════════════════════════════════════╗
║  COPY BOT v2 — GitHub Codespaces / Render ready                         ║
║  Stream-delete mode: upload hote hi local file delete                   ║
║  Restricted channel support: seedha download+upload                     ║
╚══════════════════════════════════════════════════════════════════════════╝

ENV — .env file ya seedha neeche HARD_* variables mein bhar do:
    API_ID, API_HASH, BOT_TOKEN, SESSION_STRING, ADMIN_IDS
"""

import asyncio
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
    print("pip install pyrofork aiohttp")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════
# .env loader
# ══════════════════════════════════════════════════════════════
def _load_dotenv():
    here = Path(__file__).resolve().parent
    for name in (".env", "data/.env"):
        f = here / name
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and not os.environ.get(k):
                os.environ[k] = v

_load_dotenv()

# ══════════════════════════════════════════════════════════════
# CONFIG — yahan apni values bhar do
# ══════════════════════════════════════════════════════════════
HARD_API_ID       = 36349735       # e.g. 12345678
HARD_API_HASH     = "373122633b75d964ca4a168c7b79e683"       # "abcdef1234567890abcdef1234567890"
HARD_BOT_TOKEN    = "9117075404:AAHcuv5NdbiXDry0pvUflFl67Y0h6wffDXo"       # "123456789:AAxxxx"
HARD_SESSION_STRING = "BQIqpycAu2ieCeU0pGV1tsfNvV636oWKCSU80g26idsK4q3Uwq1CycTsRc21AUHKgUfUdvQkpzYIvSELnOxTE10akDqM9oiWB486DK9zSYKGh1ncXJaIuvqdHbkKOdYBKUAah6WYSsRrazFhtj9fs4wyyHvZeY-1CFtpjwaboj8_2hBRawfWD3RzmoEAteahQnaDmKblZHXBWO_aRoOQVA590G6jBIiclkrPX6fiMKV5-3_ObK-fCiMYwol7meHff_BW51sWownkBr3JIgGRWTvdY_Z4E02oCn6tslxtp9cLhEaKOxrzn7i1O12dLkpzTCCrONugdeDlIb9_8yF_xVDjTI3GbAAAAAGspGeKAA"     # "BQxxxx..."  (Pyrogram session)
HARD_ADMIN_IDS    = ""       # "7191422858" ya "123456789,987654321"

API_ID   = int(os.environ.get("API_ID", "0") or 0) or int(HARD_API_ID or 0)
API_HASH = (os.environ.get("API_HASH", "") or HARD_API_HASH or "").strip()
BOT_TOKEN = (os.environ.get("BOT_TOKEN", "") or HARD_BOT_TOKEN or "").strip()
SESSION_STRING = (os.environ.get("SESSION_STRING", "") or HARD_SESSION_STRING or "").strip()
_ids = os.environ.get("ADMIN_IDS", "") or HARD_ADMIN_IDS or ""
ADMIN_IDS = [int(x) for x in _ids.replace(" ", "").split(",") if x.lstrip("-").isdigit()]

DATA = Path(__file__).resolve().parent / "data"
DATA.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════
SETTINGS = {
    "parallel": 2,          # ek task mein ek saath kitni files
    "batch_size": 5,
    "max_tasks": 3,
    "global_concurrency": 4,
    "disk_reserve_gb": 1,
    "delay": 0.3,
}

SETTINGS_FILE = DATA / "settings.json"

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            SETTINGS.update(json.loads(SETTINGS_FILE.read_text()))
        except Exception:
            pass

def save_settings():
    try:
        SETTINGS_FILE.write_text(json.dumps(SETTINGS, indent=1))
    except Exception:
        pass

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
    return not ADMIN_IDS or uid in ADMIN_IDS

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
            sleep_threshold=60,
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
GLOBAL_SEM: asyncio.Semaphore  # initialized in main()
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
    tasks_save()

    src = t["src"]
    dst = t["dst"]
    want = t.get("want", 0)
    st = state_load(src, dst)
    start = time.time()

    try:
        acc = await acc_client()

        # scan messages
        found = []
        async for m in acc.get_chat_history(src):
            if not m or getattr(m, "empty", False):
                continue
            mtype, mobj = get_media(m)
            if not mtype:
                continue
            found.append(m)
            if want and len(found) >= want:
                break
        found.sort(key=lambda m: m.id)
        t["total"] = len(found)
        tasks_save()

        sem = asyncio.Semaphore(int(SETTINGS.get("parallel", 2)))

        async def copy_one(msg):
            mid = str(msg.id)
            rec = st["files"].get(mid, {})
            if rec.get("status") == "done":
                t["done"] = t.get("done", 0) + 1
                return

            if t.get("cancel") or t.get("paused"):
                return

            async with sem:
                async with GLOBAL_SEM:
                    if t.get("cancel"):
                        return
                    while t.get("paused"):
                        await asyncio.sleep(1)
                    if t.get("cancel"):
                        return

                    mtype, mobj = get_media(msg)
                    if not mtype:
                        return

                    for attempt in range(3):
                        try:
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
                            wait = int(e.value) + 2
                            log.info(f"FloodWait {wait}s")
                            await asyncio.sleep(wait)
                        except StopTask as e:
                            t["status"] = f"stopped:{e}"
                            t["note"] = str(e)
                            tasks_save()
                            return
                        except Exception as e:
                            if any(x in type(e).__name__ or x in str(e) for x in WRITE_ERR):
                                t["status"] = f"error: write forbidden on dst"
                                tasks_save()
                                return
                            if attempt == 2:
                                st["files"][mid] = {"status": "failed"}
                                t["failed"] = t.get("failed", 0) + 1
                                state_save(st)
                            await asyncio.sleep(2 ** attempt)

                    t["elapsed"] = time.time() - start
                    tasks_save()
                    await asyncio.sleep(float(SETTINGS.get("delay", 0.3)))

        # batch processing with periodic state flush
        batch = int(SETTINGS.get("batch_size", 5))
        for i in range(0, len(found), batch):
            if t.get("cancel"):
                break
            chunk = found[i:i + batch]
            await asyncio.gather(*[copy_one(m) for m in chunk])
            state_save(st)
            tasks_save()

        if not t.get("cancel"):
            t["status"] = "done"
        tasks_save()

    except asyncio.CancelledError:
        t["status"] = "cancelled"
        tasks_save()
    except Exception as e:
        log.error(f"task {tid} crashed: {e}", exc_info=True)
        t["status"] = f"error: {e}"
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
        lines.append(
            f"{icon} <b>#{t['id']}</b> — {esc(t.get('src_title', t['src']))} → "
            f"{esc(t.get('dst_title', t.get('dst','?')))}\n"
            f"   {t.get('done',0)}/{t.get('total',0)} | {t.get('status','?')}"
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
    total = t.get("total") or 1
    done = t.get("done", 0)
    bar = pbar(done, total)
    pct = done * 100 // total if total else 0
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
        f"[{bar}] {done}/{t.get('total',0)} ({pct}%)\n"
        f"✅ Done: {done} | ❌ Failed: {t.get('failed',0)}\n"
        f"📶 Data: {hr(t.get('bytes',0))}{speed}\n"
        f"⏱ {elapsed/60:.1f} min\n"
        f"Status: <b>{esc(t.get('status','?'))}</b>"
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
        bar = pbar(done, total)
        pct = done * 100 // total if total else 0
        elapsed = t.get("elapsed", 0)
        eta = ""
        if done > 0 and total > done and elapsed > 0:
            rate = done / elapsed
            remaining = (total - done) / rate
            eta = f" | ETA {remaining/60:.0f}m"
        lines.append(
            f"<b>#{t['id']}</b> [{bar}] {pct}%  {t.get('status','')}\n"
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
        f"⚡ parallel (per task): <b>{SETTINGS['parallel']}</b>\n"
        f"📦 batch size: <b>{SETTINGS['batch_size']}</b>\n"
        f"🧵 max tasks: <b>{SETTINGS['max_tasks']}</b>\n"
        f"🌐 global parallel: <b>{SETTINGS['global_concurrency']}</b>\n"
        f"💾 disk reserve: <b>{SETTINGS['disk_reserve_gb']}GB</b> "
        f"(free now: {hr(disk_free())})\n"
        f"⏳ delay between files: <b>{SETTINGS['delay']}s</b>\n\n"
        f"<i>Stream-delete ON — upload ke baad local file turant delete</i>"
    )
    await safe_edit(q.message, txt, kb(
        [("⚡ par=1", "s_par_1"), ("⚡ par=2", "s_par_2"), ("⚡ par=3", "s_par_3")],
        [("📦 batch=3", "s_bat_3"), ("📦 batch=5", "s_bat_5"), ("📦 batch=10", "s_bat_10")],
        [("🧵 tasks=2", "s_tsk_2"), ("🧵 tasks=3", "s_tsk_3")],
        [("⏳ delay=0.2", "s_dly_2"), ("⏳ delay=0.5", "s_dly_5"), ("⏳ delay=1", "s_dly_10")],
        [("◀ Menu", "menu")],
    ))

@bot.on_callback_query(filters.regex(r"^s_(par|bat|tsk|dly)_(\d+)$"))
async def cb_setting(c: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        return
    kind = q.matches[0].group(1)
    val = int(q.matches[0].group(2))
    if kind == "par":
        SETTINGS["parallel"] = val
    elif kind == "bat":
        SETTINGS["batch_size"] = val
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
# KEEP-ALIVE (Render / Codespaces)
# ══════════════════════════════════════════════════════════════
async def keep_alive():
    try:
        from aiohttp import web
        app = web.Application()
        app.router.add_get("/", lambda r: web.Response(
            text=f"CopyBot OK | tasks={len(TASKS)} | disk={hr(disk_free())}"))
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", "8080"))
        await web.TCPSite(runner, "0.0.0.0", port).start()
        log.info(f"keep-alive on :{port}")
    except Exception as e:
        log.warning(f"keep-alive skip: {e}")

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
async def main():
    global GLOBAL_SEM

    load_settings()
    GLOBAL_SEM = asyncio.Semaphore(int(SETTINGS.get("global_concurrency", 4)))

    if not all([API_ID, API_HASH, BOT_TOKEN, SESSION_STRING]):
        missing = [k for k, v in (
            ("API_ID", API_ID), ("API_HASH", API_HASH),
            ("BOT_TOKEN", BOT_TOKEN), ("SESSION_STRING", SESSION_STRING),
        ) if not v]
        log.error("❌ Missing credentials: " + ", ".join(missing))
        log.error("bot.py ke upar HARD_* variables mein ya .env mein bhar do")
        return

    await keep_alive()
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
    asyncio.run(main())
