#!/usr/bin/env bash
#
# Remove Tuya Lamps (app, venv, launcher, menu entry and Plasma widget).
#
# Your configuration in ~/.config/tuya-lamps/ (devices.json with your lamp
# keys) is kept, so you don't have to run the wizard again after a reinstall.

set -euo pipefail

APP_DIR="$HOME/.local/share/tuya-lamps"
LAUNCHER="$HOME/.local/bin/tuya-lamps"
DESKTOP_FILE="$HOME/.local/share/applications/tuya-lamps.desktop"
CONFIG_DIR="$HOME/.config/tuya-lamps"
PLASMOID_ID="io.github.elegast707.tuyalamps"

remove() {
    if [ -e "$1" ]; then
        rm -rf -- "$1"
        echo "Removed $1"
    fi
}

remove "$LAUNCHER"
remove "$DESKTOP_FILE"
remove "$APP_DIR"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

if command -v kpackagetool6 >/dev/null 2>&1; then
    if kpackagetool6 --type Plasma/Applet --show "$PLASMOID_ID" >/dev/null 2>&1; then
        kpackagetool6 --type Plasma/Applet --remove "$PLASMOID_ID"
        echo "Removed the Plasma widget $PLASMOID_ID"
    fi
fi

echo
echo "Tuya Lamps has been removed."
echo "Your configuration is kept in $CONFIG_DIR"
echo "Delete it yourself if you no longer need it:  rm -r \"$CONFIG_DIR\""
