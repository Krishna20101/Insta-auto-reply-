"""selftest_bot.py — github_bot/bot.py ka engine + UI test (fake Telegram)."""
import asyncio
import builtins
import importlib.util
import json
import os
import sys

os.environ.update({
    "API_ID": "123456", "API_HASH": "a" * 32, "BOT_TOKEN": "1:TEST",
    "SESSION_STRING": "B" + "x" * 350, "ADMIN_IDS": "111",
})

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f"   {extra}" if extra and not cond else ""))


# ── fakes ──
class M:
    def __init__(self, mid, chat, mtype=None, size=350 * 1024 * 1024, text=None):
        self.id, self.chat, self.text = mid, chat, text
        self.caption = f"cap-{mid}"
        self.caption_entities, self.entities = [], None
        self.empty = False
        for a in ("video", "document", "photo", "audio", "voice", "animation", "sticker"):
            setattr(self, a, None)
        if mtype:
            o = type("O", (), {})()
            o.file_id = f"fid-{mtype}-{mid}"
            o.file_unique_id = f"uniq-{mtype}-{mid}"
            o.file_size = size
            o.file_name = f"f{mid}.mp4" if mtype == "video" else f"f{mid}.pdf"
            o.duration = o.width = o.height = 0
            setattr(self, mtype, o)


class Chat:
    def __init__(self, cid, title="Chat"):
        self.id, self.title = cid, title


class FakeAcc:
    copy_fails = set()
    write_fails = set()
    dl_sleep = 0

    def __init__(self, history):
        self.history = history
        self.copied, self.fid_sent, self.uploaded = [], [], []
        self.downloaded = 0

    async def get_me(self):
        return type("U", (), {"id": 999, "first_name": "Owner", "username": "owner"})()

    async def get_chat(self, x):
        if str(x).lower() in ("me", "self"):
            return Chat("me", "Saved")
        if isinstance(x, int) or str(x).lstrip("-").isdigit():
            return Chat(int(x), f"Chat {x}")
        raise ValueError(f"no chat {x}")

    def get_dialogs(self, limit=100):
        async def gen():
            yield type("D", (), {"chat": Chat(-1001111, "A")})()
            yield type("D", (), {"chat": None})()
        return gen()

    async def get_chat_member(self, cid, uid):
        st = "member" if cid in FakeAcc.write_fails else "administrator"
        return type("M", (), {"status": st, "privileges": None})()

    async def copy_message(self, chat_id, from_chat_id, message_id):
        if chat_id in FakeAcc.write_fails:
            from pyrogram.errors import ChatWriteForbidden
            raise ChatWriteForbidden()
        if from_chat_id in FakeAcc.copy_fails:
            raise RuntimeError("CHAT_FORWARDS_RESTRICTED")
        self.copied.append((chat_id, message_id))

    async def _fid(self, store, kind, kw):
        if kw.get("chat_id") in FakeAcc.write_fails:
            from pyrogram.errors import ChatWriteForbidden
            raise ChatWriteForbidden()
        v = kw.get(kind) if kind in kw else kw.get("document")
        if isinstance(v, str) and v.startswith("fid-"):
            store.append((kw.get("chat_id"), v, kw.get("caption")))
        else:
            self.uploaded.append((kind, kw.get("chat_id")))

    async def send_video(self, **kw): await self._fid(self.fid_sent, "video", kw)
    async def send_document(self, **kw): await self._fid(self.fid_sent, "document", kw)
    async def send_photo(self, **kw): await self._fid(self.fid_sent, "photo", kw)
    async def send_audio(self, **kw): await self._fid(self.fid_sent, "audio", kw)
    async def send_voice(self, **kw): await self._fid(self.fid_sent, "voice", kw)
    async def send_animation(self, **kw): await self._fid(self.fid_sent, "animation", kw)
    async def send_sticker(self, **kw): await self._fid(self.fid_sent, "sticker", kw)

    async def send_message(self, chat_id, text, **kw):
        m = M(1, Chat(chat_id), text=text)
        async def _del():
            return True
        m.delete = _del
        self.copied.append((chat_id, "text"))
        return m

    async def download_media(self, msg, file_name=None, progress=None):
        self.downloaded += 1
        if FakeAcc.dl_sleep:
            await asyncio.sleep(FakeAcc.dl_sleep)
        with open(file_name, "wb") as f:
            f.write(b"x" * 64)
        return file_name

    def get_chat_history(self, chat, limit=0, **kw):
        async def gen():
            msgs = self.history
            for m in (msgs[:limit] if limit else msgs):
                yield m
        return gen()

    async def get_messages(self, chat, ids):
        ids = ids if isinstance(ids, list) else [ids]
        return [m for m in self.history if m.id in ids]


def history(n=60, chat=-1001111111111):
    msgs = []
    for i in range(1, n + 1):
        if i % 7 == 0:
            msgs.append(M(i, Chat(chat), text=f"t{i}"))
        elif i % 5 == 0:
            msgs.append(M(i, Chat(chat), mtype="document", size=2 * 1024 * 1024))
        else:
            msgs.append(M(i, Chat(chat), mtype="video"))
    return list(reversed(msgs))


async def main():
    spec = importlib.util.spec_from_file_location("cbot", os.path.join(HERE, "bot.py"))
    B = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(B)

    print("\n=== 1. import + config ===")
    check("bot.py load hua", B.API_ID == 123456)
    check("settings default parallel>=1", B.SETTINGS["parallel"] >= 1)
    check("hr() sahi", B.hr(350 * 1024 ** 2) == "350.0MB")
    check("pbar() sahi", "█" in B.pbar(5, 10))

    print("\n=== 2. state / resume layer ===")
    for f in B.DATA.glob("state_*.json"):
        f.unlink()
    st = B.state_load(-1001111111111, "me")
    B.mark(st, 5, True)
    B.state_save(st, push=False)
    st2 = B.state_load(-1001111111111, "me")
    check("state save/load", st2["files"]["5"]["status"] == "done")
    check("summary", B.summary(st2)["done"] == 1)
    check("atomic write (tmp nahi bacha)", not list(B.DATA.glob("state_*.tmp")))

    print("\n=== 3. scan engine ===")
    B.SETTINGS["delay"] = 0
    acc = FakeAcc(history())
    got = await B.scan_media(acc, -1001111111111, 10)
    check("scan 10 files", len(got) == 10, str(len(got)))
    check("ascending order", [m.id for m in got] == sorted(m.id for m in got))
    check("text skip", all(B.get_media(m)[0] for m in got))
    docs = await B.scan_media(acc, -1001111111111, 5, only="document")
    check("filter document", all(B.get_media(m)[0] == "document" for m in docs) and len(docs) == 5)
    every = await B.scan_media(acc, -1001111111111, 0)
    check("all mode", len(every) == len([m for m in history() if B.get_media(m)[0]]))

    print("\n=== 4. copy engine: 0-data paths ===")
    FakeAcc.copy_fails = set()
    st = B.state_load(-1001111111111, "me")
    cnt = {"server": 0, "cache": 0, "transfer": 0, "bytes": 0}
    bud = B.Budget(0, 0)
    m = [x for x in history() if x.id == 2][0]
    ok = await B.copy_one(acc, -100777, m, bud, st, cnt)
    check("server-side copy chala", ok and cnt["server"] == 1, str(cnt))
    check("download nahi hua", acc.downloaded == 0)

    FakeAcc.copy_fails = {-1001111111111}
    acc2 = FakeAcc(history())
    cnt2 = {"server": 0, "cache": 0, "transfer": 0, "bytes": 0}
    ok = await B.copy_one(acc2, -100777, m, B.Budget(0, 0), st, cnt2)
    check("restricted -> file_id (0 data)", ok and cnt2["cache"] == 1, str(cnt2))
    check("file_id me caption gaya", acc2.fid_sent and acc2.fid_sent[0][2] == "cap-2",
          str(acc2.fid_sent[:1]))

    print("\n=== 5. multi-task engine (2 tasks ek saath) ===")
    for f in B.DATA.glob("state_*.json"):
        f.unlink()
    FakeAcc.copy_fails = set()
    fake = FakeAcc(history())
    B._acc = fake

    async def fake_acc_client():
        return fake
    B.acc_client = fake_acc_client
    B.SETTINGS.update({"parallel": 2, "batch_size": 5, "delay": 0, "max_data_gb": 0,
                       "disk_reserve_gb": 0, "max_tasks": 3, "global_concurrency": 4})

    for tid in (1, 2):
        B.TASKS[tid] = {"id": tid, "src": -1001111111111, "src_title": "src",
                        "dsts": [(-100777, "g1")] if tid == 1 else [("me", "Saved")],
                        "want": 8, "only": None, "parallel": 2, "batch": 5,
                        "status": "queued", "done": 0, "total": 0, "failed": 0, "bytes": 0,
                        "modes": {"server": 0, "cache": 0, "transfer": 0},
                        "created": 0, "elapsed": 0, "note": "", "runner": None}
    await asyncio.gather(B.run_task(1), B.run_task(2))
    t1, t2 = B.TASKS[1], B.TASKS[2]
    check("task 1 done", t1["status"] == "done", t1["status"])
    check("task 2 done", t2["status"] == "done", t2["status"])
    check("task1 ne 8 files bheji", t1["done"] == 8, f"{t1['done']}")
    check("task2 me Saved Messages", any(c[0] == "me" for c in fake.copied))
    check("0 data (koi download nahi)", fake.downloaded == 0, f"{fake.downloaded}")

    print("\n=== 6. pause / resume / cancel ===")
    B.TASKS[3] = dict(B.TASKS[1], id=3, dsts=[(-100888, "g2")], status="queued",
                      done=0, total=0, failed=0, runner=None, cancel=False, paused=False,
                      modes={"server": 0, "cache": 0, "transfer": 0})
    B.start_task(3)
    await asyncio.sleep(0.02)
    B.TASKS[3]["paused"] = True
    await asyncio.sleep(0.4)
    paused_done = B.TASKS[3]["done"]
    B.TASKS[3]["paused"] = False
    await asyncio.sleep(0.6)
    check("pause ke baad aage badha", B.TASKS[3]["done"] >= paused_done)
    B.TASKS[3]["cancel"] = True
    await asyncio.sleep(0.4)
    check("cancel kaam kiya", B.TASKS[3]["status"] in ("cancelled", "stopped"), B.TASKS[3]["status"])

    print("\n=== 7. no-duplicate on restart ===")
    before = {int(k) for k, v in B.state_load(-1001111111111, -100777)["files"].items()
              if v.get("status") == "done"}
    fake.copied.clear()
    B.TASKS[4] = dict(B.TASKS[1], id=4, status="queued", done=0, total=0, failed=0,
                      runner=None, cancel=False, paused=False,
                      modes={"server": 0, "cache": 0, "transfer": 0})
    await B.run_task(4)
    resent = [mid for _, mid in fake.copied if isinstance(mid, int)]
    dupes = [x for x in resent if x in before]
    check("restart par duplicate nahi", not dupes, f"dupes={dupes}")
    check("restart me sab done", B.TASKS[4]["status"] == "done", B.TASKS[4]["status"])

    print("\n=== 8. data limit / write-forbidden guards ===")
    FakeAcc.copy_fails = {-1001111111111}
    FakeAcc.write_fails = {-100999}
    B.SETTINGS["max_data_gb"] = 0
    B.TASKS[5] = dict(B.TASKS[1], id=5, dsts=[(-100999, "no-write")], status="queued",
                      done=0, total=0, failed=0, runner=None, cancel=False, paused=False,
                      modes={"server": 0, "cache": 0, "transfer": 0})
    await B.run_task(5)
    check("write-forbidden par task stopped (350GB bacha)",
          B.TASKS[5]["status"] in ("stopped", "error"), B.TASKS[5]["status"])
    check("download par nahi gira", fake.downloaded == 0, str(fake.downloaded))
    FakeAcc.write_fails = set()
    FakeAcc.copy_fails = set()

    print("\n=== 9. UI render (HTML valid) ===")
    for txt in (B.build_progress(), B.task_text(B.TASKS[1])):
        ok, err = True, ""
        try:
            from pyrogram.parser.html import HTML
            await HTML(None).parse(txt)
        except Exception as e:
            ok, err = False, f"{type(e).__name__}: {e}"
        check(f"render ok ({txt[:22]}...)", ok and len(txt) > 10, err)

    print("\n=== 10. tasks persist (restart ke baad list) ===")
    B.tasks_save()
    saved = json.loads(B.TASKS_FILE.read_text())
    check("tasks.json me tasks saved", len(saved) >= 3, str(len(saved)))
    check("status field hai", all("status" in x for x in saved))

    print("\n" + "=" * 60)
    print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  ✗", f)
        return 1
    print("🎉 bot ka engine + UI sahi chal raha hai")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
