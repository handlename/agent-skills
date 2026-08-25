---
name: hwwwn
description: Use when writing or reviewing code comments and documentation to decide what belongs where and how to phrase it. Applies the HWWWN routing — How to code, What to test code, Why to the commit log, Why not to code comments (after t_wada) — and gives concrete rules for writing comments that are minimal, break lines only at sentence boundaries, and never restate what the code already says. Triggers include "how should I comment this", "コメントの書き方", "where should this explanation go", "この説明はどこに書く", "hwwwn", "write a comment for", "review these comments".
---

# HWWWN — Documentation Placement & Comment Writing

**HWWWN** = **H**ow · **W**hat · **W**hy · **W**hy **n**ot — the four channels and the question each one answers.

A passive reference for **where each kind of information belongs** and **how to write the comments that remain**. Apply it whenever you write or review code, comments, tests, or commit messages.

## Core Principle: Route by Intent

Each channel answers a different question. Put information where its question is answered — do not duplicate it across channels.

| Channel | Answers | What lives here |
|---------|---------|-----------------|
| **Code** | *How* | The mechanism itself. Clear names and structure make the "how" self-evident. |
| **Test code** | *What* | The expected behavior and contract — what the code should do, expressed as executable examples. |
| **Commit log** | *Why* | Why the change was made: the motivation, the problem being solved, the context at the time. |
| **Code comment** | *Why not* | Why the code is written *this way and not the obvious alternative* — the non-obvious constraint, trade-off, or gotcha that the code cannot express by itself. |

Source: t_wada — the [original four-channel post](https://x.com/t_wada/status/904916106153828352?s=20) and the [HWWWN mnemonic](https://x.com/t_wada/status/935042169655533568?s=20).

**Placement test** — before writing a comment, ask which question the information answers:

- Explaining *how* the code works → the code should already show it; improve names/structure instead of commenting.
- Describing *what* it should do → write or strengthen a test.
- Recording *why* the change happened → put it in the commit message.
- Justifying *why not* the alternative → this is the one case that earns a code comment.

## Rules for Code Comments

When information genuinely belongs in a code comment (the *Why not* case), write it under these rules.

### 1. Minimal and to the point

Write the least that conveys the intent. Strip filler and roundabout phrasing; say the one thing the reader needs. A comment is not prose — it is a pointer to the non-obvious.

### 2. Break lines only at sentence boundaries

When a comment spans multiple lines, break **only** at sentence or clause boundaries (in Japanese, at 句点「。」and 読点「、」; in English, at the end of a sentence or a major clause). Do **not** impose an artificial column limit such as "wrap at 70 characters" — modern displays are wide enough. Line breaks should follow meaning, not width.

### 3. Do not restate what the code shows

If the reader can learn it by reading the code, the comment is noise — delete it. And if a piece of code seems to *need* a long explanatory comment to be understood, treat that as a smell: the real problem is usually poor separation of responsibilities. Fix the structure (extract a well-named function, split the responsibility) instead of papering over it with a comment.

## Precedence

These are default conventions. When a language's idiom or a project's own rules say otherwise, **follow the language/project rule first**. Examples:

- A language whose ecosystem expects doc comments on every exported symbol (e.g. Go's `// Foo does …`, Rust's `///`) — honor that convention even though it describes *what/how*, because tooling and readers depend on it.
- A project style guide that mandates a specific comment format or a column limit — obey it.

This skill fills the gaps such rules leave open; it does not override them.

## Examples

**Bad** — restates the code (violates Rule 3):

```go
// increment i by one
i++

// loop over users
for _, u := range users {
    // send the email
    send(u.Email)
}
```

**Good** — captures *Why not*, the non-obvious constraint the code cannot show:

```go
// Retry only on 5xx: the upstream returns 429 for permanent quota
// errors, so retrying those would just burn the quota further.
if resp.StatusCode >= 500 {
    return retry()
}
```

**Bad** — a long comment compensating for a tangled block (Rule 3 smell):

```python
# First we normalize the address, then geocode it, then look up the
# timezone, then convert the timestamp, then format it for display,
# because the upstream API gives everything in UTC and raw strings.
result = do_everything(raw)
```

**Good** — split the responsibilities so the code reads itself; comment only what stays non-obvious:

```python
address = normalize(raw.address)
tz = timezone_of(geocode(address))
# Upstream timestamps are UTC with no offset marker; attach tz explicitly.
local_time = to_local(raw.timestamp, tz)
```

## Verification

Before leaving a comment in place, confirm:

- [ ] The information is in its correct channel (How→code, What→test, Why→commit, Why not→comment).
- [ ] The comment says something the code itself cannot show.
- [ ] It is phrased minimally, with no filler.
- [ ] Any line breaks fall at sentence/clause boundaries, not at an arbitrary column.
- [ ] No long comment is masking a responsibility that should be separated instead.
- [ ] Any language/project-specific comment rule has been honored over these defaults.
