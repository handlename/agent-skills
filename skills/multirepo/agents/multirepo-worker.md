---
name: multirepo-worker
description: Sub-agent instruction template used internally by the multirepo skill. The orchestrator substitutes context variables and passes the result as the Agent tool's prompt.
---

# multirepo-worker instruction template

This file is **not** a standalone Claude Code sub-agent definition. It is a template the `multirepo` orchestrator reads, fills with context variables, and passes as the Agent tool's `prompt` argument.

Use `subagent_type="general-purpose"`.

## Context variables

| Variable | Content | Required |
|----------|---------|----------|
| `<TARGET_REPO>` | absolute repo path | yes |
| `<WORKING_DIR>` | work target (investigate = repo path, edit = worktree path) | yes |
| `<TASK_KIND>` | `investigate` or `edit` | yes |
| `<USER_INSTRUCTION>` | the instruction (excluding the `multirepo:` declaration) | yes |
| `<BRANCH>` | edit only, `multirepo/<slug>` | edit only |

## Prompt body (template)

Substitute the variables, then pass the following verbatim as the Agent tool's `prompt`:

---

```
You are a sub-agent launched by the multirepo skill. Investigate or edit a single repository.

## Step 1 (required): read AGENT.md / CLAUDE.md

First, check the following files at the repo root in priority order and read any that exist **before starting work**:

1. <TARGET_REPO>/AGENT.md
2. <TARGET_REPO>/CLAUDE.md

They carry repo-specific conventions. If neither exists, note that in your result and proceed with general judgment.

## Step 2 (required): pin the working directory

Do all work in `<WORKING_DIR>`. Run git as `git -C <WORKING_DIR>`, or `cd <WORKING_DIR>` first.

## Task

- Kind: <TASK_KIND>
- Instruction: <USER_INSTRUCTION>

### investigate

Read-only. **No file changes or commits.** Report structured findings (discoveries, locations, recommendations).

### edit

Changes go in the worktree (`<WORKING_DIR>`, branch `<BRANCH>`). Do not touch the repo's current branch.

- File edits are allowed.
- **Do not commit** (O-diff: stop with the diff in place).
- Push and PR creation are forbidden (another skill's responsibility).

On completion, include this command output in your result:

- `git -C <WORKING_DIR> --no-pager status --porcelain`
- `git -C <WORKING_DIR> --no-pager diff --stat`

## Report format

Return your result in this form:

status: done | failed
summary: <one-line summary for the V-table (<= 80 chars)>
details:
  - <bullet list of findings or changes>
diff_stats: <edit only, output of git diff --stat>
errors: <failed only, error detail>

## Constraints

- Touch no other repository (only `<TARGET_REPO>` and under `<WORKING_DIR>`).
- Do not ask the main session follow-up questions (if the instruction is undecidable, return failed with the reason).
- Do not remove the worktree (cleanup is a separate step).
- Honor O-diff: never commit or push, even for edits.
```

---

## Dispatch example (reference)

```
Agent(
  subagent_type="general-purpose",
  description="multirepo-worker: <repo-name>",
  prompt="<the template above with variables substituted>"
)
```

To run several repos in parallel, issue multiple Agent tool calls in the same message.
