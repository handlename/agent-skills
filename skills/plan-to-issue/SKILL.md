---
name: plan-to-issue
description: Use when an implementation plan or design has been finalized in the session and the user wants to record it on GitHub, OR when progress on an already-tracked plan needs to be reflected back to its issue. Three modes — (1) create a tracking issue (overview + a dedicated progress-checklist comment with TASK- IDs) plus a self-contained detailed comment; (2) when a related issue already exists, post the plan as a self-contained comment on that existing issue, superseding the previous plan comment; (3) progress-update mode — update the progress-checklist comment (append a PR URL under its TASK, or check the box when merged). Any write to an existing issue first runs an issue-primacy gate (re-fetch the issue, check its last-updated time, show a diff and ask before merging). Triggers include "issue化して", "計画をissueにして", "turn this plan into an issue", "create a tracking issue for this", "既存のissueにコメントとして投稿して", "計画を issue #N に投稿して", "進捗を更新して", "update the progress", "PRのURLを進捗に反映して".
---

# Plan to Issue

## Overview

Turn an **already-finalized** implementation plan/design into a GitHub issue record, and keep that record's progress in sync, in one of three modes:

- **New-issue mode (default)**: a **tracking issue** whose description holds an overview and a **big-picture diagram** (posted as an attached SVG image, with its mermaid source preserved in a collapsed `<details>` block), plus a **dedicated progress-checklist comment** carrying a **PR-granularity checklist** with stable `TASK-` IDs, plus one or more **self-contained comments** carrying the full plan.
- **Existing-issue mode**: when the plan originates from, revises, or answers an issue that already exists (e.g. a re-design superseding a plan comment posted earlier on that issue), post the plan as a **new self-contained comment on that existing issue** instead of creating a new one.
- **Progress-update mode**: when a PR has been created for a task, or a PR has merged, reflect that back into the tracking issue's **progress-checklist comment** — append the PR URL as a sub-item under its `TASK-` line, or check the box when merged. This mode is normally run **in the same session that created the PR**.

In new-issue and existing-issue modes the detailed comment is written so that a person or agent with **zero prior context can start implementing from the comment alone**.

Any write to an **existing** issue (progress-update, or an existing-issue plan-comment revision) first passes the **issue-primacy gate** (see Core Principles): the issue on GitHub is authoritative, may have been edited by others, and must be re-fetched and reconciled before writing.

This skill does NOT gather requirements or design the solution — that is the job of an interview/investigation upstream. It only structures, posts, and keeps in sync the issue content.

## When to Use

- A plan/design is settled in the current session and the user asks to record it as an issue.
- A (re)designed plan supersedes or elaborates a plan already posted on an existing issue → use existing-issue mode and post there.
- A PR has just been created (or merged) for a task on a tracked plan and its progress should be reflected → use progress-update mode. When a PR is created in a session that holds a tracking-issue context, **proactively offer to update the progress** (see Progress-Update Mode).
- Triggers: "issue化して", "計画を issue にして", "create a tracking issue", "turn this plan into an issue", "既存の issue にコメントして", "issue #N に投稿して", "進捗を更新して", "update the progress", "PR の URL を進捗に反映して".

## Do Not Use When

- The plan is not yet settled → do not invent it; ask the user where the plan is or send them to interview/investigation first.
- The user wants the work implemented (use multirepo/autopilot/etc.), not recorded.

## Core Principles

- **The issue on GitHub is the source of truth (issue-primacy)**: an existing issue may have been read by others and may have been edited by the user or others since this session last saw it. Before **any** write to an existing issue (progress-update, or an existing-issue plan-comment revision), run the **issue-primacy gate**:
  1. **Re-fetch** the current issue and the relevant comment via `gh` (never write from a stale in-session copy).
  2. **Check the last-updated time** (`gh issue view` `updatedAt`, and the target comment's `updatedAt` via the API) against what this session assumes.
  3. If the current content **diverges** from what the session expects (i.e. it changed since the session's knowledge), **present the diff** — session's assumption vs. current issue content — and **ask the user how to reconcile**, section by section. **Never silently overwrite concurrent human edits.**
  4. Merging any content requires **explicit user confirmation**. Apply only the session-originated change (e.g. one PR URL, one checkbox) on top of the current content; leave everything else as-is.
- **The issue description is non-destructive**: never rewrite the issue's description by default, in any mode. The progress checklist and the plan live in **comments**, not the description. Update the description only with separate, explicit approval.
- **Self-contained comment test**: the detailed plan comment must let a context-less agent implement each task from the comment alone. Always include: domain/background, current→changed data flow & impact, per-task implementation steps with concrete `file:line` references, logic/spec with an expected-value table where applicable, any constraints, caveats or rationale that do not fit on the checklist line, and completion criteria.
- **ID correspondence is mandatory**: every checklist item ID in the progress-checklist comment MUST appear in the detailed plan comment, and vice versa, so "implement TASK-A2" is unambiguous and so a checklist item's detail can be found by matching its ID — the only route, since items are not linked.
- **Big-picture diagram is mandatory**: the issue MUST include a diagram that lets a reader grasp the whole at a glance — the problem/system the issue targets (e.g. affected components and their relationships, current vs. changed data flow, or the state transition being fixed). Author it as **mermaid** — the mermaid source is the **canonical record of the diagram's structure**. Choose the diagram type that best conveys the structure (`flowchart`, `sequenceDiagram`, `erDiagram`, `stateDiagram-v2`, etc.). **Author the mermaid via a subagent invoking the `draw-mermaid` skill** (input: the diagram intent; output: a syntax-validated mermaid source, or `FAILED: <reason>`) so it is guaranteed to parse before it is shown or handed to `mermaid-to-svg`. It is for human comprehension, not implementation detail; keep it high-level. In new-issue mode it lives in the description; in existing-issue mode place it near the top of the plan comment.
- **Diagram is posted as an SVG image; mermaid is preserved in a collapsed `<details>` block**: the diagram is displayed as a **free-layout SVG image**, not a rendered mermaid block. `github-attachment-upload` attaches to an **existing** issue/PR (it does not create a throwaway one), so the image is embedded **after** its container is posted. Produce it via **subagents**: first `mermaid-to-svg` (input: the mermaid source; output: an SVG file path, or `FAILED: <reason>`), then — once the container exists — `github-attachment-upload` (input: that SVG path + the target `owner/repo`, issue/PR number, `target_type`, and `mode`; output: two lines `url:` / `location:`, or `FAILED: <reason>`). Which container and mode:
  - **New-issue mode** (diagram in the description): create the issue first with the mermaid in its fallback `<details>` form, then call `github-attachment-upload` with `target_type=issue`, `mode=edit`, and finally set the description via `gh issue edit --body` so `![big picture](<url>)` sits in the Big picture section, immediately followed by the mermaid source inside `<details><summary>…</summary>` with a ` ```mermaid ` fence.
  - **Existing-issue mode** (diagram in the plan comment; the description stays untouched): mint the URL with `github-attachment-upload` `target_type=issue`, `mode=comment` on the issue, embed the returned `![big picture](<url>)` into the plan comment above the collapsed mermaid `<details>`, then delete the helper comment (`gh api -X DELETE repos/<owner>/<repo>/issues/comments/<id>`; an author may delete their own comment).
  The collapsed mermaid is one click away as the canonical structure record, and it renders as a diagram when expanded (a bonus fallback for contexts that cannot show the image). **Never wrap the mermaid in an HTML comment** (`<!-- -->`): mermaid arrows (`-->`) contain `--`, which terminates the comment early and leaks the rest of the source as visible text.
  - **Update flow**: to change the diagram, edit the mermaid in the details block, re-run `mermaid-to-svg` to redraw the SVG, re-invoke `github-attachment-upload` against the same container with the new file, and replace the image URL. The SVG layout is throwaway and is redrawn from scratch each time; only the mermaid persists.
  - **Fallback**: if either subagent returns `FAILED` (`mermaid-to-svg` could not draw the SVG, or `github-attachment-upload` hit e.g. `gh` older than 2.99.0 or the attach did not complete), post a visible ` ```mermaid ` code block instead — the pre-SVG behavior — and report the fallback and its reason to the user. Never let the image block the posting.
- **Provenance header**: when the plan was produced by a structured pipeline (e.g. deep-interview → ralplan consensus), open the comment with a short quote block stating the pipeline and its key stats (interview rounds, final ambiguity %, reviewer verdict and iteration). When the comment is posted for review rather than as an accepted plan, explicitly invite feedback (e.g. "レビュー・コメント歓迎です").
- **Existing-issue mode is non-destructive**: never rewrite the existing issue's description by default. The plan lives in the comment. Link the superseded plan comment and summarize what changed (decision pivots, invalidated ADRs).
- **Never write to GitHub without explicit approval** (see GitHub Authorization).

## Progress Checklist (format & location)

- **Location**: the progress checklist lives in a **dedicated progress-checklist comment** on the tracking issue — NOT in the description. The description references it (e.g. "進捗は進捗チェックリストコメント参照 / See the progress-checklist comment"). This keeps the description non-destructive and the living progress tracker isolated and easy to edit.
- **One item, one line**: each checklist item MUST fit in **70 characters or fewer** (count characters plainly — do not distinguish full-width from half-width). The line carries only a short noun-phrase summary of the deliverable. Constraints, rationale, file names, caveats and type/status tags such as `[設定]` / `[freeze]` / `[判定]` do NOT belong on it — they go under the matching `### TASK-<ID>:` heading in the plan comment, which the reader finds by the `TASK-` ID. If the summary will not fit, shorten the deliverable phrase — never the `TASK-` ID or the repo — and move the detail to the plan comment. This section is the single statement of the rule; everywhere else points here.

  ```markdown
  Good: - [ ] **TASK-A1** agent-skills: リリース準備 workflow 追加
  Bad:  - [ ] **TASK-A1** agent-skills: s1-PR1 加算的準備 — set-commit-status composite action（NEW）、release-gate.yml（NEW・この時点では必須 check 化しない）…
  ```

- **Format**: one checklist item per PR-granularity task. The `TASK-` line carries the checkbox and the summary only. **Dependency notes and PR URLs are both indented sub-items**, one per line.

  ```markdown
  - [ ] **TASK-A1** {repo}: {PR-level work}
      - https://github.com/handlename/agent-skills/pull/1
  - [ ] **TASK-A2** {repo}: {PR-level work}
      - ※after TASK-A1
  ```

- **A pointer to the detail, not a link per item**: do NOT link individual items. The progress-checklist comment opens with one plain-text pointer line naming the plan comment (see the template). GitHub's current issue UI gives headings in issue bodies and comments **no anchor ids**, so `#user-content-<slug>` does not resolve; readers navigate by matching the `TASK-` ID.

- **State transitions** (the only mutations progress-update mode performs):
  - **PR created for a task** → append that PR's URL as an indented sub-item under the task's `TASK-` line. Multiple PRs for one task → multiple sub-items.
  - **PR merged** → check the task's box (`- [ ]` → `- [x]`). Appending the URL and checking the box are **separate stages**: a created-but-unmerged PR gets its URL but the box stays unchecked.

## Progress-Update Mode

- **Purpose**: reflect PR progress back into the tracking issue's progress-checklist comment.
- **Triggers**:
  - The user says "進捗を更新して" / "update the progress".
  - **Proactive**: when a PR is created in a session that holds a tracking-issue context, offer to update the progress (e.g. "TASK-A1 の PR を作成しました。進捗チェックリストに反映しますか？"). This proactive offer is documented **here**; the `create-pr` skill is intentionally **not** modified.
- **Identifying the target**: the issue number and the `TASK-` ID come from the **session context** — progress-update is expected to run in the same session that implemented the task and created the PR, so both are already known. **Do not auto-discover** the PR↔TASK mapping or merge state via `gh pr list`/branch/title matching. If the session context does not actually carry the issue# or TASK-ID, ask the user rather than guessing.
- **Flow**:
  1. Run the **issue-primacy gate** (re-fetch the issue and its progress-checklist comment; check last-updated time; if diverged, show the diff and get reconciliation confirmation).
  2. Apply the single session-originated change to the progress-checklist comment: append the PR URL sub-item (PR created), and/or check the box (PR merged).
  3. Get explicit approval, then write with `gh` (edit the comment).
  4. Report the updated comment URL.

## ID Scheme

Always prefix with `TASK-` so an ID alone identifies what it refers to.

- **Single-phase task** → number only: `TASK-1`, `TASK-7`.
- **Multi-phase task** → a phase-identifying letter + number: `TASK-A1`, `TASK-G7`.
  - Assign each phase a distinct letter (in dependency/logical order; a mnemonic letter is fine if clearer).
  - Number tasks within a phase sequentially.
- Granularity: **one checklist item per PR**. Record dependency order as an indented `※after TASK-X` sub-item under the dependent task, never inline on the item line.
- The scheme applies in all modes. The checklist lives in the dedicated progress-checklist comment.

## Language

- This SKILL.md is written in English.
- The **issue and comment content** must be written in the **user's language**, detected from the session conversation. Do not hardcode a language.

## Process

### Creating (new-issue / existing-issue mode)

1. **Confirm precondition** — verify a finalized plan exists in the session. If not, stop and ask the user for the plan; do not fabricate one.
2. **Determine scope & decompose into PRs** — single-repo or cross-repo? Break work into PR-sized units and capture dependencies (e.g. DB migration → application code).
3. **Decide issue topology** — choose one and confirm with the user:
   - **New tracking issue** (default when no related issue exists): ONE issue with a dedicated progress-checklist comment spanning all PRs; propose the target repo (where the originating / user-visible change lives).
   - **Comment on an existing issue** (default when the plan originates from, revises, or answers an existing issue — check the session for an issue the plan refers to): post the plan as a new comment there. Identify the superseded plan comment (if any) so it can be linked. **Run the issue-primacy gate first** (this is a write to an existing issue).
   - **Cross-repo alternative**: per-repo issues, offered alongside the single tracking issue.
4. **Draft the description** *(new-issue mode only)* — Overview (what & why) + the **big-picture diagram authored as mermaid via a subagent invoking `draw-mermaid`** (the draft shows the syntax-validated mermaid source; it becomes an SVG image in step 8) + Scope (in/out) + a pointer to the progress-checklist comment. The description does **not** contain the checklist itself.
5. **Draft the progress-checklist comment** *(new-issue mode)* — the PR-granularity checklist with `TASK-` IDs, in the format defined in Progress Checklist. (In existing-issue mode, the checklist may live inside the plan comment as acceptance criteria until the plan is accepted.)
6. **Draft the self-contained plan comment(s)** — sections: Provenance header (pipeline, stats, review invitation; and in existing-issue mode a supersession note linking the previous plan comment with a summary of what changed); Background/domain knowledge; Current → changed (data flow & impact); Implementation plan per `TASK-` ID with `file:line` references; Logic/spec + expected-value table; Completion criteria. Keep comment IDs in sync with the checklist.
7. **Apply language detection** — render everything in the user's session language.
8. **GitHub authorization gate** — present the drafted description, progress-checklist comment, and plan comment(s) to the user (the diagram appears as its mermaid source at this point) and obtain explicit approval BEFORE any GitHub write. The approval covers the posting in step 9 and the diagram attach in step 10.
9. **Post** — `gh issue create` (the description carries the diagram in its mermaid fallback `<details>` form for now), then `gh issue comment` for the progress-checklist comment and the plan comment(s).
10. **Attach & embed the diagram image** — spawn a subagent invoking `mermaid-to-svg` with the mermaid source to draw the SVG (→ file path), then embed it into the container that was just posted, per the Big-picture guideline: **new-issue mode** — `github-attachment-upload` (`target_type=issue`, `mode=edit`) against the created issue, then `gh issue edit --body` to place `![big picture](<url>)` above the collapsed mermaid; **existing-issue mode** — mint the URL via `github-attachment-upload` (`mode=comment`), embed it into the plan comment, and delete the helper comment. On `FAILED: <reason>` from either subagent: keep the visible mermaid code block already posted and report the fallback and reason to the user.
11. **Report** — return the created issue and comment URLs (and whether the diagram fell back to inline mermaid).

### Progress-update mode

See **Progress-Update Mode** above. In short: identify issue#/TASK from session context → **issue-primacy gate** → apply the single change (URL sub-item and/or checkbox) to the progress-checklist comment → explicit approval → write → report.

## Description Template (new-issue mode)

```markdown
## 概要 / Overview
{what and why, a few sentences}

## 全体像 / Big picture
{an SVG image drawn by mermaid-to-svg and uploaded via github-attachment-upload; the mermaid source stays below in a collapsed details block as the canonical structure record — NOT an HTML comment (`-->` arrows would terminate it). On FAILED fallback: replace the image line with a visible mermaid code block instead.}

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

## 進捗 / Progress
> 進捗チェックリストは進捗チェックリストコメント参照 / See the progress-checklist comment for the PR-granularity checklist.
> 詳細はコメント参照 / See the comment for the self-contained plan.
```

## Progress-Checklist Comment Template

```markdown
## 進捗チェックリスト / Progress checklist
> 各 TASK の詳細は実装計画コメントの同じ TASK-ID の見出しを参照 / See the heading with the same TASK- ID in the plan comment.

### Stage 1: {phase name}

- [ ] **TASK-A1** {repo}: {PR-level work}
    - {PR URL, appended when the PR is created}
- [ ] **TASK-A2** {repo}: {PR-level work}
    - ※after TASK-A1

### Stage 2: {phase name}

- [ ] **TASK-B1** {repo}: {PR-level work}
    - ※after TASK-A2
```

`### Stage N:` headings group a multi-phase plan (one per `TASK-` phase letter); omit them for a single-phase plan. The pointer line is plain text, not a link — GitHub gives comment headings no anchor ids — and applies to new-issue mode only, where the checklist has its own comment.

## Plan Comment Template

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
- Constraints / caveats / rationale: {constraints, rationale and caveats that do not fit on the checklist line — omit if there are none}
- Steps: ...

## 4. Logic / spec & expected values
{rules + expected-value table}

## 5. Completion criteria
- ...
```

## GitHub Authorization

Follow the `github-resource-access` skill: gh reads are free; **writes (issue create, comment create, comment edit for progress-update, and the diagram attach/embed performed by `github-attachment-upload` plus its follow-up body edit or helper-comment deletion) require explicit user instruction**. Always show drafts / the intended change and wait for an explicit go-ahead before posting, attaching, or editing. Multi-repo PR creation pattern: `gh -R <org>/<repo> ...`.

## Final Checklist

- [ ] Plan was finalized before invoking (not fabricated) — for create modes.
- [ ] Issue topology (new tracking issue / comment on existing issue / progress-update) was decided deliberately and confirmed with the user.
- [ ] **Issue-primacy gate** ran before any write to an existing issue: issue and target comment re-fetched, last-updated time checked, and on divergence a diff was shown and reconciliation confirmed — never a silent overwrite of concurrent human edits.
- [ ] New-issue mode: tracking issue has overview + scope in the description, a **dedicated progress-checklist comment** with `TASK-` IDs, and a self-contained plan comment.
- [ ] Progress checklist is in a **dedicated comment**, not the description; the description is left non-destructive.
- [ ] Checklist follows the **Progress Checklist** format: one line per item, 70 characters or fewer, with PR URLs and dependency notes as indented sub-items; overflow relocated to the plan comment.
- [ ] Progress-update mode applied the correct transition: PR created → URL sub-item appended; PR merged → box checked (`[x]`). issue#/TASK came from session context, not auto-discovery.
- [ ] Proactive progress-update offer made when a PR was created in a tracking-issue session (create-pr left unmodified).
- [ ] Big-picture diagram included (description for new-issue mode, top of the plan comment for existing-issue mode) and kept high-level.
- [ ] Diagram posted as an SVG image (container posted first, then `mermaid-to-svg` → `github-attachment-upload` into that existing issue/comment, after the authorization gate) with the mermaid source in a collapsed `<details>` block right after the image (never an HTML comment) — or, on FAILED, a visible mermaid code block with the fallback reported.
- [ ] Existing-issue mode: superseded plan comment linked with a summary of changes; issue description left untouched unless separately approved.
- [ ] Provenance header present when the plan came from a structured pipeline; review invitation included when posted for review.
- [ ] Detailed plan comment is self-contained (background, file:line refs, logic, expected values, completion criteria).
- [ ] Every checklist ID appears in the plan comment and vice versa.
- [ ] Issue/comment written in the user's detected language.
- [ ] No GitHub write before explicit approval; issue/comment URLs reported.
