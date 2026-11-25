#!/usr/bin/env bash
#
# Install Tuya Lamps for the current user (no root needed).
#
#   ./install.sh           install the desktop app and the CLI
#   ./install.sh --plasma  also install the KDE Plasma 6 widget
#
# What it does:
#   - creates a Python venv in ~/.local/share/tuya-lamps/venv
#     (system pip is "externally managed" on openSUSE Tumbleweed)
#   - copies the app to ~/.local/share/tuya-lamps/
#   - creates the launcher ~/.local/bin/tuya-lamps
#   - installs the .desktop file and creates ~/.config/tuya-lamps/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/tuya-lamps"
VENV_DIR="$APP_DIR/venv"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/tuya-lamps"
DESKTOP_DIR="$HOME/.local/share/applications"
CONFIG_DIR="$HOME/.config/tuya-lamps"
PLASMOID_ID="io.github.elegast707.tuyalamps"

INSTALL_PLASMA=0
PLASMA_INSTALLED=0

usage() {
    echo "Usage: $0 [--plasma]"
    echo "  --plasma   also install the KDE Plasma 6 widget"
}

for arg in "$@"; do
    case "$arg" in
        --plasma) INSTALL_PLASMA=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; usage >&2; exit 1 ;;
    esac
done

step() { printf '\n==> %s\n' "$*"; }

# --- Checks --------------------------------------------------------------------

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found. Install it first (openSUSE: sudo zypper install python3)." >&2
    exit 1
fi

# --- Python venv and app ----------------------------------------------------

step "Creating the Python environment in $VENV_DIR"
mkdir -p "$APP_DIR"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --upgrade --quiet pip
"$VENV_DIR/bin/python" -m pip install --upgrade --quiet -r "$SCRIPT_DIR/requirements.txt"

step "Copying the app to $APP_DIR"
install -m 644 "$SCRIPT_DIR/tuya_lamps.py" "$APP_DIR/tuya_lamps.py"
install -m 644 "$SCRIPT_DIR/requirements.txt" "$APP_DIR/requirements.txt"

# --- Launcher ------------------------------------------------------------------

step "Creating the launcher $LAUNCHER"
mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" <<EOF
#!/bin/sh
# Launcher created by the Tuya Lamps install.sh.
# Runs the app with its own venv Python and passes all arguments through.
exec "$VENV_DIR/bin/python" "$APP_DIR/tuya_lamps.py" "\$@"
EOF
chmod 755 "$LAUNCHER"

# --- Desktop file ----------------------------------------------------------------

step "Installing the menu entry"
mkdir -p "$DESKTOP_DIR"
# Use the full launcher path: the desktop session's PATH may not include ~/.local/bin.
sed "s|^Exec=.*|Exec=$LAUNCHER|" "$SCRIPT_DIR/tuya-lamps.desktop" > "$DESKTOP_DIR/tuya-lamps.desktop"
chmod 644 "$DESKTOP_DIR/tuya-lamps.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

# --- Config folder ---------------------------------------------------------------

step "Creating the config folder $CONFIG_DIR"
mkdir -p "$CONFIG_DIR"
# devices.json will contain your local keys: keep the folder private.
chmod 700 "$CONFIG_DIR"

# --- Plasma widget -----------------------------------------------------------------

if [ "$INSTALL_PLASMA" -eq 1 ]; then
    step "Installing the Plasma widget ($PLASMOID_ID)"
    if ! command -v kpackagetool6 >/dev/null 2>&1; then
        echo "kpackagetool6 not found; is KDE Plasma 6 installed? Skipping the widget." >&2
    elif kpackagetool6 --type Plasma/Applet --install "$SCRIPT_DIR/plasmoid" >/dev/null 2>&1; then
        echo "Widget installed."
        PLASMA_INSTALLED=1
    elif kpackagetool6 --type Plasma/Applet --upgrade "$SCRIPT_DIR/plasmoid"; then
        echo "Widget upgraded. Restart Plasma to load the new version:"
        echo "  systemctl --user restart plasma-plasmashell"
        PLASMA_INSTALLED=1
    else
        echo "Could not install the widget." >&2
    fi
fi

# --- Done ----------------------------------------------------------------------------

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        echo
        echo "WARNING: $BIN_DIR is not on your PATH, so 'tuya-lamps' won't be found."
        echo "Add this line to ~/.bashrc (or ~/.profile) and log in again:"
        echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
        ;;
esac

cat <<EOF

Tuya Lamps is installed.

Next steps
----------
1. Create devices.json with the TinyTuya wizard (needs a Tuya IoT account,
   see the README):

     cd "$CONFIG_DIR"
     "$VENV_DIR/bin/python" -m tinytuya wizard

2. Allow lamp discovery through the firewall (openSUSE uses firewalld):

     sudo firewall-cmd --permanent --add-port=6666/udp --add-port=6667/udp --add-port=7000/udp --add-port=6668/tcp
     sudo firewall-cmd --reload

3. Start the app from the menu, or run:

     tuya-lamps            # desktop app
     tuya-lamps list       # JSON overview of all lamps
EOF
if [ "$PLASMA_INSTALLED" -eq 1 ]; then
    echo
    echo "4. Add the widget: right-click your panel → Add or Manage Widgets… → search for \"Tuya Lamps\"."
fi
