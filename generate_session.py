#!/usr/bin/env python3
"""Log in to Telegram interactively and save private credentials to .env."""
import asyncio
import getpass
import os
import re
import sys
from pathlib import Path

from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"


def save_env(values: dict[str, str]) -> None:
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    remaining = dict(values)
    output = []
    for line in lines:
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    if output and output[-1].strip():
        output.append("")
    output.extend(f"{key}={value}" for key, value in remaining.items())
    ENV_FILE.write_text("\n".join(output).rstrip() + "\n")
    os.chmod(ENV_FILE, 0o600)


async def main() -> int:
    print("Telegram setup — secrets sirf private .env file mein save honge.\n")
    try:
        api_id = int(input("Telegram API ID: ").strip())
        api_hash = getpass.getpass("Telegram API Hash: ").strip()
        phone = input("Phone number (country code ke saath, e.g. +91...): ").strip()
        if not api_hash or not phone:
            raise ValueError("API Hash aur phone number zaroori hain.")
    except (ValueError, EOFError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 1

    client = Client("session_setup", api_id=api_id, api_hash=api_hash, in_memory=True)
    try:
        await client.connect()
        sent_code = await client.send_code(phone)
        code = input("Telegram login code (SMS/Telegram app): ").strip().replace(" ", "")
        try:
            user = await client.sign_in(phone, sent_code.phone_code_hash, code)
        except SessionPasswordNeeded:
            password = getpass.getpass("Telegram 2FA password: ")
            user = await client.check_password(password)

        session_string = await client.export_session_string()
        default_admin = str(user.id)

        print("\nAb bot ki nayi/revoked token value daalein (BotFather se).")
        bot_token = getpass.getpass("Bot token: ").strip()
        admin_ids = input(f"Admin Telegram user ID(s) [{default_admin}]: ").strip() or default_admin
        if not bot_token:
            raise ValueError("Bot token khaali nahi ho sakta.")
        if not all(part.strip().lstrip("-").isdigit() for part in admin_ids.split(",")):
            raise ValueError("Admin ID comma-separated numeric Telegram ID honi chahiye.")

        save_env({
            "API_ID": str(api_id),
            "API_HASH": api_hash,
            "SESSION_STRING": session_string,
            "BOT_TOKEN": bot_token,
            "ADMIN_IDS": admin_ids.replace(" ", ""),
        })
        print(f"\nLogin successful: {user.first_name} (ID {user.id})")
        print(f"Credentials saved privately to {ENV_FILE.name} (permissions 600).")
        print("Session string screen par print nahi ki gayi.")
        return 0
    except (EOFError, ValueError) as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Telegram login failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if client.is_connected:
            await client.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
