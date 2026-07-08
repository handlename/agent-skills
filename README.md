# Custom Agent Skills

A personal collection of custom AI Agent Skills compatible with `gh skill install`. These skills extend AI coding assistants with specialized tools, instructions, and workflows.

## Available Skills

| Skill Name | Description | Installation Command | Compatible Agents |
| :--- | :--- | :--- | :--- |
| `commit-it` | Commit changes following the Conventional Commits specification, verifying test passage first. | `gh skill install handlename/agent-skills commit-it` | Claude Code, Copilot, Gemini CLI, Cursor |
| `create-pr` | Create a Pull Request from the current branch to a base branch, using GitHub CLI to generate a suitable title and description from the commit history. | `gh skill install handlename/agent-skills create-pr` | Claude Code, Copilot, Gemini CLI, Cursor |
| `hello-world` | A simple verification skill to test GitHub CLI skill installation and agent integration. | `gh skill install handlename/agent-skills hello-world` | Claude Code, Copilot, Gemini CLI, Cursor |
| `mermaid-to-issue-image` | Turn a mermaid diagram into a hand-drawn free-layout SVG, upload it as a GitHub issue attachment via playwright-cli, and return the image URL. Designed to be called from other skills (e.g. `plan-to-issue`) through a subagent. Runs `@playwright/cli` via npx (no preinstall needed); requires a one-time GitHub sign-in in its persistent browser profile. | `gh skill install handlename/agent-skills mermaid-to-issue-image` | Claude Code, Copilot, Gemini CLI, Cursor |
| `plan-to-issue` | Turn a finalized implementation plan or design into a GitHub tracking issue, with a big-picture diagram posted as an SVG image (drawn and uploaded via `mermaid-to-issue-image`, mermaid source kept in a collapsed details block, falling back to an inline mermaid block), a PR-granularity checklist (TASK- IDs), plus a self-contained comment that lets a context-less person or agent start implementing. | `gh skill install handlename/agent-skills plan-to-issue` | Claude Code, Copilot, Gemini CLI, Cursor |
| `review-doc` | Convert specification documents or Markdown notes into highly structured, graphical HTML reports and open them in the default browser. | `gh skill install handlename/agent-skills review-doc` | Claude Code, Copilot, Gemini CLI, Cursor |


## Plugins

This repository is also a [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces).

| Plugin Name | Description | Installation Command |
| :--- | :--- | :--- |
| `worktree-setup` | Copies untracked files declared in `.worktreeinclude` / `.worktreeinclude.local` from the main git worktree into linked worktrees at session start (SessionStart hook). See [plugins/worktree-setup](plugins/worktree-setup/README.md). | `claude plugin marketplace add handlename/agent-skills && claude plugin install worktree-setup@handlename` |

## Installation

To install a skill from this repository, run the following command using the GitHub CLI:

```bash
gh skill install handlename/agent-skills <skill-name>
```

### Example: Installing Hello World
```bash
gh skill install handlename/agent-skills hello-world
```

## How it Works
When you install a skill using `gh skill install`, the GitHub CLI:
1. Clones/fetches the specific skill directory under `skills/<skill-name>/` from this repository.
2. Injects tracking and version metadata directly into the frontmatter of your local `SKILL.md`.
3. Places the skill into the appropriate directory depending on your scope:
   - **Project Scope (Default)**: Installed inside your current repository at `.agents/skills/<skill-name>/`
   - **User Scope**: Installed in your home directory (e.g., `~/.gemini/skills/` or similar, depending on your agent configuration).

