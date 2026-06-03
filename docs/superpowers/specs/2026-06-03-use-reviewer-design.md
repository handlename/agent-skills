# Design Specification: Rewrite Spec-to-Readable-HTML Skill to use `reviewer`

This document defines the changes required to rewrite the `spec-to-readable-html` skill within the `agent-skills` repository to leverage the external Go-based tool [reviewer](https://github.com/handlename/reviewer) instead of the local legacy Python scripts.

---

## 1. Goal

Simplify the maintenance of the `spec-to-readable-html` skill by:
- Deleting the custom Python parser (`render.py`), server (`spec-server.py`), and template files (`references/template.html`, `references/html-output-template.md`).
- Using the external `reviewer` Go tool, which compiles Markdown to an interactive spec sheet and hosts a local review server.
- Prompting the user to confirm installation of the `reviewer` command if it is not present in the system's `$PATH`.

---

## 2. Directory Structure Changes

Obsolete files in `skills/spec-to-readable-html/` will be removed.

### Files to Delete:
- `skills/spec-to-readable-html/scripts/render.py`
- `skills/spec-to-readable-html/scripts/spec-server.py`
- `skills/spec-to-readable-html/references/template.html`
- `skills/spec-to-readable-html/references/html-output-template.md`

### Files to Modify:
- `skills/spec-to-readable-html/SKILL.md`

---

## 3. Skill Workflow Implementation

The executing agent will perform the following steps when this skill is invoked:

### Step 1: Detect and Install `reviewer`
1. Check if the `reviewer` command is available using `which reviewer`.
2. If `reviewer` is missing:
   - Ask the user for confirmation:
     > "The `reviewer` command is not installed. May I install it using Homebrew or Go?"
   - Upon approval, execute the appropriate command based on OS/environment:
     - On macOS: `brew install handlename/tap/reviewer`
     - Otherwise: `go install github.com/handlename/reviewer/cmd/reviewer@latest`

### Step 2: Launch Review Server
1. Resolve a target path for the compiled HTML file within a Git-ignored directory (e.g. check for `dist/`, `build/`, or `tmp/` under workspace root). If none exist, fallback to creating and using a `tmp/` folder.
2. Run the `reviewer serve` command in the background:
   ```bash
   reviewer serve <path_to_markdown_spec> -o <path_to_resolved_html> -p <port>
   ```
   - Bind to a free local port (e.g., `5500` or `5555`).
   - The CLI automatically compiles the Markdown, writes it to the output path, opens the default web browser, and starts the review server.

### Step 3: Collect and Apply Feedback
1. Wait for the background process to print `FEEDBACK_RECEIVED` and exit cleanly.
2. Read the generated JSON comments from `<input-filename_without_ext>-feedback.json` (saved as a sibling of the source Markdown spec).
3. Automatically edit the source spec Markdown to incorporate the user's feedback.
4. Rerun `reviewer serve` to let the user review the updated document.

---

## 4. Verification Plan

Before completing the task, the following checklist must pass:

- [ ] All obsolete files in `skills/spec-to-readable-html/scripts/` and `references/` are successfully deleted.
- [ ] No Python script references remain in `skills/spec-to-readable-html/SKILL.md`.
- [ ] `skills/spec-to-readable-html/SKILL.md` contains the valid YAML frontmatter and fully documented Go CLI steps.
- [ ] Testing the rewritten skill successfully prompts for/verifies the `reviewer` command, serves the file, and captures comments.
