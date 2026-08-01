---
name: draw-mermaid
description: Draw a mermaid diagram from a natural-language intent and return a syntax-validated mermaid source, verifying it with mermaid.parse() against a vendored, version-pinned mermaid.js driven by playwright-cli. Designed to be called from other skills (e.g. plan-to-issue, create-pr) through a subagent whose only return value is the validated mermaid on success or a failure reason on failure. Use whenever a mermaid diagram must be produced with a guarantee that it parses without syntax errors. Triggers include "mermaidで図を描いて構文チェックまでして", "draw a mermaid diagram and make sure it has no syntax errors", "syntax-checked mermaid".
---

# Draw Mermaid

## Overview

Take a **natural-language intent** for a diagram, author it as **mermaid**, and return a mermaid source that is **guaranteed to pass `mermaid.parse()`** with no syntax errors. Authoring and syntax repair both happen inside this skill; the caller only supplies the intent and receives a validated diagram.

The syntax check is not a heuristic: the mermaid is parsed by a real, **version-pinned mermaid.js (11.16.0)** vendored into this skill (`vendor/mermaid.min.js`), loaded in a real browser via **playwright-cli**, and run through `mermaid.parse()`. Validation is fully **offline and deterministic** — no CDN, no external service, no `mmdc`, no `package.json`.

This skill does NOT decide *what* the diagram means (the caller passes the intent), does NOT render an image (that is `mermaid-to-svg`) or upload anything (that is `github-attachment-upload`), and does NOT embed the result anywhere.

## When to Use

- Another skill (e.g. `plan-to-issue`, `create-pr`) needs to author a mermaid diagram and wants a guarantee that the mermaid parses before it is shown, embedded, or handed to `mermaid-to-svg`.
- An agent is about to write mermaid and wants it verified against a real parser rather than trusting the model's output.

## Do Not Use When

- The caller already has a mermaid source they only want *imaged* — that is `mermaid-to-svg` (then `github-attachment-upload` to publish it).
- No mermaid is needed at all (plain prose is enough).
- The environment has neither `npx`/`node` (for playwright-cli) nor `python3`/`npx` (for the local static server) — see the failure contract below.

## Contract (input / output)

**Input** (provided by the caller, e.g. in the subagent prompt):

- `intent`: a natural-language description of what the diagram should convey (the components/relationships/flow/states to show). Required.
- `diagram_type` *(optional)*: a hint such as `flowchart`, `sequenceDiagram`, `stateDiagram-v2`, `erDiagram`. If omitted, pick the type that best conveys the intent.

**Output** (when run as a subagent, the final message MUST be exactly this — no prose around it):

- Success: the **validated mermaid source only** — the raw mermaid text, with no ` ```mermaid ` fence and no surrounding commentary.
- Failure: `FAILED: <one-line reason and, if applicable, a remediation hint>`.

Never return an unvalidated diagram as if it were validated; if it does not parse after the repair budget is exhausted, return `FAILED:` with the last parse error.

## Process

1. **Author the mermaid** — from `intent`, write a high-level mermaid diagram. Pick the diagram type (`flowchart`, `sequenceDiagram`, `stateDiagram-v2`, `erDiagram`, …) that best conveys the structure; honor `diagram_type` if given. Keep it for human comprehension, not implementation detail.

2. **Resolve the validator directory** — this skill ships a validator at `vendor/validate.html` + `vendor/mermaid.min.js`. Let `<vendor-dir>` be the absolute path to this skill's `vendor/` directory (the `vendor/` folder sitting next to this `SKILL.md`).

3. **Start a local static server for the validator** — playwright-cli blocks the `file:` protocol, so the validator is served over `127.0.0.1` (local only, no external network). Prefer `python3`; fall back to `npx http-server` if `python3` is absent.

   ```bash
   cd "<vendor-dir>"
   # -u is required: without unbuffered output the "Serving HTTP on ... port N" banner
   # is not flushed to the log file, so the ephemeral port cannot be read back.
   python3 -u -m http.server 0 --bind 127.0.0.1 > /tmp/draw-mermaid-server.log 2>&1 &
   SRV_PID=$!
   sleep 1
   PORT=$(grep -oE 'port [0-9]+' /tmp/draw-mermaid-server.log | head -1 | grep -oE '[0-9]+')
   # Fallback if python3 is unavailable:
   #   npx -y http-server "<vendor-dir>" -a 127.0.0.1 -p 8791 > /tmp/draw-mermaid-server.log 2>&1 &
   #   PORT=8791
   ```

   If no server can be started (no `python3` and `npx` cannot fetch `http-server`): `FAILED: could not start a local static server for the mermaid validator (<error>)`.

4. **Open the validator in playwright-cli** — run every playwright-cli command as `npx -y @playwright/cli <command>` (the same convention as `github-attachment-upload`; `-y` skips the install prompt that would otherwise hang an agent; do NOT use `npx playwright-cli`, which is a different package). Use a dedicated session so it does not collide with other browsers.

   ```bash
   npx -y @playwright/cli -s=draw-mermaid open "http://127.0.0.1:$PORT/validate.html"
   ```

   If npx cannot fetch `@playwright/cli` (offline, registry error): stop the server and return `FAILED: could not run @playwright/cli via npx (<error>)`.

5. **Validate `mermaid.parse()`** — pass the mermaid to the page as **base64** (this avoids every CLI quoting/newline problem and preserves UTF-8 labels). The page's `window.__validateMermaid(b64)` returns `{ok: true}` or `{ok: false, error: "<parse error with line and position>"}`.

   ```bash
   B64=$(printf '%s' "$MERMAID_SOURCE" | base64)
   npx -y @playwright/cli -s=draw-mermaid eval "async () => await window.__validateMermaid('$B64')"
   ```

6. **Repair loop (budget: 3 revisions)** — if the result is `ok: false`, read the `error` (it names the line and what the parser expected), fix the mermaid, and re-run step 5. Repeat until `ok: true` or **3 revisions** have failed. On exhaustion, return `FAILED: mermaid did not parse after 3 revisions — <last parse error>`.

7. **Tear down** — always close the browser and stop the server, even on failure:

   ```bash
   npx -y @playwright/cli -s=draw-mermaid close
   kill "$SRV_PID" 2>/dev/null
   ```

8. **Return** — output the validated mermaid source as the entire final message (raw mermaid, no fence), or `FAILED: <reason>`.

## Notes for Callers

- Invoke through a subagent and treat its final message as the return value: a validated mermaid source, or `FAILED: ...`.
- The returned mermaid is the **canonical structure record** — preserve it (e.g. in a collapsed `<details>` block) and pass it to `mermaid-to-svg` when an image is wanted. `draw-mermaid` produces the diagram; `mermaid-to-svg` hand-draws it as an SVG file; `github-attachment-upload` publishes that file. They compose: `draw-mermaid` → validated mermaid → `mermaid-to-svg` → SVG path → `github-attachment-upload` → attachment URL.
- Validation is pinned to mermaid **11.16.0**. A diagram that parses here is guaranteed against that grammar; features newer than 11.16.0 are not recognized.
- To bump the vendored mermaid version, replace `vendor/mermaid.min.js` with a newer pinned build and update the version noted here and in the frontmatter.

## Final Checklist

- [ ] Input `intent` was present; a diagram type was chosen (or `diagram_type` honored).
- [ ] The mermaid was validated by `mermaid.parse()` on the vendored, version-pinned mermaid.js served over `127.0.0.1` (never `file:`; never a CDN).
- [ ] On a parse error, the mermaid was revised and re-validated, within the 3-revision budget.
- [ ] The local server was stopped and the browser closed, on both success and failure.
- [ ] Returned exactly the validated mermaid source (success) or `FAILED: <reason>` (failure) — nothing else, no ` ```mermaid ` fence.
