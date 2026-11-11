<%*
// Templater only: picks the project of a supervised task note, or none, and writes its `parent` line, which the
// supervisor's notes-scan resolves to the project at pickup (state_tool.py parent_project_id).
// Include it in the task note template with tp.file.include("[[sv-project]]"), or run it as a hotkey on a task note.
// setup overwrites this file; put a changed copy under another name.
const file = tp.config.target_file;
const fmOf = (f) => (app.metadataCache.getFileCache(f) || {}).frontmatter || {};
const own = fmOf(file);
if (own.task_id) {
  new tp.obsidian.Notice("取り込み済みのタスクのプロジェクトは変えられません");
} else {
  const projects = app.vault.getMarkdownFiles()
    .map((f) => ({ f, fm: fmOf(f) }))
    .filter(({ f, fm }) => f.path !== file.path && [].concat(fm.tags || []).includes("supervised")
      && [].concat(fm.tags || []).includes("project") && fm.project_id && !["done", "archived"].includes(fm.status))
    .sort((a, b) => String(a.fm.project_id).localeCompare(String(b.fm.project_id)));
  const none = { f: null };
  const choice = await tp.system.suggester(
    ["(プロジェクトなし)", ...projects.map(({ f, fm }) => `${fm.project_id} ${fm.title || f.basename}`)],
    [none, ...projects]);
  if (choice) {
    const title = choice.f && String(fmOf(choice.f).title || choice.f.basename).replace(/[|\]"]/g, " ");
    const line = choice.f && `parent: "[[${choice.f.path.replace(/\.md$/, "")}|${title}]]"`;
    const setParent = (text) => {
      const lines = text.split("\n");
      const close = lines[0] === "---" ? lines.indexOf("---", 1) : -1;
      if (close < 0) {
        return line ? ["---", line, "---", ...lines].join("\n") : text;
      }
      const at = lines.slice(1, close).findIndex((l) => l.startsWith("parent:")) + 1;
      if (at > 0) {
        let end = at + 1;
        while (end < close && /^\s/.test(lines[end])) end += 1;
        lines.splice(at, end - at, ...(line ? [line] : []));
      } else if (line) {
        lines.splice(close, 0, line);
      }
      return lines.join("\n");
    };
    const apply = () => app.vault.process(file, setParent);
    if (tp.hooks && tp.hooks.on_all_templates_executed) {
      tp.hooks.on_all_templates_executed(apply);
    } else {
      await apply();
    }
  }
}
tR = "";
-%>
