# Custom Agent Skills

A personal collection of custom AI Agent Skills compatible with `gh skill install`. These skills extend AI coding assistants with specialized tools, instructions, and workflows.

## Available Skills

Unless noted otherwise, each skill works with Claude Code, Copilot, Gemini CLI, and Cursor. See each skill's `SKILL.md` for details.

| Skill Name | Description |
| :--- | :--- |
| `commit-it` | Commit changes following the Conventional Commits specification, verifying test passage first. Supports both git and Jujutsu (jj) repositories. |
| `create-pr` | Create a Pull Request from the current branch to a base branch, using GitHub CLI to generate a suitable title and description from the commit history. For structural changes, attaches a big-picture diagram as an SVG image (via `mermaid-to-svg` and `github-attachment-upload`), falling back to an inline mermaid block. |
| `draw-mermaid` | Draw a mermaid diagram from a natural-language intent and return a syntax-validated mermaid source, checked with `mermaid.parse()` against a vendored, version-pinned mermaid.js via playwright-cli. Designed to be called from other skills through a subagent. |
| `github-attachment-upload` | Attach a local file to an **existing** GitHub issue or PR with the GitHub CLI (`gh --attach`, requires gh 2.99.0+) and return the `user-attachments` URL plus the embed location. File-type agnostic; the file is added as a new comment or into the target body (caller's choice). Designed to be called from other skills through a subagent. |
| `github-resource-access` | Rules for accessing GitHub resources with the `gh` CLI: reads are free, writes require an explicit instruction. |
| `go1.27-changes` | Reference for Go 1.27's developer-facing changes (language, standard library, `go` command) with gotchas foregrounded, for writing or reviewing Go code without falling back on pre-1.27 training-data assumptions. |
| `hwwwn` | Passive reference for what information belongs where and how to phrase code comments: HWWWN routes How to code, What to test code, Why to the commit log, and Why not to code comments (after t_wada), then gives concrete comment rules. Language/project conventions take precedence. |
| `maint-glossary` | Create and maintain a glossary of domain terms from a requirements document, spec, or codebase — extracting, categorizing, and defining terms for a fresh glossary, and diffing the source to add/revise/deprecate entries when maintaining an existing one. |
| `mermaid-to-svg` | Turn a mermaid diagram into a hand-drawn free-layout SVG image file and return its path. Pure and offline (no mermaid-cli, no browser, no upload). Designed to be called from other skills through a subagent, then paired with `github-attachment-upload`. |
| `multirepo` | Investigate or edit multiple already-cloned local git repositories in parallel via one `multirepo-worker` sub-agent per repo, fired by a `multirepo: <repo1>, <repo2>, ...` declaration. The main session only orchestrates; worktrees are created for edits, and commits/PRs are left to other skills. Claude Code only. |
| `persona-mimic-trainer` | Train and iteratively refine a custom sub-agent that mimics a specific person's decision-making criteria, cognitive biases, and communication style for mock wall-hitting (pre-communication verification). |
| `plan-to-issue` | Turn a finalized implementation plan or design into a GitHub tracking issue (overview, big-picture diagram, PR-granularity checklist with TASK- IDs, and a self-contained detailed comment), or post it as a comment on an existing related issue. |
| `runbook-execution-support` | Support a human operator through a runbook in a GitHub issue comment: step presentation, pre-checks, verification, Slack progress posts, and checklist updates. The human performs every state-changing action. |
| `session-supervisor` | Run a supervisor session in the Claude desktop app that starts, tracks, and auto-archives worker sessions once their committed branch or recorded files are verified (git worktree, or one per non-git directory), keeps a compaction-proof `state.json`, delegates edits and pushes to workers and observation to subagents, and publishes a dashboard artifact rendered from it. Tasks can be grouped into projects, each with a read-only project manager session that reviews results and answers workers' questions. Claude Code desktop app only. |
| `setup-tagpr` | Set up [tagpr](https://github.com/Songmu/tagpr) for automated release management: creates and updates release pull requests for unreleased items, tags them on merge, and creates GitHub Releases. Covers goreleaser (Go) and TypeScript/Node.js projects. |

## Installation

### With the GitHub CLI

```bash
gh skill install handlename/agent-skills <skill-name>
```

When you install a skill using `gh skill install`, the GitHub CLI:
1. Clones/fetches the specific skill directory under `skills/<skill-name>/` from this repository.
2. Injects tracking and version metadata directly into the frontmatter of your local `SKILL.md`.
3. Places the skill into the appropriate directory depending on your scope:
   - **Project Scope (Default)**: Installed inside your current repository at `.agents/skills/<skill-name>/`
   - **User Scope**: Installed in your home directory (e.g., `~/.gemini/skills/` or similar, depending on your agent configuration).

### As a Claude Code plugin

This repository is also a [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces). All skills above are bundled into a single `handlename` plugin, which reads the same `skills/<skill-name>/` directories that `gh skill install` fetches.

```bash
claude plugin marketplace add handlename/agent-skills
claude plugin install handlename@handlename
```
