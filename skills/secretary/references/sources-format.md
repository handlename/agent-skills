# `## Sources` Format

External agents are defined under `## Sources` in `<dir>/CLAUDE.md`. The script parses it with `parse_sources(text)` and reports every problem with its line number. `setup` creates a `CLAUDE.md` with a short note, an empty `## Sources`, and a commented example; it never overwrites an existing `CLAUDE.md`. The secretary's rules live in SKILL.md and the digest, not in `CLAUDE.md`.

## Example

```markdown
## Sources

### obsidian
- directory: ~/notes
- query: List today's tasks from the daily note and every item that waits on my decision.
- interval: 2h
- relay: yes
```

## Rules

- Lines inside fenced code blocks and inside `<!-- ... -->` comments are skipped (the example that `setup` writes is a comment).
- Exactly one `## Sources` line. None means "no external sources" and is not an error; the secretary then runs with the supervisor only. Two or more is an error.
- The section runs until the next `## ` or `# ` heading, or the end of the file. Its first non-empty line must be a `### ` heading.
- `### <source-id>`: matches `^[a-z0-9][a-z0-9-]{0,31}$`, is unique, and is not the reserved word `supervisor`.
- Under each `###`, only empty lines and `- <key>: <value>` lines are allowed. The keys are `directory`, `query`, `interval`, and `relay`, each exactly once. An unknown key, a duplicate, a missing key, or any other line is an error.

| Key | Value |
| :--- | :--- |
| `directory` | An absolute path after `~` expansion. It must not be the secretary directory, inside it, or an ancestor of it, and must not be the supervisor directory or inside it. |
| `query` | One non-empty line, 500 characters or fewer. It becomes the "What to report" part of the query brief. |
| `interval` | Matches `^\d+(m\|h\|d)$` and is at least `15m`. A source is queried again only after `last_fetched_at + interval`. |
| `relay` | `yes` (answers are relayed by starting a session in `directory`) or `no` (display only). |

## Errors

- Every error is reported as `CLAUDE.md:<line>: <message>`, all at once.
- While there is any error, `commit` keeps `sources[]` unchanged, records the errors in `secretary.sources_error` (shown at the top of the dashboard's Sources section and in the digest), and rejects every new query session and every new `start` relay. Other commits still pass.
- Directory existence is checked only by `validate-sources` and right before a start (`due-sources` and the relay procedure). A source whose directory does not exist is skipped for that query or relay: `due-sources` drops it with a `warning:` line on stderr, the relay procedure checks it with `test -d`, and the dashboard's Sources section marks it "directory not found" (checked at each commit).

Validate after every edit:

```bash
python3 "<dir>/.secretary/state_tool.py" validate-sources --claude-md "<dir>/CLAUDE.md"
```

It prints one `error:` line per format error or missing directory on stderr and exits 1; exit 0 means the section is valid.

## Adding, removing, and renaming sources

- On every commit with a valid `## Sources`, `sources[]` follows the headings: a new id is added with `last_fetched_at: null` (it is due on the next user turn), and a removed id drops its `today` and `decisions` entries and leaves `sources[]`. Its `relays[]` history stays. Query or relay sessions still in flight for a removed id are collected by reconciliation.
- Renaming a source id is a removal plus an addition. The old id's decisions, **including any due dates the user set**, are lost; the new id starts empty and is queried on the next user turn. There is no grace period.
