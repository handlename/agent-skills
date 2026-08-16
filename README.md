# Custom Agent Skills

A personal collection of custom AI Agent Skills compatible with `gh skill install`. These skills extend AI coding assistants with specialized tools, instructions, and workflows.

## Available Skills

| Skill Name | Description | Installation Command | Compatible Agents |
| :--- | :--- | :--- | :--- |
| `commit-it` | Commit changes following the Conventional Commits specification, verifying test passage first. | `gh skill install handlename/agent-skills commit-it` | Claude Code, Copilot, Gemini CLI, Cursor |
| `create-pr` | Create a Pull Request from the current branch to a base branch, using GitHub CLI to generate a suitable title and description from the commit history. For structural changes, attaches a big-picture diagram as an SVG image (drawn via `mermaid-to-svg` and uploaded via `github-attachment-upload`, mermaid source kept in a collapsed details block, falling back to an inline mermaid block). | `gh skill install handlename/agent-skills create-pr` | Claude Code, Copilot, Gemini CLI, Cursor |
| `draw-mermaid` | Draw a mermaid diagram from a natural-language intent and return a syntax-validated mermaid source, verified with `mermaid.parse()` against a vendored, version-pinned mermaid.js run in a browser via playwright-cli (offline, deterministic). Designed to be called from other skills (e.g. `plan-to-issue`, `create-pr`) through a subagent. | `gh skill install handlename/agent-skills draw-mermaid` | Claude Code, Copilot, Gemini CLI, Cursor |
| `github-attachment-upload` | Upload an arbitrary local file to GitHub `user-attachments` by driving a browser with playwright-cli against an issue editor, and return the attachment URL (no issue is created). File-type agnostic (SVG, PNG, screenshots, …). Designed to be called from other skills (e.g. `plan-to-issue`, `create-pr`) through a subagent. Runs `@playwright/cli` via npx (no preinstall needed); requires a one-time GitHub sign-in in its persistent browser profile. | `gh skill install handlename/agent-skills github-attachment-upload` | Claude Code, Copilot, Gemini CLI, Cursor |
| `github-resource-access` | Rules for accessing GitHub resources with the `gh` CLI: reads are free, writes require an explicit instruction. Includes a quick reference of read vs. write commands, red flags for unrequested changes, and exceptions for skills whose purpose is a write operation. | `gh skill install handlename/agent-skills github-resource-access` | Claude Code, Copilot, Gemini CLI, Cursor |
| `maint-glossary` | Create and maintain a well-structured glossary of domain terms from a requirements document, spec, or codebase, through collaborative refinement — extracting, categorizing, and defining terms for a fresh glossary, and diffing the source to add/revise/deprecate entries when maintaining an existing one. | `gh skill install handlename/agent-skills maint-glossary` | Claude Code, Copilot, Gemini CLI, Cursor |
| `mermaid-to-svg` | Turn a mermaid diagram into a hand-drawn free-layout SVG image file and return its path. Pure and offline (no mermaid-cli, no browser, no upload). Designed to be called from other skills (e.g. `plan-to-issue`, `create-pr`) through a subagent, then paired with `github-attachment-upload` to publish the SVG. | `gh skill install handlename/agent-skills mermaid-to-svg` | Claude Code, Copilot, Gemini CLI, Cursor |
| `plan-to-issue` | Turn a finalized implementation plan or design into a GitHub tracking issue, with a big-picture diagram posted as an SVG image (drawn via `mermaid-to-svg` and uploaded via `github-attachment-upload`, mermaid source kept in a collapsed details block, falling back to an inline mermaid block), a PR-granularity checklist (TASK- IDs), plus a self-contained comment that lets a context-less person or agent start implementing. | `gh skill install handlename/agent-skills plan-to-issue` | Claude Code, Copilot, Gemini CLI, Cursor |
| `setup-tagpr` | Set up [tagpr](https://github.com/Songmu/tagpr) for automated release management: creates and updates release pull requests for unreleased items, tags them on merge, and creates GitHub Releases. Covers goreleaser (Go) and TypeScript/Node.js projects. | `gh skill install handlename/agent-skills setup-tagpr` | Claude Code, Copilot, Gemini CLI, Cursor |


## Plugins

This repository is also a [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces).

Add the marketplace once, then install the bundled plugin:

```bash
claude plugin marketplace add handlename/agent-skills
claude plugin install handlename@handlename
```

All skills listed under [Available Skills](#available-skills) are bundled into a single `handlename` plugin, together with the `worktree-setup` SessionStart hook. The repository root itself is the plugin (`.claude-plugin/marketplace.json` points at `source: "."`), so the plugin reads the same `skills/<name>/` directories that `gh skill install` fetches. Both paths share one physical source with no symlinks and no duplicated files.

| Component | Type | Description |
| :--- | :--- | :--- |
| `commit-it` | skill | Commit changes following the Conventional Commits specification, verifying test passage first. Supports both git and Jujutsu (jj) repositories. |
| `create-pr` | skill | Create a Pull Request from the current branch to a base branch, using GitHub CLI to generate a suitable title and description from the commit history. |
| `draw-mermaid` | skill | Draw a mermaid diagram from a natural-language intent and return a syntax-validated mermaid source, checked with `mermaid.parse()` against a vendored, version-pinned mermaid.js via playwright-cli. Designed to be called from other skills through a subagent. |
| `github-attachment-upload` | skill | Upload an arbitrary local file to GitHub `user-attachments` via playwright-cli (through an issue editor, no issue created), returning the attachment URL. File-type agnostic. Designed to be called from other skills through a subagent. |
| `github-resource-access` | skill | Rules for accessing GitHub resources with the `gh` CLI: reads are free, writes require an explicit instruction. |
| `maint-glossary` | skill | Create and maintain a glossary of domain terms from a requirements document, spec, or codebase — extracting, categorizing, and defining terms for a fresh glossary, and diffing the source to add/revise/deprecate entries when maintaining an existing one. |
| `mermaid-to-svg` | skill | Turn a mermaid diagram into a hand-drawn free-layout SVG image file and return its path. Pure and offline (no mermaid-cli, no browser, no upload). Designed to be called from other skills through a subagent, then paired with `github-attachment-upload`. |
| `persona-mimic-trainer` | skill | Train and iteratively refine a custom sub-agent that mimics a specific person's decision-making criteria, cognitive biases, and communication style for mock wall-hitting (pre-communication verification). |
| `plan-to-issue` | skill | Turn a finalized implementation plan or design into a GitHub tracking issue (overview + PR-granularity checklist plus a self-contained detailed comment), or post it as a comment on an existing related issue. |
| `setup-tagpr` | skill | Set up tagpr for automated release management: creates and updates release pull requests for unreleased items, tags them on merge, and creates GitHub Releases. Supports goreleaser (Go) and TypeScript/Node.js projects. |
| `worktree-setup` | hook | At session start (SessionStart hook), copies untracked files declared in `.worktreeinclude` from the main git worktree into linked worktrees, and runs setup scripts declared in `.worktreesetup` (e.g. `mise trust`, `aqua policy allow`) for repositories on a trust allowlist. |

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

