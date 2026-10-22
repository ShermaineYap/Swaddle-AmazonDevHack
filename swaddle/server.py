"""Swaddle MCP server: hands-free baby care for Alexa+.

Run:  python -m swaddle.server            (Streamable HTTP on http://localhost:8000/mcp)
The simulated Alexa+ web client is served at http://localhost:8000/
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.apps import Apps, client_supports_apps
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from . import insights as I
from .guidance import DISCLAIMER, REFERENCES, to_fahrenheit, triage_temperature
from .store import Baby, Clock, Store, local

ROOT = Path(__file__).resolve().parent
WEB = ROOT.parent / "web"
TZ = os.environ.get("SWADDLE_TZ", "America/Los_Angeles")
DEMO = os.environ.get("SWADDLE_DEMO", "1") == "1"

clock = Clock(os.environ.get("SWADDLE_DEMO_TIME"), TZ)
store = Store(os.environ.get("SWADDLE_DB", str(ROOT.parent / "data" / "swaddle.db")), clock)

INSTRUCTIONS = """Swaddle keeps a shared care log for a newborn and answers questions about it.
Use it whenever a parent or caregiver mentions feeding, nursing, bottles, diapers, naps, sleep,
waking up, temperature or fever, or asks what happened, when something last happened, or what
comes next. Parents usually have their hands full: read the `speech` field aloud as-is, keep
follow-ups to one short question, and never add medical advice beyond what a tool returns.
If a tool returns a call_now flag, say it first."""

mcp_server = MCPServer(
    name="swaddle",
    title="Swaddle — hands-free baby care",
    description="Log feeds, diapers, sleep and temperatures by voice; get predictions learned from your baby's own patterns; hand off shifts; share a pediatrician-ready report.",
    instructions=INSTRUCTIONS,
    version="1.0.0",
    website_url="https://github.com/",
)
apps = Apps()


# --------------------------------------------------------------------------- models
class Ref(BaseModel):
    statement: str
    source: str
    url: str


class Spoken(BaseModel):
    speech: str = Field(description="One or two short sentences to read aloud to the caregiver.")


class Logged(Spoken):
    event_id: int
    kind: str
    at: str
    baby: str
    caregiver: str | None = None
    details: dict[str, Any] = {}
    next_feed: dict[str, Any] | None = None


class TemperatureLogged(Logged):
    level: Literal["ok", "watch", "call_now"]
    reference: Ref | None = None
    disclaimer: str = DISCLAIMER


class LastEvent(Spoken):
    kind: str
    found: bool
    at: str | None = None
    minutes_ago: float | None = None
    details: dict[str, Any] = {}
    next_side: str | None = None


class Predictions(Spoken):
    baby: str
    next_feed: dict[str, Any] | None
    next_sleep_or_wake: dict[str, Any] | None
    asleep: bool


class HandoffOut(Spoken):
    baby: str
    since: str
    hours: float
    by_caregiver: dict[str, dict[str, int]]
    totals: dict[str, Any]
    last_feed: str | None
    next_feed: dict[str, Any] | None
    sleep_state: str
    flags: list[dict[str, Any]]
    notes: list[dict[str, Any]] = []


class Report(Spoken):
    baby: str
    age_weeks: float
    days: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    flags: list[dict[str, Any]]
    predictions: dict[str, Any]
    references: list[Ref]
    disclaimer: str = DISCLAIMER
    generated_at: str
    now_ts: float
    tz_offset_hours: float


class Undone(Spoken):
    removed: bool
    kind: str | None = None
    at: str | None = None


class BabyProfile(Spoken):
    name: str
    birth_date: str
    age_weeks: float


class BottleAmount(BaseModel):
    amount: float = Field(description="How much the baby drank", gt=0, le=12 * 30)
    unit: Literal["oz", "ml"] = Field(default="oz", description="Ounces or milliliters")


# --------------------------------------------------------------------------- helpers
def _result(model: BaseModel) -> CallToolResult:
    """Speech goes in the text block (what a voice assistant reads); everything else is structured."""
    return CallToolResult(
        content=[TextContent(type="text", text=model.speech)],  # type: ignore[attr-defined]
        structured_content=model.model_dump(mode="json"),
    )


def _baby(name: str | None) -> Baby:
    return store.baby(name)


def _ago(minutes_ago: float | None) -> float:
    return clock.now() - 60 * float(minutes_ago or 0)


def _next_feed_sentence(baby: Baby) -> tuple[str, dict[str, Any] | None]:
    p = I.predict_next_feed(store, baby)
    if not p:
        return "", None
    return f" {baby.pronoun.capitalize()}'ll likely be hungry again around {I.fmt_clock(p.eta_ts, baby)}.", p.to_dict(baby)


def _feeds_since_midnight(baby: Baby) -> int:
    from .store import local

    now = clock.now()
    midnight = local(now, baby).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return len(store.events(baby, ["feed"], since_ts=midnight, until_ts=now))


def _supports_elicitation(ctx: Context) -> bool:
    caps = ctx.client_capabilities
    return bool(caps and getattr(caps, "elicitation", None) is not None)


READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


# --------------------------------------------------------------------------- tools: logging
@mcp_server.tool(
    title="Log a feed",
    description="Record a breastfeed or bottle feed that just ended (or ended some minutes ago). "
    "Use for 'she just ate', 'fed on the left for 15 minutes', 'gave him a 3 ounce bottle'. "
    "If a bottle amount is missing, Swaddle asks for it.",
    annotations=WRITE,
)
async def log_feed(
    ctx: Context,
    method: Annotated[Literal["breast", "bottle"], Field(description="breast = nursing, bottle = any bottle")],
    side: Annotated[Literal["left", "right", "both"] | None, Field(description="Breast side, for nursing")] = None,
    duration_minutes: Annotated[int | None, Field(description="How long the feed lasted", ge=1, le=120)] = None,
    amount: Annotated[float | None, Field(description="Bottle amount", gt=0)] = None,
    unit: Annotated[Literal["oz", "ml"], Field(description="Bottle unit")] = "oz",
    contents: Annotated[Literal["breast milk", "formula"] | None, Field(description="What was in the bottle")] = None,
    ended_minutes_ago: Annotated[int, Field(description="0 if it just ended", ge=0, le=24 * 60)] = 0,
    baby: Annotated[str | None, Field(description="Baby's name if the household has more than one")] = None,
    caregiver: Annotated[str | None, Field(description="Who did the feed (from voice ID), e.g. 'Sam'")] = None,
) -> Annotated[CallToolResult, Logged]:
    b = _baby(baby)
    end = _ago(ended_minutes_ago)
    data: dict[str, Any] = {"method": method}
    asked = False
    if method == "bottle":
        if amount is None and _supports_elicitation(ctx):
            res = await ctx.elicit(f"How much did {b.name} drink from the bottle?", BottleAmount)
            if res.action == "accept" and res.data:
                amount, unit = res.data.amount, res.data.unit
            asked = True
        oz = None if amount is None else (amount / 29.5735 if unit == "ml" else amount)
        data.update(amount_oz=round(oz, 1) if oz else None, amount=amount, unit=unit, contents=f"{contents or 'breast milk'} bottle")
        dur = duration_minutes or 10
    else:
        dur = duration_minutes or 15
        data.update(side=side, last_side=side if side != "both" else None, duration_min=duration_minutes)
    start = end - dur * 60
    e = store.add_event(b, "feed", start, end, data, caregiver)

    desc = I.describe_feed(e)
    speech = f"Logged {desc}, finished at {I.fmt_clock(end, b)}."
    if method == "breast" and side in ("left", "right"):
        speech += f" Next time, start on the {'right' if side == 'left' else 'left'}."
    if method == "bottle" and amount is None:
        speech = f"Logged a bottle at {I.fmt_clock(end, b)}. How much did {b.pronoun} drink?" if not asked else f"Logged a bottle at {I.fmt_clock(end, b)} without an amount."
    n = _feeds_since_midnight(b)
    speech += f" That's feed {n} since midnight."
    nf, nf_d = _next_feed_sentence(b)
    speech += nf
    return _result(Logged(speech=speech, event_id=e.id, kind="feed", at=I.fmt_clock(end, b), baby=b.name, caregiver=caregiver, details=data, next_feed=nf_d))


@mcp_server.tool(
    title="Add a bottle amount",
    description="Add or correct the amount on the most recent bottle feed, e.g. after 'how much did she drink?' -> 'three ounces'.",
    annotations=WRITE,
)
def set_bottle_amount(
    amount: Annotated[float, Field(gt=0)],
    unit: Literal["oz", "ml"] = "oz",
    baby: str | None = None,
) -> Annotated[CallToolResult, Logged]:
    b = _baby(baby)
    e = next((f for f in reversed(store.events(b, ["feed"], since_ts=clock.now() - 86400)) if f.data.get("method") == "bottle"), None)
    if not e:
        return _result(Logged(speech="I couldn't find a bottle in the last day to update.", event_id=-1, kind="feed", at="", baby=b.name))
    oz = amount / 29.5735 if unit == "ml" else amount
    e.data.update(amount=amount, unit=unit, amount_oz=round(oz, 1))
    store.update_event(e)
    return _result(Logged(speech=f"Updated: {amount:g} {'ounces' if unit == 'oz' else 'milliliters'} at {I.fmt_clock(e.end_ts or e.start_ts, b)}.", event_id=e.id, kind="feed", at=I.fmt_clock(e.start_ts, b), baby=b.name, details=e.data))


@mcp_server.tool(
    title="Log a diaper",
    description="Record a diaper change: wet, dirty (poop), or both.",
    annotations=WRITE,
)
def log_diaper(
    type: Annotated[Literal["wet", "dirty", "both"], Field(description="wet, dirty (poop) or both")],
    minutes_ago: Annotated[int, Field(ge=0, le=24 * 60)] = 0,
    baby: str | None = None,
    caregiver: str | None = None,
) -> Annotated[CallToolResult, Logged]:
    b = _baby(baby)
    at = _ago(minutes_ago)
    e = store.add_event(b, "diaper", at, None, {"type": type}, caregiver)
    t = I.totals_between(store, b, clock.now() - 86400, clock.now())
    word = {"wet": "Wet", "dirty": "Dirty", "both": "Wet and dirty"}[type]
    speech = f"{word} diaper logged. {t.wet} wet and {t.dirty} dirty in the last 24 hours."
    if t.wet >= 6:
        speech += " Right on track."
    return _result(Logged(speech=speech, event_id=e.id, kind="diaper", at=I.fmt_clock(at, b), baby=b.name, caregiver=caregiver, details={"type": type}))


@mcp_server.tool(
    title="Baby fell asleep",
    description="Record that the baby just fell asleep (or fell asleep some minutes ago).",
    annotations=WRITE,
)
def start_sleep(
    minutes_ago: Annotated[int, Field(ge=0, le=12 * 60)] = 0,
    baby: str | None = None,
    caregiver: str | None = None,
) -> Annotated[CallToolResult, Logged]:
    b = _baby(baby)
    open_s = store.open_sleep(b)
    at = _ago(minutes_ago)
    if open_s:
        open_s.start_ts = at
        store.update_event(open_s)
        e = open_s
    else:
        e = store.add_event(b, "sleep", at, None, {}, caregiver)
    p = I.predict_next_sleep(store, b)
    speech = f"Got it, {b.name} fell asleep at {I.fmt_clock(at, b)}."
    if p:
        speech += f" Sleeps that start around now usually last about {I.fmt_minutes(p.typical_minutes)}, so maybe until {I.fmt_clock(p.eta_ts, b)}."
    return _result(Logged(speech=speech, event_id=e.id, kind="sleep", at=I.fmt_clock(at, b), baby=b.name, caregiver=caregiver, next_feed=p.to_dict(b) if p else None))


@mcp_server.tool(
    title="Baby woke up",
    description="Record that the baby just woke up (or woke some minutes ago). Closes the current sleep.",
    annotations=WRITE,
)
def end_sleep(
    minutes_ago: Annotated[int, Field(ge=0, le=12 * 60)] = 0,
    baby: str | None = None,
    caregiver: str | None = None,
) -> Annotated[CallToolResult, Logged]:
    b = _baby(baby)
    at = _ago(minutes_ago)
    s = store.open_sleep(b)
    if not s:
        return _result(Logged(speech=f"I didn't have {b.name} down as asleep. Tell me when {b.pronoun} fell asleep and I'll add the nap.", event_id=-1, kind="sleep", at="", baby=b.name))
    s.end_ts = max(at, s.start_ts + 60)
    store.update_event(s)
    p = I.predict_next_sleep(store, b)
    speech = f"{b.name} slept {I.fmt_minutes(s.duration_min or 0)}."
    if p:
        speech += f" Next sleep window opens around {I.fmt_clock(p.low_ts, b)} to {I.fmt_clock(p.high_ts, b)}."
    return _result(Logged(speech=speech, event_id=s.id, kind="sleep", at=I.fmt_clock(at, b), baby=b.name, caregiver=caregiver, details={"duration_min": round(s.duration_min or 0)}))


@mcp_server.tool(
    title="Log a temperature",
    description="Record a temperature reading and check it against American Academy of Pediatrics 'when to call' thresholds for the baby's age. "
    "Always read the result aloud; if level is call_now, say it first.",
    annotations=WRITE,
)
def log_temperature(
    value: Annotated[float, Field(description="The reading", gt=30, lt=110)],
    unit: Annotated[Literal["F", "C"], Field(description="F or C; readings under 45 are treated as Celsius")] = "F",
    method: Annotated[Literal["rectal", "armpit", "forehead", "ear", "unknown"], Field()] = "unknown",
    baby: str | None = None,
    caregiver: str | None = None,
) -> Annotated[CallToolResult, TemperatureLogged]:
    b = _baby(baby)
    if value < 45:
        unit = "C"
    temp_f = round(to_fahrenheit(value, unit), 1)
    now = clock.now()
    # How long has there been a fever? Look back through consecutive febrile readings.
    fever_hours = None
    readings = store.events(b, ["temperature"], since_ts=now - 3 * 86400)
    febrile = [r for r in readings if r.data.get("temp_f", 0) >= 100.4]
    if temp_f >= 100.4 and febrile:
        fever_hours = (now - febrile[0].start_ts) / 3600
    tri = triage_temperature(temp_f, b.age_weeks(now), fever_hours)
    e = store.add_event(b, "temperature", now, None, {"temp_f": temp_f, "value": value, "unit": unit, "method": method, "level": tri.level}, caregiver)
    ref = Ref(statement=tri.reference.statement, source=tri.reference.source, url=tri.reference.url) if tri.reference else None
    speech = tri.speech
    if tri.level == "call_now":
        speech += " I've flagged it in the log so whoever calls has the details."
    return _result(TemperatureLogged(speech=speech, event_id=e.id, kind="temperature", at=I.fmt_clock(now, b), baby=b.name, caregiver=caregiver, details={"temp_f": temp_f, "method": method}, level=tri.level, reference=ref))


@mcp_server.tool(
    title="Leave a note",
    description="Leave a short note for the other caregivers, read out at the next shift handoff, e.g. 'note: called the nurse line, bring her in at 9'.",
    annotations=WRITE,
)
def add_note(
    text: Annotated[str, Field(min_length=1, max_length=280)],
    baby: str | None = None,
    caregiver: str | None = None,
) -> Annotated[CallToolResult, Logged]:
    b = _baby(baby)
    now = clock.now()
    e = store.add_event(b, "note", now, None, {"text": text.strip()}, caregiver)
    return _result(Logged(speech="Noted. I'll read it out at the next handoff.", event_id=e.id, kind="note", at=I.fmt_clock(now, b), baby=b.name, caregiver=caregiver, details={"text": text}))


# --------------------------------------------------------------------------- tools: questions
@mcp_server.tool(
    title="When did it last happen?",
    description="Answer 'when did she last eat', 'which side was last', 'when was the last poopy diaper', 'how long has he been asleep'.",
    annotations=READ_ONLY,
)
def get_last(
    kind: Annotated[Literal["feed", "diaper", "dirty_diaper", "sleep", "temperature"], Field()],
    baby: str | None = None,
) -> Annotated[CallToolResult, LastEvent]:
    b = _baby(baby)
    now = clock.now()
    if kind == "dirty_diaper":
        e = next((d for d in reversed(store.events(b, ["diaper"], since_ts=now - 3 * 86400)) if d.data.get("type") in ("dirty", "both")), None)
    else:
        e = store.last(b, kind)
    if not e:
        return _result(LastEvent(speech=f"I don't have any {kind.replace('_', ' ')} logged for {b.name} yet.", kind=kind, found=False))
    ref_ts = e.end_ts if (kind == "feed" and e.end_ts) else e.start_ts
    ago = (now - ref_ts) / 60
    side = I.next_breast_side(store, b) if kind == "feed" else None
    if kind == "feed":
        speech = f"Last feed ended {I.fmt_minutes(ago)} ago, at {I.fmt_clock(ref_ts, b)}: {I.describe_feed(e)}."
        if side:
            speech += f" Start on the {side} next."
    elif kind == "sleep":
        if e.end_ts is None:
            speech = f"{b.name} has been asleep for {I.fmt_minutes(ago)}, since {I.fmt_clock(e.start_ts, b)}."
        else:
            speech = f"{b.name} woke up {I.fmt_minutes((now - e.end_ts) / 60)} ago after sleeping {I.fmt_minutes(e.duration_min or 0)}."
    elif kind == "temperature":
        speech = f"Last temperature was {e.data['temp_f']:.1f} degrees, {I.fmt_minutes(ago)} ago."
    else:
        speech = f"Last {'dirty ' if kind == 'dirty_diaper' else ''}diaper was {I.fmt_minutes(ago)} ago, at {I.fmt_clock(e.start_ts, b)} ({e.data.get('type')})."
    return _result(LastEvent(speech=speech, kind=kind, found=True, at=I.fmt_clock(ref_ts, b), minutes_ago=round(ago), details=e.data, next_side=side))


@mcp_server.tool(
    title="What's next?",
    description="Predict when the baby will next be hungry and when the next sleep (or waking) is likely, learned from this baby's own recent patterns at this time of day.",
    annotations=READ_ONLY,
)
def predict_next(baby: str | None = None) -> Annotated[CallToolResult, Predictions]:
    b = _baby(baby)
    feed = I.predict_next_feed(store, b)
    sleep = I.predict_next_sleep(store, b)
    asleep = store.open_sleep(b) is not None
    parts = []
    if feed:
        period = "nighttime" if "nighttime" in feed.basis else "daytime"
        parts.append(
            f"{b.poss.capitalize()} {period} feeds this past week came about every {I.fmt_minutes(feed.typical_minutes)}, "
            f"so expect {b.obj} hungry around {I.fmt_clock(feed.eta_ts, b)}, most likely between {I.fmt_clock(feed.low_ts, b)} and {I.fmt_clock(feed.high_ts, b)}."
        )
    if sleep:
        if sleep.what == "wake":
            parts.append(f"{b.pronoun.capitalize()} will probably wake around {I.fmt_clock(sleep.eta_ts, b)}.")
        else:
            parts.append(f"Next sleep window: about {I.fmt_clock(sleep.low_ts, b)} to {I.fmt_clock(sleep.high_ts, b)}.")
    if not parts:
        parts.append(f"I need a few more logged feeds and naps before I can predict {b.poss} rhythm.")
    return _result(Predictions(speech=" ".join(parts), baby=b.name, next_feed=feed.to_dict(b) if feed else None, next_sleep_or_wake=sleep.to_dict(b) if sleep else None, asleep=asleep))


@mcp_server.tool(
    title="Shift handoff",
    description="Catch up the next caregiver: what happened over the last N hours (feeds, diapers, sleep, who did what), what's coming next, and anything flagged. "
    "Use for 'catch me up', 'how was the night', 'what did I miss'.",
    annotations=READ_ONLY,
)
def shift_handoff(
    since_hours: Annotated[float, Field(description="How far back to summarize", gt=0, le=48)] = 8,
    baby: str | None = None,
) -> Annotated[CallToolResult, HandoffOut]:
    b = _baby(baby)
    h = I.handoff(store, b, clock.now() - since_hours * 3600)
    return _result(HandoffOut(speech=h.speech, baby=b.name, since=h.since, hours=h.hours, by_caregiver=h.by_caregiver, totals=h.totals, last_feed=h.last_feed, next_feed=h.next_feed, sleep_state=h.sleep_state, flags=h.flags, notes=h.notes))


REPORT_URI = "ui://swaddle/report.html"


@apps.tool(
    resource_uri=REPORT_URI,
    name="care_report",
    title="Care report",
    description="Show the baby's last N days (feeds, intake, diapers, sleep, temperatures, flags with sources) on screen, e.g. 'show me her week' or 'get a report ready for the pediatrician'. "
    "Renders an interactive view on devices with a screen; speaks a short summary otherwise.",
    annotations=READ_ONLY,
)
def care_report(
    ctx: Context,
    days: Annotated[int, Field(ge=1, le=30)] = 7,
    baby: str | None = None,
) -> Annotated[CallToolResult, Report]:
    b = _baby(baby)
    now = clock.now()
    daily = I.daily_totals(store, b, days)
    full = [d for d in daily[:-1]] or daily
    import numpy as np

    avg_feeds = float(np.mean([d.feeds for d in full]))
    avg_wet = float(np.mean([d.wet for d in full]))
    avg_sleep = float(np.mean([d.sleep_minutes for d in full]))
    fl = I.flags(store, b)
    temps = [e for e in store.events(b, ["temperature"], since_ts=now - days * 86400)]
    speech = (
        f"Over the past {len(full) + 1} days {b.name} averaged {avg_feeds:.0f} feeds, {avg_wet:.0f} wet diapers and {I.fmt_minutes(avg_sleep)} of sleep a day."
    )
    if temps:
        hi = max(t.data["temp_f"] for t in temps)
        speech += f" Highest temperature logged: {hi:.1f} degrees."
    if fl:
        speech += f" {len(fl)} item{'s' if len(fl) != 1 else ''} flagged."
    if client_supports_apps(ctx):
        speech += " It's on your screen."
    pf, ps = I.predict_next_feed(store, b), I.predict_next_sleep(store, b)
    refs = [Ref(statement=r.statement, source=r.source, url=r.url) for r in REFERENCES.values()]
    rep = Report(
        speech=speech,
        baby=b.name,
        age_weeks=round(b.age_weeks(now), 1),
        days=[d.__dict__ for d in daily],
        timeline=I.timeline(store, b, 24),
        flags=[f.__dict__ for f in fl],
        predictions={"feed": pf.to_dict(b) if pf else None, "sleep": ps.to_dict(b) if ps else None},
        references=refs,
        generated_at=I.fmt_clock(now, b),
        now_ts=now,
        tz_offset_hours=local(now, b).utcoffset().total_seconds() / 3600,
    )
    return _result(rep)


apps.add_html_resource(
    REPORT_URI,
    (ROOT / "ui" / "report.html").read_text(),
    name="swaddle-report",
    title="Swaddle care report",
    description="Interactive 24-hour timeline and multi-day trends for a baby's care log.",
    prefers_border=False,
)
# Apps tools/resources are declared above, so attach the extension now (it is additive: no tool-call interception).
mcp_server._apply_extension(apps)


# --------------------------------------------------------------------------- tools: housekeeping
@mcp_server.tool(
    title="Undo last entry",
    description="Remove the most recent log entry, for 'undo that', 'scratch that', 'that was wrong'.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
)
def undo_last(baby: str | None = None) -> Annotated[CallToolResult, Undone]:
    b = _baby(baby)
    e = store.last_created(b)
    if not e:
        return _result(Undone(speech="There's nothing to undo.", removed=False))
    store.delete_event(e.id)
    what = {"feed": "feed", "diaper": "diaper", "sleep": "sleep", "temperature": "temperature reading", "note": "note"}.get(e.kind, e.kind)
    return _result(Undone(speech=f"Removed the {what} from {I.fmt_clock(e.start_ts, b)}.", removed=True, kind=e.kind, at=I.fmt_clock(e.start_ts, b)))


@mcp_server.tool(
    title="Set up a baby",
    description="Create a baby profile (name and birth date) the first time the household uses Swaddle.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def setup_baby(
    name: Annotated[str, Field(min_length=1, max_length=40)],
    birth_date: Annotated[date, Field(description="YYYY-MM-DD")],
    pronoun: Literal["she", "he", "they"] = "they",
) -> Annotated[CallToolResult, BabyProfile]:
    try:
        b = store.baby(name)
    except LookupError:
        b = store.add_baby(name, birth_date, TZ, pronoun)
    age = b.age_weeks(clock.now())
    return _result(BabyProfile(speech=f"{b.name} is set up, {age:.0f} weeks old. Just tell me about feeds, diapers and sleep as they happen.", name=b.name, birth_date=b.birth_date.isoformat(), age_weeks=round(age, 1)))


@mcp_server.resource("swaddle://guidance", name="guidance", title="Pediatric reference points", description="The published thresholds Swaddle compares a log against, with sources.", mime_type="application/json")
def guidance_resource() -> dict[str, Any]:
    return {"disclaimer": DISCLAIMER, "references": [r.__dict__ for r in REFERENCES.values()]}


# --------------------------------------------------------------------------- web client + demo controls
@mcp_server.custom_route("/", methods=["GET"])
async def index(request: Request) -> Response:
    return FileResponse(WEB / "index.html")


@mcp_server.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> Response:
    return PlainTextResponse("ok")


@mcp_server.custom_route("/demo/clock", methods=["GET", "POST"])
async def demo_clock(request: Request) -> Response:
    """Demo-only: read or advance the server clock (to jump from 3 AM to the morning handoff)."""
    if request.method == "POST":
        if not DEMO:
            return JSONResponse({"error": "demo controls disabled"}, status_code=403)
        body = await request.json()
        clock.shift(float(body.get("minutes", 0)) * 60)
    try:
        b = store.baby()
        label = I.fmt_clock(clock.now(), b)
    except LookupError:
        label = ""
    return JSONResponse({"now": clock.now(), "label": label, "tz": TZ})


def build_app():
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.middleware.cors import CORSMiddleware

    public = os.environ.get("SWADDLE_PUBLIC", "0") == "1"
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False) if public else None
    app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=security,
        host="0.0.0.0" if public else "127.0.0.1",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id", "Mcp-Protocol-Version"],
    )
    return app


def ensure_seeded() -> None:
    if not store.babies() and os.environ.get("SWADDLE_SEED", "1") == "1":
        from .seed import seed

        seed(store, tz=TZ, end_local=os.environ.get("SWADDLE_SEED_END"))


def main() -> None:
    import uvicorn

    ensure_seeded()
    port = int(os.environ.get("PORT", "8000"))
    host = "0.0.0.0" if os.environ.get("SWADDLE_PUBLIC", "0") == "1" else "127.0.0.1"
    uvicorn.run(build_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
