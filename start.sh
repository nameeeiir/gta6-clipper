#!/usr/bin/env bash
# Start clipping (Mac/Linux):  bash start.sh            (or: bash start.sh --channels streamer1 streamer2)
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "Run setup first:  bash setup.sh"
    exit 1
fi
export PATH="$PWD/.venv/bin:$PATH"
echo "Watching live GTA VI streams. Clips go to data/clips. Press Ctrl+C to stop."
exec .venv/bin/python -m clipper run "$@"
