---
name: plan-to-issue
description: Use when an implementation plan or design has been finalized in the session and the user wants to record it on GitHub, OR when progress on an already-tracked plan needs to be reflected back to its issue. Three modes — (1) create a tracking issue (overview + a dedicated progress-checklist comment with TASK- IDs) plus a self-contained detailed plan posted as an issue artifact (`gh issue artifact`, gh 2.103.0+); (2) when a related issue already exists, post the plan as a self-contained plan artifact on that existing issue, superseding the previous plan; (3) progress-update mode — update the progress-checklist comment (append a PR URL under its TASK, or check the box when merged). Any write to an existing issue first runs an issue-primacy gate (re-fetch the issue, check its last-updated time, show a diff and ask before merging). Triggers include "issue化して", "計画をissueにして", "turn this plan into an issue", "create a tracking issue for this", "既存のissueに計画を投稿して", "計画を issue #N に投稿して", "進捗を更新して", "update the progress", "PRのURLを進捗に反映して".
---

# Plan to Issue

## Overview

Turn an **already-finalized** implementation plan/design into a GitHub issue record, and keep that record's progress in sync, in one of three modes:

- **New-issue mode (default)**: a **tracking issue** whose description holds an overview and a **big-picture diagram** (posted as an attached SVG image, with its mermaid source preserved in a collapsed `<details>` block), plus a **dedicated progress-checklist comment** carrying a **PR-granularity checklist** with stable `TASK-` IDs, plus one or more **self-contained plan artifacts** (issue artifacts of type `plan`, see Plan Artifact) carrying the full plan.
- **Existing-issue mode**: when the plan originates from, revises, or answers an issue that already exists (e.g. a re-design superseding a plan posted earlier on that issue), post the plan as a **self-contained plan artifact on that existing issue** instead of creating a new one.
- **Progress-update mode**: when a PR has been created for a task, or a PR has merged, reflect that back into the tracking issue's **progress-checklist comment** — append the PR URL as a sub-item under its `TASK-` line, or check the box when merged. This mode is normally run **in the same session that created the PR**.

In new-issue and existing-issue modes the plan artifact is written so that a person or agent with **zero prior context can start implementing from the artifact alone**.

Any write to an **existing** issue (progress-update, or an existing-issue plan revision) first passes the **issue-primacy gate** (see Core Principles): the issue on GitHub is authoritative, may have been edited by others, and must be re-fetched and reconciled before writing.

This skill does NOT gather requirements or design the solution — that is the job of an interview/investigation upstream. It only structures, posts, and keeps in sync the issue content.

## When to Use

- A plan/design is settled in the current session and the user asks to record it as an issue.
- A (re)designed plan supersedes or elaborates a plan already posted on an existing issue (as a plan artifact, or as a plan comment from before this skill used artifacts) → use existing-issue mode and post there.
- A PR has just been created (or merged) for a task on a tracked plan and its progress should be reflected → use progress-update mode. When a PR is created in a session that holds a tracking-issue context, **proactively offer to update the progress** (see Progress-Update Mode).
- Triggers: "issue化して", "計画を issue にして", "create a tracking issue", "turn this plan into an issue", "既存の issue に計画を投稿して", "issue #N に投稿して", "進捗を更新して", "update the progress", "PR の URL を進捗に反映して".

## Do Not Use When

- The plan is not yet settled → do not invent it; ask the user where the plan is or send them to interview/investigation first.
- The user wants the work implemented (use multirepo/autopilot/etc.), not recorded.

## Core Principles

- **The issue on GitHub is the source of truth (issue-primacy)**: an existing issue may have been read by others and may have been edited by the user or others since this session last saw it. Before **any** write to an existing issue (progress-update, or an existing-issue plan revision), run the **issue-primacy gate**:
  1. **Re-fetch** the current issue and the relevant comment or plan artifact via `gh` (never write from a stale in-session copy).
  2. **Check the last-updated time** (`gh issue view` `updatedAt`, and the target comment's `updatedAt` via the API, or the plan artifact's `updatedAt` via `gh issue artifact view <issue> <number> --json body,updatedAt`) against what this session assumes.
  3. If the current content **diverges** from what the session expects (i.e. it changed since the session's knowledge), **present the diff** — session's assumption vs. current issue content — and **ask the user how to reconcile**, section by section. **Never silently overwrite concurrent human edits.**
  4. Merging any content requires **explicit user confirmation**. Apply only the session-originated change (e.g. one PR URL, one checkbox) on top of the current content; leave everything else as-is.
- **The issue description is non-destructive**: never rewrite the issue's description by default, in any mode. The progress checklist lives in a **comment** and the plan in a **plan artifact**, not in the description. Update the description only with separate, explicit approval.
- **Self-contained plan test**: the plan artifact must let a context-less agent implement each task from the artifact alone. Always include: domain/background, current→changed data flow & impact, per-task implementation steps with concrete `file:line` references, logic/spec with an expected-value table where applicable, any constraints, caveats or rationale that do not fit on the checklist line, and completion criteria.
- **ID correspondence is mandatory**: every checklist item ID in the progress-checklist comment MUST appear in the plan artifact, and vice versa, so "implement TASK-A2" is unambiguous and so a checklist item's detail can be found by matching its ID — the only route, since items are not linked.
- **Big-picture diagram is mandatory**: the issue MUST include a diagram that lets a reader grasp the whole at a glance — the problem/system the issue targets (e.g. affected components and their relationships, current vs. changed data flow, or the state transition being fixed). Author it as **mermaid** — the mermaid source is the **canonical record of the diagram's structure**. Choose the diagram type that best conveys the structure (`flowchart`, `sequenceDiagram`, `erDiagram`, `stateDiagram-v2`, etc.). **Author the mermaid via a subagent invoking the `draw-mermaid` skill** (input: the diagram intent; output: a syntax-validated mermaid source, or `FAILED: <reason>`) so it is guaranteed to parse before it is shown or handed to `mermaid-to-svg`. It is for human comprehension, not implementation detail; keep it high-level. In new-issue mode it lives in the description; in existing-issue mode place it near the top of the plan artifact.
- **Diagram is posted as an SVG image; mermaid is preserved in a collapsed `<details>` block**: the diagram is displayed as a **free-layout SVG image**, not a rendered mermaid block. Draw it via a **subagent** invoking `mermaid-to-svg` (input: the mermaid source; output: an SVG file path, or `FAILED: <reason>`). How it is uploaded depends on the container:
  - **New-issue mode** (diagram in the description): `github-attachment-upload` attaches to an **existing** issue/PR, so the image is embedded **after** the issue is posted. Via a subagent invoking `github-attachment-upload` (input: the SVG path + the target `owner/repo`, issue number, `target_type`, and `mode`; output: two lines `url:` / `location:`, or `FAILED: <reason>`): create the issue first with the mermaid in its fallback `<details>` form, then call `github-attachment-upload` with `target_type=issue`, `mode=edit`, and finally set the description via `gh issue edit --body` so `![big picture](<url>)` sits in the Big picture section, immediately followed by the mermaid source inside `<details><summary>…</summary>` with a ` ```mermaid ` fence.
  - **Existing-issue mode** (diagram in the plan artifact; the description stays untouched): no helper comment and no `github-attachment-upload`. Copy the SVG next to the plan body file as `big-picture.svg`, reference it in the body as `![big picture](./big-picture.svg)` above the collapsed mermaid `<details>`, and pass `--attach ./big-picture.svg` to the same `gh issue artifact create` / `edit` call that posts the plan (see Plan Artifact). gh uploads the file and rewrites that reference to the uploaded URL. gh matches the reference (resolved against the `--body-file`'s directory) to the `--attach` path (resolved against the working directory) by absolute path, so run the command from the directory that holds both files.
  The collapsed mermaid is one click away as the canonical structure record, and it renders as a diagram when expanded (a bonus fallback for contexts that cannot show the image). **Never wrap the mermaid in an HTML comment** (`<!-- -->`): mermaid arrows (`-->`) contain `--`, which terminates the comment early and leaks the rest of the source as visible text.
  - **Update flow**: to change the diagram, edit the mermaid in the details block, re-run `mermaid-to-svg` to redraw the SVG, then upload it again — new-issue mode: re-invoke `github-attachment-upload` against the issue and replace the image URL; existing-issue mode: put `![big picture](./big-picture.svg)` back in the body and run `gh issue artifact edit <issue> <number> --body-file <plan.md> --attach ./big-picture.svg`. The SVG layout is throwaway and is redrawn from scratch each time; only the mermaid persists.
  - **Fallback**: if the image cannot be produced or uploaded (`mermaid-to-svg` returns `FAILED`, `github-attachment-upload` returns `FAILED`, or the `--attach` upload fails — when no attached file uploads, `gh issue artifact create`/`edit` exits non-zero and saves nothing, so run it again without `--attach`), post a visible ` ```mermaid ` code block instead — the pre-SVG behavior — and report the fallback and its reason to the user. Never let the image block the posting.
- **Provenance header**: when the plan was produced by a structured pipeline (e.g. deep-interview → ralplan consensus), open the plan artifact with a short quote block stating the pipeline and its key stats (interview rounds, final ambiguity %, reviewer verdict and iteration). When the plan is posted for review rather than as an accepted plan, explicitly invite feedback (e.g. "レビュー・コメント歓迎です").
- **Existing-issue mode is non-destructive**: never rewrite the existing issue's description by default. The plan lives in the plan artifact. Supersede an earlier plan as described in Plan Artifact and summarize what changed (decision pivots, invalidated ADRs).
- **Never write to GitHub without explicit approval** (see GitHub Authorization).

## Plan Artifact

The detailed plan is an **issue artifact** of type `plan` (a Markdown document attached to the issue, listed in its Artifacts section, with its own URL and an edit history), never an issue comment.

- **Requirement — gh 2.103.0 or later**: `gh issue artifact` exists from GitHub CLI 2.103.0. Before drafting in new-issue or existing-issue mode, run `gh --version`. If the version is older than 2.103.0, tell the user that the installed gh (state its version) lacks `gh issue artifact`, which this skill requires to post the plan, and that gh 2.103.0 or later is needed — then **stop**. Do not post the plan as a comment or by any other route. Progress-update mode does not touch artifacts and needs no check.
- **Name**: a short name in the user's language, e.g. `実装計画` or `Implementation plan`. Artifact names cannot contain some characters such as parentheses; leave them out.
- **Create**: write the approved body to a file, then:

  ```bash
  gh issue artifact create <issue> --type plan --name '<name>' --body-file <plan.md>
  gh issue artifact view <issue> <number> --json url --jq .url
  ```

  `create` prints the new artifact's number but not its URL: in a terminal `Created artifact <number> (<name>) on <owner>/<repo>#<issue>`, and when piped the tab-separated line `created<TAB><number><TAB><name><TAB><source><TAB>`. Pass that number to `view` to get the URL (`https://github.com/<owner>/<repo>/issues/<issue>/artifacts/<number>`); the URL is what the progress-checklist pointer and the report use. In existing-issue mode with a diagram image, add `--attach ./big-picture.svg` (see the Big-picture guideline).
- **Supersede (existing-issue mode)**: if the issue already has a plan artifact for this work, revise it in place — `gh issue artifact edit <issue> <number> --body-file <plan.md>` (plus `--attach ./big-picture.svg` for the diagram image, and `--name` if the name changes). The edit becomes a new version and the previous plan stays in the artifact's edit history (`gh issue artifact view <issue> <number>`), so the supersession note names the previous version instead of linking it. If the earlier plan is a legacy plan comment, create a new plan artifact and link that comment in the supersession note.
- **Read**: other modes and skills find the plan with `gh issue artifact list <issue> --type plan` and read it with `gh issue artifact view <issue> <number> --json body --jq .body`.

## Progress Checklist (format & location)

- **Location**: the progress checklist lives in a **dedicated progress-checklist comment** on the tracking issue — NOT in the description. The description references it (e.g. "進捗は進捗チェックリストコメント参照 / See the progress-checklist comment"). This keeps the description non-destructive and the living progress tracker isolated and easy to edit.
- **One item, one line**: each checklist item MUST fit in **70 characters or fewer** (count characters plainly — do not distinguish full-width from half-width). The line carries only a short noun-phrase summary of the deliverable. Constraints, rationale, file names, caveats and type/status tags such as `[設定]` / `[freeze]` / `[判定]` do NOT belong on it — they go under the matching `### TASK-<ID>:` heading in the plan artifact, which the reader finds by the `TASK-` ID. If the summary will not fit, shorten the deliverable phrase — never the `TASK-` ID or the repo — and move the detail to the plan artifact. This section is the single statement of the rule; everywhere else points here.

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

- **A pointer to the detail, not a link per item**: do NOT link individual items. The progress-checklist comment opens with one pointer line linking the plan artifact's URL (see the template). GitHub's current issue UI gives headings **no anchor ids**, so `#user-content-<slug>` does not resolve; readers navigate by matching the `TASK-` ID.

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
- The **issue, comment, and plan artifact content** must be written in the **user's language**, detected from the session conversation. Do not hardcode a language.

## Process

### Creating (new-issue / existing-issue mode)

1. **Confirm preconditions** — verify a finalized plan exists in the session; if not, stop and ask the user for the plan, and do not fabricate one. Run the gh version check in Plan Artifact; if gh is older than 2.103.0, report it and stop.
2. **Determine scope & decompose into PRs** — single-repo or cross-repo? Break work into PR-sized units and capture dependencies (e.g. DB migration → application code).
3. **Decide issue topology** — choose one and confirm with the user:
   - **New tracking issue** (default when no related issue exists): ONE issue with a dedicated progress-checklist comment spanning all PRs; propose the target repo (where the originating / user-visible change lives).
   - **Plan on an existing issue** (default when the plan originates from, revises, or answers an existing issue — check the session for an issue the plan refers to): post the plan as a plan artifact there. Identify the earlier plan (a plan artifact via `gh issue artifact list <issue> --type plan --json number,name,url,updatedAt`, or a legacy plan comment) so it can be superseded per Plan Artifact. **Run the issue-primacy gate first** (this is a write to an existing issue).
   - **Cross-repo alternative**: per-repo issues, offered alongside the single tracking issue.
4. **Draft the description** *(new-issue mode only)* — Overview (what & why) + the **big-picture diagram authored as mermaid via a subagent invoking `draw-mermaid`** (the draft shows the syntax-validated mermaid source; it becomes an SVG image in step 8) + Scope (in/out) + a pointer to the progress-checklist comment. The description does **not** contain the checklist itself.
5. **Draft the progress-checklist comment** *(new-issue mode)* — the PR-granularity checklist with `TASK-` IDs, in the format defined in Progress Checklist. (In existing-issue mode, the checklist may live inside the plan artifact as acceptance criteria until the plan is accepted.)
6. **Draft the self-contained plan artifact(s)** — a name (see Plan Artifact) and a body with sections: Provenance header (pipeline, stats, review invitation; and in existing-issue mode a supersession note per Plan Artifact with a summary of what changed); Background/domain knowledge; Current → changed (data flow & impact); Implementation plan per `TASK-` ID with `file:line` references; Logic/spec + expected-value table; Completion criteria. Keep the artifact's IDs in sync with the checklist.
7. **Apply language detection** — render everything in the user's session language.
8. **GitHub authorization gate** — present the drafted description, progress-checklist comment, and plan artifact(s) to the user (the diagram appears as its mermaid source at this point) and obtain explicit approval BEFORE any GitHub write. The approval covers the posting in step 9 and the diagram attach in step 10.
9. **Post** — new-issue mode: `gh issue create` (the description carries the diagram in its mermaid fallback `<details>` form for now), then create the plan artifact(s) (see Plan Artifact), then `gh issue comment` for the progress-checklist comment with its pointer line linking the plan artifact's URL. Existing-issue mode: spawn a subagent invoking `mermaid-to-svg` to draw the SVG, then create or revise the plan artifact per Plan Artifact with `--attach ./big-picture.svg`, so the image is embedded in the same write; on `FAILED` or an upload failure, post it with a visible mermaid code block and report the fallback and reason to the user.
10. **Attach & embed the diagram image** *(new-issue mode; existing-issue mode embedded it in step 9)* — spawn a subagent invoking `mermaid-to-svg` with the mermaid source to draw the SVG (→ file path), then `github-attachment-upload` (`target_type=issue`, `mode=edit`) against the created issue, then `gh issue edit --body` to place `![big picture](<url>)` above the collapsed mermaid. On `FAILED: <reason>` from either subagent: keep the visible mermaid code block already posted and report the fallback and reason to the user.
11. **Report** — return the created issue, comment, and plan artifact URLs (and whether the diagram fell back to inline mermaid).

### Progress-update mode

See **Progress-Update Mode** above. In short: identify issue#/TASK from session context → **issue-primacy gate** → apply the single change (URL sub-item and/or checkbox) to the progress-checklist comment → explicit approval → write → report.

## Description Template (new-issue mode)

```markdown
## 概要 / Overview
{what and why, a few sentences}

## 全体像 / Big picture
{an SVG image drawn by mermaid-to-svg and uploaded via github-attachment-upload (in a plan artifact: via `--attach`, referenced as `./big-picture.svg` before upload); the mermaid source stays below in a collapsed details block as the canonical structure record — NOT an HTML comment (`-->` arrows would terminate it). On FAILED fallback: replace the image line with a visible mermaid code block instead.}

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
> 詳細は Artifacts の実装計画参照 / See the plan artifact in the issue's Artifacts section for the self-contained plan.
```

## Progress-Checklist Comment Template

```markdown
## 進捗チェックリスト / Progress checklist
> 各 TASK の詳細は [実装計画]({plan artifact URL}) の同じ TASK-ID の見出しを参照 / See the heading with the same TASK- ID in the [plan artifact]({plan artifact URL}).

### Stage 1: {phase name}

- [ ] **TASK-A1** {repo}: {PR-level work}
    - {PR URL, appended when the PR is created}
- [ ] **TASK-A2** {repo}: {PR-level work}
    - ※after TASK-A1

### Stage 2: {phase name}

- [ ] **TASK-B1** {repo}: {PR-level work}
    - ※after TASK-A2
```

`### Stage N:` headings group a multi-phase plan (one per `TASK-` phase letter); omit them for a single-phase plan. The pointer line links the plan artifact as a whole, not a heading — GitHub gives headings no anchor ids — and applies to new-issue mode only, where the checklist has its own comment.

## Plan Artifact Template

```markdown
> {provenance, e.g. "Deep Interview (N ラウンド、ambiguity X%) → Ralplan (Planner / Architect / Critic、Iteration M で APPROVE) のパイプラインで作成した実装計画です。"}
> {existing-issue mode, if superseding: "前回計画 (<version N of this artifact, or link to the legacy plan comment>) を置き換える改訂版です。主な変更: <e.g. 決定事項によりクラウドを GCP → AWS に変更、旧 ADR は失効>"}
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

Goal: a person/agent without prior context can implement from this artifact alone.

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

Follow the `github-resource-access` skill: gh reads are free; **writes (issue create, comment create, comment edit for progress-update, plan artifact create/edit including its `--attach` upload, and the diagram attach/embed performed by `github-attachment-upload` plus its follow-up description edit) require explicit user instruction**. Always show drafts / the intended change and wait for an explicit go-ahead before posting, attaching, or editing. Multi-repo PR creation pattern: `gh -R <org>/<repo> ...`.

## Final Checklist

- [ ] Plan was finalized before invoking (not fabricated) — for create modes.
- [ ] Issue topology (new tracking issue / plan on existing issue / progress-update) was decided deliberately and confirmed with the user.
- [ ] **Issue-primacy gate** ran before any write to an existing issue: issue and target comment or plan artifact re-fetched, last-updated time checked, and on divergence a diff was shown and reconciliation confirmed — never a silent overwrite of concurrent human edits.
- [ ] New-issue mode: tracking issue has overview + scope in the description, a **dedicated progress-checklist comment** with `TASK-` IDs, and a self-contained plan artifact.
- [ ] Progress checklist is in a **dedicated comment**, not the description; the description is left non-destructive.
- [ ] Checklist follows the **Progress Checklist** format: one line per item, 70 characters or fewer, with PR URLs and dependency notes as indented sub-items; overflow relocated to the plan artifact.
- [ ] Progress-update mode applied the correct transition: PR created → URL sub-item appended; PR merged → box checked (`[x]`). issue#/TASK came from session context, not auto-discovery.
- [ ] Proactive progress-update offer made when a PR was created in a tracking-issue session (create-pr left unmodified).
- [ ] Big-picture diagram included (description for new-issue mode, top of the plan artifact for existing-issue mode) and kept high-level.
- [ ] Diagram posted as an SVG image after the authorization gate (new-issue mode: issue posted first, then `mermaid-to-svg` → `github-attachment-upload` into it; existing-issue mode: `mermaid-to-svg`, then the plan artifact posted with `--attach ./big-picture.svg`) with the mermaid source in a collapsed `<details>` block right after the image (never an HTML comment) — or, on FAILED, a visible mermaid code block with the fallback reported.
- [ ] Existing-issue mode: earlier plan superseded per Plan Artifact (plan artifact revised in place, or legacy plan comment linked) with a summary of changes; issue description left untouched unless separately approved.
- [ ] Provenance header present when the plan came from a structured pipeline; review invitation included when posted for review.
- [ ] Plan was posted as a `plan` issue artifact (never as a comment) after the gh version check passed.
- [ ] Plan artifact is self-contained (background, file:line refs, logic, expected values, completion criteria).
- [ ] Every checklist ID appears in the plan artifact and vice versa.
- [ ] Issue, comments, and plan artifact written in the user's detected language.
- [ ] No GitHub write before explicit approval; issue, comment, and plan artifact URLs reported.
