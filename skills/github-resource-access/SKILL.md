---
name: github-resource-access
description: Use when accessing GitHub resources such as issues, PRs, repositories, or secrets. Enforces "use the gh CLI; reads are free, writes require explicit instruction".
---

# GitHub Resource Access

## Overview

Rules for accessing GitHub resources: **use the `gh` CLI. Reads are free; writes require an explicit instruction.**

## When to Use

- Reading or changing issue / PR / repository information.
- Any time you want to access a resource on GitHub.
- Any time you feel tempted to change something on GitHub "while you're at it".

## Core Rules

1. Use the `gh` CLI to access GitHub resources.
2. **Read = always allowed.**
3. **Write = requires an explicit instruction from the user.**

Decision procedure for any GitHub access:

1. Is it a read operation? → Yes: run it.
2. Otherwise, is there an explicit user instruction for this write? → Yes: run it.
3. Neither → do not run it. Propose it to the user instead.

A **write operation** is any of:

- Creating, updating, or closing an issue or PR.
- Posting a comment.
- Changing labels or assignees.
- Setting secrets.

## Quick Reference

| Operation | Allowed | Example `gh` commands |
|-----------|---------|-----------------------|
| Read | Always | `gh issue view`, `gh pr list`, `gh repo view`, `gh secret list` |
| Write | Instruction required | `gh issue create`, `gh pr create`, `gh issue comment`, `gh secret set` |

## Red Flags

If any of these thoughts occur to you, **stop** — do not run the write. Propose it to the user and wait for approval.

| Thought | Correct action |
|---------|----------------|
| "I'll add a label while I'm at it." | Propose it and wait for approval. |
| "It'd be handy to comment a reference link." | Propose it and wait for approval. |
| "I'll close this issue." | Report to the user and wait for instruction. |
| "I'll mention the related PR." | Propose it and wait for approval. |
| "I found this info during investigation — I'll comment it." | Propose it and wait for approval. |

## Exceptions

When a skill's **explicit purpose is a write operation**, that write is authorized as part of running the skill — no separate confirmation is needed. Examples in this repository:

- `create-pr` — creating a pull request.
- `plan-to-issue` — creating an issue or posting a comment.
- `setup-tagpr` — configuring release automation (e.g. workflow files, secrets, release PRs).
