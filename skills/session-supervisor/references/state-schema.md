# state.json Schema

`state.json` is the single source of truth for the supervisor. Only the supervisor session writes it, and only through `state_tool.py commit`, which validates the candidate, checks `rev`, derives `task.status`, moves finished tasks to `archived`, and writes atomically. Never edit `state.json` directly.

## Identifiers

Field names are agent-neutral:

- `session_id`: the session identifier issued by the runtime (the value the start capability returns).
- `handle`: the `[a-z0-9-]` name (24 characters or fewer) given to the session at start, e.g. `t0003-w0004`.
- `branch` / `worktree_path`: decided by the app (it adds a prefix and a suffix to the requested slug). Fill them from the get capability after start; never guess them.
- `task.id`: `T-NNNN`. `worker.id`: `w-NNNN`. `user_actions[].id`: `UA-NNNN`. Allocate the next number after the highest one in the current `tasks` / `workers` / `user_actions`; for task ids also count the `archived` stubs, which keep their task id (a revived task reuses its stub's id, see archived[]). Stubs keep no worker or user-action ids, so those numbers may be reused after a task is archived; uniqueness is checked only within the current state.

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
      "followups": [
        { "text": "repo-a の方は指数バックオフにして", "at": "2026-10-17T10:20:00+09:00" }
      ],
      "status": "running",
      "next_action": "w-0004 / w-0005 の完了待ち",
      "user_actions": [
        { "id": "UA-0002", "description": "repo-b の PR をレビュー", "since": "2026-10-17T10:30:00+09:00" },
        { "id": "UA-0003", "description": "Review repo-a#42", "since": "2026-10-17T10:45:00+09:00", "follow_up": { "rule": "review-pr", "url": "https://github.com/org/repo-a/pull/42", "done": "pr_closed" } }
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
    { "id": "T-0001", "title": "Fix typo", "request": "repo-a の README の typo を直して", "artifacts": [{ "kind": "pr", "label": "repo-a#40", "url": "https://github.com/org/repo-a/pull/40" }], "archived_at": "2026-10-17T08:00:00+09:00" }
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
| `request` | The user's request, copied **verbatim** (never summarized). Immutable: `commit` rejects a candidate that changes the `request` of a task already in the current state (`task T-xxxx: request is verbatim and must not change`). |
| `followups[]` | Optional. `{text, at}` entries (both non-empty strings), oldest first: later user requests that continue this task, each `text` copied **verbatim** (the whole message). Appended by the supervisor (SKILL.md 6.7); an existing `text` is never changed; an entry is removed only by a correction (SKILL.md 6.9). Carried into the `archived` stub when non-empty. |
| `push_consent` | `null`, or `{text, at, message?}` where `text` is the exact phrase that grants push / PR. Without `message`, `text` comes from `request`. `message` is a later user message verbatim (including a consent given while the worker was still running, applied once the push-decision user action exists) and holds `text`; it may be set or changed only in a candidate that resolves a pending push-decision user action of the task (enforced by `commit`, the only path that accepts it). Its content is an audit record. Each push decision covers only the branch its user action names; a later consent overwrites `message`. |
| `status` | Derived by `commit`. Never write it yourself (see Derivation). |
| `next_action` | What the supervisor will do next for this task. |
| `user_actions[]` | `{id, description, since, follow_up?}`: things the user must do. `description` is rendered on the dashboard as a markdown subset (paragraphs, `-` / `1.` lists, `` `code` ``, `**bold**`, `[text](https://…)`); other markup stays literal text. A non-empty list makes the task `needs_user`. The supervisor removes each one in the candidate that records its resolution (SKILL.md section 5). `follow_up`, when present, is `{rule, url, done}`: the action is remaining work for the user (e.g. reviewing a PR) created from a `## Follow-ups` rule by `state_tool.py follow-ups` ([follow-ups-format.md](follow-ups-format.md)). `rule` is the rule id, `url` the PR URL it is about, and `done` is `pr_closed` (also removed once the PR is merged or closed) or `manual` (removed only when the user says it is done). Never write it by hand. |
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
| `branch`, `base_branch`, `worktree_path` | git only, filled after start. `branch` and `worktree_path` come from get; `base_branch` is get's `sourceBranch`, else the remote default branch (`refs/remotes/origin/HEAD`, e.g. `origin/main`), observed by a subagent. A push-only worker (SKILL.md 6.3 step 7) keeps `branch` = the pushed branch instead of the app branch from get, and copies the original worker's `base_branch`; a replacement worker (`replaced_by`, SKILL.md 6.8) likewise inherits the old worker's `base_branch` and does not use get's `sourceBranch`; one started from an archived stub (or from the fallback when the stub expired or lacks a `commit` artifact) uses the observed remote default branch, never get's `sourceBranch` (the pushed branch). |
| `intended_state` | What the supervisor wants. |
| `observed_state`, `observed_at` | What the supervisor last observed, and when. |
| `last_report` | Last processed report (see last_report). |
| `replaced_by` | Optional worker id, or null. Marks this worker as replaced by another worker of the same task (SKILL.md 6.8): the id of a different, existing worker with the same `task_id`. Allowed only with `intended_state: archived`. Following `replaced_by` from worker to worker must end at a worker without `replaced_by` (no cycles). The replacement count of a `cwd` is the number of workers of the task with that `cwd` and a `replaced_by`. A worker with `replaced_by` is exempt from the delivery artifact and push-decision requirements (see Invariants) but not from `archive_consent`, and `derive_status` does not count it as verified-archived (see Derivation). |
| `continues` | Optional integer >= 0: consecutive "continue" nudges sent for unchanged `in_progress` reports. Reset to 0 when the report's `summary` or `next_action` changes; at 3 the supervisor stops nudging and adds a user action. |

Later push workers (SKILL.md 6.3 step 7):

- A task may gain a `push_pr` worker after its `commit` workers were archived.
- A reused worker's `delivery` may change from `commit` to `push_pr` together with `intended_state` from `archived` back to `running` (only workers that newly become archived are checked), in the candidate that sets `push_consent`.

### archived[]

Stubs of finished tasks: `{id, title, request, artifacts, archived_at}`, plus `followups` (copied from the task, only when non-empty), and `cancelled: true` and `reason` (copied from `cancel.reason`) for cancelled tasks; newest first, at most 20. `request` is always present on stubs written by `commit`; a stub without `request` is an older stub. The dashboard lists them with their artifact links and a cancelled badge. Task cards list their own artifacts.

Revival (SKILL.md 6.7 (c)):

- A user request that continues an archived task revives it as a task with the **same id** as its stub. `commit` accepts a task id that is also in `archived` only when the stub has a `request`, is not `cancelled`, and its `request` equals the task's `request`. Otherwise it rejects: `task T-xxxx: id is an archived stub; revive only a non-cancelled stub with the same request`.
- The stub stays in `archived` while the task is alive, so the same id shows both on a task card and in the archived list. The revived task starts with empty `artifacts` and `push_consent: null`; nothing is copied from the stub, so earlier deliveries are never re-reported or re-verified.
- When the revived task is archived again, `commit` replaces the old stub with a new one. The new stub's `artifacts` are the old stub's `artifacts` followed by the task's `artifacts`, order preserved and without deduplication, so the last `branch` artifact of a directory is always the newest generation.
- A stub that fell out of the 20-stub window while its task was revived has lost its artifacts: the new stub holds only the task's `artifacts`.
- A cancelled stub never revives: a continuing request starts a new task. A revived task that is cancelled can never be revived again.

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
4. `done`: every worker is finished: observed `archived`, or `missing` with `intended_state: archived`. A worker with `replaced_by` is not counted as verified-archived by the final fallback of this rule: a task whose only remaining workers are replaced external-archive workers and replacements that gave up as `missing` derives `needs_user`, not `done`.

Before deriving, `commit` maintains one automatic user action, the only one the supervisor never edits. When a non-git `cwd` is occupied in current and free in the candidate, every task with a `pending` / `none` non-git worker on that `cwd` gets `Waiting task T-xxxx can start now: send any message to the supervisor to start it.` (added once, matched by exact description). `commit` removes it from a task as soon as that task has no startable worker (non-git, `pending` / `none`, `cwd` free in the candidate): the worker was started or removed, or the `cwd` is busy again because another waiting task on it started. The supervisor never adds or removes this user action itself.

A task derived as `done` is moved, **in the same commit**, to an `archived` stub, and its workers are removed from `workers`. `archived` is truncated to the 20 newest stubs.

Consequences:

- A worker with `intended_state: archived` and `observed_state: idle` keeps its task in `running`; the task moves only after the archive is observed.
- A `commit`-delivery task carries the push-decision user action, so it stays `needs_user` after its workers are archived, until the user decides.
- A follow-up user action makes its task `needs_user` like any other, even before the archive is observed and even while another worker of the task is `running`.
- A task whose only remaining user actions are follow-ups stays in `tasks`, together with its archived workers, until the last follow-up is removed; then `commit` moves it to an `archived` stub.

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
- Each task: `id`, plus `title` and `status` (checked only), all strings; `user_actions` optional, each entry with string `id`, `description`, and `since`, and optionally `follow_up` with string `rule`, `url`, and `done`.
- Each worker: string `kind`, `cwd`, `intended_state`, and `observed_state`.

### Meaning

- **User-action wording.** The secretary decides how the user answers a decision (in the app, or by pasting text into this supervisor's chat) from the wording of `user_actions[].description`. That wording comes from the User actions table and the quoted user actions in SKILL.md, the automatic waiting-task action, and free-text report blockers.
- **Follow-ups.** A user action with `follow_up` is remaining work, not a decision: the secretary lists it under "Your tasks" with a link to `follow_up.url`, and its paste text reports completion, e.g. `T-0003 UA-0003 ("Review repo-a#42"): done`. Every user action without `follow_up` is a pending decision.
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
- `delivery == push_pr` requires a non-empty `push_consent.text` that is a substring of `push_consent.message` when `message` is present, else of the task's `request`. `message`, when present, is a non-empty string. This guarantees consistency between the fields only; keeping `request` and `message` verbatim is the supervisor's rule.
- A task that is not cancelled and has a worker with `intended_state == archived` and without `replaced_by` holds the artifacts its delivery requires:
  - `push_pr`: `branch` + `commit` + (`pr`, or a `branch` with `url`)
  - `commit`: `branch` + `commit`
  - `files`: at least one `file`
- `replaced_by`, when present, names a different existing worker of the same task; its worker has `intended_state: archived`; following the chain ends at a worker without `replaced_by`.
- `followups[]`, when present, is a list of objects with non-empty string `text` and `at`; a stub's `request` and `followups` have the same shapes.
- A task id that is also in `archived` is a revival: the stub has a `request`, is not `cancelled`, and its `request` equals the task's `request`.
- `last_report`, when not null, has `message_ref`.
- A task with `cancel` has no worker with `intended_state: running`.
- `continues`, when present, is an integer >= 0.
- A `file` artifact's `path` lies under the `cwd` of a worker of that task.
- URLs are http or https only.
- A user action's `follow_up`, when present, is an object whose `rule` matches `^[a-z0-9][a-z0-9-]{0,31}$`, whose `done` is `pr_closed` or `manual`, and whose `url` is http(s).
- That `url` equals the `url` of one of the task's `pr` artifacts. A candidate that replaces or removes a `pr` artifact must remove the follow-ups of the old url in the same candidate.
- Within one task, no two follow-ups share the same `(rule, url)`.
- A user action with `follow_up` has a non-empty string `since`.
- None of these checks reads `CLAUDE.md`: `commit` does not know the rules, only the shape.

Checks against the current state (`next_state`):

- The candidate's `rev` equals the current `rev`. The written state gets `rev + 1`.
- No silent deletion: a task present in current but absent from the candidate is always rejected. Tasks leave `tasks` only through the move that `commit` itself performs for tasks derived as `done`, so never remove a task from the candidate. A worker with a non-null `session_id` may disappear only together with its task's move.
- `request` of a task present in both current and candidate is unchanged. Existing `followups[].text` values are not constrained by `commit`; keeping them verbatim is the supervisor's rule.
- Transition to archived: for each worker whose `intended_state` is not `archived` in current and is `archived` in the candidate:
  - if its delivery is `commit`, the task is not cancelled, and the worker has no `replaced_by`, the candidate task must contain the push-decision user action
  - `supervisor.archive_consent` must not be null, unless the worker is already observed `archived` or `missing` (no archive call follows); a worker with `replaced_by` is not exempt from this
- A worker already `intended_state: archived` without `replaced_by` in current (its result was verified and recorded, or its task was cancelled) must not get `replaced_by` in the candidate.
- The automatic waiting-task user action is never offered for a worker that is the `replaced_by` target of another worker, so a replacement waits for the supervisor's next user turn instead of prompting the user.
- Removing the push-decision user action from a candidate whose workers are already `archived` (or whose archive was refused and the worker remains) is accepted: the transition checks apply only to workers that newly become archived.
- Push consent from a later message: for each candidate task whose `push_consent.message` is new or differs from the current task's, the task must exist in current, current must hold a push-decision user action for it (naming the branch of one of its git workers; a user action with `follow_up` is never a push decision), and the candidate must no longer hold that user action. Otherwise: `task T-xxxx: push_consent.message may be set only while resolving a pending push decision`.

## Report to artifacts

Record artifacts from values the supervisor observed, not from the report alone.

| Report field | task.artifacts entry |
| :--- | :--- |
| `branch` | `{kind: branch, ref: "<cwd>#<branch from state>", url?: branch URL once pushed}`, where `<cwd>` is the worker's repo root as an absolute path (the form of the Example). SKILL.md 6.7 finds the source branch of a follow-up by this `<cwd>#` prefix. |
| `commit` | `{kind: commit, ref: <observed HEAD sha>}`. If it differs from the report, use the observed value and note it in the summary. |
| `pr` | `{kind: pr, url}`, only when the PR head sha equals the observed HEAD. |
| `ci` | `{kind: ci, url}` |
| `files[]` | One `{kind: file, path}` per entry that exists and lies under the worker's `cwd`. |
