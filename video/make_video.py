"""Build the submission demo video end to end.

1. Starts a Swaddle server with a frozen demo clock (3:12 AM) and seeded history.
2. Drives the simulated Alexa+ client in Chromium, capturing a screenshot per beat
   and recording the real text each tool returned.
3. Voices every line with Kokoro TTS (narrator, two caregivers, the assistant).
4. Assembles stills + audio into an H.264 MP4 with ffmpeg.

Usage: python video/make_video.py --kokoro-dir /path/to/kokoro  (writes video/build/swaddle_demo.mp4)
Everything shown in the video is the live server's real output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "video" / "build"

VOICES = {"narrator": ("af_heart", 1.0), "Sam": ("am_michael", 1.0), "Priya": ("af_nicole", 1.0), "assistant": ("bf_emma", 1.12)}


@dataclass
class Seg:
    image: str
    lines: list[tuple[str, str]] = field(default_factory=list)  # (voice, text)
    min_dur: float = 0.0
    pad: float = 0.35


def tts_normalize(text: str) -> str:
    text = re.sub(r"\b(\d{1,2}):00\s*(AM|PM)", r"\1 \2", text)
    text = re.sub(r"\b(\d{1,2}):0(\d)", r"\1 oh \2", text)
    text = re.sub(r"\b(\d{1,2}):(\d\d)", r"\1 \2", text)
    text = text.replace("AM", "A M").replace("PM", "P M").replace("Alexa+", "Alexa Plus")
    return text


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def capture(base: str) -> list[Seg]:
    from playwright.async_api import async_playwright

    segs: list[Seg] = []
    n = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        async def shot() -> str:
            nonlocal n
            n += 1
            path = BUILD / f"f{n:03d}.png"
            await page.screenshot(path=str(path))
            return str(path)

        async def slide(i: int, *lines: tuple[str, str], min_dur: float = 0) -> None:
            await page.goto(f"file://{ROOT / 'video' / 'slides.html'}?s={i}")
            await page.wait_for_timeout(300)
            segs.append(Seg(await shot(), list(lines), min_dur=min_dur, pad=0.6))

        async def settle() -> None:
            await page.wait_for_function(
                "() => { const r = document.getElementById('reply').textContent; const ask = document.getElementById('ask').classList.contains('on'); return ask || (r && r !== '…'); }",
                timeout=15000,
            )
            await page.wait_for_timeout(350)

        async def say(speaker: str, text: str, spoken: str | None = None, *, answer: bool = False) -> str:
            await page.evaluate("([t, s]) => window.swaddle.showUtterance(t, s)", [text, speaker])
            await page.wait_for_timeout(150)
            segs.append(Seg(await shot(), [(speaker, spoken or text)], pad=0.25))
            if answer:
                await page.evaluate("() => document.getElementById('reply').textContent = '…'")
            await page.evaluate("t => { window.swaddle.handle(t); }", text)
            await settle()
            if await page.evaluate("() => document.getElementById('ask').classList.contains('on')"):
                q = await page.text_content("#askq")
                segs.append(Seg(await shot(), [("assistant", q or "")]))
                return q or ""
            if await page.evaluate("() => document.getElementById('appframe').classList.contains('on')"):
                await page.wait_for_timeout(1500)
            reply = (await page.text_content("#reply")) or ""
            segs.append(Seg(await shot(), [("assistant", reply)]))
            return reply

        async def clock(minutes: float) -> None:
            await page.evaluate("m => window.swaddle.advanceClock(m)", minutes)
            await page.wait_for_timeout(200)

        async def call(name: str, args: dict) -> dict:
            return await page.evaluate("([n, a]) => window.swaddle.mcp.call(n, a)", [name, args])

        async def caption(text: str, *lines: tuple[str, str], min_dur: float = 0) -> None:
            await page.evaluate("t => window.swaddle.caption(t)", text)
            await page.wait_for_timeout(100)
            segs.append(Seg(await shot(), list(lines), min_dur=min_dur, pad=0.5))
            await page.evaluate("() => window.swaddle.caption('')")

        # ------------------------------------------------------------------ story
        await slide(1, ("narrator", "It's 3:12 AM. Sam has a newborn in one arm and a bottle in the other. "
                                     "Pediatricians want every feed, diaper and fever tracked. Today that means a phone, one thumb, in the dark."))
        await slide(2, ("narrator", "Meet Swaddle: hands-free baby care for Alexa Plus, built as an MCP server, shown in a simulated Alexa Plus client."))

        await page.goto(base + "/")
        await page.wait_for_function("() => Object.keys(window.swaddle.mcp.tools).length > 0", timeout=15000)
        await page.wait_for_timeout(600)
        await caption("3:12 AM · Sam's night shift", min_dur=1.6)

        await say("Sam", "She just finished feeding on the left, about fifteen minutes")
        await clock(1)
        await say("Sam", "I topped her up with a bottle too")
        await say("Sam", "About two ounces", answer=True)
        await say("Sam", "When will she be hungry again?")
        await clock(2)
        await say("Sam", "She feels warm. Her temperature is 100.6", "She feels warm. Her temperature is a hundred point six.")
        await clock(6)
        await say("Sam", "Leave a note: called the nurse line, they said bring her in at 9 for a check", "Leave a note: called the nurse line, they said bring her in at nine for a check.")
        await clock(9)
        await say("Sam", "She's asleep")

        # Off camera: Mia wakes inside the predicted window; Sam logs one feed and a diaper.
        pred = await call("predict_next", {})
        sc = pred.get("structuredContent", {})
        wake = (sc.get("next_sleep_or_wake") or {})
        feed = (sc.get("next_feed") or {})
        clk = httpx.get(base + "/demo/clock").json()
        target = feed.get("eta_ts", clk["now"] + 2.75 * 3600)
        await clock((target - clk["now"]) / 60)
        await call("end_sleep", {"caregiver": "Sam"})
        wake_label = httpx.get(base + "/demo/clock").json()["label"]
        await clock(3)
        await call("log_feed", {"method": "breast", "side": "right", "duration_minutes": 18, "caregiver": "Sam"})
        await clock(19)
        await call("log_diaper", {"type": "both", "caregiver": "Sam"})
        await clock(14)
        await call("start_sleep", {"caregiver": "Sam"})
        clk = httpx.get(base + "/demo/clock").json()
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(clk["tz"])
        nowd = datetime.fromtimestamp(clk["now"], tz)
        seven = nowd.replace(hour=7, minute=5, second=0)
        await clock((seven.timestamp() - clk["now"]) / 60)
        await page.evaluate("() => window.swaddle.setSpeaker('Priya')")
        await caption(f"{wake_label} · Mia wakes", ("narrator", f"Right on schedule, Mia wakes at {wake_label}. At seven, Priya takes over."))
        await caption("7:05 AM · Priya's turn", min_dur=1.4)

        await say("Priya", "Good morning. Catch me up on the night")
        await say("Priya", "Show me Mia's week for her checkup")
        segs[-1].lines.append(("narrator", "On a screen, the same tool renders an MCP App: the last 24 hours, a week of trends against pediatric guidance, and the fever, flagged with its source."))

        await slide(3, ("narrator", "Under the hood: thirteen typed tools with safety annotations, elicitation for missing details, and predictions learned from each baby's own history. "
                                     "It's standard MCP, so any host can use it, like the official MCP Inspector."))
        await slide(4, ("narrator", "3.6 million babies are born in the US every year. Swaddle: hands-free, when your hands are full."), min_dur=4)

        if errors:
            print("page errors:", errors, file=sys.stderr)
        await browser.close()
    return segs


def synth(segs: list[Seg], kokoro_dir: Path) -> None:
    import numpy as np
    import soundfile as sf
    from kokoro_onnx import Kokoro

    k = Kokoro(str(kokoro_dir / "kokoro-v1.0.onnx"), str(kokoro_dir / "voices-v1.0.bin"))
    sr = 24000
    for i, seg in enumerate(segs):
        chunks = []
        for who, text in seg.lines:
            voice, speed = VOICES.get(who, VOICES["narrator"])
            audio, sr = k.create(tts_normalize(text), voice=voice, speed=speed, lang="en-us")
            chunks.append(audio)
            chunks.append(np.zeros(int(sr * 0.25), dtype=audio.dtype))
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")
        dur = max(seg.min_dur, len(audio) / sr + seg.pad)
        total = int(dur * sr)
        audio = np.pad(audio, (0, max(0, total - len(audio))))[:total]
        path = BUILD / f"a{i:03d}.wav"
        sf.write(path, audio, sr)
        seg.audio = str(path)  # type: ignore[attr-defined]
        seg.dur = dur  # type: ignore[attr-defined]


def assemble(segs: list[Seg], out: Path) -> float:
    parts = []
    for i, seg in enumerate(segs):
        part = BUILD / f"p{i:03d}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", seg.image, "-i", seg.audio,  # type: ignore[attr-defined]
             "-t", f"{seg.dur:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-tune", "stillimage", "-pix_fmt", "yuv420p",  # type: ignore[attr-defined]
             "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-shortest", str(part)],
            check=True,
        )
        parts.append(part)
    lst = BUILD / "parts.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    raw = BUILD / "raw.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(raw)], check=True)
    # Normalise speech loudness to the YouTube-friendly -16 LUFS.
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-c:v", "copy", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(out)], check=True)
    return sum(s.dur for s in segs)  # type: ignore[attr-defined]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kokoro-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=BUILD / "swaddle_demo.mp4")
    args = ap.parse_args()
    BUILD.mkdir(parents=True, exist_ok=True)
    for f in BUILD.glob("*"):
        f.unlink()

    port = free_port()
    env = {**os.environ, "PORT": str(port), "SWADDLE_DB": str(BUILD / "demo.db"), "SWADDLE_DEMO_TIME": "2026-10-23T03:12",
           "SWADDLE_SEED_END": "2026-10-23T02:57", "SWADDLE_FROZEN": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "swaddle.server"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/healthz", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        segs = asyncio.run(capture(base))
    finally:
        proc.terminate()
    synth(segs, args.kokoro_dir)
    total = assemble(segs, args.out)
    script = [{"image": Path(s.image).name, "dur": round(s.dur, 2), "lines": s.lines} for s in segs]  # type: ignore[attr-defined]
    (BUILD / "script.json").write_text(json.dumps(script, indent=2))
    print(f"wrote {args.out} ({total:.1f}s, {len(segs)} segments)")


if __name__ == "__main__":
    main()
