"""Operator accounts, sessions, login throttling and the audit log, in one SQLite file.

  users     one row per operator: username, display name (shown to others), role, scrypt hash
  sessions  "keep me logged in" tokens. Only a SHA-256 of each token is stored, so a copy of the
            database can't be used to log in. Sliding expiry: each use extends it.
  audit     who did what, when, from where: logins (and failures), PTT with duration, channel
            changes, selcalls, clips, recording, account changes.

Roles:
  listen   hears the radio and sees its state. Can't transmit or change anything.
  control  everything.

The server reads the database on every login and re-checks connected operators every few
seconds, so `radpi-users` (server/users.py) changes take effect while the server runs.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time

ROLES = ("listen", "control")
MIN_PASSWORD = 8
USERNAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789._-")

# scrypt: about 50-100 ms on a Pi 4, run off the event loop. 16 MiB of memory per check.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    username   TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display    TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('listen', 'control')),
    pw_hash    TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    created    REAL NOT NULL,
    last_login REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created    REAL NOT NULL,
    last_seen  REAL NOT NULL,
    expires    REAL NOT NULL,
    ip         TEXT,
    agent      TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    id     INTEGER PRIMARY KEY,
    t      REAL NOT NULL,
    user   TEXT,
    ip     TEXT,
    event  TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS audit_t ON audit (t);
CREATE INDEX IF NOT EXISTS audit_user ON audit (user, t);
"""


class AuthError(ValueError):
    """Bad input for an account operation; the message is fit to show."""


def hash_password(password):
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(h)}"


def verify_password(password, stored):
    try:
        kind, n, r, p, salt, want = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(want)))
        return hmac.compare_digest(got, base64.b64decode(want))
    except (ValueError, TypeError):
        return False


# Compared against when the username doesn't exist, so a wrong username takes as long as a
# wrong password and timing doesn't reveal which accounts exist.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(12))


def check_username(name):
    name = str(name or "").strip().lower()
    if not 2 <= len(name) <= 32 or not set(name) <= USERNAME_CHARS:
        raise AuthError("username: 2-32 characters, letters, digits, '.', '_' or '-'")
    return name


def check_password(pw):
    pw = str(pw or "")
    if len(pw) < MIN_PASSWORD:
        raise AuthError(f"password must be at least {MIN_PASSWORD} characters")
    if len(pw) > 256:
        raise AuthError("password is too long")
    return pw


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class UserDB:
    def __init__(self, path, session_days=30):
        self.path = path
        self.session_s = float(session_days) * 86400
        existed = os.path.exists(path)
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")       # the CLI and the server can use it at once
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        if not existed:
            os.chmod(path, 0o600)                         # password hashes and the audit log

    # ---- users -------------------------------------------------------------

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def get(self, username=None, user_id=None):
        if user_id is not None:
            row = self.db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        else:
            row = self.db.execute("SELECT * FROM users WHERE username = ?", (str(username).lower(),)).fetchone()
        return dict(row) if row else None

    def users(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM users ORDER BY username")]

    def add(self, username, password, role="control", display=None):
        username = check_username(username)
        if role not in ROLES:
            raise AuthError(f"role must be one of {', '.join(ROLES)}")
        if self.get(username):
            raise AuthError(f"user {username} already exists")
        display = (str(display).strip() if display else username.upper())[:24]
        self.db.execute("INSERT INTO users (username, display, role, pw_hash, created) VALUES (?, ?, ?, ?, ?)",
                        (username, display, role, hash_password(check_password(password)), time.time()))
        return self.get(username)

    def _require(self, username):
        u = self.get(username)
        if not u:
            raise AuthError(f"no user {username}")
        return u

    def set_password(self, username, password, keep_session=None):
        """New password; every session of the user is ended except keep_session (a token hash)."""
        u = self._require(username)
        self.db.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(check_password(password)), u["id"]))
        self.db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash IS NOT ?", (u["id"], keep_session))

    def set_role(self, username, role):
        if role not in ROLES:
            raise AuthError(f"role must be one of {', '.join(ROLES)}")
        self.db.execute("UPDATE users SET role = ? WHERE id = ?", (role, self._require(username)["id"]))

    def set_display(self, username, display):
        self.db.execute("UPDATE users SET display = ? WHERE id = ?", (str(display).strip()[:24], self._require(username)["id"]))

    def set_active(self, username, active):
        u = self._require(username)
        self.db.execute("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, u["id"]))
        if not active:
            self.db.execute("DELETE FROM sessions WHERE user_id = ?", (u["id"],))

    def delete(self, username):
        self.db.execute("DELETE FROM users WHERE id = ?", (self._require(username)["id"],))

    def hash_for(self, username):
        """(user or None, hash to check against). Unknown users get a dummy hash, so a wrong
        username costs the same scrypt as a wrong password (no timing hint about accounts)."""
        u = self.get(username) if username else None
        return u, (u["pw_hash"] if u else _DUMMY_HASH)

    def mark_login(self, user):
        self.db.execute("UPDATE users SET last_login = ? WHERE id = ?", (time.time(), user["id"]))

    def set_pw_hash(self, user_id, pw_hash, keep_session=None):
        """Store a hash made off the event loop; end the user's other sessions."""
        self.db.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (pw_hash, user_id))
        self.db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash IS NOT ?", (user_id, keep_session))

    # ---- sessions ------------------------------------------------------------

    def new_session(self, user, ip, agent, remember=True):
        """A session token for the browser. 'Keep me logged in' ones last session_days after
        last use; the others a day, and the browser forgets them when the tab closes."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        ttl = self.session_s if remember else 86400
        self.db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (_token_hash(token), user["id"], now, now, now + ttl, ip, str(agent or "")[:200]))
        return token, _token_hash(token)

    def resume_session(self, token):
        """(user, token hash) for a valid session, extending it; or (None, None)."""
        if not token or len(token) > 100:
            return None, None
        th = _token_hash(token)
        now = time.time()
        row = self.db.execute("SELECT * FROM sessions WHERE token_hash = ? AND expires > ?", (th, now)).fetchone()
        if not row:
            return None, None
        u = self.get(user_id=row["user_id"])
        if not u or not u["active"]:
            return None, None
        ttl = max(row["expires"] - row["last_seen"], 0)    # keep each session's own lifetime
        self.db.execute("UPDATE sessions SET last_seen = ?, expires = ? WHERE token_hash = ?", (now, now + ttl, th))
        self.db.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, u["id"]))
        return u, th

    def session_valid(self, token_hash):
        return self.db.execute("SELECT 1 FROM sessions WHERE token_hash = ? AND expires > ?",
                               (token_hash, time.time())).fetchone() is not None

    def end_session(self, token_hash):
        self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def end_sessions(self, username):
        self.db.execute("DELETE FROM sessions WHERE user_id = ?", (self._require(username)["id"],))

    def sessions(self):
        return [dict(r) for r in self.db.execute(
            "SELECT s.*, u.username FROM sessions s JOIN users u ON u.id = s.user_id ORDER BY s.last_seen DESC")]

    # ---- audit log -------------------------------------------------------------

    def log(self, event, user=None, ip=None, **detail):
        self.db.execute("INSERT INTO audit (t, user, ip, event, detail) VALUES (?, ?, ?, ?, ?)",
                        (time.time(), user, ip, event, json.dumps(detail, separators=(",", ":")) if detail else None))

    def entries(self, user=None, since=None, limit=100):
        q, args = "SELECT * FROM audit WHERE 1=1", []
        if user:
            q += " AND user = ?"
            args.append(user)
        if since:
            q += " AND t >= ?"
            args.append(since)
        q += " ORDER BY t DESC LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in self.db.execute(q, args)][::-1]

    def prune(self, audit_days):
        now = time.time()
        self.db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        if audit_days:
            self.db.execute("DELETE FROM audit WHERE t < ?", (now - float(audit_days) * 86400,))


class LoginLimiter:
    """Locks out an IP address, or a username, after repeated failed logins.

    MAX_FAILS failures within WINDOW seconds lock that IP (or username) for LOCK seconds. A
    successful login clears the counter. Counting usernames too stops a spread-out attack on
    one account; counting IPs stops one host trying many accounts.
    """

    MAX_FAILS = 5
    WINDOW = 15 * 60
    LOCK = 15 * 60

    def __init__(self):
        self.fails = {}    # key -> [timestamps]
        self.locked = {}   # key -> unlock time

    def _keys(self, ip, username):
        return [f"ip:{ip}"] + ([f"user:{username.lower()}"] if username else [])

    def blocked(self, ip, username):
        """Seconds until a locked IP / username may try again, or 0."""
        now = time.monotonic()
        return max([self.locked.get(k, 0) - now for k in self._keys(ip, username)] + [0])

    def failed(self, ip, username):
        now = time.monotonic()
        for k in self._keys(ip, username):
            recent = [t for t in self.fails.get(k, []) if now - t < self.WINDOW] + [now]
            self.fails[k] = recent
            if len(recent) >= self.MAX_FAILS:
                self.locked[k] = now + self.LOCK
                self.fails[k] = []

    def succeeded(self, ip, username):
        for k in self._keys(ip, username):
            self.fails.pop(k, None)
