<%*
// Templater only: asks the title of a supervised task note and writes it as its `title` property, picks the project
// of the note, or none, and writes its `parent` line, which the supervisor's notes-scan resolves to the project at
// pickup (state_tool.py parent_project_id). Then it renames the note to its `uid` in the same folder (notes-scan finds
// notes by uid, so the name is free). Cancelling the title dialog keeps the title; cancelling the project picker
// changes nothing.
// Only projects in the same root as the note are listed: the root of a note is its folder without a trailing /YYYY/MM.
// Include it in the task note template with tp.file.include("[[sv-project]]"), or run it as a hotkey on a task note.
// setup overwrites this file; put a changed copy under another name.
const file = tp.config.target_file;
const fmOf = (f) => (app.metadataCache.getFileCache(f) || {}).frontmatter || {};
const own = fmOf(file);
const folderRoot = (f) => (f.parent && f.parent.path !== "/" ? f.parent.path : "").replace(/(^|\/)\d{4}\/\d{2}$/, "");
if (own.task_id) {
  new tp.obsidian.Notice("取り込み済みのタスクのプロジェクトは変えられません");
} else {
  const typed = await tp.system.prompt("タイトル", own.title || (/^Untitled( \d+)?$/.test(file.basename) ? "" : file.basename));
  const newTitle = typed && typed.trim();
  const projects = app.vault.getMarkdownFiles()
    .map((f) => ({ f, fm: fmOf(f) }))
    .filter(({ f, fm }) => f.path !== file.path && folderRoot(f) === folderRoot(file) && [].concat(fm.tags || []).includes("supervised")
      && [].concat(fm.tags || []).includes("project") && fm.project_id && !["done", "archived"].includes(fm.status))
    .sort((a, b) => String(a.fm.project_id).localeCompare(String(b.fm.project_id)));
  const none = { f: null };
  const choice = await tp.system.suggester(
    ["(プロジェクトなし)", ...projects.map(({ f, fm }) => `${fm.project_id} ${fm.title || f.basename}`)],
    [none, ...projects]);
  if (choice) {
    const projectTitle = choice.f && String(fmOf(choice.f).title || choice.f.basename).replace(/[|\]"]/g, " ");
    const parentLine = choice.f && `parent: "[[${choice.f.path.replace(/\.md$/, "")}|${projectTitle}]]"`;
    // Replaces the frontmatter line of key (with its indented continuation lines) by line, removes it when line is
    // empty, or adds it at the end of the frontmatter.
    const setProp = (text, key, line) => {
      const lines = text.split("\n");
      const close = lines[0] === "---" ? lines.indexOf("---", 1) : -1;
      if (close < 0) {
        return line ? ["---", line, "---", ...lines].join("\n") : text;
      }
      const at = lines.slice(1, close).findIndex((l) => l.startsWith(`${key}:`)) + 1;
      if (at > 0) {
        let end = at + 1;
        while (end < close && /^\s/.test(lines[end])) end += 1;
        lines.splice(at, end - at, ...(line ? [line] : []));
      } else if (line) {
        lines.splice(close, 0, line);
      }
      return lines.join("\n");
    };
    const renameToUid = async () => {
      const lines = (await app.vault.read(file)).split("\n");
      const close = lines[0] === "---" ? lines.indexOf("---", 1) : -1;
      const uidLine = close > 0 && lines.slice(1, close).find((l) => l.startsWith("uid:"));
      const uid = uidLine && uidLine.slice(4).trim().replace(/^(["'])(.*)\1$/, "$2");
      if (!uid || uid === file.basename || /[\\/:]/.test(uid)) return;
      const dest = (file.parent && file.parent.path !== "/" ? file.parent.path + "/" : "") + uid + ".md";
      if (app.vault.getAbstractFileByPath(dest)) {
        new tp.obsidian.Notice(`${dest} が既にあるので名前を変えません`);
      } else {
        await app.fileManager.renameFile(file, dest);
      }
    };
    const apply = async () => {
      await app.vault.process(file, (text) => {
        const withParent = setProp(text, "parent", parentLine);
        return newTitle ? setProp(withParent, "title", `title: ${JSON.stringify(newTitle)}`) : withParent;
      });
      await renameToUid();
    };
    if (tp.hooks && tp.hooks.on_all_templates_executed) {
      tp.hooks.on_all_templates_executed(apply);
    } else {
      await apply();
    }
  }
}
tR = "";
-%>
