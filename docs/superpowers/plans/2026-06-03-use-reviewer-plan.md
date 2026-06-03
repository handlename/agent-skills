# Rewrite Spec-to-Readable-HTML to use `reviewer` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the `spec-to-readable-html` skill inside `agent-skills` to use the Go-based external `reviewer` tool and clean up the legacy Python files.

**Architecture:** Update `SKILL.md` to instruct the agent to run the `reviewer` Go CLI instead of python scripts, prompt the user for permission to install it if not found, and delete the legacy files.

**Tech Stack:** Go CLI (reviewer), Shell commands.

---

### Task 1: Rewrite `skills/spec-to-readable-html/SKILL.md`

**Files:**
- Modify: `skills/spec-to-readable-html/SKILL.md`

- [ ] **Step 1: Replace `skills/spec-to-readable-html/SKILL.md` content**

Overwrite `skills/spec-to-readable-html/SKILL.md` with the following content:

```markdown
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
Because running a review server and capturing feedback takes multiple steps and background logs, offload these tasks to a specialized sub-agent (e.g. `self` or a custom sub-agent) using `invoke_subagent`. This prevents the parent agent's context window from being cluttered:
- Launch a sub-agent with a clear prompt: *"Launch the reviewer serve tool on this Markdown specification file to open it in the default browser, collect feedback, and return the structured JSON feedback array."*
- Wait for the sub-agent to finish and return the structured JSON feedback array (`{comments: [...]}`) back to your main session.

### 2. Verify `reviewer` CLI Availability
Before running the server, check if the `reviewer` command is available on your machine:
```bash
which reviewer
```
If the command is not found, ask the user for confirmation:
> "The `reviewer` command is required to compile this specification. May I install it using `brew install handlename/tap/reviewer` (on macOS) or `go install github.com/handlename/reviewer/cmd/reviewer@latest`?"

Upon approval, run the appropriate installation command:
- **macOS with Homebrew**:
  ```bash
  brew install handlename/tap/reviewer
  ```
- **Fallback / Go Install**:
  ```bash
  go install github.com/handlename/reviewer/cmd/reviewer@latest
  ```
  Ensure `$GOPATH/bin` (default `~/go/bin`) is configured in the environment `$PATH`.

### 3. Launch Local Review Server
Once the `reviewer` command is available, resolve a target path for the compiled HTML file within a Git-ignored directory (e.g. check for `dist/`, `build/`, or `tmp/` under workspace root). If none exist, create and use a `tmp/` folder.

Start the local feedback server in the background:
```bash
reviewer serve <path_to_markdown_spec> -o <path_to_resolved_html> -p <port_number>
```
- Select a clean, unoccupied port (e.g., `5500` or `5555`).
- The command automatically compiles the spec, saves it to the output path, opens the default web browser, and starts the server.
- Transition to an idle state (stop calling tools) and wait for the user to review the document and submit feedback. The server will print `FEEDBACK_RECEIVED` to stdout, write comments to `<input-filename_without_ext>-feedback.json` (as a sibling of the spec file), and cleanly terminate.

### 4. Process Feedback & Auto-Modify Spec
- When the background task notifies you of a successful exit, read and parse the generated JSON feedback file.
- Automatically modify the source spec Markdown file to incorporate all received block-level and global feedback remarks.
- Restart the server using `reviewer serve` so the user can verify the resolved updates instantly.

---

## Verification

Before claiming completeness, verify these checkpoints:

1. **Output Location**:
   - [ ] Confirm the HTML file was written to a Git-ignored directory (e.g., `docs/superpowers/`, `tmp/`) and NOT the repository root.

2. **Interactive Gutter Review features**:
   - [ ] Confirm the sidebar includes the global feedback section.
   - [ ] Verify both inline and global textareas auto-resize in height dynamically.

3. **Sub-agent Execution**:
   - [ ] Verify that no huge HTML template content was loaded directly into your primary conversation history.
```

- [ ] **Step 2: Commit changes**

Run:
```bash
git commit --no-gpg-sign -am "feat: rewrite spec-to-readable-html skill to use reviewer CLI"
```
Expected output: Commit succeeds.

---

### Task 2: Delete Obsolete Files

**Files:**
- Delete: `skills/spec-to-readable-html/scripts/render.py`
- Delete: `skills/spec-to-readable-html/scripts/spec-server.py`
- Delete: `skills/spec-to-readable-html/references/template.html`
- Delete: `skills/spec-to-readable-html/references/html-output-template.md`

- [ ] **Step 1: Delete python scripts and template references**

Run:
```bash
rm -rf skills/spec-to-readable-html/scripts skills/spec-to-readable-html/references
```
Expected: Files are deleted.

- [ ] **Step 2: Commit deletions**

Run:
```bash
git add -A skills/spec-to-readable-html/
git commit --no-gpg-sign -m "chore: delete obsolete python scripts and templates for spec-to-readable-html"
```
Expected: Commit succeeds.

---

### Task 3: Local Verification

**Files:**
- Verify: `skills/spec-to-readable-html/SKILL.md`

- [ ] **Step 1: Run Skill Validator**

Verify the skill markdown structure against the rules in `AGENTS.md`:
1. Check that `skills/spec-to-readable-html/SKILL.md` is present.
2. Confirm the YAML frontmatter contains exactly the keys `name` and `description`.
3. Confirm there are no `TODO` or `TBD` placeholders.
4. Confirm code blocks specify syntax highlighting (e.g., `bash`, `markdown`).
5. Confirm no leftover python files remain in `skills/spec-to-readable-html/`.
