#!/usr/bin/env bash
# Update an existing install from this git clone.
# Run from the clone on the Pi:  sudo ./deploy/update.sh            (pull from GitHub, then deploy)
#                                sudo ./deploy/update.sh --no-pull  (deploy the files as they are)
#
# Copies only the code (server/, web/, requirements.txt) into /opt/radpi. Your
# config.yaml, state.json, tls/ certificates, audio clips and the Python venv are left alone. Python
# packages are reinstalled only when requirements.txt changed, and the systemd unit only
# when deploy/radpi.service changed. First install: use deploy/install.sh instead.
set -euo pipefail

DEST=/opt/radpi
SRC="$(cd "$(dirname "$0")/.." && pwd)"
UNIT=/etc/systemd/system/radpi.service

if [[ $EUID -ne 0 ]]; then echo "run with sudo"; exit 1; fi

# One-off: the project was called radio-remote. Move an old install to /opt/radpi (config, accounts,
# audit log, clips and certificates go with it), swap the service and the user tool for the new
# names, and rebuild the venv (a venv can't be moved: its scripts name the old path).
OLD=/opt/radio-remote
if [[ -d "$OLD" && ! -e "$DEST" ]]; then
  echo "== renaming the install: $OLD -> $DEST"
  systemctl disable --now radio-remote 2>/dev/null || true
  rm -f /etc/systemd/system/radio-remote.service /usr/local/bin/radio-users
  systemctl daemon-reload
  mv "$OLD" "$DEST"
  sed -i "s#$OLD#$DEST#g" "$DEST/config.yaml"   # any absolute paths (e.g. tls_cert)
  usermod --home "$DEST" radio
  rm -rf "$DEST/.venv"
  python3 -m venv --system-site-packages "$DEST/.venv"
  "$DEST/.venv/bin/pip" install --upgrade pip
  "$DEST/.venv/bin/pip" install -r "$DEST/requirements.txt"
  chown -R radio:radio "$DEST"
fi

if [[ ! -x "$DEST/.venv/bin/python" ]]; then
  echo "No install found in $DEST. Run: sudo ./deploy/install.sh"; exit 1
fi

if [[ "${1:-}" != "--no-pull" ]]; then
  # Pull as the user who owns the clone, so git's files don't end up owned by root.
  owner="$(stat -c %U "$SRC")"
  echo "== git pull (as $owner)"
  sudo -u "$owner" git -C "$SRC" pull --ff-only
  # The pull may have changed this script, but bash keeps running the old copy it opened.
  # Start again with the new one, so new install steps run on this update, not the next.
  exec "$SRC/deploy/update.sh" --no-pull
fi

command -v rsync >/dev/null || apt-get install -y rsync

# Compare before copying, so we know what needs more than a file copy.
reqs_changed=0
cmp -s "$SRC/requirements.txt" "$DEST/requirements.txt" || reqs_changed=1
unit_changed=0
cmp -s "$SRC/deploy/radpi.service" "$UNIT" || unit_changed=1

echo "== copying code to $DEST"
# --delete only removes files that are gone from server/ and web/; nothing else in $DEST is touched.
rsync -a --delete --exclude __pycache__ --exclude .DS_Store --itemize-changes \
  "$SRC/server" "$SRC/web" "$SRC/requirements.txt" "$DEST/" | grep -v '^\.' || true
chown -R radio:radio "$DEST"
install -m 755 "$SRC/deploy/radpi-users" /usr/local/bin/radpi-users

if (( reqs_changed )); then
  echo "== requirements.txt changed: installing Python packages"
  "$DEST/.venv/bin/pip" install -r "$DEST/requirements.txt"
  chown -R radio:radio "$DEST/.venv"
fi

if (( unit_changed )); then
  echo "== service file changed: reloading systemd"
  cp "$SRC/deploy/radpi.service" "$UNIT"
  systemctl daemon-reload
  systemctl enable --quiet radpi
fi

echo "== restarting radpi"
systemctl restart radpi
sleep 2
if systemctl is-active --quiet radpi; then
  echo "Running. Reload the page in your browser."
else
  echo "radpi did not start. Last log lines:"
  journalctl -u radpi -n 30 --no-pager
  exit 1
fi
