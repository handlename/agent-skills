---
name: commit-it
description: Commit changes following the Conventional Commits specification, verifying test passage first. Supports both git and Jujutsu (jj) repositories.
compatibility: Requires git or Jujutsu (jj).
metadata:
  argument-hint: "[commit-message] (optional)"
---

# Commit Changes Skill

Commit changes using Conventional Commits after verifying that all related tests pass. Works in both git and Jujutsu (jj) repositories.

## Overview

Detect whether the repository is managed by Jujutsu (jj) or git, then commit using the appropriate workflow while applying the same Conventional Commits message policy in both cases.

## Version Control System Detection

Detect the VCS before committing:

- Run `jj st`. If it succeeds, treat the repository as **jj**. This includes colocated repositories where both `.jj/` and `.git/` exist — prefer jj there.
- If `jj st` fails (e.g. "There is no jj repo in ..."), treat the repository as **git**.

Whenever jj is available, prefer it.

## Instructions

Execute the following tasks. If you determine that multiple tasks can proceed in parallel, do so.

- Detect the VCS as described above.
- Check the current change status and draft a suitable commit message.
- If a commit message subject is specified as an argument, use that message.
- If no argument is specified, analyze the changes to generate a suitable message.
- Apply the appropriate prefix following the Conventional Commits specification.
- Verify that related tests pass before committing if they exist.
- Commit using the VCS-specific workflow below.

### git repositories

- Stage the relevant changes, verify that related tests pass, then create the commit with `git commit`.
- Split unrelated changes into separate commits using staging (`git add -p`, etc.).

### jj (Jujutsu) repositories

- Follow the `jujutsu` skill for all jj mechanics (describing, splitting, and refining commits). This skill owns only the Conventional Commits message policy described in the Guidelines below.
- There is no staging area and no `jj commit`: the working copy is already a commit. Set the message on the working-copy commit with `jj desc -m "<message>"`, then start the next change with `jj new` when appropriate.
- Verify that related tests pass before finalizing the described commit.
- To split multiple change types, do NOT use interactive `jj split` (it hangs in agent environments). Move unrelated changes out with `jj restore` and describe them as separate commits, per the `jujutsu` skill.
- The Conventional Commits message policy in this skill takes precedence over the default sentence-case message style described in the `jujutsu` skill.

---

## Verification

Continue working until the completion criteria described below are met:
- The change is successfully committed with an appropriate commit message.
- All related tests passed (if tests exist).

---

## Guidelines

Follow these guidelines at all times during task execution:
- Commit messages must follow these Conventional Commits rules:
  - `feat:` A new feature
  - `fix:` A bug fix
  - `docs:` Documentation only changes
  - `style:` Changes that do not affect the meaning of the code (white-space, formatting, etc.)
  - `refactor:` A code change that neither fixes a bug nor adds a feature
  - `perf:` A code change that improves performance
  - `test:` Adding missing tests or correcting existing tests
  - `chore:` Changes to the build process or auxiliary tools and libraries
- Split commits if multiple types of changes are included (git: via staging; jj: via `jj restore`, per the `jujutsu` skill).
- If a commit message is specified in the arguments, prepend the appropriate prefix and use it.
- If the argument is empty or not specified, analyze the changes and generate an appropriate message.
- In the body of the commit message, include a summary of the user's instructions.

---

## Prohibited Actions

- Committing while tests are failing.
- Using a commit message that does not conform to the Conventional Commits rules.
- In jj repositories, using interactive commands that hang in agent environments (`jj split`, `jj squash -i`, `jj resolve`); use the non-interactive alternatives described in the `jujutsu` skill.

---

## Supplementary Notes

- If there is any obstacle to smooth progress (e.g., ambiguous instructions, missing essential information), report it to the user and request additional information.
- Keep the commit message subject within 50 characters.
- If a commit message body is needed, wrap lines at 72 characters.
- Start the summary of user instructions with a line beginning with "User request:".
