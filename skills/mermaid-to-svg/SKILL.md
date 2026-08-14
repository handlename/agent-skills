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
- `focus` *(optional)*: which node(s) the diagram is really about, so the accent lands there (see Design Principles → *Focal accent*). If omitted, infer the single most important node (entry point or subject) or use none.
- `output_path` *(optional)*: where to write the SVG. If omitted, write to a temp file (e.g. `<scratchpad>/big-picture.svg`).

**Output** (when run as a subagent, the final message MUST be exactly this — no prose around it):

- Success: the absolute path to the written SVG file only, e.g. `/tmp/.../big-picture.svg`
- Failure: `FAILED: <one-line reason and, if applicable, the remediation hint>`

Never return the mermaid itself in place of a path; the fallback decision belongs to the caller.

## Design Principles

These principles are adapted from the **diagram-design** editorial system and re-expressed here in a GitHub-native palette. They are the *discipline* behind the Style Rules below — read them as the "why", the Style Rules as the "how". This skill stays pure/offline and GitHub-native; it does not depend on the diagram-design plugin.

- **Restraint is the highest-quality move.** Earn every drawn element. No shadows, no gradients, no decorative chrome — borders and whitespace carry the design. If removing a *visual* element (a container, a label, a color) wouldn't hurt comprehension, remove it.
- **Fidelity is non-negotiable and outranks restraint.** Restraint applies to *decoration you add*, never to the *content you were given*. Every node, edge, and label in the mermaid source MUST appear in the SVG. You may not merge or drop nodes to hit a budget — the mermaid is the authored content; you only lay it out. (Deletion happens at *authoring* time, in `draw-mermaid`, not here.)
- **Focal accent, not a flag.** Accent color marks 1–2 focal nodes at most — the `focus` node(s), or the subject/entry point. Accenting five nodes erases the signal; everything non-focal is neutral ink/gray.
- **Hierarchy through treatment.** Do not draw identical boxes for every node. Vary fill/stroke by the node's *role* (see the role→treatment table) so the reader sees structure at a glance.
- **Complexity budget is a layout signal, not a content cap.** Comfortable density is ≤ ~9 nodes and ≤ ~12 edges. When the incoming mermaid exceeds that, do NOT drop content — instead group related nodes into labelled zones, widen spacing, and pick the flow direction that minimizes crossings. You MAY note an over-budget source to the caller (e.g. "source has 14 nodes; consider splitting"), but you still render everything.

## Style Rules

All SVGs produced by this skill follow these rules so diagrams look consistent across invocations. Colors are GitHub Primer-ish so images feel native next to issue text.

- **Canvas**: explicit opaque background `#ffffff` (never transparent — dark-mode GitHub would make text unreadable); padding ≥ 16px on all sides; width ≤ 900px; height as needed. Set `viewBox` and matching `width`/`height`.
- **4px grid**: keep coordinates, node widths/heights, and gaps on a 4px grid (multiples of 4). It makes alignment read as intentional. Exempt: stroke widths (0.8, 1, 1.5) and opacity.
- **Font**: `-apple-system, "Segoe UI", Helvetica, Arial, sans-serif`; body text 14px, node titles 14–16px bold, edge labels 12px; text color `#1f2328`. Use a monospace stack (`ui-monospace, "SFMono-Regular", Menlo, monospace`) only for technical sublabels (ports, commands, paths).
- **Nodes**: rounded rectangles (`rx="6"`), fill `#f6f8fa`, stroke `#d0d7de` 1.5px by default. Keep one shape language per diagram (e.g. cylinders only for data stores, diamonds only for decisions). Treat node fill/stroke by **role**:

  | Role | Fill | Stroke | Notes |
  |---|---|---|---|
  | **Focal** (1–2 max) | `#ddf4ff` | `#0969da` | The `focus`/subject node |
  | **Default step / component** | `#f6f8fa` | `#d0d7de` | The neutral majority |
  | **Data store / state** | `#f6f8fa` | `#57606a` | Cylinder shape |
  | **External / third-party** | `#ffffff` | `#d0d7de` | Lighter, sits back |
  | **Decision** | `#f6f8fa` | `#d0d7de` | Diamond shape |
  | **Added / new** | `#dafbe1` | `#1a7f37` | Green — new state |
  | **Removed / old** | `#ffffff` | `#cf222e` | Red + dashed `6 4` |
  | **Optional / async** | `#ffffff` | `#8c959f` | Dashed `4 3` |

- **Accents** (use sparingly, max 2 accent colors per diagram):
  - `#0969da` (blue): the focal node / the component this issue is about
  - `#1a7f37` (green): added / new state
  - `#cf222e` (red): removed / problem being fixed
  - `#9a6700` (orange): changed / needs attention
- **Before vs after**: dashed stroke (`stroke-dasharray="6 4"`) for current/old, solid for new — plus the accent colors above.
- **Edges**: 1.5px stroke `#57606a` with an arrowhead marker (`<defs><marker>`); draw edges *before* nodes so lines sit behind the boxes. Connector rules:
  - **Orthogonal, not diagonal.** Between nodes that don't share an x or y axis, route with right-angle elbows (rounded, `r≈6–8`), never a slanted straight line. Plain straight lines are only for endpoints that share an x or y coordinate.
  - **Label with a gap and a halo.** Edge labels get a white halo (`paint-order: stroke; stroke: #ffffff; stroke-width: 3`) AND sit with a visible 6–10px gap above/beside the line — never on top of it. The line must stay traceable.
  - **No overlaps.** Two edges must not share a stroke path or stack on top of each other. Where edges cross, offset routing so each stays independently traceable; keep parallel edges ≥ 8px apart.
  - **Fan shared attach points.** When several edges enter/leave the same side of a box, give each its own attach point spread along that edge (≥ 8px apart) — no two edges touching a box at the same point.
  - **Don't route behind non-endpoint boxes.** Reroute around intervening boxes; a connector passing behind a box that is neither its source nor destination reads as touching it.
- **Grouping / zones**: related nodes sit inside a container rect (fill `#ffffff`, stroke `#d8dee4` 1px, dashed ok) with a small bold label at the top-left. Reach for zones especially when the source is over the complexity budget.
- **Legend**: if the diagram uses roles/colors that aren't self-evident, add a legend as a **horizontal strip along the bottom** (hairline `#d8dee4` separator above it), never floating inside the diagram area. Cover every treatment used and nothing extra.
- **Layout**: pick the flow direction that reads best (usually left→right for pipelines, top→down for hierarchies); ≥ 24px gaps between nodes; minimize edge crossings before adding more accents or labels.
- **Fidelity**: every node, edge, and label in the mermaid source MUST appear in the SVG; do not invent structure that is not in the mermaid. Layout is free, content is not.

## Process

1. **Validate input** — `mermaid` present and the mermaid parses conceptually (nodes/edges/labels extractable). If not: `FAILED: invalid input <detail>`.
2. **Plan the layout** — extract the structure (nodes, relationships, labels, groupings) from the mermaid source. Decide the flow direction, which node(s) are focal (from `focus` or inferred), each node's role, and whether the source is over the complexity budget (if so, plan zones and wider spacing — but keep every node).
3. **Draw the SVG** — hand-write an SVG following the Style Rules: edges first, then role-treated nodes, then labels, then (if needed) a bottom legend. Write it to `output_path` if given, else to a temp file (e.g. `<scratchpad>/big-picture.svg`).
4. **Run the taste gate** — walk the checklist below; fix anything that fails before returning.
5. **Sanity-check** — the SVG is well-formed XML (`python3 -c "import xml.dom.minidom,sys;xml.dom.minidom.parse(sys.argv[1])" <file>` or equivalent), every mermaid node/edge is represented, and no text falls outside the canvas. If the file cannot be written or is not well-formed: `FAILED: <detail>`.
6. **Return** — output the absolute path to the SVG file as the entire final message.

## Taste Gate (pre-return checklist)

Run this before returning the path — it is the discipline that keeps output from looking like generic auto-layout.

**Fidelity**

- [ ] Every mermaid node, edge, and label appears in the SVG; nothing invented, nothing dropped.

**Signal**

- [ ] Accent color on ≤ 2 focal nodes; everything else neutral.
- [ ] Node fill/stroke varies by role — no wall of identical boxes.
- [ ] Legend (if present) covers every treatment used and nothing extra, as a bottom strip.

**Restraint**

- [ ] No shadows, gradients, or decorative chrome — borders + whitespace only.
- [ ] Could any *visual* element (container, label, color) be removed without hurting comprehension? If yes, remove it.
- [ ] If the source was over budget, related nodes are grouped into zones rather than crammed.

**Connectors**

- [ ] Edges drawn before boxes (lines behind nodes).
- [ ] Off-axis edges use rounded right-angle elbows — no diagonal slants.
- [ ] Every edge label has a white halo AND a 6–10px gap above its line.
- [ ] No two edges overlap or share an attach point; parallel edges ≥ 8px apart.
- [ ] No edge routes behind a box that isn't its endpoint.

**Canvas & type**

- [ ] Opaque `#ffffff` background; nothing clipped outside the canvas; width ≤ 900px.
- [ ] Coordinates/sizes/gaps on the 4px grid.
- [ ] Monospace only on technical sublabels; human names in the sans stack.

## Notes for Callers

- Prefer a mermaid source produced by the `draw-mermaid` skill: it returns a **syntax-validated** mermaid (`mermaid.parse()`-checked), so the diagram this skill hand-draws is based on a source that actually parses. This skill only validates the mermaid *conceptually* (extractable nodes/edges) — it does not run a parser.
- Pass a `focus` node when you know what the diagram is about (e.g. the component a PR changes) — it makes the accent land where it matters instead of being guessed.
- Invoke through a subagent and treat its final message as the return value: an SVG file path, or `FAILED: ...`.
- To publish the SVG as a GitHub issue attachment, pass the returned path to `github-attachment-upload`. The composition is: `draw-mermaid` → validated mermaid → `mermaid-to-svg` → SVG path → `github-attachment-upload` → attachment URL.
- Keep the mermaid source you passed in — it is the canonical record for future updates. To update a diagram: edit the mermaid, re-run this skill to redraw the SVG, and re-upload. The SVG layout is throwaway; only the mermaid persists.
- If the source is large, this skill renders all of it but may note it is over the comfortable budget — that is a hint to consider splitting the mermaid into overview + detail at authoring time.

## Final Checklist

- [ ] Input had a mermaid source.
- [ ] SVG hand-drawn (no mermaid-cli or other renderers) following the Style Rules, including opaque background.
- [ ] Design Principles applied: focal accent ≤2, role-based node treatment, restraint, fidelity preserved.
- [ ] Taste gate walked; connector/signal/canvas checks pass.
- [ ] Every node/edge/label from the mermaid appears in the SVG; nothing invented.
- [ ] SVG written to a file and confirmed to be well-formed XML.
- [ ] Returned exactly the SVG file path (success) or `FAILED: <reason>` (failure) — nothing else.
