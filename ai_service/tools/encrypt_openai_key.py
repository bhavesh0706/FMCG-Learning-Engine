"""
Encrypt the OpenAI API key and save it in ai_service/.env as OPENAI_API_KEY_ENC.

Run from the ai_service folder:
    python tools/encrypt_openai_key.py

What it does:
  1. Uses the master key from FMCG_MASTER_KEY / FMCG_MASTER_KEY_FILE / ~/.fmcg/master.key.
     If there is none yet, it creates ~/.fmcg/master.key (readable only by you).
  2. Asks for your OpenAI key (typing is hidden) and encrypts it.
  3. Writes OPENAI_API_KEY_ENC=... into ai_service/.env and empties any plain
     OPENAI_API_KEY line there.

The master key is never written into the project. Back it up somewhere safe:
without it the encrypted value cannot be read and you must run this again.
For Docker/servers, give the container the master key as FMCG_MASTER_KEY.
"""

import getpass
import os
import re
import sys
from pathlib import Path

AI_SERVICE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AI_SERVICE_DIR))

from cryptography.fernet import Fernet  # noqa: E402

from services.secret_store import SecretError, load_master_key, master_key_file  # noqa: E402

ENV_FILE = AI_SERVICE_DIR / ".env"


def get_or_create_master_key() -> bytes:
    try:
        return load_master_key()
    except SecretError:
        pass
    path = master_key_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    key = Fernet.generate_key()
    # Create the file readable/writable by the owner only.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key.decode() + "\n")
    print(f"Created a new master key at {path} (back this file up).")
    return key


def set_env_value(text: str, name: str, value: str) -> str:
    line = f"{name}={value}"
    pattern = re.compile(rf"^{re.escape(name)}=.*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(lambda _m: line, text)
    return text.rstrip("\n") + f"\n{line}\n"


def main() -> None:
    master = get_or_create_master_key()
    api_key = getpass.getpass("OpenAI API key (input hidden): ").strip()
    if not api_key:
        sys.exit("No key entered; nothing changed.")
    if not api_key.startswith("sk-"):
        print("Note: OpenAI keys usually start with 'sk-'. Continuing anyway.")

    token = Fernet(master).encrypt(api_key.encode()).decode()

    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    text = set_env_value(text, "OPENAI_API_KEY_ENC", token)
    if re.search(r"^OPENAI_API_KEY=", text, re.MULTILINE):
        text = set_env_value(text, "OPENAI_API_KEY", "")
    ENV_FILE.write_text(text, encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)

    print(f"Saved the encrypted key to {ENV_FILE} as OPENAI_API_KEY_ENC.")
    print("Restart the AI service to use it.")


if __name__ == "__main__":
    main()
