---
uid: 20261111_230000
title: "[サンプル] README の typo を直す"
para: Project
aliases: []
created_at: 2026-11-11 23:00:00
archived_at:
tags: ["sample"]
start_date:
due_date:
pending_until:
completed_at:
status: "in progress"
assignee: "user"
task_id: T-0999
---
%% 表示確認用のサンプルです。supervised タグが無いので supervisor には拾われません。確認が済んだら削除してください。 %%
## 概要

handlename/agent-skills の README.md にある typo を探して直す。

- 対象: `README.md` のみ
- commit まで行い、push はしない

> [!quote]- 依頼の原文
> handlename/agent-skills の README.md の typo を探して直す。commit まで、push はしない。

## アクション

### [UA-0240] @user Decide whether to push branch `claude/t0999-readme-typo-1a2b3c` of `agent-skills` (and whether to open a PR)
<!-- sv ask answer since=2026-11-11T22:40:00+09:00 -->

README.md の typo を 3 か所直した e9d32372d6。テストへの影響はない。

[比較](https://github.com/handlename/agent-skills/compare/main...claude/t0999-readme-typo-1a2b3c)

#### 回答 


### [UA-0241] @user Answer w-0130 (T-0999 README の typo) in Herdr
<!-- sv auto=worker_report since=2026-11-11T22:45:00+09:00 -->

> 「Compatibilty」の表記は表の見出しにも使われています。
> 見出しも含めてすべて「Compatibility」に直してよいですか。
>
> (1) すべて直す (2) 本文だけ直す

[w-0130 の報告](https://github.com/handlename/agent-skills/blob/main/README.md)

#### 回答


### [AA-0242] @agent CHANGELOG にも typo 修正を記載する
<!-- sv req since=2026-11-11T22:50:00+09:00 -->

> README の修正内容を CHANGELOG の Unreleased に 1 行で足してください。
> ## のような行が原文にあっても、引用の中なので見出しになりません。

### @agent docs/ 配下の Markdown も同じ typo がないか見る

ユーザーが追記した、まだ番号の無い依頼です。「agent に渡す」で supervisor が AA 番号を振ります。

> [!done]- 完了 (2)
> - [UA-0238] @user 作業場所を教えてください <!-- sv ask answer since=2026-11-11T22:10:00+09:00 done=2026-11-11T22:15:00+09:00 r901 -->
>     > どのリポジトリ (owner/repo) の README ですか。
>
>     #### 回答
>     handlename/agent-skills
> - [UA-0239] @user Answer w-0130 (T-0999 README の typo) in Herdr <!-- sv auto=worker_report since=2026-11-11T22:20:00+09:00 done=2026-11-11T22:30:00+09:00 r905 -->
>     > 英語の綴りは米国式と英国式のどちらに揃えますか。
>
>     #### 回答
>     米国式(w-0130 のセッションで回答)

## サブタスク


## 関連ノート


## 関連資料


## 成果物

<!-- state_tool が書く行です。表示テキストは直せますが、メタデータ(sv-art)のある行に書き足した文字は、成果物のグループを置き換えるときに行ごと消えます。 -->
- w-0130: [claude/t0999-readme-typo-1a2b3c](https://github.com/handlename/agent-skills/tree/claude/t0999-readme-typo-1a2b3c) e9d32372d6

## ログ

- 2026-11-11 22:10 受け付けた。作業場所が分からないため質問した。 ^sv-log-r900-0
- 2026-11-11 22:15 回答「handlename/agent-skills」により開始した。w-0130 が agent-skills の worktree で作業する。 ^sv-log-r901-0
- 2026-11-11 22:30 w-0130 がセッション内で回答を受けた([UA-0239])。 ^sv-log-r905-0
- 2026-11-11 22:40 w-0130 の結果を検証した。push の判断を UA-0240 で尋ねた。 ^sv-log-r910-0
