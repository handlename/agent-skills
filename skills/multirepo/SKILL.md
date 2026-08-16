---
name: multirepo
description: Investigate or edit multiple local git repositories in parallel. Fires when a message contains a `multirepo: <repo1>, <repo2>, ...` declaration. The main session acts as an orchestrator: it assigns one dedicated sub-agent (multirepo-worker) per repo, handles dispatch and result aggregation only, and never touches code itself. Worktrees are created for edits only; commits and PRs are left to other skills.
argument-hint: "<repo1>, <repo2>, ... <instruction>"
---

# multirepo

Run investigation or edits across several already-cloned local git repositories **in parallel**.

The main session acts as an orchestrator. It never touches code directly: it delegates each repository's work to a dedicated `multirepo-worker` sub-agent and handles only progress display and result aggregation.

## Trigger

The skill fires when a message contains the following declaration, anywhere in the text:

```
multirepo: <repo-path1>, <repo-path2>, ...
```

Examples:

- `multirepo: ~/src/foo, ~/src/bar audit where the auth library is used`
- `Let me review the situation first. Then multirepo: ~/src/a, ~/src/b migrate to v2`

From the declaration onward the session is in **multi-repo mode**. A new `multirepo: ...` declaration overwrites the repo set. There is no explicit reset keyword (the mode ends with the session).

Because description-based detection depends on model inference, use `/multirepo` to trigger explicitly when reliable activation is required.

## Phase 1: Detect keyword and parse repo list

Extract the `multirepo:` declaration and parse the following comma-separated tokens as repo paths.

- Accept absolute paths only (tilde expansion allowed).
- Reject relative paths and empty tokens as invalid.
- Hold the parsed repo list at session scope.

## Phase 2: Classify task kind (investigate vs edit)

Judge from the natural-language instruction in the same message:

- **investigate** (read-only): "audit", "check", "report", "find", "analyze", etc. → no worktree.
- **edit**: "fix", "update", "apply", "change", "refactor", "migrate", and other write verbs → create a worktree.

When undecidable, treat it as **investigate** (the safe default: no unnecessary worktree or branch).

## Phase 3: Pre-flight validation

Check each repo in turn:

1. Path exists: `test -d <repo>`
2. Is a git repo: `git -C <repo> rev-parse --git-dir`
3. (edit only) working tree is clean: `git -C <repo> status --porcelain` is empty

A repo that fails validation is shown as `failed` with the cause in the V-table and **excluded from dispatch**. The remaining repos still launch in parallel. Abort the whole task only when **every repo fails** validation.

## Phase 4: Slug collision check and worktree prep

### Slug generation

The orchestrator extracts 1–3 key English keywords from the instruction (translating or summarizing to English if the instruction is non-English), kebab-cases them, and appends a timestamp suffix:

```bash
# e.g. task_summary="auth client v2 migration"
slug="$(printf %s "$task_summary" | tr 'A-Z' 'a-z' | tr -cs 'a-z0-9' '-' | sed 's/^-//;s/-$//' | cut -c1-30)"
slug="${slug:-task}-$(date +%Y%m%d-%H%M%S)"
branch="multirepo/${slug}"
```

> For non-ASCII instructions the pipeline above yields an empty string, so the orchestrator must convert to English keywords by inference **first** (e.g. `認証ライブラリの利用箇所を調査` → `auth-usage-audit`).

Before dispatch, check each repo with `git -C <repo> --no-pager branch --list <branch>` and append `-2`, `-3`, etc. on collision.

### Worktree creation (edit only)

```bash
git -C "$repo" worktree add ".git/worktrees/multirepo-${slug}" -b "$branch"
```

- Working directory: `<repo>/.git/worktrees/multirepo-<slug>`
- Branch: `multirepo/<slug>`

For investigate, no worktree is created; work is read-only on the current branch.

## Phase 5: Sub-agent dispatch

Launch one `multirepo-worker` per repo in parallel via the Agent tool.

Build each dispatch prompt from the bundled `agents/multirepo-worker.md`, substituting these context variables:

| Variable | Content |
|----------|---------|
| `<TARGET_REPO>` | absolute repo path |
| `<WORKING_DIR>` | repo path for investigate, worktree path for edit |
| `<TASK_KIND>` | `investigate` / `edit` |
| `<USER_INSTRUCTION>` | the instruction (excluding the `multirepo: ...` declaration) |
| `<BRANCH>` | edit only, `multirepo/<slug>` |

Use `subagent_type="general-purpose"`.

Default concurrency is the repo count (fully parallel). Adjust based on cues in the instruction:

- "one at a time", "sequentially" → concurrency **1**.
- "N at a time", "N in parallel" → batch size **N**.
- dependency cues ("X first, then Y") → dispatch sequentially in dependency order.
- otherwise → fully parallel.

For very large repo counts (e.g. 10+), split into batches (run sequentially between batches) to respect the Agent tool's parallel-launch limit.

> **Important**: the main session only dispatches, displays progress, and aggregates results. All real work — file edits, git commands — happens **inside the sub-agents**.

## Phase 6: Progress monitoring (V-table)

As sub-agents launch and finish, the main session updates a progress table:

```
| Repo       | Status   | Branch                              | Summary       |
|------------|----------|-------------------------------------|---------------|
| ~/src/foo  | done     | multirepo/auth-v2-20260507-163000   | updated 3     |
| ~/src/bar  | running  | multirepo/auth-v2-20260507-163000   | (in progress) |
| ~/src/baz  | pending  | -                                   | -             |
```

Status values: `pending` / `running` / `done` / `failed` / `cancelled`

Update points: all repos `pending` just before dispatch; `running` on launch; `done` or `failed` on completion; `cancelled` for anything unfinished when F-fast triggers.

## Phase 7: F-fast (abort immediately on failure)

As soon as any sub-agent returns `failed`:

1. Mark that repo `failed`.
2. Cancel not-yet-launched dispatches.
3. Attempt a soft stop (TaskStop) on running sub-agents (best-effort).
4. Report the failure cause and the status of already-completed repos immediately.

> **Known limitation**: canceling a running Agent-tool sub-agent is best-effort in Claude Code — a running sub-agent may run to completion. Operationally, F-fast means "un-launched work is reliably stopped; running work is interrupted best-effort".

Per the S-all rule, a single `failed` marks the whole task as failed.

## Phase 8: Completion report (S-all)

### All sub-agents succeeded

```
✓ multirepo: work completed across all N repositories

| Repo       | Status | Branch                              | Summary   |
|------------|--------|-------------------------------------|-----------|
| ~/src/foo  | done   | multirepo/auth-v2-20260507-163000   | updated 3 |
| ~/src/bar  | done   | multirepo/auth-v2-20260507-163000   | updated 2 |

Each worktree still holds its diff. Run commits and PR creation with a separate
skill (e.g. `github-resource-access`).
```

### One or more failed

```
✗ multirepo: failed (S-all not met)

| Repo       | Status    | Branch        | Summary                  |
|------------|-----------|---------------|--------------------------|
| ~/src/foo  | done      | multirepo/... | completed                |
| ~/src/bar  | failed    | multirepo/... | (error cause)            |
| ~/src/baz  | cancelled | -             | cancelled by F-fast      |

Partial completion is not success. Choose one:
- fix the cause and re-run
- discard the worktree changes of completed repos
- review manually as-is
```

## Out of scope

| Item | Delegated to / note |
|------|---------------------|
| Commits, push, PR creation | a separate skill such as `github-resource-access` |
| GitHub issue sync | a separate skill |
| Agent teams | not used (this skill is 1 repo = 1 sub-agent) |
| Reset keyword | not needed (re-declare to overwrite; ends with the session) |
| Auto-cloning remote repos | out of scope (repos must already be cloned locally) |
| Language/framework-specific sub-agents | not used (one shared `multirepo-worker` for all repos) |

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Skill does not fire | Description-based auto-detection depends on model inference. Placing `multirepo:` at the start of the message raises the hit rate. |
| Accumulated worktrees | Not removed automatically. Clean up with `git worktree prune` and `git worktree remove <path>`. |
| Error on dirty working tree | Commit or stash before an edit (no auto-stash). |
| Sub-agent won't cancel | Known limitation. F-fast is a best-effort soft stop. |

## Sub-agent instruction template

The dispatch prompt lives in `agents/multirepo-worker.md`. The orchestrator reads that file, fills in the context variables, and passes the result as the Agent tool's `prompt`.
