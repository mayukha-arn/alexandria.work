"""Apply the Meridian roster's time zones to existing accounts without reseeding data.

Run on the VM as alexapi with --db /var/lib/alexandria/app.db.
"""
import argparse
import pathlib
import sqlite3
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from seed.meridian.content import PEOPLE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=pathlib.Path)
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error("database does not exist")
    changed = 0
    with sqlite3.connect(args.db) as conn:
        for username, _, _, timezone in PEOPLE:
            ZoneInfo(timezone)
            changed += conn.execute(
                "UPDATE users SET timezone = ? WHERE username = ? AND (timezone IS NULL OR timezone != ?)",
                (timezone, username, timezone),
            ).rowcount
    print(f"Updated time zones for {changed} Meridian accounts.")


if __name__ == "__main__":
    main()
