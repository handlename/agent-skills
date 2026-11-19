# state.json Schema

`state.json` holds the supervisor's runtime data (`schema_version: 2`). The meaning of each task and project (title, request, status, assignee, user actions, artifacts) lives in its Obsidian note, joined by `uid` (see Storage form). Only the supervisor session writes either, and only through `state_tool.py commit`, which validates the candidate, checks `rev`, derives `task.status` and the note props, moves finished tasks to `archived`, and writes atomically. Never edit `state.json` or a vault note directly.

Most of this document describes the **v1 domain view**: the v1-shaped object `join_view(state, notes)` builds, on which validation, derivation, and digest run unchanged. Fields marked "from the note" in Storage form are not stored in `state.json`.

## Identifiers

Field names are agent-neutral:

- `runtime`: `"herdr"` on workers and managers started in Herdr (SKILL.md 6.2). Any other value is an error. Without it, the session was started in the Claude desktop app by an earlier version (SKILL.md section 7).
- `session_id`: for `runtime: "herdr"`, the Claude Code conversation uuid that the supervisor chooses (a lowercase uuid4, set and committed before the start; `launch-plan` passes it as `claude --session-id`), or `null` before it is set. It locates the session's transcript for its latest message. For a desktop-app session, the id the app returned.
- `handle`: for `runtime: "herdr"`, the `hc` terminal name and equal to the id: `w-NNNN` for a worker, `p-NNNN` for the manager of `P-NNNN` (lowercase, since `hc` names allow only `a-z 0-9 - _`; a manager started before 0.66.0 keeps `m-p-NNNN` until its next replacement). `validate` checks it. For a desktop-app session, the start-time name, e.g. `t0003-w0004`.
- `branch` / `worktree_path`: printed by `state_tool.py prepare-worktree` (SKILL.md 6.2 step 2); copy them from its output, never guess them.
- `task.id`: `T-NNNN`. `worker.id`: `w-NNNN`. `user_actions[].id`: `UA-NNNN` (kinds `ask` and `auto`). `agent_actions[].id`: `AA-NNNN` (kind `req`, the user's requests to the agent). `UA-` and `AA-` share one number sequence, so `UA-0241` and `AA-0241` never both exist, and `commit` rejects a prefix that does not match the kind; "user-action numbers" below covers both. Allocate the next number after the highest one in the current `tasks` / `workers` / `user_actions`; for task ids also count the `archived` stubs, which keep their task id (a revived task reuses its stub's id, see archived[]). Stubs keep no worker or user-action ids, so those numbers may be reused after a task is archived; uniqueness is checked only within the current state. User-action numbers are allocated across the user actions of `tasks` and `projects` together, so a task and a project never share one, and never reused: the next number is above both the highest open one and `supervisor.last_ua_number` (which covers user actions already acked in the notes).
- `project.id`: `P-NNNN`, allocated as `supervisor.last_project_number + 1`; the counter is raised in the same candidate and never reused, even after the project is removed.
- `review.id`: `R-NNNN`, allocated per project as `projects[].last_review_number + 1`; the counter is raised in the same candidate, and a review id is unique within its project (two projects may both have `R-0001`).

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
    "last_project_number": 1
  },
  "projects": [
    {
      "id": "P-0001",
      "name": "Uploader reliability",
      "context_doc_url": "https://github.com/org/repo-a/issues/30",
      "dir": "/Users/handlename/supervisor-work/P-0001-uploader-reliability",
      "created_at": "2026-10-17T08:55:00+09:00",
      "last_review_number": 1,
      "manager": {
        "session_id": "sess_mgr01",
        "started_by": "sess_sup_01",
        "handle": "p0001-manager",
        "title": "P-0001 Uploader reliability",
        "intended_state": "running",
        "observed_state": "idle",
        "observed_at": "2026-10-17T10:41:00+09:00",
        "model": "opus",
        "last_report": { "message_ref": "msg_01HMGR", "status": "idle", "summary": "R-0001 を差し戻し", "at": "2026-10-17T10:15:00+09:00" },
        "previous_session_ids": [],
        "replacements": 0
      },
      "user_actions": [
        { "id": "UA-0004", "description": "Approve new task proposal of P-0001 (`Uploader reliability`): Add upload metrics", "since": "2026-10-17T10:15:00+09:00", "proposal": { "title": "Add upload metrics", "text": "uploader の成功・失敗数をメトリクスとして出す", "message_ref": "msg_01HMGR" } }
      ]
    }
  ],
  "tasks": [
    {
      "id": "T-0003",
      "title": "Add retry to uploader",
      "request": "repo-a と repo-b の uploader にリトライを追加して。push して PR を作ってよい",
      "push_consent": { "text": "push して PR を作ってよい", "at": "2026-10-17T09:00:00+09:00" },
      "project_id": "P-0001",
      "redos": [
        { "text": "タイムアウトも再試行の対象にする", "at": "2026-10-17T10:15:00+09:00", "from": "P-0001", "review_id": "R-0001" }
      ],
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
      "model": "sonnet",
      "continues": 1,
      "last_report": { "message_ref": "msg_01HXYZ", "status": "in_progress", "summary": "retry 実装中", "next_action": "テストを追加" },
      "review": { "id": "R-0001", "state": "rejected", "at": "2026-10-17T10:15:00+09:00", "message_ref": "msg_01HXW1", "ref": "2b7e01d4c9" }
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

The example is a snapshot of the v1 view for illustration: `w-0005` and `w-0006` are elided, so this exact document would not pass `commit` on its own.

## Storage form (schema_version 2)

`state.json` keeps only runtime data. `join_view(state_v2, notes)` adds the note data back as the v1 view, and `split_view(view_candidate, view_new, ops)` turns the view `next_state` returned into the storage form again, together with note ops (`ua_add` / `ua_ack`) for the user actions `commit` itself added or removed.

| Object | From the note (not stored) | Added runtime fields |
| :--- | :--- | :--- |
| `tasks[]` | `title` (note `title`), `request` (the `> [!quote]- 依頼の原文` callout of `## 概要` when present, otherwise `## 概要`), `status` (derived), `user_actions` (open `@user` items of `## アクション`: kinds `ask` and `auto`), `agent_actions` (open numbered `@agent` items: kind `req`), `followups` (always empty; follow-up requests are `req` items), `artifacts` (`## 成果物` rows numbered `art_from` or later), `subtasks` (the numbered lines of `## サブタスク`, see Subtasks) | `uid`, `note` (path relative to the vault), `note_seen`, `user_hold`, `confirmed`, `held`, `kept`, `last_artifact_number`, `art_from`, `artifacts_quarantine` |
| `projects[]` | `name` (note `title`), `context_doc_url` (first URL of `## 概要` / `## 関連資料`), `user_actions` | `uid`, `note`, `note_seen`, `user_hold`, `done_proposed`, `confirmed` |
| `archived[]` | `title`, `request`, `followups`, `artifacts` (all rows, or the rows before the `art_from` of a revived task with the same id) | `uid`, `note_seen`, `last_artifact_number`; `project` is stored as `{id}` |
| `supervisor` | — | `last_ua_number`, `note_warnings: {uid: sha}`, `note_journal: {rev, entries}` |

Runtime fields:

| Field | Meaning |
| :--- | :--- |
| `uid` | The note's `uid` property. One live task or project per uid; a revived task and its stub share it. |
| `note_seen` | `{status, assignee, ua_sha, art_sha}`: the values `state_tool` last wrote or the supervisor last handled. A note whose values differ was changed by the user (see User changes). |
| `user_hold` | `{at, status, assignee, reason}` with `reason` `pause` or `invalid`: the user paused the item, or set values outside the transition table. Props are not written and pending workers / managers are not started while it is set. |
| `confirmed` | `{at}`: the user marked the item done/user. |
| `held` | Worker ids of held investigation workers. Each stands for a `Read the result of w-NNNN (held)` user action in the view, so the read-action invariants below apply; a worker whose read action is gone from the view leaves `held` in `split_view`. |
| `kept` | Worker ids of kept workers (SKILL.md 6.3 step 6): live sessions whose latest `done` is verified and recorded, kept open for follow-ups until the user confirms the task (or it is cancelled). A kept worker counts as finished in Derivation and does not count toward `max_workers`. See Invariants. |
| `last_artifact_number` | Integer >= 0: the highest `## 成果物` row number `n` ever used (the view raises it to the highest `n` in the note). |
| `art_from` | Integer >= 1, required on a revived task: its generation's first row number. |
| `artifacts_quarantine` | `{at, ua}`: the user removed or broke rows something depends on (see Artifact quarantine). Pending workers are not started while it is set. |
| `done_proposed` | `{at, summary}` (project): the manager proposed that the project is done. |
| `note_journal` | `{rev, entries}`: note ops written with the state and not yet confirmed in the notes. Each entry keeps the `rev` of the commit that made its ops, also when it is carried over after a failed write, and is replayed and checked under that rev. |

In the view, an open user action also carries `detector` (for `auto=<detector>` items) and `needs_answer: true` (for `ask answer` items).

`commit --notes <ops.json>` (ops `{uid: [op, ...]}`) re-applies the previous commit's unconfirmed note writes once, checks that every ack copied into `note_seen` still matches its note, fills missing action ids (`UA-` or `AA-` by kind, `ua_adopt` included), subtask ids (`ST-`, for `st_add` and `st_adopt`) and artifact numbers, applies the ops in memory, runs `next_state` on the views, derives note props (for the tasks archived in this commit from the candidate's task), writes `state.json` with `note_journal`, and only then writes the notes. Its output adds `notes_written`, `notes_deferred`, `notes_failed`, and `note_problems`. A `state.json` still at `schema_version: 1` keeps the v1 commit until it is migrated.

The vault is `vault` of `## Locations` in `<dir>/CLAUDE.md` (default `<dir>/vault`); its `dashboard` note and `templates` folder are never read as notes. A task note is tagged `supervised`, a project note `supervised`, `project`, `task` (`note_kind`).

A task note's `parent` prop links its project's note (`[[uid|<title>]]` as the `create` op writes it since 0.70.0, `[[uid]]` before, `[[<path>|<title>]]` as `sv-project.md` writes it, a path, or a list of those). `notes-scan` resolves it for an unmanaged note's event as `project_id` (`parent_project_id`: the open project whose `uid` or note file name the link names, else null). The event also carries `lang` (`note_lang` of its `## 概要`). `proposed_by` (a P-id) is written only on a proposal note, by the `create` op of SKILL.md 6.11 `proposal_note`, and comes with the event as `proposed_by`; a note without it is the user's own task, also when it links a project.

Subcommands that read the vault: `notes-scan --state` (the user's changes as events, 3.4 (2); writes nothing), `note-show --state --uid` (props, sections, `request`, `lang` (`note_lang` of `request`: `ja` when it contains Japanese, otherwise `en`; the language the supervisor writes the note's actions in), user actions with their `body`, `thread` (`[{from: user|agent, text, at}]`, the `#### やりとり` messages), and `answer` (the user's text after the supervisor's last message; SKILL.md section 5), `requests` (every open `req` item, unnumbered ones included), artifacts), `new-note --state --kind task|project` (reserves `{uid, path}`; writes nothing), `migrate-v2 --state [--dry-run]` (v1 → notes and a v2 state), `export-v1 --state --out` (the v1 form for rollback), and `retag-notes --state [--dry-run]` (gives the task and project notes of `<dir>/vault` the tags above; tags only). `check-template --kind --file` checks a note template (SKILL.md section 3, Vault interview) against the notes the `create` op writes: the tags, `status: backlog` / `assignee: user`, the `summary` prop, `## 概要`, a project's `## タスク` kanban byte for byte, and the order of the sections; `setup` runs the same check on the notes tagged `supervised` in the `templates` folder and reports each one it rejects as `outdated`. `digest`, `check-cwd`, `serial-check`, `plan-actions`, `roster`, `waits`, and `manager-reconcile` read a v2 state through `join_view`. A task's `proposal` may be stored as `{from, message_ref}` (a proposal note: `message_ref` is `note:<uid>`); the view fills `title` and `text` from the note. `push_consent.source` is `chat` or `note` (SKILL.md 6.3 step 7).

`_storage_errors(state_v2, notes)` checks what the view cannot see: `schema_version` is 2; no key that belongs to the note is stored; every task, project, and stub has a `uid`, and every live task and project's note exists (a stub's note may be gone); no two live tasks or projects share a uid; `held` lists workers of its task; a revived task has `art_from`; and the shapes of the runtime fields above.

## Fields

### supervisor

| Field | Meaning |
| :--- | :--- |
| `dir` | Absolute path of the supervisor directory (non-git). |
| `session_ids` | Desktop-app sessions that acted as this supervisor in earlier versions. Kept for the digest; a supervisor on Herdr adds nothing (it is the terminal `sv`). |
| `archive_consent` | `null`, or `{text, at}` holding the user's verbatim answer to the one-time archive question. Required before a worker that is neither observed `archived` nor `missing` may become `intended_state: archived`. |
| `last_project_number` | Optional integer >= 0 (absent counts as 0): the highest project number ever allocated. Never decreases. |

### Settings in `<dir>/CLAUDE.md`

Not part of `state.json`; `setup` writes the missing keys and `prepare-dir --show` prints the roots.

| Section | Key | Meaning |
| :--- | :--- | :--- |
| `## Locations` | `clone_root`, `work_root` | Where `prepare-dir` clones repositories and creates work and project directories (SKILL.md section 3). |
| `## Locations` | `worktree_root` | Default `~/.claude-worktrees`. Where `prepare-worktree` adds worker worktrees, `<worktree_root>/<repo name>/<slug>`; `remove-worktree` removes only paths under it. Not nested with the other roots, not inside `<dir>`. |
| `## Herdr` | `max_workers` | Positive integer, default 8. `launch-plan` refuses a worker when this many other herdr workers are intended `running` and not observed `archived` or `missing`. Managers and kept workers do not count. |
| `## Herdr` | `worker_path` | Optional non-empty string. The PATH for workers and managers (`env PATH=<worker_path> claude ...` in the `launch-plan` argv). |

### projects[]

Optional (absent counts as an empty list). A project groups tasks under one project manager session (SKILL.md 6.10). The manager is recorded here, never in `workers`.

| Field | Meaning |
| :--- | :--- |
| `id`, `name` | `P-NNNN` (at most `supervisor.last_project_number`) and a non-empty display name. |
| `context_doc_url` | http(s) URL of the context document (a GitHub issue or a Notion page). The manager only reads it. |
| `dir` | Absolute path of the project directory `<work_root>/P-NNNN-<slug>`, created by `prepare-dir --project` with the manager guard hook. No non-git worker's `cwd` may overlap it. |
| `created_at` | ISO 8601 timestamp with offset. |
| `last_review_number` | Integer >= 0: the highest review number allocated in this project. Never decreases. |
| `manager` | The project manager session (below). |
| `user_actions[]` | Same shape as a task's user actions, plus an optional `proposal: {title, text, message_ref}` (non-empty strings) on the action that asks the user to approve a new task proposed by the manager. `proposal` is not allowed on a task's user actions. A project's user actions do not change any task's status. |
| `close` | Optional `{at, reason}` once the user closed the project in the supervisor chat; then `manager.intended_state` is `archived`. A closed project whose manager is finished and that no task references is removed by `commit`. |

`manager`:

| Field | Meaning |
| :--- | :--- |
| `runtime`, `session_id`, `started_by`, `handle`, `title` | As for workers. `handle` is `p-NNNN` (`m-p-NNNN` for a herdr manager started before 0.66.0, `pNNNN-manager` for a desktop-app manager of an earlier version), `title` is `P-NNNN <name>`. |
| `intended_state`, `observed_state`, `observed_at` | Same ranges as for workers. The manager becomes `intended_state: archived` only when the project has `close` or the manager is observed `archived` or `missing`. |
| `model` | Optional, as for workers. |
| `last_report` | `null`, `{message_ref, status, summary, at}` with `status` `idle` or `needs_input`, or `{message_ref, status: "unparsed", error, at}`. `message_ref` makes the manager's turn-end handling idempotent (SKILL.md 6.11). |
| `previous_session_ids[]` | Session ids (conversation uuids) of earlier managers of this project. Their turns and reports are ignored. A replacement sets the handle `p-NNNN` (it was `m-p-NNNN` before 0.66.0) and otherwise reuses it. |
| `replacements` | Integer >= 0: how many times the manager was replaced (SKILL.md 6.12). |

### tasks[]

| Field | Meaning |
| :--- | :--- |
| `id`, `title` | Task id and short display title. |
| `request` | The user's request. A note the agent creates gets a formatted `## 概要`, followed by the chat message copied **verbatim** (never summarized) as the callout `> [!quote]- 依頼の原文` (`create`'s `original`); `request` is that callout's content, or the whole `## 概要` when there is no callout (a picked-up note, a proposal note). The user may edit the note later, so `commit` does not compare it across commits; it is read only at pickup and when a brief is written. |
| `followups[]` | Optional. `{text, at}` entries (both non-empty strings), oldest first: the v1 record of later user requests. The schema 2 view always leaves it empty: a follow-up request is a `req` item of `## アクション` (SKILL.md 6.7), listed in `agent_actions`. |
| `push_consent` | `null`, or `{text, at, message?}` where `text` is the exact phrase that grants push / PR. Without `message`, `text` comes from `request`, checked when `push_consent` is set or changed. `message` is a later user message verbatim (including a consent given while the worker was still running, applied once the push-decision user action exists) and holds `text`; it may be set or changed only in a candidate that resolves a pending push-decision user action of the task (enforced by `commit`, the only path that accepts it). Its content is an audit record. Each push decision covers only the branch its user action names; a later consent overwrites `message`. |
| `status` | Derived by `commit`. Never write it yourself (see Derivation). |
| `next_action` | What the supervisor will do next for this task. |
| `user_actions[]` | `{id, description, since, follow_up?}`: things the user must do. `description` is markdown, the short heading of the note's `### [UA-NNNN] @user` item (`[text](https://…)` and bare `https://…` URLs become links in Obsidian); details and links go in the item's body (SKILL.md section 5). A non-empty list makes the task `needs_user`, except that a follow-up or `auto=pr_closed` action does not while a worker of the task is `running` (Derivation rule 1). The supervisor removes each one in the candidate that records its resolution (SKILL.md section 5). `follow_up`, when present, is `{rule, url, done}`: the action is remaining work for the user (e.g. reviewing a PR) created from a `## Follow-ups` rule by `state_tool.py follow-ups` ([follow-ups-format.md](follow-ups-format.md)). `rule` is the rule id, `url` the PR URL it is about (the digest appends it when the description does not contain it), and `done` is `pr_closed` (also removed once the PR is merged or closed) or `manual` (removed only when the user says it is done). Never write it by hand. |
| `artifacts[]` | Results recorded from values the supervisor observed (see Artifacts). |
| `agent_actions[]` | View only. `{id, description, since}` of the open numbered `req` items (`### [AA-NNNN] @agent`): the user's requests to the agent (SKILL.md section 5). They never make the task `needs_user`; `split_view` acks them (`ua_ack`) when the task is archived. Their threads (`#### やりとり`, SKILL.md section 5) are not in the view; `note-show` and `notes-scan` print each item's `thread` and, as `answer`, the user's follow-up written after the agent's last reply. Never write them by hand. |
| `worker_ids[]` | Workers that belong to this task. |
| `created_at` | ISO 8601 timestamp with offset. |
| `cancel` | Optional. `{at, reason}` once the user cancelled the task (see Cancellation). |
| `project_id` | Optional. The id of an open project (no `close`) this task belongs to. Without it the task behaves as before projects existed. |
| `proposal` | Optional. `{title, text, from, message_ref}` (non-empty strings) when the task was created from a manager's proposal the user approved (SKILL.md 6.1); `from` equals `project_id`. `request` holds the user's approval verbatim. Carried into the `archived` stub. |
| `redos[]` | Optional, project tasks only. `{text, at, from, review_id?}` entries, oldest first: redo work returned by the project manager (a `reject` verdict or a `redo` plan, SKILL.md 6.11). They are the manager's words, not the user's, so they are kept apart from `followups`. Carried into the `archived` stub when non-empty. |
| `no_result[]` | Optional. `{worker_id, summary, at}` entries (all non-empty strings), one per worker of the task whose `done` was verified as No result (SKILL.md 6.3 step 5): `summary` is that report's `summary` copied as is, `at` the time the supervisor recorded it. The named worker is exempt from the delivery artifact and push-decision requirements (see Invariants). It may also name a held investigation worker (one with a read user action) or a kept worker, recorded at verification before the archive. Carried into the `archived` stub when non-empty; task cards do not show it. |
| `investigation` | Optional, `true` when present. Set at intake (SKILL.md 6.1) when the request only asks for investigation; never changed while the task lives. Its workers (except push-only workers) are held, not kept, after a verified `done` until the user has read the result (SKILL.md 6.3 step 6). Copied into the `archived` stub and back on revival. |
| `findings[]` | Optional, investigation tasks only. `{worker_id, session_id, title, summary, message_ref, at}` entries (all non-empty strings), at most one per worker, replaced on every verified `done` of that worker (SKILL.md 6.3 step 6): `session_id` is the worker's session id, `title` the name used in replies when recorded (`w-NNNN (T-NNNN <workers[].title>)`, SKILL.md 6.3 step 6), `summary` the verified report's `summary` (`No summary reported; read the session.` when empty), `message_ref` that report's `message_ref`. Concatenated into the `archived` stub like `no_result`. |
| `after` | Optional. The T-id of the task right before this one in a serial queue (SKILL.md 6.15). A worker of the task starts only once that task has finished its work (`serial-check`, which `launch-plan` also runs), from its branch in the same repository. Copied into the `archived` stub, so a cancelled task that is skipped still names the one before it. |
| `derived_from` | Optional. `{task_id, context}` (non-empty strings) on a task created from a change request to an investigation task (SKILL.md 6.7): `task_id` is the investigation task, `context` its `request` and each `findings` `summary` with its `title`, copied when the task is created. Worker briefs are rebuilt from it alone, so `task_id` is not checked against `tasks` or `archived`. Not copied into the `archived` stub. |

### workers[]

| Field | Meaning |
| :--- | :--- |
| `id`, `task_id` | Worker id and owning task. |
| `runtime` | Optional, `"herdr"` (see Identifiers). |
| `session_id`, `started_by` | Session id (see Identifiers) and the starter (`"sv"` on Herdr; a supervisor session id for a desktop-app session). `null` while not set. |
| `handle`, `title` | The `hc` terminal name (equal to the id on Herdr, see Identifiers) and display title (52 characters or fewer, always set; passed as the Claude session name `-n`, SKILL.md 6.2.2). |
| `cwd` | Repo root (git) or target directory (non-git), absolute. A git worker runs in its `worktree_path`. |
| `kind` | `git` or `non-git`. |
| `delivery` | `push_pr`, `commit`, or `files` (see Value ranges). |
| `branch`, `base_branch`, `worktree_path` | git only, set before the start (SKILL.md 6.2 step 3). `branch` and `worktree_path` come from `prepare-worktree`; `base_branch` is the `--base` passed to it (`source_branch`, else the remote default branch `refs/remotes/origin/HEAD`, e.g. `origin/main`, observed by a subagent). A push-only worker (SKILL.md 6.3 step 7) keeps `branch` = the pushed branch instead of the new branch of its worktree, and copies the original worker's `base_branch`; a replacement worker (`replaced_by`, SKILL.md 6.8) likewise inherits the old worker's `base_branch`; one started from an archived stub (or from the fallback when the stub expired or lacks a `commit` artifact) uses the observed remote default branch, never the `--base` (the pushed branch). |
| `model` | Optional: the model passed to `claude --model` (SKILL.md 6.2.1): `opus`, `sonnet`, `haiku`, or a model id the user gave. Set before the start. |
| `interactive` | Optional, `true` when present: the worker asks the user (in `blockers`, or in its session when that is really needed) before working (SKILL.md 6.1 step 5, 6.7). Set when the worker is added, or on a running worker that gets the Interactive message; a replacement copies it (SKILL.md 6.8). It only selects the brief; `commit` derives nothing from it. |
| `intended_state` | What the supervisor wants. |
| `observed_state`, `observed_at` | What the supervisor last observed, and when. |
| `last_report` | Last processed report (see last_report). |
| `replaced_by` | Optional worker id, or null. Marks this worker as replaced by another worker of the same task (SKILL.md 6.8): the id of a different, existing worker with the same `task_id`. Allowed only with `intended_state: archived`. Following `replaced_by` from worker to worker must end at a worker without `replaced_by` (no cycles). The replacement count of a `cwd` is the number of workers of the task with that `cwd` and a `replaced_by`. A worker with `replaced_by` is exempt from the delivery artifact and push-decision requirements (see Invariants) but not from `archive_consent`, and `derive_status` does not count it as verified-archived (see Derivation). |
| `review` | Optional, workers of project tasks only. `{id, state, at, message_ref?, ref?, reason?, stale_notified?, pending_verdict?}` (see Reviews). |
| `awaiting_since` | Optional string, required exactly while `last_report.status` is `awaiting_manager`: when the worker started waiting for the project manager. Kept while the worker reports `awaiting_manager` again with the same `blockers`. |
| `continues` | Optional integer >= 0: consecutive nudges sent to the worker: "continue" for unchanged `in_progress` reports, and the Verification failed message for a `done` that failed verification (SKILL.md 6.3 step 5). Reset to 0 when an `in_progress` report's `summary` or `next_action` changes, and by a `done` that verifies; at 3 the supervisor stops nudging and adds a user action. |

Later push workers (SKILL.md 6.3 step 7):

- A task may gain a `push_pr` worker after its `commit` workers were archived.
- A reused worker's `delivery` may change from `commit` to `push_pr` together with `intended_state` from `archived` back to `running` (only workers that newly become archived are checked), in the candidate that sets `push_consent`.

### Reviews

A worker of a project task gets a `review` once its `done` is verified (SKILL.md 6.3 step 5). The review ties the manager's verdict to the report it was given for.

| `review.state` | Meaning |
| :--- | :--- |
| `requested` | Sent to the manager; awaiting its verdict. |
| `superseded` | The worker reported something other than `done` after the request. The next `done` gets a new review id. |
| `rejected` | The manager asked for a redo (`task.redos`). |
| `approved` | The manager approved; the worker may be archived. |
| `waived` | No review needed: a push-only worker (`reason` `push-only after R-NNNN`) or the user said so in the supervisor chat (`reason` = the user's words). A held investigation worker archived outside the supervisor or missing after reporting past its approval moves from `approved` to `waived` with the same id (`reason` `session closed while holding R-NNNN; later turns were not reviewed`, SKILL.md 6.4). |

- `id` and `at` are required in every state. `message_ref` is required for `requested`, `superseded`, `rejected`, and `approved`: the `last_report.message_ref` the review was requested for, or `external:<observed_at>` for a worker archived outside the supervisor with `last_report` null. `reason` is required for `waived`.
- `ref`: the verified head sha (git) or the reported files (non-git) the review covers.
- `stale_notified`: the `message_ref` for which a Stale verdict was already sent, so it is sent once per `(id, message_ref)`.
- `pending_verdict`: `{verdict: "approve", message_ref, at}`, an approval held until the next user turn because the result had to be observed again (SKILL.md 6.3 step 6). Allowed only while `state` is `requested`, with `message_ref` equal to `review.message_ref`.

### archived[]

Stubs of finished tasks: `{id, title, request, artifacts, archived_at}`, plus `followups` (copied from the task, only when non-empty), `after` (when the task had it), `no_result` and `findings` (only when non-empty), `investigation` (when the task had it), `project: {id, name}`, `proposal`, and `redos` (only when the task had them), and `cancelled: true` and `reason` (copied from `cancel.reason`) for cancelled tasks; newest first, at most 20. `request` is always present on stubs written by `commit`; a stub without `request` is an older stub. A stub's `no_result[].worker_id` and `findings[]` `worker_id`, `session_id`, and `message_ref` are records only: worker numbers are reused, so they are not checked against `workers` (only the shape of stub `findings` is checked).

Revival (SKILL.md 6.7 (c)):

- A user request that continues an archived task revives it as a task with the **same id** as its stub. `commit` accepts a task id that is also in `archived` only when the stub is not `cancelled` and its `uid` equals the task's `uid` (the same note). Otherwise it rejects: `task T-xxxx: id is an archived stub; revive only a non-cancelled stub with the same uid`. The revived task gets `art_from = last_artifact_number + 1`, so its view starts with no artifacts and the stub's view keeps the earlier rows.
- The stub stays in `archived` while the task is alive, so the same id is both in `tasks` and in `archived`. The revived task starts with empty `artifacts` and `push_consent: null`; nothing is copied from the stub, so earlier deliveries are never re-reported or re-verified.
- When the revived task is archived again, `commit` replaces the old stub with a new one. The new stub's `artifacts` are the old stub's `artifacts` followed by the task's `artifacts`, order preserved and without deduplication, so the last `branch` artifact of a directory is always the newest generation. `no_result` and `findings` are concatenated the same way; the revived task starts with none of them. The revived task copies `investigation` from the stub.
- A stub that fell out of the 20-stub window while its task was revived has lost its artifacts: the new stub holds only the task's `artifacts`.
- A cancelled stub never revives: a continuing request starts a new task. A revived task that is cancelled can never be revived again.

## Value ranges

- `worker.delivery`:
  - `push_pr`: kind is `git` and the task has `push_consent`
  - `commit`: kind is `git` without consent
  - `files`: kind is `non-git`
- `worker.intended_state`: `pending` | `running` | `archived`
- `worker.observed_state`: `none` | `running` | `idle` | `needs_input` | `archived` | `missing`
- `manager.intended_state` / `manager.observed_state`: the same ranges as for workers
- `manager.last_report.status`: `idle` | `needs_input` | `unparsed`
- `worker.review.state`: `requested` | `superseded` | `rejected` | `approved` | `waived`
- `worker.model` (optional): `opus` | `sonnet` | `haiku`, or absent / null
- `task.artifacts[].kind`: `branch` | `commit` | `pr` | `ci` | `file`
  - `pr` / `ci`: `url` (http or https) is required
  - `file`: `path` (absolute) is required; `url` is forbidden
  - `branch` / `commit`: `ref` is required; `url` is optional once pushed
- `task.status`: `needs_user` | `running` | `waiting` | `done` (derived)
- Note `status`: `backlog` | `in progress` | `pending` | `done` | `archived`; note `assignee`: `user` | `agent`. `state_tool` never writes `pending` / `agent`.
- `user_hold.reason`: `pause` | `invalid`

## last_report

`null`, or one of two shapes. `message_ref` is required in both: it is the identifier of the latest message the report was read from (the uuid of the last assistant entry of the session's transcript, or `hc:<handle>:<turn>` when it was read with `hc read`, SKILL.md section 2).

- Parsed: `{message_ref, status, summary, next_action, read_confirmed?}`, where `status` is the report block's value. `awaiting_manager` is allowed only for workers of project tasks, and then `awaiting_since` is set. `read_confirmed` is copied from the report when present: a boolean, `true` only with `status` `done` (the user said in the worker session that they read an investigation result). Keeping it here lets a deferred verification or a pending review still see the mark.
- Parse failure: `{message_ref, status: "unparsed", error, at}`.

Idempotency uses `message_ref`: if the latest message's id equals `last_report.message_ref`, the turn is already processed. Stall detection uses `continues` (see workers[]).

## Derivation (performed by commit)

`task.status` looks at **observed values only**. The first matching rule wins:

0. `done`: the task has `cancel` and no occupying worker (see Cancellation).
1. `needs_user`: the task's `user_actions` holds an action that stops the task, or some worker is observed `archived` while its `intended_state` is not `archived` (archived outside the supervisor; cleared when the supervisor verifies the result and sets `intended_state: archived`). While some worker of the task is at work (`observed_state: running`, i.e. `hc` status `working`, and not in `kept`), a PR wait does not stop the task: a follow-up (`follow_up`) or an `auto=pr_closed` action (`detector: pr_closed`) is skipped here, so the task derives `running` and its note shows `in progress` / `agent`. Every other user action (a push decision, a read action, a failed verification, a worker's question) still makes it `needs_user`. Once no worker is at work (`idle`, `needs_input`, archived, or kept), PR waits make it `needs_user` again.
2. `running`: some worker has `observed_state` in `running` / `idle` / `needs_input`, or is `missing` while its `intended_state` is not `archived`. A `missing` worker with `intended_state: archived` counts as finished, and so does a worker in its task's `kept`.
3. `waiting`: only unstarted workers (`observed_state == none`) remain.
3a. `needs_user`: a numbered subtask is unchecked (an `@user` one, or one the user reopened; see Subtasks). Running and waiting workers come first, so unchecked subtasks of a task at work change nothing.
4. `done`: every worker is finished: observed `archived`, `missing` with `intended_state: archived`, or kept (it counts as verified-archived). A worker with `replaced_by` is not counted as verified-archived by the final fallback of this rule: a task whose only remaining workers are replaced external-archive workers and replacements that gave up as `missing` derives `needs_user`, not `done`. A `missing` worker with `intended_state: archived`, no `replaced_by`, and an entry in its task's `findings` (a held investigation worker that vanished, SKILL.md 6.4) counts as verified-archived. Its `findings` entry carried into the stub is its last verified conclusion, or the previous generation's when it vanished during later work.

Before deriving, `commit` maintains two automatic user actions, the only ones the supervisor never edits. The first is the waiting-task action. When a non-git `cwd` is occupied in current and free in the candidate, every task with a `pending` / `none` non-git worker on that `cwd` gets `Waiting task T-xxxx can start now: send any message to the supervisor to start it.`, or `待機中のタスク T-xxxx を開始できます: supervisor に何かメッセージを送ると開始します。` when the task's request contains Japanese (`note_lang`); it is added once and matched by its `start_possible` detector or by either exact description. `commit` removes it from a task as soon as that task has no startable worker (non-git, `pending` / `none`, `cwd` free in the candidate): the worker was started or removed, or the `cwd` is busy again because another waiting task on it started. The supervisor never adds or removes this user action itself.

The second is the completion ask (`auto=task_done`), for tasks of a note (with `uid`; a v1 state archives a finished task at once). A task that is neither cancelled nor `confirmed` and would derive `done` without it gets `The work is done: check the grounds in 完了報告 and the results, then set this note to done / user` (or `作業が完了しました。完了報告の根拠と結果を確認して done / user にしてください`, `note_lang`), so it derives `needs_user`, stays in `tasks`, and its note shows `in progress` / `user`. `commit --notes` rejects the commit that adds it unless that commit's ops for the task's note include `report_set` (the grounds for completion in `## 完了報告`). `commit` removes it as soon as the task would no longer derive `done` without it (a follow-up worker was added, another user action opened, a cancel) or the task is `confirmed`; then a confirmed task derives `done` and is archived.

A task derived as `done` is moved, **in the same commit**, to an `archived` stub, and its workers are removed from `workers`. `archived` is truncated to the 20 newest stubs. The stub keeps the task's `uid` and `last_artifact_number`.

The waiting-task user action is not added to a task with `user_hold` or `artifacts_quarantine`, and their pending workers are not startable.

A project with `confirmed`, no `close`, and no live task left gets `close = {at, reason: "done/user で完了"}` and `manager.intended_state: archived` in the same commit, and its `auto=children_closed` user action is removed. `commit` then validates the result again (this also covers the waiting-task user actions it added or removed).

### Note props (`note_props`)

`commit` writes the note's `status` / `assignee` from the view. The first matching row wins:

| Condition | Note props |
| :--- | :--- |
| `confirmed` | `done` / `user` |
| task `cancel` / project `close` (occupying workers or not) | `archived` (assignee kept) |
| `user_hold` | not written |
| project `done_proposed` | `in progress` / `user` (the user confirms with `done` / `user` or rejects with `in progress` / `agent`) |
| task derived `done` (a v1 state; a task of a note derives `done` only when `confirmed` or cancelled) | `done` / `user` |
| task derived `needs_user` (held read actions and the completion ask included) / project with open user actions | `in progress` / `user` |
| otherwise | `in progress` / `agent` |

When the written `status` becomes `done` or `archived`, `commit` also writes the properties of that line of `## Note props` in `<dir>/CLAUDE.md` (default: `completed_at` / `archived_at` with the time as `YYYY-MM-DD HH:mm:ss`); a `{now}` value is written only when the property is empty. When it becomes anything else, every property named in `## Note props` is reset (`para` to `Project`, `aliases` to `[]`, the others to null).

Props are written only when the note's current values equal the candidate's `note_seen`; otherwise the note is reported in `notes_deferred` and handled by the next scan. Stubs get no `status` / `assignee`.

Every commit also writes `task_id` and `project_id` (`id_props`) to each note of a task, stub, or project whose values differ, so the kanban shows them: a task note gets its `T-` id and its project's `P-` id (none outside a project), a project note only its `P-` id. A live task wins over its stub. These are not user values: `notes-scan` ignores them, and `migrate-v2` writes them too.

For a task or stub note it writes in the same way `subtasks`: `<checked>/<all>` of the checklist lines of `## サブタスク` (numbered or not), e.g. `2/5`, and nothing for a note without one. It is not a user value either, and it is brought up to date only when `commit` writes the note.

The `summary` prop is written only by the `summary` note op (SKILL.md section 4), never derived: one line of at most 80 characters (`note_summary` joins the lines with spaces and cuts a longer text to 79 characters plus `…`), shown on the kanban card. New notes start with `summary: null` (templates from the Vault interview have it empty; a note without it gets it at the first `summary` op). It is not a user value either: `notes-scan` ignores it, and a user's edit stays until the next `summary` op.

The `priority` prop (`A`, `B`, or `C`) is the user's alone: `state_tool` never writes or checks it, `set_props` keeps its line as it is, and `notes-scan` ignores it. Only the task kanbans read it, ranking a column's cards by it (none or another value ranks as `B`) and then by the last change (SKILL.md section 3).

### Subtasks

`## サブタスク` holds the task's subtasks, one top-level checklist line each (an indented line, a fenced block, or a comment is the user's note):

```markdown
- [x] [ST-0290] hook エラーの調査 (w-0145 e1513f0)
- [ ] [ST-0291] @agent mise trust の自動化 (w-0147)
- [ ] [ST-0292] @user nature-server で動作確認する
- [ ] 書いたばかりの行
```

`parse_subtasks` reads each as `{id, done, assignee, text, worker, commit}`: `id` is `ST-NNNN` or null for a line the supervisor has not numbered yet, `assignee` is `user` for `@user` and `agent` otherwise (no `@` is the agent's), and the trailing `(w-NNNN)` / `(w-NNNN <sha>)` is the worker it was sent to and the verified commit. `ST-` numbers share the counter of `UA-` / `AA-` (`supervisor.last_ua_number`), so one number names one item; `join_view` raises `last_ua_number` to the highest `ST-` number in the notes. Subtasks are kept only in the note: the view's `subtasks` lists the numbered lines (unnumbered lines are not read until the supervisor numbers them), and `split_view` drops it. `notes-scan` events of task notes and `note-show` carry every line as `subtasks`.

Note ops (all idempotent): `st_adopt {st: {text, id?}}` numbers the first unnumbered line whose text equals `text`; `st_add {st: {text, assignee?, id?}}` appends a numbered line (`@<assignee>` written when given); `st_set {id, done?, assignee?, worker?, commit?}` sets the given fields of a numbered line (`worker: null` removes the trailing mark; a new worker without `commit` drops the old commit; a `commit` needs a worker). A line not found is a `note_problems` entry.

### User changes (`user_transition`)

`notes-scan` compares each note with `note_seen` (or with the `before` values of an unconfirmed journal entry) and classifies the change. `state_tool` never writes `done` / `agent` (completion is the user's to confirm); the rows seen from done/agent cover notes written by earlier versions. Any combination not listed is `invalid`: the user is told, `user_hold` (`reason: invalid`) stops props and starts, and nothing is acked until the user sets listed values. A change of `## アクション` alone needs no handling.

| # | Seen | Now | Condition | Event |
| :--- | :--- | :--- | :--- | :--- |
| 1 | unmanaged (tagged `supervised`; other unmanaged notes are ignored) | backlog/agent | | `pickup` |
| 2 | unmanaged | assignee not agent | | `none` |
| 3 | unmanaged | agent with another status | | `invalid` |
| 4 | managed | unknown status / assignee, pending/agent, unreadable frontmatter, duplicate uid | | `invalid` |
| 5 | in progress/agent | in progress/user, backlog/user, pending/user | active | `pause` |
| 6 | in progress/user | pending/user, backlog/user | active | `pause` |
| 7 | in progress/agent or in progress/user | the user values of #5 / #6 | not active | `pause` |
| 8 | paused | in progress/agent, backlog/agent | | `resume` |
| 9 | paused | another user value | | `none` |
| 10 | in progress/user | only `## アクション` changed (answers, requests) | open user actions | `none` |
| 11 | in progress/user | in progress/agent, backlog/agent | | `resume` |
| 12 | in progress/agent | backlog/agent | | `restore` |
| 13 | done/agent; in progress/* with only held read actions or the completion ask open, or with nothing open and every worker finished (the user checked the last `@user` subtask); an unchecked numbered subtask counts as open | done/user | task | `confirm` |
| 14 | done/agent, in progress/*, pending/user (paused) | done/user | project | `confirm` |
| 15 | in progress/*, paused | done/user, done/agent | task, except #13 | `cancel` |
| 16 | done/agent, done/user; or any value but archived on a stub (a task that is not live) | backlog/agent; also in progress/agent on a stub | | `follow_up` |
| 17 | managed | archived | task | `cancel` |
| 18 | managed | archived | project | `close` |
| 19 | archived | any | | `invalid` |
| 20 | done/user | done/agent | | `restore` |
| 21 | done/agent | in progress/agent, in progress/user | project | `reject_done` (with `pause` for in progress/user) |
| 22 | only `## 成果物` changed | dependencies met (or a stub) | | `artifacts_edit` |
| 23 | only `## 成果物` changed | dependencies not met | live task | `artifacts_broken` |
| 24 | in progress/user | in progress/agent | project with `done_proposed` | `reject_done` |

The stub case of #16 is checked right after #19 and before the rows that look at a hold or a worker (#8, #11, #12), so a stub set to in progress/agent or backlog/agent is always `follow_up` (SKILL.md 6.7 (c)). A change to the same values as before stays `none`.

### Artifact quarantine

`artifact_needs(view_task, workers, items)` reads the parsed `## 成果物` rows (not the view, which drops a `branch` not directly followed by a `commit`) and lists what is missing: the delivery artifacts of each worker intended archived (except cancelled tasks, `missing`, `replaced_by`, `no_result`), the PR of each follow-up user action, the branch group of each worker an open push decision names, and the `commit` after each `branch` from `art_from` on. When a live task's rows no longer meet them, the task gets `artifacts_quarantine` and an `ask answer` user action. While it is set, `validate` skips, for that task only, the delivery check of workers already intended archived in current and the follow-up url check of follow-up user actions current already has; a worker that newly becomes archived is checked in full.

Consequences:

- A worker with `intended_state: archived` and `observed_state: idle` keeps its task in `running`; the task moves only after the archive is observed.
- A `commit`-delivery task carries the push-decision user action, so it stays `needs_user` after its workers are archived, until the user decides. A worker whose verified head was observed pushed (its `commit` artifact has the commit `url`) adds none, and neither does a worker in `no_result`.
- A follow-up or `auto=pr_closed` user action makes its task `needs_user` once no worker of the task is observed `running`, even before the archive is observed. While a worker is `running` it leaves the task `running` (`in progress` / `agent`).
- A task whose only remaining user actions are follow-ups stays in `tasks`, together with its archived workers, until the last follow-up is removed; then `commit` moves it to an `archived` stub.
- A project worker under review that is archived outside the supervisor keeps its task `needs_user`, and one that went `missing` keeps it `running`: it is not `intended_state: archived` until its review is approved or waived (see Invariants), so the task does not finish.
- A held investigation worker (verified, `intended_state: running`, with a read user action) keeps its task `needs_user`. Its push decision is added only in the candidate that releases it (SKILL.md 6.3 step 6).
- A kept worker (`intended_state: running`, observed `idle`) does not keep its task `running`: once nothing else is open, the task gets the completion ask. Its session is archived after the user confirms the task (SKILL.md 6.13 `confirm`).

## Cancellation

`task.cancel = {at, reason}` (non-empty strings) records the user's request to stop the task. While it is set, no worker of the task may have `intended_state: running`. A worker of any kind is occupying while `intended_state == running`, or `intended_state == archived` and `observed_state` is neither `archived` nor `missing` (a vanished session cannot be archived, so it counts as finished). A cancelled task with no occupying worker derives `done` (rule 0 of Derivation, ahead of user actions), so `commit` moves it to an `archived` stub with `cancelled: true` and the reason, and drops its unstarted workers. A cancelled task with an occupying worker follows the normal rules.

A started worker left at `intended_state: pending` is not occupying, so cancelling would archive the task while that session is still alive. When cancelling, set `intended_state: archived` on every worker with a `session_id`. For a cancelled task the delivery artifact requirement and the push-decision requirement do not apply; `archive_consent` still does for workers that are neither observed `archived` nor `missing`. The automatic waiting-task user action is never added to a cancelled task.

## Invariants

`commit` rejects the candidate (exit 1, `error:` lines on stderr, nothing written) when any of these fails. Malformed shapes (non-object items, non-string ids, non-list `user_actions` / `artifacts` / `worker_ids`) are rejected the same way.

Single-state checks (`validate`):

- Ids are unique, every task has at least one entry in `worker_ids` (except a task with an open `ask answer` user action, which waits for the user to say where it runs), and `worker.task_id` and `task.worker_ids` agree in both directions.
- At most one non-git worker occupies each directory tree: two occupied `cwd`s may be neither equal nor ancestor / descendant of each other (after realpath normalization). A worker occupies its `cwd` while `intended_state == running`, and also while `intended_state == archived` but `observed_state` is neither `archived` nor `missing` (an archive was requested but not yet observed).
- `delivery == push_pr` requires a non-empty `push_consent.text` that is a substring of `push_consent.message` when `message` is present (the substring of the task's `request` is checked against current, below). `message`, when present, is a non-empty string. This guarantees consistency between the fields only; keeping `request` and `message` verbatim is the supervisor's rule.
- A task that is not cancelled and has a worker with `intended_state == archived` and without `replaced_by` holds the artifacts its delivery requires:
  - `push_pr`: `branch` + `commit` + (`pr`, or a `branch` with `url`)
  - `commit`: `branch` + `commit`
  - `files`: at least one `file`
  - A worker named in its task's `no_result` needs none of these.
- Each `no_result` entry of a task names a worker in that task's `worker_ids` whose `intended_state` is `archived`, that a read user action names (held), or that is in `kept`, that has no `replaced_by`, and no worker appears twice. A worker set back to `running` (SKILL.md 6.4, 6.7 (a)), or a held worker whose read user action is removed without archiving it, must lose its entry in the same candidate. Otherwise: `task T-xxxx: no_result w-xxxx must name an archived, non-replaced worker of the task once`.
- `investigation`, when present on a task or a stub, is `true`: `task T-xxxx: investigation must be true when present`.
- `interactive`, when present on a worker, is `true`: `worker w-xxxx: interactive must be true when present`.
- `findings`, on a task or a stub, is a list of objects whose `worker_id`, `session_id`, `title`, `summary`, `message_ref`, and `at` are all non-empty strings: `task T-xxxx: findings must be a list of {worker_id, session_id, title, summary, message_ref, at} with non-empty strings` (a stub's error starts with its own prefix). On a task (not on a stub) it also requires `investigation`, and each entry names a worker in `worker_ids` whose `session_id` equals the entry's, at most once: `task T-xxxx: findings w-xxxx must name a worker of an investigation task by its session_id, once`.
- `after`, when present, is the T-id of another task (`task T-xxxx: after must be the T-id of another task`), and following `after` through the live tasks never comes back to the task (`task T-xxxx: after must not form a cycle`). The task it names is not looked up, since a stub leaves the 20-stub window.
- `derived_from`, when present, is an object whose `task_id` and `context` are non-empty strings: `task T-xxxx: derived_from must be {task_id, context} with non-empty strings`. The task it names is not looked up.
- Read user action: a user action without `follow_up` whose description starts with `Read the result of w-` (the reserved prefix `Read the result of w-NNNN (`, the worker id ending before `(`). Its task has `investigation`; its worker belongs to the task, has `intended_state: running`, is observed neither `archived` nor `missing`, and has a `findings` entry; no worker has two; and on a project task the worker's `review.state` is `approved` or `waived`. Otherwise: `task T-xxxx: read action for w-xxxx needs a live, approved or waived (project) worker of an investigation task with findings, once`. Putting a held worker back to review (`requested`, `rejected`, `superseded`) therefore must remove its read user action in the same candidate.
- Kept worker: each entry of a task's `kept` names, once, a worker of the task with `intended_state: running`, observed `running`, `idle`, or `needs_input`, no `replaced_by`, not held, whose `last_report.status` is `done`, that is in `no_result` or whose task holds the artifacts its delivery requires (above), and, on a project task, whose `review.state` is `waived`, or `approved` with `review.message_ref` equal to `last_report.message_ref`. Otherwise: `task T-xxxx: kept w-xxxx needs a live, unheld worker of the task whose latest report is a recorded done (...)`. So a candidate that records a newer report, sends a follow-up, cancels, or replaces the worker removes it from `kept` (SKILL.md 6.3 step 1, 6.7 (a)).
- `replaced_by`, when present, names a different existing worker of the same task; its worker has `intended_state: archived`; following the chain ends at a worker without `replaced_by`.
- `followups[]`, when present, is a list of objects with non-empty string `text` and `at`; a stub's `request` and `followups` have the same shapes.
- A task id that is also in `archived` is a revival: the stub is not `cancelled` and its `uid` equals the task's `uid`.
- `last_report`, when not null, has `message_ref`. Its `read_confirmed`, when present, is a boolean, and `true` only with `status` `done`.
- A task with `cancel` has no worker with `intended_state: running`.
- `continues`, when present, is an integer >= 0.
- A `file` artifact's `path` lies under the `cwd` of a worker of that task.
- URLs are http or https only.
- A user action's `follow_up`, when present, is an object whose `rule` matches `^[a-z0-9][a-z0-9-]{0,31}$`, whose `done` is `pr_closed` or `manual`, and whose `url` is http(s).
- That `url` equals the `url` of one of the task's `pr` artifacts. A candidate that replaces or removes a `pr` artifact must remove the follow-ups of the old url in the same candidate.
- Within one task, no two follow-ups share the same `(rule, url)`.
- A user action with `follow_up` has a non-empty string `since`.
- None of these checks reads `CLAUDE.md`: `commit` does not know the rules, only the shape.
- Project ids are unique and their numbers are at most `supervisor.last_project_number`. A closed project's `manager.intended_state` is `archived`.
- A task's `project_id` names a project in `projects` without `close`: `task T-xxxx: project_id P-xxxx must name an open project`. `proposal.from` equals `project_id`, and `redos` appears only on a task with `project_id`.
- `review` appears only on workers of tasks with `project_id`. Review ids are unique within a project, and their numbers are at most that project's `last_review_number`.
- Approval gate: a worker of a project task with `intended_state: archived`, no `replaced_by`, and a task without `cancel` has `review.state` `approved` or `waived`; `approved` also needs `last_report` null or `last_report.message_ref` equal to `review.message_ref`. Being observed `archived` or `missing` exempts nothing. Otherwise: `worker w-xxxx: archived project worker needs an approved review of its latest report (or waived / cancel / replaced_by)`. A candidate that records a newer report on an approved worker must also set it back to `intended_state: running` (SKILL.md 6.4).
- `last_report.status` `awaiting_manager` is allowed only on workers of project tasks and requires `awaiting_since`; `awaiting_since` is not allowed otherwise.
- A non-git worker's `cwd`, in any `intended_state` (`pending` included), does not overlap any project's `dir` (equal, ancestor, or descendant). `check-cwd` reports such a `cwd` as busy.

Checks against the current state (`next_state`):

- The candidate's `rev` equals the current `rev`. The written state gets `rev + 1`.
- No silent deletion: a task present in current but absent from the candidate is always rejected. Tasks leave `tasks` only through the move that `commit` itself performs for tasks derived as `done`, so never remove a task from the candidate. A worker with a non-null `session_id` may disappear only together with its task's move.
- Push consent without `message`: when a task with a `push_pr` worker has a `push_consent` that differs from current's, its `text` is a substring of the task's `request` at that time (the content of the note's `依頼の原文` callout when present, otherwise its `## 概要`), so formatting `## 概要` never changes the result.
- Keeping: a worker that is in a candidate task's `kept` and in no current task's `kept`, with `commit` delivery, gets the same push-decision check as the transition to archived below: `worker w-xxxx: keeping a commit delivery worker requires a push-decision user_action`.
- Transition to archived: for each worker whose `intended_state` is not `archived` in current and is `archived` in the candidate:
  - if its delivery is `commit`, the worker is not in a current task's `kept` (its push decision was checked when it was kept, and the user may have resolved it since), the task is not cancelled, the worker has no `replaced_by` and is not in the task's `no_result`, and its group is not pushed at its verified head (the `commit` artifact right after the last `branch` artifact `<cwd>#<branch>` of that worker has a `url` ending in `/commit/<its ref>`; SKILL.md 6.3 step 6), the candidate task must contain the push-decision user action. A group's `commit` always directly follows its `branch`; a `pr` artifact is appended after the group
  - `supervisor.archive_consent` must not be null, unless the worker is already observed `archived` or `missing` (no archive call follows); a worker with `replaced_by` is not exempt from this
- A worker already `intended_state: archived` without `replaced_by` in current (its result was verified and recorded, or its task was cancelled) must not get `replaced_by` in the candidate.
- Projects are never removed by the supervisor, and `last_project_number` and `last_review_number` never decrease. A project id absent from current has a number above current's `last_project_number`; a review id that is new or changed on a worker has a number above current's `last_review_number` of its project (above 0 for a new project), so ids are never issued again.
- The manager may newly become `intended_state: archived` only when its project has `close` or it is observed `archived` or `missing`.
- The automatic waiting-task user action is never offered for a worker that is the `replaced_by` target of another worker, so a replacement waits for the supervisor's next user turn instead of prompting the user.
- Removing the push-decision user action from a candidate whose workers are already `archived` (or whose archive was refused and the worker remains) is accepted: the transition checks apply only to workers that newly become archived.
- Serial queue: a task's `after` may be new or changed (not removed) only while every worker of the task is observed `none`: `task T-xxxx: after may change only while no worker of the task has started`.
- Push consent from a later message: for each candidate task whose `push_consent.message` is new or differs from the current task's, the task must exist in current, current must hold a push-decision user action for it (naming the branch of one of its git workers; a user action with `follow_up` is never a push decision), and the candidate must no longer hold that user action. Otherwise: `task T-xxxx: push_consent.message may be set only while resolving a pending push decision`.

## Report to artifacts

Record artifacts from values the supervisor observed, not from the report alone.

| Report field | task.artifacts entry |
| :--- | :--- |
| `branch` | `{kind: branch, ref: "<cwd>#<branch from state>", url?: branch URL once pushed}`, where `<cwd>` is the worker's repo root as an absolute path (the form of the Example). SKILL.md 6.7 finds the source branch of a follow-up by this `<cwd>#` prefix. |
| `commit` | `{kind: commit, ref: <observed HEAD sha>}`. If it differs from the report, use the observed value and note it in the summary. For `commit` delivery, `url` = `<web_url>/commit/<observed HEAD>` only when that HEAD was observed pushed (SKILL.md 6.3 step 6); otherwise no `url`. It gates the push-decision check, so keep it unabbreviated and directly after its `branch` artifact. |
| `pr` | `{kind: pr, url}`, only when the PR head sha equals the observed HEAD. |
| `ci` | `{kind: ci, url}` |
| `files[]` | One `{kind: file, path}` per entry that exists and lies under the worker's `cwd`. |
