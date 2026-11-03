# Telegram Copy Bot — Codespaces terminal setup

> **Note:** This project is a Telegram media-copy bot; it does not automate Instagram.

## Run it in GitHub Codespaces

1. Open the repository in a Codespace and open **Terminal → New Terminal**.
2. Run:
   ```bash
   bash run.sh
   ```
3. On first run, the terminal asks, in order, for Telegram **API ID**, **API hash**, phone number, login code, and (if enabled) the Telegram 2FA password.
4. It then asks for a **new BotFather bot token** and an admin Telegram user ID. By default, the Telegram account used for login is the admin.
5. The bot starts in that terminal. Keep the terminal/Codespace open while using it. Stop it with `Ctrl+C`.

The wizard saves credentials in the private, gitignored `.env` file with owner-only file permissions. Do not upload `.env`, paste session strings into chat, or commit credentials. To change credentials, remove `.env` and run `bash run.sh` again.

The second run reuses `.env` and starts the bot without asking for the login code again. The Codespace filesystem keeps `.env` while the Codespace exists, but this is not a permanent backup; deleting the Codespace deletes its local files.

## Telegram prerequisites

- Create a bot with [@BotFather](https://t.me/BotFather) and use its token when prompted.
- Get an API ID and API hash from [my.telegram.org](https://my.telegram.org).
- The logged-in Telegram user must be able to read the source chat and post to the destination.
- Use a private GitHub repository. Codespaces Secrets are optional for this terminal-wizard flow; credentials are stored in the Codespace's ignored `.env` file.

## Important limitations

- Codespaces is a development environment, not guaranteed 24/7 hosting. It can stop after its idle timeout, and the bot stops with it. Free usage is limited by your GitHub account's monthly Codespaces allowance.
- Current transfer mode downloads each file and uploads it to the destination, so it uses network data and temporary disk space. It does not currently implement the server-side or file-ID zero-download paths described in older documentation.
- Completed file progress is saved locally under `data/`. A stopped task can be resumed manually from the bot's Tasks menu, but active transfers do not continue while the Codespace is stopped.

## Security warning for existing users

An earlier version of this repository contained hardcoded Telegram credentials. Removing them from the latest files does **not** erase them from Git history. Revoke/regenerate the bot token in BotFather and terminate the exposed user session under Telegram **Settings → Devices** before using this version. Generate a fresh session with `bash run.sh`.
