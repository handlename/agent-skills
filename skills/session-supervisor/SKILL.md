---
name: session-supervisor
description: Run a single supervisor session in the Claude desktop app that accepts tasks spanning several repositories or directories, starts one or more worker sessions per task (an app worktree per git worker, one running worker per non-git directory), verifies each worker's committed or recorded results, archives finished workers automatically, and keeps everything in a validated state.json with a published dashboard that survives compaction, resume, /clear, and re-creation of the supervisor; tasks may belong to a project whose read-only project manager session reviews its workers' results, answers their questions, and proposes redo work and new tasks. Triggers include "監督セッションとして動いて", "このタスクを作業用セッションに振り分けて", "supervisorとして複数repoの作業を回して", "act as the session supervisor", "dispatch these tasks to worker sessions", "supervise worker sessions", "プロジェクトを作って管理セッションを置いて", "create a project with a project manager session".
---

# Session Supervisor

## Overview

One supervisor session, opened in a dedicated non-git directory (the supervisor directory, default `~/supervisor`), is the user's point of contact for tasks; the user answers a worker's questions in that worker's session (6.3 step 4). For each task the user gives it, the supervisor:

1. splits the task by directory and starts one worker session per directory (git: inside an app worktree; non-git: at most one running worker per directory tree),
2. detects when a worker's turn ends (the app's automatic continuation, plus periodic reconciliation),
3. verifies the result through an observer subagent: a committed branch (pushed / PR, by a worker, only when the user explicitly allowed it in this chat or told that worker to in its own session), or result files recorded under the directory,
4. archives the worker without asking, based on the user's one-time archive consent (a worker of an investigation task waits until the user says they have read its result), and
5. records every state change in `state.json` and republishes a dashboard to the same artifact URL.

The supervisor decides where to delegate without asking the user: it picks the most likely target directory (cloning a repository or creating a work directory under the `## Locations` of `<dir>/CLAUDE.md` when none exists), routes a follow-up request to its existing task, and replaces a worker that went missing or unverified. It asks about delegation only when a worker is not progressing or replacements reached their limit (6.8).

A task may belong to a **project** (6.10): an id, a name, a context document (a GitHub issue or a Notion page), a project directory under `work_root`, and a **project manager** session that the supervisor starts there. The manager only reads (repositories, PRs, the context document) and returns decisions in a `project-report` block: verdicts on the reviews the supervisor requests after verifying a project worker's `done` (the worker is archived only after an approval), redo work, new-task proposals (started only after the user approves), and questions. Project workers send questions about other work of the project to the manager directly and wait with `awaiting_manager`. Tasks without a project behave as before.

Determinism lives in `scripts/state_tool.py` (Python 3 stdlib): validated atomic commits, status derivation, dashboard rendering, the recovery digest, report parsing, the project roster, wait timeouts, and the mapping of a manager's report to actions. The supervisor (the LLM) only operates session tools, delegates observation to subagents, and makes judgments.

Reference files:

- [references/state-schema.md](references/state-schema.md): `state.json` fields, value ranges, status derivation, invariants, report-to-artifact mapping, example
- [references/worker-prompt.md](references/worker-prompt.md): the two-part worker brief (`prompt` and `background`), the push-only worker, and the `supervisor-report` block
- [references/manager-prompt.md](references/manager-prompt.md): the project manager brief, the messages exchanged with the manager and with project workers, and the `project-report` block
- [references/observer-brief.md](references/observer-brief.md): the observer subagent brief and the facts it writes per purpose
- [references/follow-ups-format.md](references/follow-ups-format.md): the `## Follow-ups` rules in `<dir>/CLAUDE.md` that turn verified PRs into follow-up user actions

In the commands below, `<skill-dir>` is the directory containing this SKILL.md and `<dir>` is the supervisor directory.

## When to Use

- The user asks to set up or run a supervisor (first run included).
- The session runs in the Claude Code tab of the desktop app, in a supervisor directory prepared by `setup`.
- The user wants work in several repositories or directories delegated to worker sessions and tracked to completion.
- The SessionStart hook injected a supervisor digest (after startup, resume, `/clear`, or compaction).

## Do Not Use When

- The session is a worker (its brief says it was started by a supervisor). Workers never start, archive, or delete sessions.
- The environment is not the desktop app or lacks the start capability entirely. See Degraded modes before giving up.
- The target is a non-colocated jj repository (an observer subagent reports `.jj` but a failing `git rev-parse`). This is out of scope; tell the user.
- The work fits in this one session; a supervisor adds overhead.

## Instructions

### 0. Entry

Decide which case applies before anything else:

- **This session's `cwd` has `state.json` and `.supervisor/`**: it is the supervisor. Continue with the Capability check (section 2).
- **Otherwise**: ask the user for the supervisor directory (default `~/supervisor`). If `<dir>/state.json` does not exist, run Setup (section 3). Then tell the user to open `<dir>` in the app and start a **new** session there on opus in auto permission mode, and stop. This session never acts as the supervisor: the SessionStart hook does not fire here, turn-end notes of workers started from here would not reach a session in `<dir>`, and a supervisor in a worker's non-git directory breaks the one-worker-per-directory rule.

### 1. Core rules

- The supervisor may only: operate sessions (start, list, get, read events, send, archive); change state through the state-change set (section 4); publish the dashboard; talk to the user; and read files under `<dir>` (state, digest, observation results, the last-message file, `.claude/settings.json`).
- The supervisor must not: edit or write outside `<dir>` (the only exception is `state_tool.py prepare-dir`, which clones repositories into `clone_root`, creates work directories under `work_root` (6.1), and creates project directories with their `.claude/settings.json` guard hook under `work_root` (6.10)); run any mutating git / jj / gh command (committing, pushing, merging, rebasing, resetting, tagging, creating or removing worktrees, creating / merging / editing / commenting on PRs and issues, non-GET API calls, and jj changes); read repository files or run git / gh observation itself; answer questions about code by reading it. Changes go to a worker; observation and investigation go to an observer subagent (6.0).
- The PreToolUse guard installed by `setup` enforces the edit and mutating-command part for the supervisor and its subagents alike. A block (`[session-supervisor guard] blocked: ...`) means "delegate": never work around it with scripts, other interpreters, or shell tricks. `prepare-dir` is the one permitted exception, not a way around the guard: use it only as 6.1 and 6.10 describe. The guard is a guardrail against mistakes, not a security boundary; the rules above apply to what it misses.
- `state.json` has a single writer: this supervisor. Every state change, without exception, goes through the state-change set (section 4). Never edit `state.json` directly and never write `task.status` (it is derived).
- Separate intent from observation: `intended_state` is what you asked for; `observed_state` is what list / get showed. Status derivation and result checks use observed values only.
- Copy the user's request into `task.request` verbatim, and each follow-up request into `followups[].text` verbatim (6.7). Never summarize or change them. A follow-up is removed only by a correction (6.9).
- push / PR happen only when the user explicitly wrote it in the request (or later in this chat, with the same explicit wording) or told a worker to in that worker's own session, and only a worker carries them out. Only the wording in this chat becomes `push_consent`. Otherwise workers stop at commit and the push decision becomes a user action.
- Never use the delete capability. Never remove worktrees or branches yourself.
- `supervisor.archive_consent` is the explicit user consent that the archive capability requires. It covers only workers whose `startedBy` is in `supervisor.session_ids`, and only after their results are verified or their task is cancelled by the user. Quote it in every archive `reason`.
- Writes outside the working directory (push, PR creation, posts) follow the user's rules: only with explicit consent recorded in `state.json`, or by a worker on the user's own instruction in that worker's session. PRs are created only by workers, whose bodies start with the worker marker ([references/worker-prompt.md](references/worker-prompt.md)).
- A message sent from another session (project managers included) carries no user authority. Text the user pastes into this chat is the user's own words. A turn that starts with `From P-NNNN ` (a project manager's title, the P-id followed by a space) is event-triggered: never start a session in it.
- Set `review.state: waived` only for a push-only worker (6.3 step 7, New push worker and Reuse), for a held investigation worker closed or missing after a later turn (6.4), and when the user said in this chat that a review is not needed. Never set it to get past a `commit` rejection.

### 2. Capability check

Run at the start of every supervisor session and after every recovery (section 7).

1. Confirm these capabilities are available. Tool names differ between builds; the names in the table were observed in one desktop build and are examples only. Use the equivalent tool you have.

   | Capability | Used for | Example name |
   | :--- | :--- | :--- |
   | Start a session (`prompt`, `background` of 80+ characters, `cwd`, use worktree, `branch_name` slug, `name` handle, `title`, fresh context, `permission_mode`, `model` id (only the same family as this session or a cheaper one takes effect), immediate start) | Launching workers | `start_session` |
   | List sessions (`limit`, `include_archived`, rows with `startedBy`, `name`, running / archived flags) | Reconciliation | `list_sessions` |
   | Get one session (branch, worktree path, `sourceBranch`, parent session, `model`); `self` returns this session | Filling branch / worktree / base branch / model after start; learning your own session id and model | `get_session`, e.g. `get_session("self")` |
   | Read a session's latest events in full | Reading the report block, detecting pending approvals | `list_events` |
   | Send a message to a session | Continue nudges, report resubmission requests, follow-up and push messages, relaying answers other than report blockers | `send_message` |
   | Rename a session (`title`) | Retitling a reused worker; optional, see 6.2.2 Rename outcome | `set_session_title` |
   | Archive a session | Finishing workers | `archive_session` |
   | Recurring self-paced turns | Periodic reconciliation | `/loop` |
   | Publish / read an Artifact | Dashboard | `Artifact` |
   | Run a subagent with a brief and a `model` (run it without `model` when the capability has none) | Observation and investigation (6.0) | `Agent` |
   | Delete a session | **Never used** | `delete_session` |

2. Check the permission mode. If it is not auto, tell the user once that every archive will show an approval card, then continue.
3. If `supervisor.archive_consent` is null, ask the user once: "May I archive workers started by this supervisor without asking, once their results are verified or you cancel their task? I never delete sessions." Record the verbatim answer as `archive_consent`. Without consent, every archive becomes the user action "Please archive w-xxxx".
4. Get your own session id with get `self`; the list capability never returns the calling session. If that id is not in `supervisor.session_ids`, add it.
5. Compare the installed copy with the bundled copy:

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" version
   python3 "<skill-dir>/scripts/state_tool.py" version
   ```

   If the two outputs differ, re-run setup (section 3). Also read `<dir>/.claude/settings.json`: if no PreToolUse entry has a command that contains both `state_tool.py` and ` guard --dir`, ask the user once whether they removed it on purpose; re-run setup only if they did not, and otherwise leave it removed for this supervisor directory. Ask the user to start a new supervisor session (or to review hooks in `/hooks`) only when setup printed `created:` or `updated:` for `settings.json`, because hooks and the `model` setting are loaded at session start; a run that printed only `exists:` needs no restart.

   For each project without `close`, read `<project dir>/.claude/settings.json`: if no PreToolUse entry has a command that contains `state_tool.py`, ` guard --dir`, and `--role manager`, re-run `prepare-dir --project <name of the project dir>` (6.10; idempotent, it prints `updated:`). A running manager loads hooks only at its start, so the restored guard applies from its next replacement.
6. Check your own model: read `model` from get `self` (step 4). If it does not name opus, tell the user "This supervisor is not running on opus; pick opus in the app's model menu" and continue. Say it once per supervisor session; a recheck after recovery (section 7) does not repeat it. Never change your own model: the capability that changes a session's model refuses the calling session. If get returns no `model`, skip this step.
7. Check the follow-up rules. This runs after step 5 so that a stale installed copy has already been replaced by one that knows the subcommand. It only reads `<dir>/CLAUDE.md` and runs no `gh`:

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" validate-follow-ups --claude-md "<dir>/CLAUDE.md"
   ```

   `ok: N rules` means the rules are usable (`ok: 0 rules` when there is no `## Follow-ups`). On exit 1, tell the user the `error: CLAUDE.md:<line>: ...` lines once and continue: with an invalid section no follow-up is created, and everything else works as usual.

Facts the procedures rely on (observed in the desktop app):

- In event-triggered turns (automatic continuation, the result of a send, a periodic `/loop` turn) **start is refused**; only turns that carry the user's own words may start sessions. send and archive work in every turn.
- A git repository that has never been opened in the app cannot get a worktree: start is refused, and retrying without a worktree is forbidden. The app may instead show a "Trust repository" button in the supervisor session.
- Archive stops the session and keeps the branch. The worktree may remain; leave it and mention it to the user (they may run `git worktree remove`). In auto mode archive completes immediately without an approval card.
- Archive is refused while the session is mid-turn, doing background work, pinned, or shown on screen. It also takes along side sessions that share the worktree.
- The turn-end note quotes only about 400 characters and replaces `"` with `'`, so it cannot be parsed. Read the full latest message with the event-reading capability; the report sits before trailing result / wrap-up events, so request about 12 events.
- Turn-end notes go only to the session that started the worker, and may arrive late after a send. After the supervisor is re-created, the new session receives none for workers the previous one started. The automatic continuation is not guaranteed either (the user answered a prompt, or started / stopped / archived the session). Periodic reconciliation covers all of these.
- A project manager's turn-end notes likewise go only to the supervisor session that started it. A send between a worker and a project manager reaches only its recipient, never the supervisor; the supervisor sees the result only through the next turn-end of a session it started. (Both are assumptions to confirm in the desktop app.)

### 3. Setup

Run from the Entry (section 0) for a new directory, and again whenever the version check differs. The directory must not be inside a git repository.

```bash
python3 "<skill-dir>/scripts/state_tool.py" setup --dir "$HOME/supervisor"
```

`setup` is idempotent. It creates the directory (aborts inside a git repository), copies `state_tool.py` and `dashboard.template.html` to `<dir>/.supervisor/`, adds a SessionStart hook (matcher `startup|resume|clear|compact`) and a PreToolUse guard to `<dir>/.claude/settings.json` unless already present (aborts on invalid JSON or a malformed `hooks.SessionStart` / `hooks.PreToolUse`, before writing `state.json`; existing entries are kept), sets `"model": "opus"` there when the file has no `model` key (an existing value is kept; the Capability check reports a supervisor that is not on opus), creates `state.json` and `CLAUDE.md` if missing, and prints what it created, kept, or updated. The created `CLAUDE.md` contains a commented-out `## Follow-ups` example; the user enables follow-ups by writing that section ([references/follow-ups-format.md](references/follow-ups-format.md)).

`CLAUDE.md` also gets a `## Locations` section, the places where `prepare-dir` (6.1) may write:

- `clone_root` (default `~/src`): repositories are cloned to `<clone_root>/<host>/<owner>/<repo>`.
- `work_root` (default `<parent of dir>/<name of dir>-work`): non-git work directories `<work_root>/T-NNNN-<slug>` for new or investigation-only requests, and project directories `<work_root>/P-NNNN-<slug>` (6.10).

Values are absolute paths or start with `~`. `setup` writes the section when it creates `CLAUDE.md`; on an existing file it adds only the missing keys (appending the section when absent), never changes a value, and prints `updated:`. A key that is still missing falls back to the same default, so `prepare-dir` behaves as after a re-run of `setup`. A value that is not such a path, or an unknown key, makes `setup` and `prepare-dir` print `error: CLAUDE.md:<line>: ...` and exit 1 without writing anything; fix the line and run it again.

The guard (matcher `Edit|MultiEdit|Write|NotebookEdit|Bash`) runs `state_tool.py guard --dir <dir>` before each such tool call of the supervisor and its subagents:

- Edits and writes are allowed under `<dir>`, in Claude's auto-memory directories (`projects/*/memory/` under `$CLAUDE_CONFIG_DIR` or `~/.claude`), and in temp directories. Everything else is blocked, and so are the protected paths inside `<dir>`: `.claude/`, `state.json`, `.supervisor/state_tool.py`, and `.supervisor/dashboard.template.html` (only `setup` and `commit` write them).
- Bash commands are blocked when they run a mutating git / jj / gh command (the deny-list described in section 1), including inside `sh -c` and command substitution. Read-only forms pass.
- A block exits 2 with a message starting `[session-supervisor guard] blocked:` that names the matched command. Any other failure of the guard (missing or stale script, malformed input) lets the call through, which is why the Capability check looks for the guard entry.
- Worker sessions run in repository worktrees or their own directories and never load `<dir>/.claude/settings.json`, so the guard does not affect them.
- A project directory gets the same guard with `--role manager` (`guard --dir <project dir> --role manager`): the project manager may edit nothing outside its directory and run no mutating git / jj / gh command, and a block tells it to answer through send or return a plan in its `project-report`. It does not see MCP tools (session start, Notion), which the manager brief forbids.
- `state_tool.py follow-ups` writes only the candidate under `<dir>` (it refuses `state.json` and any path outside a supervisor directory itself, because the guard does not see writes made from Python). The `pr-state` observer (6.4, and its branch mode in 6.3 step 7 and 6.7) runs only read-only `gh` commands (`gh auth status`, `gh pr view`), and the `locate` observer (6.1) only `ls`, `gh repo view`, and `gh config get`; the guard lets them through.
- `state_tool.py prepare-dir` is the only command that writes outside `<dir>`, and only inside `## Locations`: `--clone` clones into `clone_root`, `--work` creates a directory under `work_root`, `--project` creates a project directory under `work_root` and adds the manager guard (below) to its `.claude/settings.json`, keeping existing entries, and `--show` only prints the expanded roots. It refuses a root inside `<dir>`, a `work_root` inside a git repository, nested roots, and any destination outside its root (after resolving symlinks). The guard does not see writes made from Python, so these limits live in the script.
- Manual recovery: if the guard misbehaves, the user removes its PreToolUse entry from `<dir>/.claude/settings.json` by hand and starts a new session. The Capability check then asks before restoring it, so a deliberate removal sticks.

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
6. Follow-ups: in every candidate that records a newly verified `pr` artifact (6.3 step 6, 6.3 step 7 "Verification", and 6.4 for an external archive or a missing worker that reappeared), run this once, after all Edits and right before step 3:

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" follow-ups --state "<dir>/state.json" --candidate "<dir>/state.next.json"
   ```

   - It adds a follow-up user action for each `## Follow-ups` rule to each `pr` url that the candidate has and `state.json` does not, and prints the added actions as JSON. Already recorded PRs never get one again, so a completed follow-up does not come back.
   - Pass `--closed <url>` (repeatable) only for urls whose `pr.state` the observer of that same verification (`verify-git` or `repo-side`, the "Merged PR" path of 6.3 step 5) reported as `MERGED` or `CLOSED`; `pr_closed` rules are skipped for them. Never launch an extra observer just to decide `--closed`. Omit it otherwise.
   - When a candidate replaces a `pr` artifact, remove the follow-up user actions of the old url in the same candidate; `commit` rejects a follow-up whose url is not a `pr` artifact of its task.
   - If you edit the candidate afterwards, read the file again first: the command rewrote it.
   - **Exit 1 is not a failure of the state-change set** (step 5 does not apply): the candidate is unchanged. Tell the user the `error:` lines once and commit the candidate as it is.
   - To add a missed follow-up by hand (only when the user asks), run `follow-ups --candidate "<dir>/state.next.json" --task T-xxxx --url <pr-url>` instead. It does not compare with `state.json`, so it also re-adds a follow-up the user already finished.

### 5. User actions

Any user action keeps its task `needs_user`, so a resolved one left behind blocks the task forever. Rule: **remove a user action in the same candidate that records its resolution.** The only exception is the automatic "Waiting task T-xxxx can start now ..." action, which `commit` adds and removes by itself; never touch it.

A project's user actions (`projects[].user_actions`, 6.10) follow the same rule. They change no task's status and are shown in the project's group on the dashboard. The table says for each row whether it is a task's or a project's user action.

Links: when the user has to open a page to act (a PR, a CI run, an issue, a document), put its URL in the description so they can open it from the dashboard: `[label](https://…)` or a bare `https://…` URL (only http(s) URLs become links). Use only a URL you observed (an observer result, a worker report, an artifact), never a guessed one. For a wording quoted in this file, keep the quoted text unchanged at the start and append the link after it, e.g. "Failed verification of w-0004: [CI run](https://github.com/org/repo-a/actions/runs/123) is red". A follow-up needs no link in its description: the dashboard links `follow_up.url` itself.

The prefixes `Answer w-NNNN (` (report-blocker guidance, 6.3 step 4), `Hub wait w-NNNN (`, `Review wait w-NNNN (` (6.4), and `Read the result of w-NNNN (` (6.3 step 6) are reserved; never start another user action with them.

| User action | Added in | Resolved by (remove it in that candidate) |
| :--- | :--- | :--- |
| Report-blocker guidance "Answer w-xxxx (T-xxxx <worker title>) in the app: <blocker>" | 6.3 step 4, 6.4 (a worker brought back) | The worker's next report processed in 6.3 (step 4 updates the guidance by difference), or the candidate that sets the worker `intended_state: archived`, `missing`, or replaced (6.3 step 6, 6.4, 6.8, 6.9) |
| Unparsed report summary, "w-xxxx is not progressing" | 6.3 | Relaying the user's answer with send |
| Report blockers recorded as free text before session-supervisor 0.27.0 | 6.3 (old wording) | The user mentioning it in this chat (e.g. `T-xxxx UA-xxxx (...): ...`); see "Old blocker wording" below |
| Failed verification of a `done` report | 6.3 step 5 | Relaying the user's instruction, or a later report that verifies |
| Push decision | 6.3 step 6, 6.4 | The user's decision in 6.3 step 7: push worker assigned (reused or started), new task created, or no push |
| "Open repo `<path>` once in the app, then tell me" | 6.2 | The worker starting successfully |
| Archive refused ("Close w-xxxx from the screen or unpin it", "Please archive w-xxxx") | 6.3 step 6, 6.4, 6.6 | Reconciliation observing `archived` |
| "Answer the pending approval of w-xxxx in the app" | 6.4 | Reconciliation observing the worker no longer `needs_input` |
| Replacement limit reached (6.8): "Replacement workers for w-xxxx in `<cwd>` went missing or unverified 3 times: ..." | 6.8 (from 6.4) | The user's instruction carried out (new worker, recorded result, or cancel) |
| "Tell w-xxxx in the app: ..." (send unavailable) | Degraded modes | The user confirming it |
| Follow-up (has `follow_up`; wording from a `## Follow-ups` rule, e.g. "Review repo-a#42") | Section 4 step 6 (6.3 step 6, 6.3 step 7 "Verification", 6.4) | `done: pr_closed` only: the `pr-state` observer of 6.4 reporting `MERGED` or `CLOSED` for its url. Any follow-up: the user naming it as done, e.g. `T-0003 UA-0003 ("Review repo-a#42"): done` (other wording such as "完了" counts too). Check that the named user action exists in that task and has `follow_up`, and that a quoted description matches the start of its description; if not, ask the user which one they mean |
| Task: "Hub wait w-xxxx (T-xxxx `<worker title>`): no answer from the P-xxxx project manager for 30 minutes: `<question>`" | 6.4 (`waits`, kind `hub`) | The worker's next report processed in 6.3, `replaced_by`, or cancel |
| Task: "Review wait w-xxxx (T-xxxx `<worker title>`): the P-xxxx project manager has not reviewed R-NNNN for 2 hours; ask it in its session, or tell me to skip the review" | 6.4 (`waits`, kind `review`) | `review.state` becoming `approved`, `rejected`, `waived`, or `superseded`, a new review id, `replaced_by`, or cancel |
| Task: "Read the result of w-xxxx (T-xxxx <worker title>) in its session, then tell me or the worker that you have read it" | 6.3 step 6 (Investigation), 6.11 `approve_ok` | Archives the worker: the user saying in this chat that they read it (matched as for a follow-up, under the preconditions of 6.3 step 6 "Investigation"), a verified `done` with `read_confirmed: true`, or the held worker archived outside the supervisor or missing (6.4). Removed without archiving when the worker goes back to work or review: a new review (6.3 step 5), `redo_send` (6.11), or a follow-up (6.7 (a)). Cancel |
| Project: "Approve new task proposal of P-xxxx (`<name>`): `<title>`" (with `proposal`) | 6.11 (`proposal_add`) | The user's approval in this chat (the task is created in the same candidate, 6.1) or rejection |
| Project: "Answer the P-xxxx project manager in its session: `<question>`" | 6.11 step 4 | The manager's next report processed in 6.11 (updated by difference as in 6.3 step 4) |
| Project: "Approve the pending tool request of the P-xxxx project manager in its session" | 6.4 (`pending_approval`) | Reconciliation run with `--events` (the manager listed and not running) no longer reporting `pending_approval`; a run without `--events` keeps it |
| Project: "Session `<id>` was started by the P-xxxx project manager; check it in the app" | 6.4 (`foreign_session`) | Reconciliation no longer reporting that session (it reports only sessions that are not archived) |
| Project: "Project manager of P-xxxx disappeared 3 times: start another one or close the project?" | 6.12 | The user's instruction carried out |
| Project: "Please archive the previous P-xxxx manager session in the app" | 6.4 (`stale_previous`) | Reconciliation observing that session `archived` |
| Project: "Pass review R-NNNN to the P-xxxx project manager in its session: `<review request>`" (send unavailable) | Degraded modes | `review.state` leaving `requested`, or cancel |
| Any action of a task | any | Cancelling the task (6.6) |
| Any action of a project | any | Closing the project (6.10) |

Old blocker wording: when the user mentions a user action of a task in this chat and it may be a free-text report blocker recorded before 0.27.0, take the task's live workers (`intended_state` not `archived`, `observed_state` neither `archived` nor `missing`) whose `last_report.status` is `needs_input`, `blocked`, or `failed`. Read each one's message `last_report.message_ref` again and run `parse-report` on it. The user action is an old blocker only when its description equals one of those `blockers`; otherwise it belongs to another row (an unparsed summary, a failed verification).

- One matching worker: in one candidate, replace the user action with that worker's guidance (6.3 step 4; a new id). Send nothing. Reply with the user's words quoted and point them to that worker's session.
- No live worker: remove the user action and tell the user.
- Two or more matching workers: ask the user which worker the question came from.

### 6. Procedures

#### 6.0 Observation and investigation

Every fact from outside `<dir>` reaches the supervisor as a result file written by an observer subagent: repository candidates (`locate`, 6.1), repository kind (6.1), base branch (6.2), verification (6.3), repo-side checks (6.4), PR states of follow-ups (`pr-state`, 6.4), the PR state of a branch (`pr-state` branch mode, 6.3 step 7 and 6.7), and answers to the user's questions about repositories or files.

1. Fill the brief in [references/observer-brief.md](references/observer-brief.md) with the purpose, the target, and the result path, and run it with the subagent capability on model `haiku`, for every purpose.
2. Result path: `<dir>/.supervisor/observations/<purpose>-<id>.json`, where `<id>` is the worker id (the task id for `kind` and `locate`; for `pr-state`, the fixed value `follow-ups` in its follow-ups mode and, in its branch mode, the id of the worker being started or of the worker that pushes, so it never overwrites `pr-state-follow-ups.json`). The name is fixed per purpose and worker or task, and each run overwrites it. A user question uses `<dir>/.supervisor/observations/question-<UTC timestamp>.md`.
3. The subagent returns only the path. Read the file and decide yourself: the observer reports facts, the calling procedure holds the rules. A missing file, a malformed file, or a `target` that does not match counts as not observed: run the observer once more, and if that fails too, do not verify or archive and tell the user. Exception for `pr-state` (6.4): if the subagent cannot be launched, defer to the next user turn without telling the user; if it was launched but the result is still missing or malformed after the second run, change no user action and tell the user once per conversation.
4. Questions and investigations about code or files go to an observer with purpose `question`; relay its answer from the result file. Never read target files yourself, not even as a fallback.
5. If a subagent cannot be launched in the current turn, defer (section 8).

#### 6.1 Task intake

0. First decide with 6.7 whether the message continues an existing task, and handle a continuation there. A message that only asks to push or open a PR for an existing branch goes to 6.3 step 7. Continue here only for a new task; a task created by 6.3 step 7 ("Archived stub") skips this step.

   Project: set `project_id` when the user names a P-id or a project name of an open project, or when the task is created from a proposal the user approved. When you cannot tell, set none (the task behaves as before). For an approved proposal, in one candidate: `request` = the user's approval message verbatim, `proposal` = the user action's `proposal` plus `from` = the P-id, and remove the proposal user action.
1. Decide each target directory yourself and tell the user the chosen directory in one line; never ask them to confirm it. Get the expanded roots first (read-only):

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" prepare-dir --claude-md "<dir>/CLAUDE.md" --show
   ```

   It prints `clone_root: <abs>` and `work_root: <abs>` (`(default)` when the key is missing). A task with `derived_from` first tries the `cwd` of the original task's workers (6.7, "Derived from an investigation"). Then take the first rule that applies:

   1. An explicit absolute path: use it when it exists (run the `kind` observer of step 4 for it now and read `exists`). When it does not exist and the request asks to create something new, use a work directory (rule 4). Otherwise go to rule 5.
   2. A URL or `owner/repo`: `<clone_root>/<host>/<owner>/<repo>` (host `github.com` for `owner/repo`). Clone it when it is not there.
   3. A repository name only: run one observer (6.0, purpose `locate`, target `clone_root` from `--show`, `names`, and `owner` when known) and pick from its candidates. With several, prefer the one that appears most recently in the `cwd` of a worker in `tasks` or in the `ref` (`<repo>#<branch>`) of a stub's `branch` artifact; then one under `<clone_root>/github.com/`. With none, clone the URL that `gh repo view` reported: `sshUrl` when `git_protocol` is `ssh`, otherwise `url`.
   4. A request to create something new or to investigate that names no repository: a work directory `<work_root>/T-NNNN-<slug>` (the new task id and a short lowercase slug). It becomes a `non-git` worker with `files` delivery, checked with `check-cwd` before the start (6.2 step 1).
   5. Terminal: a repository name for which no URL was found, or a non-existent absolute path when the request is not to create something new. Create no task, record nothing in the state, and report "Cannot resolve the target of this request: ..." in one line.

   Clone or create with `prepare-dir`, the only write outside `<dir>` (section 1). Run `--clone` with a Bash timeout of 360000 ms, longer than its internal 300 seconds, so that the script cleans up a failed clone itself. It prints `exists:` when the destination is already a clone of the same origin. On exit 1, report its `error:` lines and see Degraded modes.

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" prepare-dir --claude-md "<dir>/CLAUDE.md" --clone "https://github.com/org/repo-a"
   python3 "<dir>/.supervisor/state_tool.py" prepare-dir --claude-md "<dir>/CLAUDE.md" --work "T-0007-release-notes"
   ```

2. Copy the request verbatim into `request`.
3. Split it by directory: one task, one worker per directory. When one message both continues tasks and asks for something new, append the same verbatim message to each continued task (6.7) and make the new part a new task whose `request` is that message.
4. Determine the kind of each directory with one observer (6.0, purpose `kind`, all directories of the task), after `prepare-dir` has created them. For each directory it reports `kind` and `repo_root`: `git` means the worker is `git` and `cwd` becomes `repo_root`; `jj-only` (`.jj` present, `git rev-parse` fails) means a non-colocated jj repo: out of scope, tell the user; `non-git` means a `non-git` worker.
5. Set `investigation: true` on the task when the request only asks to look into, check, compare, or explain something, and asks for no change, commit, result file, or PR. When you cannot tell, do not set it. It is set only here and never changes while the task lives:
   - "Find out why the retry does not work" → set
   - "Find the cause and fix it" → not set
   - "Make a comparison table and leave it there" → not set
6. Record push consent only when the request contains explicit wording, copying the exact phrase into `push_consent.text` (it must be a substring of `request`). A negated phrase is not consent even though it contains the words:
   - "README を直して。push して PR を作ってよい" → consent, text `push して PR を作ってよい`, delivery `push_pr`
   - "Fix the typo and open a PR" → consent, text `open a PR`, delivery `push_pr`
   - "Fix the typo. Do not open a PR" → no consent, delivery `commit`
   - "LICENSE の年を直して" → no consent, delivery `commit` (push becomes a user action later)
7. Set `delivery` per worker: `push_pr` (git with consent), `commit` (git without), `files` (non-git).
8. Add the task and its workers with `intended_state: pending`, `observed_state: none`. Run the state-change set.
9. Launch (6.2) in the same turn, since this turn carries the user's words.

#### 6.2 Launch

Start only in a turn that carries the user's own words. A start refused in an event-triggered turn is not retried; the task stays `waiting` and is launched on the user's next message.

1. Non-git: check the directory first. It is busy when an occupying worker's `cwd` is the same directory, an ancestor, or a descendant. If it prints busy, do not start; the task derives as `waiting`.

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" check-cwd --state "<dir>/state.json" --cwd "<cwd>"
   ```

2. Build `prompt` and `background` from [references/worker-prompt.md](references/worker-prompt.md). For a worker of a project task, fill the project, proposal, and redo fields as well. A worker of a task with `investigation` gets the `{investigation only: ...}` rules, except a push-only worker. A worker of a task with `derived_from` gets the `{derived from an investigation only: ...}` block.
3. Start the worker with: `prompt`, `background`, `cwd`, worktree on (git only), `branch_name` (a slug from the task), `name` (the handle, e.g. `t0003-w0004`), `title` (6.2.2), fresh context, `permission_mode` auto (never above the supervisor's), `model` as 6.2.1 decides, immediate start. Pass `source_branch` only when the user named one, for a push-only worker (6.3 step 7), where it is the pushed branch, for a follow-up worker (6.7), as its start table decides, or for a replacement worker (6.8), as 6.8 decides.
4. If start is refused because the repo was never opened in the app, do not retry without a worktree. Add the user action "Open repo `<path>` once in the app, then tell me" (or ask the user to press "Trust repository" if shown) and launch again on the user's next message.
5. On success, record the returned id as `session_id` and your own id as `started_by`. Call get and fill `branch`, `worktree_path`, `model` (6.2.1), `observed_state` (normally `running`), and `observed_at`. For git, set `base_branch` to the get result's `sourceBranch`; if it is absent, ask an observer (6.0, purpose `base`) for the remote default branch (`refs/remotes/origin/HEAD`, which works in jj-colocated repos whose HEAD is detached; never the `HEAD` symbolic ref). A push-only worker (6.3 step 7) keeps `branch` = the pushed branch instead of get's branch, and keeps the `base_branch` copied from the original worker; a push worker of the "Archived stub" path of 6.3 step 7 (or its fallback) uses the observer's remote default branch, never get's `sourceBranch`. This does not apply to a follow-up worker of 6.7 (c). A follow-up worker (6.7) uses get's `sourceBranch` as usual; the push-time rule of 6.3 step 7 reads it later. A replacement worker (6.8), like a push-only worker, keeps the `base_branch` inherited from the worker it replaces and ignores get's `sourceBranch`, so that `commit_count` still counts the commits the old worker made before it disappeared. Set `intended_state: running` and run the state-change set.
6. For a worker of a project task, send the project manager a Roster update ([references/manager-prompt.md](references/manager-prompt.md)) in the same turn.
7. If periodic reconciliation is not running, start it (6.4).

#### 6.2.1 Worker model

Choose the family from the brief type of the worker being started. Every worker starts on opus, whatever its work looks like at the start, because work that looks simple can grow complex as the worker digs into it. The only exception is the push-only worker, whose work stays routine.

| Family | Worker |
| :--- | :--- |
| `opus` | Every worker except push-only: normal, follow-up (6.7), and replacement (6.8) workers whose inherited brief type is not push-only |
| `haiku` | Push-only worker (6.3 step 7), including a replacement (6.8) of one |

- **Passing it**: for `opus`, omit `model`; the worker inherits this supervisor's model. For `haiku`, pass a model id of that family, taken from the first that applies: an id the user gave in this supervisor session (see Mismatch); a model id of that family stated in your own environment information; else the family name itself.
- **Recording it**: in 6.2 step 5, set `model` to the family (`opus`, `sonnet`, or `haiku`) named in the `model` that get returns, not the one you chose. If get returns no `model`, or it names none of the three, leave `model` unset.
- **Mismatch** (only when you passed `haiku`): if get reports another family, the start ignored the id. Let the worker continue; do not restart it or change its model. Tell the user once per supervisor session: "Could not start a `haiku` worker; tell me the `haiku` model id shown in the app's model picker." Until the user answers, omit `model` for `haiku` in this supervisor session.

#### 6.2.2 Worker title

- **Setting it**: every worker gets a `title` in the candidate that adds it, whichever section adds it (for example 6.1 step 8, 6.3 step 7, 6.7 (b) and (c), 6.8, 6.9): a short phrase of 52 characters or fewer saying what this worker does, e.g. `uploader retry (repo-a)`. A push-only worker uses `push <branch>`, shortening the branch part if needed. A replacement worker (6.8) copies the old worker's `title`. If a worker has no `title` when 6.2 launches it, set one in a state-change set before the start.
- **Session title**: pass `title` = `<prefix> <worker title>`, where the prefix joins the ids with `|`: `<task id>|<worker id>` for a task outside projects, e.g. `T-0003|w-0004 uploader retry (repo-a)`, and `<project id>|<task id>|<worker id>` for a project task, e.g. `P-0001|T-0020|w-0020 API client`. The prefix is never shortened; shorten `workers[].title` itself.
- **Retitle on reuse**: when an existing worker gets new work without a new start, retitle it only when its `title` no longer describes that work. 6.3 step 7 Reuse always retitles to `push <branch>`. 6.7 (a) retitles only when the follow-up asks for a different deliverable than its `title` says; a fix or addition to the same deliverable keeps the title. To retitle, set the new `title` in the same candidate as the reuse, run the state-change set, then call rename once with `<prefix> <new title>` (same prefix as above).
- **Rename outcome**: if rename is unavailable, declines, or the app asks the user to approve (it does for a title the user set themselves), do not retry and keep the new `title` in state. When it was unavailable or declined, tell the user in one line: "Could not rename w-xxxx; its sidebar title is still the old one". Add no user action.

#### 6.3 Turn-end handling

Called only with workers that reconciliation (6.4) found changed; on an automatic continuation, run 6.4 first and let it hand workers here. 6.3 never calls 6.4. For each such worker:

1. Read the worker's latest events in full (about 12) and save only the last assistant message to `<dir>/.supervisor/last-message.txt`. If its message id equals `last_report.message_ref`, the turn is already processed; stop. Otherwise parse it:

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" parse-report --file "<dir>/.supervisor/last-message.txt" --expect-task T-0003 --expect-worker w-0004
   ```

   Whatever the result, the candidate that records the new `last_report` also removes this worker's guidance user actions (those starting with `Answer <worker id> (`); step 4 updates them by difference instead. This holds when verification of a `done` report is deferred (section 8) too. A report's `read_confirmed: true` is recorded in `last_report` as well, so the mark survives a deferred verification or a pending review.

   A worker of a project task (its task has `project_id`):
   - Run `parse-report` with `--allow-awaiting-manager`; without it `awaiting_manager` is an invalid status.
   - The candidate that records the new `last_report` also removes the worker's user action starting with `Hub wait <worker id> (`, and removes `awaiting_since` unless the new status is `awaiting_manager`.
   - `review.state` is `requested` and the new status is anything but `done` (`unparsed` included): in the same candidate set `review.state: superseded`, keeping its id and `message_ref`, remove `pending_verdict`, and remove the worker's Review wait user action. The next `done` gets a new review id (step 5).
   - `intended_state: archived` with `review.state: approved` (approved, archive pending): process the new report, whatever path handed it here (6.4 "Listed as active" included), only in the same candidate as the rollback of 6.4. `commit` rejects a candidate that records the report alone.

2. Parse failure (`"ok": false`, including a field of the wrong type): set `last_report` to `{message_ref, status: "unparsed", error, at}`. If the previous `last_report.status` was not `unparsed`, send the worker one request to resubmit the report block. If it was already `unparsed`, read the message yourself, summarize it into a user action, and do not archive.
3. `in_progress`: if `summary` and `next_action` differ from the previous `last_report`, set `worker.continues` to 0. If `continues` is below 3, send "continue" and increment it; otherwise add the user action "w-xxxx is not progressing" and send nothing. A worker that is not progressing is never replaced automatically (6.8 is only for missing and unverified workers). Update `last_report`. Do not let rewording defeat the cap: once you have sent 3 nudges in total while the worker is on the same step of the task, add that user action even if the wording changed.

   `awaiting_manager` (project workers only): the worker sent its questions in `blockers` to the project manager. Update `last_report`, set `continues` to 0, and set `awaiting_since` to now, keeping the previous value when the previous report was also `awaiting_manager` with the same `blockers`. Send nothing and add no user action; 6.4 (`waits`) reports a wait that lasts too long.
4. `needs_input` / `blocked` / `failed`: the worker asked the user in its own session, and the user answers there. The supervisor only points the user to that session and never relays the answer.
   - Guidance: for each entry of `blockers`, the user action "Answer w-xxxx (T-xxxx <worker title>) in the app: <blocker>", with `<worker title>` = `workers[].title`. A `failed` report with empty `blockers` gets one guidance built from `summary`.
   - Update by difference: compare the worker's existing guidance (description starting with `Answer <worker id> (`) with the guidance built from this report. Keep each one whose description is identical, so its id and `since` stay. Remove the other existing ones and add only the new ones.
   - Send the worker nothing about its blockers. In this turn's reply, link the session as `[T-xxxx <title>](#local_<session_id>)`, add its sidebar title `T-xxxx <title>` (6.2.2) in case the link does not render, and tell the user to answer there.
   - When the user answers a blocker in this chat, do not send it either: reply with the same link and title only.
   - The answer reaches the supervisor only through the worker's next report, found by an automatic continuation or by periodic reconciliation (about 5 minutes). Guidance and "Answer the pending approval of w-xxxx in the app" may exist for the same worker at once; each resolves by its own rule, so do not fix them by hand.
   - The guidance of a worker on a push brief (title `push <branch>`) contains `push` and its branch, so `commit` also treats it as a push decision. This is harmless: a push decision is resolved only with a consent `message`, which only 6.3 step 7 writes.
5. `done`: verify through an observer (6.0): purpose `verify-git` for a git worker (target: `worktree_path`, `branch`, `base_branch`, the reported `pr`, and for a push worker of 6.3 step 7 `expected_sha` = `{commit}`), purpose `verify-files` for a non-git worker (target: `cwd` and the reported files). Apply these rules to its facts:
   - `porcelain_empty` is true (untracked files included).
   - `commit_count` (`base_branch..HEAD`) is 1 or more, or the result is No result (below).
   - `expected_sha`, when given: `head` equals it.
   - `push_pr`: `remote_sha` (of `refs/heads/<branch>`) equals `head`, and an existing PR's `pr.headRefOid` equals `head`. Skip this rule for No result: there is nothing to push.
   - `commit`: a pushed branch or a PR is not a failure, since the user may have told the worker to push in its session. When `pr` is not null, `pr.headRefOid` equals `head` (outside the Merged PR rule).
   - Merged PR: if `pr.state` is `MERGED` and `pr.headRefOid` equals the recorded `commit` artifact (or `head`), the result is verified; ignore `remote_sha` and `commit_count`, since the head branch may have been auto-deleted and the count against an updated base may be 0. This rule does not apply to a follow-up worker (6.7) or to a replacement worker started with `source_branch` (6.8): their branch starts at a branch that may already carry a merged PR.
   - non-git: every entry of `files_exist` is true.
   - No result: the worker finished without keeping anything (typically after the user said in its session that the work is complete; the supervisor does not check why). git: no `expected_sha` was given (never for a push-only worker), `porcelain_empty` is true, `commit_count` is 0 with `commit_count_basis` `base` (a 0 counted against `not-remotes` may mean everything was pushed, so it fails as usual), `pr` is null, and the Merged PR rule did not apply; any delivery. non-git: the report's `files` is empty. A No result is verified; step 6 records it without artifacts.
   - Any failed check becomes a user action with the reason; do not archive.
   - Project task, unless the worker's `review` is `waived` (a push-only worker, or the user waived it): once verified, request a review instead of step 6. In one candidate:
     - git: record the artifact group (`branch` + `commit` without `url`), replacing an existing group of the same worker in place. non-git: record the `file` artifacts, replacing in place the existing `file` artifacts under the worker's `cwd`. No result: record no group.
     - Record no `pr` artifact, follow-ups, push decision, or `no_result` yet; they wait for the approval.
     - `review`: for git, when the current `review` is `requested` with the same `ref` (the verified head), set its `message_ref` to this report's and `at` to now, and remove `stale_notified` and `pending_verdict`, keeping the id. Otherwise raise the project's `last_review_number` by 1 and set `review = {id: R-NNNN, state: requested, at, message_ref, ref}`, with `ref` the verified head (git) or the reported files (non-git). Every non-git `done` gets a new id.
     - Keep the worker `intended_state: running`.
     - A held worker (`approved`, with a read user action, step 6 "Investigation"), whether or not the report has `read_confirmed`: give it a new review id as above, and remove its read user action and its `no_result` entry. Keep `findings` (the approved conclusion); the next `approve_ok` updates them.
   - After the commit, send the project manager a Review request ([references/manager-prompt.md](references/manager-prompt.md)), even when the id is unchanged. When the new review's `ref` equals the `ref` of the worker's previous `rejected` review, say "No change since R-NNNN" in it.
6. Request the archive: record artifacts from observed values (mapping in [references/state-schema.md](references/state-schema.md)). For each git worker other than a push worker (whose artifacts are recorded in "Verification" of step 7), append one group before its archive: its own branch as `branch`, followed by its verified head as `commit`. A worker brought back by 6.7 (a) usually already has a group; replace that group in place instead of appending a second one (one brought back after a No result has none, so append as usual). For `commit` delivery, the commit is pushed when the observer reported `remote_sha` equal to `head` or a `pr` whose `headRefOid` equals `head`. Then give that group's `commit` artifact `url` = `<web_url>/commit/<head>` (`web_url` from the same observation; when `web_url` is null, treat it as not pushed), append the `pr` artifact when there is one after the group, never between its `branch` and `commit` (its `## Follow-ups` apply, section 4), and add no push decision. Otherwise the `commit` artifact has no `url` (remove it when replacing a group in place) and you put the user action "Decide whether to push branch `<branch>` of `<repo>` (and whether to open a PR)" in the **same candidate**. Remove the worker's guidance (6.3 step 4) in that candidate. Set `intended_state: archived` and run the state-change set. Then call archive with a `reason` naming the task id, the verified result, and that it relies on the recorded `archive_consent`.
   - No result (step 5): record no artifact (`branch`, `commit`, `pr`, `file`) and no push decision. Instead, in the same candidate that sets `intended_state: archived` and removes the worker's guidance, append `{worker_id, summary, at}` to the task's `no_result`, with `summary` copied as is from the report's `summary` recorded in `last_report` and `at` the current time. The archive `reason` says "no result". The dashboard shows the summary in the archived list once the task is archived.
   - Success: set `observed_state: archived` and run the state-change set. When no worker of the task is active any more, the task moves to `archived` in that commit (a task with a user action stays `needs_user`).
   - Refused because mid-turn or background work: do nothing; reconciliation retries.
   - Refused because pinned, shown on screen, or approval denied: add a user action with the reason right away (e.g. "Close w-0004 from the screen or unpin it").
   - Project task, unless the worker's `review` is `waived`: this step runs in the approval candidate of 6.11 (`approve_ok`), not after step 5.
     - Reuse an observation whose `head` (`branch_head` for `repo-side`) equals `review.ref`: `<dir>/.supervisor/observations/verify-git-<wid>.json` or `repo-side-<wid>.json`, or `verify-files-<wid>.json` for non-git. Without one, observe again (6.0), with `repo-side` instead of `verify-git` when the worker is observed `archived` or `missing`.
     - When it must observe again and no subagent can be launched in this turn: in one candidate set `review.pending_verdict = {verdict: "approve", message_ref: review.message_ref, at}` (the state stays `requested`) and the task's `next_action` to `approve R-NNNN of w-xxxx on the next user message`. On the next user turn, if `pending_verdict.message_ref` still equals `review.message_ref`, observe and build the `approve_ok` candidate; otherwise drop `pending_verdict`. Any candidate that sets `superseded` or a new review id removes `pending_verdict` too.
     - Record the `commit` url, the `pr` artifact (only when no `pr` artifact with the same url exists) and its follow-ups (section 4 step 6), the push decision, or the `no_result` entry, as above, and set `intended_state: archived`. A worker already observed `archived` or `missing` is not archived again and needs no `archive_consent`.
   - **Investigation** (the task has `investigation`; never for a push-only worker): verification (step 5) is never skipped; only the archive waits until the user has read the result.
     - `findings`: in every candidate below that records a verified `done`, replace this worker's entry with `{worker_id, session_id, title, summary, message_ref, at}`: `title` = the sidebar title `<prefix> <workers[].title>` (6.2.2), `summary` = the report's `summary` copied as is (`No summary reported; read the session.` when empty), `message_ref` = the report's `message_ref`, `at` = now.
     - **Hold** (the verified report, i.e. `last_report`, has no `read_confirmed: true`): in one candidate, record the artifact group or the `no_result` entry as above (replace the worker's group in place; add or remove its `no_result` entry according to this verification), update `findings`, remove its guidance, and put the read user action "Read the result of w-xxxx (T-xxxx <worker title>) in its session, then tell me or the worker that you have read it" by difference (an existing identical one keeps its id and `since`). Add no push decision, keep `intended_state: running`, and call no archive; the task derives `needs_user`. A later unmarked `done` repeats this. In the reply, link the session as `[<sidebar title>](#local_<session_id>)`, add its sidebar title, and give the summary; for a non-git worker whose `cwd` is not a work directory of 6.1 rule 4, add "Other tasks in this directory wait until you have read it".
     - Only a worker observed `running`, `idle`, or `needs_input` is held. A worker observed `archived` or `missing` (e.g. `approve_ok` after an external archive) gets `findings` and is set `intended_state: archived` as usual; it counts as read.
     - **Release** (a verified `done` with `read_confirmed: true`, the first one included, or the user's confirmation in this chat): in one candidate do step 6 as usual (the artifacts or the `no_result` entry, and the push decision for an unpushed `commit`), update `findings`, remove the read user action and the guidance, and set `intended_state: archived`; then archive.
     - Failed verification: add the failed-verification user action as usual and keep the read user action; `findings` stay as last verified.
     - **Confirmation in this chat**: match the named user action as for a follow-up (section 5): it must be a read user action, and a quoted description must match the start of its description. When a free-text "read it" fits two or more held workers, ask which one. Release only when all of these hold: (1) the worker is not observed `running`; (2) the id of its latest message (event-reading capability) equals `last_report.message_ref`; (3) that id equals this worker's `findings` `message_ref`; (4) for a project task, `review.state` is `waived`, or `approved` with `review.message_ref` equal to it too. Reuse `verify-git-<wid>.json` or `verify-files-<wid>.json` when its `head` matches, otherwise observe again (6.0).
     - Otherwise decide in this order, and change no record in (ii) or (iii): (i) an unprocessed message or a deferred verification (`next_action` `verify w-xxxx ...`): process it with 6.3 first, and when it verifies, apply the confirmation in that candidate and release. (ii) The worker is observed `running`, or its project review is `requested`: do not release; reply that they should tell the worker in its session, or tell you again after the review. (iii) The last processed report is not `done` (`needs_input`, `in_progress`, ...), or the last `done` failed verification: do not release; reply that they should answer the worker's question in its session and tell it there that they have read the result, or close the session, which counts as read (6.4).
     - Project task: a `waived` worker follows the rules above as a non-project worker. For an `approved` worker, `approve_ok` (6.11) applies this branch using the `read_confirmed` of the approved report (`last_report`), and a later `done` goes to a new review (step 5).
7. Push decision. The supervisor never pushes or opens a PR; a worker does. A push-decision user action names one branch and is resolved only by the user's own words in this chat: a clear "no push", or consent with the same explicit wording as 6.1. A message that only asks to push or open a PR for an existing branch (an archived task's branch included) is handled here, never as a follow-up (6.7).
   - **PR base** `{pr_base_flag}` (in the brief of a reused or new push worker): decide it at push time from the `base_branch` of the worker that pushes, never by reading artifacts again. Let `b` be that `base_branch` without `origin/`. When `b` equals the remote default branch (`base_branch` of the `base` observer, `refs/remotes/origin/HEAD`), the flag is empty. Take the remote default branch from a `base` observation of `cwd` made in this turn (`<dir>/.supervisor/observations/base-<id of the pushing worker>.json`); when there is none, run one observer (6.0, purpose `base`, target `cwd`) before comparing. Otherwise run one observer (6.0, purpose `pr-state` branch mode, target `repo` = `cwd`, `branch` = `b`): `ok: true` with `state: OPEN` makes it ` --base <b>` (stacked on the open PR); any other state, or no PR, makes it empty, since the original PR may have been merged or closed after the follow-up started; `ok: false` makes it empty, and the reply that resolves the push decision adds one line: "could not confirm the state of the pull request for `<b>`; the new pull request targets the default branch".
   - **No push**: remove the user action in a candidate; nothing else changes.
   - **Consent**: set `push_consent` to `{text, at, message}`, where `message` is the user's message verbatim and `text` the exact consenting phrase inside it. `commit` accepts a new or changed `message` only in a candidate that removes a pending push-decision user action of that task, so remove only the action naming the consented branch (`commit` checks that a push decision of the task is removed, not which one). A later consent overwrites `message`.
   - **Expected sha** `{commit}`: the HEAD verified for that branch's original worker, i.e. the `ref` of the `commit` artifact recorded in the same candidate as that branch's `branch` artifact (adjacent entries, same repo label). It goes into the push brief and to the observer as `expected_sha`.
   - **Reuse** a worker of the same task with the same `cwd` and `branch` whose `observed_state` is neither `archived` nor `missing` (its archive was refused or is still pending; rare after a normal archive). In one candidate: set `push_consent`; set that worker's `delivery: push_pr`, `intended_state: running`, and `continues: 0`; remove the push-decision user action and any archive-refused user action of that worker; set its new `title` (6.2.2); for a project task, replace its `review` with `{id: <new R-id>, state: waived, at, reason: "push-only after R-NNNN"}` (raising `last_review_number`; R-NNNN is the approved review the push follows). Run the state-change set, then send it the push follow-up message ([references/worker-prompt.md](references/worker-prompt.md)) with `{pr_base_flag}` from its own `base_branch`. Its `model` stays as it is (nothing is started).
   - **New push worker** otherwise. In one candidate: set `push_consent`; add a worker to the same task with `kind: git`, `delivery: push_pr`, `cwd` = the repo root, `branch` = the original branch, `base_branch` copied from the original worker, `intended_state: pending`, `observed_state: none`, and for a project task `review = {id: <new R-id>, state: waived, at, reason: "push-only after R-NNNN"}` as for Reuse; remove the push-decision user action. Run the state-change set (the task derives `waiting`) and launch it with 6.2: the push-only worker brief with `{pr_base_flag}` from the copied `base_branch`, worktree on, `source_branch` = the original branch, and model `haiku` (6.2.1). The app creates a new branch for that worktree at the original branch's tip; it stays unused, so mention it to the user as a leftover branch.
   - **Consent while the worker is still running** (no push-decision user action exists yet): change no worker and no state, and tell the user the push follows verification. A held investigation worker (step 6) counts as running; its push decision is added in the release candidate. Once steps 5-6 add the push-decision user action, resolve it as above with the user's earlier message verbatim as `message`, as soon as that message is still in this conversation. In an event-triggered turn the new push worker is committed but launched on the user's next message (6.2). If the earlier message was lost (compaction, recovery), the user action waits for the user to answer again. If step 6 found the branch already pushed (no push decision added), there is nothing to resolve: record no `push_consent` and tell the user it was already pushed in the worker session.
   - **Archived stub** (the task already left `tasks`: an earlier "no push", a cancel, or a finished task): this stays a new task id, even for a stub that 6.7 (c) could revive. Create a new task via 6.1 with `request` = the user's message verbatim (the consent is in it, so no `message`). In the same candidate copy from the stub the `branch` artifact of the named branch and the `commit` artifact recorded with it; `{commit}` is that copied `ref`. Add one push worker as above, except that `base_branch` is the remote default branch from an observer (purpose `base`), never get's `sourceBranch` (here the pushed branch itself, which would make `commit_count` 0). Tell the user it became a new task id. If the stub is gone (only the 20 newest are kept) or lacks a `commit` artifact, tell the user and create the task via 6.1 without copied artifacts; `{commit}` is the `branch_head` reported by the `base` observer, confirmed with the user before the start. That worker starts exactly like the stub case, and its artifacts are recorded from observed values at verification (step 6).
   - **Review not needed**: when the user says in this chat that a project worker needs no review, and its review is absent or `requested` (a `rejected` result is redone, never waived), in one candidate set its `review` to `{id, state: waived, at, reason}` with the user's words as `reason` (a new id when it has none, raising `last_review_number`), and remove its Review wait user action. When its `done` was already verified (a review was requested), record step 6 in the same candidate, choosing the observation as for an approval; otherwise its next verified `done` goes to step 6 directly.
   - **Verification** of a push worker is step 5 with `verify-git` in its own `worktree_path`, `branch` = the original branch, and `expected_sha` = `{commit}`. Then record the branch `url` and `pr` artifacts, set `intended_state: archived`, and archive as in step 6. Never unarchive an old session.

#### 6.4 Reconciliation

When: at the start of every supervisor turn, right after the hook digest is injected, on every automatic continuation, and periodically.

Periodic reconciliation: while at least one worker is `running`, `idle`, or `needs_input`, keep a self-paced `/loop` (about every 5 minutes) that runs this procedure. Start it after a launch (6.2). Stop it once no such worker remains: in a self-paced loop, end the turn without scheduling the next one; if the loop was created with a fixed interval, delete its recurring job. A held investigation worker (6.3 step 6) counts as such a worker; when only held workers remain, schedule the next turn about 30 minutes later instead, so a `done` with `read_confirmed` is handled within about 30 minutes. When a `/loop` turn cannot launch a subagent and defers that verification (section 8), the next user turn handles it.

Call list with `include_archived: true` and `limit: 100`. Do not use `linked`. Match rows to `workers` by `session_id`, and evaluate the first rule before "Listed as active":

- Replaced worker listed as active (it has `replaced_by`, 6.8):
  - Successor still `pending`: in one candidate, remove the successor, remove `replaced_by` from the old worker, set it to `intended_state: running`, remove its `no_result` entry if any, and update its observed state from the list. In the same candidate, read its latest message again and run `parse-report` on it; if it reports `needs_input`, `blocked`, or `failed`, add its guidance again (6.3 step 4), because 6.3 step 1 stops on the unchanged `message_ref`. From then on handle it as a live worker.
  - Successor already started: send the old worker "You were replaced by w-xxxx. Stop now; do not commit, push, or open pull requests.", then call archive quoting `archive_consent`. On success record `observed_state: archived`.
  - A non-git old worker keeps its observed state until that archive succeeds, because two running workers would occupy the same directory. If archive is refused mid-turn, retry by this rule in every reconciliation.
  - A git old worker occupies nothing, so update its observed state as usual.
- Listed as active: update `observed_state` (`running` while running, otherwise `idle`) and `observed_at`. If the worker's latest events (event-reading capability) end in a pending permission request or an auto-mode classifier stop, set `needs_input` and add the user action "Answer the pending approval of w-xxxx in the app". Hand a non-running worker to 6.3.
- `intended_state: archived`, not yet observed archived, and not running: call archive again with the same `reason` and the refusal branching of 6.3 step 6. Do this regardless of `message_ref`, since a worker can finish background work without a new message.
  - Project worker with `review.state: approved`: first read its latest message id. When it equals `review.message_ref`, call archive as above. Otherwise (it ended a new turn after the approval) do not archive; roll back in one candidate and hand the new report to 6.3:
    - set `intended_state: running`, `continues: 0`, and `review.state: superseded` (a new `done` becomes a new `requested` review in the same candidate, 6.3 step 5);
    - remove the push decision and the `no_result` entry added at the approval (as in 6.7 (a)); keep the artifact group, which the next review request replaces in place;
    - when no subagent can be launched in this turn, commit only the rollback and the `last_report`, and set `next_action` to `verify w-xxxx on the next user message`; the new review id is allocated in that verification candidate.
  - Without this rollback in the same candidate, `commit` rejects the new report (approval gate).
  - Apply the same check when an approved project worker with `intended_state: archived` is listed as archived: if its latest message id differs from `review.message_ref`, roll back in the candidate that records `observed_state: archived` and hand the new report to 6.3 (verify with `repo-side`).
- Held investigation worker (a read user action names it, 6.3 step 6) listed as archived, or not listed and still not found by get: the user closed it or it vanished, which counts as read. This rule comes before the two rules below and their project branches.
  - In one candidate: set `observed_state` `archived` or `missing`, `intended_state: archived`, remove the read user action and its guidance, and add the push decision when its recorded `commit` artifact is not pushed (6.3 step 6). Do not verify again, do not process an unprocessed report, and never replace it with 6.8.
  - When an unprocessed report exists, say in one line that the last turn of w-xxxx was not verified and the dashboard keeps the previous conclusion.
  - Project task with `review.state: approved` whose `last_report.message_ref` or latest message id differs from `review.message_ref` (it reported after the approval): in the same candidate set `review` to `{id, state: waived, at, reason: "session closed while holding R-NNNN; later turns were not reviewed"}`, keeping its id. The recorded `findings` and artifacts are the approved ones, so nothing unreviewed is archived.
- Listed as archived while `intended_state` is not `archived` (the user or the app archived it, e.g. after a PR merge; a held investigation worker follows the rule above): set `observed_state: archived` and remove its guidance (6.3 step 4); the task derives `needs_user`. First, when its latest message (event-reading capability) holds a report not yet processed (its message id differs from `last_report.message_ref`) whose `parse-report` status is `done`, and for git its `worktree_path` still exists (the `verify-git` observer finds it), handle the worker with 6.3 steps 1-6 instead (pass `expected_sha` for a push worker), without the archive call and without `archive_consent`: in one candidate record `last_report`, remove its failed-verification user actions and guidance, record the No result or the artifacts, and set `intended_state: archived`. This keeps a user who closed the session right after declaring the work complete from getting a replacement. Otherwise (no such report, the worktree is gone, or step 5 finds neither a verified result nor a No result), verify the result on the repo side through an observer (6.0, purpose `repo-side` in `cwd`, since the worktree may be gone; non-git: `verify-files`). A merged PR whose head equals the recorded commit is enough (6.3 step 5, "Merged PR"). Otherwise `branch_exists` is true, `commit_count` (`base_branch..branch`) is 1 or more, and the PR / remote rules of 6.3 step 5 hold for `branch_head`; non-git: the reported files exist. If verified, record the artifacts (for `commit` delivery: when `remote_sha` or `pr.headRefOid` equals `branch_head`, the `commit` artifact `url` = `<web_url>/commit/<branch_head>`, any `pr` artifact appended after the group, and no push decision; when `web_url` is null, treat it as not pushed; otherwise the push-decision user action) and set `intended_state: archived` in one candidate; `archive_consent` is not needed for a worker already observed archived. Otherwise replace the worker with 6.8. When the user said in this chat that the work should stop, follow 6.6 or 6.9 instead.
  - Project worker: first compare its latest message id with `last_report.message_ref`, and hand an unprocessed report to 6.3 in the candidate that records the external archive (`requested` and not `done`: `superseded`; `done`: verify with `repo-side` and request a new review).
  - With `review.state` `requested`, `superseded`, or `rejected`: record `observed_state: archived`, remove its guidance, and keep the review going; do not verify again and do not replace it with 6.8. The task stays `needs_user`. When `rejected` and the worker has no `replaced_by` yet, add the redo successor as for `reject_ok` (6.11), once.
  - Without `review`: verify as above, but when verified, request a review (6.3 step 5) instead of setting `intended_state: archived`. Its `review.message_ref` is `last_report.message_ref`, or `external:<observed_at>` only when `last_report` is null. When not verified, replace it with 6.8 as above.
- Follow-ups with `done: pr_closed`: **only in the 6.4 at the start of a turn that begins with the user's message**, after matching the list, and only when at least one such user action exists. Never try this in an automatic continuation or a `/loop` turn; the `/loop` rule above does not change.
  - If `<dir>/.supervisor/observations/pr-state-follow-ups.json` has an `observed_at` within about 5 minutes and its `target.urls` is the same set as the current `pr_closed` follow-up urls, use it as is. Otherwise run one observer (6.0, purpose `pr-state`, target `urls` = all those urls).
  - Apply the result only to urls that are in its `target.urls` and still belong to a `pr_closed` follow-up; a different set alone is not a target mismatch and does not trigger a rerun. Remove every follow-up reported `MERGED` or `CLOSED` in one candidate.
  - `auth_ok: false`: tell the user once in this conversation that `gh` is not authenticated, and do not repeat it in later turns (nothing is recorded in the state).
  - A url whose `state` is `null` for another reason: mention it in one line of this turn's reply.
  - A missing or malformed result: follow 6.0 step 3 and its `pr-state` exception.
- Not listed (a held investigation worker: call get first, and only when it is still not found follow "Held investigation worker" above): a worker already intended `archived` without `replaced_by` has its result verified and its artifacts recorded (or its task was cancelled): set `missing` only if it is not observed `archived`, and never replace it (`commit` rejects `replaced_by` on it). Otherwise call get for that session. If still not found, set `missing`, remove its guidance (6.3 step 4), and replace the worker with 6.8 in the same candidate (when the user said in this chat that the work should stop, follow 6.6 or 6.9 instead). Never treat it as done. A `missing` worker still intended `running` keeps its task active; 6.8 sets it to `intended_state: archived`, which counts it as finished (no archive call, no `archive_consent` needed); for cancel, see 6.6. If a worker you gave up as `missing` (intended archived) shows up in the list again, do not just update its observed state: either set it back to `intended_state: running` (removing its `no_result` entry if any, and re-adding its guidance as in "Successor still pending" above when its latest message reports `needs_input`, `blocked`, or `failed`) and handle it as a live worker, or verify its deliverables through an observer (`repo-side` or `verify-files`) and archive it as in 6.3 step 6. A worker that has `replaced_by` follows the first rule above instead.
  - Project worker with `review.state` `requested`, `superseded`, or `rejected`: set `missing` and do not replace it with 6.8. When `rejected` without `replaced_by`, add the redo successor as above.
- Project managers: for each project, write the list rows to `<dir>/.supervisor/manager-rows.json`. When the manager is listed and not running, read its latest events (about 12) and write a summary of them to `<dir>/.supervisor/manager-events.json` as `{"last_message_ref": "<id of its latest assistant message, or null>", "pending_approval": <true when the events end in a pending permission request or an auto-mode classifier stop>}`; otherwise omit `--events`. Then run:

  ```bash
  python3 "<dir>/.supervisor/state_tool.py" manager-reconcile --state "<dir>/state.json" --project P-0001 --rows "<dir>/.supervisor/manager-rows.json" --events "<dir>/.supervisor/manager-events.json"
  ```

  Act on each decision it prints:
  - `update`: record the manager's `observed_state` and `observed_at`.
  - `handle_turn`: handle the manager's turn with 6.11.
  - `pending_approval`: add "Approve the pending tool request of the P-xxxx project manager in its session"; remove it once this decision is no longer printed.
  - `missing` (call get for that session first; act only if it is still not found) / `external_archive`: replace the manager with 6.12 (for a closed project, record the observed state only).
  - `stale_previous`: an id in `previous_session_ids` is still active; add "Please archive the previous P-xxxx manager session in the app".
  - `foreign_session`: a session was started by the manager; add "Session `<id>` was started by the P-xxxx project manager; check it in the app".
  - Never process a turn or a report of a session in `previous_session_ids`.
- Waits: run this every time and add, for each entry, the Hub wait (kind `hub`) or Review wait (kind `review`) user action of section 5 to the worker's task. With no entry, change nothing.

  ```bash
  python3 "<dir>/.supervisor/state_tool.py" waits --state "<dir>/state.json"
  ```

- Send the project manager a Roster update after a candidate in which one of its project's workers became `archived` or `missing`, or was replaced (6.8, 6.9).

Run the state-change set only when something differs.

In a turn that carries the user's words, after reconciliation, start every `pending` project manager (6.12), then launch every startable `pending` worker with 6.2, replacement workers first.

#### 6.5 Dashboard

1. First publish: publish `<dir>/dashboard.html` as-is with `icon: dashboard`. Do not load the artifact-design skill and do not edit or redesign the HTML; `state_tool.py` renders it from the bundled template. Record the URL in `supervisor.dashboard.url`.
2. Within the same conversation, republish the same file path whenever `dashboard_changed` is true.
3. After a recovery or a new supervisor session, read the artifact by `supervisor.dashboard.url`, then publish to that `url` so the link stays the same.
4. The displayed rev may lag behind `state.json`; that is acceptable.
5. If Artifacts are unavailable, give the user the local path `<dir>/dashboard.html` instead.
6. Each task card header and archived entry shows the task ID as a click-to-copy button (label `Copy T-0007`, keyboard-focusable, briefly shows `Copied`; falls back to selecting the ID when the clipboard is refused) that copies exactly the ID, so the user can paste it into chat. There are no separate Copy buttons.
7. A task whose only remaining user actions are follow-ups stays on the dashboard, together with its archived workers, until the last follow-up is removed.
8. With projects, the header holds a project index (one jump link per project and one for tasks outside projects, each with its task count); the header and each project heading stay fixed while scrolling.

#### 6.6 Cancel

When the user says a task should stop (e.g. "stop", "cancel", "やめて", "不要"):

1. `archive_consent` is required for any worker that is neither observed archived nor `missing`. If it is null, ask for it first (Capability check step 3); if the user declines, ask them to archive the task's workers in the app and cancel after reconciliation observes them archived (no consent is needed then).
2. In one candidate: set `task.cancel` to `{at, reason}` with the user's words as `reason`, set `intended_state: archived` on **every worker that has a `session_id`** (whatever its current intended state, `missing` workers included), and remove the task's user actions. Leave never-started workers as they are; `commit` drops them. A started worker left at `pending` would not hold the task, so it would be archived at once with its session still alive. Run the state-change set; no artifacts or push decision are needed for a cancelled task.
3. Archive each worker that is neither observed archived nor `missing` (a `missing` session cannot be archived; with `intended_state: archived` it already counts as finished), with a `reason` quoting the user's cancel and the recorded `archive_consent`, using the refusal branching of 6.3 step 6.
4. Once no worker of the task is running or awaiting an observed archive (a `missing` worker intended archived does not count), `commit` moves the task to `archived` with `cancelled: true` and the reason. Tell the user which branches remain in which repositories.
5. Project task: cancel needs no review (the approval gate does not apply). After the commit of step 2, send the project manager Worker finished ([references/manager-prompt.md](references/manager-prompt.md)).

#### 6.7 Follow-up requests

Decide from the content of each user message whether it continues an existing task (a task in `tasks` or an `archived` stub):

- Not a follow-up, evaluated before the follow-up rules: a message that only answers the question of a worker with guidance (6.3 step 4) or with an old-wording blocker (section 5), asking for no new work; reply with the guidance of 6.3 step 4 (or follow "Old blocker wording" in section 5) and send nothing. When a message mixes such an answer with new work, or you cannot tell, ask the user once whether to send it as a follow-up.
- Not a follow-up: an answer to a pending push-decision user action, and a message that only asks to push or open a PR for an existing branch (6.3 step 7).
- Not a follow-up: a message that only says the user has read the result of a held investigation worker; resolve it by section 5 (6.3 step 6, "Confirmation in this chat").
- Not a follow-up: a message that asks an investigation task (one with `investigation`) for a change, a commit, a result file, or a PR, e.g. "Now that the cause is known, fix it". Make it a new task derived from the investigation (below), whether the task would be (a), (b), or (c). A message that only asks for more investigation or a question stays a follow-up of the same task.
- A follow-up: a fix, addition, review response, or redo of the same deliverable; a mention of its T-ID, PR, branch, or files; a request on the same subject in the same directory as the previous one.
- Undecidable: a new task (6.1).

Append the follow-up as `{text, at}` to the task's `followups`, with `text` = the whole message verbatim. Never change `request`.

**Target directory**: add workers only for the directories the follow-up concerns (each a `cwd`, the absolute repo root for git). When a task spans several directories and you cannot tell which one the follow-up concerns, make it a new task (6.1).

**Original branch** (decided once, when the follow-up worker starts): for the target `cwd`, scan the `artifacts` of the task (of the stub in (c)) in array order and take the last `branch` artifact whose `ref` starts with `<cwd>#`. Revived stubs keep "old, then new" order, so this is always the newest generation.

- Original commit: the `commit` right after that `branch` (recorded in the same candidate, as for "Expected sha" in 6.3 step 7).
- Original PR: never read from the artifacts order (`pr` artifacts are appended later, so in a task spanning several repositories they can follow another repository's `branch`). Run one observer (6.0, purpose `pr-state` branch mode, target `repo` = `cwd`, `branch` = the original branch).

Start of a follow-up worker in (b) and (c):

| `pr-state` for the original branch | `source_branch` (6.2 step 3) | `base_branch` (6.2 step 5) |
| :--- | :--- | :--- |
| `ok: true`, `state: OPEN` | The original branch | get's `sourceBranch` = the original branch |
| `ok: true`, `state: MERGED` or `CLOSED` | Not passed (the default branch) | The default branch |
| `ok: true`, `state: null` (no PR) | The original branch when it exists locally (`branch_head` of the `base` observer), otherwise not passed | The original branch, or the default branch |
| `ok: false` (could not confirm) | Not passed (the default branch) | The default branch |

With no original branch for the target `cwd`, do not pass `source_branch`.

Route the follow-up by where the task is:

- **(a) A running worker** (observed `running`, `idle`, or `needs_input`) in the target directory: append the follow-up and send the Follow-up message ([references/worker-prompt.md](references/worker-prompt.md)). If the worker is `intended_state: archived` and its archive is not yet observed, set it back to `intended_state: running` with `continues: 0` in the same candidate, and remove its push-decision and archive-refused user actions (as for "Reuse" in 6.3 step 7) and its `no_result` entry if any (`commit` rejects one left on a running worker). An existing PR of its branch is updated as it is. Its `model` stays as it is (nothing is started). If its purpose changes, retitle it (6.2.2). For a held investigation worker (6.3 step 6), remove its read user action and its `no_result` entry in the same candidate; keep `findings`, which its next verified `done` updates.
- **(b) The task is in `tasks` and its workers have finished**: add a worker to the same task with the target directory's `kind` and `cwd`, `delivery: commit` for git (even when the original was `push_pr`) or `files` for non-git, `intended_state: pending`, `observed_state: none`, and start it with the table above and the follow-up brief; 6.2.1 starts it on opus. Leave the task's remaining follow-up user actions (e.g. "Review repo-a#42") as they are.
- **(c) An archived stub**: create a task with the same id, copying `title`, `request`, and `followups` from the stub and appending the new follow-up; `artifacts` empty and `push_consent` null. Add workers as in (b). Keep the stub; `commit` replaces it on the next archive. Make a new task (6.1) instead for a cancelled stub, a stub without `request`, or a stub no longer in `archived` (only the 20 newest are kept). When the stub's `project` names an open project, set `project_id` and copy `proposal`; otherwise revive it without `project_id`. Copy `investigation` when the stub has it.

**Derived from an investigation**: a new task (6.1) for a change requested of an investigation task.

- Record `derived_from: {task_id, context}` on it: `task_id` = the investigation task, `context` = its `request` and each `findings` `summary` with its sidebar title, copied when the task is created. Briefs (6.2, 6.8, after compaction) are rebuilt from this record alone, even after the original task or stub is gone. It is not copied to the stub.
- Target directory: when the message names no directory, take the `cwd` of the original task's workers before the rules of 6.1 step 1. When they have several `cwd`s and you cannot pick one, ask the user once. When the original `cwd` is a work directory of 6.1 rule 4 (not the repository investigated), do not take it and use 6.1 step 1; when that decides nothing, ask the user once.
- Branch: for git, pass no `source_branch` (start from the default branch). Pass the investigation worker's branch only when the user names it (6.2 step 3).
- Read and change in one message (e.g. "I read it. Fix the cause"): first resolve the read user action when the preconditions of 6.3 step 6 hold, then create the derived task, so a non-git `cwd` is freed first. When they do not hold, create only the derived task; when a held non-git worker occupies its `cwd`, tell the user it stays `waiting` until they say they have read the result of the original T-id.
- Reply with the new T-id and the original T-id.

A follow-up that asks for work and also consents to push: never record that consent in `push_consent` directly (`commit` rejects it). A running worker that receives it in the Follow-up message (6.7 (a)) may push on that wording itself; 6.3 step 6 then observes the push and adds no push decision. Otherwise resolve it at the push decision after verification, with that message verbatim as `message`, as in "Consent while the worker is still running" (6.3 step 7). If the message was lost, wait for the user's answer.

#### 6.8 Replacement workers

A worker that went missing (6.4 "Not listed") or was archived outside the supervisor without a verifiable result (6.4) is replaced without asking. A worker that is not progressing is not (6.3 step 3), and neither is a worker whose result was already verified, i.e. one already intended `archived` without `replaced_by` (its delivery artifacts are recorded, or its task was cancelled); `commit` rejects adding `replaced_by` to it.

1. Count `n` before marking the failed worker: the workers of the same task with the same `cwd` that have `replaced_by`, except those with `review.state: rejected` (replaced as a redo successor, 6.11).
2. `n` < 3: in one candidate,
   - set the old worker to `intended_state: archived` with `replaced_by: <new worker id>`;
   - remove the old worker's guidance (6.3 step 4); the new worker asks again in its own report;
   - add a new worker with `intended_state: pending`, `observed_state: none`, copying from the old worker `kind`, `cwd`, `delivery`, `base_branch`, `title`, and its brief type (normal, push-only, or follow-up); a push-only worker also copies `branch` (the pushed branch) and `{commit}`. Do not copy `model`: 6.2.1 chooses it again from the inherited brief type when the worker is launched;
   - its `source_branch`, decided when it is launched (6.2): the pushed branch for a push-only worker; otherwise the old worker's app branch when the `base` observer reports it exists (`branch_head`), else the same start condition as the old worker;
   - its brief gets the same `{investigation only: ...}` rules and `{derived from an investigation only: ...}` block as the old worker's, from the task's `investigation` and `derived_from`;
   - set the task's `next_action` to `start w-xxxx on the next user message` and add no user action.

   Run the state-change set. Launch it with 6.2 in the next turn that carries the user's words (6.4, last paragraph); it keeps the inherited `base_branch` (6.2 step 5).
   A redo successor (6.11 `reject_ok` for a worker observed `archived` or `missing`, and 6.4) is added the same way: it inherits the old worker's `base_branch`, its `source_branch` is the old worker's branch, and it gets the redo brief. Since the old commits count in `commit_count`, a redo without changes still verifies, and its Review request says "No change since R-NNNN" (6.3 step 5).
3. `n` >= 3: add the user action "Replacement workers for w-xxxx in `<cwd>` went missing or unverified 3 times: start a new worker, record the result, or cancel task T-xxxx?". A worker started on the user's instruction is also added with `replaced_by` on the old worker; if it fails again, ask again.

#### 6.9 Corrections

When the user corrects a delegation decision in this chat (the dashboard does not change):

- **Replace a worker**: use `replaced_by` as in 6.8 (the new `cwd` may differ). Archiving the old worker needs `archive_consent`. Remove the old worker's guidance (6.3 step 4) in the same candidate. Tell the user that its branch and any unpushed commits remain. A replacement in the same `cwd` counts toward the limit of 6.8. A project worker under review needs no approval when replaced (`replaced_by` exempts it); a later verdict for its review id is ignored (`plan-actions` reports it as `stale_silent`).
- **Move a follow-up**: remove it from one task's `followups` and append it to another task. Tell the original worker with send that the request is withdrawn, and tell the user that commits already made remain.
- **Split a follow-up**: remove it from `followups` and create a new task (6.1) whose `request` is its `text`.

#### 6.10 Projects

**Create** when the user asks for a project, naming it and its context document (a GitHub issue or a Notion page URL); without a URL, ask for it once.

1. Take `P-NNNN` = `supervisor.last_project_number` + 1 and a short lowercase slug, and create the project directory:

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" prepare-dir --claude-md "<dir>/CLAUDE.md" --project "P-0001-uploader-reliability"
   ```

2. In one candidate, raise `last_project_number` and add the project: `id`, `name`, `context_doc_url`, `dir`, `created_at`, `last_review_number: 0`, `user_actions: []`, and `manager` with `intended_state: pending`, `observed_state: none`, `handle` `pNNNN-manager`, `title` `P-NNNN <name>` (the project id and its name, e.g. `P-0001 Nature MCP`), `previous_session_ids: []`, `replacements: 0`. Run the state-change set.
3. In the same turn, start the manager: `prompt` and `background` from [references/manager-prompt.md](references/manager-prompt.md) (with the `roster` output), `cwd` = the project `dir`, no worktree, `name` = the handle, `title`, fresh context, `permission_mode` auto, no `model` (it runs on opus), immediate start. On success record `session_id`, `started_by`, `observed_state`, `observed_at`, and `model` (6.2.1) from get, set `intended_state: running`, and run the state-change set. The roster for the brief (and for every Roster update) comes from:

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" roster --state "<dir>/state.json" --project P-0001
   ```

**Talk to the manager**: the user talks to it in its own session. When a reply points the user there, link it as `[P-NNNN manager](#local_<session_id>)` and add its sidebar title `P-NNNN <name>`.

**Close** when the user says so in this chat. If a task still has its `project_id`, ask whether to cancel those tasks (6.6) or wait for them. Otherwise, in one candidate set `close = {at, reason}` with the user's words as `reason`, set `manager.intended_state: archived`, and remove the project's user actions; then archive the manager with a `reason` quoting the user's words. `commit` removes the project once the manager is finished and no task references it; its P-id is never reused.

#### 6.11 Manager turn-end handling

Called from 6.4 (`handle_turn`) for a project's current manager; never for a session in `previous_session_ids`. The manager never sends to the supervisor; its turn-end note comes to the session that started it.

1. Read the manager's latest events in full (about 12) and save only the last assistant message to `<dir>/.supervisor/last-message.txt`. If its id equals `manager.last_report.message_ref`, stop.
2. Parse it:

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" parse-project-report --file "<dir>/.supervisor/last-message.txt" --expect-project P-0001
   ```

   On failure, record `manager.last_report = {message_ref, status: "unparsed", error, at}`. If the previous status was not `unparsed`, send the manager the Resubmit message once; if it was, summarize the message for the user in this turn's reply. Stop here.
3. Call get for every worker named by a verdict in `reviews`, then map the report to actions, passing each worker that get showed running:

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" plan-actions --state "<dir>/state.json" --project P-0001 --file "<dir>/.supervisor/last-message.txt" --running w-0009
   ```

   Carry out each action it prints:
   - `approve_ok`: set `review.state: approved`, remove the worker's Review wait user action, and do 6.3 step 6 (its project branch) in the same candidate; archive in any turn. When 6.3 step 6 must observe again and cannot, hold the approval in `pending_verdict` as 6.3 step 6 says. For an investigation task, follow the "Investigation" branch of 6.3 step 6: archive when the approved report (`last_report`) has `read_confirmed: true`, otherwise hold the worker. Send a Roster update afterwards.
   - `reject_ok`: set `review.state: rejected` (`at` = now), remove its Review wait user action, and append `{text: <redo>, at, from: <P-id>, review_id}` to the task's `redos`. A live worker gets the Redo message after the commit. A worker observed `archived` or `missing`: in the same candidate, unless it already has `replaced_by`, set it `intended_state: archived` with `replaced_by` and add a redo successor with `intended_state: pending` (6.8, not counted toward its limit); set `next_action` to `start w-xxxx on the next user message` and add no user action.
   - `stale_notify`: record `review.stale_notified = review.message_ref` and send Stale verdict after the commit.
   - `stale_silent`, `redo_foreign`, `redo_dup`, `proposal_dup`: do nothing and send nothing.
   - `redo_send`: append the redo to `redos` and send the Redo message to the worker it names. When that worker is held (6.3 step 6), remove its read user action and its `no_result` entry in the same candidate.
   - `redo_ambiguous` / `redo_archived`: send the manager the matching Resubmit message.
   - `redo_start`: append the redo to `redos` and add a worker with the redo brief as in 6.7 (b), `intended_state: pending`; set `next_action` to `start w-xxxx on the next user message` and add no user action.
   - `proposal_add`: add the project user action "Approve new task proposal of P-xxxx (`<name>`): `<title>`" with `proposal = {title, text, message_ref}`.
4. Build "Answer the P-xxxx project manager in its session: `<question>`" for each entry of `questions` (none for `status: idle`) and update the project's user actions of that form by difference, as in 6.3 step 4, so an `idle` report removes them all. In the reply, link the manager as in 6.10.
5. Record `manager.last_report = {message_ref, status, summary, at}`. Steps 3-5 form one candidate: run the state-change set, then send the messages.

#### 6.12 Manager replacement

Called from 6.4 when a manager of an open project is `missing` or archived outside the supervisor.

- `replacements` below 3: in one candidate append the old `session_id` to `previous_session_ids`, set `manager.session_id: null`, `intended_state: pending`, `observed_state: none`, and raise `replacements` by 1. Start it in the next turn that carries the user's words, as in 6.10 step 3, with the current `roster` output (open reviews included). After the start, send every live worker of the project the Manager changed message; the open reviews are in the roster, so send no Review request for them.
- `replacements` 3 or more: add "Project manager of P-xxxx disappeared 3 times: start another one or close the project?".

### 7. Recovery (compact / resume / clear / re-creation)

The SessionStart hook runs `state_tool.py digest` and injects at most 10,000 characters: the state and dashboard paths, `rev`, the dashboard URL, `session_ids`, whether `archive_consent` exists, user actions (a follow-up ends with `[follow-up <rule> <done>]` and its PR url when the description does not contain it), one line per task, and the instruction to run the Capability check and reconciliation before acting. If the digest is missing (hook did not fire), run it yourself:

```bash
python3 "<dir>/.supervisor/state_tool.py" digest --state "<dir>/state.json"
```

Then:

1. Run the Capability check. A re-created supervisor gets its new session id with get `self` and adds it to `session_ids`.
2. Run reconciliation, project managers included (the digest has a `projects:` section). Workers and managers whose `startedBy` is an earlier supervisor id are taken over as they are.
3. If any worker is `running`, `idle`, or `needs_input`, restart periodic reconciliation.
4. If the current turn cannot start a session, commit what you detected and launch on the next user message.
5. Before building a brief (6.2 step 2) or a Follow-up message (6.7), read the task's `followups`, `redos`, and `proposal` from `state.json`.
6. Resend what a lost send may have dropped: one Roster update to each open project's manager, a Review request for each `requested` review, and the Redo message to each worker whose `review.state` is `rejected`, whose `last_report.message_ref` still equals `review.message_ref`, and that is observed `idle` or `needs_input` (a `running` one is working on the redo).

### 8. Degraded modes

| Situation | Fallback |
| :--- | :--- |
| Start unavailable entirely | Show the user the filled `prompt` and `background` and the title from 6.2.2 to set on it, ask them to start the session, and record the session id they give. Also tell them the model family 6.2.1 chose, to pick in the app's model menu; record `model` from get as 6.2.1 says. A replacement worker (6.8) is handled the same way, with the brief type it inherited. |
| Start refuses the call because of `model` (the result names `model` as the reason) | In the same user turn, retry once without `model` and record `model` from get. A `model` that start accepted but ignored is a mismatch (6.2.1). |
| `prepare-dir` fails (exit 1) | Create no worker for that directory and tell the user its `error:` lines. Causes include authentication, the network, the timeout, an origin mismatch, a clone stopped halfway (no `HEAD`; the user decides whether to delete it, never the supervisor), and a `core.sshCommand` wrapper that does not accept `-o`. |
| The target cannot be resolved (6.1 step 1, rule 5) | Create no task and report "Cannot resolve the target of this request: ..." in one line. |
| Archive unavailable or refused by a classifier | After verifying the result, add the user action "Please archive w-xxxx". |
| send does not reach the worker | Replace nudges and relays with the user action "Tell w-xxxx in the app: ..." (report blockers are answered in the worker session and never relayed). |
| send does not reach the project manager | Replace the Review request with the project user action "Pass review R-NNNN to the P-xxxx project manager in its session: ..." (section 5). |
| A project worker cannot send to the project manager | The worker asks the user with `needs_input` instead ([references/worker-prompt.md](references/worker-prompt.md)); 6.3 step 4 applies as usual. |
| Latest message unreadable or the fence is mangled | Ask the worker to resend the report as single-line JSON in the same fence. If that also fails, ask the user to paste the worker's final message. Never parse the turn-end note. |
| Neither `sourceBranch` nor `origin/HEAD` is available | Run `verify-git` with an empty `base_branch`; the observer then counts commits not on any remote (`commit_count_basis: not-remotes`), which must be 1 or more. |
| Subagent cannot be launched (for example refused in an automatic continuation or a `/loop` turn) | Do not verify and do not archive; never observe yourself. Set `next_action` to `verify w-xxxx on the next user message` and verify in the next turn that carries the user's words. For `pr-state`, defer to the start of the next user turn, leave `next_action` unchanged, and do not tell the user. |

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

- Every worker this supervisor started in this session was started with `title` = `<prefix> <workers[].title>` (6.2.2), and every worker in `workers` has a `title`.
- Every worker started in this session whose get returned a `model` has that family recorded as `model`.
- If this supervisor was not running on opus, the user was told once in this session.
- `python3 "<dir>/.supervisor/state_tool.py" version` matches the bundled script after setup.
- Every state change in this session went through `commit`, and `rev` increased each time.
- Every user action that was resolved in this session was removed in the candidate that recorded its resolution.
- Every archived worker had, before the archive call, the artifacts its delivery requires, a `no_result` entry of its own, or a cancelled task, and a non-null `archive_consent`.
- The supervisor did not edit or write outside `<dir>` (except Claude's memory and temp directories, and `prepare-dir` inside `## Locations`) and ran no mutating git / jj / gh command; the guard entry is present in `<dir>/.claude/settings.json`.
- No inferred target directory was put to the user for confirmation.
- Every `request` and `followups[].text` is the user's words verbatim and unchanged (except a correction of 6.9 removing a follow-up).
- The only user actions about delegation were "w-xxxx is not progressing" and the replacement limit of 6.8 (push decisions, archives, "Open repo" / Trust repository, pending approvals, report-blocker guidance, and failed verifications are not about delegation).
- No replacement or follow-up worker got a failed-verification user action.
- Every report blocker became a guidance user action "Answer w-xxxx (...) in the app: ...", and nothing about a blocker was sent to a worker.
- Every guidance was removed in the candidate that processed the worker's next report, or that set the worker archived, `missing`, or replaced.
- A blocker reported again with the same text kept the id and `since` of its existing guidance.
- Every push / PR was made by a worker, either with `push_consent` recorded from the user's words in this chat or on the user's own instruction in that worker's session (observed as in 6.3 step 6).
- The delete capability was never called, and the branches of archived workers still exist (`branch_exists` from a `repo-side` observer).
- The dashboard URL in `supervisor.dashboard.url` is unchanged after republishes and recoveries.
- While any worker is `running`, `idle`, or `needs_input`, periodic reconciliation is active; with none, it is stopped.
- Every `pr` artifact recorded in this session has the follow-up user actions its `## Follow-ups` rules require (none when the section is absent or invalid).
- A `pr_closed` follow-up whose PR was merged or closed is gone after the first user turn that followed the merge or close.
- Every project worker became `intended_state: archived` only after an approval of its latest report, `waived`, a cancel, or `replaced_by`; a worker that reported after its approval was rolled back instead of archived.
- No `awaiting_manager` report got a nudge or a user action, and Hub wait / Review wait user actions were added only for workers that `waits` printed.
- A Stale verdict was sent at most once per review id and `message_ref`.
- A new task proposed by a project manager was started only after the user approved it in this chat.
- No project manager started or archived a session (no `foreign_session` user action).
- Every worker of an investigation task (except push-only workers) became `intended_state: archived` only in a release candidate of 6.3 step 6 (a verified `done` with `read_confirmed: true`, the user's confirmation in this chat, `approve_ok` of such a report, or a worker observed `archived` or `missing`), or by cancel or `replaced_by`.
- Every dashboard link to a worker session (`findings`) was built from an observed `session_id`.
