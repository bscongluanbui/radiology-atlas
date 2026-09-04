#!/usr/bin/env python3
"""Interactive account management for first boot and recovery."""
from argparse import ArgumentParser
from getpass import getpass
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from docker.auth_store import AuthStore


def secret(prompt):
    first = getpass(prompt)
    second = getpass("Nhập lại mật khẩu: ")
    if first != second:
        raise ValueError("Hai mật khẩu không trùng nhau.")
    return first


def main():
    parser = ArgumentParser(description="Quản lý tài khoản Radiology Atlas (mật khẩu chỉ nhập tương tác).")
    parser.add_argument("--state-dir", default=os.environ.get("STATE_DIR", "/state"))
    sub = parser.add_subparsers(dest="command", required=True)
    for command, label in (("create-root", "Tạo Root quản trị toàn bộ hệ thống"), ("create-admin", "Tạo Admin chỉ xem toàn bộ giải phẫu")):
        create = sub.add_parser(command, help=label)
        create.add_argument("--username", required=True)
    reset = sub.add_parser("reset-password", help="Đặt mật khẩu mới và thu hồi toàn bộ phiên")
    reset.add_argument("--username", required=True)
    sub.add_parser("list", help="Liệt kê tài khoản, không hiện mật khẩu")
    args = parser.parse_args()
    store = AuthStore(args.state_dir)
    if args.command in ("create-root", "create-admin"):
        role = "root" if args.command == "create-root" else "admin"
        store.create_user(args.username, secret("Mật khẩu mới: "), role, actor="console")
        print(f"ACCOUNT_CREATED={args.username.lower()}; ROLE={role}; PASSWORD_STORED=argon2id")
    elif args.command == "reset-password":
        users = {u["username"]: u for u in store.users()}
        user = users.get(args.username.lower())
        if not user:
            raise ValueError("Tài khoản không tồn tại.")
        store.update_user(user["id"], user["role"], bool(user["active"]), bool(user["all_modules"]), user["modules"],
                          secret("Mật khẩu mới: "), actor="console", regions=user["regions"])
        print(f"PASSWORD_RESET={user['username']}; SESSIONS=revoked")
    else:
        for user in store.users():
            print(f"{user['username']}\trole={user['role']}\tactive={bool(user['active'])}\tregions={','.join(user['regions']) or '-'}\tmodule_grants={len(user['modules'])}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"ERROR={error}", file=sys.stderr)
        raise SystemExit(2)
