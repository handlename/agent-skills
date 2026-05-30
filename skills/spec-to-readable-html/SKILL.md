---
name: spec-to-readable-html
description: Convert specification documents or Markdown notes into highly structured, graphical HTML reports and open them in the default browser.
---

# Spec to Readable HTML

Convert specification documents, requirements, API specs, PRDs, technical designs, or Markdown notes into beautiful, highly structured, and interactive graphical HTML documents, and open them instantly in the browser.

## Overview

Markdown specifications are highly functional for AI development but can be dense and visually exhausting for human stakeholders (product managers, QA engineers, clients). This skill enables the agent to act as a document designer, translating plain Markdown or text files into visually premium HTML reports featuring responsive sidebars, Mermaid-based interactive diagrams (with pan/zoom support), priority badges, colored callouts, and clean data grids.

### When to Trigger This Skill

Trigger this skill whenever the user:
- Asks to "convert a spec to HTML", "make a spec readable", or "display a spec graphically".
- Mentions a Zenn article or refers to the "spec-to-readable-html" skill.
- Wants a visual way to review product specs, PRDs, API schemas, database ER designs, or system flows.
- Requests a "web-based spec sheet" or "interactive design document".

---

## Instructions

Follow these instructions systematically to process a target specification file.

### 1. Optimize Context Using Sub-agents (Highly Recommended)
Because the HTML template and rendering assets contain significant styling overhead (~50KB+), offload the rendering and HTTP server lifecycle tasks to a specialized sub-agent (e.g. `self` or a custom sub-agent) using `invoke_subagent`. This prevents the parent agent's context window from being cluttered:
- Launch a sub-agent with a clear prompt: *"Compile this Markdown specification file to HTML, launch the review server on a free port, and open it in the browser."*
- Wait for the sub-agent to finish and return the structured JSON feedback array (`{comments: [...]}`) back to your main session.

### 2. Compile Markdown to Interactive HTML
To compile any raw Markdown specification, execute the included high-performance, automated render utility. Do NOT attempt to manually rewrite the HTML body or load the template directly into your context:
```bash
python3 skills/spec-to-readable-html/scripts/render.py <path_to_markdown_spec>
```
This script automatically performs the following actions:
- Parses title and YAML frontmatter metadata dynamically.
- Resolves the output folder based on `.gitignore` dynamically (FR-010). It automatically searches for Git-ignored folders (e.g., `dist/`, `build/`, `tmp/`) to place the HTML. If none are found, it falls back to creating and using a `tmp/` folder.
- Injects CSS styles, responsive sidebars, interactive Mermaid diagram containers, styled callouts, data grid tables, and badging.
- Embeds the premium **Google Docs / Notion style Gutter review UI** featuring auto-resizing textareas (FR-012) and `Cmd + Enter` quick keyboard submission shortcuts (FR-013).

### 3. Launch Local Review Server
Once the HTML is successfully rendered, start the local feedback server in the background:
```bash
python3 skills/spec-to-readable-html/scripts/spec-server.py <path_to_resolved_html> <port_number>
```
- Ensure the server runs on a loopback address (`127.0.0.1`) and binds to a clean, unoccupied port (e.g., `5555`).
- Immediately launch the page in the browser (e.g., `open http://localhost:5555` on macOS).
- Transition to an idle state (stop calling tools) and wait for the user to review the document and submit feedback. The server will write `{filename}-feedback.json` and cleanly terminate.

### 4. Process Feedback & Auto-Modify Spec
- When the background task notifies you of a successful exit, read and parse the generated JSON feedback file.
- Automatically modify the source spec Markdown file to incorporate all received block-level and global feedback remarks.
- Re-compile the HTML using `render.py` and restart the server so the user can verify the resolved updates instantly.

---

## Verification

Before claiming completeness, verify these checkpoints:

1. **Output Location**:
   - [ ] Confirm the HTML file was written to a Git-ignored directory (e.g., `docs/superpowers/`, `tmp/`) and NOT the repository root.

2. **Interactive Gutter Review features**:
   - [ ] Confirm the sidebar includes the "✍️ 全体へのコメント / 提言" global feedback section.
   - [ ] Verify both inline and global textareas auto-resize in height dynamically.
   - [ ] Verify keyboard submission (`Cmd + Enter` / `Ctrl + Enter`) is fully operational.

3. **Sub-agent Execution**:
   - [ ] Verify that no huge HTML template content was loaded directly into your primary conversation history.
