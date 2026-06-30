# Custom Agent Skills

A personal collection of custom AI Agent Skills compatible with `gh skill install`. These skills extend AI coding assistants with specialized tools, instructions, and workflows.

## Available Skills

| Skill Name | Description | Installation Command | Compatible Agents |
| :--- | :--- | :--- | :--- |
| `commit-it` | Commit changes following the Conventional Commits specification, verifying test passage first. | `gh skill install handlename/agent-skills commit-it` | Claude Code, Copilot, Gemini CLI, Cursor |
| `create-pr` | Create a Pull Request from the current branch to a base branch, using GitHub CLI to generate a suitable title and description from the commit history. | `gh skill install handlename/agent-skills create-pr` | Claude Code, Copilot, Gemini CLI, Cursor |
| `hello-world` | A simple verification skill to test GitHub CLI skill installation and agent integration. | `gh skill install handlename/agent-skills hello-world` | Claude Code, Copilot, Gemini CLI, Cursor |
| `plan-to-issue` | Turn a finalized implementation plan or design into a GitHub tracking issue, with a PR-granularity checklist (TASK- IDs) plus a self-contained comment that lets a context-less person or agent start implementing. | `gh skill install handlename/agent-skills plan-to-issue` | Claude Code, Copilot, Gemini CLI, Cursor |
| `review-doc` | Convert specification documents or Markdown notes into highly structured, graphical HTML reports and open them in the default browser. | `gh skill install handlename/agent-skills review-doc` | Claude Code, Copilot, Gemini CLI, Cursor |


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

