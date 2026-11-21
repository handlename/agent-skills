# Maintenance

Section numbers (`section N`, `6.x`) refer to [SKILL.md](../SKILL.md).

## Dashboard and kanbans from earlier versions

Section 6.5. `setup` brings an unedited dashboard from an earlier version up to date. An edited one keeps its old layout: when the user asks, tell them to add `summary` after `formula.title` in the `order:` line of the task kanbans, to add the filter line `- '!file.hasTag("project")'` under `and:` in both task kanbans, and to copy the `## プロジェクト` section from a dashboard that `setup` creates in an empty directory (`setup --dir <tmp>`). A project note created before session-supervisor 0.57.0, or from a template without it, has no `## タスク` kanban; `commit` never adds one, because the sections below `## 概要` that no note op writes are the user's. When the user asks, tell them to paste the `## タスク` section of a project note `state_tool` creates right after `## 概要` of the note, and of their project template. A `## タスク` kanban written before 0.73.0 lists the cards of a column in no set order; to sort it by the last change, add these lines under `order:` of its view (the user's section, so `commit` never adds them):

```yaml
    sort:
      - property: file.mtime
        direction: DESC
```

A `## タスク` kanban written before 0.79.0 shows all of the project's tasks on one kanban; to split it by `assignee` like the dashboard, replace the section with the `## タスク` section of a project note `state_tool` creates, in the note and in the project template (the user's section, so `commit` never rewrites it; until then `setup` reports the template as `outdated`). Task kanbans written before 0.82.0 (the `## タスク` sections, and an edited dashboard) ignore `priority`; to sort them by it, replace the `## タスク` section the same way, and in each task kanban of an edited dashboard add the `priority_rank` line under `formulas:`, replace its `sort:` lines (or add them under `order:`) with the ones below, and add `priority` after `summary` in its `order:` line:

```yaml
formulas:
  title: link(file.name, title)
  priority_rank: 'if(priority == "A", 1, if(priority == "C", 3, 2))'
```

```yaml
    sort:
      - property: formula.priority_rank
        direction: ASC
      - property: file.mtime
        direction: DESC
```

## Old blocker wording

Section 5 (the user action table). Old blocker wording: when the user mentions a user action of a task in this chat and it may be a free-text report blocker recorded before 0.27.0, take the task's live workers (`intended_state` not `archived`, `observed_state` neither `archived` nor `missing`) whose `last_report.status` is `needs_input`, `blocked`, or `failed`. Read each one's message `last_report.message_ref` again and run `parse-report` on it. The user action is an old blocker only when its description equals one of those `blockers`; otherwise it belongs to another row (an unparsed summary, a failed verification).

- One matching worker: in one candidate, replace the user action with that worker's guidance (6.3 step 4; a new id). Send nothing. Reply with the user's words quoted and point them to that worker's session.
- No live worker: remove the user action and tell the user.
- Two or more matching workers: ask the user which worker the question came from.

## Uninstall

1. Confirm no worker or manager is `running`, `idle`, or `needs_input` (reconcile first), stop periodic reconciliation, and end `sv-watch` (`hc kill <name_prefix>sv-watch`, `hc rm <name_prefix>sv-watch`). Ask the user to end `sv-chat` themselves, or `sv` and `sv-chat` together with `<dir>/sv.sh stop` once this turn is over.
2. Ask the user to delete the supervisor directory themselves. The agent never deletes it.
3. Leftover worktrees (kept for uncommitted changes, or from desktop-app workers of earlier versions) can be listed and removed by the user:

   ```bash
   git -C "<repo>" worktree list
   git -C "<repo>" worktree remove "<worktree>"
   ```

   Branches stay in each repository.

## Migration to notes (schema 2)

A `state.json` at `schema_version: 1` keeps working with the v1 commit until it is migrated. Migrate only when the user asks, with no worker running:

```bash
python3 "<dir>/.supervisor/state_tool.py" migrate-v2 --state "<dir>/state.json" --dry-run
python3 "<dir>/.supervisor/state_tool.py" migrate-v2 --state "<dir>/state.json"
```

The dry run lists the notes it would write. The real run writes one note per task, project, and stub (a revived task and its stub share one; project proposal user actions become backlog/user proposal notes; read user actions become `held`), then `state.json`. Rerun it after an interruption; it refuses a note that exists with other content. Rollback: write the v1 form with `export-v1 --state "<dir>/state.json" --out "<dir>/state.v1.json"`, check it (it prints `tasks_without_workers`: the previous version rejects a task without workers, so cancel those tasks or let their workers start first, and export again), and replace `state.json` with it from a supervisor on the previous skill version (`setup` from that version reinstalls its script). Held workers come back as read user actions; follow-ups stay quoted in the notes.

Moving a supervisor from the desktop app to Herdr needs no state migration: run `setup` again, start `sv` and `sv-watch` in Herdr (`<dir>/sv.sh start`, [setup.md](setup.md), Starting the supervisor), and leave the remaining desktop-app workers and managers to the user (6.4, section 7).
