"""Generate a realistic, reproducible care history for a demo baby.

The simulation runs a simple wake -> feed -> diaper -> awake -> sleep cycle with
day/night differences typical of a 6-8 week old: night sleep stretches of 2-3.5
hours, daytime naps of 30-100 minutes, awake windows of 45-80 minutes, mostly
breastfeeding with an evening bottle of expressed milk.
"""

from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .store import Store


def _is_night(dt: datetime) -> bool:
    return dt.hour >= 22 or dt.hour < 6


def seed(
    store: Store,
    name: str = "Mia",
    tz: str = "America/Los_Angeles",
    age_weeks: int = 7,
    days: int = 10,
    end_local: str | None = None,
    rng_seed: int = 7,
    caregivers: tuple[str, str] = ("Priya", "Sam"),
) -> None:
    rng = random.Random(rng_seed)
    zone = ZoneInfo(tz)
    end = datetime.fromisoformat(end_local).replace(tzinfo=zone) if end_local else datetime.fromtimestamp(store.clock.now(), zone)
    birth = (end - timedelta(weeks=age_weeks, days=2)).date()
    baby = store.add_baby(name, birth, tz, "she")
    primary, partner = caregivers

    t = end - timedelta(days=days)
    t = t.replace(hour=6, minute=40, second=0, microsecond=0)
    side = "left"
    last_feed = t - timedelta(hours=3)
    last_bottle_day = None

    def who(dt: datetime) -> str:
        # Partner covers 11 PM - 4 AM; primary otherwise.
        if dt.hour >= 23 or dt.hour < 4:
            return partner
        return primary

    while True:
        # ---- wake: feed if it's been long enough ----
        if (t - last_feed).total_seconds() > 105 * 60:
            evening = 19 <= t.hour <= 23
            if evening and last_bottle_day != t.date() and rng.random() < 0.9:
                oz = rng.choice([2.5, 3.0, 3.0, 3.5])
                dur = rng.randint(10, 15)
                store.add_event(baby, "feed", t.timestamp(), (t + timedelta(minutes=dur)).timestamp(),
                                {"method": "bottle", "amount_oz": oz, "contents": "breast milk bottle"}, partner)
                last_bottle_day = t.date()
            else:
                dur = rng.randint(12, 24) if not _is_night(t) else rng.randint(10, 18)
                store.add_event(baby, "feed", t.timestamp(), (t + timedelta(minutes=dur)).timestamp(),
                                {"method": "breast", "side": side, "last_side": side, "duration_min": dur}, who(t))
                side = "right" if side == "left" else "left"
            last_feed = t
            t += timedelta(minutes=dur + rng.randint(1, 4))
            # diaper after (most) feeds
            if rng.random() < 0.92:
                kind = "both" if rng.random() < 0.33 else "wet"
                store.add_event(baby, "diaper", t.timestamp(), None, {"type": kind}, who(t))
                t += timedelta(minutes=rng.randint(2, 5))

        # ---- awake window ----
        if _is_night(t):
            awake = rng.randint(8, 20)
        else:
            awake = rng.randint(30, 60)
        t += timedelta(minutes=awake)
        if t >= end:
            break

        # ---- sleep ----
        if _is_night(t):
            sleep = rng.randint(140, 205)
        elif t.hour >= 19:
            sleep = rng.randint(50, 110)
        else:
            sleep = rng.randint(35, 95)
        s_end = t + timedelta(minutes=sleep)
        if s_end >= end:
            # Leave the baby waking up right at the end of the history.
            s_end = end
            store.add_event(baby, "sleep", t.timestamp(), s_end.timestamp(), {}, who(t))
            break
        store.add_event(baby, "sleep", t.timestamp(), s_end.timestamp(), {}, who(t))
        t = s_end
        # occasional extra wet diaper on waking
        if rng.random() < 0.25:
            store.add_event(baby, "diaper", t.timestamp(), None, {"type": "wet"}, who(t))


def main() -> None:
    p = argparse.ArgumentParser(description="Seed a Swaddle database with demo history")
    p.add_argument("--db", default="data/swaddle.db")
    p.add_argument("--end", default=None, help="local end time of history, e.g. 2026-10-23T02:55")
    p.add_argument("--tz", default="America/Los_Angeles")
    args = p.parse_args()
    store = Store(args.db)
    store.clear()
    seed(store, tz=args.tz, end_local=args.end)
    print(f"seeded {args.db}")


if __name__ == "__main__":
    main()
