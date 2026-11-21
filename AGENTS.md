# AI Agent Instructions

Welcome! This repository is a personal collection of custom agent skills. As an AI assistant working in this repository, you must act as a strict custodian of high-quality instructions.

## 1. Directory Structure Rules
To ensure compatibility with `gh skill install`, you MUST follow this structure:
- All skill files must reside in the `skills/` directory.
- Each skill must have its own subdirectory: `skills/<skill-name>/`.
- Every skill directory must contain a `SKILL.md` file as its entrypoint.
- Supporting scripts, resources, or references for a skill must be placed inside `skills/<skill-name>/`, never at the repository root.
- The repository root is also a Claude Code plugin marketplace. `.claude-plugin/marketplace.json` declares one plugin per skill, named `handlename-<skill-name>`, each with `source: "."` and `skills: ["./skills/<skill-name>"]`, so every plugin reads the same `skills/` directories — no symlinks, no duplicated files. Only the following root-level entry supports the marketplace and may exist alongside `skills/`: `.claude-plugin/` (`marketplace.json` only). Do NOT create any other folders or files at the repository root except for those explicitly requested.
- Do NOT add `.claude-plugin/plugin.json`: with it present, every plugin entry takes its name from that file and the per-skill plugins collide.
- When adding a skill, add its `handlename-<skill-name>` entry to `marketplace.json`. When a skill invokes other skills, list their plugins in the entry's `dependencies`.

## 2. SKILL.md Standards
Every `SKILL.md` file MUST start with a syntactically valid YAML frontmatter:
```yaml
---
name: <kebab-case-name>
description: <clear-1-sentence-actionable-description-starting-with-action-verb>
---
```

### Prompt Design Guidelines
- Write highly actionable, clear, and context-aware instructions.
- Keep bullet points concise and use standard markdown.
- No placeholders, TBD, or TODOs.
- Structure instructions logically, using headings like `## Overview`, `## Instructions`, and `## Verification`.

## 3. Versioning
Whenever you change anything in this repository (skills, hooks, docs, or configuration), you MUST bump the plugin version following [Semantic Versioning](https://semver.org/):
- The version is the `version` of every plugin entry in `.claude-plugin/marketplace.json`. All entries share one version; bump them together and keep them identical.
- MAJOR: incompatible changes to a skill's interface or the plugin structure.
- MINOR: backward-compatible additions, such as a new skill or hook.
- PATCH: backward-compatible fixes or documentation-only changes.

## 4. Validation Checklist
Before completing any task that adds or modifies a skill, you MUST run this validation checklist:
- [ ] The skill is stored at `skills/<skill-name>/SKILL.md`.
- [ ] Frontmatter contains both `name` and `description`.
- [ ] There are no placeholder blocks or "TODO" items inside any markdown file.
- [ ] All code block examples have syntax highlighting specified (e.g., `python`, `bash`, `yaml`).
- [ ] The skill has its `handlename-<skill-name>` entry in `.claude-plugin/marketplace.json`, with `dependencies` for the skills it invokes.
- [ ] The version of every plugin entry in `.claude-plugin/marketplace.json` is bumped per Semantic Versioning and identical.
- [ ] `claude plugin validate .` passes.
