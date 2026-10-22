"""Turns a baby's raw log into answers a tired parent can act on.

Predictions are learned from *this* baby's recent history, weighted toward the
same time of day and toward the last few days, so a 3 AM question is answered
from 3 AM behaviour rather than a population average. Each prediction reports
how many observations it rests on so the assistant can say how sure it is.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from .guidance import REFERENCES, Reference, triage_temperature, wake_window_prior
from .store import Baby, Event, Store, local

DAY = 86400.0


# ---------------------------------------------------------------- formatting
def fmt_minutes(minutes: float) -> str:
    minutes = int(round(minutes))
    h, m = divmod(minutes, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m or not h:
        parts.append(f"{m} minute{'s' if m != 1 else ''}")
    return " ".join(parts)


def fmt_short(minutes: float) -> str:
    minutes = int(round(minutes))
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def fmt_clock(ts: float, baby: Baby) -> str:
    return local(ts, baby).strftime("%-I:%M %p")


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------------------------------------------------------- weighting
def _tod_hours(ts: float, baby: Baby) -> float:
    d = local(ts, baby)
    return d.hour + d.minute / 60


def _circular_gap(a: float, b: float) -> float:
    g = abs(a - b) % 24
    return min(g, 24 - g)


def _weights(anchor_ts: list[float], now_ts: float, baby: Baby, tod_sigma_h: float = 2.5, half_life_days: float = 3.0) -> np.ndarray:
    now_tod = _tod_hours(now_ts, baby)
    w = []
    for ts in anchor_ts:
        gap = _circular_gap(_tod_hours(ts, baby), now_tod)
        tod_w = math.exp(-0.5 * (gap / tod_sigma_h) ** 2)
        age_days = max(0.0, (now_ts - ts) / DAY)
        rec_w = 0.5 ** (age_days / half_life_days)
        w.append(tod_w * rec_w)
    return np.asarray(w, dtype=float)


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w)
    cum /= cum[-1]
    return float(np.interp(q, cum, v))


# ---------------------------------------------------------------- predictions
@dataclass
class Prediction:
    what: str
    eta_ts: float
    low_ts: float
    high_ts: float
    typical_minutes: float
    observations: int
    basis: str
    confidence: str

    def to_dict(self, baby: Baby) -> dict[str, Any]:
        d = asdict(self)
        d.update(eta=fmt_clock(self.eta_ts, baby), low=fmt_clock(self.low_ts, baby), high=fmt_clock(self.high_ts, baby))
        return d


def _confidence(effective_n: float, spread_min: float) -> str:
    if effective_n >= 6 and spread_min <= 60:
        return "high"
    if effective_n >= 3:
        return "medium"
    return "low"


def predict_next_feed(store: Store, baby: Baby, now_ts: float | None = None) -> Prediction | None:
    now_ts = now_ts or store.clock.now()
    feeds = store.events(baby, ["feed"], since_ts=now_ts - 7 * DAY, until_ts=now_ts)
    if len(feeds) < 3:
        return None
    starts = [f.start_ts for f in feeds]
    intervals = np.diff(starts) / 60.0
    anchors = starts[:-1]
    keep = (intervals > 20) & (intervals < 6 * 60)
    intervals = intervals[keep]
    anchors = [a for a, k in zip(anchors, keep) if k]
    if len(intervals) < 2:
        return None
    w = _weights(anchors, now_ts, baby)
    median = weighted_quantile(intervals, w, 0.5)
    lo = weighted_quantile(intervals, w, 0.25)
    hi = weighted_quantile(intervals, w, 0.75)
    last = starts[-1]
    eff_n = float(w.sum() ** 2 / (w**2).sum())
    night = 22 <= _tod_hours(now_ts, baby) or _tod_hours(now_ts, baby) < 6
    period = "nighttime" if night else "daytime"
    eta = max(last + median * 60, now_ts + 5 * 60)
    return Prediction(
        what="feed",
        eta_ts=eta,
        low_ts=max(last + lo * 60, now_ts),
        high_ts=max(last + hi * 60, eta),
        typical_minutes=median,
        observations=len(intervals),
        basis=f"{baby.name}'s {period} feeds over the last 7 days come about every {fmt_minutes(median)}",
        confidence=_confidence(eff_n, hi - lo),
    )


def _sleeps(store: Store, baby: Baby, now_ts: float, days: float = 7) -> list[Event]:
    return [s for s in store.events(baby, ["sleep"], since_ts=now_ts - days * DAY, until_ts=now_ts) if s.end_ts]


def predict_next_sleep(store: Store, baby: Baby, now_ts: float | None = None) -> Prediction | None:
    """If awake: when the next sleep window opens. If asleep: when the baby will likely wake."""
    now_ts = now_ts or store.clock.now()
    age = baby.age_weeks(now_ts)
    open_sleep = store.open_sleep(baby)
    sleeps = _sleeps(store, baby, now_ts)

    if open_sleep:
        durations = np.array([s.duration_min for s in sleeps if s.duration_min], dtype=float)
        if len(durations) < 3:
            return None
        w = _weights([s.start_ts for s in sleeps], open_sleep.start_ts, baby)
        med = weighted_quantile(durations, w, 0.5)
        lo = weighted_quantile(durations, w, 0.25)
        hi = weighted_quantile(durations, w, 0.75)
        eff_n = float(w.sum() ** 2 / (w**2).sum())
        start = open_sleep.start_ts
        return Prediction(
            what="wake",
            eta_ts=max(start + med * 60, now_ts),
            low_ts=max(start + lo * 60, now_ts),
            high_ts=max(start + hi * 60, now_ts),
            typical_minutes=med,
            observations=len(durations),
            basis=f"sleeps that start around this time usually last {fmt_minutes(med)} for {baby.name}",
            confidence=_confidence(eff_n, hi - lo),
        )

    last_sleep = sleeps[-1] if sleeps else None
    prior_lo, prior_hi = wake_window_prior(age)
    prior_mid = (prior_lo + prior_hi) / 2
    windows, anchors = [], []
    for a, b in zip(sleeps, sleeps[1:]):
        gap = (b.start_ts - a.end_ts) / 60
        if 10 < gap < 5 * 60:
            windows.append(gap)
            anchors.append(a.end_ts)
    awake_since = last_sleep.end_ts if last_sleep else now_ts
    if windows:
        wv = np.array(windows)
        w = _weights(anchors, now_ts, baby)
        own_mid = weighted_quantile(wv, w, 0.5)
        own_lo = weighted_quantile(wv, w, 0.25)
        own_hi = weighted_quantile(wv, w, 0.75)
        eff_n = float(w.sum() ** 2 / (w**2).sum())
        k = eff_n / (eff_n + 4)  # how much to trust the baby's own data over the prior
        mid = k * own_mid + (1 - k) * prior_mid
        lo = k * own_lo + (1 - k) * prior_lo
        hi = k * own_hi + (1 - k) * prior_hi
        basis = f"{baby.name}'s recent awake stretches at this time of day run about {fmt_minutes(own_mid)}"
        conf = _confidence(eff_n, hi - lo)
        n = len(windows)
    else:
        mid, lo, hi = prior_mid, prior_lo, prior_hi
        basis = f"typical awake time for a {age:.0f}-week-old is {prior_lo} to {prior_hi} minutes"
        conf, n = "low", 0
    return Prediction(
        what="sleep",
        eta_ts=max(awake_since + mid * 60, now_ts),
        low_ts=max(awake_since + lo * 60, now_ts),
        high_ts=max(awake_since + hi * 60, now_ts),
        typical_minutes=mid,
        observations=n,
        basis=basis,
        confidence=conf,
    )


# ---------------------------------------------------------------- totals
@dataclass
class Totals:
    label: str
    feeds: int = 0
    breast_minutes: float = 0
    bottle_oz: float = 0
    wet: int = 0
    dirty: int = 0
    sleep_minutes: float = 0
    longest_sleep_minutes: float = 0
    max_temp_f: float | None = None


def totals_between(store: Store, baby: Baby, start_ts: float, end_ts: float, label: str = "") -> Totals:
    t = Totals(label=label)
    for e in store.events(baby, since_ts=start_ts, until_ts=end_ts):
        if e.kind == "feed" and start_ts <= e.start_ts <= end_ts:
            t.feeds += 1
            if e.data.get("method") == "bottle":
                oz = e.data.get("amount_oz") or 0
                t.bottle_oz += float(oz)
            else:
                t.breast_minutes += float(e.data.get("duration_min") or 0)
        elif e.kind == "diaper" and start_ts <= e.start_ts <= end_ts:
            k = e.data.get("type", "wet")
            if k in ("wet", "both"):
                t.wet += 1
            if k in ("dirty", "both"):
                t.dirty += 1
        elif e.kind == "sleep":
            s0 = max(e.start_ts, start_ts)
            s1 = min(e.end_ts or end_ts, end_ts)
            if s1 > s0:
                mins = (s1 - s0) / 60
                t.sleep_minutes += mins
                full = ((e.end_ts or end_ts) - e.start_ts) / 60
                t.longest_sleep_minutes = max(t.longest_sleep_minutes, full)
        elif e.kind == "temperature" and start_ts <= e.start_ts <= end_ts:
            f = float(e.data.get("temp_f", 0))
            t.max_temp_f = f if t.max_temp_f is None else max(t.max_temp_f, f)
    return t


def daily_totals(store: Store, baby: Baby, days: int, now_ts: float | None = None) -> list[Totals]:
    now_ts = now_ts or store.clock.now()
    today = local(now_ts, baby).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for i in range(days - 1, -1, -1):
        d0 = today - timedelta(days=i)
        d1 = d0 + timedelta(days=1)
        end = min(d1.timestamp(), now_ts)
        out.append(totals_between(store, baby, d0.timestamp(), end, label=d0.strftime("%a %-d %b")))
    return out


# ---------------------------------------------------------------- flags
@dataclass
class Flag:
    level: str  # "info" | "watch" | "call_now"
    speech: str
    reference: dict[str, str] | None = None
    at: str | None = None


def _ref(r: Reference | None) -> dict[str, str] | None:
    return {"statement": r.statement, "source": r.source, "url": r.url} if r else None


def logging_coverage_hours(store: Store, baby: Baby, now_ts: float) -> float:
    first = store.events(baby, since_ts=now_ts - 2 * DAY, until_ts=now_ts)
    if not first:
        return 0.0
    return (now_ts - first[0].start_ts) / 3600


def flags(store: Store, baby: Baby, now_ts: float | None = None, window_h: float = 24) -> list[Flag]:
    now_ts = now_ts or store.clock.now()
    out: list[Flag] = []
    age_days = baby.age_days(now_ts)
    covered = logging_coverage_hours(store, baby, now_ts) >= window_h
    t = totals_between(store, baby, now_ts - window_h * 3600, now_ts)

    for e in store.events(baby, ["temperature"], since_ts=now_ts - window_h * 3600, until_ts=now_ts):
        tri = triage_temperature(float(e.data["temp_f"]), baby.age_weeks(e.start_ts))
        if tri.level != "ok":
            out.append(Flag(tri.level, f"A {e.data['temp_f']:.1f} degree temperature at {fmt_clock(e.start_ts, baby)}. " + tri.speech.split(". ", 1)[-1], _ref(tri.reference), fmt_clock(e.start_ts, baby)))

    if covered and age_days >= 5 and t.wet < 6:
        out.append(
            Flag(
                "watch",
                f"Only {t.wet} wet diapers logged in the last 24 hours. Guidance is 6 or more after the first week. If that count is right and not just missed logs, mention it to your pediatrician.",
                _ref(REFERENCES["wet_diapers"]),
            )
        )
    if covered and baby.age_weeks(now_ts) < 8 and t.feeds < 8:
        out.append(
            Flag(
                "watch",
                f"{t.feeds} feeds logged in the last 24 hours. Newborns usually feed 8 to 12 times a day.",
                _ref(REFERENCES["feeds_per_day"]),
            )
        )

    # Bottle intake vs the baby's own 7-day baseline.
    base = [totals_between(store, baby, now_ts - (i + 1) * DAY, now_ts - i * DAY).bottle_oz for i in range(1, 8)]
    base = [b for b in base if b > 0]
    if covered and len(base) >= 4 and t.bottle_oz > 0:
        avg = float(np.mean(base))
        if t.bottle_oz < 0.7 * avg:
            out.append(
                Flag(
                    "info",
                    f"Bottle intake is {t.bottle_oz:.0f} ounces in the last 24 hours, below {baby.poss} usual {avg:.0f}. Worth watching.",
                )
            )
    return out


# ---------------------------------------------------------------- summaries
def next_breast_side(store: Store, baby: Baby) -> str | None:
    for e in reversed(store.events(baby, ["feed"], since_ts=store.clock.now() - 2 * DAY)):
        if e.data.get("method") == "breast":
            side = e.data.get("last_side") or e.data.get("side")
            if side == "left":
                return "right"
            if side == "right":
                return "left"
            return None
    return None


def describe_feed(e: Event) -> str:
    d = e.data
    if d.get("method") == "bottle":
        amt = d.get("amount_oz")
        what = d.get("contents", "bottle")
        return f"{amt:g} ounce {what}" if amt else what
    side = d.get("side")
    dur = d.get("duration_min")
    side_txt = {"left": "left side", "right": "right side", "both": "both sides"}.get(side or "", "breastfeed")
    return f"{side_txt}, {fmt_minutes(dur)}" if dur else side_txt


@dataclass
class Handoff:
    since: str
    hours: float
    by_caregiver: dict[str, dict[str, int]] = field(default_factory=dict)
    totals: dict[str, Any] = field(default_factory=dict)
    last_feed: str | None = None
    next_feed: dict[str, Any] | None = None
    sleep_state: str = ""
    flags: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    speech: str = ""


def handoff(store: Store, baby: Baby, since_ts: float, now_ts: float | None = None) -> Handoff:
    now_ts = now_ts or store.clock.now()
    t = totals_between(store, baby, since_ts, now_ts)
    events = store.events(baby, since_ts=since_ts, until_ts=now_ts)
    by: dict[str, dict[str, int]] = {}
    for e in events:
        if e.start_ts < since_ts:
            continue
        if e.kind == "note":
            continue
        who = e.caregiver or "someone"
        by.setdefault(who, {}).setdefault(e.kind, 0)
        by[who][e.kind] += 1
    last_feed = store.last(baby, "feed")
    pred = predict_next_feed(store, baby, now_ts)
    open_sleep = store.open_sleep(baby)
    fl = flags(store, baby, now_ts)

    pieces = [f"Since {fmt_clock(since_ts, baby)}:"]
    feed_bits = []
    if t.breast_minutes:
        feed_bits.append(f"{fmt_minutes(t.breast_minutes)} nursing")
    if t.bottle_oz:
        feed_bits.append(f"{t.bottle_oz:g} ounces by bottle")
    pieces.append(f"{t.feeds} feed{'s' if t.feeds != 1 else ''}" + (f" ({', '.join(feed_bits)})" if feed_bits else "") + ",")
    n_diapers = len([e for e in events if e.kind == "diaper" and e.start_ts >= since_ts])
    pieces.append(f"{n_diapers} diaper{'s' if n_diapers != 1 else ''}, {t.dirty} dirty,")
    pieces.append(f"and {fmt_minutes(t.sleep_minutes)} of sleep, longest stretch {fmt_minutes(t.longest_sleep_minutes)}.")
    speech = " ".join(pieces)
    if len(by) > 1:
        speech += " " + " ".join(
            f"{who} did {v.get('feed', 0)} feed{'s' if v.get('feed', 0) != 1 else ''} and {v.get('diaper', 0)} diaper{'s' if v.get('diaper', 0) != 1 else ''}." for who, v in by.items() if who != "someone"
        )
    if open_sleep:
        speech += f" {baby.name} has been asleep since {fmt_clock(open_sleep.start_ts, baby)}."
        sleep_state = f"asleep since {fmt_clock(open_sleep.start_ts, baby)}"
    else:
        last_sleep = store.last(baby, "sleep")
        sleep_state = f"awake since {fmt_clock(last_sleep.end_ts, baby)}" if last_sleep and last_sleep.end_ts else "awake"
    if last_feed:
        ago = (now_ts - last_feed.start_ts) / 60
        speech += f" Last feed was {fmt_minutes(ago)} ago."
    if pred:
        speech += f" Next one likely around {fmt_clock(pred.eta_ts, baby)}."
    notes = [e for e in events if e.kind == "note" and e.start_ts >= since_ts]
    serious = [f for f in fl if f.level == "call_now"]
    watch = [f for f in fl if f.level == "watch"]
    if serious:
        speech += " Important: " + serious[0].speech
    elif watch:
        speech += " One thing to watch: " + watch[0].speech
    else:
        speech += " Nothing flagged."
    for n in notes:
        speech += f" {n.caregiver or 'Someone'} left a note at {fmt_clock(n.start_ts, baby)}: {n.data.get('text', '').rstrip('.')}."
    return Handoff(
        since=fmt_clock(since_ts, baby),
        hours=round((now_ts - since_ts) / 3600, 1),
        by_caregiver=by,
        totals=asdict(t),
        last_feed=describe_feed(last_feed) if last_feed else None,
        next_feed=pred.to_dict(baby) if pred else None,
        sleep_state=sleep_state,
        flags=[asdict(f) for f in fl],
        notes=[{"at": fmt_clock(n.start_ts, baby), "by": n.caregiver, "text": n.data.get("text")} for n in notes],
        speech=speech,
    )


def timeline(store: Store, baby: Baby, hours: float = 24, now_ts: float | None = None) -> list[dict[str, Any]]:
    now_ts = now_ts or store.clock.now()
    out = []
    for e in store.events(baby, since_ts=now_ts - hours * 3600, until_ts=now_ts):
        out.append(
            {
                "id": e.id,
                "kind": e.kind,
                "start": e.start_ts,
                "end": e.end_ts if e.end_ts else (now_ts if e.kind == "sleep" else None),
                "open": e.kind == "sleep" and e.end_ts is None,
                "label": fmt_clock(e.start_ts, baby),
                "data": e.data,
                "caregiver": e.caregiver,
            }
        )
    return out
