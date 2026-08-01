---
name: mermaid-to-svg
description: Turn a mermaid diagram into a hand-drawn free-layout SVG image file and return its path. Pure and offline — it draws an SVG following GitHub Primer-ish style rules but does NOT render via mermaid-cli, upload anything, or embed the result. Designed to be called from other skills (e.g. plan-to-issue, create-pr) through a subagent whose only return value is the SVG file path on success or a failure reason on failure, then paired with github-attachment-upload to publish it. Use when a mermaid diagram should be presented as a freely laid out image. Triggers include "mermaidを自由レイアウトのSVGにして", "hand-draw this mermaid as an SVG", "generate a free-layout SVG for this mermaid".
---

# Mermaid to SVG

## Overview

Take a **mermaid diagram** (the canonical record of a diagram's structure) and hand-draw it as a **free-layout SVG image file**, returning the path to that file.

Motivation: mermaid describes structure well but gives little layout control. This skill keeps mermaid as the structural source of truth while producing a human-optimized image. The SVG layout is **throwaway** — every invocation redraws it from the mermaid source.

This skill is **pure**: it has no external dependencies (no browser, no network, no mermaid-cli). It does NOT decide what the diagram contains, does NOT upload the file anywhere, and does NOT embed the path/URL anywhere. Uploading is `github-attachment-upload`; those other decisions are the caller's responsibility.

## When to Use

- Another skill (e.g. `plan-to-issue`, `create-pr`) needs a big-picture diagram as a free-layout SVG image before uploading it via `github-attachment-upload`.
- The user has a mermaid diagram and wants it rendered as a freely designed SVG image.

## Do Not Use When

- A rendered mermaid code block is acceptable — just paste the mermaid.
- You only need to publish an existing image file to GitHub — that is `github-attachment-upload`.
- The mermaid still needs to be authored or syntax-checked — that is `draw-mermaid`.

## Contract (input / output)

**Input** (provided by the caller, e.g. in the subagent prompt):

- `mermaid`: the mermaid source describing the diagram structure. Required.
- `output_path` *(optional)*: where to write the SVG. If omitted, write to a temp file (e.g. `<scratchpad>/big-picture.svg`).

**Output** (when run as a subagent, the final message MUST be exactly this — no prose around it):

- Success: the absolute path to the written SVG file only, e.g. `/tmp/.../big-picture.svg`
- Failure: `FAILED: <one-line reason and, if applicable, the remediation hint>`

Never return the mermaid itself in place of a path; the fallback decision belongs to the caller.

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

1. **Validate input** — `mermaid` present and the mermaid parses conceptually (nodes/edges/labels extractable). If not: `FAILED: invalid input <detail>`.
2. **Draw the SVG** — extract the structure (nodes, relationships, labels, groupings) from the mermaid source and hand-write an SVG following the Style Rules. Write it to `output_path` if given, else to a temp file (e.g. `<scratchpad>/big-picture.svg`).
3. **Sanity-check** — the SVG is well-formed XML (`python3 -c "import xml.dom.minidom,sys;xml.dom.minidom.parse(sys.argv[1])" <file>` or equivalent), every mermaid node/edge is represented, and no text falls outside the canvas. If the file cannot be written or is not well-formed: `FAILED: <detail>`.
4. **Return** — output the absolute path to the SVG file as the entire final message.

## Notes for Callers

- Prefer a mermaid source produced by the `draw-mermaid` skill: it returns a **syntax-validated** mermaid (`mermaid.parse()`-checked), so the diagram this skill hand-draws is based on a source that actually parses. This skill only validates the mermaid *conceptually* (extractable nodes/edges) — it does not run a parser.
- Invoke through a subagent and treat its final message as the return value: an SVG file path, or `FAILED: ...`.
- To publish the SVG as a GitHub issue attachment, pass the returned path to `github-attachment-upload`. The composition is: `draw-mermaid` → validated mermaid → `mermaid-to-svg` → SVG path → `github-attachment-upload` → attachment URL.
- Keep the mermaid source you passed in — it is the canonical record for future updates. To update a diagram: edit the mermaid, re-run this skill to redraw the SVG, and re-upload. The SVG layout is throwaway; only the mermaid persists.

## Final Checklist

- [ ] Input had a mermaid source.
- [ ] SVG hand-drawn (no mermaid-cli or other renderers) following the Style Rules, including opaque background.
- [ ] Every node/edge/label from the mermaid appears in the SVG; nothing invented.
- [ ] SVG written to a file and confirmed to be well-formed XML.
- [ ] Returned exactly the SVG file path (success) or `FAILED: <reason>` (failure) — nothing else.
