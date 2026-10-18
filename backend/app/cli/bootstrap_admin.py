"""Create the first administrator without exposing passwords in shell arguments."""

import argparse
import getpass
import sys
from sqlalchemy import select
from app.platform.db import DB, User
from app.platform.identity import create_user


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument(
        "--temporary",
        action="store_true",
        help="Require password change on first sign-in",
    )
    args = parser.parse_args()
    password = (
        sys.stdin.readline().rstrip("\r\n")
        if args.password_stdin
        else getpass.getpass("Admin password (12–128 characters): ")
    )
    with DB.begin() as db:
        if db.scalar(select(User.id).where(User.role == "admin", User.active == True)):
            raise SystemExit(
                "An active administrator already exists. Use the admin UI."
            )
        user = create_user(db, args.username, password, "admin", args.temporary)
    print("Created administrator:", user["username"])


if __name__ == "__main__":
    main()
