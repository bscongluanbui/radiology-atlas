#!/bin/bash

# One-click macOS launcher for the Radiology Atlas offline viewer.
# Keep this file inside the offline_anatomy_viewer directory.

set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
DATA_ROOT="${ANATOMY_DATA_ROOT:-$PROJECT_DIR/imaios_data/all_modules}"

show_error() {
  message="$1"
  printf '\nERROR: %s\n' "$message" >&2
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display alert \"Radiology Atlas\" message \"$message\" as critical" >/dev/null 2>&1 || true
  fi
}

wait_before_close() {
  if [ -t 0 ]; then
    printf '\nPress Return to close this window...'
    IFS= read -r _unused
  fi
}

if [ -n "${PYTHON_BIN:-}" ]; then
  PYTHON="$PYTHON_BIN"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
elif [ -x /opt/homebrew/bin/python3 ]; then
  PYTHON=/opt/homebrew/bin/python3
elif [ -x /usr/local/bin/python3 ]; then
  PYTHON=/usr/local/bin/python3
else
  show_error "Python 3.9 or newer is required. Install Python for macOS, then double-click this launcher again."
  if command -v open >/dev/null 2>&1; then
    open "https://www.python.org/downloads/macos/" >/dev/null 2>&1 || true
  fi
  wait_before_close
  exit 1
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
  show_error "The selected Python is too old. Radiology Atlas requires Python 3.9 or newer."
  wait_before_close
  exit 1
fi

if [ ! -f "$SCRIPT_DIR/server.py" ]; then
  show_error "server.py was not found beside this launcher. Keep the launcher inside offline_anatomy_viewer."
  wait_before_close
  exit 1
fi

if [ ! -d "$DATA_ROOT" ]; then
  show_error "Captured data was not found at: $DATA_ROOT"
  printf '%s\n' "Expected layout: <project>/offline_anatomy_viewer and <project>/imaios_data/all_modules" >&2
  printf '%s\n' "You can override the location with the ANATOMY_DATA_ROOT environment variable." >&2
  wait_before_close
  exit 1
fi

printf '%s\n' \
  'RADIOLOGY ATLAS — OFFLINE VIEWER' \
  "Python: $PYTHON" \
  "Viewer: $SCRIPT_DIR" \
  "Data:   $DATA_ROOT" \
  'The browser will open automatically. Keep this Terminal window open.' \
  'Press Control+C here to stop the viewer.' \
  ''

"$PYTHON" "$SCRIPT_DIR/server.py" --data-root "$DATA_ROOT" "$@"
status=$?

if [ "$status" -ne 0 ]; then
  show_error "Radiology Atlas stopped with exit status $status."
  wait_before_close
fi

exit "$status"
