---
name: create-pr
description: Create a Pull Request from the current branch to a base branch, using GitHub CLI to generate a suitable title and description from the commit history.
compatibility: Requires git and gh (GitHub CLI) installed and authenticated.
metadata:
  argument-hint: "[--base-branch=<branch>] [--draft]"
---

# Create Pull Request Skill

Create a Pull Request from the current branch to a specified base branch. Use the GitHub CLI (`gh`) to automatically generate a suitable title and description from the commit history.

## Instructions

Execute the following tasks. Run tasks in parallel where possible.

### Phase 1 (Parallelizable)
- Verify the current branch and repository status.
- Read `README.md` to determine the language to use for the Pull Request.
- Determine the base branch and check if an existing PR already exists.
- Check for the existence of design documents.

### Phase 2 (Sequential)
1. Verify the push status to remote, and push if necessary.
2. Analyze the commit history to understand the intent of the changes.
3. If a design document exists, gather information from it.
4. Check if a PR template exists and generate the description.
5. Generate the PR title.
6. Present the PR creation details to the user and obtain approval.
7. Create the PR and report the result.

---

## Options

Parse `$ARGUMENTS` as follows:
- `--base-branch=<branch>`: The base branch name. If not specified, the default branch is used.
- `--draft`: Create the Pull Request as a draft.

---

## Completion Criteria

- The PR is successfully created and its URL is retrieved.
- The PR details (title, description summary, URL) are reported to the user.

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
BASE_BRANCH=${specified_value:-$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')}

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

**If template does not exist:**
Generate the description with the following structure:
```markdown
## Why this change is necessary
[Motivation, background, or issue to solve]

## Approach
[Selected solution and reasons. If there were alternatives, why this approach was chosen]

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

### PR Title Generation
- Single commit: Use the first line of the commit message.
- Multiple commits: Generate from the most significant Conventional Commits type (`feat > fix > refactor > docs > ...`) and the intent of the change.
- Within 50 characters, no period at the end.
- Written in the language determined in the "Language Determination" step.

### Confirmation of Creation Content
Before creating the PR, present the following to the user and obtain approval:
```text
=== Confirm Pull Request Creation ===
Title: [Title]
Base Branch: [Branch Name]
Draft PR: [Yes/No]
Design Document: [Yes (Type: Online/Local) / No]

Description:
---
[Description]
---

Do you want to create this PR?
```

### Executing PR Creation
```bash
gh pr create \
  --title "$TITLE" \
  --body "$BODY" \
  --base "$BASE_BRANCH" \
  $DRAFT_FLAG
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

---

## Supplementary Notes

- If the intent cannot be read from commit messages, infer it from the changes and ask the user.
- If multiple Conventional Commits types are mixed, adopt the one with the highest impact for the title.
- If the `gh` command fails, check the usage with `--help` and re-run.
