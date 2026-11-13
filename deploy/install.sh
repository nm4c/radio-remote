#!/usr/bin/env bash
# Install the remote head on Raspberry Pi OS (Bookworm or later).
# Run from the project directory:  sudo ./deploy/install.sh
#                                  sudo ./deploy/install.sh --with-ai   (also the AI agent's packages)
#
# The AI agent (bot: in config.yaml) needs Whisper, a speaker model and Piper, which pull in
# PyTorch: well over a gigabyte. They're only installed with --with-ai, and update.sh keeps
# them up to date only on installs that have them. Add them later with  sudo ./deploy/update.sh --with-ai
set -euo pipefail

DEST=/opt/radpi
SRC="$(cd "$(dirname "$0")/.." && pwd)"
AI_MARK="$DEST/.with-ai"   # present = this install has the AI agent's packages

with_ai=0
for arg in "$@"; do
  case "$arg" in
    --with-ai) with_ai=1 ;;
    *) echo "unknown option: $arg  (use --with-ai to include the AI agent)"; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then echo "run with sudo"; exit 1; fi

apt-get update
apt-get install -y python3-venv python3-lgpio libportaudio2 alsa-utils

id radio &>/dev/null || useradd --system --home "$DEST" --shell /usr/sbin/nologin radio
usermod -aG gpio,audio,dialout radio   # dialout: serial port for the RadPi SA818 HAT

mkdir -p "$DEST"
cp -r "$SRC/server" "$SRC/web" "$SRC/requirements.txt" "$SRC/requirements-bot.txt" "$DEST/"
[[ -f "$DEST/config.yaml" ]] || cp "$SRC/config.example.yaml" "$DEST/config.yaml"

# --system-site-packages so the venv can use the apt-installed lgpio
python3 -m venv --system-site-packages "$DEST/.venv"
"$DEST/.venv/bin/pip" install --upgrade pip
"$DEST/.venv/bin/pip" install -r "$DEST/requirements.txt"
if (( with_ai )); then
  echo "== installing the AI agent's packages (large download, several minutes)"
  "$DEST/.venv/bin/pip" install -r "$DEST/requirements-bot.txt"
  touch "$AI_MARK"
fi

chown -R radio:radio "$DEST"
chmod 600 "$DEST/config.yaml"

install -m 755 "$SRC/deploy/radpi-users" /usr/local/bin/radpi-users
cp "$SRC/deploy/radpi.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable radpi

echo
echo "Installed. Next:"
echo "  1. sudo nano $DEST/config.yaml      (radio.type, sound card, channels)"
echo "  2. sudo radpi-users add <your-callsign> --role control   (your login; more with --role listen)"
echo "  3. sudo -u radio $DEST/.venv/bin/python -m sounddevice   (list sound cards)"
echo "  4. sudo systemctl start radpi && journalctl -u radpi -f"
echo "  5. sudo tailscale serve --bg 8080   (HTTPS so browsers allow the mic)"
echo
echo "To update later: sudo ./deploy/update.sh   (from a git clone of the repo)"
if (( with_ai )); then
  echo "AI agent packages installed. Switch it on with bot: enabled: true in config.yaml (README, AI agent)."
else
  echo "AI agent not installed (optional). Add it any time with: sudo ./deploy/update.sh --with-ai"
fi
