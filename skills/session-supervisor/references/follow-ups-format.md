# `## Follow-ups` Format

A worker's result can be verified while work remains for the user, such as reviewing the PR it opened. Rules under `## Follow-ups` in `<dir>/CLAUDE.md` turn each newly verified PR into a follow-up user action (`follow_up: {rule, url, done}`, see [state-schema.md](state-schema.md)). The script parses the section with `parse_follow_ups(text)` and reports every problem with its line number. `setup` writes a commented-out example into a new `CLAUDE.md`; it never overwrites an existing one.

## Example

```markdown
## Follow-ups

### review-pr
- on: pr
- text: Review {label}
- done: pr_closed
```

With this rule, a verified PR `https://github.com/org/repo-a/pull/42` gets the user action "Review org/repo-a#42". The task stays `needs_user` until the follow-up is removed, then `commit` archives it.

## Rules

- Lines inside fenced code blocks and inside `<!-- ... -->` comments are skipped (the example that `setup` writes is a comment).
- Exactly one `## Follow-ups` line. None means "no follow-ups" and is not an error. Two or more is an error.
- The section runs until the next `## ` or `# ` heading, or the end of the file. Its first non-empty line must be a `### ` heading.
- `### <rule-id>`: matches `^[a-z0-9][a-z0-9-]{0,31}$` and is unique.
- Under each `###`, only empty lines and `- <key>: <value>` lines are allowed. The keys are `on`, `text`, and `done`, each exactly once. An unknown key, a duplicate, a missing key, or any other line is an error.

| Key | Value |
| :--- | :--- |
| `on` | `pr`: the rule applies to every `pr` artifact newly recorded after verification. |
| `text` | The user action's description. Not empty, 200 characters or fewer. The only placeholders are `{label}` and `{url}`. |
| `done` | `pr_closed`: also removed when the PR is merged or closed (checked by the `pr-state` observer at the start of a user turn). `manual`: removed only when the user says it is done. |

## Substitution

- `{label}`: the `label` of the `pr` artifact when it is a non-empty string. Otherwise, for a URL of the form `https://<host>/<owner>/<repo>/pull/<n>`, `<owner>/<repo>#<n>`. Otherwise the URL itself. Artifacts recorded from reports have no `label`, so `{label}` is normally `<owner>/<repo>#<n>`.
- `{url}`: the PR URL.
- Prefer a short `text` without `{url}`, such as `Review {label}`. The secretary shows at most 60 characters of the description in its paste text, and a long URL would be cut.

## Errors

- Every error is reported as `CLAUDE.md:<line>: <message>`, all at once.
- While there is any error, no rule applies: no follow-up is created, and verification, archiving, and push decisions continue as usual.

Validate after every edit (the Capability check also runs this once per session):

```bash
python3 "<dir>/.supervisor/state_tool.py" validate-follow-ups --claude-md "<dir>/CLAUDE.md"
```

It prints `ok: N rules` and exits 0, or prints one `error:` line per problem on stderr and exits 1.

## Creating follow-ups

The supervisor runs `follow-ups` once in every candidate that records a newly verified `pr` artifact, after all edits and right before `commit` (SKILL.md section 4):

```bash
python3 "<dir>/.supervisor/state_tool.py" follow-ups --state "<dir>/state.json" --candidate "<dir>/state.next.json"
```

- Only `pr` urls that the candidate has and `state.json` does not are considered, so a follow-up the user already finished never comes back.
- A rule whose `(rule, url)` pair the task already has is skipped, so running it twice with the same `--closed` urls adds nothing. A rerun without `--closed` adds the `pr_closed` follow-ups the first run skipped.
- `--closed <url>` (repeatable) skips the `pr_closed` rules for a PR that the verifying observer reported as `MERGED` or `CLOSED`. A url that is not newly recorded gets a `warning:` line and changes nothing.
- It prints the added user actions as JSON and rewrites the candidate only when something was added. With rule errors it exits 1 and leaves the candidate unchanged; the supervisor then commits the candidate as it is. Without `CLAUDE.md` or without the section it exits 0 and adds nothing.
- It writes only the candidate, and refuses a candidate that is `state.json` itself or that has no `state.json` and `.supervisor/` next to it.

To add a missed follow-up by hand, only when the user asks:

```bash
python3 "<dir>/.supervisor/state_tool.py" follow-ups --candidate "<dir>/state.next.json" --task T-0003 --url "https://github.com/org/repo-a/pull/42"
```

This mode does not compare with `state.json`, so it also re-adds follow-ups the user already finished. Add `--closed` to skip the `pr_closed` rules.

## Completing follow-ups

- `pr_closed`: at the start of a turn that begins with the user's message, the supervisor asks one `pr-state` observer for the state of every such PR and removes those reported `MERGED` or `CLOSED` ([observer-brief.md](observer-brief.md)).
- Any follow-up: the user names it as done in the supervisor's chat, for example by pasting the secretary's text `T-0003 UA-0003 ("Review repo-a#42"): done`.
