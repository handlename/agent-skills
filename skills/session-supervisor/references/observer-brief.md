# Observer Brief

The supervisor never observes repositories or reads target files itself (SKILL.md 6.0). It runs a subagent with the brief below, the subagent writes its facts to a result file, and returns only that file's path. The supervisor reads the file and applies the rules of the calling procedure; the observer reports facts, never verdicts.

Fill every `{...}` field before running the subagent; never run it with an unfilled field.

Run the subagent on model `haiku` for every purpose (without a model when the subagent capability takes none).

## Brief

````markdown
You are an observer subagent of a session supervisor. Collect read-only facts and write them to a file. Do not judge them.

Purpose: {purpose}
Target:
{one `name: value` line per target field of this purpose}
Result file: {result path}

Rules:
- Never modify anything. The only file you write is the result file. A PreToolUse hook blocks edits outside the supervisor directory and mutating git / jj / gh commands; if it blocks a command, record that in `errors` instead of working around it.
- Use git, never jj, in target repositories: in jj-colocated repositories jj imports and exports refs.
- Run each command in its own Bash call, on one line. No heredocs, and no command substitution around git or gh.
- Run only the commands listed for your purpose. A failing command is a fact: record its result as the purpose describes, or its error in `errors`, and continue.

Commands:
{the commands of this purpose, with the target values filled in}

Write one JSON object with the fields of this purpose to the result file with the Write tool (purpose `question`: Markdown). Your final message is the result file path and nothing else.
````

## Result files

- Path: `<dir>/.supervisor/observations/<purpose>-<id>.json`, where `<id>` is the worker id (the task id for `kind` and `locate`). For `pr-state` it depends on the mode: the fixed value `follow-ups` in the follow-ups mode (`target.urls`, which belongs to no worker or task), and the worker id in the branch mode (`target.branch`: the worker being started, or the worker that pushes). Each run overwrites the file.
- User questions: `<dir>/.supervisor/observations/question-<UTC timestamp>.md`, e.g. `question-20261020T091500Z.md`.
- Every JSON result has `purpose`, `target` (the target fields as given), `observed_at` (ISO 8601 with offset), and `errors` (a list of strings, empty when every command ran), plus the facts of its purpose.

## Purposes

### kind

Used by SKILL.md 6.1. Target: `dirs`, the absolute directories of the task. For each directory:

```bash
ls -d "<cwd>"
git -C "<cwd>" rev-parse --show-toplevel
ls -d "<cwd>/.jj"
```

Also check each ancestor of `<cwd>` for a `.jj` directory with `ls -d`; do not run jj. When `ls -d "<cwd>"` fails, skip the other commands for that directory.

```json
{"purpose": "kind", "targets": [{"cwd": "/abs/dir", "exists": true, "kind": "git", "repo_root": "/abs/repo", "has_jj": false}]}
```

- `exists`: `ls -d "<cwd>"` succeeded. When `false`, `kind` and `repo_root` are `null` and `has_jj` is `false`.
- `kind`: `git` when `rev-parse` succeeds; otherwise `jj-only` when `has_jj`; otherwise `non-git`.
- `repo_root`: the `rev-parse` output, or `null`.
- `has_jj`: `<cwd>` or one of its ancestors contains `.jj`.

### locate

Used by SKILL.md 6.1 when the request names a repository only by name. Target: `clone_root` (the absolute path printed by `prepare-dir --show`), `names` (the repository names as a one-line JSON array), and `owner` (when known; otherwise empty). For each name:

```bash
ls -d "<clone_root>"/*/*/"<name>"
gh repo view "<owner>/<name>" --json url,sshUrl
gh config get git_protocol
```

Run `gh repo view` with `<owner>/<name>` when `owner` is given, otherwise with `<name>` alone (gh then uses the authenticated user). Run `gh config get git_protocol` once.

```json
{"purpose": "locate", "git_protocol": "https", "names": [{"name": "repo-a", "local": ["/Users/me/src/github.com/org/repo-a"], "remote": {"url": "https://github.com/org/repo-a", "sshUrl": "git@github.com:org/repo-a.git"}}]}
```

- `local`: every directory matched by `ls -d`, in its output order; empty when none matched.
- `remote`: `{url, sshUrl}` from `gh repo view`, or `null` when it failed; the error goes into `errors`.
- `git_protocol`: the output of `gh config get git_protocol`, or `null` when it failed.

### base

Used by SKILL.md 6.2, 6.3 step 7, 6.7, and 6.8. Target: `repo`, and `branch` when the supervisor needs that branch's tip.

```bash
git -C "<repo>" symbolic-ref --short refs/remotes/origin/HEAD
git -C "<repo>" rev-parse --verify "refs/heads/<branch>"
```

```json
{"purpose": "base", "base_branch": "origin/main", "branch_head": "3f2a9c1e0d..."}
```

- `base_branch`: the remote default branch, or `null` when `origin/HEAD` is not set. Never use the `HEAD` symbolic ref.
- `branch_head`: the sha of `refs/heads/<branch>`, or `null` (no `branch` given, or the branch does not exist).

### verify-git

Used by SKILL.md 6.3 step 5 for every git worker, including push workers, and by 6.4 for a worker archived outside the supervisor whose latest report is `done` while its worktree remains. Target: `worktree`, `branch`, `base_branch` (may be empty), `pr` (the reported PR URL, may be empty), `expected_sha` (push workers only).

```bash
git -C "<worktree>" status --porcelain
git -C "<worktree>" rev-parse HEAD
git -C "<worktree>" rev-list --count "<base_branch>..HEAD"
git -C "<worktree>" ls-remote origin "refs/heads/<branch>"
git -C "<worktree>" remote get-url origin
gh repo view "<owner/name>" --json url -q .url
gh pr view "<pr>" --json url,headRefOid,state
```

With an empty `base_branch`, count with `git -C "<worktree>" rev-list --count HEAD --not --remotes` instead. Run `gh pr view` only when `pr` is given. Take `<owner/name>` for `gh repo view` from the origin URL.

```json
{"purpose": "verify-git", "porcelain_empty": true, "head": "3f2a9c1e0d...", "expected_sha": null, "commit_count": 1, "commit_count_basis": "base", "remote_sha": null, "pr": null, "web_url": "https://github.com/org/repo-a"}
```

- `porcelain_empty`: the `status --porcelain` output is empty (untracked files included).
- `head`: the `rev-parse HEAD` output. `expected_sha`: copied from the target, or `null`.
- `commit_count`: the count as an integer; `commit_count_basis`: `base` or `not-remotes`.
- `remote_sha`: the sha from `ls-remote`, or `null` when the remote branch does not exist.
- `pr`: `{url, headRefOid, state}` from `gh pr view`, or `null`.
- `web_url`: the web URL of the `origin` repository from `gh repo view`, or `null` when it failed (error in `errors`).

### verify-files

Used by SKILL.md 6.3 step 5 and 6.4 for non-git workers. Target: `cwd` and `files` (absolute paths). For each file:

```bash
ls -ld "<file>"
```

```json
{"purpose": "verify-files", "files_exist": {"/abs/dir/result.md": true}}
```

- `files_exist`: one entry per target file, `true` when it exists.

### repo-side

Used by SKILL.md 6.4 when the worktree may be gone (a worker archived outside the supervisor, a missing worker that reappeared) and by the Verification checklist. Target: `repo`, `branch`, `base_branch`, `pr` (may be empty).

```bash
git -C "<repo>" rev-parse --verify "refs/heads/<branch>"
git -C "<repo>" rev-list --count "<base_branch>..<branch>"
git -C "<repo>" ls-remote origin "refs/heads/<branch>"
git -C "<repo>" remote get-url origin
gh repo view "<owner/name>" --json url -q .url
gh pr view "<pr>" --json url,headRefOid,state
gh pr list --repo "<owner/name>" --head "<branch>" --state all --json url,headRefOid,state --limit 1
```

Run `gh pr view` when `pr` is given; otherwise run `gh pr list`, with `<owner/name>` taken from the origin URL. Run `gh repo view` with the same `<owner/name>`.

```json
{"purpose": "repo-side", "branch_exists": true, "branch_head": "3f2a9c1e0d...", "commit_count": 1, "remote_sha": "3f2a9c1e0d...", "pr": {"url": "https://github.com/org/repo-a/pull/42", "headRefOid": "3f2a9c1e0d...", "state": "MERGED"}, "web_url": "https://github.com/org/repo-a"}
```

- `branch_exists` / `branch_head`: from `rev-parse --verify`; `branch_head` is `null` when the branch is gone.
- `commit_count`: an integer, or `null` when the branch is gone.
- `remote_sha`, `pr`, and `web_url`: as in `verify-git`.

### pr-state

Two modes, told apart by the target: the follow-ups mode (`urls`) and the branch mode (`repo` and `branch`).

Follow-ups mode: used by SKILL.md 6.4, only at the start of a turn that begins with the user's message, while a follow-up with `done: pr_closed` exists. Target: `urls`, the PR URLs of every such follow-up as a one-line JSON array, e.g. `urls: ["https://github.com/org/repo-a/pull/42", "https://github.com/org/repo-b/pull/7"]`. Result file: `<dir>/.supervisor/observations/pr-state-follow-ups.json`.

Run this once first, then one `gh pr view` per url:

```bash
gh auth status
gh pr view "<url>" --json url,state
```

```json
{"purpose": "pr-state", "auth_ok": true, "prs": [{"url": "https://github.com/org/repo-a/pull/42", "state": "MERGED"}]}
```

- `auth_ok`: `true` when `gh auth status` succeeded.
- `prs`: one entry per target url, in target order. `state` is `OPEN`, `MERGED`, or `CLOSED` from `gh pr view`, or `null` when that command failed; the error goes into `errors`.

Branch mode: used by SKILL.md 6.7 (the original branch of a follow-up, when its worker starts) and 6.3 step 7 (the `base_branch` of the worker that pushes). Target: `repo` (the `cwd`, an absolute repo root) and `branch` (a branch name without `origin/`). Result file: `<dir>/.supervisor/observations/pr-state-<worker id>.json`. Run one command in `<repo>` (read-only):

```bash
cd "<repo>" && gh pr view "<branch>" --json state,url,headRefName
```

```json
{"purpose": "pr-state", "ok": true, "state": "OPEN", "url": "https://github.com/org/repo-a/pull/42", "head_ref_name": "fix-readme"}
```

- `ok: true` with `state` `OPEN`, `MERGED`, or `CLOSED`, and `url` / `head_ref_name` from the output, when the command succeeded.
- `ok: true` with `state`, `url`, and `head_ref_name` `null` only when `gh pr view` failed with "no pull requests found".
- `ok: false` with `state` `null` for any other failure (authentication, a base repository that cannot be determined, and so on); the error goes into `errors`. Never report such a failure as "no PR": the supervisor would open a duplicate PR against the default branch while an open one exists.

### question

Used for the user's questions and investigations about repositories or files (SKILL.md 6.0). Target: `question` (the user's words verbatim) and `paths` (the repositories or directories concerned).

Commands: read files with the Read, Grep, and Glob tools, and use read-only git commands (`git -C "<repo>" log`, `show`, `diff`, `status`) when history matters.

```markdown
# Answer

The answer to the question, in a few sentences.

## Sources

- /abs/path/file.go:42-58
- git -C /abs/repo log -1 --format=%H
```
