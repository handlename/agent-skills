<%*
// Templater hotkey only (do not insert it with the core Templates plugin): changes the status/assignee of the
// supervised note being edited to a value the supervisor's notes-scan classifies (state_tool.py user_transition).
// setup overwrites this file; put a changed copy under another name.
const MOVES = [
  {"kind": "task", "label": "agent に渡す", "from": ["backlog/user"], "to": "backlog/agent"},
  {"kind": "task", "label": "agent に渡す", "from": ["in progress/user", "pending/user"], "managed": true, "to": "in progress/agent"},
  {"kind": "task", "label": "agent に渡す", "from": ["in progress/user", "pending/user"], "managed": false, "to": "backlog/agent"},
  {"kind": "task", "label": "agent に渡す", "from": ["done/agent", "done/user"], "to": "backlog/agent"},
  {"kind": "task", "label": "自分に引き取る", "from": ["in progress/agent"], "to": "in progress/user"},
  {"kind": "task", "label": "保留", "from": ["in progress/user", "in progress/agent"], "to": "pending/user"},
  {"kind": "task", "label": "保留", "from": ["backlog/user", "backlog/agent"], "managed": false, "to": "pending/user"},
  {"kind": "task", "label": "完了確認", "from": ["in progress/user"], "when": "held_only", "to": "done/user"},
  {"kind": "task", "label": "完了確認", "from": ["done/agent"], "to": "done/user"},
  {"kind": "task", "label": "完了にする(残りの作業は取り消し)", "from": ["in progress/user"], "when": "unheld", "to": "done/user"},
  {"kind": "task", "label": "完了にする(残りの作業は取り消し)", "from": ["in progress/agent", "pending/user"], "managed": true, "to": "done/user"},
  {"kind": "task", "label": "取り消す", "from": ["backlog/user", "backlog/agent", "in progress/user", "in progress/agent", "pending/user", "pending/agent"], "to": "archived/user"},
  {"kind": "project", "label": "agent に渡す", "from": ["backlog/user", "in progress/user", "pending/user"], "to": "backlog/agent"},
  {"kind": "project", "label": "完了を差し戻す", "from": ["in progress/user"], "when": "done_proposed", "to": "in progress/agent"},
  {"kind": "project", "label": "完了を差し戻す", "from": ["done/agent"], "managed": true, "to": "in progress/agent"},
  {"kind": "project", "label": "自分に引き取る", "from": ["in progress/agent"], "to": "in progress/user"},
  {"kind": "project", "label": "保留", "from": ["in progress/user", "in progress/agent"], "to": "pending/user"},
  {"kind": "project", "label": "保留", "from": ["backlog/user", "backlog/agent"], "managed": false, "to": "pending/user"},
  {"kind": "project", "label": "完了確認", "from": ["in progress/user"], "when": "done_proposed", "to": "done/user"},
  {"kind": "project", "label": "完了確認", "from": ["done/agent"], "to": "done/user"},
  {"kind": "project", "label": "完了", "from": ["in progress/agent", "in progress/user", "pending/user"], "managed": true, "when": "not_proposed", "to": "done/user"},
  {"kind": "project", "label": "取り消す", "from": ["backlog/user", "backlog/agent", "in progress/user", "in progress/agent", "pending/user", "pending/agent"], "to": "archived/user"}
];

const unquote = (value) => (value || "").trim().replace(/^(["'])(.*)\1$/, "$2");
const editor = app.workspace.activeEditor && app.workspace.activeEditor.editor;
const fm = (app.metadataCache.getFileCache(tp.config.target_file) || {}).frontmatter || {};
const tags = [].concat(fm.tags || []);

if (!tags.includes("supervised")) {
  new tp.obsidian.Notice("supervised タグのあるノートで実行してください");
} else if (!editor) {
  new tp.obsidian.Notice("編集表示で実行してください");
} else {
  const lines = editor.getValue().split("\n");
  const close = lines[0] === "---" ? lines.indexOf("---", 1) : -1;
  const find = (key) => lines.slice(1, close).findIndex((line) => line.startsWith(key + ":")) + 1;
  const value = (key) => (find(key) > 0 ? unquote(lines[find(key)].slice(key.length + 1)) : "");
  const kind = tags.includes("project") ? "project" : "task";
  const managed = Boolean(value("task_id") || value("project_id"));
  const now = value("status") + "/" + value("assignee");
  // state_tool never writes done/agent: a finished task shows in progress/user with only its completion ask
  // (auto=task_done) open, a task held only for investigation results shows in progress/user with no open @user
  // action, and a project the manager proposes as done shows in progress/user with its completion ask.
  // done/agent comes only from notes written by earlier versions. done/user with anything else open cancels the rest
  // of the task and marks it done (notes-scan row 15).
  const body = lines.slice(close + 1);
  // The metadata line is right below the heading, or after one blank line (0.65.3 and later).
  const meta = (i) => (body[i + 1] || "").trim() ? body[i + 1] : body[i + 2] || "";
  const open = body.map((line, i) => (/^### \[UA-\d+\] @user /.test(line) ? meta(i) : null)).filter((m) => m !== null);
  // A numbered subtask left unchecked keeps the task open as well: done/user would cancel it.
  const subtasks = body.some((line) => /^- \[ \] \[ST-\d+\] /.test(line));
  const waiting = !subtasks && open.every((meta) => /^<!-- sv auto=task_done /.test(meta));
  const when = {
    held_only: managed && waiting,
    unheld: managed && !waiting,
    done_proposed: managed && body.some((line) => line.startsWith("### [UA-")
      && (line.includes("@user プロジェクトの完了を確認してください") || line.includes("@user Confirm that the project is done"))),
  };
  // A project the manager has not proposed as done can still be closed by the user (notes-scan row 14).
  when.not_proposed = managed && !when.done_proposed;
  const moves = close < 0 ? [] : MOVES.filter((m) => m.kind === kind && m.from.includes(now)
    && (m.managed === undefined || m.managed === managed) && (m.when === undefined || when[m.when]));
  if (!moves.length) {
    new tp.obsidian.Notice(`${now} から変えられる操作はありません`);
  } else {
    const move = await tp.system.suggester(moves.map((m) => `${m.label}(${m.to})`), moves);
    if (move) {
      const [status, assignee] = move.to.split("/");
      const changes = [["status", status], ["assignee", assignee]].map(([key, v]) => {
        const at = find(key);
        return at > 0
          ? { from: { line: at, ch: 0 }, to: { line: at, ch: lines[at].length }, text: `${key}: "${v}"` }
          : { from: { line: close, ch: 0 }, text: `${key}: "${v}"\n` };
      });
      // Origin "set" (Obsidian's own for programmatic edits) skips Live Preview's properties filter, which drops a
      // frontmatter change while the cursor is in the frontmatter or at the start of the line right below it.
      editor.transaction({ changes }, "set");
    }
  }
}
tR = "";
-%>
