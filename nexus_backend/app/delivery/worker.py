"""Bounded delivery worker entrypoint.

The dispatcher already commits the send marker before touching the network and
settles through a fenced update afterwards. This module only supplies the loop
and the environment-selected channel; it never retries an ``unknown`` outcome
and never treats channel acceptance as customer delivery.
"""

import argparse
import os
import time

from .followups import DeliveryFollowupService, channel_from_env


def run_once(db, channel=None, limit=20):
    channel = channel or channel_from_env(os.environ.get("DELIVERY_CHANNEL"))
    return DeliveryFollowupService(db).dispatch(channel, limit)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Drain pending delivery notifications safely")
    parser.add_argument("--once", action="store_true", help="dispatch one batch and exit")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    if not 1 <= args.interval <= 3600:
        parser.error("--interval must be between 1 and 3600 seconds")
    from ..accounting.database import Database

    dsn = os.environ.get("ACCOUNTING_DATABASE_URL", "").strip()
    if not dsn:
        parser.error("ACCOUNTING_DATABASE_URL is required")
    database = Database(dsn)
    try:
        while True:
            result = run_once(database, limit=args.limit)
            if args.once:
                return 0 if result.get("blocked", 0) == 0 else 2
            time.sleep(args.interval)
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
