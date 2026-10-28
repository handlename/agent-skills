# state.json Schema

`state.json` is the single source of truth for the secretary. Only the secretary session writes it, and only through `state_tool.py` (`commit`, `merge-supervisor`, `ingest-report`), which validates the candidate, checks `rev`, normalizes `sources[]` against `## Sources`, and writes the state and `dashboard.html` atomically. Never edit `state.json` directly. The supervisor's `state.json` is read-only input and is never written.

## Identifiers

- `decisions[].id`: `D-NNNN`. `relays[].id`: `R-NNNN`. `sessions[].id`: `S-NNNN`. Each is allocated by incrementing `counters.decision` / `counters.relay` / `counters.session` and is never reused. `commit` rejects a decreasing counter.
- `decisions[].key`: the identifier inside the source, unique across `decisions`.
  - Supervisor items: `supervisor/<task_id>/<ua_id>@<since>`. The supervisor may reuse a `UA-NNNN` number, so `since` tells two items with the same number apart.
  - External items: `<source-id>/<item id from the report>`.
- `sessions[].session_id`: the runtime session id returned by start. Reconciliation matches on it. The start-time `name` is `sec-<S-id>` (e.g. `sec-S-0005`) and is used only to recover a `pending` entry.
- `today[].key`: `<source-id>/<item id from the report>`.
- External item ids must not change across queries or days. The query brief in [agent-prompt.md](agent-prompt.md) asks for the item's link URL without its query string, else the path of the linked file in the source directory, else `<file path>#<item text>`, one item per task-list line. When an id changes, the merge treats the item as a new decision: it gets a new `D-NNNN`, and the old item's `due` and relay records do not carry over.

## Example

```json
{
  "schema_version": 1,
  "rev": 7,
  "updated_at": "2026-10-18T09:30:00+09:00",
  "counters": { "decision": 12, "relay": 3, "session": 5 },
  "secretary": {
    "dir": "/Users/handlename/secretary",
    "session_ids": ["sess_sec_01"],
    "archive_consent": { "text": "問い合わせ用のセッションは終わったらアーカイブしてよい", "at": "2026-10-18T08:00:00+09:00" },
    "dashboard": { "url": "https://claude.ai/code/artifact/xxxxxxxx", "path": "/Users/handlename/secretary/dashboard.html" },
    "sources_error": []
  },
  "supervisor": {
    "dir": "/Users/handlename/supervisor",
    "read_at": "2026-10-18T09:30:00+09:00",
    "state_rev": 42,
    "dashboard_url": "https://claude.ai/code/artifact/yyyyyyyy",
    "error": null
  },
  "sources": [
    { "id": "obsidian", "last_fetched_at": "2026-10-18T08:05:00+09:00", "last_attempt_at": "2026-10-18T08:05:00+09:00", "last_error": null }
  ],
  "today": [
    { "key": "obsidian/daily-2026-10-18#3", "source": "obsidian", "title": "週次レポートを書く", "link": null }
  ],
  "decisions": [
    {
      "id": "D-0011",
      "key": "supervisor/T-0003/UA-0002@2026-10-17T10:30:00+09:00",
      "source": "supervisor",
      "ref": { "task_id": "T-0003", "ua_id": "UA-0002" },
      "description": "Decide whether to push branch claude/license-x1 of /repo/a (and whether to open a PR)",
      "since": "2026-10-17T10:30:00+09:00",
      "due": "2026-10-19",
      "relay_mode": "paste",
      "relay_id": "R-0003",
      "follow_up": null,
      "link": null
    },
    {
      "id": "D-0012",
      "key": "supervisor/T-0004/UA-0003@2026-10-17T10:45:00+09:00",
      "source": "supervisor",
      "ref": { "task_id": "T-0004", "ua_id": "UA-0003" },
      "description": "Review repo-a#42",
      "since": "2026-10-17T10:45:00+09:00",
      "due": null,
      "relay_mode": "paste",
      "relay_id": null,
      "follow_up": { "rule": "review-pr", "url": "https://github.com/org/repo-a/pull/42", "done": "pr_closed" },
      "link": null
    }
  ],
  "relays": [
    {
      "id": "R-0003",
      "decision_id": "D-0011",
      "decision_key": "supervisor/T-0003/UA-0002@2026-10-17T10:30:00+09:00",
      "mode": "paste",
      "target": { "kind": "supervisor" },
      "words": "push して PR を作ってよい",
      "at": "2026-10-18T09:10:00+09:00",
      "status": "awaiting_user",
      "session_closed_at": null
    }
  ],
  "sessions": [
    {
      "id": "S-0005",
      "purpose": "query",
      "source_id": "obsidian",
      "relay_id": null,
      "session_id": "sess_q_09",
      "state": "running",
      "created_at": "2026-10-18T09:20:00+09:00",
      "started_by": "sess_sec_01",
      "observed_state": "running",
      "last_message_ref": null,
      "resend_requested": false
    }
  ]
}
```

## Fields

### secretary

| Field | Meaning |
| :--- | :--- |
| `dir` | Absolute path of the secretary directory (non-git). |
| `session_ids` | Every session that has acted as this secretary, appended during the Capability check. Used to match `started_by`. |
| `archive_consent` | `null`, or `{text, at}` holding the user's verbatim answer to the one-time archive question. Covers only sessions in `sessions[]` started by an id in `session_ids`. |
| `dashboard` | `{url, path}`: the published artifact URL (`null` until first publish) and the local HTML path. |
| `sources_error` | Errors from the latest `## Sources` check, each `CLAUDE.md:<line>: <message>`, or `CLAUDE.md: not readable (...)` when the file is missing or unreadable. Empty means valid. Written by `commit`. |

### supervisor

Written only by `merge-supervisor`.

| Field | Meaning |
| :--- | :--- |
| `dir` | The supervisor directory recorded by `setup --supervisor-dir`. |
| `read_at` | When the supervisor's `state.json` was last read. |
| `state_rev` | The supervisor's `rev` at that read. An unchanged `rev` means no commit. |
| `dashboard_url` | The supervisor's dashboard URL, linked from the Sources section of the dashboard. |
| `error` | `null`, or why the read failed (e.g. `supervisor not found`, malformed JSON, a minimum-schema violation). The previous supervisor items are kept while it is set. |

### sources[]

Kept in step with `## Sources` by `commit` while it is valid. A new id is added with `last_fetched_at: null`. A removed id drops its `today` and `decisions` entries; its `relays[]` history stays. While `## Sources` has errors, `sources[]` keeps its previous content.

| Field | Meaning |
| :--- | :--- |
| `id` | The `### <source-id>` heading. |
| `directory`, `interval`, `relay` | Copied from `## Sources` (`relay` as a boolean) so the dashboard and digest can show the next due time and staleness. |
| `last_fetched_at` | Last successful query ingest, or `null` (never fetched, or reset after a `done` relay so the next user turn checks the relay). |
| `last_attempt_at` | Last query attempt, successful or not. |
| `last_error` | `null`, or why the last attempt failed. A source with `last_error` is not due again until `last_attempt_at + 15m`. |

### today[]

`{key, source, title, link}`. Replaced per source on each query ingest. `link` is an http(s) URL or `null`. Supervisor tasks are not copied here; the dashboard links to the supervisor's dashboard instead.

### decisions[]

Supervisor user actions and external decisions share this list. An entry whose `follow_up` is set is a follow-up: remaining work for the user (e.g. reviewing a PR), shown under "Your tasks" instead of "Decisions".

| Field | Meaning |
| :--- | :--- |
| `id`, `key` | See Identifiers. |
| `source` | `supervisor` or a source id. |
| `ref` | `{task_id, ua_id}` for supervisor items; `{item_id}` (the `id` the source reported) for external items. Set by the merge; never edit it. |
| `description` | Updated from the source on every merge. |
| `since` | When the source first reported it, as given by the source (`YYYY-MM-DD` or ISO 8601 with offset); `null` when an external report sent `""`. |
| `due` | `null`, `YYYY-MM-DD`, or ISO 8601 with offset. Set only on the user's instruction; merges keep it. |
| `relay_mode` | `paste` \| `app` \| `start` \| `none`. Recomputed by the script on every merge (see relay_mode classification); `commit` rejects any other value. |
| `relay_id` | The latest relay for this item, or `null`. |
| `follow_up` | `null`, or `{rule, url, done}` (all strings) copied from the supervisor user action's `follow_up` on every merge. Always `null` for external items. A missing key is treated as `null`. `url` is the PR the task is about; `done` is `pr_closed` or `manual` (the supervisor decides when the follow-up is gone). Set by the merge; never edit it. |
| `link` | An http(s) URL of the item's origin (e.g. the PR it is about), or `null`. Updated from the external report on every merge (`""` becomes `null`); the dashboard links the description to it. Always `null` for supervisor items. A missing key is treated as `null`. |

### relays[]

Newest first, at most 50. A relay still referenced by a `decisions[].relay_id` is never trimmed.

| Field | Meaning |
| :--- | :--- |
| `id`, `decision_id`, `decision_key` | Relay id and the decision it answers. `decision_key` survives the decision's removal. |
| `mode` | `paste` \| `start`. |
| `target` | `{kind: supervisor}` or `{kind: source, source_id, directory}`. |
| `words` | Non-empty. The user's first message that stated the decision, verbatim (not a later "yes"). |
| `at` | When the relay was recorded. |
| `status` | See Value ranges. |
| `session_closed_at` | `start` relays only: when reconciliation observed the relay session archived or `missing`. `null` until then (always `null` for `paste`). `commit` fills it in when the relay session leaves `sessions[]`. |
| `blockers` | Set only when the relay `failed`: the relay session's blockers (or the parse error). |

### sessions[]

Query and relay sessions started by the secretary.

| Field | Meaning |
| :--- | :--- |
| `id` | `S-NNNN`. |
| `purpose` | `query` (requires `source_id`) or `relay` (requires `source_id` and `relay_id`). |
| `source_id`, `relay_id` | The source queried or relayed to, and the relay this session carries (`null` for queries). |
| `session_id`, `started_by` | Runtime session id (`null` while `pending`) and the secretary session that started it. |
| `state` | `pending` (committed, not started) \| `running` (started) \| `closing` (result fixed, waiting for archive). |
| `created_at` | When the entry was added. Used for the 10-minute `pending` and 30-minute `running` timeouts. |
| `observed_state` | `none` \| `running` \| `idle` \| `needs_input` \| `archived` \| `missing`. |
| `last_message_ref` | Id of the last ingested message. The same `message_ref` is a no-op. |
| `resend_requested` | Whether one resend of the report was already requested. Set by `ingest-report`. |
| `archive_refused` | Optional boolean. Set to `true` when archive was refused because the session is pinned or shown on screen; the dashboard and digest then ask "Close sec-S-xxxx from the screen or unpin it". |

An entry leaves `sessions[]` only when reconciliation observes it archived or `missing`. Without `archive_consent`, a `closing` entry stays (the dashboard and digest ask "Please archive sec-S-xxxx") until the user archives it.

## Value ranges

- `decisions[].relay_mode`:
  - `paste`: supervisor item; the user pastes the prepared text into the supervisor chat. Every follow-up is `paste`.
  - `app`: supervisor item; the user performs an operation in the app. No relay can be created.
  - `start`: external item from a `relay: yes` source; relayed by a relay session.
  - `none`: external item from a `relay: no` source. No relay can be created.
- `relays[].status`: `pending` (`start` before the start) \| `sent` (`start` relay session started; `commit` sets it once the relay session leaves `pending`) \| `awaiting_user` (`paste`) \| `failed` \| `confirmed` (the source no longer reports the item) \| `unconfirmed` (`start` relay whose item was still reported by the first query started after `session_closed_at`).
- `sessions[].state`: `pending` \| `running` \| `closing`.
- `decisions[].due`: `YYYY-MM-DD` means until 23:59:59 of that day in the script's local time zone.
- URLs (`secretary.dashboard.url`, `supervisor.dashboard_url`, `today[].link`, `decisions[].link`): http or https, or `null`.

## relay_mode classification

The script decides `relay_mode` for supervisor items on every merge, so a changed `description` may change the mode. A follow-up is always `paste` (order 0); for every other item `classify_supervisor_ua(description)` applies. Matching is case-insensitive; rows are evaluated **from the top and the first match wins**. `start` and `restart` match on word boundaries.

| Order | Words | `relay_mode` | Origin in session-supervisor |
| :--- | :--- | :--- | :--- |
| 0 | `follow_up` is set (the wording is not looked at) | `paste` | Follow-ups created from a `## Follow-ups` rule (e.g. "Review repo-a#42"); the user reports completion by pasting |
| 1 | `in the app`, `unpin`, `please archive`, `open repo` | `app` | App-operation user actions: open repo, close or unpin, please archive, answer the pending approval, tell a worker in the app (send unavailable), report-blocker guidance (`Answer w-xxxx (...) in the app: ...`; the user answers in the worker session) |
| 2 | `waiting task`, `push`, `pull request`, `new worker`, `replace`, `restart`, `start`, `cancel`, `archive` | `paste` | The automatic waiting-task action, push decisions, the replacement-limit question (replacement workers went missing or unverified 3 times: start a new worker, record the result, or cancel) |
| 3 | anything else (unparsed summaries, `is not progressing`, report blockers recorded before 0.27.0) | `paste` | Answers to unparsed summaries and `is not progressing`; a relayed answer does not advance the supervisor's state. Free-text report blockers recorded before session-supervisor 0.27.0 also land here; when they are pasted, the supervisor points the user to the worker session |

Rows 2 and 3 give the same mode; they stay separate to record their origin. A misclassification only changes which guidance the dashboard shows. Update this table when the supervisor's user-action wording changes.

External items get `start` when their source has `relay: yes` and `none` otherwise, recomputed on every query ingest.

## Relay badges

`relay_badges(state)` renders the relay state of each decision. There is no wall-clock timeout.

| `mode` | Badge while relaying | Becomes `unconfirmed` when | Badge when `unconfirmed` |
| :--- | :--- | :--- | :--- |
| `paste` | paste pending (never times out; nothing happens until the user pastes) | never | none |
| `start` | relayed; checked at next refresh | the first query started after the relay session's archive was observed still reports the item | relay not reflected (no paste text; there is nowhere to paste) |

A `failed` relay shows "relay failed" with its blockers. Paste text appears only for supervisor `paste` items: `{task_id} {ua_id} ("{first 60 characters of description}"): {words}`, or the same template with an empty `{words}` before the user answers. A follow-up uses `done` as `{words}` until the user states something else (e.g. `T-0003 UA-0003 ("Review repo-a#42"): done`); a follow-up relay uses the same records and badges as a decision relay.

## Invariants

`commit` rejects the candidate (exit 1, `error:` lines on stderr, nothing written) when any of these fails. Like the supervisor, transition checks apply **only to entries that are new or changed** in the candidate.

1. `rev` equals the current `rev`; `schema_version` is 1; shapes are valid; `counters` do not decrease; `decisions[].key` and every id are unique.
2. A changed `decisions[].due` has a valid format.
3. A new `relays[]` entry has non-empty `words`, and its `mode` is allowed by the item's `relay_mode` as the script derives it (classification for supervisor items, the source's `relay:` for external items), not as the candidate states it: `paste` → {`paste`}, `start` → {`start`}, `app` / `none` → {} (no relay). Anything else is rejected, including an unknown `mode` such as `send` and `start` on a `paste` item. A `start` relay also requires `## Sources` without errors. A recorded relay's `mode`, `decision_id`, `decision_key`, and `words` never change; only its status fields do. A changed `decisions[].relay_mode` must equal the derived one.
4. A new `sessions[]` entry (either purpose) requires `## Sources` without errors, an existing source, and no other `pending` / `running` session (query or relay) for the same source; `closing` does not count. A `purpose: query` entry must also be in the result of `due_sources` at commit time (interval expired).
5. A new `purpose: relay` entry requires its relay to be `pending`, and its directory must not overlap the `cwd` of a running non-git supervisor worker. `commit` reads the supervisor's `state.json` read-only for this check; if it is unreadable or malformed, only the new relay session is rejected.
6. A change from `pending` to `running` comes with a `session_id`.
7. `secretary.dashboard.url`, `supervisor.dashboard_url`, `today[].link`, and `decisions[].link` are http(s) or `null`.

While `## Sources` has errors, every new query session and every new `start` relay is rejected. Due dates, supervisor merges, `paste` relays, and updates of existing sessions still pass.
