# Note formats

Section numbers (`section N`, `6.x`) refer to [SKILL.md](../SKILL.md). This file holds the examples and the layout details of what `commit --notes` writes; the rules for writing them stay in SKILL.md.

## A formatted `## 概要`

Section 1 (the `request` and `original` of `create`). Example, from the chat message 「uploader のリトライが効いてないみたい https://github.com/org/repo-a/issues/12 ログは ERROR retry=0 timeout。直して PR まで作ってよい」:

  ````markdown
  ## 概要

  repo-a の uploader でリトライが効いていない問題を直す。

  - 関連 issue:
    https://github.com/org/repo-a/issues/12
  - 観測されたログ:

    ```text
    ERROR retry=0 timeout
    ```

  - 修正後に PR まで作ってよい

  > [!quote]- 依頼の原文
  > uploader のリトライが効いてないみたい https://github.com/org/repo-a/issues/12 ログは ERROR retry=0 timeout。直して PR まで作ってよい
  ````

## `## アクション`

Section 5 (User actions). An example:

```markdown
## アクション

### [UA-0240] @user Decide whether to push branch `claude/fix-typo` of `repo-a` (and whether to open a PR)

<!-- sv ask answer since=2026-11-11T22:40:00+09:00 -->

> [!sv-agent] 2026-11-11 22:40
> Fixed 3 typos in README.md in e9d32372d6.
>
> [compare](https://github.com/org/repo-a/compare/main...claude/fix-typo)
>

#### やりとり

- [ ] push して PR を作る
- [ ] push だけする(PR は作らない)
- [ ] push しない



### [AA-0242] @agent CHANGELOG にも typo 修正を記載する

<!-- sv req since=2026-11-11T22:50:00+09:00 -->

> README の修正内容を CHANGELOG の Unreleased に 1 行で足してください。

#### やりとり

> [!sv-agent] 2026-11-11 22:52
> w-0005 に送りました。
>

日付も添えてください。

### @agent docs/ 配下の Markdown も同じ typo がないか見る

The user's own request, not numbered yet, in the user's words.

> [!done]- 完了 (1)
> - [UA-0238] @user 作業場所を教えてください <!-- sv ask answer since=2026-11-11T22:10:00+09:00 done=2026-11-11T22:15:00+09:00 r901 -->
>     > どのリポジトリ (owner/repo) の README ですか。
>
>     #### やりとり
>
>     github.com/handlename
>
>     > [!sv-agent] 2026-11-11 22:12
>     > どのリポジトリですか。
>     >
>
>     handlename/agent-skills
```

- **Open item**: `### [UA-NNNN] @user <short title>` or `### [AA-NNNN] @agent <short title>`, a blank line, the metadata line `<!-- sv ... -->` (kind, `since`; never edited by hand), a blank line, and the body. Every heading the supervisor writes has a blank line below it; notes written before 0.65.3 have the metadata line right below the heading, read the same way. The heading is the `description`: one short line, the wording this file gives. Details go in the body.
- **Thread**: a user action of kind `ask`, `auto=worker_report`, or `auto=pr_closed` ends with `#### やりとり`, the exchange about the item up to the next heading of level 1-3 or the done callout. Other `auto` items have none (`ua_answer` adds it); under an agent action it appears with the first reply. A blank line follows the heading and separates the messages. The supervisor's messages are callouts `> [!sv-agent] <YYYY-MM-DD HH:MM>` with the text quoted and a closing empty `>` line, so that text typed right below one does not join it (Markdown lazy continuation); the user's messages are the plain text (any Markdown) between them. After writing a message of the supervisor into an open item, `ua_answer` leaves three blank lines before the next block: one below the message, one to type in, one to separate. Under an agent action, plain text before its first `[!sv-agent]` is a reply written before 0.65.0 (the agent's). Notes written before 0.65.0 have `#### 回答` instead; it is read the same way, and new items get `#### やりとり`.
- **Done**: `ua_ack` moves the item into the single callout `> [!done]- 完了 (N)` at the end of the section, as `> - [<id>] @<who> <title> <!-- sv ... -->` with its body and `>     #### やりとり` indented below (without unchecked options).
- **User-written text** (answers, `### @agent` requests): no line may start with `## `, `### `, `#### やりとり`, `#### 回答`, or `> [!sv-agent]`, because such a line splits the item or reads as the supervisor's; the user's own headings use `#####` or lower.

## `## サブタスク`

Section 5 (Subtasks). An example:

```markdown
## サブタスク

- [x] [ST-0290] hook エラーの調査 (w-0145 e1513f0)
- [ ] [ST-0291] @agent mise trust の自動化 (w-0147)
- [ ] [ST-0292] @user nature-server で動作確認する
- [ ] 書いたばかりの行
```
