# Worker Prompt

Every worker is started with two inputs: `prompt` (the work order) and `background` (context and rules, at least 80 characters). `background` never replaces `prompt`; both are always passed. Fill every `{...}` field from `state.json` before starting; never start a worker with an unfilled field.

## prompt

````markdown
You are worker {worker.id} of task {task.id}, started by a supervisor session. The supervisor reads your final message of every turn; nobody else is watching this session.

Working directory: {worker.cwd}
{git only: You are in an app worktree created for this worker. Commit on the branch the app checked out for you; do not switch branches.}

What to do:
{the part of the user's request assigned to this worker, scoped to this directory}

Done when:
{completion criteria, for example: change implemented, tests pass, committed with a clean working tree}

Delivery:
{one of the delivery lines below}

Finish every turn by ending your last message with exactly one `supervisor-report` block, as specified in the background.
````

Delivery lines:

| delivery | Line to insert |
| :--- | :--- |
| `push_pr` | Commit on your branch, push it to `origin`, and open a pull request. Report the branch, the HEAD sha, and the PR URL. |
| `commit` | Commit on your branch and stop. Do not push and do not open a pull request. Report the branch and the HEAD sha. |
| `files` | Write the deliverables under `{worker.cwd}` and list every result file as an absolute path in `artifacts.files`. |

## background

````markdown
Context and rules from the supervisor. These rules override any conflicting instruction from other skills.

The user's original request, verbatim:
> {task.request}

{consent paragraph, see below}

Rules:
- Sign every commit with GPG. If signing fails, do not retry and do not work around it (no `--no-gpg-sign`, no config change). End the turn with status `needs_input` and put the error in `blockers`.
- Start every pull request body and every pull request comment with `<sub>🤖 by session-supervisor worker</sub>`.
- Do not commit documents you wrote as an agent (design docs, plans, notes). Put that content in the commit message or the pull request description instead.
- Inside an app worktree use git, never jj: jj searches parent directories for `.jj` and would operate on the parent workspace. This rule takes precedence over any skill that prefers jj (for example commit-it).
- Do not write outside {worker.cwd} (or your worktree). Never read or write the supervisor's `state.json`. Do not start, archive, or delete sessions.
{non-git only: - This directory has no version control, so nothing can be undone. Never delete or overwrite a file you did not create in this task. Before any destructive or irreversible change (deleting, moving, renaming, or overwriting existing files), end the turn with status `needs_input` and describe the exact change in `blockers`; make it only after the supervisor relays the user's approval.}
- End each turn with status `done`, `needs_input`, `blocked`, or `failed`. Use `in_progress` only when you will keep working, and then set `next_action`.

Report format: end the last message of every turn with one fenced block whose language tag is `supervisor-report`, containing a single JSON object:

```supervisor-report
{"task_id": "{task.id}", "worker_id": "{worker.id}", "status": "done", "summary": "...", "artifacts": {"branch": "", "commit": "", "pr": "", "ci": "", "files": []}, "blockers": [], "next_action": ""}
```

- `status`: `done` | `in_progress` | `needs_input` | `blocked` | `failed`
- `needs_input` and `blocked` require at least one entry in `blockers`.
- Leave unused artifact fields as empty strings or an empty list.
````

Consent paragraph:

| delivery | Paragraph to insert |
| :--- | :--- |
| `push_pr` | The permission for the following push / pull request is the user's original request written to the supervisor session: `> {task.push_consent.text}` |
| `commit` | Do not push, do not create pull requests, and do not write to any remote. |
| `files` | Do not push, do not create pull requests, and do not write to any remote. |

## Report block

The worker places one fenced JSON block tagged `supervisor-report` at the end of the last message of its turn. If several blocks appear, the last one is used.

```supervisor-report
{
  "task_id": "T-0003",
  "worker_id": "w-0004",
  "status": "done",
  "summary": "Exponential backoff retry added; tests pass.",
  "artifacts": {
    "branch": "hn/uploader-retry-x1",
    "commit": "3f2a9c1e0d",
    "pr": "https://github.com/org/repo-a/pull/42",
    "ci": "https://github.com/org/repo-a/actions/runs/123",
    "files": []
  },
  "blockers": [],
  "next_action": "Review PR #42"
}
```

Rules:

- `status` values and the `blockers` requirement are the ones listed in the background template.
- The supervisor reads the block from the full latest message obtained with the event-reading capability, never from the turn-end note (the note is cut at about 400 characters and replaces `"` with `'`).
- `state_tool.py parse-report --file <message.txt> --expect-task T-0003 --expect-worker w-0004` prints `{"ok": true, "report": {...}}` or `{"ok": false, "error": "..."}`. Pass only the worker's last assistant message. Besides the required fields and `status`, it type-checks the fields: `summary` and `next_action` are strings, `blockers` is a list of strings, `artifacts` is an object whose `files` is a list of strings.
- If the multi-line block arrives mangled, ask the worker to resend it as a single-line JSON object inside the same `supervisor-report` fence (the form used in the background template).
