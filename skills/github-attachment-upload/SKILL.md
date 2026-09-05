---
name: github-attachment-upload
description: Attach a local file to an EXISTING GitHub issue or PR using the GitHub CLI (`gh --attach`, added in gh 2.99.0), then return the resulting `user-attachments` URL and the embed location. File-type agnostic — works for SVG, PNG, screenshots, or video that GitHub's uploader accepts. It does NOT create a throwaway issue: the target issue/PR is supplied by the caller, and the file is added either as a new comment or into the target body (caller's choice). Designed to be called from other skills (e.g. plan-to-issue, create-pr) through a subagent whose only return value is the result on success or a failure reason on failure. Use when a local file must become a GitHub `user-attachments` URL embedded in a known issue/PR. Triggers include "このファイルをGitHubのissue/PRに添付してURLをちょうだい", "attach this file to the issue with gh and give me the URL", "get me an attachment URL for this image".
---

# GitHub Attachment Upload

## Overview

Take a **local file** and a **target GitHub issue or PR**, attach the file with the GitHub CLI's
`gh --attach` flag (added in **gh 2.99.0**), and return the resulting **`user-attachments` URL** together
with **where it was embedded**. GitHub rewrites a local reference such as `![alt](./diagram.svg)` in the
body/comment text into a `https://github.com/user-attachments/assets/<uuid>` URL as part of the upload.

This skill is **file-type agnostic**: it uploads whatever file it is given (SVG, PNG, screenshot, video, …)
as long as GitHub's uploader accepts it. SVG is accepted and rewritten into a `user-attachments` URL
(verified). It does NOT know or care how the file was produced.

Unlike the previous browser-driven implementation, this skill **does not create a throwaway issue**. The
target issue/PR already exists and is provided by the caller. The attachment is added to that target — as a
new comment (`comment` mode) or by editing the target body (`edit` mode).

## When to Use

- Another skill (e.g. `plan-to-issue`, `create-pr`) has a local file (such as an SVG from `mermaid-to-svg`)
  and an existing issue/PR, and needs the file embedded there plus its `user-attachments` URL.
- The user has a file and an existing issue/PR and wants the file attached and its `user-attachments` URL.

## Do Not Use When

- No target issue/PR exists yet — this skill attaches to an existing one; it does not create issues/PRs.
  The caller must create (or already have) the target first.
- `gh` is older than 2.99.0 (the `--attach` flag is unavailable).
- The destination is something other than GitHub issues/PRs/comments — `user-attachments` is a GitHub feature.
- The file does not exist yet (e.g. an SVG still needs drawing — that is `mermaid-to-svg`).
- The caller has no user approval for the GitHub write yet — see Authorization.

## Contract (input / output)

**Input** (provided by the caller, e.g. in the subagent prompt):

- `file`: absolute path to the local file to attach. Required.
- `repository`: `owner/repo` of the target. Required.
- `target`: the issue or PR number to attach to. Required.
- `target_type`: `issue` or `pr`. Required.
- `mode`: `comment` (add a new comment) or `edit` (append into the target body). Required.
- `alt`: alt text for the embedded reference. Optional; defaults to the file name.

**Output** (when run as a subagent, the final message MUST be exactly this — no prose around it):

- Success: two lines, the attachment URL and the embed location, e.g.

  ```
  url: https://github.com/user-attachments/assets/<uuid>
  location: https://github.com/<owner>/<repo>/issues/<n>#issuecomment-<id>
  ```

  (for `edit` mode the `location` is the target issue/PR URL, e.g. `.../issues/<n>` or `.../pull/<n>`).
- Failure: `FAILED: <one-line reason and, if applicable, the remediation hint>`

Never fabricate a URL; if the attachment does not complete, return `FAILED:`.

## Process

1. **Validate input** — `file`, `repository`, `target`, `target_type` (`issue`|`pr`), and `mode`
   (`comment`|`edit`) are present, and `file` exists on disk. If not: `FAILED: invalid input <detail>`.
2. **Check gh** — `gh --version` must be ≥ 2.99.0 (the `--attach` flag). If unavailable:
   `FAILED: gh 2.99.0+ required for --attach (found <version>)`.
   The `gh` subcommand base is `gh issue …` when `target_type` is `issue`, and `gh pr …` when it is `pr`.
3. **Attach and extract** — GitHub rewrites a body reference `![<alt>](<file>)` into the
   `user-attachments` URL. gh prints the resulting comment URL (comment mode) to stdout. Note gh behavior:
   if some attachments fail, the command still applies the successful ones, exits non-zero, and still prints
   the URL — so treat a non-zero exit as a partial/total failure and return `FAILED:`.

   **`comment` mode:**
   1. `gh <issue|pr> comment <target> -R <owner/repo> --body '![<alt>](<file>)' --attach '<file>#<alt>'`
      Capture stdout as the comment URL (e.g. `.../issues/<n>#issuecomment-<id>`).
   2. Parse the comment id (the number after `issuecomment-`), then read the comment body:
      `gh api repos/<owner>/<repo>/issues/comments/<id> -q .body`
      (PR conversation comments are issue comments, so this endpoint works for both.)
   3. Extract the single `https://github.com/user-attachments/assets/<uuid>` from that body.
      `location` is the comment URL from step 3.1.

   **`edit` mode:**
   1. Read the current body: `BEFORE=$(gh <issue|pr> view <target> -R <owner/repo> --json body -q .body)`.
   2. `gh <issue|pr> edit <target> -R <owner/repo> --body "<BEFORE>\n![<alt>](<file>)" --attach '<file>#<alt>'`.
   3. Read the body again: `AFTER=$(gh <issue|pr> view <target> -R <owner/repo> --json body -q .body)`.
   4. The new `user-attachments/assets/<uuid>` URL is the one present in `AFTER` but not in `BEFORE`.
      `location` is the target issue/PR URL (`.../issues/<n>` or `.../pull/<n>`).
4. **Verify** — success is confirmed when a `user-attachments/assets/<uuid>` URL was extracted AND gh exited
   zero. If gh exits non-zero, or no such URL appears (e.g. GitHub rejected the file type or size):
   `FAILED: attach did not complete <detail>`. Do NOT re-check the URL over the network — anonymous access to
   `user-attachments` assets 404s by design (served through signed redirects); rendering is ultimately
   confirmed when the URL is viewed in the issue/PR.
5. **Return** — output exactly the two `url:` / `location:` lines (success) or `FAILED: <reason>` (failure).

## Authorization

Attaching writes data to GitHub (a new comment, or an edit to an existing body). Callers MUST invoke this
skill only after the user has explicitly approved the GitHub write it is part of (e.g. `plan-to-issue` and
`create-pr` call it after their authorization gate passes). This skill mutates the provided target and never
creates a new issue/PR.

## Notes for Callers

- Invoke through a subagent and treat its final message as the return value: the `url:`/`location:` pair,
  or `FAILED: ...`.
- The target issue/PR must already exist. A caller that is creating the issue/PR itself can either create it
  first and then call this skill, or attach at creation time with `gh <issue|pr> create --attach` directly.
- For a mermaid diagram, produce the file first with `mermaid-to-svg` and pass its returned path here:
  `mermaid-to-svg` → SVG path → `github-attachment-upload` (with a target issue/PR) → attachment URL.
- Attachment URLs are immutable. To update an image, produce a new file and attach it again.
- Choose `mode`: `comment` keeps the existing body intact and yields a clean, single-attachment comment to
  read the URL back from; `edit` embeds the file into the target body itself.

## Final Checklist

- [ ] Input had `file` (existing on disk), `repository`, `target`, `target_type`, and `mode`.
- [ ] `gh` was ≥ 2.99.0 and the attach was driven via `gh <issue|pr> <comment|edit> --attach`.
- [ ] No new issue/PR was created; only the provided target was mutated.
- [ ] A `user-attachments/assets/<uuid>` URL was extracted and gh exited zero before returning.
- [ ] Returned exactly the `url:`/`location:` pair (success) or `FAILED: <reason>` (failure) — nothing else.
