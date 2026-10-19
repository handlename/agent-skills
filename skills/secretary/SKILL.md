---
name: secretary
description: Run a resident secretary session in the Claude desktop app that collects today's tasks and pending decisions from the session-supervisor state.json (read-only) and from external agents defined in the secretary directory's CLAUDE.md, tracks due dates in its own validated state.json, publishes one dashboard artifact ordered by due date, and relays the user's verbatim answers back to the right place (paste text for the supervisor, a relay session for external sources). It also sets up a secretary directory interactively when started in a directory that is not one yet. Triggers include "秘書として動いて", "秘書をセットアップして", "このディレクトリを秘書にして", "今日やることと判断待ちをまとめて", "判断待ちの期限を管理して", "act as my secretary", "set up a secretary here", "collect my todos and pending decisions", "run the secretary dashboard".
---

# Secretary

## Overview

One secretary session, opened in a dedicated non-git directory (the secretary directory, default `~/secretary`), gathers information from two kinds of sources:

1. the session-supervisor's `state.json`, read-only: every `tasks[].user_actions[]` entry becomes a pending decision, and
2. external agents listed under `## Sources` in `<dir>/CLAUDE.md`: for each source whose interval has expired, the secretary starts a fresh query session in that source's directory and ingests its `secretary-report` block.

It merges them into "today" items and "decisions" (pending decisions with optional due dates), publishes one dashboard artifact, and relays the user's answers:

- supervisor decisions are never sent to the supervisor. The secretary prepares paste text with the user's verbatim words (`paste`), or tells the user which app operation to perform (`app`);
- external decisions from a `relay: yes` source are relayed by starting a relay session in that source's directory (`start`); `relay: no` sources are display-only (`none`).

An item leaves the dashboard only when its source stops reporting it. The secretary adds due dates and relay records, nothing else.

Determinism lives in `scripts/state_tool.py` (Python 3 stdlib): `## Sources` parsing, validated atomic commits, merging, classification, due checks, dashboard rendering, the recovery digest, and report parsing. The secretary (the LLM) only operates session tools and interprets the user's words.

Reference files:

- [references/state-schema.md](references/state-schema.md): `state.json` fields, value ranges, the `relay_mode` classification table, relay badges, invariants, example
- [references/sources-format.md](references/sources-format.md): the `## Sources` format and its validation rules
- [references/agent-prompt.md](references/agent-prompt.md): the query and relay briefs (`prompt` and `background`) and the `secretary-report` block

In the commands below, `<skill-dir>` is the directory containing this SKILL.md and `<dir>` is the secretary directory.

How the secretary and the session-supervisor split responsibilities: [Session orchestration: supervisor and secretary](https://github.com/handlename/agent-skills#session-orchestration-supervisor-and-secretary).

## When to Use

- The user asks to set up or run a secretary (first run included). Setup runs interactively in a session opened in the directory to set up.
- The session runs in the Claude Code tab of the desktop app, in a secretary directory prepared by `setup`.
- The user wants one place that lists today's tasks and the decisions waiting on them, with due dates, across a session-supervisor and other agents.
- The SessionStart hook injected a secretary digest (after startup, resume, `/clear`, or compaction).

## Do Not Use When

- The session is a query or relay session (its brief says it was started by a secretary). Those sessions never start, archive, or delete sessions.
- The session is the session-supervisor or one of its workers. The secretary never writes to them.
- The environment is not the desktop app or lacks the start capability entirely. See Degraded modes before giving up.
- The user only wants to look at the supervisor's dashboard; open that artifact instead.

## Instructions

### 0. Entry

Decide which case applies before anything else:

- **This session's `cwd` has `state.json` and `.secretary/`**: it is the secretary. Continue with the Capability check (section 2).
- **Otherwise**: this session sets up a secretary directory and never acts as the secretary itself. The SessionStart hook that setup adds is not loaded into a session that is already running, and turn-end notes of sessions started from here would not reach a later session. Run the interactive setup (section 3) with `cwd` as the proposed secretary directory, then stop after its hand-off step.

### 1. Core rules

- `state.json` has a single writer: this secretary. Every state change goes through the state-change set (section 4) or a subcommand that commits internally (`merge-supervisor`, `ingest-report`). Never edit `state.json` directly.
- Never write the supervisor's `state.json`, its directory, or the session-supervisor skill files. `merge-supervisor` only opens the supervisor's `state.json` read-only.
- Never send a message to the supervisor session. A message from another session carries no user authority there, so consent and answers for the supervisor are always pasted by the user (`paste`) or done in the app (`app`).
- Never use the delete capability.
- Relays carry the user's words verbatim in `relays[].words`: the user's first message that stated the decision, never a summary and never a later "yes".
- Start sessions only in a turn that carries the user's own words. A `/loop` turn, an automatic continuation, or the result of a send never starts a session.
- `secretary.archive_consent` is the explicit user consent that the archive capability requires. It covers only query and relay sessions recorded in `sessions[]` whose `started_by` is in `secretary.session_ids`. Quote it in every archive `reason`.
- Writes outside the working directory (push, posts) happen only with the user's explicit consent. A relay session may write outside only when the relayed `words` explicitly allow it.
- Due dates are set only on the user's instruction (section 7). Merges never change `due`.

### 2. Capability check

Run at the start of every secretary session and after every recovery (section 10).

1. Confirm these capabilities are available. Tool names differ between builds; the names in the table are examples only. Use the equivalent tool you have.

   | Capability | Used for | Example name |
   | :--- | :--- | :--- |
   | Start a session (`prompt`, `background` of 80+ characters, `cwd`, no worktree, `name` handle, fresh context, `permission_mode`, immediate start) | Query and relay sessions | `start_session` |
   | List sessions (`limit`, `include_archived`, rows with `startedBy`, `name`, running / archived flags) | Reconciliation | `list_sessions` |
   | Get one session; `self` returns this session | Learning your own session id, checking a session missing from the list | `get_session`, e.g. `get_session("self")` |
   | Read a session's latest events in full | Reading the report block, detecting pending approvals | `list_events` |
   | Send a message to a session | Asking a query or relay session to resend its report (never the supervisor) | `send_message` |
   | Archive a session | Closing query and relay sessions | `archive_session` |
   | Recurring self-paced turns | Periodic reconciliation | `/loop` |
   | Publish / read an Artifact | Dashboard | `Artifact` |
   | Delete a session | **Never used** | `delete_session` |

2. Check the permission mode. The secretary assumes auto. If it is not auto, tell the user once that every archive will show an approval card, then continue.
3. If `secretary.archive_consent` is null, ask the user once: "May I archive the query and relay sessions I start, once their report is ingested? I never delete sessions." Record the verbatim answer as `archive_consent` with the state-change set. Without consent, every finished session stays `closing` and the dashboard asks "Please archive sec-S-xxxx".
4. Get your own session id with get `self`; the list capability never returns the calling session. If that id is not in `secretary.session_ids`, add it.
5. Compare the installed copy with the bundled copy:

   ```bash
   python3 "<dir>/.secretary/state_tool.py" version
   python3 "<skill-dir>/scripts/state_tool.py" version
   ```

   If the two outputs differ, re-run the `setup` command (section 3, version update).

Facts the procedures rely on (observed in the desktop app):

- **start is refused** in event-triggered turns (automatic continuation, the result of a send, a periodic `/loop` turn) and in turns triggered by another session's message. Only turns that carry the user's own words may start sessions. send and archive work in every turn.
- A session started by another session cannot start sessions in its first turn (that turn runs the task another session gave it). Query and relay sessions therefore never start sessions, and their briefs say so.
- A message from another session does not carry user authority: the supervisor received a relayed answer but did not record it as resolved and waited for the user's own confirmation. This is why supervisor decisions are `paste` or `app`.
- Archive is refused while the session is mid-turn, doing background work, pinned, or shown on screen. In auto mode archive completes immediately without an approval card.
- The turn-end note quotes only about 400 characters and replaces `"` with `'`, so it cannot be parsed. Read the full latest message with the event-reading capability; request about 12 events.
- Turn-end notes go only to the session that started the query or relay session. After the secretary is re-created, the new session receives none for sessions the previous one started. Reconciliation (6.2) covers this.

### 3. Setup

Two paths use this section:

- **Interactive setup** (from the Entry, section 0): the user opened a session in the directory they want as the secretary directory and asked to set it up. Follow steps 1–6 below.
- **Version update** (from the Capability check, step 5): only re-run the `setup` command of step 3 for `<dir>`; it overwrites the copied script and template and keeps everything else.

Ask one question per message, show the default or your suggestion with each, and accept the user's free-form answers. Never edit files outside `<dir>`.

1. **Secretary directory.** Propose `cwd` as `<dir>` and confirm it. The directory must not be inside a git repository (`git -C "<dir>" rev-parse --show-toplevel` succeeds) and must not be the supervisor directory, inside it, or contain it. If `cwd` breaks a rule, ask the user for another directory (default `~/secretary`); setup then creates it, and the hand-off (step 6) tells the user to open that directory instead.
2. **Supervisor directory.** Ask for the session-supervisor directory (default `~/supervisor`). Check whether `<supervisor-dir>/state.json` exists and say what you found. The user may answer that they have no supervisor; then pass the default anyway, and the secretary runs with external sources only.
3. **Run setup**:

   ```bash
   python3 "<skill-dir>/scripts/state_tool.py" setup --dir "<dir>" --supervisor-dir "<supervisor-dir>"
   ```

   `setup` is idempotent. It creates the directory (aborting when a rule of step 1 is broken), copies `state_tool.py` and `dashboard.template.html` to `<dir>/.secretary/`, adds a SessionStart hook (matcher `startup|resume|clear|compact`) to `<dir>/.claude/settings.json` unless already present, creates `state.json` and `CLAUDE.md` (a short note, an empty `## Sources`, and a commented example) if missing, and prints what it created, kept, or updated. It reports on stderr when the supervisor directory does not exist. Show the user the printed lines.

   If `CLAUDE.md` already existed and has no `## Sources` line, ask before appending an empty `## Sources` section at its end; without it no external source can be defined.
4. **Sources.** Ask whether to register an external agent. For each one, ask in turn:
   - the directory where that agent runs (`~` is allowed). Check that it exists. If it has a `CLAUDE.md`, read it (read-only) to learn what the agent manages, and use that to suggest the id and the query;
   - the id (`[a-z0-9-]`, 32 characters or fewer, not `supervisor`, unique; suggest one from the directory name);
   - the query: one line, 500 characters or fewer, in the language the agent understands. Suggest one that asks for today's tasks and the items waiting on the user's decision, and adapt it to what the agent manages;
   - the interval (`<n>m`, `<n>h`, or `<n>d`, at least 15m; default `2h`). Mention that a query starts a session and uses some usage;
   - whether the secretary may relay the user's answers to that agent (`relay: yes`) or only display its items (`relay: no`; default `no`).

   Show the `### <id>` block you will write, append it under `## Sources` in `<dir>/CLAUDE.md`, and validate:

   ```bash
   python3 "<dir>/.secretary/state_tool.py" validate-sources --claude-md "<dir>/CLAUDE.md"
   ```

   Exit 1 prints one `error:` line per problem (`CLAUDE.md:<line>: <message>`, or a missing directory). Fix the block with the user and validate again until it passes. Then ask whether to add another source. The format and rules are in [references/sources-format.md](references/sources-format.md). Sources can be added or changed later by editing `## Sources`; the secretary picks them up on its next commit.
5. **Summary.** Show the secretary directory, the supervisor directory (and whether its `state.json` was found), and the registered sources with their intervals and relay settings.
6. **Hand-off.** Tell the user to start a **new** session in `<dir>` in auto permission mode and write "秘書として動いて" there. This session stays a setup session: the hook added in step 3 is loaded only by sessions that start after it, and the secretary depends on it for recovery. The user may archive this session afterwards. Stop here.

When handing off, also explain that:

- the dashboard is a private artifact republished on every visible change;
- periodic reconciliation (`/loop`, about every 5 minutes) runs only while query or relay sessions exist and uses some usage even when nothing changes;
- without `archive_consent`, finished sessions stay `closing` and `/loop` keeps running until the user archives them;
- external sources are queried only in turns that carry the user's words, so their freshness follows how often the user talks to the secretary.

### 4. The state-change set

Every state change that no subcommand performs runs this set (due dates, relays, session records, consent, dashboard URL).

1. Copy the current state to a candidate:

   ```bash
   cp "<dir>/state.json" "<dir>/state.next.json"
   ```

2. Change only the needed fields in `state.next.json` with the Edit tool. Do not rewrite the whole file and do not change `rev`. Allocate new `D-` / `R-` / `S-` ids by incrementing the matching `counters` entry (ids are never reused).
3. Commit:

   ```bash
   python3 "<dir>/.secretary/state_tool.py" commit --state "<dir>/state.json" --candidate "<dir>/state.next.json" --claude-md "<dir>/CLAUDE.md"
   ```

4. Exit 0 means the state was committed; it prints `{"rev": n, "dashboard_changed": bool}`. If `dashboard_changed` is true, republish the dashboard (section 9). If the output also has `dashboard_error`, the HTML was not updated (a warning is on stderr); the next commit rewrites it.
5. Non-zero exit means nothing was written: the `error:` lines on stderr name each violated invariant. Copy `state.json` again (never reuse the rejected candidate), redo the edit correctly, and commit again. Never bypass the invariants.
6. Put a new relay session (`purpose: relay`) in a candidate of its own. Its check reads the supervisor's `state.json`; if that file is unreadable only the relay session is rejected, and unrelated changes in a separate candidate still pass.

`merge-supervisor` and `ingest-report` commit internally through the same validation and print the same JSON; republish when `dashboard_changed` is true.

### 5. Refresh

Run at the start of every user turn and in every `/loop` turn.

1. Merge the supervisor's state (read-only; commits only when the supervisor's `rev` changed):

   ```bash
   python3 "<dir>/.secretary/state_tool.py" merge-supervisor --state "<dir>/state.json"
   ```

   If the supervisor is missing or its state is malformed, the error is recorded in `supervisor.error` and the previous supervisor items are kept. Continue with external sources.
2. Reconcile sessions (6.2).
3. User turns only: handle the user's requests first (due dates in section 7, relays in section 8), then, at the end of the turn, query the expired external sources (6.1).
4. `/loop` turns never start sessions. Expired sources show "stale: refreshed on your next message" in the dashboard and the digest.

Periodic reconciliation: while `sessions[]` is not empty (`closing` entries included), keep a self-paced `/loop` (about every 5 minutes) that runs this Refresh. Stop it once `sessions[]` is empty: in a self-paced loop, end the turn without scheduling the next one; if the loop was created with a fixed interval, delete its recurring job. Pending `start` relays and `paste` relays awaiting the user do not keep the loop alive.

### 6. Sessions

#### 6.1 Querying external sources

Only in a turn that carries the user's own words.

1. List the sources that may be queried now (interval expired, no query or relay session `pending` / `running` for the source, directory exists):

   ```bash
   python3 "<dir>/.secretary/state_tool.py" due-sources --state "<dir>/state.json" --claude-md "<dir>/CLAUDE.md"
   ```

   It prints a JSON list of source ids. A source whose last attempt failed is skipped for 15 minutes.
2. For each id, add `{purpose: query, source_id, state: pending, session_id: null, observed_state: none, created_at, started_by: <your id>, last_message_ref: null, resend_requested: false}` with a new `S-` id and run the state-change set. `commit` rejects a query whose interval has not expired, a second session for the same source, and any new query while `## Sources` has errors.
3. Build `prompt` and `background` from the query brief in [references/agent-prompt.md](references/agent-prompt.md) and start the session: `cwd` = the source's directory, no worktree, `name` = `sec-<S-id>` (e.g. `sec-S-0005`), fresh context, `permission_mode` auto (never above the secretary's), immediate start.
4. On success, record `session_id` and `observed_state` and set `state: running` with the state-change set. If start is refused, remove that `sessions[]` entry and record `last_error` and `last_attempt_at` on the source in the same candidate.
5. If periodic reconciliation is not running, start it (section 5).

#### 6.2 Reconciliation

When: in every Refresh, on every automatic continuation, and right after a recovery.

Call list with `include_archived: true` and `limit: 100`. Match rows to `sessions[]` by `session_id`, taking over sessions whose `started_by` is an earlier secretary id:

- `state: pending` (the secretary stopped before recording the start): if the list has a session named `sec-<S-id>`, record its `session_id` and set `state: running` (the only place `name` is used); for a relay session, that commit also moves its relay from `pending` to `sent`. Otherwise, once `created_at + 10m` has passed, remove the entry (query: set the source's `last_error` to `start not recorded`; relay: set the relay to `failed`).
- `state: running` and idle: read the latest events (about 12), save only the last assistant message to `<dir>/.secretary/last-message.txt`, and ingest it:

  ```bash
  python3 "<dir>/.secretary/state_tool.py" ingest-report --state "<dir>/state.json" --session S-0005 --message-ref "<message id>" --file "<dir>/.secretary/last-message.txt"
  ```

  It prints one JSON object on stdout. Branch on its `outcome`; every outcome except `noop` has already been committed, so no state-change set is needed:
  - `noop`: the `message_ref` equals `last_message_ref`; nothing to do.
  - `merged` (query `done`): the source's today items are replaced, its decisions added / updated / removed, `last_fetched_at` updated, and the session is `closing`.
  - `query_failed` (query `failed` / `needs_input`): the source's `last_error` and `last_attempt_at` are set, and the session is `closing`.
  - `relay_done` (relay `done`): the relay stays `sent`, the source's `last_fetched_at` is reset to null (so the next user turn queries it and checks the relay), today / decisions are unchanged, and the session is `closing`.
  - `relay_failed` (relay `failed` / `needs_input`): the relay is `failed` with its blockers shown, and the session is `closing`.
  - `resend_requested` (exit 1, first report that did not parse): `resend_requested` is now true and the session stays `running`. Send the session one request to resend the report as single-line JSON in the same `secretary-report` fence.
  - `parse_failed` (exit 1, the resent report did not parse either): the failure is recorded (query: source `last_error`; relay: `failed`) and the session is `closing`.

  Exit 1 with no JSON on stdout means nothing was committed (e.g. the session is not `running`, or `commit` rejected the result); read the `error:` lines.
- The latest events end in a pending permission request or an auto-mode classifier stop: set `observed_state: needs_input`; the dashboard and digest show "Answer the pending approval of sec-S-xxxx in the app".
- `state: running` with no report after `created_at + 30m`: record the failure (query: `last_error`; relay: `failed`) and set `state: closing`.
- `state: closing`: with `archive_consent`, call archive with a `reason` naming the session, its source, and the recorded `archive_consent`.
  - Archived observed: remove the entry from `sessions[]`; for `purpose: relay`, set the relay's `session_closed_at` in the same candidate.
  - Refused because mid-turn or background work: do nothing; the next reconciliation retries.
  - Refused because pinned or shown on screen: set `archive_refused: true` on the entry with the state-change set, so the dashboard and digest show "Close sec-S-xxxx from the screen or unpin it"; retry on the next reconciliation. The flag goes away with the entry once the archive is observed.
  - Without consent: do not call archive. Keep it `closing` ("Please archive sec-S-xxxx") until reconciliation observes it archived, then remove it.
- Listed as archived outside the secretary, or not listed and not found by get (`missing`): if it was not `closing`, record the failure; in both cases remove the entry (and set `session_closed_at` for a relay).

Run the state-change set only when something differs.

### 7. Due dates

When the user sets a due date (e.g. "D-0011 の期限は金曜"):

1. Identify the decision by `D-NNNN` or by its description.
2. Resolve the date to `YYYY-MM-DD` (the end of that local day) or an ISO 8601 date-time with offset. If it is ambiguous, ask the user.
3. Set `decisions[].due` with the state-change set; `commit` validates the format. Use `null` to clear it.

Due dates survive merges. Renaming a source id in `## Sources` drops the old id's decisions and their due dates.

### 8. Relay

When the user states a decision (e.g. "D-0011 は push して PR を作ってよい"):

1. Identify the decision by `D-NNNN` or by its content. Repeat its `description` in the same reply (the user may be looking at an old dashboard), without waiting for confirmation. Only when the id is not found, or the user's words contradict the `description`, ask before relaying. Copy into `words` the user's first message that stated the decision, verbatim (line breaks kept); if you had to ask, still use that original message, not the "yes".
2. Branch on `relay_mode`:
   - `paste` (supervisor): show the paste text `{task_id} {ua_id} ("{first 60 characters of description}"): {words}` in the chat; the dashboard shows it as click-to-copy. In one candidate, add `{id: R-NNNN, decision_id, decision_key, mode: paste, target: {kind: supervisor}, words, at, status: awaiting_user, session_closed_at: null}` and set the decision's `relay_id`. Do not send anything. Tell the user to paste it into the supervisor chat.
   - `app` (supervisor): create no relay. Tell the user which operation to perform in the app (e.g. close or unpin the session, archive it, open the repo once).
   - `start` (external source with `relay: yes`):
     1. Check that the source directory exists and does not overlap the supervisor's running non-git workers:

        ```bash
        test -d "<source directory>" && python3 "<dir>/.secretary/state_tool.py" check-overlap --state "<dir>/state.json" --directory "<source directory>"
        ```

        A missing directory means do not relay; tell the user to fix `## Sources` or the directory. `busy` means do not relay now; tell the user the relay waits until that supervisor worker finishes.
     2. In one candidate containing nothing else, add the relay `{mode: start, target: {kind: source, source_id, directory}, status: pending, ...}`, set the decision's `relay_id`, and add `{purpose: relay, source_id, relay_id, state: pending, ...}` to `sessions[]`. If `commit` rejects it because a query session of the same source is `pending` / `running`, create nothing and tell the user the relay runs after that report is ingested; redo this step with the same `words` in a later user turn.
     3. Start the relay session from the relay brief in [references/agent-prompt.md](references/agent-prompt.md), with the same start options as 6.1 step 3.
     4. On success, record `session_id` and set the session `running`; that commit moves the relay from `pending` to `sent`. If start is refused, set the relay `failed` and remove the session entry.
   - `none` (external source with `relay: no`): do not relay. Tell the user to handle it at the source.
3. Badges are rendered by the script (Relay badges in [references/state-schema.md](references/state-schema.md)). A relayed item stays in the decisions list until its source stops reporting it, which marks the relay `confirmed`.

### 9. Dashboard

1. First publish: publish `<dir>/dashboard.html` as-is with `icon: dashboard`. Do not load the artifact-design skill and do not edit or redesign the HTML; `state_tool.py` renders it from the bundled template. Record the URL in `secretary.dashboard.url`.
2. Within the same conversation, republish the same file path whenever `dashboard_changed` is true.
3. After a recovery or a new secretary session, read the artifact by `secretary.dashboard.url`, then publish to that `url` so the link stays the same.
4. Decision ids (`D-NNNN`) and paste texts are click-to-copy so the user can paste them into a chat.

### 10. Recovery (compact / resume / clear / re-creation)

The SessionStart hook runs `state_tool.py digest` and injects at most 10,000 characters: the state and dashboard paths, `rev`, the dashboard URL, `session_ids`, whether `archive_consent` exists, `sources_error`, the supervisor read status, each source's last fetch and whether it is stale, `sessions[]`, requests to the user (pending approvals, archive and unpin requests), overdue decisions (judged at hook time), the top 20 decisions by due date, unconfirmed relays, and the instruction to run the Capability check and Refresh before acting. If the digest is missing, run it yourself:

```bash
python3 "<dir>/.secretary/state_tool.py" digest --state "<dir>/state.json"
```

Then:

1. Run the Capability check. A re-created secretary adds its new session id to `session_ids`.
2. Run Refresh (section 5). Reconciliation takes over sessions started by earlier secretary ids.
3. If `sessions[]` is not empty, restart periodic reconciliation.
4. If the current turn cannot start a session, commit what you detected and query or relay on the next user message.

### 11. Degraded modes

| Situation | Fallback |
| :--- | :--- |
| Start unavailable entirely | Show the user the filled `prompt` and `background`, ask them to start the session in the source directory with name `sec-<S-id>`, and record the session id they give. |
| Archive unavailable or refused by a classifier | Keep the session `closing`; the dashboard asks "Please archive sec-S-xxxx". |
| send unavailable | Skip the resend request and treat a parse failure as a failure right away. |
| Latest message unreadable or the fence is mangled | Ask once for a single-line JSON resend in the same fence. If that also fails, record the failure; never parse the turn-end note. |
| Supervisor directory or `state.json` missing | Run with external sources only; `supervisor.error` is shown on the dashboard. |
| `## Sources` has errors | `sources_error` is shown on the dashboard; new queries and `start` relays are rejected until it is fixed. Due dates, supervisor merges, and `paste` relays still work. |
| Artifacts unavailable | Give the user the local path `<dir>/dashboard.html`. |

### 12. Uninstall

1. Reconcile until `sessions[]` is empty (archiving only within `archive_consent`; ask the user to archive the rest) and stop periodic reconciliation.
2. Ask the user to delete the secretary directory themselves. The agent never deletes it.

## Verification

- `python3 "<dir>/.secretary/state_tool.py" version` matches the bundled script after setup.
- Every state change in this session went through `commit`, `merge-supervisor`, or `ingest-report`, and `rev` increased each time.
- The supervisor's `state.json` and directory were never written, and no message was sent to the supervisor session.
- Every relay in `relays[]` has non-empty verbatim `words`, and every `start` relay was started in a turn carrying the user's words.
- No session was started in a `/loop` turn or an event-triggered turn, and no external source was queried before its interval expired.
- Every archive call had a non-null `archive_consent` and covered only a session in `sessions[]`; the delete capability was never called.
- The dashboard URL in `secretary.dashboard.url` is unchanged after republishes and recoveries.
- While `sessions[]` is not empty, periodic reconciliation is active; when it is empty, it is stopped.
