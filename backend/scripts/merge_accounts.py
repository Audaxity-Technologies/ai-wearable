"""Move one account's data onto another, and disable the emptied account.

    python scripts/merge_accounts.py --from <id> --to <id>            # plan
    python scripts/merge_accounts.py --from <id> --to <id> --apply    # do it

## Why this exists

Registering twice splits a history in half. Tasks, memories, people, open
loops and utterances are all scoped per account, so each half becomes
invisible from the other - and a question parser searching half a history
looks broken for reasons that have nothing to do with the parser.

## What it does, and what it refuses to do

Everything user-scoped is reassigned. Rows keyed by session (`analyses`,
`jobs`) follow their session without being touched, because the session is
what carries the owner.

**It refuses to run when keys would collide.** Two accounts can each hold a
memory keyed `person:manan` or a task with the same `dedupe_key`; reassigning
both would leave one account holding two rows that the dedupe logic believes
are one, and every later merge would pick between them arbitrarily. Rather
than guess which to keep, this stops and names them.

**The emptied account is disabled, not deleted.** Disabling is reversible,
and it has a useful side effect: a phone still signed in as that account
starts getting 401s, the app clears the rejected token, and the user is sent
to sign in as the right one. Deleting the row would do the same but could not
be undone.

**A backup is written first, always**, even with `--apply`. This edits the
only copy of real recordings' derived data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

#: Collections with a `user_id` field to rewrite.
USER_SCOPED = (
    "sessions",
    "segments",
    "memories",
    "tasks",
    "utterances",
    "actions",
    "devices",
    "people",
    "google_accounts",
)

#: Collections whose owner is implied by their session. Untouched.
SESSION_SCOPED = ("analyses", "jobs")

#: Collections whose record id IS the user id, so they need rekeying.
KEYED_BY_USER = ("voice_profiles",)

#: Fields whose values must stay unique per user after the move.
UNIQUE_PER_USER = {"memories": "key", "tasks": "dedupe_key"}


def load(store: Path, name: str) -> dict:
    path = store / f"{name}.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save(store: Path, name: str, data: dict) -> None:
    (store / f"{name}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def resolve(users: dict, needle: str) -> str:
    """Accept a full id, a prefix, or an email."""
    for uid, row in users.items():
        if uid == needle or row.get("email", "").lower() == needle.lower():
            return uid
    matches = [uid for uid in users if uid.startswith(needle)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"No account matches {needle!r}")
    raise SystemExit(f"{needle!r} matches {len(matches)} accounts; be specific")


def collisions(store: Path, source: str, target: str) -> list[str]:
    found: list[str] = []
    for name, field in UNIQUE_PER_USER.items():
        buckets: dict[str, dict[str, list[str]]] = defaultdict(
            lambda: {source: [], target: []}
        )
        for rid, row in load(store, name).items():
            uid = row.get("user_id")
            if uid in (source, target):
                buckets[row.get(field) or rid][uid].append(rid)
        for key, sides in buckets.items():
            if sides[source] and sides[target]:
                found.append(f"{name}.{field}={key!r}")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move one account's data onto another"
    )
    parser.add_argument("--data", type=Path, default=Path("data_phone"))
    parser.add_argument("--from", dest="source", required=True)
    parser.add_argument("--to", dest="target", required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Write the changes"
    )
    args = parser.parse_args()

    store = args.data / "store"
    if not store.is_dir():
        raise SystemExit(f"No store at {store}")

    users = load(store, "users.json".removesuffix(".json"))
    source = resolve(users, args.source)
    target = resolve(users, args.target)
    if source == target:
        raise SystemExit("--from and --to are the same account")

    print(f"from  {source[:8]}  {users[source].get('email')}")
    print(f"to    {target[:8]}  {users[target].get('email')}")
    print()

    clashes = collisions(store, source, target)
    if clashes:
        print("REFUSING: these would become duplicate keys on one account,")
        print("and every later dedupe would pick between them arbitrarily:")
        for clash in clashes:
            print(f"  {clash}")
        print()
        print("Resolve them by hand, then re-run.")
        return 1

    moves: dict[str, int] = {}
    for name in USER_SCOPED:
        rows = load(store, name)
        moves[name] = sum(
            1 for row in rows.values() if row.get("user_id") == source
        )
    for name in KEYED_BY_USER:
        moves[name] = 1 if source in load(store, name) else 0

    for name, count in moves.items():
        if count:
            print(f"  {name:18} {count:4d} row(s) -> {target[:8]}")
    following = {
        name: sum(
            1
            for row in load(store, name).values()
            if load(store, "sessions").get(
                row.get("session_id") or "", {}
            ).get("user_id")
            == source
        )
        for name in SESSION_SCOPED
    }
    for name, count in following.items():
        if count:
            print(f"  {name:18} {count:4d} row(s) follow their session")
    print(f"  users              disable {source[:8]}")
    print()

    if not args.apply:
        print("Plan only. Re-run with --apply to write it.")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = args.data / f"store-backup-{stamp}"
    shutil.copytree(store, backup)
    print(f"backup  {backup}")

    for name in USER_SCOPED:
        rows = load(store, name)
        changed = 0
        for row in rows.values():
            if row.get("user_id") == source:
                row["user_id"] = target
                changed += 1
        if changed:
            save(store, name, rows)
            print(f"  {name:18} {changed:4d} moved")

    for name in KEYED_BY_USER:
        rows = load(store, name)
        if source in rows:
            row = rows.pop(source)
            row["id"] = target
            row["user_id"] = target
            rows[target] = row
            save(store, name, rows)
            print(f"  {name:18}    1 rekeyed")

    users[source]["disabled"] = True
    save(store, "users", users)
    print(f"  users              {source[:8]} disabled")
    print()
    print("Done. A phone still signed in as the disabled account will get")
    print("401s; the app clears the rejected token and sends it to sign-in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
