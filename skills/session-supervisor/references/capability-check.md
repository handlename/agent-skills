# Capability check

Section numbers (`section N`, `6.x`) refer to [SKILL.md](../SKILL.md).

Run at the start of every supervisor session and after every recovery (section 7). The capabilities it checks are the commands in the table of SKILL.md section 2.

1. Confirm the session runtime. This session is Claude Code in the `hc` terminal `<name_prefix>sv`: `$HC_TERMINAL_ID` is set and `hc get <name_prefix>sv --json` returns that address (section 0); its success also proves that herdr-core is running (never run `hc server ...`: the guard blocks it). The capabilities the procedures use are the commands in the table of SKILL.md section 2.

2. Check the permission mode. Run `sv` in auto mode ([setup.md](setup.md), Starting the supervisor); workers and managers start in auto mode too (`launch-plan`).
3. If `supervisor.archive_consent` is null, ask the user once: "May I archive workers and managers started by this supervisor without asking, once you confirm their task complete or cancel it? Archiving ends the session and removes a clean worktree; branches stay." Record the verbatim answer as `archive_consent`. Without consent, every archive becomes the user action "Please archive w-xxxx" (the user runs `hc kill <terminal>` themselves, with the worker's `terminal`).
4. Check `sv-watch`: `hc get <name_prefix>sv-watch --json` must show a live terminal. If it is missing or finished, start it again as [setup.md](setup.md), Starting the supervisor, shows.
5. Compare the installed copy with the bundled copy:

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" version
   python3 "<skill-dir>/scripts/state_tool.py" version
   ```

   If the two outputs differ, re-run setup ([setup.md](setup.md)), then restart `sv-watch` so it runs the new copy. Also read `<dir>/.claude/settings.json`: if no PreToolUse entry has a command that contains both `state_tool.py` and ` guard --dir`, ask the user once whether they removed it on purpose; re-run setup only if they did not, and otherwise leave it removed for this supervisor directory. Ask the user to restart `sv` (`<dir>/sv.sh restart`, or to review hooks in `/hooks`) only when setup printed `created:` or `updated:` for `settings.json`, because hooks and the `model` setting are loaded at session start; a run that printed only `exists:` needs no restart. Likewise ask the user to restart `sv-chat` (if it runs; `<dir>/sv.sh chat`) when setup printed `created:` or `updated:` for a file under `<dir>/chat`.

   For each project without `close`, read `<project dir>/.claude/settings.json`: if no PreToolUse entry has a command that contains `state_tool.py`, ` guard --dir`, and `--role manager`, re-run `prepare-dir --project <name of the project dir>` (6.10; idempotent, it prints `updated:`). A running manager loads hooks only at its start, so the restored guard applies from its next replacement.
6. Check your own model from your environment information. If it does not name opus, tell the user once per supervisor session: "This supervisor is not running on opus; restart `sv` with `<dir>/sv.sh restart`", and continue.
7. Check the follow-up rules. This runs after step 5 so that a stale installed copy has already been replaced by one that knows the subcommand. It only reads `<dir>/CLAUDE.md` and runs no `gh`:

   ```bash
   python3 "<dir>/.supervisor/state_tool.py" validate-follow-ups --claude-md "<dir>/CLAUDE.md"
   ```

   `ok: N rules` means the rules are usable (`ok: 0 rules` when there is no `## Follow-ups`). On exit 1, tell the user the `error: CLAUDE.md:<line>: ...` lines once and continue: with an invalid section no follow-up is created, and everything else works as usual.

## Facts

Facts the procedures rely on (observed with hc 0.1.19 and Claude Code 2.1.322):

- **Starting** (F1, F2, S8): `hc run --bg ... claude ... "<prompt>"` starts Claude Code in its `--cwd` and delivers the prompt. It works in any turn, including turns started by a signal or `/loop`. A variadic option directly before the prompt swallows it, which is why `launch-plan` writes `--allowedTools=...` as one argument and puts the prompt last.
- **State** (F3, S7): `hc get` reports `status.state` (`working`, `idle`, `done`, `blocked`) and `turn`; a blocked session also has `status.kind` (`permission`) and `status.msg` (the command waiting for approval). `hc events` streams the same changes.
- **Sending** (F4, C2): `hc send` types the text and presses enter. Text sent during a turn is queued and handled in that same turn; nothing is lost and no extra turn starts.
- **Ending** (F5, S5): `hc kill` ends the program (`reason: killed`); `hc rm` removes the finished terminal. Neither touches the worktree or the branch. When `sv` itself exits, its terminal finishes; children started with `--outlive-parent` keep running.
- **Trust** (F6, F7, C1): Claude Code asks whether to trust a git repository the first time it runs there, and records the answer for the repository, not the worktree: worktrees of a trusted repository start without the dialog. Non-git directories start without the dialog. A worker in an untrusted repository waits at the dialog and its prompt is delivered only after the user answers.
- **Signals to the parent** (S1–S4, S9): a child that ends its turn without reporting sends nothing. A child's `hc send <name_prefix>sv "<text>"` arrives as `<hc from="tN <name_prefix>w-NNNN"><text></hc>` and starts a turn of `sv`. `hc` itself types `<hc>tN <name_prefix>w-NNNN is blocked (permission). See: hc read tN</hc>` when a child waits for approval and `<hc>tN <name_prefix>w-NNNN exited (exit N). Read: hc read tN</hc>` when it ends (an archive by `sv` included). One signal starts one turn; no duplicates were seen. These notes go to the parent terminal only, and Herdr shows a child under its parent's pane (`sub_agents` in `herdr-app ls`).
- **Project workers under their manager**: `launch-plan` starts a worker of a project task with `--parent <manager address> --no-notify` when the manager's terminal `p-NNNN` is live, so Herdr shows it under the manager. `--no-notify` keeps `hc`'s blocked / exit notes out of the manager's input box; `sv-watch` forwards those changes to `sv` instead (its parent is not `sv`), within its 10-second interval. Its `hc send <name_prefix>sv` and everything matched by name (`observe-hc`, `hc get`, `hc send`, archive) work as for any worker. With `--outlive-parent` it keeps running when the manager ends; after the manager's terminal is removed (6.12) it keeps running under no pane, like the children of an earlier `sv`. A worker started while the manager is not live starts under `sv` as before.
- **Re-creating `sv`** (S6): a child's `hc send <name_prefix>sv` reaches a new terminal with the same name, but `hc`'s own blocked / exit notes go to the original parent terminal and are lost. `sv-watch` forwards those changes as `worker: <name> <blocked|finished> (hc read <address>)`.
- **Typing collision** (C3): `hc send` appends its text to whatever is typed in the input box and submits both. The user's unfinished text in `sv` can be sent early, joined to a signal.
- **Core restarts** (C4): `hc server restart` and `hc update` keep programs running. `hc server stop` and a crash of the core end them: their workers become `missing` (6.4).
- **Environment** (F11): `hc` starts `claude` without an interactive shell, so PATH entries that only an interactive shell adds (for example mise shims, needed by hooks that run `node`) are missing. `worker_path` in `## Herdr` ([setup.md](setup.md)) fixes this for workers and managers.
