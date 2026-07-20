# worktree-setup

A Claude Code plugin that sets up linked git worktrees automatically via a
[SessionStart hook](https://code.claude.com/docs/en/hooks).

## setup-worktree.sh

Prepares the current linked git worktree at session start, in two phases:

1. **Copy** — copies untracked files declared in the main worktree's
   `.worktreeinclude` into the worktree. This automates carrying over
   gitignored config files (`.env.local`, `.claude/settings.local.json`, ...)
   every time a worktree is created.
2. **Run** — executes setup scripts declared in the main worktree
   (`.worktreesetup`), but only for trusted repositories. This automates
   per-worktree bootstrap commands such as `mise trust` or `aqua policy allow`.

Regardless of whether the worktree was created from Zed, the CLI, or anywhere
else, both phases run by the time a Claude Code session starts there.

### How it works

On session start the script:

1. Exits silently unless the current directory is a *linked* git worktree
   (detected by comparing `git rev-parse --git-dir` with `--git-common-dir`).
2. Locates the main worktree (first entry of `git worktree list --porcelain`).
3. **Copy phase** — reads the copy declarations in the main worktree root
   (gitignore-style patterns; the same file format Claude Code's own worktree
   feature uses) and copies each matching untracked file
   (`git ls-files --others --ignored --exclude-from=...`) into the worktree,
   preserving permissions and timestamps (`cp -p`). Skipped if neither exists:
   - `.worktreeinclude` — team-shared list, usually tracked
   - `.worktreeinclude.local` — personal additions, untracked
4. **Run phase** — if the repository is trusted (see below), executes the
   setup scripts found in the main worktree root, in order, with `bash` and
   the worktree root as the working directory:
   - `.worktreesetup` — team-shared setup script, usually tracked
   - `.worktreesetup.local` — personal setup script, untracked

Safety properties:

- **Never overwrites** existing files in the worktree (copy phase).
- **Never follows symlinks** — a symlinked declaration or setup script is
  skipped, so a repository cannot use one to reach files outside itself.
- **Copy phase is idempotent** — a second run copies nothing new.
- **Setup scripts run every session**, so they must be written to be
  idempotent. There is no first-run marker; `mise trust` and
  `aqua policy allow` are safe to re-run.
- A failing setup script is reported to stderr but does **not** abort the
  session.
- **Always exits 0** so a session start is never blocked, even on errors.
- The copied files and the setup scripts being run are printed to stdout,
  which lands in the Claude Code session context — intentional, so you can
  see what was set up.

### Trust model

Copying files is low-risk: a repository can only copy its own untracked files
into its own worktrees. **Executing setup scripts is different** — an arbitrary
`.worktreesetup` shipped by a cloned repository could run any code. To contain
this, setup scripts run **only when the repository is on a trust allowlist that
lives outside any repository**, so a clone cannot authorize itself.

The allowlist is a plain text file:

```
${XDG_CONFIG_HOME:-$HOME/.config}/worktree-setup/trusted
```

One entry per line; `#` comments and blank lines are ignored. Each entry names
either a whole organization or a single repository:

```
# ~/.config/worktree-setup/trusted
github.com/handlename                 # trust every repo under this org
github.com/someorg/somerepo           # trust just this one repo
```

The repository's identity is derived from its `origin` remote
(`git remote get-url origin`), normalized to `host/org/repo`. Both
`.worktreesetup` and `.worktreesetup.local` are gated: if the repository (or
its org) is not listed — or the repository has no `origin` remote — neither
setup script runs. The copy phase is unaffected by the allowlist.

When setup scripts exist but the repository is not trusted, the script does
not stay silent: it prints a notice to the session naming the scripts it did
**not** run and the exact command to add the repository to the allowlist, e.g.

```
setup-worktree: setup scripts found but NOT run — this repository is not trusted:
  - /path/to/main/.worktreesetup
  To run them, add this repository (or its org) to the trust allowlist:
    echo 'github.com/your-org/your-repo' >> "/home/you/.config/worktree-setup/trusted"
```

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

To run bootstrap commands, add a `.worktreesetup` script to the repository
root (and, for machine-local commands, an untracked `.worktreesetup.local`):

```bash
#!/usr/bin/env bash
# .worktreesetup — runs every session; keep it idempotent.
mise trust
aqua policy allow
```

Then add the repository (or its org) to your trust allowlist so the script is
allowed to run — see [Trust model](#trust-model):

```bash
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/worktree-setup"
echo "github.com/your-org" >> "${XDG_CONFIG_HOME:-$HOME/.config}/worktree-setup/trusted"
```

Projects with none of these files are simply left untouched.
