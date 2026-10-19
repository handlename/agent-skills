# state.json Schema

`state.json` is the single source of truth for the supervisor. Only the supervisor session writes it, and only through `state_tool.py commit`, which validates the candidate, checks `rev`, derives `task.status`, moves finished tasks to `archived`, and writes atomically. Never edit `state.json` directly.

## Identifiers

Field names are agent-neutral:

- `session_id`: the session identifier issued by the runtime (the value the start capability returns).
- `handle`: the `[a-z0-9-]` name (24 characters or fewer) given to the session at start, e.g. `t0003-w0004`.
- `branch` / `worktree_path`: decided by the app (it adds a prefix and a suffix to the requested slug). Fill them from the get capability after start; never guess them.
- `task.id`: `T-NNNN`. `worker.id`: `w-NNNN`. `user_actions[].id`: `UA-NNNN`. Allocate the next number after the highest one in the current `tasks` / `workers` / `user_actions`; for task ids also count the `archived` stubs, which keep their task id. Stubs keep no worker or user-action ids, so those numbers may be reused after a task is archived; uniqueness is checked only within the current state.

## Example

```json
{
  "schema_version": 1,
  "rev": 12,
  "updated_at": "2026-10-17T10:42:00+09:00",
  "supervisor": {
    "dir": "/Users/handlename/supervisor",
    "session_ids": ["sess_sup_01", "sess_sup_02"],
    "archive_consent": { "text": "成果を確認できた worker は確認なしでアーカイブしてよい", "at": "2026-10-16T08:50:00+09:00" },
    "dashboard": { "url": "https://claude.ai/code/artifact/xxxxxxxx", "path": "/Users/handlename/supervisor/dashboard.html" }
  },
  "tasks": [
    {
      "id": "T-0003",
      "title": "Add retry to uploader",
      "request": "repo-a と repo-b の uploader にリトライを追加して。push して PR を作ってよい",
      "push_consent": { "text": "push して PR を作ってよい", "at": "2026-10-17T09:00:00+09:00" },
      "status": "running",
      "next_action": "w-0004 / w-0005 の完了待ち",
      "user_actions": [
        { "id": "UA-0002", "description": "repo-b の PR をレビュー", "since": "2026-10-17T10:30:00+09:00" }
      ],
      "artifacts": [
        { "kind": "branch", "label": "repo-a hn/uploader-retry-x1", "ref": "/Users/handlename/src/github.com/org/repo-a#hn/uploader-retry-x1" },
        { "kind": "commit", "label": "repo-a 3f2a9c1", "ref": "3f2a9c1e0d..." },
        { "kind": "pr", "label": "repo-a#42", "url": "https://github.com/org/repo-a/pull/42" },
        { "kind": "ci", "label": "repo-a CI", "url": "https://github.com/org/repo-a/actions/runs/123" }
      ],
      "worker_ids": ["w-0004", "w-0005"],
      "created_at": "2026-10-17T09:00:00+09:00"
    },
    {
      "id": "T-0004",
      "title": "Tidy notes folder",
      "request": "~/notes の重複ファイルを整理して",
      "push_consent": null,
      "status": "waiting",
      "next_action": "~/notes の先行 worker w-0006 のアーカイブ後に起動",
      "user_actions": [],
      "artifacts": [],
      "worker_ids": ["w-0007"],
      "created_at": "2026-10-17T10:00:00+09:00"
    }
  ],
  "workers": [
    {
      "id": "w-0004",
      "task_id": "T-0003",
      "session_id": "sess_abc123",
      "started_by": "sess_sup_01",
      "handle": "t0003-w0004",
      "title": "uploader retry (repo-a)",
      "cwd": "/Users/handlename/src/github.com/org/repo-a",
      "kind": "git",
      "delivery": "push_pr",
      "branch": "hn/uploader-retry-x1",
      "base_branch": "main",
      "worktree_path": "/Users/handlename/src/github.com/org/repo-a/.claude/worktrees/uploader-retry-x1",
      "intended_state": "running",
      "observed_state": "idle",
      "observed_at": "2026-10-17T10:41:00+09:00",
      "continues": 1,
      "last_report": { "message_ref": "msg_01HXYZ", "status": "in_progress", "summary": "retry 実装中", "next_action": "テストを追加" }
    },
    {
      "id": "w-0007",
      "task_id": "T-0004",
      "session_id": null,
      "started_by": null,
      "handle": "t0004-w0007",
      "title": "notes tidy",
      "cwd": "/Users/handlename/notes",
      "kind": "non-git",
      "delivery": "files",
      "branch": null,
      "worktree_path": null,
      "intended_state": "pending",
      "observed_state": "none",
      "observed_at": null,
      "last_report": null
    }
  ],
  "archived": [
    { "id": "T-0002", "title": "Bump deps", "artifacts": [], "archived_at": "2026-10-17T08:30:00+09:00", "cancelled": true, "reason": "やっぱり不要" },
    { "id": "T-0001", "title": "Fix typo", "artifacts": [{ "kind": "pr", "label": "repo-a#40", "url": "https://github.com/org/repo-a/pull/40" }], "archived_at": "2026-10-17T08:00:00+09:00" }
  ]
}
```

The example is a snapshot for illustration: `w-0005` and `w-0006` are elided, so this exact document would not pass `commit` on its own.

## Fields

### supervisor

| Field | Meaning |
| :--- | :--- |
| `dir` | Absolute path of the supervisor directory (non-git). |
| `session_ids` | Every session that has acted as this supervisor. Add your own id during the Capability check. Used to match `started_by`. |
| `archive_consent` | `null`, or `{text, at}` holding the user's verbatim answer to the one-time archive question. Required before a worker that is neither observed `archived` nor `missing` may become `intended_state: archived`. |
| `dashboard` | `{url, path}`: the published artifact URL (`null` until first publish) and the local HTML path. |

### tasks[]

| Field | Meaning |
| :--- | :--- |
| `id`, `title` | Task id and short display title. |
| `request` | The user's request, copied **verbatim** (never summarized). |
| `push_consent` | `null`, or `{text, at}` where `text` is the exact phrase from `request` (or a later user message) that grants push / PR. |
| `status` | Derived by `commit`. Never write it yourself (see Derivation). |
| `next_action` | What the supervisor will do next for this task. |
| `user_actions[]` | `{id, description, since}`: things the user must do. A non-empty list makes the task `needs_user`. The supervisor removes each one in the candidate that records its resolution (SKILL.md section 5). |
| `artifacts[]` | Results recorded from values the supervisor observed (see Artifacts). |
| `worker_ids[]` | Workers that belong to this task. |
| `created_at` | ISO 8601 timestamp with offset. |
| `cancel` | Optional. `{at, reason}` once the user cancelled the task (see Cancellation). |

### workers[]

| Field | Meaning |
| :--- | :--- |
| `id`, `task_id` | Worker id and owning task. |
| `session_id`, `started_by` | Runtime session id and the supervisor session that started it (`null` while pending). |
| `handle`, `title` | Start-time name (`[a-z0-9-]`, 24 characters or fewer) and display title. |
| `cwd` | Repo root (git) or target directory (non-git), absolute. |
| `kind` | `git` or `non-git`. |
| `delivery` | `push_pr`, `commit`, or `files` (see Value ranges). |
| `branch`, `base_branch`, `worktree_path` | git only, filled after start. `branch` and `worktree_path` come from get; `base_branch` is get's `sourceBranch`, else `git symbolic-ref --short refs/remotes/origin/HEAD` (e.g. `origin/main`). |
| `intended_state` | What the supervisor wants. |
| `observed_state`, `observed_at` | What the supervisor last observed, and when. |
| `last_report` | Last processed report (see last_report). |
| `continues` | Optional integer >= 0: consecutive "continue" nudges sent for unchanged `in_progress` reports. Reset to 0 when the report's `summary` or `next_action` changes; at 3 the supervisor stops nudging and adds a user action. |

### archived[]

Stubs of finished tasks: `{id, title, artifacts, archived_at}`, plus `cancelled: true` and `reason` (copied from `cancel.reason`) for cancelled tasks; newest first, at most 20. The dashboard lists them with their artifact links and a cancelled badge. Task cards list their own artifacts.

## Value ranges

- `worker.delivery`:
  - `push_pr`: kind is `git` and the task has `push_consent`
  - `commit`: kind is `git` without consent
  - `files`: kind is `non-git`
- `worker.intended_state`: `pending` | `running` | `archived`
- `worker.observed_state`: `none` | `running` | `idle` | `needs_input` | `archived` | `missing`
- `task.artifacts[].kind`: `branch` | `commit` | `pr` | `ci` | `file`
  - `pr` / `ci`: `url` (http or https) is required
  - `file`: `path` (absolute) is required; `url` is forbidden
  - `branch` / `commit`: `ref` is required; `url` is optional once pushed
- `task.status`: `needs_user` | `running` | `waiting` | `done` (derived)

## last_report

`null`, or one of two shapes. `message_ref` is required in both: it is the identifier of the latest message the report was read from (the message UUID or cursor that the event-reading capability returns).

- Parsed: `{message_ref, status, summary, next_action}`, where `status` is the report block's value.
- Parse failure: `{message_ref, status: "unparsed", error, at}`.

Idempotency uses `message_ref`: if the latest message's id equals `last_report.message_ref`, the turn is already processed. Stall detection uses `continues` (see workers[]).

## Derivation (performed by commit)

`task.status` looks at **observed values only**. The first matching rule wins:

0. `done`: the task has `cancel` and no occupying worker (see Cancellation).
1. `needs_user`: the task's `user_actions` is not empty, or some worker is observed `archived` while its `intended_state` is not `archived` (archived outside the supervisor; cleared when the supervisor verifies the result and sets `intended_state: archived`).
2. `running`: some worker has `observed_state` in `running` / `idle` / `needs_input`, or is `missing` while its `intended_state` is not `archived`. A `missing` worker with `intended_state: archived` counts as finished.
3. `waiting`: only unstarted workers (`observed_state == none`) remain.
4. `done`: every worker is finished: observed `archived`, or `missing` with `intended_state: archived`.

Before deriving, `commit` maintains one automatic user action, the only one the supervisor never edits. When a non-git `cwd` is occupied in current and free in the candidate, every task with a `pending` / `none` non-git worker on that `cwd` gets `Waiting task T-xxxx can start now: send any message to the supervisor to start it.` (added once, matched by exact description). `commit` removes it from a task as soon as that task has no startable worker (non-git, `pending` / `none`, `cwd` free in the candidate): the worker was started or removed, or the `cwd` is busy again because another waiting task on it started. The supervisor never adds or removes this user action itself.

A task derived as `done` is moved, **in the same commit**, to an `archived` stub, and its workers are removed from `workers`. `archived` is truncated to the 20 newest stubs.

Consequences:

- A worker with `intended_state: archived` and `observed_state: idle` keeps its task in `running`; the task moves only after the archive is observed.
- A `commit`-delivery task carries the push-decision user action, so it stays `needs_user` after its workers are archived, until the user decides.

## Cancellation

`task.cancel = {at, reason}` (non-empty strings) records the user's request to stop the task. While it is set, no worker of the task may have `intended_state: running`. A worker of any kind is occupying while `intended_state == running`, or `intended_state == archived` and `observed_state` is neither `archived` nor `missing` (a vanished session cannot be archived, so it counts as finished). A cancelled task with no occupying worker derives `done` (rule 0 of Derivation, ahead of user actions), so `commit` moves it to an `archived` stub with `cancelled: true` and the reason, and drops its unstarted workers. A cancelled task with an occupying worker follows the normal rules.

A started worker left at `intended_state: pending` is not occupying, so cancelling would archive the task while that session is still alive. When cancelling, set `intended_state: archived` on every worker with a `session_id`. For a cancelled task the delivery artifact requirement and the push-decision requirement do not apply; `archive_consent` still does for workers that are neither observed `archived` nor `missing`. The automatic waiting-task user action is never added to a cancelled task.

## Read by the secretary

The secretary skill opens `<dir>/state.json` read-only. This section declares what it depends on.

### Shape

The source of truth is `supervisor_errors` in the secretary skill's `state_tool.py`; this list summarizes it. A state that fails it is not merged.

- `schema_version` equal to `1`, and `rev` an integer.
- `supervisor` an object with `session_ids` a list of strings (checked only) and `dashboard` an object or `null` (optional).
- `tasks` and `workers` lists (may be empty).
- Each task: `id`, plus `title` and `status` (checked only), all strings; `user_actions` optional, each entry with string `id`, `description`, and `since`.
- Each worker: string `kind`, `cwd`, `intended_state`, and `observed_state`.

### Meaning

- **User-action wording.** The secretary decides how the user answers a decision (in the app, or by pasting text into this supervisor's chat) from the wording of `user_actions[].description`. That wording comes from the User actions table and the quoted user actions in SKILL.md, the automatic waiting-task action, and free-text report blockers.
- **Identity.** `(task.id, user_actions[].id, since)` identifies a decision. Because user-action numbers are reused, `since` is the time the action was added and is never rewritten.
- **Occupancy.** The secretary copies the occupy rule of the non-git worker invariant (see Invariants) to decide whether a directory is busy before it relays to an external source.
- **Dashboard.** `supervisor.dashboard.url` is optional and used only when it is an http(s) URL.
- **Revision.** `rev` increases on every write; the secretary merges only when `rev` changed.

### Changing these

- Renaming a field, changing its type, value range, or meaning, changing user-action wording or the occupy rule, or bumping `schema_version` affects the secretary. Update, in the same change, the secretary skill's `state_tool.py` (`supervisor_errors`, `merge_supervisor`, `_occupies_cwd`, `SUPERVISOR_UA_RULES`) and the relay_mode classification table in the secretary skill's `references/state-schema.md`.
- After a `schema_version` bump the secretary keeps showing its last merged items, its overlap check fails, and new relay sessions to external sources are rejected until it is updated.
- Adding a field is compatible. A new user-action wording is still classified, so check it against the secretary's classification table.
- Run the secretary skill's tests as well; its contract test checks this state shape and the quoted user-action wording from the secretary's side.

## Invariants

`commit` rejects the candidate (exit 1, `error:` lines on stderr, nothing written) when any of these fails. Malformed shapes (non-object items, non-string ids, non-list `user_actions` / `artifacts` / `worker_ids`) are rejected the same way.

Single-state checks (`validate`):

- Ids are unique, every task has at least one entry in `worker_ids`, and `worker.task_id` and `task.worker_ids` agree in both directions.
- At most one non-git worker occupies each directory tree: two occupied `cwd`s may be neither equal nor ancestor / descendant of each other (after realpath normalization). A worker occupies its `cwd` while `intended_state == running`, and also while `intended_state == archived` but `observed_state` is neither `archived` nor `missing` (an archive was requested but not yet observed).
- `delivery == push_pr` requires a non-empty `push_consent.text` that is a substring of the task's `request`. This guarantees consistency between the two fields only; keeping `request` verbatim is the supervisor's rule.
- A task that is not cancelled and has a worker with `intended_state == archived` holds the artifacts its delivery requires:
  - `push_pr`: `branch` + `commit` + (`pr`, or a `branch` with `url`)
  - `commit`: `branch` + `commit`
  - `files`: at least one `file`
- `last_report`, when not null, has `message_ref`.
- A task with `cancel` has no worker with `intended_state: running`.
- `continues`, when present, is an integer >= 0.
- A `file` artifact's `path` lies under the `cwd` of a worker of that task.
- URLs are http or https only.

Checks against the current state (`next_state`):

- The candidate's `rev` equals the current `rev`. The written state gets `rev + 1`.
- No silent deletion: a task present in current but absent from the candidate is always rejected. Tasks leave `tasks` only through the move that `commit` itself performs for tasks derived as `done`, so never remove a task from the candidate. A worker with a non-null `session_id` may disappear only together with its task's move.
- Transition to archived: for each worker whose `intended_state` is not `archived` in current and is `archived` in the candidate:
  - if its delivery is `commit` and the task is not cancelled, the candidate task must contain the push-decision user action
  - `supervisor.archive_consent` must not be null, unless the worker is already observed `archived` or `missing` (no archive call follows)
- Removing the push-decision user action from a candidate whose workers are already `archived` (or whose archive was refused and the worker remains) is accepted: the transition checks apply only to workers that newly become archived.

## Report to artifacts

Record artifacts from values the supervisor observed, not from the report alone.

| Report field | task.artifacts entry |
| :--- | :--- |
| `branch` | `{kind: branch, ref: "<repo>#<branch from state>", url?: branch URL once pushed}` |
| `commit` | `{kind: commit, ref: <observed HEAD sha>}`. If it differs from the report, use the observed value and note it in the summary. |
| `pr` | `{kind: pr, url}`, only when the PR head sha equals the observed HEAD. |
| `ci` | `{kind: ci, url}` |
| `files[]` | One `{kind: file, path}` per entry that exists and lies under the worker's `cwd`. |
