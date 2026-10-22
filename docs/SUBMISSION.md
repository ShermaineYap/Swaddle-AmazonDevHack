# Devpost submission: copy and paste

Fields are in the order the Devpost form asks for them. Replace `<GITHUB URL>` and `<YOUTUBE URL>` once those exist.

---

## Project name
Swaddle

## Elevator pitch (tagline, 200 chars max)
Hands-free baby care for Alexa+: log feeds, diapers, sleep and fevers by voice, get predictions learned from your baby's own rhythm, and hand off the night with one question.

## Track
Alexa+ (self-hosted MCP server, Streamable HTTP, protocol 2025-11-25)

## Mini challenges
Open Source

---

## About the project (Markdown)

### Inspiration
At 3 AM a new parent has a baby in one arm and a bottle in the other. Pediatricians ask parents to track every feed, diaper and fever: newborns feed 8–12 times a day, parents watch for 6+ wet diapers a day, and a 100.4°F temperature under 3 months means calling the doctor right away (American Academy of Pediatrics). Today all of that goes into a phone, typed with one thumb in the dark. Of all the times voice is useful, this is the time it's the *only* thing that works. About 3.6 million babies are born in the US every year, and none of their parents have a free hand.

### What it does
Swaddle is a baby-care service for Alexa+, built as a self-hosted MCP server.

- **Log by talking:** "She finished on the left, about fifteen minutes." "I topped her up with a bottle." If a detail is missing, Swaddle asks for it mid-call: *"How much did Mia drink?"*
- **What's next:** next feed and nap predictions learned from **this baby's** last week, weighted toward the same time of day, with a "likely between" window and a confidence level.
- **Safety thresholds with sources:** a temperature is checked against AAP "when to call" guidance for the baby's age. "100.6 degrees is a fever for a baby under 3 months. The American Academy of Pediatrics says to call your pediatrician now, even at night."
- **Shift handoff:** "Catch me up on the night" leads with what needs action (the flagged fever, the note Sam left after calling the nurse line), then the totals, then what's next. Voice ID records who did what.
- **On a screen:** the same `care_report` tool renders an MCP App with a 24-hour clock of sleep, feeds and diapers, a week of trends against pediatric guidance, and flags with sources, ready for the checkup. Without a screen it speaks one line.

### How I built it
- **Python + the official MCP SDK (`mcp` 2.x `MCPServer`)**, served over **Streamable HTTP** at `/mcp`, protocol **2025-11-25**.
- **13 tools**, one intent each, with typed `outputSchema`s and `readOnly`/`destructive`/`idempotent` annotations. Every result carries a short `speech` line for voice plus structured content for screens and agents.
- **Elicitation** when a bottle amount is missing, with a graceful spoken follow-up plus a `set_bottle_amount` tool for hosts that don't support it.
- **MCP Apps** (`io.modelcontextprotocol/ui`): `ui://swaddle/report.html` bound to `care_report`, rendered on screens only when the client negotiates the extension.
- **Prediction engine:** weighted quantiles over the baby's own feed intervals and awake windows. Weights combine a circular time-of-day Gaussian kernel (σ = 2.5 h) with a 3-day recency half-life. Sleep predictions blend in an age-based prior that fades as the baby's own data accumulates. Confidence comes from the effective sample size and the spread.
- **Guidance module:** a small set of AAP thresholds, each with its source URL, so every safety statement can be cited. Count-based flags wait for a full day of logs to avoid false alarms.
- **Simulated Alexa+ client:** real Alexa+ access isn't available to entrants, so I wrote an SDK-free MCP client in one HTML file. It speaks 2025-11-25 over Streamable HTTP, parses SSE, answers server elicitation requests, hosts MCP Apps in a sandboxed iframe, and shows every JSON-RPC message in a live panel. A small rule-based router stands in for Alexa+'s LLM so it runs offline.
- **Tested end to end:** pytest launches the real server and drives it with the official MCP client over HTTP, including elicitation. It also works with the official MCP Inspector CLI.
- **Reproducible demo video:** a script drives the client in Chromium with a frozen demo clock, voices it with Kokoro TTS and assembles it with ffmpeg. Everything on screen is real server output.

### Challenges I ran into
- No access to the Alexa+ MCP Toolkit or simulator, so I built a protocol-faithful client to prove the server works the way a host would use it.
- The MCP Python SDK 2.x renamed FastMCP to `MCPServer`, so most published samples fail on a fresh `pip install mcp`.
- Designing for the ear: the first version's replies took 20+ seconds to speak. I rewrote every response to lead with the action, and moved detail to structured content and the screen.
- Predictions that don't lie: a 3 AM question needs 3 AM data, and a sparse log needs to admit low confidence instead of sounding sure.

### Accomplishments I'm proud of
- A complete night-to-morning flow (log, predict, flag, note, hand off, report) in one coherent product, not a list of features.
- Safety statements that are conservative, age-aware and cited every time.
- One server that works as voice only, voice plus screen (MCP App) and agent-to-agent (structured output), from the same tool calls.

### What I learned
Voice-first tools need two outputs: one sentence for the ear and a schema for everything else. MCP's structured content, elicitation and Apps map onto those needs well.

### What's next
OAuth 2.1 account linking so each household has its own log, Alexa+ routines ("remind me if she hasn't fed in 3.5 hours"), multiple babies (twins), medication timing, and exporting the report to the pediatrician's portal.

### Disclaimer
Swaddle is a logging assistant, not medical advice. The demo household and history are generated.

---

## Built with
python, mcp, model-context-protocol, streamable-http, mcp-apps, numpy, sqlite, starlette, uvicorn, javascript, html, css, playwright, kokoro-tts, ffmpeg

## Try it out links
- `<GITHUB URL>`
- (optional) live deployment URL if you deploy with `render.yaml`

## Video demo link
`<YOUTUBE URL>` (public, 2:51, English)

## Pre-existing project?
No. Swaddle is a new project; all code was written during the submission window.

---

## Open Source mini challenge fields
- **Contribution type:** New open-source project
- **Project repository URL:** `<GITHUB URL>`
- **Contribution URL:** `<GITHUB URL>`
- **GitHub username:** `<YOUR GITHUB USERNAME>`
- **What / how / why:** Swaddle is a new MIT-licensed MCP server and an SDK-free reference MCP client. The client shows exactly what an Alexa+-style host sends over Streamable HTTP (2025-11-25), including elicitation over SSE and hosting an MCP App. I open-sourced it so other Alexa+ builders without Toolkit access can test their servers the way a host would use them, and so parents and developers can build on the care engine.

---

## Product feedback (required, one block per tool)

### 1. Model Context Protocol Python SDK (`mcp` 2.3, `MCPServer`)
- **What I used it for:** The whole server: tool registration with typed output schemas and annotations, Streamable HTTP transport, elicitation, resources, and the MCP Apps extension.
- **What worked well:** Return-type-driven `outputSchema` generation; `Annotated[CallToolResult, Model]` let me control the spoken text and the structured content separately. `ctx.elicit()` with a Pydantic schema worked first time over Streamable HTTP. The in-core `Apps` extension and `client_supports_apps()` made graceful degradation easy.
- **What needs work:** The FastMCP → `MCPServer` rename means most tutorials (including AWS and hackathon samples) break on a fresh install. The error message is good, but the samples need updating. Extensions are fixed at construction, so registering `@apps.tool` handlers in a module that already created the server means calling a private `_apply_extension`. A public `add_extension()` would help.
- **Onboarding (zero to hello world):** About 20 minutes, mostly spent finding the 2.x names.
- **Would I build with it again?** Yes. It's the most direct way to ship a spec-current MCP server in Python.

### 2. MCP specification 2025-11-25 / Streamable HTTP
- **What I used it for:** The wire protocol between the simulated Alexa+ client and the server: initialize handshake, session header, SSE responses with interleaved server requests.
- **What worked well:** Writing a client with no SDK in about 150 lines of JavaScript was realistic. The spec is clear on headers and SSE framing.
- **What needs work:** Elicitation requires a stateful session, while the 2026-07-28 single-exchange mode moves to `input_required` results. It's unclear which model Alexa+ will support.
- **Onboarding:** About 1 hour to a working hand-written client.
- **Would I build with it again?** Yes.

### 3. MCP Apps extension (`io.modelcontextprotocol/ui`)
- **What I used it for:** The on-screen care report (`ui://swaddle/report.html`) bound to `care_report`.
- **What worked well:** A plain HTML resource with a small postMessage handshake (`ui/initialize` → `ui/notifications/tool-result`) is easy to build and test.
- **What needs work:** There are few minimal host examples. Most docs assume you're using a full host, so writing the host side meant reading the spec closely.
- **Onboarding:** About 45 minutes.
- **Would I build with it again?** Yes. One tool working as voice only or voice plus screen is exactly the Alexa+ multimodal story.

### 4. Alexa+ developer documentation (MCP / Agent Skills)
- **What I used it for:** Design guidance: one intent per tool, "always return something", short spoken responses, account linking expectations.
- **What worked well:** The design guidance on concise, voice-first results was directly useful and shaped every response.
- **What needs work:** The MCP Toolkit, CLI, Web Simulator and Local Inspector aren't available to hackathon participants, so there's no way to test against a real Alexa+ host. The docs also don't say whether Alexa+ supports elicitation or MCP Apps on Echo Show.
- **Onboarding:** Reading only; there's no hello-world path without Toolkit access.
- **Would I build with it again?** Yes, especially once a public sandbox exists.

### 5. MCP Inspector (CLI)
- **What I used it for:** Verifying the server against an independent, official client.
- **What worked well:** `--cli --transport http --method tools/call` gives one-line, scriptable checks.
- **What needs work:** Nothing major. An example in the README of passing tool arguments over HTTP would help.
- **Onboarding:** 5 minutes.
- **Would I build with it again?** Yes.

---

## Friction log (optional, up to +10%)

**1. Alexa+ testing tools are gated**
- **Task:** Test my MCP server inside Alexa+.
- **Steps:** Followed the Alexa+ MCP docs, then tried to install `@alexa-ai/cli` and use the Web Simulator.
- **Expected vs actual:** Expected a sandbox or simulator. Actually, the CLI isn't on public npm (404) and the Toolkit and simulator are partner-only; organizers confirmed they won't be granted to participants.
- **Severity:** High.
- **Workaround:** Wrote my own protocol-faithful simulated client.
- **Suggested fix:** A public, rate-limited Alexa+ MCP sandbox or a downloadable local host, or at least an official reference simulated client.

**2. Most MCP samples break on a fresh install**
- **Task:** Start from a Python MCP sample.
- **Steps:** `pip install mcp`, then `from mcp.server.fastmcp import FastMCP` as in most samples.
- **Expected vs actual:** Expected it to run. Actually got `ModuleNotFoundError` (mcp 2.x renamed it to `MCPServer`).
- **Severity:** Medium.
- **Workaround:** Read the SDK migration guide.
- **Suggested fix:** Update hackathon and AWS samples, or pin `mcp<2` in them.

**3. Unclear hosting requirement for judging**
- **Task:** Decide whether the server must be live during judging.
- **Steps:** Read the FAQ ("hosting not required") and a forum reply ("needs to be live and accessible till the end of the judging period").
- **Expected vs actual:** Expected one answer. Actually found two conflicting answers.
- **Severity:** Medium.
- **Workaround:** Made the repo runnable locally in one command and added Docker and Render configs.
- **Suggested fix:** Put one canonical statement in the Rules.

**4. Elicitation and MCP Apps support in Alexa+ is undocumented**
- **Task:** Decide whether to rely on elicitation and MCP Apps.
- **Steps:** Searched the Alexa+ docs for client capabilities.
- **Expected vs actual:** Expected a capability matrix. Actually found none.
- **Severity:** Medium.
- **Workaround:** Built graceful fallbacks: a spoken follow-up plus `set_bottle_amount` instead of elicitation, and one spoken line instead of the MCP App.
- **Suggested fix:** Publish which MCP client capabilities Alexa+ negotiates, per device type.

**5. MCP Apps extension must exist before tools are decorated (Python SDK)**
- **Task:** Bind a UI resource to a tool with `@apps.tool`.
- **Steps:** Created `MCPServer` first (needed for the other decorators), then decorated the Apps tools.
- **Expected vs actual:** Expected them to register. Actually, extensions are only applied at construction.
- **Severity:** Low.
- **Workaround:** Called `mcp_server._apply_extension(apps)` after the definitions.
- **Suggested fix:** A public `MCPServer.add_extension()`.

## Feature requests (optional)
- **Public Alexa+ MCP sandbox** (critical): lets every developer validate tool selection and speech end to end.
- **Alexa+ client capability matrix** (important): elicitation, MCP Apps and screen sizes per device.
- **Voice ID passed to MCP tools** (important): for example the speaker's display name as a tool argument or meta field, so household services can attribute actions.
- **Reference simulated Alexa+ web client from Amazon** (nice to have).
