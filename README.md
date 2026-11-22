# Custom Agent Skills

A personal collection of custom AI Agent Skills compatible with `gh skill install`. These skills extend AI coding assistants with specialized tools, instructions, and workflows.

## Available Skills

| Skill Name | Description | Installation Command | Compatible Agents |
| :--- | :--- | :--- | :--- |
| `commit-it` | Commit changes following the Conventional Commits specification, verifying test passage first. | `gh skill install handlename/agent-skills commit-it` | Claude Code, Copilot, Gemini CLI, Cursor |
| `create-pr` | Create a Pull Request from the current branch to a base branch, using GitHub CLI to generate a suitable title and description from the commit history. For structural changes, attaches a big-picture diagram as an SVG image (drawn via `mermaid-to-svg` and attached to the created PR via `github-attachment-upload`, mermaid source kept in a collapsed details block, falling back to an inline mermaid block). | `gh skill install handlename/agent-skills create-pr` | Claude Code, Copilot, Gemini CLI, Cursor |
| `draw-mermaid` | Draw a mermaid diagram from a natural-language intent and return a syntax-validated mermaid source, verified with `mermaid.parse()` against a vendored, version-pinned mermaid.js run in a browser via playwright-cli (offline, deterministic). Designed to be called from other skills (e.g. `plan-to-issue`, `create-pr`) through a subagent. | `gh skill install handlename/agent-skills draw-mermaid` | Claude Code, Copilot, Gemini CLI, Cursor |
| `github-attachment-upload` | Attach a local file to an **existing** GitHub issue or PR with the GitHub CLI (`gh --attach`, requires gh 2.99.0+) and return the resulting `user-attachments` URL plus the embed location. File-type agnostic (SVG, PNG, screenshots, video, …); the target issue/PR is supplied by the caller and the file is added as a new comment or into the target body (caller's choice). Does not create a throwaway issue. Designed to be called from other skills (e.g. `plan-to-issue`, `create-pr`) through a subagent. | `gh skill install handlename/agent-skills github-attachment-upload` | Claude Code, Copilot, Gemini CLI, Cursor |
| `github-resource-access` | Rules for accessing GitHub resources with the `gh` CLI: reads are free, writes require an explicit instruction. Includes a quick reference of read vs. write commands, red flags for unrequested changes, and exceptions for skills whose purpose is a write operation. | `gh skill install handlename/agent-skills github-resource-access` | Claude Code, Copilot, Gemini CLI, Cursor |
| `go1.27-changes` | Reference for what changed in Go 1.27 — read when writing or reviewing Go code in a `go 1.27`+ module so pre-1.27 training-data assumptions don't creep in. Covers generic methods, new/changed standard-library APIs (`encoding/json/v2`, `uuid`, `crypto/mldsa`, `strings.CutLast`, …), and dangerous behavior changes (unbuffered time channels, `UnixConn` raw `io.EOF`, localhost-only `go tool trace -http`), each linked to official docs with a verify-before-use rule. | `gh skill install handlename/agent-skills go1.27-changes` | Claude Code, Copilot, Gemini CLI, Cursor |
| `hwwwn` | Passive reference for what information belongs where and how to phrase code comments. HWWWN routes each kind of information to its channel — How to code, What to test code, Why to the commit log, Why not to code comments (after t_wada) — then gives concrete comment rules: keep it minimal, break lines only at sentence boundaries (no artificial column limit), and never restate what the code shows (a long explanatory comment signals a responsibility that should be separated instead). Language/project conventions take precedence. | `gh skill install handlename/agent-skills hwwwn` | Claude Code, Copilot, Gemini CLI, Cursor |
| `maint-glossary` | Create and maintain a well-structured glossary of domain terms from a requirements document, spec, or codebase, through collaborative refinement — extracting, categorizing, and defining terms for a fresh glossary, and diffing the source to add/revise/deprecate entries when maintaining an existing one. | `gh skill install handlename/agent-skills maint-glossary` | Claude Code, Copilot, Gemini CLI, Cursor |
| `mermaid-to-svg` | Turn a mermaid diagram into a hand-drawn free-layout SVG image file and return its path. Pure and offline (no mermaid-cli, no browser, no upload). Designed to be called from other skills (e.g. `plan-to-issue`, `create-pr`) through a subagent, then paired with `github-attachment-upload` to attach the SVG to an issue/PR. | `gh skill install handlename/agent-skills mermaid-to-svg` | Claude Code, Copilot, Gemini CLI, Cursor |
| `multirepo` | Investigate or edit multiple already-cloned local git repositories in parallel. Fires on a `multirepo: <repo1>, <repo2>, ...` declaration; the main session orchestrates one `multirepo-worker` sub-agent per repo (dispatch and result aggregation only), creating worktrees for edits and leaving commits/PRs to other skills. | `gh skill install handlename/agent-skills multirepo` | Claude Code |
| `plan-to-issue` | Turn a finalized implementation plan or design into a GitHub tracking issue, with a big-picture diagram posted as an SVG image (drawn via `mermaid-to-svg` and attached via `github-attachment-upload` after the issue/comment is posted, mermaid source kept in a collapsed details block, falling back to an inline mermaid block), a PR-granularity checklist (TASK- IDs), plus a self-contained plan posted as an issue artifact (`gh issue artifact`, requires gh 2.103.0+) that lets a context-less person or agent start implementing. | `gh skill install handlename/agent-skills plan-to-issue` | Claude Code, Copilot, Gemini CLI, Cursor |
| `runbook-execution-support` | Support a human operator through a runbook written in a GitHub issue comment: present each step, pre-check the target state, verify the result with evidence, post progress to a Slack thread (bold state heading, `[#number title](URL)` links), and tick the runbook/progress checklists with exact-match edits. The human performs every state-changing action. | `gh skill install handlename/agent-skills runbook-execution-support` | Claude Code, Copilot, Gemini CLI, Cursor |
| `setup-tagpr` | Set up [tagpr](https://github.com/Songmu/tagpr) for automated release management: creates and updates release pull requests for unreleased items, tags them on merge, and creates GitHub Releases. Covers goreleaser (Go) and TypeScript/Node.js projects. | `gh skill install handlename/agent-skills setup-tagpr` | Claude Code, Copilot, Gemini CLI, Cursor |

## Plugins

This repository is also a [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces). Every skill is a plugin of its own, named `handlename-<skill-name>`, so you install only the skills you want.

Add the marketplace once, then install the plugins you need:

```bash
claude plugin marketplace add handlename/agent-skills
claude plugin install handlename-commit-it@handlename
claude plugin install handlename-create-pr@handlename
```

A local clone works as the marketplace too: `claude plugin marketplace add /path/to/agent-skills`. Plugins from a local marketplace load in place, so edits take effect at the next session start or `/reload-plugins`.

A plugin skill is invoked with its plugin name as a prefix, e.g. `/handlename-commit-it:commit-it`. Plugins whose skill invokes other skills declare them as dependencies, and installing the plugin installs them as well (see "Also installs" below).

Every plugin entry in `.claude-plugin/marketplace.json` points at the repository root (`source: "."`) and lists only its own `skills/<skill-name>/` directory, so the plugins read the same directories that `gh skill install` fetches. Both paths share one physical source with no symlinks and no duplicated files.

| Plugin | Install | Also installs | Description |
| :--- | :--- | :--- | :--- |
| `handlename-commit-it` | `/plugin install handlename-commit-it@handlename` |  | Commit changes following the Conventional Commits specification, verifying test passage first. Supports both git and Jujutsu (jj) repositories. |
| `handlename-create-pr` | `/plugin install handlename-create-pr@handlename` | `handlename-draw-mermaid`, `handlename-mermaid-to-svg`, `handlename-github-attachment-upload` | Create a Pull Request from the current branch to a base branch, using GitHub CLI to generate a suitable title and description from the commit history. |
| `handlename-draw-mermaid` | `/plugin install handlename-draw-mermaid@handlename` |  | Draw a mermaid diagram from a natural-language intent and return a syntax-validated mermaid source, checked with `mermaid.parse()` against a vendored, version-pinned mermaid.js via playwright-cli. Designed to be called from other skills through a subagent. |
| `handlename-github-attachment-upload` | `/plugin install handlename-github-attachment-upload@handlename` |  | Attach a local file to an existing GitHub issue/PR via the GitHub CLI (`gh --attach`, gh 2.99.0+), returning the `user-attachments` URL and embed location. File-type agnostic; the target issue/PR is supplied by the caller (comment or body-edit mode), and no throwaway issue is created. Designed to be called from other skills through a subagent. |
| `handlename-github-resource-access` | `/plugin install handlename-github-resource-access@handlename` |  | Rules for accessing GitHub resources with the `gh` CLI: reads are free, writes require an explicit instruction. |
| `handlename-go1.27-changes` | `/plugin install handlename-go1.27-changes@handlename` |  | Reference for Go 1.27's developer-facing changes (language, standard library, `go` command) with gotchas foregrounded, for writing or reviewing Go code without falling back on pre-1.27 training-data assumptions. |
| `handlename-hwwwn` | `/plugin install handlename-hwwwn@handlename` |  | Passive reference for what information belongs where and how to phrase code comments: HWWWN routes How to code, What to test code, Why to the commit log, and Why not to code comments (after t_wada), then gives concrete comment rules — minimal, line breaks only at sentence boundaries, and no comment that restates what the code shows. Language/project conventions take precedence. |
| `handlename-maint-glossary` | `/plugin install handlename-maint-glossary@handlename` |  | Create and maintain a glossary of domain terms from a requirements document, spec, or codebase — extracting, categorizing, and defining terms for a fresh glossary, and diffing the source to add/revise/deprecate entries when maintaining an existing one. |
| `handlename-mermaid-to-svg` | `/plugin install handlename-mermaid-to-svg@handlename` |  | Turn a mermaid diagram into a hand-drawn free-layout SVG image file and return its path. Pure and offline (no mermaid-cli, no browser, no upload). Designed to be called from other skills through a subagent, then paired with `github-attachment-upload`. |
| `handlename-multirepo` | `/plugin install handlename-multirepo@handlename` |  | Investigate or edit multiple already-cloned local git repositories in parallel via one `multirepo-worker` sub-agent per repo. The main session only orchestrates; worktrees are created for edits, and commits/PRs are left to other skills. |
| `handlename-persona-mimic-trainer` | `/plugin install handlename-persona-mimic-trainer@handlename` |  | Train and iteratively refine a custom sub-agent that mimics a specific person's decision-making criteria, cognitive biases, and communication style for mock wall-hitting (pre-communication verification). |
| `handlename-plan-to-issue` | `/plugin install handlename-plan-to-issue@handlename` | `handlename-draw-mermaid`, `handlename-mermaid-to-svg`, `handlename-github-attachment-upload`, `handlename-github-resource-access` | Turn a finalized implementation plan or design into a GitHub tracking issue (overview + PR-granularity checklist plus a self-contained plan issue artifact), or post it as a plan artifact on an existing related issue. |
| `handlename-runbook-execution-support` | `/plugin install handlename-runbook-execution-support@handlename` |  | Support a human operator through a runbook in a GitHub issue comment: step presentation, pre-checks, verification, Slack progress posts, and checklist updates. |
| `handlename-setup-tagpr` | `/plugin install handlename-setup-tagpr@handlename` |  | Set up tagpr for automated release management: creates and updates release pull requests for unreleased items, tags them on merge, and creates GitHub Releases. Supports goreleaser (Go) and TypeScript/Node.js projects. |

### Migrating from the `handlename` bundle plugin

Up to version 0.91.x, all skills were bundled into a single `handlename` plugin. It no longer exists. Uninstall it and install the plugins you want:

```bash
claude plugin uninstall handlename@handlename
claude plugin marketplace update handlename
claude plugin install handlename-commit-it@handlename
```

Skill names change from `handlename:<skill-name>` to `handlename-<skill-name>:<skill-name>`.

## Installation

To install a skill from this repository, run the following command using the GitHub CLI:

```bash
gh skill install handlename/agent-skills <skill-name>
```

### Example: Installing Commit It
```bash
gh skill install handlename/agent-skills commit-it
```

## How it Works
When you install a skill using `gh skill install`, the GitHub CLI:
1. Clones/fetches the specific skill directory under `skills/<skill-name>/` from this repository.
2. Injects tracking and version metadata directly into the frontmatter of your local `SKILL.md`.
3. Places the skill into the appropriate directory depending on your scope:
   - **Project Scope (Default)**: Installed inside your current repository at `.agents/skills/<skill-name>/`
   - **User Scope**: Installed in your home directory (e.g., `~/.gemini/skills/` or similar, depending on your agent configuration).

