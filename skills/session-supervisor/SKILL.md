---
name: session-supervisor
description: Run a single supervisor session in the Claude desktop app that accepts tasks spanning several repositories or directories, starts one or more worker sessions per task (an app worktree per git worker, one running worker per non-git directory), verifies each worker's committed or recorded results, archives finished workers automatically, and keeps everything in a validated state.json with a published dashboard that survives compaction, resume, /clear, and re-creation of the supervisor. Triggers include "監督セッションとして動いて", "このタスクを作業用セッションに振り分けて", "supervisorとして複数repoの作業を回して", "act as the session supervisor", "dispatch these tasks to worker sessions", "supervise worker sessions".
---

# Session Supervisor

## Overview

One supervisor session, opened in a dedicated non-git directory (the supervisor directory, default `~/supervisor`), is the user's only point of contact. For each task the user gives it, the supervisor:

1. splits the task by directory and starts one worker session per directory (git: inside an app worktree; non-git: at most one running worker per directory tree),
2. detects when a worker's turn ends (the app's automatic continuation, plus periodic reconciliation),
3. verifies the result from observed values: a committed branch (pushed / PR only when the user explicitly allowed it in the request), or result files recorded under the directory,
4. archives the worker without asking, based on the user's one-time archive consent, and
5. records every state change in `state.json` and republishes a dashboard to the same artifact URL.

Determinism lives in `scripts/state_tool.py` (Python 3 stdlib): validated atomic commits, status derivation, dashboard rendering, the recovery digest, and report parsing. The supervisor (the LLM) only operates session tools and makes judgments.

Reference files:

- [references/state-schema.md](references/state-schema.md): `state.json` fields, value ranges, status derivation, invariants, report-to-artifact mapping, example
- [references/worker-prompt.md](references/worker-prompt.md): the two-part worker brief (`prompt` and `background`) and the `supervisor-report` block

In the commands below, `<skill-dir>` is the directory containing this SKILL.md and `<dir>` is the supervisor directory.

## When to Use

- The user asks to set up or run a supervisor (first run included).
- The session runs in the Claude Code tab of the desktop app, in a supervisor directory prepared by `setup`.
- The user wants work in several repositories or directories delegated to worker sessions and tracked to completion.
- The SessionStart hook injected a supervisor digest (after startup, resume, `/clear`, or compaction).

## Do Not Use When

- The session is a worker (its brief says it was started by a supervisor). Workers never start, archive, or delete sessions.
- The environment is not the desktop app or lacks the start capability entirely. See Degraded modes before giving up.
- The target is a non-colocated jj repository (has `.jj` but `git rev-parse` fails). This is out of scope; tell the user.
- The work fits in this one session; a supervisor adds overhead.

## Instructions

### 0. Entry

Decide which case applies before anything else:

- **This session's `cwd` has `state.json` and `.supervisor/`**: it is the supervisor. Continue with the Capability check (section 2).
- **Otherwise**: ask the user for the supervisor directory (default `~/supervisor`). If `<dir>/state.json` does not exist, run Setup (section 3). Then tell the user to open `<dir>` in the app and start a **new** session there in auto permission mode, and stop. This session never acts as the supervisor: the SessionStart hook does not fire here, turn-end notes of workers started from here would not reach a session in `<dir>`, and a supervisor in a worker's non-git directory breaks the one-worker-per-directory rule.

### 1. Core rules

- `state.json` has a single writer: this supervisor. Every state change, without exception, goes through the state-change set (section 4). Never edit `state.json` directly and never write `task.status` (it is derived).
- Separate intent from observation: `intended_state` is what you asked for; `observed_state` is what list / get showed. Status derivation and result checks use observed values only.
- Copy the user's request into `task.request` verbatim. Never summarize it.
- push / PR happen only when the user explicitly wrote it in the request (or later in this chat, with the same explicit wording). Otherwise workers stop at commit and the push decision becomes a user action.
- Never use the delete capability. Never remove worktrees or branches yourself.
- `supervisor.archive_consent` is the explicit user consent that the archive capability requires. It covers only workers whose `startedBy` is in `supervisor.session_ids`, and only after their results are verified or their task is cancelled by the user. Quote it in every archive `reason`.
- Writes outside the working directory (push, PR creation, posts) follow the user's rules: only with explicit consent recorded in `state.json`, and every PR body you create starts with `<sub>🤖 by session-supervisor</sub>`.

### 2. Capability check

Run at the start of every supervisor session and after every recovery (section 7).

1. Confirm these capabilities are available. Tool names differ between builds; the names in the table were observed in one desktop build and are examples only. Use the equivalent tool you have.

   | Capability | Used for | Example name |
   | :--- | :--- | :--- |
   | Start a session (`prompt`, `background` of 80+ characters, `cwd`, use worktree, `branch_name` slug, `name` handle, fresh context, `permission_mode`, immediate start) | Launching workers | `start_session` |
   | List sessions (`limit`, `include_archived`, rows with `startedBy`, `name`, running / archived flags) | Reconciliation | `list_sessions` |
   | Get one session (branch, worktree path, `sourceBranch`, parent session); `self` returns this session | Filling branch / worktree / base branch after start; learning your own session id | `get_session`, e.g. `get_session("self")` |
   | Read a session's latest events in full | Reading the report block, detecting pending approvals | `list_events` |
   | Send a message to a session | Continue nudges, relaying answers | `send_message` |
   | Archive a session | Finishing workers | `archive_session` |
   | Recurring self-paced turns | Periodic reconciliation | `/loop` |
   | Publish / read an Artifact | Dashboard | `Artifact` |
   | Delete a session | **Never used** | `delete_session` |

2. Check the permission mode. If it is not auto, tell the user once that every archive will show an approval card, then continue.
3. If `supervisor.archive_consent` is null, ask the user once: "May I archive workers started by this supervisor without asking, once their results are verified or you cancel their task? I never delete sessions." Record the verbatim answer as `archive_consent`. Without consent, every archive becomes the user action "Please archive w-xxxx".
4. Get your own session id with get `self`; the list capability never returns the calling session. If that id is not in `supervisor.session_ids`, add it.
5. Compare the installed copy with the bundled copy:

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" version
   python3 "<skill-dir>/scripts/state_tool.py" version
   ```

   If the two outputs differ, re-run setup (section 3).

Facts the procedures rely on (observed in the desktop app):

- In event-triggered turns (automatic continuation, the result of a send, a periodic `/loop` turn) **start is refused**; only turns that carry the user's own words may start sessions. send and archive work in every turn.
- A git repository that has never been opened in the app cannot get a worktree: start is refused, and retrying without a worktree is forbidden. The app may instead show a "Trust repository" button in the supervisor session.
- Archive stops the session and keeps the branch. The worktree may remain; leave it and mention it to the user (they may run `git worktree remove`). In auto mode archive completes immediately without an approval card.
- Archive is refused while the session is mid-turn, doing background work, pinned, or shown on screen. It also takes along side sessions that share the worktree.
- The turn-end note quotes only about 400 characters and replaces `"` with `'`, so it cannot be parsed. Read the full latest message with the event-reading capability; the report sits before trailing result / wrap-up events, so request about 12 events.
- Turn-end notes go only to the session that started the worker, and may arrive late after a send. After the supervisor is re-created, the new session receives none for workers the previous one started. The automatic continuation is not guaranteed either (the user answered a prompt, or started / stopped / archived the session). Periodic reconciliation covers all of these.

### 3. Setup

Run from the Entry (section 0) for a new directory, and again whenever the version check differs. The directory must not be inside a git repository.

```bash
python3 "<skill-dir>/scripts/state_tool.py" setup --dir "$HOME/supervisor"
```

`setup` is idempotent. It creates the directory (aborts inside a git repository), copies `state_tool.py` and `dashboard.template.html` to `<dir>/.supervisor/`, adds a SessionStart hook (matcher `startup|resume|clear|compact`) to `<dir>/.claude/settings.json` unless already present (aborts on invalid JSON), creates `state.json` and `CLAUDE.md` if missing, and prints what it created, kept, or updated.

When telling the user to open the new session, also explain that the dashboard is a private artifact republished on every visible change, and that periodic reconciliation (about every 5 minutes while workers run) uses some usage even when nothing changes.

### 4. The state-change set

Every state change runs this set. Never skip it, never batch unrelated edits across user turns.

1. Copy the current state to a candidate:

   ```bash
   cp "<dir>/state.json" "<dir>/state.next.json"
   ```

2. Change only the needed fields in `state.next.json` with the Edit tool. Do not rewrite the whole file, do not change `rev`, and do not touch `status`.
3. Commit:

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" commit --state "<dir>/state.json" --candidate "<dir>/state.next.json"
   ```

4. Exit 0 means the state was committed; it prints `{"rev": n, "dashboard_changed": bool}`. If `dashboard_changed` is true, republish the dashboard (6.5). If the output also has `dashboard_error`, the HTML was not updated (a warning is on stderr); the next commit rewrites it, so continue and skip republishing until then.
5. Non-zero exit means nothing was written: the `error:` lines on stderr name each violated invariant or malformed field, and `state.json` and `dashboard.html` are unchanged. Copy `state.json` again (never reuse the rejected candidate), redo the edit correctly, and commit again. Never bypass the invariants.

### 5. User actions

Any user action keeps its task `needs_user`, so a resolved one left behind blocks the task forever. Rule: **remove a user action in the same candidate that records its resolution.** The only exception is the automatic "Waiting task T-xxxx can start now ..." action, which `commit` adds and removes by itself; never touch it.

| User action | Added in | Resolved by (remove it in that candidate) |
| :--- | :--- | :--- |
| Report blockers (`needs_input` / `blocked` / `failed`), unparsed report summary, "w-xxxx is not progressing" | 6.3 | Relaying the user's answer with send |
| Failed verification of a `done` report | 6.3 step 5 | Relaying the user's instruction, or a later report that verifies |
| Push decision | 6.3 step 6, 6.4 | The user's decision (pushed, PR created, or no push) in 6.3 step 7 |
| "Open repo `<path>` once in the app, then tell me" | 6.2 | The worker starting successfully |
| Archive refused ("Close w-xxxx from the screen or unpin it", "Please archive w-xxxx") | 6.3 step 6, 6.4, 6.6 | Reconciliation observing `archived` |
| "Answer the pending approval of w-xxxx in the app" | 6.4 | Reconciliation observing the worker no longer `needs_input` |
| Missing worker, unverifiable external archive | 6.4 | The user's instruction carried out (new worker, recorded result, or cancel) |
| "Tell w-xxxx in the app: ..." (send unavailable) | Degraded modes | The user confirming it |
| Any action of a task | any | Cancelling the task (6.6) |

### 6. Procedures

#### 6.1 Task intake

1. Confirm the request names the target directories. If you inferred one, ask the user to confirm it.
2. Copy the request verbatim into `request`.
3. Split it by directory: one task, one worker per directory.
4. Determine the kind of each directory:

   ```bash
   git -C "<cwd>" rev-parse --show-toplevel
   ```

   Success means `git` and `cwd` becomes the repo root. A `.jj` directory with a failing `git` command means a non-colocated jj repo: out of scope, tell the user. Anything else is `non-git`.
5. Record push consent only when the request contains explicit wording, copying the exact phrase into `push_consent.text` (it must be a substring of `request`). A negated phrase is not consent even though it contains the words:
   - "README を直して。push して PR を作ってよい" → consent, text `push して PR を作ってよい`, delivery `push_pr`
   - "Fix the typo and open a PR" → consent, text `open a PR`, delivery `push_pr`
   - "Fix the typo. Do not open a PR" → no consent, delivery `commit`
   - "LICENSE の年を直して" → no consent, delivery `commit` (push becomes a user action later)
6. Set `delivery` per worker: `push_pr` (git with consent), `commit` (git without), `files` (non-git).
7. Add the task and its workers with `intended_state: pending`, `observed_state: none`. Run the state-change set.
8. Launch (6.2) in the same turn, since this turn carries the user's words.

#### 6.2 Launch

Start only in a turn that carries the user's own words. A start refused in an event-triggered turn is not retried; the task stays `waiting` and is launched on the user's next message.

1. Non-git: check the directory first. It is busy when an occupying worker's `cwd` is the same directory, an ancestor, or a descendant. If it prints busy, do not start; the task derives as `waiting`.

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" check-cwd --state "<dir>/state.json" --cwd "<cwd>"
   ```

2. Build `prompt` and `background` from [references/worker-prompt.md](references/worker-prompt.md).
3. Start the worker with: `prompt`, `background`, `cwd`, worktree on (git only), `branch_name` (a slug from the task), `name` (the handle, e.g. `t0003-w0004`), fresh context, `permission_mode` auto (never above the supervisor's), immediate start. Pass `source_branch` only when the user named one.
4. If start is refused because the repo was never opened in the app, do not retry without a worktree. Add the user action "Open repo `<path>` once in the app, then tell me" (or ask the user to press "Trust repository" if shown) and launch again on the user's next message.
5. On success, record the returned id as `session_id` and your own id as `started_by`. Call get and fill `branch`, `worktree_path`, `observed_state` (normally `running`), and `observed_at`. For git, set `base_branch` to the get result's `sourceBranch`; if it is absent, use the remote default branch (works in jj-colocated repos whose HEAD is detached; never use `symbolic-ref HEAD`):

   ```bash
   git -C "<repo>" symbolic-ref --short refs/remotes/origin/HEAD
   ```

   Set `intended_state: running` and run the state-change set.
6. If periodic reconciliation is not running, start it (6.4).

#### 6.3 Turn-end handling

Called only with workers that reconciliation (6.4) found changed; on an automatic continuation, run 6.4 first and let it hand workers here. 6.3 never calls 6.4. For each such worker:

1. Read the worker's latest events in full (about 12) and save only the last assistant message to `<dir>/.supervisor/last-message.txt`. If its message id equals `last_report.message_ref`, the turn is already processed; stop. Otherwise parse it:

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" parse-report --file "<dir>/.supervisor/last-message.txt" --expect-task T-0003 --expect-worker w-0004
   ```

2. Parse failure (`"ok": false`, including a field of the wrong type): set `last_report` to `{message_ref, status: "unparsed", error, at}`. If the previous `last_report.status` was not `unparsed`, send the worker one request to resubmit the report block. If it was already `unparsed`, read the message yourself, summarize it into a user action, and do not archive.
3. `in_progress`: if `summary` and `next_action` differ from the previous `last_report`, set `worker.continues` to 0. If `continues` is below 3, send "continue" and increment it; otherwise add the user action "w-xxxx is not progressing" and send nothing. Update `last_report`. Do not let rewording defeat the cap: once you have sent 3 nudges in total while the worker is on the same step of the task, add that user action even if the wording changed.
4. `needs_input` / `blocked` / `failed`: copy `blockers` into user actions. When the user answers in this chat, relay the answer with send.
5. `done`: verify the result inside the worktree with git (never jj):

   ```bash
   git -C "<worktree>" status --porcelain
   git -C "<worktree>" rev-parse HEAD
   git -C "<worktree>" rev-list --count "<base_branch>..HEAD"
   ```

   - `status --porcelain` is empty (untracked files included).
   - The commit count is 1 or more.
   - `push_pr`: the remote sha equals HEAD, and an existing PR's head equals HEAD:

     ```bash
     git -C "<worktree>" ls-remote origin "refs/heads/<branch>"
     gh pr view "<pr-url>" --json url,headRefOid,state
     ```

   - Merged PR: if the PR's `state` is `MERGED` and its `headRefOid` equals the recorded `commit` artifact (or the branch HEAD), the result is verified; skip the remote-branch and commit-count checks, since the head branch may have been auto-deleted and the count against an updated base may be 0:

     ```bash
     gh pr view "<number>" --repo "<owner/name>" --json state,headRefOid
     ```

   - non-git: every reported file exists under `cwd`.
   - Any failed check becomes a user action with the reason; do not archive.
6. Request the archive: record artifacts from observed values (mapping in [references/state-schema.md](references/state-schema.md)). For `commit` delivery, put the user action "Decide whether to push branch `<branch>` of `<repo>` (and whether to open a PR)" in the **same candidate**. Set `intended_state: archived` and run the state-change set. Then call archive with a `reason` naming the task id, the verified result, and that it relies on the recorded `archive_consent`.
   - Success: set `observed_state: archived` and run the state-change set. When no worker of the task is active any more, the task moves to `archived` in that commit (a task with a user action stays `needs_user`).
   - Refused because mid-turn or background work: do nothing; reconciliation retries.
   - Refused because pinned, shown on screen, or approval denied: add a user action with the reason right away (e.g. "Close w-0004 from the screen or unpin it").
7. Push decision: when the user allows the push in this chat with the same explicit wording as 6.1, record that phrase in `push_consent` and push yourself:

   ```bash
   git -C "<repo>" push origin "<branch>"
   git -C "<repo>" ls-remote origin "refs/heads/<branch>"
   ```

   Confirm the remote sha equals the `commit` artifact and add the branch URL to the branch artifact. If the user also wants a PR (the supervisor directory is non-git, so name the repo; take `owner/name` from `git -C "<repo>" remote get-url origin`):

   ```bash
   gh pr create --repo "<owner/name>" --head "<branch>" --title "<title>" --body "<sub>🤖 by session-supervisor</sub>

   <summary of the change>"
   ```

   Remove the push-decision user action in that candidate in every outcome, including "no push needed".

#### 6.4 Reconciliation

When: at the start of every supervisor turn, right after the hook digest is injected, on every automatic continuation, and periodically.

Periodic reconciliation: while at least one worker is `running`, `idle`, or `needs_input`, keep a self-paced `/loop` (about every 5 minutes) that runs this procedure. Start it after a launch (6.2). Stop it once no such worker remains: in a self-paced loop, end the turn without scheduling the next one; if the loop was created with a fixed interval, delete its recurring job.

Call list with `include_archived: true` and `limit: 100`. Do not use `linked`. Match rows to `workers` by `session_id`:

- Listed as active: update `observed_state` (`running` while running, otherwise `idle`) and `observed_at`. If the worker's latest events (event-reading capability) end in a pending permission request or an auto-mode classifier stop, set `needs_input` and add the user action "Answer the pending approval of w-xxxx in the app". Hand a non-running worker to 6.3.
- `intended_state: archived`, not yet observed archived, and not running: call archive again with the same `reason` and the refusal branching of 6.3 step 6. Do this regardless of `message_ref`, since a worker can finish background work without a new message.
- Listed as archived while `intended_state` is not `archived` (the user or the app archived it, e.g. after a PR merge): set `observed_state: archived`; the task derives `needs_user`. Verify the result on the repo side, since the worktree may be gone. A merged PR whose head equals the recorded commit is enough (6.3 step 5, "Merged PR"). Otherwise the branch exists (`git -C "<repo>" rev-parse --verify "<branch>"`), `git -C "<repo>" rev-list --count "<base_branch>..<branch>"` is 1 or more, and the PR / remote checks of 6.3 step 5 pass; non-git: the reported files exist. If verified, record the artifacts (and the push-decision user action for `commit` delivery) and set `intended_state: archived` in one candidate; `archive_consent` is not needed for a worker already observed archived. Otherwise add a user action that names the branch or directory and asks whether to start a new worker or cancel the task.
- Not listed: call get for that session. If still not found, set `missing` and add a user action asking whether to start a new worker or cancel the task. Never treat it as done. A `missing` worker still intended `running` keeps its task active, so either answer sets it to `intended_state: archived`, which counts it as finished (no archive call, no `archive_consent` needed): for a new worker, do this in the candidate that adds the new worker; for cancel, see 6.6. If a worker you gave up as `missing` (intended archived) shows up in the list again, do not just update its observed state: either set it back to `intended_state: running` and handle it as a live worker, or verify its deliverables and archive it as in 6.3 step 6.

Run the state-change set only when something differs.

#### 6.5 Dashboard

1. First publish: publish `<dir>/dashboard.html` as-is with `icon: dashboard`. Do not load the artifact-design skill and do not edit or redesign the HTML; `state_tool.py` renders it from the bundled template. Record the URL in `supervisor.dashboard.url`.
2. Within the same conversation, republish the same file path whenever `dashboard_changed` is true.
3. After a recovery or a new supervisor session, read the artifact by `supervisor.dashboard.url`, then publish to that `url` so the link stays the same.
4. The displayed rev may lag behind `state.json`; that is acceptable.
5. If Artifacts are unavailable, give the user the local path `<dir>/dashboard.html` instead.
6. Each task card header and archived entry shows the task ID as a click-to-copy button (label `Copy T-0007`, keyboard-focusable, briefly shows `Copied`; falls back to selecting the ID when the clipboard is refused) that copies exactly the ID, so the user can paste it into chat. There are no separate Copy buttons.

#### 6.6 Cancel

When the user says a task should stop (e.g. "stop", "cancel", "やめて", "不要"):

1. `archive_consent` is required for any worker that is neither observed archived nor `missing`. If it is null, ask for it first (Capability check step 3); if the user declines, ask them to archive the task's workers in the app and cancel after reconciliation observes them archived (no consent is needed then).
2. In one candidate: set `task.cancel` to `{at, reason}` with the user's words as `reason`, set `intended_state: archived` on **every worker that has a `session_id`** (whatever its current intended state, `missing` workers included), and remove the task's user actions. Leave never-started workers as they are; `commit` drops them. A started worker left at `pending` would not hold the task, so it would be archived at once with its session still alive. Run the state-change set; no artifacts or push decision are needed for a cancelled task.
3. Archive each worker that is neither observed archived nor `missing` (a `missing` session cannot be archived; with `intended_state: archived` it already counts as finished), with a `reason` quoting the user's cancel and the recorded `archive_consent`, using the refusal branching of 6.3 step 6.
4. Once no worker of the task is running or awaiting an observed archive (a `missing` worker intended archived does not count), `commit` moves the task to `archived` with `cancelled: true` and the reason. Tell the user which branches remain in which repositories.

### 7. Recovery (compact / resume / clear / re-creation)

The SessionStart hook runs `state_tool.py digest` and injects at most 10,000 characters: the state and dashboard paths, `rev`, the dashboard URL, `session_ids`, whether `archive_consent` exists, user actions, one line per task, and the instruction to run the Capability check and reconciliation before acting. If the digest is missing (hook did not fire), run it yourself:

```bash
python3 "<dir>/.supervisor/state_tool.py" digest --state "<dir>/state.json"
```

Then:

1. Run the Capability check. A re-created supervisor gets its new session id with get `self` and adds it to `session_ids`.
2. Run reconciliation. Workers whose `startedBy` is an earlier supervisor id are taken over as they are.
3. If any worker is `running`, `idle`, or `needs_input`, restart periodic reconciliation.
4. If the current turn cannot start a session, commit what you detected and launch on the next user message.

### 8. Degraded modes

| Situation | Fallback |
| :--- | :--- |
| Start unavailable entirely | Show the user the filled `prompt` and `background`, ask them to start the session, and record the session id they give. |
| Archive unavailable or refused by a classifier | After verifying the result, add the user action "Please archive w-xxxx". |
| send does not reach the worker | Replace nudges and relays with the user action "Tell w-xxxx in the app: ...". |
| Latest message unreadable or the fence is mangled | Ask the worker to resend the report as single-line JSON in the same fence. If that also fails, ask the user to paste the worker's final message. Never parse the turn-end note. |
| Neither `sourceBranch` nor `origin/HEAD` is available | Verify the commit count with `git -C "<worktree>" rev-list --count HEAD --not --remotes` (1 or more). |

### 9. Uninstall

1. Confirm no worker is `running`, `idle`, or `needs_input` (reconcile first) and stop periodic reconciliation.
2. Ask the user to delete the supervisor directory themselves. The agent never deletes it.
3. Leftover app worktrees can be listed and removed by the user:

   ```bash
   git -C "<repo>" worktree list
   git -C "<repo>" worktree remove "<worktree>"
   ```

   Branches stay in each repository.

## Verification

- `python3 "<dir>/.supervisor/state_tool.py" version` matches the bundled script after setup.
- Every state change in this session went through `commit`, and `rev` increased each time.
- Every user action that was resolved in this session was removed in the candidate that recorded its resolution.
- Every archived worker had, before the archive call, the artifacts its delivery requires (or a cancelled task) and a non-null `archive_consent`.
- No push or PR happened without `push_consent` recorded from the user's own words.
- The delete capability was never called, and the branches of archived workers still exist (`git -C "<repo>" branch --list "<branch>"`).
- The dashboard URL in `supervisor.dashboard.url` is unchanged after republishes and recoveries.
- While any worker is `running`, `idle`, or `needs_input`, periodic reconciliation is active; with none, it is stopped.
