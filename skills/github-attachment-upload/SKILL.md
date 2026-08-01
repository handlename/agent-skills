---
name: github-attachment-upload
description: Upload an arbitrary local file to GitHub user-attachments by driving a real browser with playwright-cli against an issue editor, then return the attachment URL. File-type agnostic — works for SVG, PNG, screenshots, or any file GitHub's editor accepts; it does not create an issue or comment. Designed to be called from other skills (e.g. plan-to-issue, create-pr) through a subagent whose only return value is the URL on success or a failure reason on failure. Use when a local file must become a GitHub user-attachments URL. Triggers include "このファイルをGitHubのuser-attachmentsに上げてURLをちょうだい", "upload this file to GitHub user-attachments", "get me an attachment URL for this image".
---

# GitHub Attachment Upload

## Overview

Take a **local file** and upload it as a **GitHub attachment** (`user-attachments`) by driving a real browser with **playwright-cli**, then return the attachment URL. The upload is performed through GitHub's web issue editor — the same mechanism as drag & drop — but **no issue or comment is created**; the draft is abandoned once the URL is obtained.

This skill is **file-type agnostic**: it uploads whatever file it is given (SVG, PNG, screenshot, PDF, …) as long as GitHub's editor accepts it. It does NOT know or care how the file was produced, does NOT embed the URL anywhere, and does NOT fall back when uploading is impossible. Those are the caller's responsibilities.

## When to Use

- Another skill (e.g. `plan-to-issue`, `create-pr`) has a local image file (such as an SVG from `mermaid-to-svg`) and needs it published as an issue-attachable `user-attachments` URL.
- The user has a file and wants a GitHub `user-attachments` URL for it without creating an issue.

## Do Not Use When

- The destination is something other than GitHub issues/PRs/comments — `user-attachments` URLs are a GitHub feature.
- The file does not exist yet (e.g. an SVG still needs drawing — that is `mermaid-to-svg`).
- The caller has no user approval for GitHub writes yet — see Authorization.

## Contract (input / output)

**Input** (provided by the caller, e.g. in the subagent prompt):

- `file`: absolute path to the local file to upload. Required.
- `repository`: `owner/repo` whose issue editor is used to perform the upload. Required.

**Output** (when run as a subagent, the final message MUST be exactly this — no prose around it):

- Success: the attachment URL only, e.g. `https://github.com/user-attachments/assets/<uuid>`
- Failure: `FAILED: <one-line reason and, if applicable, the remediation hint>`

Never fabricate a URL; if the upload does not complete, return `FAILED:`.

## Process

1. **Validate input** — both `file` and `repository` present, and `file` exists on disk. If not: `FAILED: invalid input <detail>`.
2. **Invocation convention** — run every playwright-cli command as `npx -y @playwright/cli <command>`. npx auto-installs the package on first use, so no preinstall check is needed (`-y` skips the interactive install prompt, which would hang an agent). Do NOT use `npx playwright-cli` — that resolves a different npm package. The steps below write `playwright-cli` for brevity. If npx itself cannot fetch the package (offline, registry error): `FAILED: could not run @playwright/cli via npx (<error>)`.
3. **Upload via the issue editor** — the goal is to make GitHub's web editor perform the attachment upload, then leave WITHOUT creating anything:
   1. `playwright-cli open --persistent https://github.com/<owner>/<repo>/issues/new`
   2. Take a `snapshot`. If a login form appears instead of the issue editor: close and return `FAILED: GitHub session not logged in — run 'npx -y @playwright/cli open --persistent --headed https://github.com/login' once and sign in, then retry` (`--headed` is required: the default headless browser shows no window to sign in with).
   3. Open the file-chooser modal first, then upload: click the editor's **"Paste, drop, or click to add files"** button (below the body textarea) to open the file chooser, THEN run `upload <path/to/file>` against it. Uploading against a merely-focused textarea fails with `browser_file_upload can only be used when there is related modal state present` — the file-chooser modal must be open. This drives the same attachment mechanism as drag & drop.
   4. Wait until the editor finishes uploading: the body textarea content changes from an "Uploading..." placeholder to text containing `https://github.com/user-attachments/assets/<uuid>`. Read it from a fresh `snapshot`.
   5. Extract the URL. Then **abandon the draft** — do NOT submit the issue. The uploaded attachment remains valid even though no issue was created.
4. **Verify** — the success signal is the editor behavior in step 3.4: the upload is confirmed exactly when the "Uploading…" placeholder is replaced by the final `![...](https://github.com/user-attachments/assets/<uuid>)` (or a non-image `[...](...)` link for non-image files) markdown. If instead an error appears (e.g. GitHub rejects the file type or size) or the placeholder never resolves: `FAILED: upload did not complete <detail>`. Close the browser (`playwright-cli close`). Do NOT try to re-check the URL over the network — every path fails for the wrong reasons: anonymous `curl` returns 404 by design (attachments are served through signed `private-user-images` redirects), in-page `fetch` is blocked by CSP, and direct browser navigation turns into a download and times out. Rendering is ultimately confirmed when the caller posts the URL.
5. **Return** — output the URL as the entire final message.

## Authorization

Uploading an attachment writes data to GitHub even though no issue is created. Callers MUST invoke this skill only after the user has explicitly approved the GitHub write it is part of (e.g. `plan-to-issue` and `create-pr` call it after their authorization gate passes). This skill itself never creates issues or comments.

## Notes for Callers

- Invoke through a subagent and treat its final message as the return value: a URL, or `FAILED: ...`.
- For a mermaid diagram, produce the file first with `mermaid-to-svg` and pass its returned path here: `mermaid-to-svg` → SVG path → `github-attachment-upload` → attachment URL.
- Attachment URLs are immutable. To update an image, produce a new file, upload it again, and replace the old URL.
- Embed the result as `![<alt text>](<url>)` for images.

## Final Checklist

- [ ] Input had both a file path (existing on disk) and a target repository.
- [ ] Upload driven through playwright-cli with the persistent profile; no issue or comment was created.
- [ ] Returned exactly the attachment URL (success) or `FAILED: <reason>` (failure) — nothing else.
- [ ] Upload confirmed via the editor's placeholder → attachment-markdown replacement before returning.
