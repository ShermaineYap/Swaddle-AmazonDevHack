"""End-to-end tests: real server process, real MCP client, real Streamable HTTP."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server_url():
    port = _free_port()
    db = Path(tempfile.mkdtemp()) / "swaddle.db"
    env = {
        **os.environ,
        "PORT": str(port),
        "SWADDLE_DB": str(db),
        "SWADDLE_DEMO_TIME": "2026-10-23T03:12",
        "SWADDLE_SEED_END": "2026-10-23T02:57",
    }
    proc = subprocess.Popen([sys.executable, "-m", "swaddle.server"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/healthz", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError(proc.stdout.read().decode())
    yield base + "/mcp"
    proc.terminate()
    proc.wait(5)


async def _elicit(ctx, params: types.ElicitRequestParams):
    assert "How much" in params.message
    return types.ElicitResult(action="accept", content={"amount": 2.5, "unit": "oz"})


async def _session(url, **kw):
    return streamable_http_client(url), kw


@pytest.mark.anyio
async def test_full_night(server_url):
    async with streamable_http_client(server_url) as (r, w, *_):
        async with ClientSession(r, w, elicitation_callback=_elicit, client_info=types.Implementation(name="test", version="1")) as s:
            init = await s.initialize()
            assert init.protocol_version >= "2025-11-25"
            assert init.server_info.name == "swaddle"

            tools = {t.name: t for t in (await s.list_tools()).tools}
            assert {"log_feed", "log_diaper", "start_sleep", "end_sleep", "log_temperature", "get_last", "predict_next", "shift_handoff", "care_report", "undo_last", "setup_baby", "set_bottle_amount", "add_note"} <= set(tools)
            assert tools["get_last"].annotations.read_only_hint is True
            assert tools["undo_last"].annotations.destructive_hint is True
            assert tools["care_report"].meta["ui"]["resourceUri"] == "ui://swaddle/report.html"
            assert tools["log_feed"].output_schema is not None

            res = await s.call_tool("log_feed", {"method": "breast", "side": "left", "duration_minutes": 15, "caregiver": "Sam"})
            speech = res.content[0].text
            assert "left side, 15 minutes" in speech and "start on the right" in speech
            assert res.structured_content["next_feed"]["observations"] > 10

            res = await s.call_tool("log_feed", {"method": "bottle", "caregiver": "Sam"})  # amount missing -> elicitation
            assert "2.5 ounce" in res.content[0].text

            res = await s.call_tool("get_last", {"kind": "feed"})
            assert "2.5 ounce" in res.content[0].text

            res = await s.call_tool("undo_last", {})
            assert res.structured_content["removed"] is True

            res = await s.call_tool("log_diaper", {"type": "wet", "caregiver": "Sam"})
            assert "Wet diaper logged" in res.content[0].text

            res = await s.call_tool("predict_next", {})
            assert res.structured_content["next_feed"]["confidence"] in ("high", "medium")

            res = await s.call_tool("log_temperature", {"value": 100.6, "unit": "F", "method": "rectal", "caregiver": "Sam"})
            assert res.structured_content["level"] == "call_now"
            assert "healthychildren.org" in res.structured_content["reference"]["url"]

            res = await s.call_tool("log_temperature", {"value": 37.2, "unit": "C"})
            assert res.structured_content["level"] == "ok"

            res = await s.call_tool("start_sleep", {"caregiver": "Sam"})
            assert "asleep at" in res.content[0].text

            res = await s.call_tool("add_note", {"text": "Called the nurse line, bring her in at 9", "caregiver": "Sam"})
            res = await s.call_tool("shift_handoff", {"since_hours": 8})
            assert "Important" in res.content[0].text
            assert "nurse line" in res.content[0].text  # the 100.6 reading is surfaced first-class

            res = await s.call_tool("care_report", {"days": 7})
            d = res.structured_content
            assert len(d["days"]) == 7 and d["flags"] and d["timeline"]

            ui = await s.read_resource("ui://swaddle/report.html")
            assert ui.contents[0].mime_type == "text/html;profile=mcp-app"

            g = await s.read_resource("swaddle://guidance")
            assert "American Academy of Pediatrics" in g.contents[0].text


@pytest.mark.anyio
async def test_no_elicitation_client_gets_spoken_followup(server_url):
    async with streamable_http_client(server_url) as (r, w, *_):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("log_feed", {"method": "bottle"})
            assert "How much did she drink" in res.content[0].text
            res = await s.call_tool("set_bottle_amount", {"amount": 90, "unit": "ml"})
            assert "90 milliliters" in res.content[0].text


@pytest.fixture
def anyio_backend():
    return "asyncio"
