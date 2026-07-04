# worktree-setup

A Claude Code plugin that sets up linked git worktrees automatically via a
[SessionStart hook](https://code.claude.com/docs/en/hooks).

## setup-worktree.sh

Copies untracked files declared in the main worktree's `.worktreeinclude` into
the current linked git worktree. This automates the manual step of carrying
over gitignored config files (`.env.local`, `.claude/settings.local.json`, ...)
every time a worktree is created — regardless of whether the worktree was
created from Zed, the CLI, or anywhere else, the files are in place by the
time a Claude Code session starts there.

### How it works

On session start the script:

1. Exits silently unless the current directory is a *linked* git worktree
   (detected by comparing `git rev-parse --git-dir` with `--git-common-dir`).
2. Locates the main worktree (first entry of `git worktree list --porcelain`).
3. Reads the declaration files in the main worktree root (gitignore-style
   patterns; the same file format Claude Code's own worktree feature uses).
   Exits silently if neither exists:
   - `.worktreeinclude` — team-shared list, usually tracked
   - `.worktreeinclude.local` — personal additions, untracked
4. Copies each untracked file in the main worktree matching the patterns
   (`git ls-files --others --ignored --exclude-from=...`) into the worktree,
   preserving permissions and timestamps (`cp -p`). Files matching both
   declarations are copied once.

Safety properties:

- **Never overwrites** existing files in the worktree.
- **Never follows symlinks** — a symlink in the main worktree is skipped, so
  a repository cannot use one to pull files from outside itself.
- **Idempotent** — a second run is a no-op.
- **Always exits 0** so a session start is never blocked, even on errors.
- Copies only; symlink creation and setup command execution are out of scope.

Trust model: `.worktreeinclude` is trusted at the same level as anything else
in the repository (Makefiles, git hooks, ...). A repository can only declare
copies of its own untracked files into its own worktrees; review the file as
you would any other repo content when working with untrusted repositories.
The list of copied files is printed to stdout, which lands in the Claude Code
session context — intentional, so you can see what was set up.

### Requirements

- bash 3.2+ (the macOS default is sufficient)
- git 2.31+ (for `rev-parse --path-format=absolute`; on older git the script
  silently no-ops)

On very large working trees the `git ls-files --others` scan can take a few
seconds; it only runs in linked worktrees of projects that opted in via
`.worktreeinclude`.

### Installation

Install as a plugin (recommended):

```bash
claude plugin marketplace add handlename/agent-skills
claude plugin install worktree-setup@handlename --scope user
```

Or interactively: `/plugin marketplace add handlename/agent-skills` then
`/plugin install worktree-setup@handlename`.

Alternatively, register the script manually in the `SessionStart` hooks of
your Claude Code settings (`~/.claude/settings.json`), using an absolute
path (do not combine with the plugin install — the hook would run twice):

```json
{
  "type": "command",
  "command": "/path/to/agent-skills/plugins/worktree-setup/hooks/setup-worktree.sh"
}
```

### Per-project setup

Put a `.worktreeinclude` file in the repository root of each project,
listing the gitignored files to carry over:

```gitignore
.env.*
.claude/settings.local.json
.claude/**/*.local.md
```

When `.worktreeinclude` is shared with your team (tracked in git), declare
personal, machine-local files in `.worktreeinclude.local` instead — it is
read in addition to the shared file and should stay untracked:

```gitignore
# .worktreeinclude.local
.mise.local.toml
zed.local
```

Projects with neither file are simply left untouched.
