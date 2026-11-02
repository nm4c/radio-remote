#!/usr/bin/env bash
# Install the remote head on Raspberry Pi OS (Bookworm or later).
# Run from the project directory:  sudo ./deploy/install.sh
set -euo pipefail

DEST=/opt/radio-remote
SRC="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then echo "run with sudo"; exit 1; fi

apt-get update
apt-get install -y python3-venv python3-lgpio libportaudio2 alsa-utils

id radio &>/dev/null || useradd --system --home "$DEST" --shell /usr/sbin/nologin radio
usermod -aG gpio,audio,dialout radio   # dialout: serial port for the SA818S HAT

mkdir -p "$DEST"
cp -r "$SRC/server" "$SRC/web" "$SRC/requirements.txt" "$DEST/"
[[ -f "$DEST/config.yaml" ]] || cp "$SRC/config.example.yaml" "$DEST/config.yaml"

# --system-site-packages so the venv can use the apt-installed lgpio
python3 -m venv --system-site-packages "$DEST/.venv"
"$DEST/.venv/bin/pip" install --upgrade pip
"$DEST/.venv/bin/pip" install -r "$DEST/requirements.txt"

chown -R radio:radio "$DEST"
chmod 600 "$DEST/config.yaml"

cp "$SRC/deploy/radio-remote.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable radio-remote

echo
echo "Installed. Next:"
echo "  1. sudo nano $DEST/config.yaml      (password, radio.type, sound card, channels)"
echo "  2. sudo -u radio $DEST/.venv/bin/python -m sounddevice   (list sound cards)"
echo "  3. sudo systemctl start radio-remote && journalctl -u radio-remote -f"
echo "  4. sudo tailscale serve --bg 8080   (HTTPS so browsers allow the mic)"
