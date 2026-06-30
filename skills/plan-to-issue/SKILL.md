---
name: plan-to-issue
description: Use when an implementation plan or design has been finalized in the session and the user wants to turn it into a GitHub issue. Creates a tracking issue (overview + PR-granularity checklist with TASK- IDs) plus a self-contained detailed comment so a person or agent with no prior context can implement it. Triggers include "issue化して", "計画をissueにして", "turn this plan into an issue", "create a tracking issue for this".
---

# Plan to Issue

## Overview

Turn an **already-finalized** implementation plan/design into a GitHub issue:

- A **tracking issue** whose description holds an overview and a **PR-granularity progress checklist** with stable IDs.
- One or more **self-contained comments** carrying the full plan, written so that a person or agent with **zero prior context can start implementing from the comment alone**.

This skill does NOT gather requirements or design the solution — that is the job of an interview/investigation upstream. It only structures and posts the issue.

## When to Use

- A plan/design is settled in the current session and the user asks to record it as an issue.
- Triggers: "issue化して", "計画を issue にして", "create a tracking issue", "turn this plan into an issue".

## Do Not Use When

- The plan is not yet settled → do not invent it; ask the user where the plan is or send them to interview/investigation first.
- The user wants the work implemented (use multirepo/autopilot/etc.), not recorded.

## Core Principles

- **Self-contained comment test**: the detailed comment must let a context-less agent implement each task from the comment alone. Always include: domain/background, current→changed data flow & impact, per-task implementation steps with concrete `file:line` references, logic/spec with an expected-value table where applicable, and completion criteria.
- **ID correspondence is mandatory**: every checklist item ID in the description MUST appear in the detailed comment, and vice versa, so "implement TASK-A2" is unambiguous.
- **Never write to GitHub without explicit approval** (see GitHub Authorization).

## ID Scheme

Always prefix with `TASK-` so an ID alone identifies what it refers to.

- **Single-phase task** → number only: `TASK-1`, `TASK-7`.
- **Multi-phase task** → a phase-identifying letter + number: `TASK-A1`, `TASK-G7`.
  - Assign each phase a distinct letter (in dependency/logical order; a mnemonic letter is fine if clearer).
  - Number tasks within a phase sequentially.
- Granularity: **one checklist item per PR**. Note dependency order explicitly (e.g. `TASK-A1 → TASK-A2`).

## Language

- This SKILL.md is written in English.
- The **issue and comment content** must be written in the **user's language**, detected from the session conversation. Do not hardcode a language.

## Process

1. **Confirm precondition** — verify a finalized plan exists in the session. If not, stop and ask the user for the plan; do not fabricate one.
2. **Determine scope & decompose into PRs** — single-repo or cross-repo? Break work into PR-sized units and capture dependencies (e.g. DB migration → application code).
3. **Decide issue topology** — default to ONE tracking issue with a checklist spanning all PRs. For cross-repo work, also offer per-repo issues as an alternative. Propose the target repo (where the originating / user-visible change lives) and confirm with the user.
4. **Draft the description** — Overview (what & why) + Scope (in/out) + PR-granularity checklist with `TASK-` IDs and dependency notes.
5. **Draft the self-contained comment(s)** — sections: Background/domain knowledge; Current → changed (data flow & impact); Implementation plan per `TASK-` ID with `file:line` references; Logic/spec + expected-value table; Completion criteria. Keep comment IDs in sync with the checklist.
6. **Apply language detection** — render the issue/comment in the user's session language.
7. **GitHub authorization gate** — present the drafted description and comment(s) to the user and obtain explicit approval BEFORE running any `gh` write. Then `gh issue create` and `gh issue comment`.
8. **Report** — return the created issue and comment URLs.

## Description Template

```markdown
## 概要 / Overview
{what and why, a few sentences}

## スコープ / Scope
- 対象 / In scope: ...
- 対象外 / Out of scope: ...

## 進捗チェックリスト / Progress checklist
{dependency note, e.g. TASK-A1 → TASK-A2 → (TASK-B1, TASK-C1 parallel)}

- [ ] **TASK-A1** {repo}: {PR-level work}
- [ ] **TASK-A2** {repo}: {PR-level work} ※after TASK-A1
- [ ] **TASK-B1** {repo}: {PR-level work}

> 詳細はコメント参照 / See the comment for the self-contained plan.
```

## Comment Template

```markdown
# Implementation plan (self-contained)

Goal: a person/agent without prior context can implement from this comment alone.

## 1. Background / domain knowledge
...

## 2. Current → changed (data flow & impact)
| layer | current | change |
|---|---|---|

## 3. Implementation plan (per TASK- ID)
### TASK-A1: {title}
- References: `path/to/file.go:120`, ...
- Steps: ...

## 4. Logic / spec & expected values
{rules + expected-value table}

## 5. Completion criteria
- ...
```

## GitHub Authorization

Follow the `github-resource-access` skill: gh reads are free; **writes (issue create, comment) require explicit user instruction**. Always show drafts and wait for an explicit go-ahead before posting. Multi-repo PR creation pattern: `gh -R <org>/<repo> ...`.

## Final Checklist

- [ ] Plan was finalized before invoking (not fabricated).
- [ ] Tracking issue has overview, scope, and PR-granularity checklist with `TASK-` IDs.
- [ ] Detailed comment is self-contained (background, file:line refs, logic, expected values, completion criteria).
- [ ] Every checklist ID appears in the comment and vice versa.
- [ ] Issue/comment written in the user's detected language.
- [ ] No GitHub write before explicit approval; issue/comment URLs reported.
