#!/usr/bin/env bash
# Update an existing install from this git clone.
# Run from the clone on the Pi:  sudo ./deploy/update.sh               (pull from GitHub, then deploy)
#                                sudo ./deploy/update.sh --no-pull     (deploy the files as they are)
#                                sudo ./deploy/update.sh --with-ai     (also add the AI agent's packages)
#                                sudo ./deploy/update.sh --without-ai  (remove them: rebuilds the venv)
#
# The AI agent's packages (requirements-bot.txt: Whisper, a speaker model, Piper and PyTorch, well
# over a gigabyte) are only installed when asked for. After --with-ai the install remembers it
# (/opt/radpi/.with-ai) and later updates keep them up to date; without it they're never downloaded.
#
# Copies only the code (server/, web/, requirements*.txt) into /opt/radpi. Your
# config.yaml, state.json, tls/ certificates, audio clips and the Python venv are left alone. Python
# packages are reinstalled only when requirements.txt changed, and the systemd unit only
# when deploy/radpi.service changed. First install: use deploy/install.sh instead.
set -euo pipefail

DEST=/opt/radpi
SRC="$(cd "$(dirname "$0")/.." && pwd)"
UNIT=/etc/systemd/system/radpi.service
AI_MARK="$DEST/.with-ai"   # present = this install has the AI agent's packages

pull=1
ai=""        # "", on or off
for arg in "$@"; do
  case "$arg" in
    --no-pull) pull=0 ;;
    --with-ai) ai=on ;;
    --without-ai) ai=off ;;
    *) echo "unknown option: $arg  (options: --no-pull, --with-ai, --without-ai)"; exit 1 ;;
  esac
done

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

if (( pull )); then
  # Pull as the user who owns the clone, so git's files don't end up owned by root.
  owner="$(stat -c %U "$SRC")"
  echo "== git pull (as $owner)"
  sudo -u "$owner" git -C "$SRC" pull --ff-only
  # The pull may have changed this script, but bash keeps running the old copy it opened.
  # Start again with the new one, so new install steps run on this update, not the next.
  again=(--no-pull)
  [[ $ai == on ]] && again+=(--with-ai)
  [[ $ai == off ]] && again+=(--without-ai)
  exec "$SRC/deploy/update.sh" "${again[@]}"
fi

command -v rsync >/dev/null || apt-get install -y rsync

# Compare before copying, so we know what needs more than a file copy.
reqs_changed=0
cmp -s "$SRC/requirements.txt" "$DEST/requirements.txt" || reqs_changed=1
ai_reqs_changed=0
cmp -s "$SRC/requirements-bot.txt" "$DEST/requirements-bot.txt" || ai_reqs_changed=1
unit_changed=0
cmp -s "$SRC/deploy/radpi.service" "$UNIT" || unit_changed=1

echo "== copying code to $DEST"
# --delete only removes files that are gone from server/ and web/; nothing else in $DEST is touched.
rsync -a --delete --exclude __pycache__ --exclude .DS_Store --itemize-changes \
  "$SRC/server" "$SRC/web" "$SRC/requirements.txt" "$SRC/requirements-bot.txt" "$DEST/" | grep -v '^\.' || true
chown -R radio:radio "$DEST"
install -m 755 "$SRC/deploy/radpi-users" /usr/local/bin/radpi-users

if [[ $ai == off && -e "$AI_MARK" ]]; then
  # pip can't cleanly take PyTorch and its dependencies back out: start a fresh venv instead.
  echo "== removing the AI agent's packages (rebuilding the venv)"
  rm -f "$AI_MARK"
  rm -rf "$DEST/.venv"
  python3 -m venv --system-site-packages "$DEST/.venv"
  "$DEST/.venv/bin/pip" install --upgrade pip
  reqs_changed=1
fi

if (( reqs_changed )); then
  echo "== requirements.txt changed: installing Python packages"
  "$DEST/.venv/bin/pip" install -r "$DEST/requirements.txt"
  chown -R radio:radio "$DEST/.venv"
fi

if [[ $ai == on ]] || { [[ -e "$AI_MARK" ]] && (( ai_reqs_changed )); }; then
  echo "== installing the AI agent's packages (the first time is a large download, several minutes)"
  "$DEST/.venv/bin/pip" install -r "$DEST/requirements-bot.txt"
  touch "$AI_MARK"
  chown -R radio:radio "$DEST/.venv" "$AI_MARK"
fi

# The agent switched on in config.yaml without its packages: it would only show errors in its panel.
bot_on="$("$DEST/.venv/bin/python" -c 'import sys, yaml; c = yaml.safe_load(open(sys.argv[1])) or {}; print(bool((c.get("bot") or {}).get("enabled")))' "$DEST/config.yaml" 2>/dev/null || echo False)"
if [[ $bot_on == True && ! -e "$AI_MARK" ]]; then
  echo "!! bot.enabled is true in config.yaml but the AI agent's packages aren't installed."
  echo "   Install them with: sudo ./deploy/update.sh --with-ai   (or set bot.enabled: false)"
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
