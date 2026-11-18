#!/usr/bin/env bash
# One-time setup for Mac and Linux:  bash setup.sh
# Installs Python and ffmpeg if missing (Mac: via Homebrew), installs the clipper, then asks for your keys.
set -u
cd "$(dirname "$0")"

say() { printf '\n== %s\n' "$1"; }
fail() {
    printf '\nSETUP STOPPED: %s\nCopy everything in this window and send it to Claude for the fix.\n' "$1"
    exit 1
}
find_python() {
    for c in python3.12 python3.11 python3.13 python3.10 python3 python; do
        if command -v "$c" >/dev/null 2>&1 && [ "$("$c" -c 'import sys; print(sys.version_info >= (3, 10))' 2>/dev/null)" = "True" ]; then
            echo "$c"
            return 0
        fi
    done
    return 1
}

# Homebrew lives here on Apple Silicon and isn't always on PATH in a fresh Terminal.
[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
[ -x /usr/local/bin/brew ] && eval "$(/usr/local/bin/brew shellenv)"

need_brew() {
    command -v brew >/dev/null 2>&1 && return 0
    if [ "$(uname)" = "Darwin" ]; then
        fail "Homebrew is not installed. Go to https://brew.sh, copy the install command into Terminal, run it, then run: bash setup.sh"
    fi
    fail "$1 is missing. Install it with your package manager (e.g. sudo apt install $2), then run: bash setup.sh"
}

say "Checking Python (need 3.10 or newer)"
PY="$(find_python)" || {
    need_brew "Python 3.10+" "python3 python3-venv"
    brew install python || fail "brew could not install Python."
    PY="$(find_python)" || fail "Python installed but not found. Close Terminal, open it again, and run: bash setup.sh"
}
echo "Using: $("$PY" --version)"

say "Checking ffmpeg"
if ! command -v ffmpeg >/dev/null 2>&1; then
    need_brew "ffmpeg" "ffmpeg"
    brew install ffmpeg || fail "brew could not install ffmpeg."
fi
echo "ffmpeg found."

say "Creating a private Python environment in .venv"
if [ ! -x .venv/bin/python ]; then
    "$PY" -m venv .venv || fail "could not create .venv (on Linux: sudo apt install python3-venv)."
fi

say "Installing the clipper and its libraries (takes a few minutes the first time)"
.venv/bin/python -m pip install --upgrade pip --quiet --disable-pip-version-check
.venv/bin/python -m pip install -e . --disable-pip-version-check || fail "installing the libraries failed (see the errors above)."

export PATH="$PWD/.venv/bin:$PATH"

say "Your keys"
.venv/bin/python -m clipper setup
KEYS_OK=$?

say "Final check"
.venv/bin/python -m clipper check

if [ "$KEYS_OK" -eq 0 ]; then
    printf '\nALL SET. To start clipping run:  bash start.sh\nFinished clips appear in data/READY TO POST. Nothing gets posted anywhere.\n'
else
    printf '\nAlmost done: the Twitch keys are still missing. Run  bash setup.sh  again once you have them.\n'
fi
