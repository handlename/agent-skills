---
name: mermaid-to-issue-image
description: Turn a mermaid diagram into a hand-drawn free-layout SVG image, upload it as a GitHub issue attachment (user-attachments) via playwright-cli, and return the image URL. Designed to be called from other skills (e.g. plan-to-issue) through a subagent whose only return value is the URL on success or a failure reason on failure. Use when a mermaid diagram should be presented as a freely laid out image in an issue instead of a rendered mermaid block. Triggers include "mermaidをissue用の画像にして", "upload this diagram as an issue image", "generate an SVG for this mermaid and give me the attachment URL".
---

# Mermaid to Issue Image

## Overview

Take a **mermaid diagram** (the canonical record of a diagram's structure), hand-draw it as a **free-layout SVG**, upload the SVG as a **GitHub issue attachment** (`user-attachments`) by driving a real browser with **playwright-cli**, and return the attachment URL.

Motivation: mermaid describes structure well but gives little layout control. This skill keeps mermaid as the structural source of truth while producing a human-optimized image for display. The SVG layout is **throwaway** — every invocation redraws it from the mermaid source.

This skill does NOT decide what the diagram contains, does NOT embed the URL anywhere, and does NOT fall back when uploading is impossible. Those are the caller's responsibilities.

## When to Use

- Another skill (e.g. `plan-to-issue`) needs a big-picture diagram posted as an image in an issue.
- The user has a mermaid diagram and wants it as an issue-attachable image with a freely designed layout.

## Do Not Use When

- A rendered mermaid code block is acceptable — just paste the mermaid.
- The image is destined for something other than GitHub issues/PRs/comments (user-attachments URLs are a GitHub feature).
- The caller has no user approval for GitHub writes yet — see Authorization.

## Contract (input / output)

**Input** (provided by the caller, e.g. in the subagent prompt):

- `mermaid`: the mermaid source describing the diagram structure. Required.
- `repository`: `owner/repo` whose issue editor is used for the upload. Required.

**Output** (when run as a subagent, the final message MUST be exactly this — no prose around it):

- Success: the attachment URL only, e.g. `https://github.com/user-attachments/assets/<uuid>`
- Failure: `FAILED: <one-line reason and, if applicable, the remediation hint>`

Never fall back to returning the mermaid itself; the fallback decision belongs to the caller.

## Style Rules

All SVGs produced by this skill follow these rules so diagrams look consistent across invocations. Colors are GitHub Primer-ish so images feel native next to issue text.

- **Canvas**: explicit opaque background `#ffffff` (never transparent — dark-mode GitHub would make text unreadable); padding ≥ 16px on all sides; width ≤ 900px; height as needed. Set `viewBox` and matching `width`/`height`.
- **Font**: `-apple-system, "Segoe UI", Helvetica, Arial, sans-serif`; body text 14px, node titles 14–16px bold, edge labels 12px; text color `#1f2328`.
- **Nodes**: rounded rectangles (`rx="6"`), fill `#f6f8fa`, stroke `#d0d7de` 1.5px. Keep one shape language per diagram (e.g. cylinders only for data stores).
- **Edges**: 1.5px stroke `#57606a` with an arrowhead marker (`<defs><marker>`); edge labels get a white halo (`paint-order: stroke; stroke: #ffffff`) so they stay readable over lines.
- **Accents** (use sparingly, max 2 accents per diagram):
  - `#0969da` (blue): the primary flow / the component this issue is about
  - `#1a7f37` (green): added / new state
  - `#cf222e` (red): removed / problem being fixed
  - `#9a6700` (orange): changed / needs attention
- **Before vs after**: dashed stroke (`stroke-dasharray="6 4"`) for current/old, solid for new — plus the accent colors above.
- **Grouping**: related nodes sit inside a container rect (fill `#ffffff`, stroke `#d8dee4` 1px, dashed ok) with a small bold label at the top-left.
- **Layout**: pick the flow direction that reads best (usually left→right for pipelines, top→down for hierarchies); ≥ 24px gaps between nodes; avoid edge crossings before adding more accents or labels.
- **Fidelity**: every node, edge, and label in the mermaid source MUST appear in the SVG; do not invent structure that is not in the mermaid. Layout is free, content is not.

## Process

1. **Validate input** — both `mermaid` and `repository` present; the mermaid parses conceptually (nodes/edges/labels extractable). If not: `FAILED: invalid input <detail>`.
2. **Invocation convention** — run every playwright-cli command as `npx -y @playwright/cli <command>`. npx auto-installs the package on first use, so no preinstall check is needed (`-y` skips the interactive install prompt, which would hang an agent). Do NOT use `npx playwright-cli` — that resolves a different npm package. The steps below write `playwright-cli` for brevity. If npx itself cannot fetch the package (offline, registry error): `FAILED: could not run @playwright/cli via npx (<error>)`.
3. **Draw the SVG** — extract the structure (nodes, relationships, labels, groupings) from the mermaid source and hand-write an SVG following the Style Rules. Write it to a temp file (e.g. `<scratchpad>/big-picture.svg`). Sanity-check: well-formed XML (`python3 -c "import xml.dom.minidom,sys;xml.dom.minidom.parse(sys.argv[1])" <file>` or equivalent), every mermaid node/edge represented, no text outside the canvas.
4. **Upload via the issue editor** — the goal is to make GitHub's web editor perform the attachment upload, then leave WITHOUT creating anything:
   1. `playwright-cli open --persistent https://github.com/<owner>/<repo>/issues/new`
   2. Take a `snapshot`. If a login form appears instead of the issue editor: close and return `FAILED: GitHub session not logged in — run 'npx -y @playwright/cli open --persistent --headed https://github.com/login' once and sign in, then retry` (`--headed` is required: the default headless browser shows no window to sign in with).
   3. Focus the issue body textarea and `upload <path/to/svg>` against the editor's file input (the same mechanism as drag & drop).
   4. Wait until the editor finishes uploading: the body textarea content changes from an "Uploading..." placeholder to text containing `https://github.com/user-attachments/assets/<uuid>`. Read it from a fresh `snapshot`.
   5. Extract the URL. Then **abandon the draft** — do NOT submit the issue. The uploaded attachment remains valid even though no issue was created.
5. **Verify** — the success signal is the editor behavior in step 4.4: the upload is confirmed exactly when the "Uploading…" placeholder is replaced by the final `![...](https://github.com/user-attachments/assets/<uuid>)` markdown. If instead an error appears or the placeholder never resolves: `FAILED: upload did not complete`. Close the browser (`playwright-cli close`). Do NOT try to re-check the URL over the network — every path fails for the wrong reasons: anonymous `curl` returns 404 by design (issue pages serve attachments through signed `private-user-images` redirects), in-page `fetch` is blocked by CSP, and direct browser navigation turns into a download and times out. Rendering is ultimately confirmed when the caller posts the URL.
6. **Return** — output the URL as the entire final message.

## Authorization

Uploading an attachment writes data to GitHub even though no issue is created. Callers MUST invoke this skill only after the user has explicitly approved the GitHub write it is part of (e.g. `plan-to-issue` calls it after its authorization gate passes). This skill itself never creates issues or comments.

## Notes for Callers

- Prefer a mermaid source produced by the `draw-mermaid` skill: it returns a **syntax-validated** mermaid (`mermaid.parse()`-checked), so the diagram this skill hand-draws is based on a source that actually parses. This skill only validates the mermaid *conceptually* (extractable nodes/edges) — it does not run a parser.
- Invoke through a subagent and treat its final message as the return value: a URL, or `FAILED: ...`.
- Keep the mermaid source you passed in — you will want to preserve it next to the embedded image (e.g. in a collapsed `<details>` block; NOT an HTML comment — mermaid arrows `-->` contain `--`, which terminates comments and leaks the source) since it is the canonical record for future updates.
- To update a diagram: edit the mermaid, call this skill again, replace the old URL. Attachment URLs are immutable.
- Embed the result as `![<alt text>](<url>)`.

## Final Checklist

- [ ] Input had both mermaid source and target repository.
- [ ] SVG hand-drawn (no mermaid-cli or other renderers) and follows the Style Rules, including opaque background.
- [ ] Every node/edge/label from the mermaid appears in the SVG; nothing invented.
- [ ] Upload driven through playwright-cli with the persistent profile; no issue or comment was created.
- [ ] Returned exactly the attachment URL (success) or `FAILED: <reason>` (failure) — nothing else.
- [ ] Upload confirmed via the editor's placeholder → attachment-markdown replacement before returning.
