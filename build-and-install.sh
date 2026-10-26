#!/usr/bin/env bash
# End-to-end CleanYourTV Backup, Batched Debloat, Apple TV 4K Launcher Build & Install,
# Theme Push, and Verification Script — pre-configured for TV IP 172.30.102.100 (model hi352l).
#
# Usage:
#   ./build-and-install.sh [debloat.txt]
#   ./build-and-install.sh --debloat-only [debloat.txt]
#   ./build-and-install.sh --launcher-only
#   ./build-and-install.sh --enable-dns
#   ./build-and-install.sh --verify-closed
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
    echo "Setting up Python virtual environment in cleanyourtv/tools/venv..."
    python3 -m venv "$CYT/tools/venv"
    "$CYT/tools/venv/bin/pip" install -r "$CYT/tools/requirements.txt"
    PY="$CYT/tools/venv/bin/python"
  fi
}

usage() {
  cat <<'EOF'
Usage (defaults to TV_HOST=172.30.102.100 for model hi352l):
  ./build-and-install.sh [debloat.txt]
      1. Connect to 172.30.102.100 over ADB (port 5555 or TV_PORT) and run pre-flight safety checks
      2. Back up all packages, disabled packages, getprop, settings, and display overrides to backup/
      3. Run batched debloat via cleanyourtv/tools/disable.sh with TV screenshots after each batch
      4. Build & install the Apple TV 4K-style launcher via cleanyourtv/launcher/build-and-install.sh
      5. Push my-theme/config.json overlay and verify Home & D-pad navigation with screenshots

  ./build-and-install.sh --debloat-only [debloat.txt]
      Run only the backup and batched debloat steps (skip building/installing the launcher).

  ./build-and-install.sh --launcher-only
      Run only the launcher build, install, theme push, and screenshot verification steps.

  ./build-and-install.sh --enable-dns
      Configure AdGuard Private DNS (dns.adguard-dns.com) on Android 9+ TVs.
      (Undo anytime with: ./restore.sh --reset-dns)

  ./build-and-install.sh --verify-closed
      Verify that network ADB is closed on 172.30.102.100 (connection refused) using tools/adb-closed.py.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

ensure_venv

MODE="all"
LIST="$ROOT/debloat.txt"

case "${1:-}" in
  --debloat-only)
    MODE="debloat"
    LIST="${2:-$ROOT/debloat.txt}"
    ;;
  --launcher-only)
    MODE="launcher"
    ;;
  --enable-dns)
    echo "Setting Private DNS to dns.adguard-dns.com on $TV_HOST..."
    "$PY" -I "$CYT/tools/tv.py" "settings put global private_dns_specifier dns.adguard-dns.com"
    "$PY" -I "$CYT/tools/tv.py" "settings put global private_dns_mode hostname"
    echo "Private DNS configured:"
    "$PY" -I "$CYT/tools/tv.py" "settings get global private_dns_mode; settings get global private_dns_specifier"
    echo "Note: This does not block YouTube ads. Test that your streaming apps still play normally."
    echo "To undo: ./restore.sh --reset-dns"
    exit 0
    ;;
  --verify-closed)
    echo "Checking that network ADB is closed on $TV_HOST..."
    "$PY" -I "$CYT/tools/adb-closed.py" "$TV_HOST"
    exit 0
    ;;
  "")
    ;;
  *)
    LIST="$1"
    ;;
esac

mkdir -p "$ROOT/backup" "$ROOT/shots" "$ROOT/work"

echo "======================================================================"
echo "Step 1: Connecting to hi352l TV at $TV_HOST"
echo "If prompted on the TV screen, accept 'Allow USB debugging?' (Always allow)."
echo "(Note: Some HiSilicon/CVTE hi352l builds have ro.adb.secure=0 and connect"
echo " immediately without a prompt.)"
echo "======================================================================"
"$PY" -I "$CYT/tools/tv.py" --probe

echo ""
echo "Checking USB host state and active network transport..."
"$PY" -I "$CYT/tools/tv.py" "dumpsys usb | grep host_connected || true"
"$PY" -I "$CYT/tools/tv.py" "dumpsys connectivity | grep -o 'Transports: [A-Z]*' | head -n 5 || true"

MODEL=$("$PY" -I "$CYT/tools/tv.py" "getprop ro.product.model" | tr -d '\r\n' || true)
DEVICE=$("$PY" -I "$CYT/tools/tv.py" "getprop ro.product.device" | tr -d '\r\n' || true)
BRAND=$("$PY" -I "$CYT/tools/tv.py" "getprop ro.product.brand" | tr -d '\r\n' || true)
SDK=$("$PY" -I "$CYT/tools/tv.py" "getprop ro.build.version.sdk" | tr -dc '0-9' || true)
echo "Detected: Brand='$BRAND' Model='$MODEL' Device='$DEVICE' SDK='$SDK'"

echo ""
echo "======================================================================"
echo "Step 2: Backing up packages, properties, settings & display state"
echo "======================================================================"
"$PY" -I "$CYT/tools/tv.py" "pm list packages -f" > "$ROOT/backup/packages-all.txt"
"$PY" -I "$CYT/tools/tv.py" "pm list packages -d --user 0" > "$ROOT/backup/packages-disabled.txt"
"$PY" -I "$CYT/tools/tv.py" "pm list packages -e --user 0" > "$ROOT/backup/packages-enabled.txt"
"$PY" -I "$CYT/tools/tv.py" "getprop" > "$ROOT/backup/getprop.txt"
"$PY" -I "$CYT/tools/tv.py" "settings list system" > "$ROOT/backup/settings-system.txt"
"$PY" -I "$CYT/tools/tv.py" "settings list secure" > "$ROOT/backup/settings-secure.txt"
"$PY" -I "$CYT/tools/tv.py" "settings list global" > "$ROOT/backup/settings-global.txt"
"$PY" -I "$CYT/tools/tv.py" "wm size; wm density; settings get secure display_density_forced" > "$ROOT/backup/display.txt"
echo "Saved full backup into $ROOT/backup/"

echo "Capturing initial screenshot -> shots/00-before.png..."
"$PY" -I "$CYT/tools/tv.py" --screenshot "$ROOT/shots/00-before.png" || \
  echo "Note: Live TV / HDMI / DRM surfaces return empty screenshots; checked ResumedActivity instead."

if [ "$MODE" = "all" ] || [ "$MODE" = "debloat" ]; then
  echo ""
  echo "======================================================================"
  echo "Step 3: Debloating hi352l in small batches with safety checks & screenshots"
  echo "Using list: $LIST"
  echo "======================================================================"
  if [ ! -f "$LIST" ]; then
    echo "ERROR: Debloat list not found: $LIST" >&2
    exit 1
  fi

  # Save the pre-cleanup recovery record keyed to the main list before touching any batch.
  (cd "$CYT" && "$PY" -I tools/recovery.py --save "$LIST")

  PKGS=$(LC_ALL=C awk 'NR==1{sub(/^\357\273\277/,"")} {sub(/\r$/,""); print}' "$LIST" \
    | grep -vE '^\s*(#|$)' | sed -E 's/\s+#.*$//' | awk '{print $1}' || true)

  BATCH_NUM=0
  TMP_BATCH_DIR="$ROOT/work/batches"
  rm -rf "$TMP_BATCH_DIR"
  mkdir -p "$TMP_BATCH_DIR"
  printf '%s\n' "$PKGS" | split -l 8 - "$TMP_BATCH_DIR/batch_"

  for bfile in "$TMP_BATCH_DIR"/batch_*; do
    [ -f "$bfile" ] || continue
    BATCH_NUM=$((BATCH_NUM + 1))
    mv "$bfile" "${bfile}.txt"
    bfile="${bfile}.txt"
    echo ""
    echo "--- Running Batch $BATCH_NUM ---"
    cat "$bfile" | sed 's/^/  /'
    "$CYT/tools/disable.sh" "$bfile"
    SHOT_FILE=$(printf "$ROOT/shots/%02d-after-batch-%d.png" "$BATCH_NUM" "$BATCH_NUM")
    echo "Capturing verification screenshot -> $SHOT_FILE..."
    "$PY" -I "$CYT/tools/tv.py" --screenshot "$SHOT_FILE" || \
      "$PY" -I "$CYT/tools/tv.py" "dumpsys activity activities | grep -i ResumedActivity || true"
  done

  # Final pass on the full list so the main list's recovery record and verification are complete.
  "$CYT/tools/disable.sh" "$LIST"
  echo "Debloat complete."
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "launcher" ]; then
  echo ""
  echo "======================================================================"
  echo "Step 4: Checking SDK & hi352l ROM variant (CVTE / Zeasn / BestV)"
  echo "======================================================================"
  if [ -n "$SDK" ] && [ "$SDK" -lt 26 ]; then
    echo "WARNING: This TV runs Android SDK $SDK, which is below the launcher's minSdk 26 (Android 8.0)." >&2
    echo "The debloat half succeeded, but the launcher cannot be installed on SDK < 26." >&2
    exit 0
  fi

  ENABLED_NOW=$("$PY" -I "$CYT/tools/tv.py" "pm list packages -e --user 0" | tr -d '\r' || true)
  if [ -z "${STOCK_LAUNCHER:-}" ]; then
    if printf '%s\n' "$ENABLED_NOW" | grep -q 'com\.cvte\.'; then
      echo "Detected CVTE firmware on HiSilicon hi352l."
      echo "Keeping stock CVTE launcher enabled (STOCK_LAUNCHER=none) per safety rules (#741 / #1201)."
      export STOCK_LAUNCHER=none
    elif printf '%s\n' "$ENABLED_NOW" | grep -qx 'package:com.vstv.mercurytransit'; then
      echo "Detected VSTV MercuryTransit stock launcher on HiSilicon hi352l (STOCK_LAUNCHER=com.vstv.mercurytransit)."
      export STOCK_LAUNCHER=com.vstv.mercurytransit
    elif printf '%s\n' "$ENABLED_NOW" | grep -qx 'package:com.zeasn.whale.saas'; then
      echo "Detected Zeasn Whale OS launcher on HiSilicon hi352l (STOCK_LAUNCHER=com.zeasn.whale.saas)."
      export STOCK_LAUNCHER=com.zeasn.whale.saas
    elif printf '%s\n' "$ENABLED_NOW" | grep -qx 'package:com.bestv.ott'; then
      echo "Detected BestV launcher on HiSilicon hi352l (STOCK_LAUNCHER=com.bestv.ott)."
      export STOCK_LAUNCHER=com.bestv.ott
    fi
  fi

  echo ""
  echo "======================================================================"
  echo "Step 5: Building & installing the Apple TV 4K style launcher"
  echo "======================================================================"
  "$CYT/launcher/build-and-install.sh"

  if [ -f "$ROOT/my-theme/config.json" ]; then
    echo ""
    echo "Applying custom dock/layout configuration from my-theme/config.json..."
    "$CYT/tools/theme.sh" "$ROOT/my-theme"
  fi

  echo ""
  echo "======================================================================"
  echo "Step 6: Verifying Home takeover and D-pad navigation with screenshots"
  echo "======================================================================"
  "$PY" -I "$CYT/tools/tv.py" "dumpsys activity activities | grep mFocusedApp || true"
  "$PY" -I "$CYT/tools/tv.py" --screenshot "$ROOT/shots/10-launcher-home.png" HOME || true
  "$PY" -I "$CYT/tools/tv.py" --screenshot "$ROOT/shots/11-dpad-right.png" DPAD_RIGHT || true
  "$PY" -I "$CYT/tools/tv.py" --screenshot "$ROOT/shots/12-dpad-up-topbar.png" DPAD_UP || true
  "$PY" -I "$CYT/tools/tv.py" --screenshot "$ROOT/shots/13-dpad-down-dock.png" DPAD_DOWN || true
fi

echo ""
echo "======================================================================"
echo "All steps finished! Screenshots are in $ROOT/shots/ and backups in $ROOT/backup/."
echo ""
echo "IMPORTANT — Close Network ADB when finished:"
echo "  - Standard Android TV: Settings -> Developer options -> USB debugging OFF,"
echo "    Revoke authorizations, and Reboot."
echo "  - CVTE hi352l firmware (no Developer options): Switch to TV or HDMI source,"
echo "    press MENU then 1 1 4 7, set DEBUG -> ADB ENABLE -> OFF, and press Back."
echo "  - Skyworth/BYINTEK hi352l firmware: Open Settings -> press Menu, Menu, Up, Up,"
echo "    Down, Down, Left, Left, Right, Right -> Developer -> ADB OFF."
echo "  - Then verify port 5555 is closed:"
echo "    ./build-and-install.sh --verify-closed"
echo "======================================================================"
