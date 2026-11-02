# Copy Bot — GitHub Codespaces par, poora control Telegram se

Is folder ki **saari files GitHub par upload** karni hain. Uske baad sirf **bot se** kaam hoga —
terminal ki zarurat nahi (terminal hai bhi, par optional).

```
github_bot/
├── bot.py                       ← poora bot (engine + UI)
├── requirements.txt
├── selftest_bot.py              ← 31 tests (optional)
├── .devcontainer/
│   ├── devcontainer.json        ← Codespace banate hi sab auto
│   ├── start.sh                 ← install + bot start
│   └── resume.sh                ← bot ko background me chalata hai
└── data/                        ← state/progress yahan save hota hai
```

---

## STEP 1 — GitHub par repo banao (phone/browser se bhi ho jayega)

1. github.com → **New repository** → naam: `copy-bot` → **Private** → Create
2. **Add file → Upload files** → is folder ki saari files drag-drop karo
   (`.devcontainer` folder ke saath — folder banake andar 3 files daal do)
3. **Commit changes**

## STEP 2 — Secrets daalo (bas 1 baar)

GitHub → **Settings → Codespaces → Secrets → New secret** — ye 5 add karo:

| Secret | Kahan se |
|---|---|
| `API_ID` | my.telegram.org |
| `API_HASH` | my.telegram.org |
| `BOT_TOKEN` | @BotFather |
| `SESSION_STRING` | aapke paas pehle se hai (`C:\Users\kumar\.rcf\config.json` me, ya Render env) |
| `ADMIN_IDS` | aapki Telegram user id (bot bata dega) |

**Naam bilkul same rakhna** (capital letters).

## STEP 3 — Codespace kholo

Repo → **Code** (green button) → **Codespaces** → **Create codespace on main**

- Pehli baar 2-3 min: deps install + bot khud start (`data/bot.log`)
- Ready hone par **Telegram me bot ko `/start`** bhejo — bas ho gaya 🎉

> Terminal ki zarurat nahi. Agar kabhi band ho jaye to: Codespace kholo →
> terminal me ek line: `bash .devcontainer/resume.sh`
> (ya Codespace band-khol do — `postStartCommand` khud chala deta hai)

## STEP 4 — Codespace ko zyada der chalta rakho

GitHub → **Settings → Codespaces → Default idle timeout → 240 minutes**
(default 30 min me so jata hai). Band ho jaye to kaam **rukta nahi** — state save hai,
wapas kholte hi Resume se wahin se chalta hai.

---

## Bot me kya kya hai

**Main menu**
```
➕ New Task      📋 Tasks
📊 Progress      ⚙️ Settings
🩺 Test / Diagnostics
```

### ➕ New Task (5 step)
1. **Source** — `-1001234567890` ya `@username` / invite link
2. **Target** — ID / username / `me` (Saved Messages) / **comma se kai**: `me, -1001234..., @mychannel`
   - bot turant batata hai: ✅ admin hai / 🚫 nahi (file_id mode ke liye zaroori)
3. **Kitni files** — `50` ya `all` (saari)
4. **Filter** — sab (video/pdf/photo/audio) / sirf video / sirf documents
5. **Parallel** — 1 (order) / 3 (fast) / 5
→ Confirm → **▶️ Start**

### 📋 Tasks (ek saath 2-3 tasks)
- Har task ka apna progress, pause / resume / cancel / delete, retry failed
- Max 3 tasks ek saath (Settings me badal sakte ho)
- Global concurrency cap → FloodWait nahi lagta

### 📊 Progress
```
#2 [██████░░░░░░░░] 42%  running
    ✅ 21 | ⏳ 29 | ❌ 0 | 📶 3.9GB | ⏱ 12.4m
#1 [██████████████] 100% done
⏳ total baaki (saved state): 29
```

### ⚙️ Settings (button se, terminal ke bina)
parallel (1/3/5) · batch size (3/5/10) · data limit (0 = no limit / 20GB / 100GB)

### 🩺 Test / Diagnostics
env, bot, user account, chats, Saved Messages, disk — sab check karke batata hai.

---

## Copy kaise hoti hai (3 tarike, jo chale pehle)

| # | Tarika | Data |
|---|---|---|
| 1 | `copy_message` (Telegram server-side) | **0** |
| 2 | `file_id` re-send | **0** |
| 3 | download → upload | 2× file size |

Aapke channel ne 1 & 2 block kar diye (`ChatForwardsRestricted`) to 3 chalega —
data lagega, isliye **Codespaces / cloud** par chalao (aapka PC/phone data 0).

## Resume + no-duplicate (aapki demand)
- Har file ka status: `pending / inflight / done / failed` → `data/state_*.json`
- Beech me ruka → dobara chalao → **jahan se ruka wahin se**, jo ho chuki hain skip
- Crash ke waqt jo file ja rahi thi → **target channel check** (`file_unique_id`) →
  gayi to skip, nahi gayi to retry → **ek file 2 baar nahi jayegi**
- **Batch of 5**: har 5 ke baad sab-done check → fail hui turant retry → phir agla 5

## Data / storage
- Ek file download → upload → **turant delete** (peak ~1 file, ya parallel × size)
- Disk guard: jagah kam ho to naya download ruk jata hai (crash nahi)
- Data limit guard: limit paar hone wali ho to task ruk jata hai (350 GB accident nahi)

## Firebase (optional, abhi zarurat nahi)
`data/settings.json` me:
```json
{ "firebase_url": "https://xxx-default-rtdb.firebaseio.com", "firebase_token": "..." }
```
Har batch ke baad progress cloud me bhi save → dusre device se resume.

## Dikkat aaye to
| Problem | Fix |
|---|---|
| `/start` par jawab nahi | `ADMIN_IDS` me aapki id hai? (bot error me id batata hai) |
| "user account fail" | `SESSION_STRING` galat/purana — naya chahiye |
| "target me likhne ki permission nahi" | us channel me **apna account** admin banao (Post Messages ON) |
| bahut slow | Settings → parallel 5, aur Codespaces me TgCrypto already installed hai |
| Codespace so gaya | wapas kholo → `bash .devcontainer/resume.sh` (state safe hai) |

## Tests (optional)
```bash
python3 selftest_bot.py     # 31 checks — engine, resume, no-duplicate, guards, UI
```
