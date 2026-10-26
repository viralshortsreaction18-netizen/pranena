#!/usr/bin/env bash
# Restore script for CleanYourTV debloat, launcher handover, and optional Private DNS.
# Pre-configured for TV_HOST=172.30.102.100 (model hi352l).
#
# Usage:
#   ./restore.sh [debloat.txt] [stock.launcher.package]
#   ./restore.sh --enable <package.name>
#   ./restore.sh --enable-voice
#   ./restore.sh --reset-dns
#   ./restore.sh --from-backup
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CYT="$ROOT/cleanyourtv"
PY="$CYT/tools/venv/bin/python"
[ -x "$PY" ] || PY="$CYT/tools/venv/Scripts/python.exe"

export TV_HOST="${TV_HOST:-172.30.102.100}"

ensure_venv() {
  if [ ! -d "$CYT/tools" ]; then
    echo "Cloning https://github.com/Gathin23/cleanyourtv into $CYT..."
    git clone https://github.com/Gathin23/cleanyourtv "$CYT"
  fi
  if [ ! -x "$PY" ]; then
    echo "Creating Python virtual environment in cleanyourtv/tools/venv..."
    python3 -m venv "$CYT/tools/venv"
    "$CYT/tools/venv/bin/pip" install -r "$CYT/tools/requirements.txt"
    PY="$CYT/tools/venv/bin/python"
  fi
}

usage() {
  cat <<'EOF'
Usage (defaults to TV_HOST=172.30.102.100 for model hi352l):
  ./restore.sh [debloat.txt] [stock.launcher.package]
      Restore everything disabled by debloat.txt, switch Home back to the stock
      launcher, and restore animation scales using CleanYourTV's recovery record.

  ./restore.sh --enable <package.name>
      Re-enable a single disabled app (runs `pm enable --user 0 <package.name>`).

  ./restore.sh --enable-voice
      Re-enable Google Assistant / Voice Search (com.google.android.katniss &
      com.google.android.speech.pumpkin) and Alexa (org.droidtv.amazonalexa).

  ./restore.sh --reset-dns
      Revert Private DNS back to the Android default (`private_dns_mode opportunistic`).

  ./restore.sh --from-backup
      Compare the TV's current disabled packages with backup/packages-disabled.txt
      and re-enable every package disabled since the backup was taken, one per call.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

ensure_venv

case "${1:-}" in
  --enable)
    PKG="${2:?Usage: ./restore.sh --enable <package.name>}"
    echo "Re-enabling $PKG on $TV_HOST..."
    "$PY" -I "$CYT/tools/tv.py" "pm enable --user 0 $PKG"
    exit 0
    ;;

  --enable-voice)
    echo "Re-enabling Voice Assistant packages (one per shell call)..."
    for pkg in com.google.android.katniss com.google.android.speech.pumpkin org.droidtv.amazonalexa; do
      "$PY" -I "$CYT/tools/tv.py" "pm enable --user 0 $pkg" 2>/dev/null || true
    done
    echo "Done. Voice Assistant packages have been re-enabled if installed."
    exit 0
    ;;

  --reset-dns)
    echo "Reverting Private DNS to default (opportunistic)..."
    "$PY" -I "$CYT/tools/tv.py" "settings put global private_dns_mode opportunistic"
    echo "Private DNS mode is now:"
    "$PY" -I "$CYT/tools/tv.py" "settings get global private_dns_mode"
    exit 0
    ;;

  --from-backup)
    BACKUP_DISABLED="$ROOT/backup/packages-disabled.txt"
    if [ ! -f "$BACKUP_DISABLED" ]; then
      echo "ERROR: $BACKUP_DISABLED not found. Run ./restore.sh [debloat.txt] instead." >&2
      exit 1
    fi
    echo "Comparing current disabled packages against $BACKUP_DISABLED..."
    BEFORE=$(sed 's/^package://; s/\r$//' "$BACKUP_DISABLED" | sort -u)
    NOW=$("$PY" -I "$CYT/tools/tv.py" "pm list packages -d --user 0" | sed 's/^package://; s/\r$//' | sort -u)
    ADDED=$(comm -13 <(printf '%s\n' "$BEFORE") <(printf '%s\n' "$NOW") | grep -E '^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$' || true)
    if [ -z "$ADDED" ]; then
      echo "No newly disabled packages found compared to backup/packages-disabled.txt."
    else
      for pkg in $ADDED; do
        echo "Re-enabling $pkg..."
        "$PY" -I "$CYT/tools/tv.py" "pm enable --user 0 $pkg"
      done
    fi
    exit 0
    ;;
esac

LIST="${1:-$ROOT/debloat.txt}"
if [ ! -f "$LIST" ]; then
  echo "ERROR: Debloat list not found at $LIST" >&2
  exit 1
fi

echo "Restoring hi352l TV at $TV_HOST using $LIST..."
if [ -n "${2:-}" ]; then
  "$CYT/tools/restore.sh" "$LIST" "$2"
else
  "$CYT/tools/restore.sh" "$LIST" com.vstv.mercurytransit
fi

echo ""
echo "Taking verification screenshot to shots/restored.png..."
mkdir -p "$ROOT/shots"
"$PY" -I "$CYT/tools/tv.py" --screenshot "$ROOT/shots/restored.png" HOME || true
echo "Restore complete."
