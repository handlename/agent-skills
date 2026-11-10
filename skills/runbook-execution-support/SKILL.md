---
name: runbook-execution-support
description: Support a human operator through a runbook (a checklist of operational steps written in a GitHub issue comment) — present each step, pre-check the target state before the human acts, verify the result after, post progress to a Slack thread, and tick the runbook/progress checklists. Use this whenever the user says they will carry out a cutover, migration, release switch, or any written procedure themselves and wants the agent to "support", "assist", "guide", or "track progress", e.g. "手順書に従って切り替え作業を行う。エージェントには作業のサポートを行ってほしい", "runbook の実行をサポートして", "手順書どおりに進めるので確認役をお願い", "walk me through this runbook and post progress to Slack". Use it even when the user only gives a runbook URL and a Slack thread URL.
---

# Runbook Execution Support

## Overview

The human does the real work: merging PRs, changing repository settings, running commands that change state, pushing.
The agent does everything around it:

- presents the next step and exactly what the human should do,
- pre-checks the target state before the human acts,
- verifies the result after the human acts, with evidence,
- posts progress to a Slack thread so other members can see what is happening and what is being waited on,
- ticks the runbook and the progress checklist,
- notices where the runbook and reality disagree, and hands the decision back to the human.

The split exists because the human owns every irreversible action.
The agent's value is that nothing is done blind and nothing is left unexplained to the rest of the team.

This skill is repository-agnostic.
Repository-specific checks (e.g. which commit status gates a release) come from the runbook itself, from documents it links to, or from reading the repository at the time.

## Instructions

### 1. Kickoff

Read the runbook comment and the Slack thread before saying anything.
Also read what the runbook links to (task details, progress checklist) and the current state of every PR, branch and workflow it names.

Then confirm these four points with the user in one message, and do not start until they are settled:

1. **Comments the agent may edit.** Usually the runbook and the progress checklist. Editing an issue comment is an external write, so get explicit permission per comment.
2. **Scope and end condition.** Which sections are done today, and what "finished" means (e.g. "through the deploy, to the end of the runbook").
3. **Role split.** Restate who does what. The default is: the human performs state-changing actions; the agent reads, verifies, posts to Slack and edits the permitted comments.
4. **Open questions found by comparing the runbook with the current state.** For example: an item is ticked but the evidence is from yesterday; a PR that should be up to date is behind its base; a PR the runbook expects is missing.

Agent-attribution markers (e.g. "🤖 by Claude Code") follow the user's CLAUDE.md. Do not ask about them at kickoff; drop them only where the user says they are unnecessary.

Post a 🔧 message to the Slack thread when work starts.

### 2. The step loop

Repeat for each runbook item, in order unless the user reorders.

1. **Cross-check the step.** Before presenting it, compare its wording with the linked task details and the implementation it depends on (workflow files, scripts, settings). If they disagree, report it as 🔍️ before the human acts. Examples of disagreements worth catching: the runbook says "PR description" but the workflow writes a commit-status description; the task details impose a rule (such as "avoid minute 0 in cron") that the change does not follow.
2. **Pre-check.** Verify the preconditions of the human's action with read-only commands: CI results, `mergeable` / merge state, how far the branch is behind its base, review decision, required statuses. Report blockers before the human acts, not after.
3. **Present the human's action.** Label it `（ユーザー作業）` and put any command in a code block so it can be copied.
4. **Wait.** Post a ⏳ message saying exactly what is being waited on. When the wait is for CI or a workflow run, start a background watcher (`gh run watch <id>` / `gh pr checks <n> --watch` with `run_in_background`) instead of asking the user to report back, and resume when it finishes.
5. **Verify.** Check every confirmation item of the step yourself. Report results with evidence (SHA, run ID, API output), not with "looks fine".
6. **Record.** Tick the runbook item (see section 4), then post a ✅ message.

When the human reports an action ("merged", "re-ran"), confirm it from GitHub before building on it. A branch that was said to be updated may not be, and a pushed branch may have been rebased onto a different base.

### 3. Slack progress posts

Post as replies in the given thread. Post when a step starts, when a wait begins, when a step completes, and when something unexpected is found — not only on completion. Other members read the thread to learn what is happening now and what is blocking.

Format every post like this:

```markdown
**⏳ 待ち | 3. freeze 区間 / TASK-A2**
- [#27258 ci: release PR を release/* ブランチ経由に切り替える](https://github.com/OWNER/REPO/pull/27258) をマージ
- `git-pr-release` の run 待ち
- 次: release ブランチと release PR の作成を確認
```

- **Heading:** the first line is bold, `**{emoji} {state} | {position in the runbook}**`. The Slack MCP tool takes standard Markdown, so bold is `**...**`; `*...*` renders as italic. All posts come from the user's account and Slack collapses consecutive avatars, so the heading is what separates one post from the next.
- **States:** exactly four — 🔧 作業中, ⏳ 待ち, ✅ 完了, 🔍️ 確認中 (something unexpected is being investigated). Task-specific states such as a freeze are named by the runbook, not invented.
- **Body:** bullets in terse, sentence-final form ("〜をマージ", "〜の run 待ち", "〜を確認"), not polite full sentences. The last bullet is `次: …を確認` (or the next action).
- **Waiting posts** name the concrete thing waited on: which PR's CI, which workflow run (with link), or which human action.
- **Links:** always write issues and PRs as `[#{number} {title}](URL)`. A bare `#27258` forces readers to click to learn what it is. Fetch the title with `gh pr view` / `gh issue view` when you do not have it.

### 4. Updating checklists

Issue comments are shared documents; a bad write is visible to everyone and can silently erase someone else's edit.

- Re-fetch the comment body immediately before each edit. Never write from a copy fetched earlier.
- Replace an exact, unique substring of the target line. If the substring is not found or matches more than once, do not PATCH — stop and look at the current body. Chain the steps so a failed replacement cannot fall through to the PATCH:

```bash
gh api repos/OWNER/REPO/issues/comments/ID --jq .body > body.md \
  && python3 - body.md <<'EOF' \
  && gh api -X PATCH repos/OWNER/REPO/issues/comments/ID -F body=@body.md --jq .updated_at
import sys
p = sys.argv[1]; s = open(p).read()
old = "- [ ] TASK-A2: #27258 をマージする。"
if s.count(old) != 1:
    sys.exit("not found or not unique: " + old)
open(p, "w").write(s.replace(old, old.replace("[ ]", "[x]", 1)))
EOF
```

- Edit only checkboxes and short end-of-line notes (e.g. "（今回は stream の変更を含まないため実行不要）", "（#27678）"). If the wording itself is wrong, propose the correction and wait for the user's approval.
- Tick items completed out of order as soon as they are done.
- Do not tick an item whose condition is only partly met (e.g. a health check that passed on a feature branch but not yet on the default branch). Say what remains instead.

### 5. Deviations and failures

- **CI failures.** Read the failed job log, identify the failing test and its error, and judge whether it relates to the change. For an unrelated flaky failure, propose a re-run and say why it is unrelated; if it repeats, look for a pattern (e.g. an external site returning 429 to CI runners) before proposing the next move.
- **Runbook vs. reality.** When a setting or behaviour contradicts the runbook's assumptions (e.g. a branch-protection option that would make every future release PR unmergeable), post 🔍️, explain the impact, and give options with a recommendation.
- **Work outside the runbook.** Present the cause and options. If the user picks one, carry it out in the same session: code change, local commit, PR creation, issue filing. The human still does the push.
- Correct yourself explicitly when an earlier statement turns out wrong (e.g. "the diff will refresh on the next push" and it did not).

### 6. Wrap-up

When the user declares the session finished:

- Post a ✅ summary to the Slack thread: what was completed, what remains (with the condition that unblocks it), and follow-up issues.
- List the items worth turning into issues (deviations found, flaky tests, missing checks) and file the ones the user picks.
- Offer to record durable lessons (e.g. an API that needs admin rights, a setting that breaks the flow) in memory.

## Verification

Before reporting a step as complete, confirm:

- [ ] Every confirmation item of the step was checked by the agent, with evidence in the report.
- [ ] The runbook (and the progress checklist, if the step maps to a task) was re-fetched and updated with an exact-match replacement.
- [ ] A Slack post with the bold `**{emoji} {state} | {position}**` heading was sent, and every issue/PR in it is a `[#{number} {title}](URL)` link.
- [ ] If the next step involves waiting, a ⏳ post names what is waited on and a background watcher is running.
