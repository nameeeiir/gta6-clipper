"""Interactive key setup: `python -m clipper setup`.

Creates config.yaml and .env if missing, asks for the Twitch keys (and optionally a Claude key),
checks each key against the real API before saving it, and writes it into .env.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import httpx

ENV_FILE = Path(".env")


def set_env_value(path: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env file, replacing an existing KEY= line or appending one."""
    lines = path.read_text().splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if not done and line.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")


def read_env_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def clean(raw: str) -> str:
    """Strip whitespace and stray quotes people paste along with a key."""
    return raw.strip().strip('"').strip("'").strip()


def check_twitch(client_id: str, client_secret: str) -> tuple[bool | None, str]:
    try:
        r = httpx.post("https://id.twitch.tv/oauth2/token", timeout=20, data={
            "client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"})
    except httpx.HTTPError as e:
        return None, f"could not reach Twitch to check the keys ({e.__class__.__name__})"
    if r.status_code == 200:
        return True, "Twitch keys work"
    try:
        msg = r.json().get("message", r.text)
    except ValueError:
        msg = r.text
    if "secret" in msg.lower():
        return False, "the Client Secret is wrong (Client ID is fine). Click 'New Secret' in the Twitch console and copy it again"
    return False, f"Twitch rejected the Client ID ({msg}). Copy it again from your app's Manage page"


def check_anthropic(key: str) -> tuple[bool | None, str]:
    try:
        r = httpx.get("https://api.anthropic.com/v1/models", timeout=20,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    except httpx.HTTPError as e:
        return None, f"could not reach Anthropic to check the key ({e.__class__.__name__})"
    if r.status_code == 200:
        return True, "Claude key works (make sure your Anthropic account has credit)"
    if r.status_code in (401, 403):
        return False, "Anthropic rejected that key. Create a new one at console.anthropic.com -> API Keys"
    return None, f"Anthropic answered {r.status_code}; could not confirm the key"


def mask(v: str) -> str:
    return v[:4] + "..." + v[-4:] if len(v) > 10 else "(set)"


def ask(prompt: str) -> str:
    try:
        return clean(input(prompt))
    except EOFError:
        return ""


def setup_twitch() -> bool:
    cur_id, cur_secret = read_env_value(ENV_FILE, "TWITCH_CLIENT_ID"), read_env_value(ENV_FILE, "TWITCH_CLIENT_SECRET")
    if cur_id and cur_secret:
        ok, msg = check_twitch(cur_id, cur_secret)
        if ok:
            print(f"  Twitch keys already saved and working ({mask(cur_id)}).")
            return True
        print(f"  Saved Twitch keys don't work: {msg}")

    print("""
  TWITCH KEYS (required, free). If you don't have them yet:
    1. Turn on two-factor auth on your Twitch account (Twitch requires it).
    2. Go to https://dev.twitch.tv/console and log in.
    3. Click "Register Your Application":
         Name:               anything unique, e.g. myclipper123
         OAuth Redirect URL: http://localhost   (then click Add)
         Category:           Application Integration
         Client Type:        Confidential
    4. Click Create, then "Manage" next to your app.
    5. Copy the Client ID. Click "New Secret" and copy the Client Secret.
""")
    for _ in range(5):
        cid = ask("  Paste your Twitch Client ID and press Enter: ")
        secret = ask("  Paste your Twitch Client Secret and press Enter: ")
        if not cid or not secret:
            print("  Both are needed. Try again.\n")
            continue
        if cid == secret:
            print("  You pasted the same thing twice. The ID and the Secret are different. Try again.\n")
            continue
        ok, msg = check_twitch(cid, secret)
        if ok is False:
            print(f"  X {msg}\n")
            continue
        set_env_value(ENV_FILE, "TWITCH_CLIENT_ID", cid)
        set_env_value(ENV_FILE, "TWITCH_CLIENT_SECRET", secret)
        print(f"  OK: {msg}. Saved." if ok else f"  Saved, but {msg}.")
        return True
    print("  Giving up on Twitch keys for now. Run setup again when you have them.")
    return False


def setup_claude() -> None:
    cur = read_env_value(ENV_FILE, "ANTHROPIC_API_KEY")
    if cur:
        ok, msg = check_anthropic(cur)
        if ok is not False:
            print(f"  Claude key already saved ({mask(cur)}): {msg}.")
            return
        print(f"  Saved Claude key doesn't work: {msg}")
    print("""
  CLAUDE KEY (optional, costs a little per clip): better titles and skips boring clips.
    Get one at https://console.anthropic.com -> add credit -> API Keys -> Create Key.
    No key? Just press Enter to skip. Clips still get made with template titles.
""")
    for _ in range(3):
        key = ask("  Paste your Claude API key (or press Enter to skip): ")
        if not key:
            print("  Skipped.")
            return
        ok, msg = check_anthropic(key)
        if ok is False:
            print(f"  X {msg}\n")
            continue
        set_env_value(ENV_FILE, "ANTHROPIC_API_KEY", key)
        print(f"  OK: {msg}. Saved." if ok else f"  Saved, but {msg}.")
        return


def run() -> int:
    if not Path("config.yaml").exists() and Path("config.example.yaml").exists():
        shutil.copyfile("config.example.yaml", "config.yaml")
        print("  Created config.yaml")
    if not ENV_FILE.exists():
        if Path(".env.example").exists():
            shutil.copyfile(".env.example", ENV_FILE)
        else:
            ENV_FILE.write_text("")
        print("  Created .env")
    twitch_ok = setup_twitch()
    setup_claude()
    # Make the freshly saved values visible to anything that runs next in this process.
    for k in ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "ANTHROPIC_API_KEY"):
        v = read_env_value(ENV_FILE, k)
        if v:
            os.environ[k] = v
    return 0 if twitch_ok else 1
