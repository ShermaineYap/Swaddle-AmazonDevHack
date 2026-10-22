# Swaddle: friction log

Build, Ship, Shape: Amazon Developer Hackathon, Alexa+ track. Each entry lists the task, steps taken, expected vs actual result, severity, workaround and suggested fix.

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
