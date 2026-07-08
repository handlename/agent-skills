---
name: plan-to-issue
description: Use when an implementation plan or design has been finalized in the session and the user wants to record it on GitHub. Either creates a tracking issue (overview + PR-granularity checklist with TASK- IDs) plus a self-contained detailed comment, or — when a related issue already exists (e.g. the plan revises a plan previously posted there) — posts the plan as a self-contained comment on that existing issue, superseding the previous plan comment. Triggers include "issue化して", "計画をissueにして", "turn this plan into an issue", "create a tracking issue for this", "既存のissueにコメントとして投稿して", "計画を issue #N に投稿して".
---

# Plan to Issue

## Overview

Turn an **already-finalized** implementation plan/design into a GitHub issue record, in one of two modes:

- **New-issue mode (default)**: a **tracking issue** whose description holds an overview, a **big-picture diagram** (posted as an attached SVG image, with its mermaid source preserved in a collapsed `<details>` block) giving readers the big picture of the problem/system the issue targets, and a **PR-granularity progress checklist** with stable IDs, plus one or more **self-contained comments** carrying the full plan.
- **Existing-issue mode**: when the plan originates from, revises, or answers an issue that already exists (e.g. a re-design superseding a plan comment posted earlier on that issue), post the plan as a **new self-contained comment on that existing issue** instead of creating a new one.

In both modes the detailed comment is written so that a person or agent with **zero prior context can start implementing from the comment alone**.

This skill does NOT gather requirements or design the solution — that is the job of an interview/investigation upstream. It only structures and posts the issue content.

## When to Use

- A plan/design is settled in the current session and the user asks to record it as an issue.
- A (re)designed plan supersedes or elaborates a plan already posted on an existing issue → use existing-issue mode and post there.
- Triggers: "issue化して", "計画を issue にして", "create a tracking issue", "turn this plan into an issue", "既存の issue にコメントして", "issue #N に投稿して".

## Do Not Use When

- The plan is not yet settled → do not invent it; ask the user where the plan is or send them to interview/investigation first.
- The user wants the work implemented (use multirepo/autopilot/etc.), not recorded.

## Core Principles

- **Self-contained comment test**: the detailed comment must let a context-less agent implement each task from the comment alone. Always include: domain/background, current→changed data flow & impact, per-task implementation steps with concrete `file:line` references, logic/spec with an expected-value table where applicable, and completion criteria.
- **ID correspondence is mandatory**: every checklist item ID in the description MUST appear in the detailed comment, and vice versa, so "implement TASK-A2" is unambiguous.
- **Big-picture diagram is mandatory**: the issue description MUST include a diagram that lets a reader grasp the whole at a glance — the problem/system the issue targets (e.g. affected components and their relationships, current vs. changed data flow, or the state transition being fixed). Author it as **mermaid** — the mermaid source is the **canonical record of the diagram's structure**. Choose the diagram type that best conveys the structure (`flowchart`, `sequenceDiagram`, `erDiagram`, `stateDiagram-v2`, etc.). It is for human comprehension, not implementation detail; keep it high-level. In existing-issue mode there is no new description — place the diagram near the top of the plan comment instead.
- **Diagram is posted as an SVG image; mermaid is preserved in a collapsed `<details>` block**: the diagram is displayed as a **free-layout SVG image**, not a rendered mermaid block. Delegate drawing and uploading to a **subagent invoking the `mermaid-to-issue-image` skill** (input: the mermaid source + target repository; output: an attachment URL, or `FAILED: <reason>`). The main session then embeds `![big picture](<url>)` itself, immediately followed by the mermaid source inside `<details><summary>…</summary>` with a ` ```mermaid ` fence — collapsed by default so it stays out of the way, one click away as the canonical structure record, and it renders as a diagram when expanded (a bonus fallback for contexts that cannot show the image). **Never wrap the mermaid in an HTML comment** (`<!-- -->`): mermaid arrows (`-->`) contain `--`, which terminates the comment early and leaks the rest of the source as visible text.
  - **Update flow**: to change the diagram, edit the mermaid in the details block, re-invoke `mermaid-to-issue-image` with it, and replace the image URL. The SVG layout is throwaway and is redrawn from scratch each time; only the mermaid persists.
  - **Fallback**: if the subagent returns `FAILED` (e.g. GitHub session not logged in, npx cannot fetch @playwright/cli), post a visible ` ```mermaid ` code block instead — the pre-SVG behavior — and report the fallback and its reason to the user. Never let the image block the posting.
- **Provenance header**: when the plan was produced by a structured pipeline (e.g. deep-interview → ralplan consensus), open the comment with a short quote block stating the pipeline and its key stats (interview rounds, final ambiguity %, reviewer verdict and iteration). When the comment is posted for review rather than as an accepted plan, explicitly invite feedback (e.g. "レビュー・コメント歓迎です").
- **Existing-issue mode is non-destructive**: never rewrite the existing issue's description by default. The plan lives in the comment. Link the superseded plan comment and summarize what changed (decision pivots, invalidated ADRs). Update the issue description/checklist only with separate explicit approval.
- **Never write to GitHub without explicit approval** (see GitHub Authorization).

## ID Scheme

Always prefix with `TASK-` so an ID alone identifies what it refers to.

- **Single-phase task** → number only: `TASK-1`, `TASK-7`.
- **Multi-phase task** → a phase-identifying letter + number: `TASK-A1`, `TASK-G7`.
  - Assign each phase a distinct letter (in dependency/logical order; a mnemonic letter is fine if clearer).
  - Number tasks within a phase sequentially.
- Granularity: **one checklist item per PR**. Note dependency order explicitly (e.g. `TASK-A1 → TASK-A2`).
- The scheme applies in both modes. In existing-issue mode the checklist may live inside the comment (e.g. as milestone acceptance criteria) until the plan is accepted.

## Language

- This SKILL.md is written in English.
- The **issue and comment content** must be written in the **user's language**, detected from the session conversation. Do not hardcode a language.

## Process

1. **Confirm precondition** — verify a finalized plan exists in the session. If not, stop and ask the user for the plan; do not fabricate one.
2. **Determine scope & decompose into PRs** — single-repo or cross-repo? Break work into PR-sized units and capture dependencies (e.g. DB migration → application code).
3. **Decide issue topology** — choose one and confirm with the user:
   - **New tracking issue** (default when no related issue exists): ONE issue with a checklist spanning all PRs; propose the target repo (where the originating / user-visible change lives).
   - **Comment on an existing issue** (default when the plan originates from, revises, or answers an existing issue — check the session for an issue the plan refers to, e.g. the issue the original draft was posted on): post the plan as a new comment there. Identify the superseded plan comment (if any) so it can be linked.
   - **Cross-repo alternative**: per-repo issues, offered alongside the single tracking issue.
4. **Draft the description** *(new-issue mode only)* — Overview (what & why) + the **big-picture diagram authored as mermaid** (the draft shows the mermaid source; it becomes an SVG image in step 8) + Scope (in/out) + PR-granularity checklist with `TASK-` IDs and dependency notes.
5. **Draft the self-contained comment(s)** — sections: Provenance header (pipeline, stats, review invitation; and in existing-issue mode a supersession note linking the previous plan comment with a summary of what changed); Background/domain knowledge; Current → changed (data flow & impact); Implementation plan per `TASK-` ID with `file:line` references; Logic/spec + expected-value table; Completion criteria. Keep comment IDs in sync with the checklist.
6. **Apply language detection** — render the issue/comment in the user's session language.
7. **GitHub authorization gate** — present the drafted description and comment(s) to the user (the diagram appears as its mermaid source at this point) and obtain explicit approval BEFORE any GitHub write. The approval covers both the attachment upload in step 8 and the posting in step 9.
8. **Generate & upload the diagram image** — spawn a subagent that invokes the `mermaid-to-issue-image` skill with the mermaid source and the target repository. On success (URL returned): replace the draft's mermaid block with `![big picture](<url>)` followed by the mermaid source in a collapsed `<details>` block. On `FAILED: <reason>`: keep the visible mermaid code block and report the fallback and reason to the user.
9. **Post** — `gh issue create` and/or `gh issue comment`.
10. **Report** — return the created/updated issue and comment URLs (and whether the diagram fell back to inline mermaid).

## Description Template (new-issue mode)

```markdown
## 概要 / Overview
{what and why, a few sentences}

## 全体像 / Big picture
{an SVG image uploaded via mermaid-to-issue-image; the mermaid source stays below in a collapsed details block as the canonical structure record — NOT an HTML comment (`-->` arrows would terminate it). On FAILED fallback: replace the image line with a visible mermaid code block instead.}

![big picture]({user-attachments URL})

<details>
<summary>図のソース / Diagram source (mermaid)</summary>

```mermaid
flowchart LR
    {e.g. affected components / current → changed data flow}
```

</details>

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
> {provenance, e.g. "Deep Interview (N ラウンド、ambiguity X%) → Ralplan (Planner / Architect / Critic、Iteration M で APPROVE) のパイプラインで作成した実装計画です。"}
> {existing-issue mode, if superseding: "前回計画 (<link to previous plan comment>) を置き換える改訂版です。主な変更: <e.g. 決定事項によりクラウドを GCP → AWS に変更、旧 ADR は失効>"}
> {if posted for review: "詳細は以下の通り。レビュー・コメント歓迎です。"}

{existing-issue mode: place the big-picture diagram here, near the top — same image + collapsed-details scheme (and the same mermaid fallback) as the description template}

![big picture]({user-attachments URL})

<details>
<summary>図のソース / Diagram source (mermaid)</summary>

```mermaid
{diagram structure}
```

</details>

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

Follow the `github-resource-access` skill: gh reads are free; **writes (issue create, comment, and the diagram attachment upload performed by `mermaid-to-issue-image`) require explicit user instruction**. Always show drafts and wait for an explicit go-ahead before uploading or posting. Multi-repo PR creation pattern: `gh -R <org>/<repo> ...`.

## Final Checklist

- [ ] Plan was finalized before invoking (not fabricated).
- [ ] Issue topology (new tracking issue vs comment on existing issue) was decided deliberately and confirmed with the user.
- [ ] New-issue mode: tracking issue has overview, scope, and PR-granularity checklist with `TASK-` IDs.
- [ ] Big-picture diagram included (in the description for new-issue mode, near the top of the plan comment for existing-issue mode) and kept high-level for human comprehension.
- [ ] Diagram posted as an SVG image (via a subagent invoking `mermaid-to-issue-image`, after the authorization gate) with the mermaid source preserved in a collapsed `<details>` block right after the image (never an HTML comment — `-->` terminates it) — or, if the subagent FAILED, posted as a visible mermaid code block with the fallback reported to the user.
- [ ] Existing-issue mode: superseded plan comment linked with a summary of changes; issue description left untouched unless separately approved.
- [ ] Provenance header present when the plan came from a structured pipeline; review invitation included when posted for review.
- [ ] Detailed comment is self-contained (background, file:line refs, logic, expected values, completion criteria).
- [ ] Every checklist ID appears in the comment and vice versa.
- [ ] Issue/comment written in the user's detected language.
- [ ] No GitHub write before explicit approval; issue/comment URLs reported.
