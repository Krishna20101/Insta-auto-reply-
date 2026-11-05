"""Combined Telegram forwarding bot.

Run ``python3 bot.py`` for the bot, or ``python3 bot.py login <telegram-user-id>``
from a trusted Codespaces terminal to add a user-account session. OTPs and 2FA
passwords are never collected in the bot chat, and session strings are never
shown by the bot.
"""

import asyncio
import getpass
import html
import os
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    from pyrogram import Client, filters, enums, idle
    from pyrogram.errors import FloodWait
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
except ImportError:
    print("Install dependencies first: python3 -m pip install -r requirements.txt")
    raise SystemExit(1)

# Set your real Telegram numeric ID here. Send /start to the running bot once
# to see your ID in the access-denied reply. Keep this source file private.
OWNER_ID = 0
OWNER_USERNAME = "@theheads"  # placeholder; replace with your Telegram username (no spaces)

# All runtime credentials and data are kept outside the Git repository.
APP_DIR = Path.home() / ".local" / "share" / "combined-telegram-bot"
APP_DIR.mkdir(parents=True, exist_ok=True)
os.chmod(APP_DIR, 0o700)
DB_PATH = APP_DIR / "bot.sqlite3"


def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER NOT NULL,
                created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                label TEXT NOT NULL,
                api_id INTEGER NOT NULL,
                api_hash TEXT NOT NULL,
                phone TEXT NOT NULL,
                session_string TEXT NOT NULL,
                created REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_owner_idx ON sessions(owner_id);
            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA secure_delete=ON")
    os.chmod(DB_PATH, 0o600)


def get_config(key: str) -> str:
    with connect_db() as conn:
        row = conn.execute("SELECT value FROM app_config WHERE key=?", (key,)).fetchone()
    return row[0] if row else ""


def set_config(key: str, value: str) -> None:
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO app_config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value)
        )
    os.chmod(DB_PATH, 0o600)


def is_admin(user_id: int) -> bool:
    if OWNER_ID > 0 and user_id == OWNER_ID:
        return True
    with connect_db() as conn:
        return conn.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone() is not None


def admin_ids() -> list[int]:
    ids = [OWNER_ID] if OWNER_ID > 0 else []
    with connect_db() as conn:
        ids.extend(row[0] for row in conn.execute("SELECT user_id FROM admins ORDER BY user_id"))
    return ids


def user_sessions(user_id: int) -> list[sqlite3.Row]:
    with connect_db() as conn:
        return list(conn.execute(
            "SELECT id,label,api_id,phone,created FROM sessions WHERE owner_id=? ORDER BY id",
            (user_id,),
        ))


def get_session(session_id: int, user_id: int):
    with connect_db() as conn:
        return conn.execute(
            "SELECT * FROM sessions WHERE id=? AND owner_id=?", (session_id, user_id)
        ).fetchone()


def add_admin(user_id: int, added_by: int) -> bool:
    if user_id <= 0 or user_id == OWNER_ID:
        return False
    with connect_db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO admins(user_id,added_by,created) VALUES(?,?,?)",
            (user_id, added_by, time.time()),
        )
    return cur.rowcount > 0


def remove_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return False
    with connect_db() as conn:
        cur = conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    return cur.rowcount > 0


def save_session(owner_id: int, label: str, api_id: int, api_hash: str,
                 phone: str, session_string: str) -> int:
    with connect_db() as conn:
        cur = conn.execute(
            "INSERT INTO sessions(owner_id,label,api_id,api_hash,phone,session_string,created) "
            "VALUES(?,?,?,?,?,?,?)",
            (owner_id, label[:64], api_id, api_hash, phone, session_string, time.time()),
        )
        session_id = int(cur.lastrowid)
    os.chmod(DB_PATH, 0o600)
    return session_id


def delete_session(session_id: int, owner_id: int) -> bool:
    with connect_db() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE id=? AND owner_id=?", (session_id, owner_id))
    return cur.rowcount > 0


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def keyboard(*rows):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text, callback_data=data) for text, data in row]
        for row in rows
    ])


def main_keyboard():
    rows = [
        [("↗️ Forwarder", "mode_forward"), ("⬇️ Download / Upload", "mode_upload")],
        [("🔐 Login", "menu_login"), ("🗂 Manage Logins", "menu_sessions")],
        [("👤 My Details", "my_details")],
    ]
    rows.append([("➕ Add Admin", "admin_add")])
    return keyboard(*rows)


def profile_name(message: Message) -> str:
    user = message.from_user
    return " ".join(x for x in (user.first_name, user.last_name) if x) or "there"


async def say_access_denied(message: Message):
    uid = message.from_user.id
    await message.reply_text(
        "👋 <b>Welcome to Telegram Copy Assistant</b>\n\n"
        f"🆔 Your Telegram ID: <code>{uid}</code>\n\n"
        "🔒 This bot is available to approved admins only.\n"
        f"Please contact <a href=\"https://t.me/{OWNER_USERNAME.lstrip('@')}\">"
        f"{esc(OWNER_USERNAME)}</a> to request access.",
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )


# Account sessions are cached only in this process and scoped to their Telegram
# owner. No bot UI action can inspect another admin's session.
USER_CLIENTS: dict[int, Client] = {}
USER_CLIENT_LOCK = asyncio.Lock()
BOT_TOKEN = ""
bot: Client | None = None
FLOW: dict[int, dict] = {}
RUNNING: dict[int, asyncio.Task] = {}
CANCEL: dict[int, asyncio.Event] = {}


def get_session_client(session_id: int, owner_id: int) -> Client:
    row = get_session(session_id, owner_id)
    if not row:
        raise ValueError("Saved login was not found for this Telegram user.")
    client = USER_CLIENTS.get(session_id)
    if client and client.is_connected:
        return client
    client = Client(
        f"combined_user_{session_id}",
        api_id=int(row["api_id"]),
        api_hash=row["api_hash"],
        session_string=row["session_string"],
        in_memory=True,
        no_updates=True,
        sleep_threshold=60,
    )
    USER_CLIENTS[session_id] = client
    return client


async def start_session_client(session_id: int, owner_id: int) -> Client:
    async with USER_CLIENT_LOCK:
        client = get_session_client(session_id, owner_id)
        if not client.is_connected:
            await client.start()
        return client


async def terminal_login(owner_id: int):
    print(f"\nAdding a private Telegram login for bot user ID {owner_id}.")
    api_id = int(input("Telegram API ID: ").strip())
    api_hash = getpass.getpass("Telegram API Hash: ").strip()
    phone = input("Phone number with country code (for example +91...): ").strip()
    if not api_hash or not phone:
        raise ValueError("API hash and phone number are required.")

    client = Client("combined_terminal_login", api_id=api_id, api_hash=api_hash,
                    in_memory=True, no_updates=True)
    try:
        await client.connect()
        sent = await client.send_code(phone)
        print("Enter the code here in this trusted terminal, not in Telegram chat.")
        code = getpass.getpass("Telegram login code (spaces are okay): ")
        code = re.sub(r"\s+", "", code)
        try:
            user = await client.sign_in(phone, sent.phone_code_hash, code)
        except Exception as exc:
            if type(exc).__name__ != "SessionPasswordNeeded":
                raise
            user = await client.check_password(getpass.getpass("Telegram 2FA password: "))
        session_string = await client.export_session_string()
        label = input(f"Login label [{user.first_name}]: ").strip() or user.first_name
    finally:
        if client.is_connected:
            await client.disconnect()

    session_id = save_session(owner_id, label, api_id, api_hash, phone, session_string)
    print(f"\n✅ Login saved privately as '{label}' (profile #{session_id}).")
    print("The session string is stored in the local private database and was not printed.")


def parse_cli():
    if len(sys.argv) >= 2 and sys.argv[1].lower() == "login":
        if len(sys.argv) < 3 or not sys.argv[2].isdigit():
            print("Usage: python3 bot.py login <your-telegram-user-id>")
            raise SystemExit(2)
        init_db()
        try:
            asyncio.run(terminal_login(int(sys.argv[2])))
        except KeyboardInterrupt:
            print("\nLogin cancelled.")
        except Exception as exc:
            print(f"Login failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            raise SystemExit(1)
        raise SystemExit(0)


parse_cli()


# Menu helpers are defined before Client so callback handlers use this one bot.
def session_picker_rows(user_id: int, mode: str):
    rows = []
    for row in user_sessions(user_id):
        label = f"{row['label']} · {row['phone'][-4:]}"
        callback = f"view_session_{row['id']}" if mode == "login" else f"use_{mode}_{row['id']}"
        rows.append([(label[:60], callback)])
    rows.append([("➕ Add login in terminal", "menu_login"), ("◀ Menu", "menu_home")])
    return rows


if __name__ == "__main__":
    init_db()
    try:
        saved_id = get_config("bot_api_id")
        BOT_API_ID = int(saved_id or input("Telegram API ID: ").strip())
        if not saved_id:
            set_config("bot_api_id", str(BOT_API_ID))
        BOT_API_HASH = get_config("bot_api_hash")
        if not BOT_API_HASH:
            BOT_API_HASH = getpass.getpass("Telegram API Hash: ").strip()
            if not BOT_API_HASH:
                raise ValueError("Telegram API Hash is required.")
            set_config("bot_api_hash", BOT_API_HASH)
        BOT_TOKEN = getpass.getpass("BotFather bot token: ").strip()
        if not BOT_TOKEN:
            raise ValueError("Bot token is required.")
    except (KeyboardInterrupt, EOFError, ValueError) as exc:
        print(f"Setup stopped: {exc}", file=sys.stderr)
        raise SystemExit(2)
else:
    BOT_API_ID = int(os.environ.get("API_ID", "0") or 0)
    BOT_API_HASH = os.environ.get("API_HASH", "")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

bot = Client("combined_bot", api_id=BOT_API_ID, api_hash=BOT_API_HASH,
             bot_token=BOT_TOKEN, in_memory=True, workers=16, sleep_threshold=60)
init_db()


@bot.on_message(filters.command("start") & filters.private)
async def start_message(c: Client, m: Message):
    if not is_admin(m.from_user.id):
        await say_access_denied(m)
        return
    await m.reply_text(
        f"👋 <b>Welcome, {esc(profile_name(m))}!</b>\n"
        f"🆔 Your ID: <code>{m.from_user.id}</code>\n\n"
        "Please select an option:",
        reply_markup=main_keyboard(),
        parse_mode=enums.ParseMode.HTML,
    )


@bot.on_message(filters.command("id") & filters.private)
async def show_id(c: Client, m: Message):
    await m.reply_text(f"Your Telegram ID: <code>{m.from_user.id}</code>", parse_mode=enums.ParseMode.HTML)


@bot.on_message(filters.command("addadmin") & filters.private)
async def cmd_add_admin(c: Client, m: Message):
    if not is_admin(m.from_user.id):
        await say_access_denied(m)
        return
    parts = (m.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await m.reply_text("Usage: <code>/addadmin 123456789</code>", parse_mode=enums.ParseMode.HTML)
        return
    ok = add_admin(int(parts[1]), m.from_user.id)
    await m.reply_text("✅ Admin added." if ok else "ℹ️ That ID is already an admin or invalid.")


@bot.on_message(filters.command("removeadmin") & filters.private)
async def cmd_remove_admin(c: Client, m: Message):
    if not is_admin(m.from_user.id):
        await say_access_denied(m)
        return
    parts = (m.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await m.reply_text("Usage: <code>/removeadmin 123456789</code>", parse_mode=enums.ParseMode.HTML)
        return
    await m.reply_text("✅ Admin removed." if remove_admin(int(parts[1])) else "Admin not found.")


@bot.on_callback_query(filters.regex(r"^(menu_home|my_details|menu_login|menu_sessions|admin_add|mode_forward|mode_upload)$"))
async def menu_callbacks(c: Client, q: CallbackQuery):
    uid = q.from_user.id
    if not is_admin(uid):
        await q.answer("Admin access only.", show_alert=True)
        return
    action = q.data
    await q.answer()
    if action == "menu_home":
        FLOW.pop(uid, None)
        await q.message.edit_text(
            f"👋 <b>Welcome, {esc(q.from_user.first_name)}!</b>\n🆔 Your ID: <code>{uid}</code>\n\nPlease select an option:",
            reply_markup=main_keyboard(), parse_mode=enums.ParseMode.HTML)
    elif action == "my_details":
        sessions = user_sessions(uid)
        username = f"@{q.from_user.username}" if q.from_user.username else "not set"
        await q.message.edit_text(
            f"👤 <b>My Details</b>\n\nName: {esc(q.from_user.first_name)}\n"
            f"Username: {esc(username)}\nTelegram ID: <code>{uid}</code>\n"
            f"Role: {'Owner' if uid == OWNER_ID else 'Admin'}\nSaved logins: {len(sessions)}",
            reply_markup=keyboard([("◀ Menu", "menu_home")]), parse_mode=enums.ParseMode.HTML)
    elif action == "menu_login":
        rows = session_picker_rows(uid, "login")
        text = (
            "🔐 <b>Your saved logins</b>\n\n"
            "Choose a saved account, or open a second trusted Codespaces terminal and run:\n"
            f"<code>python3 bot.py login {uid}</code>\n\n"
            "For account safety, never send login codes or 2FA passwords to this bot."
        )
        await q.message.edit_text(text, reply_markup=keyboard(*rows), parse_mode=enums.ParseMode.HTML)
    elif action == "menu_sessions":
        rows = []
        for row in user_sessions(uid):
            rows.append([(f"🗂 {row['label']} · ••••{row['phone'][-4:]}", f"view_session_{row['id']}")])
        rows.append([("➕ Add login in terminal", "menu_login"), ("◀ Menu", "menu_home")])
        await q.message.edit_text(
            "🗂 <b>Manage your saved logins</b>\n\n"
            "Session strings and API hashes are never displayed in Telegram.",
            reply_markup=keyboard(*rows), parse_mode=enums.ParseMode.HTML)
    elif action == "admin_add":
        FLOW[uid] = {"step": "admin_id"}
        await q.message.edit_text(
            "➕ Send the numeric Telegram ID to add as an admin.\n\nUse /cancel to stop.",
            reply_markup=keyboard([("Cancel", "menu_home")]), parse_mode=enums.ParseMode.HTML)
    elif action in ("mode_forward", "mode_upload"):
        mode = "forward" if action == "mode_forward" else "upload"
        rows = session_picker_rows(uid, mode)
        if not user_sessions(uid):
            await q.message.edit_text(
                "🔐 No saved login yet. Open a second trusted Codespaces terminal and run:\n"
                f"<code>python3 bot.py login {uid}</code>",
                reply_markup=keyboard([("🔐 Login", "menu_login"), ("◀ Menu", "menu_home")]),
                parse_mode=enums.ParseMode.HTML)
        else:
            title = "Forward messages" if mode == "forward" else "Download and upload allowed content"
            await q.message.edit_text(
                f"{title}\n\nSelect the Telegram account to use:",
                reply_markup=keyboard(*rows), parse_mode=enums.ParseMode.HTML)


@bot.on_callback_query(filters.regex(r"^use_(forward|upload)_(\d+)$"))
async def choose_account(c: Client, q: CallbackQuery):
    uid = q.from_user.id
    if not is_admin(uid):
        await q.answer("Admin access only.", show_alert=True)
        return
    mode, session_id = q.matches[0].group(1), int(q.matches[0].group(2))
    if not get_session(session_id, uid):
        await q.answer("Saved login not found.", show_alert=True)
        return
    FLOW[uid] = {"mode": mode, "step": "source", "session_id": session_id}
    await q.answer()
    await q.message.edit_text(
        "📥 Send the source chat ID or @username.\n"
        "The selected account must already be able to read that chat.\n\nUse /cancel to stop.",
        reply_markup=keyboard([("Cancel", "menu_home")]), parse_mode=enums.ParseMode.HTML)


@bot.on_callback_query(filters.regex(r"^view_session_(\d+)$"))
async def view_session(c: Client, q: CallbackQuery):
    uid = q.from_user.id
    if not is_admin(uid):
        await q.answer("Admin access only.", show_alert=True)
        return
    sid = int(q.matches[0].group(1))
    row = get_session(sid, uid)
    if not row:
        await q.answer("Login not found.", show_alert=True)
        return
    await q.answer()
    await q.message.edit_text(
        f"🗂 <b>{esc(row['label'])}</b>\n\n"
        f"API ID: <code>{row['api_id']}</code>\n"
        f"Phone: <code>••••{esc(row['phone'][-4:])}</code>\n"
        "API hash: <i>hidden for security</i>\n"
        "Session string: <i>hidden for security</i>",
        reply_markup=keyboard([("🗑 Delete this login", f"delete_session_{sid}"), ("◀ Logins", "menu_sessions")]),
        parse_mode=enums.ParseMode.HTML)


@bot.on_callback_query(filters.regex(r"^delete_session_(\d+)$"))
async def delete_session_callback(c: Client, q: CallbackQuery):
    uid = q.from_user.id
    if not is_admin(uid):
        await q.answer("Admin access only.", show_alert=True)
        return
    sid = int(q.matches[0].group(1))
    client = USER_CLIENTS.pop(sid, None)
    if client and client.is_connected:
        try:
            await client.stop()
        except Exception:
            pass
    removed = delete_session(sid, uid)
    await q.answer("Login deleted." if removed else "Login not found.", show_alert=True)
    await q.message.edit_text(
        "🗂 <b>Manage your saved logins</b>\n\nLogin deleted. Session string was removed from the local store.",
        reply_markup=keyboard([("◀ Menu", "menu_home")]), parse_mode=enums.ParseMode.HTML)


@bot.on_callback_query(filters.regex(r"^range_(az|fast|special)$"))
async def select_range(c: Client, q: CallbackQuery):
    uid = q.from_user.id
    if not is_admin(uid):
        await q.answer("Admin access only.", show_alert=True)
        return
    flow = FLOW.get(uid)
    if not flow or flow.get("step") != "range":
        await q.answer("This setup expired. Start again.", show_alert=True)
        return
    action = q.matches[0].group(1)
    await q.answer()
    if action == "special":
        flow["step"] = "link"
        await q.message.edit_text(
            "🔗 Send a Telegram post link from the source chat.\n"
            "Messages from that post onward will be processed.",
            reply_markup=keyboard([("Cancel", "menu_home")]), parse_mode=enums.ParseMode.HTML)
        return
    order = "newest" if action == "fast" else "oldest"
    FLOW.pop(uid, None)
    await begin_job(uid, flow, start_id=None, order=order, progress_message=q.message)


def parse_post_link(text: str, source_chat) -> int:
    parsed = urlparse(text.strip() if "://" in text else "https://" + text.strip())
    host = parsed.netloc.lower().split(":", 1)[0]
    if host not in {"t.me", "telegram.me", "www.t.me"}:
        raise ValueError("Send a valid t.me post link.")
    parts = [x for x in parsed.path.strip("/").split("/") if x]
    if parts[:1] == ["s"]:
        parts = parts[1:]
    if len(parts) < 2:
        raise ValueError("The link must point to a specific post.")
    if parts[0] == "c":
        if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
            raise ValueError("Private post link format should be t.me/c/CHAT_ID/MESSAGE_ID.")
        internal = str(source_chat.id)
        internal = internal[4:] if internal.startswith("-100") else ""
        if internal and internal != parts[1]:
            raise ValueError("That post link is from a different chat than the selected source.")
        return int(parts[2])
    if not parts[-1].isdigit():
        raise ValueError("The link must end in a message number.")
    source_username = (getattr(source_chat, "username", "") or "").lower()
    if source_username and parts[0].lower() != source_username:
        raise ValueError("That post link is from a different public chat.")
    return int(parts[-1])


async def begin_job(user_id: int, flow: dict, start_id: int | None,
                    order: str, progress_message: Message):
    old = RUNNING.get(user_id)
    if old and not old.done():
        await progress_message.reply_text("A task is already running for you. Please wait or cancel it.")
        return
    event = asyncio.Event()
    CANCEL[user_id] = event
    task = asyncio.create_task(run_copy_job(user_id, flow, start_id, order, progress_message, event))
    RUNNING[user_id] = task


@bot.on_callback_query(filters.regex(r"^cancel_job$"))
async def cancel_job(c: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        await q.answer("Admin access only.", show_alert=True)
        return
    event = CANCEL.get(q.from_user.id)
    if event:
        event.set()
        await q.answer("Cancel requested.", show_alert=True)
    else:
        await q.answer("No running task.", show_alert=True)


def protected_chat(chat) -> bool:
    return bool(getattr(chat, "has_protected_content", False))


def protected_message(message) -> bool:
    return bool(getattr(message, "has_protected_content", False)) or protected_chat(message.chat)


def media_kind(message):
    for name in ("video", "document", "photo", "audio", "voice", "animation", "sticker"):
        item = getattr(message, name, None)
        if item:
            return name, item
    return None, None


async def copy_as_upload(client: Client, message, destination):
    # Protected messages are rejected before this function; never use download/upload
    # as a way to bypass Telegram's protected-content or no-forward restrictions.
    if message.text and not media_kind(message)[0]:
        return await client.send_message(destination, message.text, entities=message.entities)
    kind, media = media_kind(message)
    if not kind:
        return None
    suffix = Path(getattr(media, "file_name", "") or "").suffix
    if not suffix:
        suffix = {"video": ".mp4", "photo": ".jpg", "audio": ".mp3",
                  "voice": ".ogg", "animation": ".mp4", "sticker": ".webp"}.get(kind, ".bin")
    fd, path = tempfile.mkstemp(prefix="tgcopy_", suffix=suffix, dir=str(APP_DIR))
    os.close(fd)
    os.unlink(path)
    try:
        downloaded = await client.download_media(message, file_name=path)
        if not downloaded:
            raise RuntimeError("Telegram did not provide a downloadable file.")
        caption = message.caption or ""
        entities = message.caption_entities or None
        common = {"chat_id": destination, "caption": caption, "caption_entities": entities}
        if kind == "video":
            return await client.send_video(**common, video=downloaded,
                duration=media.duration or 0, width=media.width or 0, height=media.height or 0,
                supports_streaming=True)
        if kind == "document":
            return await client.send_document(**common, document=downloaded,
                file_name=getattr(media, "file_name", None))
        if kind == "photo":
            return await client.send_photo(**common, photo=downloaded)
        if kind == "audio":
            return await client.send_audio(**common, audio=downloaded,
                duration=getattr(media, "duration", 0) or 0)
        if kind == "voice":
            return await client.send_voice(**common, voice=downloaded,
                duration=getattr(media, "duration", 0) or 0)
        if kind == "animation":
            return await client.send_animation(**common, animation=downloaded)
        if kind == "sticker":
            return await client.send_sticker(chat_id=destination, sticker=downloaded)
    finally:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


async def edit_progress(message: Message, text: str):
    try:
        await message.edit_text(text, reply_markup=keyboard([("⏹ Cancel", "cancel_job")]),
                                parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass


async def run_copy_job(user_id: int, flow: dict, start_id: int | None,
                       order: str, progress_message: Message, cancel_event: asyncio.Event):
    session_id = int(flow["session_id"])
    mode = flow["mode"]
    try:
        client = await start_session_client(session_id, user_id)
        source = await client.get_chat(flow["source_id"])
        destination = await client.get_chat(flow["destination_id"])
        if protected_chat(source):
            await edit_progress(progress_message,
                "🚫 This chat protects its content. Telegram forwarding and download/upload are not available for protected content.")
            return

        sent = skipped = failed = 0
        last_update = time.monotonic()
        blocked = False

        async def transfer_one(message):
            nonlocal sent, skipped, failed, blocked
            if protected_message(message):
                skipped += 1
                return
            try:
                if mode == "forward":
                    await client.forward_messages(destination.id, source.id, message.id)
                else:
                    result = await copy_as_upload(client, message, destination.id)
                    if result is None:
                        skipped += 1
                        return
                sent += 1
            except FloodWait as exc:
                # Telegram-imposed FloodWait cannot be safely bypassed.
                await asyncio.sleep(int(exc.value) + 1)
                try:
                    if mode == "forward":
                        await client.forward_messages(destination.id, source.id, message.id)
                    else:
                        result = await copy_as_upload(client, message, destination.id)
                        if result is None:
                            skipped += 1
                            return
                    sent += 1
                except Exception as retry_exc:
                    failed += 1
                    if "forwardsrestricted" in type(retry_exc).__name__.lower():
                        blocked = True
            except Exception as exc:
                failed += 1
                name = type(exc).__name__.lower()
                if "forwardsrestricted" in name or "protected" in name:
                    blocked = True

        if order == "newest" and start_id is None:
            # Telegram supplies newest-first history. Stream and copy as pages
            # arrive, so the user does not wait for a full-channel count first.
            await edit_progress(progress_message, "⚡ Latest-first streaming started; total will grow while scanning.")
            scanned = 0
            async for message in client.get_chat_history(source.id):
                if cancel_event.is_set() or blocked:
                    break
                scanned += 1
                await transfer_one(message)
                now = time.monotonic()
                if scanned % 10 == 0 or now - last_update >= 3:
                    state = "Cancelled" if cancel_event.is_set() else "Streaming latest-first"
                    await edit_progress(progress_message,
                        f"⚡ {state}: <b>{sent}/~{scanned}</b> scanned\n"
                        f"⏭ Skipped: {skipped} · ❌ Failed: {failed}")
                    last_update = now
            total = scanned
        else:
            # A→Z and a specific starting post need chronological ordering, so
            # scan that range first, then sort and send oldest-to-newest.
            await edit_progress(progress_message, "🔎 Scanning source history… total is not known yet.")
            messages = []
            scanned = 0
            async for message in client.get_chat_history(source.id):
                if cancel_event.is_set():
                    break
                if start_id is not None and message.id < start_id:
                    break
                messages.append(message)
                scanned += 1
                now = time.monotonic()
                if scanned % 100 == 0 or now - last_update >= 4:
                    await edit_progress(progress_message,
                        f"🔎 Scanning… <b>{scanned}</b> messages checked; total is still being counted.")
                    last_update = now
            if cancel_event.is_set():
                await edit_progress(progress_message, f"⏹ Cancelled during scan. Scanned {scanned} messages.")
                return
            messages.sort(key=lambda item: item.id)
            total = len(messages)
            await edit_progress(progress_message, f"📦 Found <b>{total}</b> messages. Starting oldest-first transfer…")
            for index, message in enumerate(messages, start=1):
                if cancel_event.is_set() or blocked:
                    break
                await transfer_one(message)
                now = time.monotonic()
                if index % 10 == 0 or now - last_update >= 3 or index == total:
                    await edit_progress(progress_message,
                        f"⚡ Copying: <b>{sent}/{total}</b>\n"
                        f"⏭ Skipped: {skipped} · ❌ Failed: {failed}")
                    last_update = now

        if blocked:
            await edit_progress(progress_message,
                "🚫 Telegram restricts forwarding for this source. Protected content cannot be copied using download/upload either.")
            return
        final = "⏹ Cancelled" if cancel_event.is_set() else "✅ Complete"
        await edit_progress(progress_message,
            f"{final}\n✅ Sent: <b>{sent}/{total}</b>\n"
            f"⏭ Skipped: {skipped} · ❌ Failed: {failed}")
    except Exception as exc:
        await edit_progress(progress_message, f"❌ Task failed: {esc(type(exc).__name__)}: {esc(exc)}")
    finally:
        RUNNING.pop(user_id, None)
        CANCEL.pop(user_id, None)


@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_command(c: Client, m: Message):
    uid = m.from_user.id
    FLOW.pop(uid, None)
    event = CANCEL.get(uid)
    if event:
        event.set()
        await m.reply_text("⏹ Cancellation requested.")
    else:
        await m.reply_text("Setup cancelled. No task is running.")



@bot.on_message(filters.text & filters.private & ~filters.command(["start", "id", "addadmin", "removeadmin", "cancel"]))
async def receive_text(c: Client, m: Message):
    uid = m.from_user.id
    if not is_admin(uid):
        await say_access_denied(m)
        return
    flow = FLOW.get(uid)
    if not flow:
        await m.reply_text("Use /start to open the menu.")
        return
    text = (m.text or "").strip()
    if text.lower() == "/cancel":
        FLOW.pop(uid, None)
        await m.reply_text("Cancelled.", reply_markup=main_keyboard())
        return

    if flow.get("step") == "admin_id":
        if not text.isdigit():
            await m.reply_text("Send a numeric Telegram ID, or /cancel.")
            return
        added = add_admin(int(text), uid)
        FLOW.pop(uid, None)
        await m.reply_text("✅ Admin added." if added else "ℹ️ Already admin or invalid ID.",
                           reply_markup=main_keyboard())
        return

    client = await start_session_client(int(flow["session_id"]), uid)
    if flow.get("step") == "source":
        try:
            source = await client.get_chat(int(text) if text.lstrip("-").isdigit() else text)
        except Exception as exc:
            await m.reply_text(f"❌ Source chat not found or not accessible: {esc(exc)}")
            return
        flow.update(step="destination", source_id=source.id, source_title=source.title or source.first_name or str(source.id))
        await m.reply_text(
            f"✅ Source: <b>{esc(flow['source_title'])}</b> (<code>{source.id}</code>)\n\n"
            "Send the destination chat ID or @username. The selected account must be an admin there and allowed to post.",
            parse_mode=enums.ParseMode.HTML)
    elif flow.get("step") == "destination":
        try:
            destination = await client.get_chat(int(text) if text.lstrip("-").isdigit() else text)
            me = await client.get_me()
            member = await client.get_chat_member(destination.id, me.id)
            status = getattr(member.status, "value", str(member.status)).lower().split(".")[-1]
            if status not in {"administrator", "owner", "creator"}:
                raise ValueError("The selected Telegram account must be an admin in the destination.")
            privileges = getattr(member, "privileges", None)
            if privileges is not None and getattr(privileges, "can_post_messages", True) is False:
                raise ValueError("The selected account does not have permission to post in that channel.")
        except Exception as exc:
            await m.reply_text(f"❌ Destination check failed: {esc(exc)}")
            return
        flow.update(step="range", destination_id=destination.id,
                    destination_title=destination.title or destination.first_name or str(destination.id))
        await m.reply_text(
            f"✅ Destination admin check passed: <b>{esc(flow['destination_title'])}</b>\n\n"
            "Choose where to start:",
            reply_markup=keyboard(
                [("A → Z (oldest first)", "range_az")],
                [("⚡ Latest first (faster)", "range_fast")],
                [("🔗 From a specific post link", "range_special")],
                [("Cancel", "menu_home")],
            ),
            parse_mode=enums.ParseMode.HTML)
    elif flow.get("step") == "link":
        try:
            source = await client.get_chat(flow["source_id"])
            start_id = parse_post_link(text, source)
        except Exception as exc:
            await m.reply_text(f"❌ Invalid source post link: {esc(exc)}")
            return
        FLOW.pop(uid, None)
        progress = await m.reply_text("🔎 Starting the selected range…", reply_markup=keyboard([("⏹ Cancel", "cancel_job")]))
        await begin_job(uid, flow, start_id=start_id, order="oldest", progress_message=progress)


async def main():
    init_db()
    if not BOT_TOKEN:
        raise RuntimeError("Enter a valid BotFather token in the terminal.")
    await bot.start()
    me = await bot.get_me()
    print(f"Bot @{me.username} is online. Master owner ID: {OWNER_ID}.")
    try:
        await idle()
    finally:
        for client in list(USER_CLIENTS.values()):
            if client.is_connected:
                try:
                    await client.stop()
                except Exception:
                    pass
        await bot.stop()


if __name__ == "__main__":
    try:
        bot.run(main())
    except KeyboardInterrupt:
        pass
