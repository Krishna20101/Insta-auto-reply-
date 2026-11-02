"""
╔══════════════════════════════════════════════════════════════════════════╗
║  COPY BOT (GitHub / Codespaces / Render ready)                          ║
║  Telegram se poora control — koi terminal ki zarurat nahi               ║
║                                                                          ║
║  ENV (GitHub Codespaces Secrets ya Render Environment):                  ║
║    API_ID, API_HASH, BOT_TOKEN, SESSION_STRING, ADMIN_IDS                 ║
║                                                                          ║
║  FEATURES                                                                 ║
║   • ➕ New Task — source → target(s) → count/all → filter → parallel      ║
║   • 2-3 tasks ek saath (global concurrency cap = flood safe)             ║
║   • copy: 1) copy_message  2) file_id (0 data)  3) download+upload       ║
║   • batch of N (default 5): har batch ke baad sab-done check + retry     ║
║   • durable state: crash/restart par wahin se resume, koi file 2 baar    ║
║     nahi (done skip + inflight verify against target)                    ║
║   • 📊 Progress — kitna hua, kitna baaki, speed, ETA, data used          ║
║   • Pause / Resume / Cancel / Retry failed                               ║
║   • 0-data counter, data limit guard, disk guard                         ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import functools
import html as htmlmod
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from pyrogram import Client, filters, enums, idle
    from pyrogram.errors import (
        FloodWait, UserNotParticipant, ChatAdminRequired, ChannelPrivate,
        ChatForbidden, ChannelInvalid, RPCError,
    )
    from pyrogram.types import (
        Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    )
except ImportError:
    print("❌ pyrogram nahi hai. Chalao: pip install pyrofork aiohttp")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════
API_ID = int(os.environ.get("API_ID", "0") or 0)
API_HASH = os.environ.get("API_HASH", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
SESSION_STRING = os.environ.get("SESSION_STRING", "").strip()
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",")
             if x.lstrip("-").isdigit()]

DATA = Path(__file__).resolve().parent / "data"
DATA.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA / "settings.json"

SETTINGS = {
    "parallel": 3,          # ek task ke andar ek saath kitni files
    "batch_size": 5,        # har N ke baad barrier + retry
    "max_tasks": 3,         # ek saath kitne tasks
    "global_concurrency": 6,  # sab tasks mila ke max parallel copies
    "max_data_gb": 0,       # 0 = no limit (download+upload mode ka data guard)
    "disk_reserve_gb": 2,
    "delay": 0.2,
    "firebase_url": "",
    "firebase_token": "",
}


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            SETTINGS.update(json.loads(SETTINGS_FILE.read_text()))
        except Exception:
            pass


def save_settings():
    try:
        SETTINGS_FILE.write_text(json.dumps(SETTINGS, ensure_ascii=False, indent=1))
    except Exception:
        pass


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
log = logging.getLogger("CB")

MEDIA = ("video", "document", "photo", "audio", "voice", "animation", "sticker")
SAVED = ("me", "self", "saved", "saved messages", "savedmessages")
WRITE_ERR = ("ChatWriteForbidden", "CHAT_WRITE_FORBIDDEN", "ChatAdminRequired",
             "CHAT_ADMIN_REQUIRED", "ChatRestricted", "ChannelPrivate")


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════
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


def is_saved(x) -> bool:
    return isinstance(x, str) and x.strip().lower() in SAVED


def label(x) -> str:
    return "Saved Messages" if is_saved(x) else str(x)


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


async def safe_edit(msg, text, markup=None):
    for kw in (dict(text=text, reply_markup=markup, parse_mode=enums.ParseMode.HTML),
               dict(text=text, reply_markup=markup)):
        try:
            return await msg.edit_text(**kw)
        except FloodWait as e:
            await asyncio.sleep(int(e.value) + 1)
        except Exception as e:
            if "NotModified" in type(e).__name__:
                return None
            if "TooLong" in type(e).__name__:
                try:
                    return await msg.edit_text(text[:3900] + "…", reply_markup=markup)
                except Exception:
                    return None
    return None


def disk_free() -> int:
    try:
        return shutil.disk_usage(str(DATA)).free
    except Exception:
        return 0


# ══════════════════════════════════════════════════════════════
# STATE (per source→target, durable)
# ══════════════════════════════════════════════════════════════
FB_WARN = {"x": False}


def _key(src, dst) -> str:
    return f"{str(src).replace('-', 'n')}__{str(dst).replace('-', 'n')}"


def state_path(src, dst) -> Path:
    return DATA / f"state_{_key(src, dst)}.json"


def fb_url(src, dst) -> str | None:
    u = (SETTINGS.get("firebase_url") or "").strip().rstrip("/")
    if not u:
        return None
    tok = (SETTINGS.get("firebase_token") or "").strip()
    q = ("?auth=" + urllib.parse.quote(tok)) if tok else ""
    return f"{u}/rcf/{_key(src, dst)}.json{q}"


def fb_push(st):
    u = fb_url(st["src"], st["dst"])
    if not u:
        return
    try:
        req = urllib.request.Request(
            u, data=json.dumps(st, ensure_ascii=False).encode(), method="PUT",
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        if not FB_WARN["x"]:
            log.warning(f"firebase push fail: {type(e).__name__}: {str(e)[:100]}")
            FB_WARN["x"] = True


def fb_pull(src, dst):
    u = fb_url(src, dst)
    if not u:
        return None
    try:
        with urllib.request.urlopen(u, timeout=10) as r:
            d = json.loads(r.read().decode() or "null")
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def state_load(src, dst) -> dict:
    st = {"src": str(src), "dst": str(dst), "files": {}, "created": time.time(), "updated": 0}
    p = state_path(src, dst)
    if p.exists():
        try:
            st.update(json.loads(p.read_text()))
        except Exception:
            pass
    fb = fb_pull(src, dst)
    if fb:
        for k, v in (fb.get("files") or {}).items():
            cur = st["files"].get(k)
            if not cur or (cur.get("status") != "done" and v.get("status") == "done"):
                st["files"][k] = v
    return st


def state_save(st, push=True):
    st["updated"] = time.time()
    try:
        p = state_path(st["src"], st["dst"])
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1))
        tmp.replace(p)
    except Exception as e:
        log.warning(f"state save fail: {e}")
    if push:
        fb_push(st)


def summary(st) -> dict:
    c = {"done": 0, "pending": 0, "inflight": 0, "failed": 0}
    for v in (st.get("files") or {}).values():
        s = v.get("status", "pending")
        c[s if s in c else "pending"] += 1
    return c


def mark(st, mid, ok, **extra):
    rec = st["files"].setdefault(str(mid), {})
    rec["status"] = "done" if ok else "failed"
    rec["ts"] = time.time()
    rec.update(extra)


# ══════════════════════════════════════════════════════════════
# CLIENTS
# ══════════════════════════════════════════════════════════════
bot = Client("cb_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN,
             in_memory=True, workers=32, sleep_threshold=60)
_acc: Client | None = None


async def acc_client() -> Client:
    global _acc
    if _acc and _acc.is_connected:
        return _acc
    _acc = Client("cb_user", api_id=API_ID, api_hash=API_HASH,
                  session_string=SESSION_STRING, in_memory=True, no_updates=True,
                  workers=64, sleep_threshold=60)
    await _acc.start()
    log.info("user account connected")
    return _acc


# ══════════════════════════════════════════════════════════════
# COPY ENGINE
# ══════════════════════════════════════════════════════════════
class StopTask(Exception):
    pass


class Budget:
    """data limit + disk guard, parallel-safe (per task)."""

    def __init__(self, max_bytes=0, reserve=0):
        self.max = max_bytes
        self.reserve = reserve
        self.used = 0
        self._lock = asyncio.Lock()

    async def claim(self, size):
        async with self._lock:
            if self.max and self.used + size * 2 > self.max:
                raise StopTask(f"data limit ({hr(self.max)}) paar ho rahi thi — "
                               f"ab tak {hr(self.used)}")
            free = disk_free()
            need = int(size * 1.3) + self.reserve + self.used
            if free and free < need:
                raise StopTask(f"disk kam hai — free {hr(free)}, chahiye {hr(need)}")
            self.used += int(size * 1.3)

    async def release(self, size):
        async with self._lock:
            self.used = max(0, self.used - int(size * 1.3))


async def send_by_file_id(acc: Client, dst, msg, mtype, mobj) -> bool:
    fid = getattr(mobj, "file_id", None)
    if not fid:
        return False
    kw = dict(chat_id=dst, caption=msg.caption or "",
              caption_entities=msg.caption_entities or None)
    for attempt in (1, 2):
        try:
            if mtype == "video":
                await acc.send_video(**kw, video=fid,
                                     duration=getattr(mobj, "duration", 0) or 0,
                                     width=getattr(mobj, "width", 0) or 0,
                                     height=getattr(mobj, "height", 0) or 0,
                                     supports_streaming=True)
            elif mtype == "document":
                await acc.send_document(**kw, document=fid,
                                        file_name=getattr(mobj, "file_name", None))
            elif mtype == "photo":
                await acc.send_photo(**kw, photo=fid)
            elif mtype == "audio":
                await acc.send_audio(**kw, audio=fid,
                                     duration=getattr(mobj, "duration", 0) or 0,
                                     performer=getattr(mobj, "performer", None),
                                     title=getattr(mobj, "title", None))
            elif mtype == "voice":
                await acc.send_voice(**kw, voice=fid,
                                     duration=getattr(mobj, "duration", 0) or 0)
            elif mtype == "animation":
                await acc.send_animation(**kw, animation=fid)
            elif mtype == "sticker":
                await acc.send_sticker(chat_id=dst, sticker=fid)
            else:
                await acc.send_document(**kw, document=fid)
            return True
        except FloodWait:
            raise
        except Exception as e:
            name = type(e).__name__
            if any(t in name or t in str(e) for t in WRITE_ERR):
                raise
            if attempt == 1 and ("Reference" in name or "REFERENCE" in str(e)):
                try:
                    fresh = await acc.get_messages(msg.chat.id, msg.id)
                except Exception:
                    return False
                if not fresh:
                    return False
                mtype, mobj = get_media(fresh)
                msg = fresh
                fid = getattr(mobj, "file_id", None) if mobj else None
                if not fid:
                    return False
                continue
            return False
    return False


async def transfer(acc: Client, dst, msg, mtype, mobj, budget, cb=None) -> bool:
    size = getattr(mobj, "file_size", 0) or 0
    await budget.claim(size)
    tmp = tempfile.mkdtemp(prefix="cb_", dir=str(DATA))
    try:
        ext = {"video": ".mp4", "audio": ".mp3", "voice": ".ogg",
               "animation": ".mp4", "photo": ".jpg", "sticker": ".webp"}.get(mtype, ".bin")
        name = getattr(mobj, "file_name", None) or f"file_{msg.id}{ext}"
        path = os.path.join(tmp, name)
        got = await acc.download_media(msg, file_name=path)
        if not got or not os.path.exists(got):
            return False
        kw = dict(chat_id=dst, caption=msg.caption or "",
                  caption_entities=msg.caption_entities or None)
        if mtype == "video":
            await acc.send_video(**kw, video=got,
                                 duration=getattr(mobj, "duration", 0) or 0,
                                 width=getattr(mobj, "width", 0) or 0,
                                 height=getattr(mobj, "height", 0) or 0,
                                 supports_streaming=True)
        elif mtype == "photo":
            await acc.send_photo(**kw, photo=got)
        elif mtype == "audio":
            await acc.send_audio(**kw, audio=got,
                                 duration=getattr(mobj, "duration", 0) or 0)
        elif mtype == "voice":
            await acc.send_voice(**kw, voice=got,
                                 duration=getattr(mobj, "duration", 0) or 0)
        elif mtype == "animation":
            await acc.send_animation(**kw, animation=got)
        elif mtype == "sticker":
            await acc.send_sticker(chat_id=dst, sticker=got)
        else:
            await acc.send_document(**kw, document=got, force_document=True)
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        await budget.release(size)


async def copy_one(acc: Client, dst, msg, budget, st=None, counters=None) -> bool:
    mtype, mobj = get_media(msg)
    if st is not None:
        rec = st["files"].setdefault(str(msg.id), {})
        rec.update({"status": "inflight", "ts": time.time(),
                    "name": getattr(mobj, "file_name", None) if mobj else None,
                    "size": (getattr(mobj, "file_size", 0) or 0) if mobj else 0,
                    "unique_id": (getattr(mobj, "file_unique_id", None)
                                  or getattr(mobj, "file_id", None)) if mobj else None})
        state_save(st, push=False)

    if not mtype:                                    # text
        for kw in (dict(entities=msg.entities or None), {}):
            try:
                await acc.send_message(dst, msg.text or "",
                                       disable_web_page_preview=True, **kw)
                if counters is not None:
                    counters["server"] += 1
                return True
            except FloodWait:
                raise
            except Exception:
                continue
        return False

    try:                                             # 1) server-side copy (0 data)
        await acc.copy_message(chat_id=dst, from_chat_id=msg.chat.id, message_id=msg.id)
        if counters is not None:
            counters["server"] += 1
        return True
    except FloodWait:
        raise
    except Exception as e:
        if any(t in type(e).__name__ or t in str(e) for t in WRITE_ERR):
            raise

    try:                                             # 2) file_id (0 data)
        if await send_by_file_id(acc, dst, msg, mtype, mobj):
            if counters is not None:
                counters["cache"] += 1
            return True
    except FloodWait:
        raise
    except Exception:
        pass

    try:                                             # 3) download + upload
        if await transfer(acc, dst, msg, mtype, mobj, budget):
            size = getattr(mobj, "file_size", 0) or 0
            if counters is not None:
                counters["transfer"] += 1
                counters["bytes"] += size * 2
            return True
    except FloodWait:
        raise
    return False


# ══════════════════════════════════════════════════════════════
# SCAN
# ══════════════════════════════════════════════════════════════
async def newest_id(acc, src) -> int:
    async for m in acc.get_chat_history(src, limit=1):
        return getattr(m, "id", 0) or 0
    return 0


async def fetch_ids(acc, src, ids):
    for _ in range(3):
        try:
            res = await acc.get_messages(src, ids)
            return [m for m in (res if isinstance(res, list) else [res])
                    if m and not getattr(m, "empty", False)]
        except FloodWait as e:
            await asyncio.sleep(int(e.value) + 2)
        except Exception as e:
            log.warning(f"fetch fail: {e}")
            return []
    return []


async def scan_media(acc, src, want=0, only=None, on_progress=None):
    """want>0 -> start se itni media (id probing) | want==0 -> saari media."""
    if want:
        probe, picked, empty, calls = 1, [], 0, 0
        top = await newest_id(acc, src)
        while len(picked) < want:
            if top and probe > top + 100:
                break
            msgs = await fetch_ids(acc, src, list(range(probe, probe + 100)))
            calls += 1
            if not msgs:
                empty += 1
                if empty >= 8:
                    break
            else:
                empty = 0
            for m in sorted(msgs, key=lambda x: x.id):
                t, _ = get_media(m)
                if not t or (only and t != only):
                    continue
                picked.append(m)
                if len(picked) >= want:
                    break
            probe += 100
            if on_progress and calls % 5 == 0:
                on_progress(len(picked))
            await asyncio.sleep(0.25)
        if len(picked) < want:                       # sparse channel fallback
            extra = await scan_all(acc, src, only)
            have = {m.id for m in picked}
            for m in extra:
                if m.id not in have:
                    picked.append(m)
                if len(picked) >= want:
                    break
        picked.sort(key=lambda m: m.id)
        return picked[:want]

    return await scan_all(acc, src, only)


async def scan_all(acc, src, only=None):
    found = []
    async for m in acc.get_chat_history(src):
        if not m or getattr(m, "empty", False):
            continue
        t, _ = get_media(m)
        if not t or (only and t != only):
            continue
        found.append(m)
    found.sort(key=lambda m: m.id)
    return found


# ══════════════════════════════════════════════════════════════
# TASK MANAGER
# ══════════════════════════════════════════════════════════════
TASKS: dict[int, dict] = {}
COUNTER = {"n": 0}
GLOBAL_SEM = asyncio.Semaphore(6)
TASKS_FILE = DATA / "tasks.json"


def tasks_save():
    try:
        out = []
        for t in TASKS.values():
            out.append({k: t[k] for k in
                        ("id", "src", "src_title", "dsts", "want", "only", "parallel",
                         "batch", "status", "done", "total", "failed", "bytes",
                         "modes", "created", "elapsed")})
        TASKS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    except Exception:
        pass


def tasks_load():
    if not TASKS_FILE.exists():
        return
    try:
        for t in json.loads(TASKS_FILE.read_text()):
            t["status"] = "stopped" if t.get("status") in ("running", "paused") else t.get("status")
            t.setdefault("runner", None)
            TASKS[t["id"]] = t
        if TASKS:
            COUNTER["n"] = max(TASKS)
    except Exception:
        pass


async def resolve_targets(acc, text):
    out = []
    for part in [p.strip() for p in (text or "").split(",") if p.strip()]:
        if part.lower() in SAVED:
            out.append(("me", "Saved Messages"))
        elif part.lstrip("-").isdigit():
            cid = int(part)
            try:
                ch = await acc.get_chat(cid)
                t = getattr(ch, "title", None) or getattr(ch, "first_name", None)
            except Exception:
                t = None
            out.append((cid, t))
        else:
            ch = await acc.get_chat(part)
            out.append((ch.id, getattr(ch, "title", None) or getattr(ch, "first_name", None)))
    if not out:
        raise ValueError("koi target nahi mila")
    return out


async def verify_inflight(acc, dst, st):
    inflight = [k for k, v in st["files"].items() if v.get("status") == "inflight"]
    if not inflight:
        return
    log.info(f"verifying {len(inflight)} inflight files against target")
    seen = set()
    try:
        async for m in acc.get_chat_history(dst, limit=300):
            t, o = get_media(m)
            if o is not None:
                u = getattr(o, "file_unique_id", None) or getattr(o, "file_id", None)
                if u:
                    seen.add(u)
    except Exception:
        for k in inflight:
            st["files"][k]["status"] = "pending"
        state_save(st, push=False)
        return
    for k in inflight:
        u = st["files"][k].get("unique_id")
        st["files"][k]["status"] = "done" if (u and u in seen) else "pending"
    state_save(st, push=False)


def task_text(t) -> str:
    sm = {"done": t.get("done", 0), "pending": max(0, t.get("total", 0) - t.get("done", 0))}
    total = t.get("total") or 1
    bar = pbar(t.get("done", 0), total)
    md = t.get("modes", {}) or {}
    icon = {"running": "⚡", "paused": "⏸", "done": "✅", "cancelled": "❌",
            "stopped": "⏹", "queued": "⏳", "error": "⚠️"}.get(
        (t.get("status") or "").split(":")[0], "🔄")
    txt = (f"{icon} <b>Task #{t['id']}</b> — {esc(t.get('status'))}\n\n"
           f"📤 {esc(t.get('src_title') or t['src'])}\n"
           f"📥 {esc(', '.join(lbl for _, lbl in (t.get('dsts') or [])) or '?')}\n\n"
           f"[{bar}] {t.get('done', 0)}/{t.get('total', 0)} files"
           + (f" | ❌ {t.get('failed', 0)}" if t.get("failed") else "") + "\n"
           f"⚙️ server: {md.get('server', 0)} | file_id(0-data): {md.get('cache', 0)} | "
           f"download+upload: {md.get('transfer', 0)}\n"
           f"📶 data: {hr(t.get('bytes', 0))} | ⏱ {t.get('elapsed', 0)/60:.1f} min")
    if t.get("note"):
        txt += f"\n\n⚠️ {esc(t['note'])[:400]}"
    return txt


def task_kb(t):
    tid = t["id"]
    st = t.get("status")
    rows = []
    if st in ("running",):
        rows.append([("⏸ Pause", f"t_pause_{tid}"), ("❌ Cancel", f"t_cancel_{tid}")])
    elif st in ("paused", "stopped", "cancelled", "error") or (st or "").startswith("stopped"):
        rows.append([("▶️ Resume", f"t_resume_{tid}"), ("🗑 Delete", f"t_del_{tid}")])
    if t.get("failed"):
        rows.append([("🔁 Retry failed", f"t_retry_{tid}")])
    rows.append([("🔄 Refresh", f"t_view_{tid}"), ("◀ Tasks", "menu_tasks")])
    return kb(*rows)


async def run_task(tid: int):
    t = TASKS.get(tid)
    if not t:
        return
    t["status"] = "running"
    t["note"] = ""
    tasks_save()
    t0 = time.time()
    par = max(1, int(t.get("parallel", 3)))
    batch = max(1, int(t.get("batch", 5)))
    max_bytes = int(float(SETTINGS.get("max_data_gb", 0)) * 1024 ** 3) if SETTINGS.get("max_data_gb") else 0
    reserve = int(float(SETTINGS.get("disk_reserve_gb", 2)) * 1024 ** 3)

    try:
        acc = await acc_client()
    except Exception as e:
        t["status"] = "error"
        t["note"] = f"user account connect nahi hua: {e}"
        return

    st = None
    try:
        # ── scan ──
        t["status"] = "scanning"
        only = t.get("only")
        media = await scan_media(acc, t["src"], int(t.get("want") or 0), only)
        if not media:
            t["status"] = "done"
            t["note"] = "koi file nahi mili (filter galat ya chat khali)"
            return
        total = len(media)

        # multi-target: state har target ke liye alag
        for dst, dtitle in t["dsts"]:
            stt = state_load(t["src"], dst)
            await verify_inflight(acc, dst, stt)
            todo = [m for m in media if stt["files"].get(str(m.id), {}).get("status") != "done"]

            t["status"] = "running"
            t["total"] = total
            t["done"] = total - len(todo)
            t["dst_current"] = label(dst)
            tasks_save()

            budget = Budget(max_bytes, reserve)
            groups = [todo[i:i + batch] for i in range(0, len(todo), batch)]

            for gi, group in enumerate(groups, 1):
                if t.get("cancel"):
                    t["status"] = "cancelled"
                    raise StopTask("cancel kiya gaya")
                while t.get("paused"):
                    if t.get("cancel"):
                        t["status"] = "cancelled"
                        raise StopTask("cancel kiya gaya")
                    t["status"] = "paused"
                    await asyncio.sleep(1)
                if t.get("cancel"):
                    t["status"] = "cancelled"
                    raise StopTask("cancel kiya gaya")
                if t.get("status") != "cancelled":
                    t["status"] = "running"

                sem = asyncio.Semaphore(par)

                async def worker(m):
                    async with sem:
                        async with GLOBAL_SEM:
                            while t.get("paused"):
                                if t.get("cancel"):
                                    raise StopTask("cancel kiya gaya")
                                await asyncio.sleep(1)
                            if t.get("cancel"):
                                raise StopTask("cancel kiya gaya")
                            mtype, mobj = get_media(m)
                            size = getattr(mobj, "file_size", 0) or 0
                            t["current"] = f"#{m.id} {mtype}"
                            ok = False
                            f0 = time.time()
                            try:
                                ok = await asyncio.wait_for(
                                    copy_one(acc, dst, m, budget, stt, t["modes"]), timeout=1800)
                            except StopTask:
                                raise
                            except FloodWait as e:
                                await asyncio.sleep(int(e.value) + 3)
                            except asyncio.TimeoutError:
                                t["note"] = f"#{m.id} timeout (30 min) — skip"
                            except Exception as e:
                                if any(x in type(e).__name__ or x in str(e) for x in WRITE_ERR):
                                    raise StopTask(
                                        "target me likhne ki permission nahi — us channel me "
                                        "apna account admin banao (Post Messages ON)")
                                log.warning(f"copy fail #{m.id}: {type(e).__name__}: {e}")

                            mark(stt, m.id, ok, name=getattr(mobj, "file_name", None), size=size,
                                 unique_id=(getattr(mobj, "file_unique_id", None)
                                            or getattr(mobj, "file_id", None)) if mobj else None)
                            state_save(stt, push=False)
                            if ok:
                                t["done"] = t.get("done", 0) + 1
                                t["bytes"] = t.get("bytes", 0) + size * 2 if t["modes"]["transfer"] else t["bytes"]
                                t["speed"] = (size * 2) / max(0.1, time.time() - f0) / 1024 ** 2
                                t["last_ok"] = f"#{m.id} {mtype} {hr(size)} {time.time()-f0:.0f}s"
                            else:
                                t["failed"] = t.get("failed", 0) + 1
                            tasks_save()
                            if SETTINGS.get("delay"):
                                await asyncio.sleep(float(SETTINGS["delay"]))

                if par == 1:
                    for m in group:
                        await worker(m)
                else:
                    await asyncio.gather(*(worker(m) for m in group))

                # batch barrier + retry
                again = [m for m in group
                         if stt["files"].get(str(m.id), {}).get("status") != "done"]
                if again and not t.get("cancel"):
                    for m in again:
                        try:
                            async with GLOBAL_SEM:
                                ok = await asyncio.wait_for(
                                    copy_one(acc, dst, m, budget, stt, t["modes"]), timeout=1800)
                            mark(stt, m.id, ok)
                            if ok:
                                t["done"] = t.get("done", 0) + 1
                                t["failed"] = max(0, t.get("failed", 1) - 1)
                        except Exception:
                            pass

                state_save(stt, push=True)
                t["batch_no"] = f"{gi}/{len(groups)}"
                tasks_save()

        t["status"] = "done"
        t["note"] = ""
    except StopTask as e:
        if t.get("status") != "cancelled":
            t["status"] = "stopped"
        t["note"] = str(e)
    except asyncio.CancelledError:
        t["status"] = "stopped"
        t["note"] = "band kiya gaya"
    except Exception as e:
        log.exception("task crash")
        t["status"] = "error"
        t["note"] = f"{type(e).__name__}: {e}"
    finally:
        t["elapsed"] = t.get("elapsed", 0) + (time.time() - t0)
        t["current"] = ""
        tasks_save()


def start_task(tid):
    t = TASKS[tid]
    if t.get("runner") and not t["runner"].done():
        return
    t["cancel"] = False
    t["paused"] = False
    t["runner"] = asyncio.create_task(run_task(tid))


# ══════════════════════════════════════════════════════════════
# BOT UI
# ══════════════════════════════════════════════════════════════
STATES: dict[int, dict] = {}


def main_menu():
    running = sum(1 for t in TASKS.values() if t.get("status") == "running")
    rows = [[("➕ New Task", "new"), ("📋 Tasks", "menu_tasks")],
            [("📊 Progress", "menu_progress"), ("⚙️ Settings", "menu_settings")],
            [("🩺 Test / Diagnostics", "diag")]]
    head = f"🤖 <b>Copy Bot</b>\n\n" + (f"⚡ {running} task chal raha hai\n" if running else "")
    return head, kb(*rows)


def guard(func):
    @functools.wraps(func)
    async def wrap(client, upd, *a, **kw):
        u = getattr(upd, "from_user", None) or getattr(
            getattr(upd, "message", None), "from_user", None)
        uid = u.id if u else 0
        iscb = hasattr(upd, "answer")
        if not is_admin(uid):
            if iscb:
                try:
                    await upd.answer(f"⛔ Admin nahi. Aapki ID: {uid}", show_alert=True)
                except Exception:
                    pass
            else:
                await upd.reply(f"⛔ Admin nahi. Aapki ID: <code>{uid}</code>",
                                parse_mode=enums.ParseMode.HTML)
            return
        if iscb:
            try:
                await upd.answer()
            except Exception:
                pass
        try:
            return await func(client, upd, *a, **kw)
        except FloodWait as e:
            log.warning(f"floodwait {e.value}s")
        except Exception as e:
            log.exception(f"handler {func.__name__}")
            txt = f"❌ <b>Error</b>\n<code>{esc(type(e).__name__)}: {esc(e)}</code>"
            try:
                if iscb and getattr(upd, "message", None):
                    await safe_edit(upd.message, txt, kb([("◀ Menu", "menu")]))
                elif hasattr(upd, "reply"):
                    await upd.reply(txt, parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
    return wrap


@bot.on_message(filters.command(["start", "menu"]) & filters.private)
@guard
async def cmd_start(c, m):
    STATES.pop(m.from_user.id, None)
    head, markup = main_menu()
    await m.reply(head + "\nNeeche se chuno 👇", reply_markup=markup,
                  parse_mode=enums.ParseMode.HTML)


@bot.on_callback_query(filters.regex("^menu$"))
@guard
async def cb_menu(c, q):
    STATES.pop(q.from_user.id, None)
    head, markup = main_menu()
    await safe_edit(q.message, head + "\nNeeche se chuno 👇", markup)


# ── NEW TASK ──
@bot.on_callback_query(filters.regex("^new$"))
@guard
async def cb_new(c, q):
    STATES[q.from_user.id] = {"step": "src", "d": {}}
    await safe_edit(q.message,
                    "➕ <b>New Task — Step 1/5</b>\n\n"
                    "📤 <b>Source</b> bhejo — jahan se copy karna hai\n\n"
                    "• ID: <code>-1001234567890</code>\n"
                    "• ya <code>@username</code> / invite link\n\n"
                    "<i>Source wale account se hi SESSION_STRING hai, warna access nahi hoga.</i>",
                    kb([("◀ Cancel", "menu")]))


@bot.on_message(filters.private & filters.text & ~filters.command(["start", "menu"]))
@guard
async def on_text(c, m):
    uid = m.from_user.id
    st = STATES.get(uid)
    if not st:
        head, markup = main_menu()
        await m.reply(head + "\nNeeche se chuno 👇", reply_markup=markup,
                      parse_mode=enums.ParseMode.HTML)
        return
    txt = (m.text or "").strip()
    step, d = st["step"], st["d"]
    acc = await acc_client()

    if step == "src":
        try:
            if txt.lstrip("-").isdigit():
                src = int(txt)
                try:
                    ch = await acc.get_chat(src)
                    title = getattr(ch, "title", None)
                except Exception:
                    title = None
            else:
                ch = await acc.get_chat(txt)
                src, title = ch.id, getattr(ch, "title", None)
        except Exception as e:
            await m.reply(f"❌ Source nahi mila: <code>{esc(type(e).__name__)}: {esc(e)}</code>\n"
                          "Pehle us account se channel join karo.",
                          parse_mode=enums.ParseMode.HTML)
            return
        d["src"], d["src_title"] = src, title
        st["step"] = "dst"
        await m.reply(f"✅ Source: <b>{esc(title or src)}</b>\n<code>{src}</code>\n\n"
                      "📥 <b>Target</b> bhejo (jahan file jaayegi)\n\n"
                      "• ID / @username / link\n"
                      "• <code>me</code> = Saved Messages\n"
                      "• ek se zyada: comma se — <code>me, -1001234..., @mychannel</code>",
                      reply_markup=kb([("◀ Cancel", "menu")]),
                      parse_mode=enums.ParseMode.HTML)
        return

    if step == "dst":
        try:
            targets = await resolve_targets(acc, txt)
        except Exception as e:
            await m.reply(f"❌ Target nahi mila: <code>{esc(type(e).__name__)}: {esc(e)}</code>")
            return
        d["dsts"] = targets
        warn = ""
        for dst, _ in targets:
            if is_saved(dst):
                warn += "\n✅ Saved Messages"
                continue
            try:
                me = await acc.get_me()
                mem = await acc.get_chat_member(dst, me.id)
                stt = str(getattr(mem, "status", "")).split(".")[-1].lower()
                warn += (f"\n{'✅' if stt in ('administrator','owner','creator') else '🚫'} "
                         f"{esc(label(dst))}: {stt}")
            except Exception as e:
                warn += f"\n⚠️ {esc(label(dst))}: check fail ({type(e).__name__})"
        st["step"] = "count"
        await m.reply(f"✅ Target set{warn}\n\n"
                      "🔢 <b>Kitni files?</b>\n\n"
                      "• number: <code>50</code>\n"
                      "• <code>all</code> = saari files (start se)\n\n"
                      "<i>Resume apne aap hota hai — jo pehle ho chuki hain skip hongi.</i>",
                      reply_markup=kb([("◀ Cancel", "menu")]),
                      parse_mode=enums.ParseMode.HTML)
        return

    if step == "count":
        if txt.lower() in ("all", "a", "0", "*"):
            d["want"] = 0
        elif txt.isdigit():
            d["want"] = int(txt)
        else:
            await m.reply("❌ number ya `all` likho")
            return
        st["step"] = "filter"
        await m.reply("🗂 <b>Kaunsi files?</b>",
                      reply_markup=kb([("Sab (video/pdf/photo/audio)", "f_all")],
                                      [("Sirf video", "f_video")],
                                      [("Sirf documents (pdf/txt)", "f_document")]))
        return

    if step == "parallel":
        d["parallel"] = max(1, min(8, int(txt))) if txt.isdigit() else 3
        st["step"] = "confirm"
        await show_confirm(m, d)
        return


@bot.on_callback_query(filters.regex("^f_(all|video|document)$"))
@guard
async def cb_filter(c, q):
    uid = q.from_user.id
    st = STATES.get(uid)
    if not st:
        await safe_edit(q.message, "Session expire — /start dobara", kb([("◀ Menu", "menu")]))
        return
    val = q.data.split("_")[1]
    st["d"]["only"] = None if val == "all" else val
    st["step"] = "parallel"
    await safe_edit(q.message,
                    f"⚙️ <b>Parallel files?</b> (ek saath kitni)\n\n"
                    f"1 = strict order\n3 = fast (recommended)\n5 = sabse fast\n\n"
                    f"Abhi default: <b>{SETTINGS['parallel']}</b>",
                    kb([("1 (order)", "p_1"), ("3 (fast)", "p_3"), ("5", "p_5")],
                       [("◀ Cancel", "menu")]))


@bot.on_callback_query(filters.regex("^p_(\\d)$"))
@guard
async def cb_parallel(c, q):
    uid = q.from_user.id
    st = STATES.get(uid)
    if not st:
        return
    st["d"]["parallel"] = int(q.matches[0].group(1))
    st["step"] = "confirm"
    await show_confirm(q, st["d"])


async def show_confirm(q, d):
    src, src_title = d["src"], d.get("src_title")
    want = d.get("want", 0)
    only = d.get("only")
    tl = "\n".join(f"  • {esc(t or label(x))}" for x, t in d["dsts"])
    txt = (f"🧾 <b>Confirm Task</b>\n\n"
           f"📤 Source: <b>{esc(src_title or src)}</b>\n     <code>{src}</code>\n"
           f"📥 Target ({len(d['dsts'])}):\n{tl}\n\n"
           f"🔢 Files: <b>{'saari' if not want else want}</b>"
           f" | filter: {only or 'sab'}\n"
           f"⚙️ parallel: {d.get('parallel', 3)} | batch: {SETTINGS['batch_size']}\n\n"
           f"▶️ Start dabao (resume apne aap).")
    await safe_edit(q.message, txt, kb([("▶️ Start", "do_start")], [("◀ Cancel", "menu")]))


@bot.on_callback_query(filters.regex("^do_start$"))
@guard
async def cb_do_start(c, q):
    uid = q.from_user.id
    st = STATES.pop(uid, None)
    if not st:
        await safe_edit(q.message, "Session expire — /start dobara", kb([("◀ Menu", "menu")]))
        return
    d = st["d"]
    running = [t for t in TASKS.values() if t.get("status") in ("running", "scanning", "paused")]
    if len(running) >= int(SETTINGS.get("max_tasks", 3)):
        await safe_edit(q.message,
                        f"⚠️ Already {len(running)} tasks chal rahe hain (max {SETTINGS['max_tasks']}).\n"
                        "Pehle koi pause/cancel karo.", kb([("◀ Tasks", "menu_tasks")]))
        return
    COUNTER["n"] += 1
    tid = COUNTER["n"]
    TASKS[tid] = {
        "id": tid, "src": d["src"], "src_title": d.get("src_title"),
        "dsts": d["dsts"], "want": d.get("want", 0), "only": d.get("only"),
        "parallel": d.get("parallel", 3), "batch": SETTINGS["batch_size"],
        "status": "queued", "done": 0, "total": 0, "failed": 0, "bytes": 0,
        "modes": {"server": 0, "cache": 0, "transfer": 0},
        "created": time.time(), "elapsed": 0, "note": "", "runner": None,
    }
    start_task(tid)
    tasks_save()
    await safe_edit(q.message, f"🚀 <b>Task #{tid} shuru!</b>\n\n"
                               "📋 Tasks se live dekho, 📊 Progress se overall.",
                    kb([(f"👁 Task #{tid}", f"t_view_{tid}")], [("◀ Menu", "menu")]))


# ── TASKS ──
@bot.on_callback_query(filters.regex("^menu_tasks$"))
@guard
async def cb_tasks(c, q):
    if not TASKS:
        await safe_edit(q.message, "📋 Koi task nahi. ➕ New Task se banao.",
                        kb([("➕ New Task", "new")], [("◀ Menu", "menu")]))
        return
    rows = []
    for t in sorted(TASKS.values(), key=lambda x: -x["id"])[:10]:
        icon = {"running": "⚡", "paused": "⏸", "done": "✅", "cancelled": "❌",
                "stopped": "⏹", "error": "⚠️", "scanning": "🔎", "queued": "⏳"}.get(
            (t.get("status") or "").split(":")[0], "🔄")
        rows.append([(f"{icon} #{t['id']} {t.get('done',0)}/{t.get('total',0)} — {t.get('status')}",
                      f"t_view_{t['id']}")])
    rows.append([("◀ Menu", "menu")])
    await safe_edit(q.message, "📋 <b>Tasks</b>\n\nKisi par tap karo:", kb(*rows))


@bot.on_callback_query(filters.regex(r"^t_view_(\d+)$"))
@guard
async def cb_tview(c, q):
    tid = int(q.matches[0].group(1))
    t = TASKS.get(tid)
    if not t:
        await safe_edit(q.message, "Task nahi mila", kb([("◀ Tasks", "menu_tasks")]))
        return
    extra = ""
    if t.get("current"):
        extra = f"\n\n📄 ab: <code>{esc(t['current'])}</code>"
    if t.get("speed"):
        extra += f"\n🚀 last speed: {t['speed']:.1f} MB/s"
    if t.get("batch_no"):
        extra += f"\n📦 batch: {t['batch_no']}"
    await safe_edit(q.message, task_text(t) + extra, task_kb(t))


@bot.on_callback_query(filters.regex(r"^t_(pause|resume|cancel|del|retry)_(\d+)$"))
@guard
async def cb_taction(c, q):
    act, tid = q.matches[0].group(1), int(q.matches[0].group(2))
    t = TASKS.get(tid)
    if not t:
        return
    if act == "pause":
        t["paused"] = True
        t["status"] = "paused"
    elif act == "resume":
        if t.get("runner") and not t["runner"].done():
            t["paused"] = False
            t["status"] = "running"
        else:
            start_task(tid)
    elif act == "cancel":
        t["cancel"] = True
        t["status"] = "cancelled"
    elif act == "del":
        t["cancel"] = True
        TASKS.pop(tid, None)
        tasks_save()
        await cb_tasks(c, q)
        return
    elif act == "retry":
        st = state_load(t["src"], t["dsts"][0][0])
        n = 0
        for k, v in st["files"].items():
            if v.get("status") == "failed":
                v["status"] = "pending"
                n += 1
        state_save(st)
        t["failed"] = 0
        start_task(tid)
        await q.answer(f"{n} files retry queue me", show_alert=True)
    tasks_save()
    await safe_edit(q.message, task_text(TASKS.get(tid) or t),
                    task_kb(TASKS.get(tid) or t))


# ── PROGRESS ──
@bot.on_callback_query(filters.regex("^menu_progress$"))
@guard
async def cb_progress(c, q):
    await safe_edit(q.message, build_progress(), kb([("🔄 Refresh", "menu_progress")],
                                                    [("◀ Menu", "menu")]))


def build_progress() -> str:
    if not TASKS:
        return "📊 Koi task nahi. ➕ New Task se banao."
    lines = ["📊 <b>Progress</b>\n"]
    for t in sorted(TASKS.values(), key=lambda x: -x["id"])[:8]:
        total = t.get("total") or 0
        bar = pbar(t.get("done", 0), total)
        pct = (t.get("done", 0) * 100 // total) if total else 0
        lines.append(
            f"<b>#{t['id']}</b> [{bar}] {pct}%  {t.get('status')}\n"
            f"    ✅ {t.get('done', 0)} | ⏳ {max(0, total - t.get('done', 0))} | "
            f"❌ {t.get('failed', 0)} | 📶 {hr(t.get('bytes', 0))} | ⏱ {t.get('elapsed',0)/60:.1f}m")
    pend = 0
    for f in DATA.glob("state_*.json"):
        try:
            pend += summary(json.loads(f.read_text()))["pending"]
        except Exception:
            pass
    lines.append(f"\n⏳ total baaki (save hua state): <b>{pend}</b>")
    lines.append("↪️ Resume: Tasks → ▶️ Resume, ya naya task same source/target par — "
                 "jo ho chuki hain skip hongi.")
    return "\n".join(lines)


# ── SETTINGS ──
@bot.on_callback_query(filters.regex("^menu_settings$"))
@guard
async def cb_settings(c, q):
    txt = (f"⚙️ <b>Settings</b>\n\n"
           f"⚡ parallel (per task): <b>{SETTINGS['parallel']}</b>\n"
           f"📦 batch size: <b>{SETTINGS['batch_size']}</b>\n"
           f"🧵 max tasks saath: <b>{SETTINGS['max_tasks']}</b>\n"
           f"🌐 global parallel: <b>{SETTINGS['global_concurrency']}</b>\n"
           f"📶 data limit (download mode): "
           f"<b>{SETTINGS['max_data_gb'] or 'no limit'}</b>"
           f"{' GB' if SETTINGS['max_data_gb'] else ''}\n"
           f"💾 disk reserve: <b>{SETTINGS['disk_reserve_gb']} GB</b> "
           f"(free: {hr(disk_free())})\n"
           f"☁️ firebase: <b>{'ON' if SETTINGS['firebase_url'] else 'OFF'}</b>")
    await safe_edit(q.message, txt, kb(
        [("⚡ 1", "s_par_1"), ("⚡ 3", "s_par_3"), ("⚡ 5", "s_par_5")],
        [("📦 batch 3", "s_bat_3"), ("📦 5", "s_bat_5"), ("📦 10", "s_bat_10")],
        [("📶 limit: 0", "s_lim_0"), ("📶 20GB", "s_lim_20"), ("📶 100GB", "s_lim_100")],
        [("◀ Menu", "menu")]))


@bot.on_callback_query(filters.regex(r"^s_(par|bat|lim)_(\d+)$"))
@guard
async def cb_setting(c, q):
    kind, val = q.matches[0].group(1), int(q.matches[0].group(2))
    if kind == "par":
        SETTINGS["parallel"] = val
    elif kind == "bat":
        SETTINGS["batch_size"] = val
    else:
        SETTINGS["max_data_gb"] = val
    save_settings()
    await cb_settings(c, q)


# ── DIAGNOSTICS ──
@bot.on_callback_query(filters.regex("^diag$"))
@guard
async def cb_diag(c, q):
    await safe_edit(q.message, "🩺 Test chal raha hai...", kb([("◀ Menu", "menu")]))
    lines, ok = [], True

    def add(s, name, detail=""):
        nonlocal ok
        lines.append(f"{s} <b>{name}</b>" + (f"\n     <code>{esc(detail)[:250]}</code>" if detail else ""))
        if s.startswith("❌"):
            ok = False

    miss = [k for k, v in [("API_ID", API_ID), ("API_HASH", API_HASH),
                           ("BOT_TOKEN", BOT_TOKEN), ("SESSION_STRING", SESSION_STRING)] if not v]
    add("✅" if not miss else "❌", "Env", "sab set" if not miss else f"missing: {miss}")
    add("ℹ️", "Admins", str(ADMIN_IDS) or "khali (sab allowed)")

    try:
        me = await bot.get_me()
        add("✅", "Bot", f"@{me.username}")
    except Exception as e:
        add("❌", "Bot", f"{type(e).__name__}: {e}")

    try:
        acc = await acc_client()
        me = await acc.get_me()
        add("✅", "User account", f"{me.first_name} (id {me.id})")
        n = 0
        async for d in acc.get_dialogs(limit=100):
            if getattr(d, "chat", None):
                n += 1
        add("✅", "Chats", f"{n} mila (Get ID kaam karega)")
        try:
            t = await acc.send_message("me", "🩺 test")
            await t.delete()
            add("✅", "Saved Messages", "write OK")
        except Exception as e:
            add("⚠️", "Saved Messages", f"{type(e).__name__}: {e}")
    except Exception as e:
        add("❌", "User account", f"{type(e).__name__}: {e} — SESSION_STRING check karo")

    add("✅" if disk_free() > 2 * 1024 ** 3 else "⚠️", "Disk", hr(disk_free()))
    add("ℹ️", "Engine", f"parallel={SETTINGS['parallel']} batch={SETTINGS['batch_size']} "
                        f"tasks≤{SETTINGS['max_tasks']}")

    await safe_edit(q.message, "🩺 <b>Diagnostics</b>\n\n" + "\n".join(lines)
                    + "\n\n" + ("✅ sab theek" if ok else "❌ kuch fix karna hai"),
                    kb([("🔄 Test", "diag")], [("◀ Menu", "menu")]))


# ══════════════════════════════════════════════════════════════
# KEEP-ALIVE (Render) + MAIN
# ══════════════════════════════════════════════════════════════
async def keep_alive():
    try:
        from aiohttp import web
        app = web.Application()
        app.router.add_get("/", lambda r: web.Response(
            text=f"Copy Bot running | tasks={len(TASKS)}"))
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", "8080"))
        await web.TCPSite(runner, "0.0.0.0", port).start()
        log.info(f"keep-alive on 0.0.0.0:{port}")
    except Exception as e:
        log.warning(f"keep-alive skip: {e}")


async def main():
    load_settings()
    GLOBAL_SEM = asyncio.Semaphore(int(SETTINGS.get("global_concurrency", 6)))
    await keep_alive()

    if not all([API_ID, API_HASH, BOT_TOKEN, SESSION_STRING]):
        log.error("ENV missing: API_ID / API_HASH / BOT_TOKEN / SESSION_STRING")
        log.error("GitHub → Settings → Codespaces → Secrets me daalo, phir restart karo.")
        return

    tasks_load()
    try:
        acc = await acc_client()
        me = await acc.get_me()
        log.info(f"user: {me.first_name} ({me.id})")
    except Exception as e:
        log.error(f"user account fail: {e}")

    await bot.start()
    me = await bot.get_me()
    log.info(f"bot @{me.username} ready | admins={ADMIN_IDS}")
    for uid in ADMIN_IDS[:3]:
        try:
            await bot.send_message(uid, "🤖 <b>Copy Bot online</b>\n\n/start dabao",
                                   parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
    await idle()
    await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
