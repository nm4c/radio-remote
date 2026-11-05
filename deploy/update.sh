#!/usr/bin/env bash
# Update an existing install from this git clone.
# Run from the clone on the Pi:  sudo ./deploy/update.sh            (pull from GitHub, then deploy)
#                                sudo ./deploy/update.sh --no-pull  (deploy the files as they are)
#
# Copies only the code (server/, web/, requirements.txt) into /opt/radio-remote. Your
# config.yaml, state.json, tls/ certificates and the Python venv are left alone. Python
# packages are reinstalled only when requirements.txt changed, and the systemd unit only
# when deploy/radio-remote.service changed. First install: use deploy/install.sh instead.
set -euo pipefail

DEST=/opt/radio-remote
SRC="$(cd "$(dirname "$0")/.." && pwd)"
UNIT=/etc/systemd/system/radio-remote.service

if [[ $EUID -ne 0 ]]; then echo "run with sudo"; exit 1; fi
if [[ ! -x "$DEST/.venv/bin/python" ]]; then
  echo "No install found in $DEST. Run: sudo ./deploy/install.sh"; exit 1
fi

if [[ "${1:-}" != "--no-pull" ]]; then
  # Pull as the user who owns the clone, so git's files don't end up owned by root.
  owner="$(stat -c %U "$SRC")"
  echo "== git pull (as $owner)"
  sudo -u "$owner" git -C "$SRC" pull --ff-only
fi

command -v rsync >/dev/null || apt-get install -y rsync

# Compare before copying, so we know what needs more than a file copy.
reqs_changed=0
cmp -s "$SRC/requirements.txt" "$DEST/requirements.txt" || reqs_changed=1
unit_changed=0
cmp -s "$SRC/deploy/radio-remote.service" "$UNIT" || unit_changed=1

echo "== copying code to $DEST"
# --delete only removes files that are gone from server/ and web/; nothing else in $DEST is touched.
rsync -a --delete --exclude __pycache__ --exclude .DS_Store --itemize-changes \
  "$SRC/server" "$SRC/web" "$SRC/requirements.txt" "$DEST/" | grep -v '^\.' || true
chown -R radio:radio "$DEST"

if (( reqs_changed )); then
  echo "== requirements.txt changed: installing Python packages"
  "$DEST/.venv/bin/pip" install -r "$DEST/requirements.txt"
  chown -R radio:radio "$DEST/.venv"
fi

if (( unit_changed )); then
  echo "== service file changed: reloading systemd"
  cp "$SRC/deploy/radio-remote.service" "$UNIT"
  systemctl daemon-reload
fi

echo "== restarting radio-remote"
systemctl restart radio-remote
sleep 2
if systemctl is-active --quiet radio-remote; then
  echo "Running. Reload the page in your browser."
else
  echo "radio-remote did not start. Last log lines:"
  journalctl -u radio-remote -n 30 --no-pager
  exit 1
fi
