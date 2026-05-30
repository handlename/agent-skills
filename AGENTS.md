# AI Agent Instructions

Welcome! This repository is a personal collection of custom agent skills. As an AI assistant working in this repository, you must act as a strict custodian of high-quality instructions.

## 1. Directory Structure Rules
To ensure compatibility with `gh skill install`, you MUST follow this structure:
- All skill files must reside in the `skills/` directory.
- Each skill must have its own subdirectory: `skills/<skill-name>/`.
- Every skill directory must contain a `SKILL.md` file as its entrypoint.
- Do NOT create any folders or files at the repository root except for those explicitly requested. Supporting scripts, resources, or references for a skill must be placed inside `skills/<skill-name>/`.

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

## 3. Validation Checklist
Before completing any task that adds or modifies a skill, you MUST run this validation checklist:
- [ ] The skill is stored at `skills/<skill-name>/SKILL.md`.
- [ ] Frontmatter contains both `name` and `description`.
- [ ] There are no placeholder blocks or "TODO" items inside any markdown file.
- [ ] All code block examples have syntax highlighting specified (e.g., `python`, `bash`, `yaml`).
