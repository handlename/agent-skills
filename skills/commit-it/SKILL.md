---
name: commit-it
description: Commit changes following the Conventional Commits specification, verifying test passage first.
compatibility: Requires git.
metadata:
  argument-hint: "[commit-message] (optional)"
---

# Commit Changes Skill

Commit Git changes using Conventional Commits after verifying that all related tests pass.

## Overview

Commit Git changes using Conventional Commits after verifying that all related tests pass.

## Instructions

Execute the following tasks. If you determine that multiple tasks can proceed in parallel, do so.

- Check the current change status and draft a suitable commit message.
- If a commit message subject is specified as an argument, use that message.
- If no argument is specified, analyze the changes to generate a suitable message.
- Apply the appropriate prefix following the Conventional Commits specification.
- Verify that related tests pass before committing if they exist.

---

## Verification

Continue working until the completion criteria described below are met:
- The commit is successfully completed with an appropriate commit message.
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
- Split commits if multiple types of changes are included.
- If a commit message is specified in the arguments, prepend the appropriate prefix and use it.
- If the argument is empty or not specified, analyze the changes and generate an appropriate message.
- In the body of the commit message, include a summary of the user's instructions.

---

## Prohibited Actions

- Committing while tests are failing.
- Using a commit message that does not conform to the Conventional Commits rules.

---

## Supplementary Notes

- If there is any obstacle to smooth progress (e.g., ambiguous instructions, missing essential information), report it to the user and request additional information.
- Keep the commit message subject within 50 characters.
- If a commit message body is needed, wrap lines at 72 characters.
- Start the summary of user instructions with a line beginning with "User request:".
