"""radio-users: manage operator accounts and read the audit log.

On the Pi (installed as /usr/local/bin/radio-users by install.sh / update.sh):
    sudo radio-users add vk3abc --role control --name "VK3ABC Nick"
    sudo radio-users add listener1 --role listen
    sudo radio-users list
    sudo radio-users passwd vk3abc
    sudo radio-users role vk3abc listen
    sudo radio-users disable vk3abc        (also ends its sessions)
    sudo radio-users enable vk3abc
    sudo radio-users delete vk3abc
    sudo radio-users sessions              (who is kept logged in where)
    sudo radio-users logout vk3abc         (end all of a user's sessions)
    sudo radio-users log [--user vk3abc] [--days 7] [--limit 200]

Changes take effect within a few seconds, even for operators who are connected now.
Passwords are asked for without echo; --password-stdin reads one from standard input instead.
"""

import argparse
import datetime
import getpass
import json
import os
import sys
import time

from .auth import ROLES, AuthError, UserDB
from .config import load


def _ask_password(args):
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass("New password: ")
    if getpass.getpass("Again: ") != pw:
        raise AuthError("passwords don't match")
    return pw


def _when(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") if t else "never"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="radio-users", description="Manage Radio Remote operator accounts.")
    ap.add_argument("-c", "--config", default="/opt/radio-remote/config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="create an account")
    p.add_argument("username")
    p.add_argument("--role", choices=ROLES, default="control")
    p.add_argument("--name", help="shown to other operators (default: the username in capitals)")
    p.add_argument("--password-stdin", action="store_true")
    p = sub.add_parser("passwd", help="set a new password (ends the user's sessions)")
    p.add_argument("username")
    p.add_argument("--password-stdin", action="store_true")
    p = sub.add_parser("role", help="change role")
    p.add_argument("username")
    p.add_argument("role", choices=ROLES)
    p = sub.add_parser("name", help="change the display name")
    p.add_argument("username")
    p.add_argument("display")
    for name, hlp in (("disable", "block logins and end sessions"), ("enable", "allow logins again"),
                      ("delete", "remove the account"), ("logout", "end all of the user's sessions")):
        sub.add_parser(name, help=hlp).add_argument("username")
    sub.add_parser("list", help="list accounts")
    sub.add_parser("sessions", help="list kept-logged-in sessions")
    p = sub.add_parser("log", help="show the audit log")
    p.add_argument("--user")
    p.add_argument("--days", type=float, default=7)
    p.add_argument("--limit", type=int, default=200)
    args = ap.parse_args(argv)

    cfg = load(args.config)
    db = UserDB(cfg["server"]["db_file"], cfg["server"]["session_days"])
    who = f"cli:{os.environ.get('RADIO_USERS_BY') or getpass.getuser()}"
    try:
        if args.cmd == "add":
            u = db.add(args.username, _ask_password(args), args.role, args.name)
            db.log("user_added", who, None, username=u["username"], role=u["role"])
            print(f"added {u['username']} ({u['role']}), shown as {u['display']}")
        elif args.cmd == "passwd":
            db.set_password(args.username, _ask_password(args))
            db.log("password_set", who, None, username=args.username.lower())
            print(f"password changed for {args.username}; its sessions were ended")
        elif args.cmd == "role":
            db.set_role(args.username, args.role)
            db.log("role_set", who, None, username=args.username.lower(), role=args.role)
            print(f"{args.username} is now {args.role}")
        elif args.cmd == "name":
            db.set_display(args.username, args.display)
            print(f"{args.username} is shown as {args.display}")
        elif args.cmd in ("disable", "enable"):
            db.set_active(args.username, args.cmd == "enable")
            db.log(f"user_{args.cmd}d", who, None, username=args.username.lower())
            print(f"{args.username} {args.cmd}d")
        elif args.cmd == "delete":
            db.delete(args.username)
            db.log("user_deleted", who, None, username=args.username.lower())
            print(f"{args.username} deleted")
        elif args.cmd == "logout":
            db.end_sessions(args.username)
            db.log("sessions_ended", who, None, username=args.username.lower())
            print(f"all sessions of {args.username} ended")
        elif args.cmd == "list":
            print(f"{'USERNAME':16s} {'SHOWN AS':16s} {'ROLE':8s} {'STATE':9s} LAST LOGIN")
            for u in db.users():
                print(f"{u['username']:16s} {u['display']:16s} {u['role']:8s} "
                      f"{'active' if u['active'] else 'disabled':9s} {_when(u['last_login'])}")
        elif args.cmd == "sessions":
            for s in db.sessions():
                print(f"{s['username']:16s} {s['ip'] or '?':16s} last used {_when(s['last_seen'])}, "
                      f"expires {_when(s['expires'])}  {s['agent'][:60]}")
        elif args.cmd == "log":
            for e in db.entries(args.user and args.user.lower(), time.time() - args.days * 86400, args.limit):
                detail = json.loads(e["detail"]) if e["detail"] else {}
                extra = " ".join(f"{k}={v}" for k, v in detail.items())
                print(f"{_when(e['t'])}  {e['user'] or '-':14s} {e['ip'] or '':16s} {e['event']:16s} {extra}")
    except AuthError as e:
        sys.exit(f"radio-users: {e}")


if __name__ == "__main__":
    main()
