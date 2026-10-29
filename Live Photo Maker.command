#!/bin/bash
# Live Photo Maker - double-click to open (macOS).
cd "$(dirname "$0")" || exit 1

pause_exit() { read -n 1 -s -r -p "Press any key to close"; echo; exit 1; }

# Apple's built-in /usr/bin/python3 ships Tk 8.5, which can't open windows on current
# macOS, so look for a Python whose Tk is 8.6 or newer (python.org or Homebrew + python-tk).
pick_python() {
  for dir in /Library/Frameworks/Python.framework/Versions/Current/bin /opt/homebrew/bin /usr/local/bin; do
    for name in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
      exe="$dir/$name"
      [ -x "$exe" ] || continue
      if "$exe" -c 'import sys, tkinter; sys.exit(sys.version_info < (3, 8) or tkinter.TkVersion < 8.6)' 2>/dev/null; then
        echo "$exe"; return
      fi
    done
  done
}

if [ ! -x .venv/bin/python ]; then
  PY=$(pick_python)
  if [ -z "$PY" ]; then
    echo "Live Photo Maker needs Python 3.8+ with Tk 8.6 or newer."
    echo "Install Python from https://www.python.org/downloads/ and double-click this file again."
    echo "(Homebrew users: brew install python python-tk)"
    pause_exit
  fi
  echo "First run: setting up Live Photo Maker with $PY (needs internet once)..."
  if ! { "$PY" -m venv .venv && .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt; }; then
    rm -rf .venv
    echo "Setup failed. Check your internet connection and try again."
    pause_exit
  fi
  # optional: drag and drop onto the window
  .venv/bin/python -m pip install -q --disable-pip-version-check tkinterdnd2 || true
fi

exec .venv/bin/python livephoto.py "$@"
