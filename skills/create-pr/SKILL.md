---
name: create-pr
description: Create a Pull Request from the current branch to a base branch, using GitHub CLI to generate a suitable title and description from the commit history.
compatibility: Requires git and gh (GitHub CLI) installed and authenticated.
metadata:
  argument-hint: "[--base-branch=<branch>] [--draft]"
---

# Create Pull Request Skill

## Overview

Create a Pull Request from the current branch to a specified base branch. Use the GitHub CLI (`gh`) to automatically generate a suitable title and description from the commit history.

## Instructions

Execute the following tasks. Run tasks in parallel where possible.

### Phase 1 (Parallelizable)
- Verify the current branch and repository status.
- Read `README.md` to determine the language to use for the Pull Request.
- Determine the base branch and check if an existing PR already exists.
- Check for the existence of design documents.
- Detect whether the repository adopts tagpr for releases (see "tagpr Version-Bump Label" guideline).

### Phase 2 (Sequential)
1. Verify the push status to remote, and push if necessary.
2. Analyze the commit history to understand the intent of the changes.
3. If a design document exists, gather information from it.
4. Assess whether the change is structural enough to warrant a big-picture diagram (see "Big-picture Diagram" guideline). If so, author a mermaid diagram of the target system and how this PR's change fits within it.
5. Check if a PR template exists and generate the description (embed the mermaid source as a draft when a diagram was authored).
6. Generate the PR title.
7. If the repository adopts tagpr, ask the user which version-bump label to apply to this PR (see "tagpr Version-Bump Label" guideline). Skip this step otherwise.
8. Present the PR creation details — including the drafted diagram (shown as mermaid source at this point) — to the user and obtain approval. This approval also authorizes the diagram attachment upload.
9. If a diagram was authored: spawn a subagent invoking the `mermaid-to-issue-image` skill, then embed the returned image (or fall back to inline mermaid on failure).
10. Create the PR (applying the chosen tagpr label, if any) and report the result.

---

## Options

Parse `$ARGUMENTS` as follows:
- `--base-branch=<branch>`: The base branch name. If not specified, the default branch is used.
- `--draft`: Create the Pull Request as a draft.

---

## Verification

- The PR is successfully created and its URL is retrieved.
- The PR details (title, description summary, URL) are reported to the user.
- When a big-picture diagram was warranted, it is embedded as an image (or, on upload failure, as an inline mermaid block with the fallback reported), and no paragraph or list item in the body contains an internal line break.

---

## Guidelines

### Language Determination
Match the language used in the PR title and description to the language used in the repository's `README.md`.
- **README.md exists:** Read the content and determine the language.
- **README.md does not exist:** Default to Japanese.

### Pre-checks
```bash
# Current branch name
CURRENT_BRANCH=$(git branch --show-current)

# Determine the base branch
BASE_BRANCH=${BASE_BRANCH_OPTION:-$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')}

# Check for same branch
[ "$CURRENT_BRANCH" = "$BASE_BRANCH" ] && echo "ERROR: Same branch"

# Check for existing PR
gh pr list --head "$CURRENT_BRANCH" --base "$BASE_BRANCH" --json url,title
```

Items to verify:
- The current branch is different from the base branch.
- No existing PR exists for the same branch pair.
- If there are unpushed commits, suggest running `git push -u origin HEAD`.

### Commit History Analysis
```bash
# Commit history
git log "$BASE_BRANCH..HEAD" --oneline --no-decorate

# List of changed files
git diff --name-status "$BASE_BRANCH...HEAD"
```
Understand the **intent of the changes** from these. Focus on *why* this change is necessary rather than specific implementation details.

### Checking and Gathering Design Documents
Check if there are design documents related to the changes. Search the following candidates in order:

**Search Targets (Priority Order):**
1. General design documents in the repository: `DESIGN.md`, `docs/design/`, `docs/architecture/`
2. Design documents corresponding to the modified files: documents inferred from the paths of the changed files.
3. Document URLs mentioned in commit messages or issues.

**If a document is found:**
- Determine the type (online/local).
- Online document: Record the URL.
- Local file: Record the file path and content.

**If no document is found:**
- Ask the user if any design document exists.
- If the user provides a URL or local file path, use it.

### PR Description Generation Principles
- **Important: Write the PR title and body in the language determined in the "Language Determination" step.**
- **Important: Do not describe implementation details that can be understood by reading the diff. Explain the intent and background of the change.**
- **Important: Never insert a line break inside a paragraph or a list item.** Write each paragraph and each list item as a single unbroken line, and let the browser handle wrapping at render time. Line breaks belong only *between* blocks (between paragraphs, between list items, before/after headings or code blocks) — never *within* one. This keeps the source diff-friendly and avoids hard wraps that render awkwardly at different viewport widths.

*Good Examples:*
- "To improve search result display speed based on user feedback."
- "To address vulnerabilities pointed out in the security audit."

*Examples to Avoid:*
- "Added caching to the getUser function in UserService.ts."
- "Cleaned up imports and imported a new function from utils."

### PR Template Check and Description Generation
Check for the existence of `.github/PULL_REQUEST_TEMPLATE.md`.

**If template exists:**
- Read the template and fill in each section with appropriate content.
- Keep checklist items in `[ ]` format.
- If there are checklist items regarding test passage, mark them as checked `[x]`.
- If a design document exists, describe it in the appropriate place (e.g., Related Documents section, or at the end) according to the "How to Describe Design Documents" section below.
- If a big-picture diagram was authored, place it in a suitable spot near the top of the body (e.g. right after the change-overview section) following the "Big-picture Diagram" guideline.

**If template does not exist:**
Generate the description with the following structure:
```markdown
## Why this change is necessary
[Motivation, background, or issue to solve]

## Approach
[Selected solution and reasons. If there were alternatives, why this approach was chosen]

## Big picture
[Include this section only for structural changes. An SVG image uploaded via mermaid-to-issue-image, with the mermaid source preserved below in a collapsed <details> block. See the "Big-picture Diagram" guideline]

## Design Documents
[Include this section only if design documents exist. See below for details]

## Review Points
[Specific points for reviewers to check, or design decisions that need discussion]
```

**How to Describe Design Documents:**
If a design document exists, describe it according to its type as follows. If no design document exists, omit the "Design Documents" section entirely.
- **Online Document (URL accessible by reviewers):**
  List it as a link:
  ```markdown
  ## Design Documents
  - [Design Document Title or Filename](URL)
  ```
- **Local File:**
  Include the file content directly in the PR description, collapsed using `<details><summary>` tags:
  ```markdown
  ## Design Documents
  <details>
  <summary>Design Document Title or Filename (Click to expand)</summary>

  [File contents here]

  </details>
  ```
- **If multiple design documents exist:**
  Combine the formats above and list all documents.

**About testing status:**
Passing relevant tests is a prerequisite for creating a PR and does not need to be explicitly mentioned in the description. Only mark test-related checklists as checked if they exist in the template.

### Big-picture Diagram (structural changes only)
When the change is **structural**, attach a diagram that lets a reviewer grasp the target system's big picture and where this PR's change fits within it. This is judged from the commit history and changed files, not requested from the user.

**When to attach (structural change):** the change alters relationships between multiple components, a data flow, or a state machine — anything where a picture of the whole makes the change easier to understand than prose alone.

**When NOT to attach:** trivial or self-contained changes — a single-function bugfix, a typo/wording fix, a dependency bump, config-only edits, or anything a reviewer can fully grasp from the diff. When in doubt, prefer no diagram; an unhelpful image is worse than none.

**What the diagram depicts:** the target system's overall structure **and** how this PR's change relates to it — situate the changed/added/removed parts within the surrounding system (e.g. affected components highlighted in context, or the current→changed data flow shown against the system it lives in). Keep it high-level for human comprehension, not an implementation transcript.

**How to produce and embed it** (mirrors the `plan-to-issue` skill):
1. Author the diagram as **mermaid** — the mermaid source is the canonical record of the diagram's structure. Pick the type that best conveys it (`flowchart`, `sequenceDiagram`, `stateDiagram-v2`, `erDiagram`, …).
2. The mermaid appears as a visible source block in the draft shown at the confirmation gate. Uploading is a GitHub write, so it happens **only after** the user approves PR creation (that approval covers the upload).
3. After approval, spawn a **subagent invoking the `mermaid-to-issue-image` skill** (input: the mermaid source + the target `owner/repo`; output: an attachment URL, or `FAILED: <reason>`).
4. On success, embed the image, immediately followed by the mermaid source in a collapsed `<details>` block so the canonical structure stays one click away:
   ~~~markdown
   ![big picture](<user-attachments URL>)

   <details>
   <summary>図のソース / Diagram source (mermaid)</summary>

   ```mermaid
   flowchart LR
       ...
   ```

   </details>
   ~~~
   **Never wrap the mermaid in an HTML comment** (`<!-- -->`): mermaid arrows (`-->`) contain `--`, which terminates the comment early and leaks the source as visible text.
5. **Fallback:** if the subagent returns `FAILED` (e.g. GitHub session not logged in, npx cannot fetch `@playwright/cli`), keep a visible ` ```mermaid ` code block instead and report the fallback and its reason to the user. Never let the image step block PR creation.

**To update the diagram later:** edit the mermaid, re-invoke `mermaid-to-issue-image`, and replace the image URL. The SVG layout is throwaway; only the mermaid persists.

### PR Title Generation
- Single commit: Use the first line of the commit message.
- Multiple commits: Generate from the most significant Conventional Commits type (`feat > fix > refactor > docs > ...`) and the intent of the change.
- Within 50 characters, no period at the end.
- Written in the language determined in the "Language Determination" step.

### tagpr Version-Bump Label (release-workflow repositories only)
This applies **only** when the repository adopts [tagpr](https://github.com/Songmu/tagpr) for releases. tagpr derives the next version from the labels on merged PRs, so a version-bump label must be applied to *this* PR at creation time.

**Detection:** the repository adopts tagpr when a `.tagpr` file exists at the repository root. If it is absent, skip this guideline entirely — do not ask about labels.

**Resolve the label names from `.tagpr`** (they are configurable via tagpr settings, so never hard-code them):
```bash
# .tagpr is gitconfig-format. Fall back to tagpr defaults when unset.
MAJOR_LABEL=$(git config -f .tagpr tagpr.majorLabels 2>/dev/null | cut -d, -f1)
MINOR_LABEL=$(git config -f .tagpr tagpr.minorLabels 2>/dev/null | cut -d, -f1)
MAJOR_LABEL=${MAJOR_LABEL:-tagpr:major}
MINOR_LABEL=${MINOR_LABEL:-tagpr:minor}
```
`majorLabels` / `minorLabels` may hold several comma-separated values; use the **first** as the label to apply.

**Ask the user** which version bump this PR represents, presenting the resolved names (three choices, default **None**):
- `$MINOR_LABEL` — minor bump
- `$MAJOR_LABEL` — major bump
- None — patch (default; apply no label, since patch is tagpr's default when no bump label is present)

**If the chosen label does not yet exist in the repository** (`gh label list` does not contain it), `gh pr create --label` would fail. Do **not** create it silently: warn the user that the label is missing and ask whether to (a) create it now with `gh label create "<name>"` and apply it, or (b) proceed without the label. Respect the choice.

Carry the resulting label (if any) into the confirmation gate display and the `gh pr create` command.

### Confirmation of Creation Content
Before creating the PR, present the following to the user and obtain approval:
```text
=== Confirm Pull Request Creation ===
Title: [Title]
Base Branch: [Branch Name]
Draft PR: [Yes/No]
Design Document: [Yes (Type: Online/Local) / No]
Big-picture Diagram: [Yes (attaches image after approval) / No]
Version bump (tagpr): [minor / major (resolved label name) / None / N/A (not a tagpr repo)]

Description:
---
[Description]
---

Do you want to create this PR?
```
When a big-picture diagram was authored, show its mermaid source inline in the description here, and note that approving also authorizes uploading it as an image attachment.

### Executing PR Creation
```bash
# LABEL_FLAG is set (e.g. --label "$MINOR_LABEL") only when a tagpr version-bump
# label was chosen and confirmed to exist; empty otherwise.
gh pr create \
  --title "$TITLE" \
  --body "$BODY" \
  --base "$BASE_BRANCH" \
  $DRAFT_FLAG \
  $LABEL_FLAG
```

### Reporting Creation Result
```text
✓ Pull Request created successfully

URL: [PR URL]
Title: [Title]
Base Branch: [Branch Name]

--- Notes ---
- Set reviewers: gh pr edit [URL] --add-reviewer [username]
- Add labels: gh pr edit [URL] --add-label [label]
```

---

## Interruption Conditions

Stop the process and report the situation and remaining tasks if any of the following are met:
- Current branch is identical to the base branch.
- An existing PR already exists for the same branch pair.
- GitHub CLI is not installed or authentication is not complete.
- `git push` fails and the user declines to retry.
- The user does not approve creating the PR.

---

## Prohibited Actions

- Creating a PR without user confirmation.
- Describing implementation details that can be understood by reading the diff in the PR description.
- Ignoring the structure of the PR template when it exists.
- Modifying files unrelated to the task.
- Describing test passage as an independent section or item in the PR description (except when it exists in the template).
- Inserting a line break inside a paragraph or a list item in the PR description (write each as a single line; line breaks belong only between blocks).
- Uploading the diagram attachment before the user has approved PR creation.

---

## Supplementary Notes

- If the intent cannot be read from commit messages, infer it from the changes and ask the user.
- If multiple Conventional Commits types are mixed, adopt the one with the highest impact for the title.
- If the `gh` command fails, check the usage with `--help` and re-run.
