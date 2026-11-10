#!/usr/bin/env python3
"""State tool for the session-supervisor skill.

Functional core: validate, next_state, render, digest, parse_report, cwd_busy, serialize,
parse_project_report, plan_actions, roster, waits, manager_reconcile,
guard_decision (bash_violations, path_violation), parse_follow_ups, new_pr_urls, follow_up_actions,
parse_locations, default_locations, add_missing_locations, clone_url_parts,
parse_frontmatter, set_props, new_note_text, parse_sections, parse_user_actions, ua_open, render_ua_line,
apply_note_ops, parse_artifacts, render_artifact_line, artifacts_view, art_sha,
join_view, split_view, note_props, user_transition, artifact_needs, note_values, journal_reflected, number_note_ops,
proposal_titles, migrate_v2, export_v1.
Imperative shell: the argparse subcommands at the bottom of this file.
"""

import argparse
import contextlib
import copy
import hashlib
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = 2
VIEW_SCHEMA_VERSION = 1
ARCHIVED_KEEP = 20
DIGEST_LIMIT = 10000
HERE = Path(__file__).resolve().parent
TEMPLATE_NAME = "dashboard.template.html"

KINDS = ("git", "non-git")
DELIVERIES = ("push_pr", "commit", "files")
INTENDED_STATES = ("pending", "running", "archived")
OBSERVED_STATES = ("none", "running", "idle", "needs_input", "archived", "missing")
MODELS = ("opus", "sonnet", "haiku")
SUPERVISOR_MODEL = "opus"
ACTIVE_OBSERVED = ("running", "idle", "needs_input", "missing")
TASK_STATUSES = ("needs_user", "running", "waiting", "done")
ARTIFACT_KINDS = ("branch", "commit", "pr", "ci", "file")
REPORT_STATUSES = ("done", "in_progress", "needs_input", "blocked", "failed", "awaiting_manager")
REVIEW_STATES = ("requested", "superseded", "rejected", "approved", "waived")
MANAGER_REPORT_STATUSES = ("idle", "needs_input")
HUB_WAIT_MINUTES = 30
REVIEW_WAIT_MINUTES = 120
PROJECT_ID_RE = re.compile(r"^P-(\d{4,})$")
REVIEW_ID_RE = re.compile(r"^R-(\d{4,})$")
PROJECT_REPORT_RE = re.compile(r"```project-report[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.S)
REPORT_FIELDS = ("task_id", "worker_id", "status", "summary", "artifacts", "blockers", "next_action")
REPORT_ARTIFACT_KEYS = ("branch", "commit", "pr", "ci")

FOOTER_RE = re.compile(r"<!--sv-footer-->.*?<!--/sv-footer-->", re.S)
REPORT_RE = re.compile(r"```supervisor-report[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.S)
UA_ID_RE = re.compile(r"^UA-(\d+)$")
HUB_WAIT_PREFIX = "Hub wait {wid} ("
REVIEW_WAIT_PREFIX = "Review wait {wid} ("
READ_ACTION_PREFIX = "Read the result of "
FINDING_KEYS = ("worker_id", "session_id", "title", "summary", "message_ref", "at")

FOLLOW_UP_HEADING = "## Follow-ups"
FOLLOW_UP_RULE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
FOLLOW_UP_KEYS = ("on", "text", "done")
FOLLOW_UP_ON = ("pr",)
FOLLOW_UP_DONE = ("pr_closed", "manual")
FOLLOW_UP_PLACEHOLDERS = ("{label}", "{url}")
FOLLOW_UP_TEXT_MAX = 200
PR_URL_RE = re.compile(r"^https?://[^/]+/([^/]+)/([^/]+)/pull/(\d+)/?$", re.I)

NOTE_STATUSES = ("backlog", "in progress", "pending", "done", "archived")
ASSIGNEES = ("user", "agent")
NOTE_KEYS = ("uid", "title", "para", "aliases", "created_at", "archived_at", "tags", "start_date", "due_date",
             "pending_until", "completed_at", "status", "assignee", "task_id", "project_id", "parent", "follows")
NOTE_RAW_KEYS = ("created_at", "archived_at")
NOTE_SECTIONS = ("概要", "ユーザーアクション", "サブタスク", "関連ノート", "関連資料", "成果物", "ログ")
UA_SECTION = "ユーザーアクション"
ART_SECTION = "成果物"
LOG_SECTION = "ログ"
UA_KINDS = ("ask", "auto", "slot")
UA_DETECTORS = ("worker_report", "worker_started", "archived_observed", "approval_cleared", "pr_closed",
                "review_state", "start_possible", "manager_report", "foreign_session_gone", "children_closed")
UA_META_KEYS = ("rule", "url", "since")
HOLD_REASONS = ("pause", "invalid")
SEEN_KEYS = ("status", "assignee", "ua_sha", "art_sha")
NOTE_MOVED_KEYS = {
    "tasks": ("title", "request", "status", "user_actions", "followups", "artifacts"),
    "projects": ("name", "context_doc_url", "user_actions"),
    "archived": ("title", "request", "followups", "artifacts"),
}
HELD_UA_PREFIX = "held-"
CONSENT_QUOTE_ERROR = "{prefix}: push_pr requires push_consent.text quoted from push_consent.message when present, else from the task request"
AUTO_CLOSE_REASON = "done/user で完了"
ART_KEYS = ("n", "g", "kind", "ref", "url", "path", "label", "r")
ART_FIELDS = ("kind", "ref", "url", "path", "label")
FM_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*):(?:[ \t]+(.*?))?[ \t]*$")
FM_ITEM_RE = re.compile(r"^[ \t]*-(?:[ \t]+(.*?))?[ \t]*$")
UA_LINE_RE = re.compile(r"^([ \t]*)[-*+] \[(.)\] (UA-\d+)\b(.*?)<!--[ \t]*sv[ \t]+(.*?)-->[ \t]*(?:\^([A-Za-z0-9-]+))?[ \t]*$")
ART_COMMENT_RE = re.compile(r"<!--[ \t]*sv-art[ \t]+(.*?)[ \t]*-->")

LOCATIONS_HEADING = "## Locations"
LOCATION_KEYS = ("clone_root", "work_root")
LOCATIONS_NOTE = ("`python3 .supervisor/state_tool.py prepare-dir` is the only command that writes outside this directory: "
                  "it clones repositories into `clone_root/<host>/<owner>/<repo>` and creates work directories "
                  "and project directories (with a guard hook) under `work_root`. "
                  "A missing key uses its default.")
CLONE_URL_RES = (
    re.compile(r"^https://([^/]+)/([^/]+)/([^/]+?)(?:\.git)?/?$"),
    re.compile(r"^git@([^:/]+):([^/]+)/([^/]+?)(?:\.git)?$"),
)
CLONE_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CLONE_TIMEOUT = 300
WORK_NAME_RE = re.compile(r"^T-\d{4,}-[a-z0-9-]{1,40}$")
PROJECT_NAME_RE = re.compile(r"^P-\d{4,}-[a-z0-9-]{1,40}$")

GUARD_MATCHER = "Edit|MultiEdit|Write|NotebookEdit|Bash"
GUARD_INPUT_KEYS = {"Edit": "file_path", "MultiEdit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path", "Bash": "command"}
GUARD_PROTECTED = (".claude", "state.json", ".supervisor/state_tool.py", ".supervisor/" + TEMPLATE_NAME)
GUARD_SEPARATOR_CHARS = frozenset(";&|()\n")
GUARD_SHELLS = ("sh", "bash", "zsh")
GUARD_WORD_RE = re.compile(r"(?<![\w./-])(git|jj|gh)(?![\w.-])")
GUARD_VALUE_OPTIONS = {
    "git": ("-C", "-c", "--git-dir", "--work-tree", "--namespace"),
    "jj": ("-R", "--repository", "--at-op", "--at-operation", "--color", "--config", "--config-file"),
    "gh": ("-R", "--repo"),
}
GIT_DENY = frozenset({
    "commit", "push", "pull", "fetch", "merge", "rebase", "reset", "add", "rm", "mv", "cherry-pick", "revert",
    "checkout", "switch", "restore", "clean", "apply", "am", "format-patch", "update-ref", "gc", "prune",
    "filter-branch", "init", "clone",
})
GIT_DENY_ACTIONS = {
    "worktree": frozenset({"add", "remove", "move", "prune", "repair", "lock", "unlock"}),
    "remote": frozenset({"add", "remove", "rm", "rename", "set-url", "set-head", "set-branches", "prune", "update"}),
    "reflog": frozenset({"expire", "delete"}),
}
GIT_ALLOW_ACTIONS = {
    "stash": frozenset({"list", "show"}),
    "notes": frozenset({"list", "show"}),
    "submodule": frozenset({"status", "summary"}),
}
GIT_TAG_FLAGS = ("-d", "-a", "-s", "-f", "-m")
GIT_BRANCH_FLAGS = ("-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy", "-f", "--force", "-u",
                    "--set-upstream-to", "--unset-upstream", "--edit-description")
GIT_BRANCH_LIST_FLAGS = ("--list", "-l", "--contains", "--merged", "--no-merged", "--points-at")
GIT_CONFIG_WRITE_FLAGS = ("--unset", "--unset-all", "--add", "--replace-all", "--rename-section", "--remove-section", "-e", "--edit")
JJ_DENY = frozenset({
    "commit", "describe", "desc", "new", "edit", "squash", "split", "abandon", "rebase", "restore", "resolve",
    "duplicate", "backout", "revert", "absorb", "diffedit", "fix", "sign", "unsign", "parallelize",
    "simplify-parents", "metaedit", "undo", "redo", "init", "git",
})
JJ_DENY_ACTIONS = {
    "file": frozenset({"chmod", "track", "untrack"}),
    "config": frozenset({"set", "unset", "edit"}),
}
JJ_ALLOW_ACTIONS = {
    "bookmark": frozenset({"list"}),
    "branch": frozenset({"list"}),
    "tag": frozenset({"list"}),
    "op": frozenset({"log", "show", "diff"}),
    "workspace": frozenset({"list", "root"}),
}
GH_DENY_ACTIONS = {
    "pr": frozenset({"create", "merge", "edit", "close", "reopen", "comment", "review", "ready", "lock", "unlock", "checkout", "update-branch"}),
    "issue": frozenset({"create", "edit", "close", "reopen", "comment", "delete", "transfer", "pin", "unpin", "lock", "unlock", "develop"}),
    "repo": frozenset({"create", "edit", "delete", "fork", "clone", "rename", "archive", "unarchive", "sync", "set-default"}),
    "release": frozenset({"create", "edit", "delete", "upload", "delete-asset"}),
    "label": frozenset({"create", "edit", "delete", "clone"}),
    "secret": frozenset({"set", "delete"}),
    "variable": frozenset({"set", "delete"}),
    "workflow": frozenset({"run", "enable", "disable"}),
    "run": frozenset({"rerun", "cancel", "delete"}),
    "gist": frozenset({"create", "edit", "delete"}),
}
GH_API_FIELD_FLAGS = ("-f", "-F", "--field", "--raw-field", "--input")


class StateError(Exception):
    def __init__(self, errors):
        self.errors = errors if isinstance(errors, list) else [errors]
        super().__init__("; ".join(self.errors))


# ---------------------------------------------------------------- functional core


def serialize(state):
    return json.dumps(state, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def empty_state(supervisor_dir):
    return {
        "schema_version": SCHEMA_VERSION,
        "rev": 0,
        "updated_at": None,
        "supervisor": {
            "dir": supervisor_dir,
            "session_ids": [],
            "archive_consent": None,
            "dashboard": {"url": None, "path": os.path.join(supervisor_dir, "dashboard.html")},
            "last_project_number": 0,
            "last_ua_number": 0,
            "note_warnings": {},
            "note_journal": None,
        },
        "tasks": [],
        "workers": [],
        "archived": [],
        "projects": [],
    }


def _real(path):
    return os.path.realpath(os.path.expanduser(path))


def _is_http_url(value):
    return isinstance(value, str) and re.match(r"^https?://", value, re.I) is not None


def _under(path, root):
    path, root = _real(path), _real(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _finished(worker):
    # A vanished session cannot be archived, so a missing worker the supervisor gave up on counts as finished.
    return worker.get("intended_state") == "archived" and worker.get("observed_state") in ("archived", "missing")


def _occupies(worker):
    # A worker holds its session (and a non-git worker its cwd) from start until its
    # archive is observed, so an archive request that was refused still counts.
    intended = worker.get("intended_state")
    return intended == "running" or (intended == "archived" and not _finished(worker))


def _occupies_cwd(worker):
    return worker.get("kind") == "non-git" and _occupies(worker)


def _cwds_overlap(a, b):
    return _under(a, b) or _under(b, a)


def _projects(state):
    projects = state.get("projects")
    return projects if isinstance(projects, list) else []


def _project_dirs(state):
    return [p["dir"] for p in _projects(state) if isinstance(p, dict) and isinstance(p.get("dir"), str)]


def cwd_busy(state, cwd):
    # A project directory belongs to its manager session for the project's whole life.
    if any(_cwds_overlap(d, cwd) for d in _project_dirs(state)):
        return True
    return any(_occupies_cwd(w) and _cwds_overlap(w["cwd"], cwd) for w in state.get("workers", []))


def _ua_ids(state):
    """Every user action id; task and project user actions share one numbering."""
    owners = list(state.get("tasks", [])) + _projects(state)
    return [ua.get("id") for o in owners if isinstance(o, dict) and isinstance(o.get("user_actions", []), list)
            for ua in o.get("user_actions", []) if isinstance(ua, dict)]


def _next_ua_number(state):
    """Numbers are never reused: the counter covers user actions already acked in the notes."""
    used = [int(m.group(1)) for i in _ua_ids(state) if isinstance(i, str) and (m := UA_ID_RE.match(i))]
    return max(used + [(state.get("supervisor") or {}).get("last_ua_number", 0)]) + 1


def _id_number(regex, value):
    m = regex.match(value) if isinstance(value, str) else None
    return int(m.group(1)) if m else None


def _shell_tokens(command):
    lex = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def _segments(tokens):
    # shlex groups adjacent punctuation (`&&\n`, `;;`), so any token made only of separator characters separates.
    segments, current = [], []
    for tok in tokens:
        if tok and set(tok) <= GUARD_SEPARATOR_CHARS:
            segments.append(current)
            current = []
        else:
            current.append(tok)
    segments.append(current)
    return [s for s in segments if s]


def _strip_redirections(segment):
    words, i = [], 0
    while i < len(segment):
        tok = segment[i]
        if tok and set(tok) <= set("<>&|") and ("<" in tok or ">" in tok):
            if words and words[-1].isdigit():
                words.pop()
            i += 2
            continue
        words.append(tok)
        i += 1
    return words


def _positionals(args):
    return [a for a in args if not a.startswith("-")]


def _has_flag(args, flags):
    return any(a.split("=", 1)[0] in flags if a.startswith("--") else a in flags for a in args)


def _action_violation(sub, first, deny_actions, allow_actions):
    if first in deny_actions.get(sub, ()) or (sub in allow_actions and first not in allow_actions[sub]):
        return f"{sub} {first} (mutating command)" if first else f"{sub} (mutating command)"
    return None


def _git_config_writes(args, pos):
    if pos and pos[0] in ("get", "list"):
        return False
    if pos and pos[0] in ("set", "unset", "rename-section", "remove-section", "edit"):
        return True
    return _has_flag(args, GIT_CONFIG_WRITE_FLAGS) or len(pos) >= 2


def _git_violation(sub, args):
    pos = _positionals(args)
    if any(a == "--output" or a.startswith("--output=") for a in args):
        return f"{sub} --output (writes a file)"
    if sub in GIT_DENY:
        return f"{sub} (mutating command)"
    found = _action_violation(sub, pos[0] if pos else None, GIT_DENY_ACTIONS, GIT_ALLOW_ACTIONS)
    if found:
        return found
    if sub == "tag" and (pos or _has_flag(args, GIT_TAG_FLAGS)):
        return "tag (creates or deletes a tag)"
    if sub == "branch" and (_has_flag(args, GIT_BRANCH_FLAGS) or (pos and not _has_flag(args, GIT_BRANCH_LIST_FLAGS))):
        return "branch (creates, renames or deletes a branch)"
    if sub == "config" and _git_config_writes(args, pos):
        return "config (writes configuration)"
    if sub == "symbolic-ref" and (_has_flag(args, ("-d", "--delete")) or len(pos) >= 2):
        return "symbolic-ref (changes a ref)"
    return None


def _jj_violation(sub, args):
    if sub in JJ_DENY:
        return f"{sub} (mutating command)"
    pos = _positionals(args)
    return _action_violation(sub, pos[0] if pos else None, JJ_DENY_ACTIONS, JJ_ALLOW_ACTIONS)


def _gh_violation(sub, args):
    if sub == "api":
        method = next((args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-X", "--method")), None)
        if method is not None:
            return None if method == "GET" else f"api -X {method} (non-GET request)"
        return "api (fields without -X GET make gh send POST)" if _has_flag(args, GH_API_FIELD_FLAGS) else None
    pos = _positionals(args)
    return _action_violation(sub, pos[0] if pos else None, GH_DENY_ACTIONS, {})


GUARD_CHECKS = {"git": _git_violation, "jj": _jj_violation, "gh": _gh_violation}


def _split_subcommand(tool, args):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GUARD_VALUE_OPTIONS[tool] else 1
    return (args[i], args[i + 1:]) if i < len(args) else (None, [])


def _tool_violations(words):
    for i, tok in enumerate(words):
        tool = os.path.basename(tok)
        if tool in GUARD_CHECKS:
            sub, args = _split_subcommand(tool, words[i + 1:])
            found = GUARD_CHECKS[tool](sub, args) if sub else None
            return [f"{tool} {found}"] if found else []
    return []


def _shell_c_violations(words):
    for i, tok in enumerate(words):
        if os.path.basename(tok) not in GUARD_SHELLS:
            continue
        has_c = False
        for opt in words[i + 1:]:
            if not opt.startswith("-"):
                return bash_violations(opt) if has_c else []
            has_c = has_c or (not opt.startswith("--") and "c" in opt[1:])
        return []
    return []


def bash_violations(command):
    """Deny-list matches of git / jj / gh in a Bash command. A guardrail, not a security boundary."""
    command = command.replace("\\\n", "")
    try:
        tokens = _shell_tokens(command)
    except ValueError:
        if GUARD_WORD_RE.search(command):
            return ["unparsable command (could not parse; quote the command correctly or split it)"]
        return []
    violations = []
    for segment in _segments(tokens):
        text = " ".join(segment)
        if ("`" in text or "$(" in text) and GUARD_WORD_RE.search(text):
            violations.append("command substitution (split the command; command substitution around git/jj/gh is not inspected)")
            continue
        words = _strip_redirections(segment)
        violations += _shell_c_violations(words) + _tool_violations(words)
    return violations


def path_violation(path, cwd, supervisor_dir, config_dir, tmp_dirs):
    full = _real(os.path.join(_real(cwd), os.path.expanduser(path)))
    if _under(full, os.path.join(supervisor_dir, "vault")):
        return f"{full} (vault notes are written only by state_tool commit)"
    if _under(full, supervisor_dir):
        if any(_under(full, os.path.join(supervisor_dir, rel)) for rel in GUARD_PROTECTED):
            return f"{full} (written only by setup or commit)"
        return None
    if _under(full, config_dir):
        parts = Path(os.path.relpath(full, _real(config_dir))).parts
        if len(parts) >= 4 and parts[0] == "projects" and parts[2] == "memory":
            return None
        return f"{full} (inside Claude's configuration directory)"
    if any(_under(full, d) for d in tmp_dirs):
        return None
    return f"{full} (outside the supervisor directory)"


GUARD_TAILS = {
    None: "The supervisor does not change repositories: send it to a worker session, or ask an observer subagent for read-only facts.",
    "manager": "The project manager only reads: answer or forward through send_message, or return a plan in your project-report.",
}


def guard_decision(payload, supervisor_dir, env, role=None):
    """Return the block message for a PreToolUse payload, or None to allow it."""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    key = GUARD_INPUT_KEYS.get(tool)
    value = tool_input.get(key) if key and isinstance(tool_input, dict) else None
    if not isinstance(value, str) or not value:
        return None
    if tool == "Bash":
        violations = bash_violations(value)
    else:
        config_dir = env.get("CLAUDE_CONFIG_DIR") or "~/.claude"
        tmp_dirs = [d for d in (env.get("TMPDIR"), "/tmp") if d]
        found = path_violation(value, payload.get("cwd") or supervisor_dir, supervisor_dir, config_dir, tmp_dirs)
        violations = [f"{tool} {found}"] if found else []
    if not violations:
        return None
    return f"[session-supervisor guard] blocked: {'; '.join(violations)}. {GUARD_TAILS[role]}"


def _archived_externally(worker):
    return worker.get("observed_state") == "archived" and worker.get("intended_state") != "archived"


def derive_status(task, workers_by_id):
    workers = [workers_by_id[wid] for wid in task.get("worker_ids", []) if wid in workers_by_id]
    if task.get("cancel") and not any(_occupies(w) for w in workers):
        return "done"
    # An archive the supervisor did not request must be verified before the task can finish.
    if task.get("user_actions") or any(_archived_externally(w) for w in workers):
        return "needs_user"
    observed = ["archived" if _finished(w) else w.get("observed_state") for w in workers]
    if any(o in ACTIVE_OBSERVED for o in observed):
        return "running"
    if any(o == "none" for o in observed):
        return "waiting"
    # Workers given up as missing deliver nothing, and a replaced worker was never verified;
    # without one verified archive the task needs a replacement or a cancel. An investigation
    # worker that went missing while held has its verified findings, so it counts as finished.
    finding_ids = {f.get("worker_id") for f in task.get("findings", [])}
    if not any((w.get("observed_state") == "archived"
                or (w.get("observed_state") == "missing" and w.get("intended_state") == "archived" and w["id"] in finding_ids))
               and not w.get("replaced_by") for w in workers):
        return "needs_user"
    return "done"


def _validate_artifact(prefix, artifact, cwds):
    errors = []
    kind = artifact.get("kind")
    if kind not in ARTIFACT_KINDS:
        return [f"{prefix}: unknown kind {kind!r}"]
    url = artifact.get("url")
    if kind in ("pr", "ci") and not _is_http_url(url):
        errors.append(f"{prefix}: {kind} requires an http(s) url")
    if kind in ("branch", "commit"):
        if not artifact.get("ref"):
            errors.append(f"{prefix}: {kind} requires ref")
        if url is not None and not _is_http_url(url):
            errors.append(f"{prefix}: url must be http(s)")
    if kind == "file":
        path = artifact.get("path")
        if "url" in artifact:
            errors.append(f"{prefix}: file must not have url")
        if not isinstance(path, str) or not os.path.isabs(path):
            errors.append(f"{prefix}: file requires an absolute path")
        elif cwds is not None and not any(_under(path, c) for c in cwds):
            errors.append(f"{prefix}: file path is outside the worker cwd")
    return errors


def _validate_last_report(prefix, report):
    if report is None:
        return []
    if not isinstance(report, dict):
        return [f"{prefix}: last_report must be an object or null"]
    errors = []
    if not report.get("message_ref"):
        errors.append(f"{prefix}: last_report requires message_ref")
    status = report.get("status")
    if status == "unparsed":
        for key in ("error", "at"):
            if not report.get(key):
                errors.append(f"{prefix}: unparsed last_report requires {key}")
    elif status in REPORT_STATUSES:
        for key in ("summary", "next_action"):
            if not isinstance(report.get(key), str):
                errors.append(f"{prefix}: last_report requires {key}")
    else:
        errors.append(f"{prefix}: last_report has invalid status {status!r}")
    errors += [f"{prefix}: last_report {e}" for e in _read_confirmed_errors(report)]
    return errors


def _read_confirmed_errors(report):
    if "read_confirmed" not in report:
        return []
    if not isinstance(report["read_confirmed"], bool):
        return ["read_confirmed must be a boolean"]
    if report["read_confirmed"] and report.get("status") != "done":
        return ["read_confirmed is allowed only with status done"]
    return []


def _read_action_worker(ua):
    """The worker id a read-the-result user action names, or None for any other user action."""
    description = ua.get("description")
    if ua.get("follow_up") is not None or not isinstance(description, str) or not description.startswith(READ_ACTION_PREFIX + "w-"):
        return None
    return description[len(READ_ACTION_PREFIX):].split(" (", 1)[0]


def _held_ids(task):
    return {wid for wid in map(_read_action_worker, task.get("user_actions", [])) if wid}


def _no_result_ids(task):
    return {n["worker_id"] for n in task.get("no_result", [])}


def _delivery_artifacts_ok(delivery, artifacts):
    kinds = [a.get("kind") for a in artifacts]
    if delivery == "files":
        return "file" in kinds
    if "branch" not in kinds or "commit" not in kinds:
        return False
    if delivery == "push_pr":
        return "pr" in kinds or any(a.get("kind") == "branch" and a.get("url") for a in artifacts)
    return True


def _shape_errors(state):
    """Type checks that let the remaining validation index fields safely."""
    errors = []
    sup = state["supervisor"]
    for key in ("archive_consent", "dashboard"):
        if sup.get(key) is not None and not isinstance(sup.get(key), dict):
            errors.append(f"supervisor.{key} must be an object or null")
    for key in ("tasks", "workers", "archived"):
        for i, item in enumerate(state[key]):
            if not isinstance(item, dict):
                errors.append(f"{key}[{i}] must be an object")
            elif not isinstance(item.get("id"), str):
                errors.append(f"{key}[{i}].id must be a string")
    if errors:
        return errors
    nested = [(f"task {t['id']}", t, ("user_actions", "artifacts")) for t in state["tasks"]]
    nested += [(f"archived {s['id']}", s, ("artifacts",)) for s in state["archived"]]
    for prefix, owner, keys in nested:
        for key in keys:
            items = owner.get(key, [])
            if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
                errors.append(f"{prefix}: {key} must be a list of objects")
        followups = owner.get("followups", [])
        if not isinstance(followups, list) or not all(
                isinstance(f, dict) and all(isinstance(f.get(k), str) and f.get(k) for k in ("text", "at")) for f in followups):
            errors.append(f"{prefix}: followups must be a list of {{text, at}} with non-empty strings")
        no_result = owner.get("no_result", [])
        if not isinstance(no_result, list) or not all(
                isinstance(n, dict) and all(isinstance(n.get(k), str) and n.get(k) for k in ("worker_id", "summary", "at")) for n in no_result):
            errors.append(f"{prefix}: no_result must be a list of {{worker_id, summary, at}} with non-empty strings")
        findings = owner.get("findings", [])
        if not isinstance(findings, list) or not all(
                isinstance(f, dict) and all(isinstance(f.get(k), str) and f.get(k) for k in FINDING_KEYS) for f in findings):
            errors.append(f"{prefix}: findings must be a list of {{{', '.join(FINDING_KEYS)}}} with non-empty strings")
        if "investigation" in owner and owner["investigation"] is not True:
            errors.append(f"{prefix}: investigation must be true when present")
    for s in state["archived"]:
        if s.get("request") is not None and not isinstance(s["request"], str):
            errors.append(f"archived {s['id']}: request must be a string")
    for t in state["tasks"]:
        if not isinstance(t.get("worker_ids", []), list) or not all(isinstance(i, str) for i in t.get("worker_ids", [])):
            errors.append(f"task {t['id']}: worker_ids must be a list of strings")
        consent = t.get("push_consent")
        if consent is not None and not isinstance(consent, dict):
            errors.append(f"task {t['id']}: push_consent must be an object or null")
        elif consent is not None and "message" in consent and not (isinstance(consent["message"], str) and consent["message"]):
            errors.append(f"task {t['id']}: push_consent.message must be a non-empty string when present")
        cancel = t.get("cancel")
        if cancel is not None and not (isinstance(cancel, dict) and all(isinstance(cancel.get(k), str) and cancel.get(k) for k in ("at", "reason"))):
            errors.append(f"task {t['id']}: cancel must be null or {{at, reason}} with non-empty strings")
        derived = t.get("derived_from")
        if derived is not None and not (isinstance(derived, dict) and all(_str(derived.get(k)) for k in ("task_id", "context"))):
            errors.append(f"task {t['id']}: derived_from must be {{task_id, context}} with non-empty strings")
    for w in state["workers"]:
        for key in ("task_id", "cwd"):
            if not isinstance(w.get(key), str):
                errors.append(f"worker {w['id']}: {key} must be a string")
        if w.get("replaced_by") is not None and not isinstance(w["replaced_by"], str):
            errors.append(f"worker {w['id']}: replaced_by must be a string")
        if w.get("model") is not None and w["model"] not in MODELS:
            errors.append(f"worker {w['id']}: model must be one of {', '.join(MODELS)}")
        continues = w.get("continues", 0)
        if not isinstance(continues, int) or isinstance(continues, bool) or continues < 0:
            errors.append(f"worker {w['id']}: continues must be a non-negative integer")
    errors += _project_shape_errors(state)
    if not errors:
        if not all(isinstance(i, str) for i in _ua_ids(state)):
            errors.append("user_action ids must be strings")
        for t in state["tasks"]:
            for ua in t.get("user_actions", []):
                if ua.get("follow_up") is not None and not isinstance(ua["follow_up"], dict):
                    errors.append(f"task {t['id']}: user_action {ua.get('id')} follow_up must be an object or absent")
                if "proposal" in ua:
                    errors.append(f"task {t['id']}: user_action {ua.get('id')} proposal belongs to a project user action")
    return errors


def _str(value):
    return isinstance(value, str) and bool(value)


def _nonneg_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _fields_ok(obj, keys):
    return isinstance(obj, dict) and all(_str(obj.get(k)) for k in keys)


def _redos_ok(redos):
    return isinstance(redos, list) and all(
        _fields_ok(r, ("text", "at", "from")) and (r.get("review_id") is None or _str(r.get("review_id"))) for r in redos)


def _review_shape_errors(prefix, review):
    if not isinstance(review, dict):
        return [f"{prefix}: review must be an object"]
    errors = []
    state = review.get("state")
    if _id_number(REVIEW_ID_RE, review.get("id")) is None:
        errors.append(f"{prefix}: review.id must be R-NNNN")
    if state not in REVIEW_STATES:
        errors.append(f"{prefix}: review.state must be one of {list(REVIEW_STATES)}")
    if not _str(review.get("at")):
        errors.append(f"{prefix}: review.at must be a non-empty string")
    if state in ("requested", "superseded", "rejected", "approved") and not _str(review.get("message_ref")):
        errors.append(f"{prefix}: {state} review requires message_ref")
    if state == "waived" and not _str(review.get("reason")):
        errors.append(f"{prefix}: waived review requires reason")
    for key in ("message_ref", "ref", "reason", "stale_notified"):
        if review.get(key) is not None and not isinstance(review[key], str):
            errors.append(f"{prefix}: review.{key} must be a string")
    pending = review.get("pending_verdict")
    if pending is not None:
        if not (_fields_ok(pending, ("message_ref", "at")) and pending.get("verdict") == "approve"):
            errors.append(f"{prefix}: review.pending_verdict must be {{verdict: approve, message_ref, at}}")
        elif state != "requested" or pending["message_ref"] != review.get("message_ref"):
            errors.append(f"{prefix}: review.pending_verdict is allowed only on a requested review with the same message_ref")
    return errors


def _manager_shape_errors(prefix, m):
    errors = []
    for key in ("session_id", "started_by", "handle", "observed_at"):
        if m.get(key) is not None and not isinstance(m[key], str):
            errors.append(f"{prefix}: manager.{key} must be a string or null")
    if not _str(m.get("title")):
        errors.append(f"{prefix}: manager.title must be a non-empty string")
    if m.get("intended_state") not in INTENDED_STATES:
        errors.append(f"{prefix}: manager has invalid intended_state {m.get('intended_state')!r}")
    if m.get("observed_state") not in OBSERVED_STATES:
        errors.append(f"{prefix}: manager has invalid observed_state {m.get('observed_state')!r}")
    if m.get("model") is not None and m["model"] not in MODELS:
        errors.append(f"{prefix}: manager.model must be one of {', '.join(MODELS)}")
    prev = m.get("previous_session_ids", [])
    if not isinstance(prev, list) or not all(_str(i) for i in prev):
        errors.append(f"{prefix}: manager.previous_session_ids must be a list of strings")
    if not _nonneg_int(m.get("replacements", 0)):
        errors.append(f"{prefix}: manager.replacements must be a non-negative integer")
    report = m.get("last_report")
    if report is not None:
        status = report.get("status") if isinstance(report, dict) else None
        if status == "unparsed":
            ok = _fields_ok(report, ("message_ref", "error", "at"))
        else:
            ok = (status in MANAGER_REPORT_STATUSES and _fields_ok(report, ("message_ref", "at"))
                  and isinstance(report.get("summary"), str))
        if not ok:
            errors.append(f"{prefix}: manager.last_report must be null, {{message_ref, status (idle | needs_input), summary, at}} "
                          "or {message_ref, status: unparsed, error, at}")
    return errors


def _project_shape_errors(state):
    errors = []
    if not _nonneg_int(state["supervisor"].get("last_project_number", 0)):
        errors.append("supervisor.last_project_number must be a non-negative integer")
    projects = state.get("projects", [])
    if not isinstance(projects, list):
        return errors + ["projects must be a list"]
    for i, p in enumerate(projects):
        if not isinstance(p, dict) or _id_number(PROJECT_ID_RE, p.get("id")) is None:
            errors.append(f"projects[{i}] must be an object with an id P-NNNN")
            continue
        prefix = f"project {p['id']}"
        if not _str(p.get("name")):
            errors.append(f"{prefix}: name must be a non-empty string")
        uas = p.get("user_actions", [])
        # A project delegated from a note without a context document waits for the user's answer (ask answer).
        asking = isinstance(uas, list) and any(isinstance(ua, dict) and ua.get("needs_answer") for ua in uas)
        if not _is_http_url(p.get("context_doc_url")) and not (p.get("context_doc_url") is None and asking):
            errors.append(f"{prefix}: context_doc_url must be http(s)")
        if not isinstance(p.get("dir"), str) or not os.path.isabs(p["dir"]):
            errors.append(f"{prefix}: dir must be an absolute path")
        if not _str(p.get("created_at")):
            errors.append(f"{prefix}: created_at must be a non-empty string")
        if not _nonneg_int(p.get("last_review_number")):
            errors.append(f"{prefix}: last_review_number must be a non-negative integer")
        if p.get("close") is not None and not _fields_ok(p["close"], ("at", "reason")):
            errors.append(f"{prefix}: close must be null or {{at, reason}} with non-empty strings")
        if not isinstance(uas, list) or not all(isinstance(ua, dict) for ua in uas):
            errors.append(f"{prefix}: user_actions must be a list of objects")
        else:
            for ua in uas:
                if not _str(ua.get("id")) or not _str(ua.get("description")):
                    errors.append(f"{prefix}: user_action requires id and a description string")
                if "proposal" in ua and not _fields_ok(ua["proposal"], ("title", "text", "message_ref")):
                    errors.append(f"{prefix}: user_action {ua.get('id')} proposal must be {{title, text, message_ref}} with non-empty strings")
                if "follow_up" in ua:
                    errors.append(f"{prefix}: user_action {ua.get('id')} follow_up belongs to a task user action")
        if not isinstance(p.get("manager"), dict):
            errors.append(f"{prefix}: manager must be an object")
        else:
            errors += _manager_shape_errors(prefix, p["manager"])
    owners = [(f"task {t['id']}", t) for t in state["tasks"]] + [(f"archived {s['id']}", s) for s in state["archived"]]
    for prefix, owner in owners:
        if owner.get("project_id") is not None and not isinstance(owner["project_id"], str):
            errors.append(f"{prefix}: project_id must be a string")
        if "proposal" in owner and not _fields_ok(owner["proposal"], ("title", "text", "from", "message_ref")):
            errors.append(f"{prefix}: proposal must be {{title, text, from, message_ref}} with non-empty strings")
        if "redos" in owner and not _redos_ok(owner["redos"]):
            errors.append(f"{prefix}: redos must be a list of {{text, at, from, review_id?}} with non-empty strings")
    for s in state["archived"]:
        if "project" in s and not _fields_ok(s["project"], ("id", "name")):
            errors.append(f"archived {s['id']}: project must be {{id, name}}")
    for w in state["workers"]:
        if "review" in w:
            errors += _review_shape_errors(f"worker {w['id']}", w["review"])
        if w.get("awaiting_since") is not None and not _str(w["awaiting_since"]):
            errors.append(f"worker {w['id']}: awaiting_since must be a non-empty string")
    return errors


def _follow_up_errors(prefix, task, relieved=()):
    errors, seen = [], set()
    pr_urls = set(_pr_urls(task))
    for ua in task.get("user_actions", []):
        fu = ua.get("follow_up")
        if fu is None:
            continue
        where = f"{prefix}: user_action {ua.get('id')} follow_up"
        rule, url, done = fu.get("rule"), fu.get("url"), fu.get("done")
        if not isinstance(rule, str) or not FOLLOW_UP_RULE_RE.match(rule):
            errors.append(f"{where}.rule must be a rule id (lowercase letters, digits and '-', up to 32 characters)")
        if done not in FOLLOW_UP_DONE:
            errors.append(f"{where}.done must be one of {list(FOLLOW_UP_DONE)}")
        if not _is_http_url(url):
            errors.append(f"{where}.url must be http(s)")
        elif url not in pr_urls and ua.get("id") not in relieved:
            errors.append(f"{where}.url {url} is not a pr artifact of the task (remove the follow-up when its pr artifact is replaced)")
        if not isinstance(ua.get("since"), str) or not ua["since"]:
            errors.append(f"{where} requires a non-empty since")
        key = (rule, url)
        if isinstance(rule, str) and isinstance(url, str) and key in seen:
            errors.append(f"{where} duplicates rule {rule!r} for {url}")
        seen.add(key)
    return errors


def validate(state, relief=None):
    """Check the single-state invariants of a v1 view. Returns a list of error strings.
    relief ({workers, user_actions}) skips the delivery and follow-up url checks of a quarantined task (3.9)."""
    if not isinstance(state, dict):
        return ["state must be an object"]
    relief = relief or {"workers": set(), "user_actions": set()}
    errors = []
    if state.get("schema_version") != VIEW_SCHEMA_VERSION:
        errors.append(f"schema_version must be {VIEW_SCHEMA_VERSION}")
    if not isinstance(state.get("rev"), int) or state.get("rev") < 0:
        errors.append("rev must be a non-negative integer")
    for key in ("tasks", "workers", "archived"):
        if not isinstance(state.get(key), list):
            errors.append(f"{key} must be a list")
    sup = state.get("supervisor")
    if not isinstance(sup, dict) or not isinstance(sup.get("session_ids"), list):
        errors.append("supervisor.session_ids must be a list")
    if errors:
        return errors
    errors = _shape_errors(state)
    if errors:
        return errors

    dashboard_url = (sup.get("dashboard") or {}).get("url")
    if dashboard_url is not None and not _is_http_url(dashboard_url):
        errors.append("supervisor.dashboard.url must be http(s)")

    tasks, workers = state["tasks"], state["workers"]
    task_ids = [t.get("id") for t in tasks]
    worker_ids = [w.get("id") for w in workers]
    ua_ids = _ua_ids(state)
    for label, ids in (("task", task_ids), ("worker", worker_ids), ("user_action", ua_ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1}, key=str)
        if dupes:
            errors.append(f"duplicate {label} ids: {dupes}")
    tasks_by_id = {t.get("id"): t for t in tasks}
    workers_by_id = {w.get("id"): w for w in workers}
    stubs_by_id = {s["id"]: s for s in state["archived"]}

    for task in tasks:
        tid = task.get("id")
        prefix = f"task {tid}"
        if task.get("status") is not None and task.get("status") not in TASK_STATUSES:
            errors.append(f"{prefix}: invalid status {task.get('status')!r}")
        # A task waiting for the user's answer to where it should run has no worker yet.
        if not task.get("worker_ids") and not any(ua.get("needs_answer") for ua in task.get("user_actions", [])):
            errors.append(f"{prefix}: worker_ids must not be empty")
        for wid in task.get("worker_ids", []):
            w = workers_by_id.get(wid)
            if w is None or w.get("task_id") != tid:
                errors.append(f"{prefix}: worker {wid} does not point back to the task")
        for ua in task.get("user_actions", []):
            if not ua.get("id") or not isinstance(ua.get("description"), str) or not ua["description"]:
                errors.append(f"{prefix}: user_action requires id and a description string")
        errors += _follow_up_errors(prefix, task, relief["user_actions"])
        if task.get("cancel"):
            for wid in task.get("worker_ids", []):
                if workers_by_id.get(wid, {}).get("intended_state") == "running":
                    errors.append(f"{prefix}: cancelled but worker {wid} is still intended running")
        # A no_result entry exempts its worker from delivery artifacts, so it must stay tied to that finished or held worker.
        held = _held_ids(task)
        seen = set()
        for entry in task.get("no_result", []):
            wid = entry["worker_id"]
            w = workers_by_id.get(wid)
            if (wid in seen or w is None or w.get("task_id") != tid
                    or (w.get("intended_state") != "archived" and wid not in held) or w.get("replaced_by")):
                errors.append(f"{prefix}: no_result {wid} must name an archived, non-replaced worker of the task once")
            seen.add(wid)
        seen = set()
        for entry in task.get("findings", []):
            wid = entry["worker_id"]
            w = workers_by_id.get(wid)
            if (wid in seen or not task.get("investigation") or w is None or w.get("task_id") != tid
                    or w.get("session_id") != entry["session_id"]):
                errors.append(f"{prefix}: findings {wid} must name a worker of an investigation task by its session_id, once")
            seen.add(wid)
        # A held worker stays running only while the user still has to read its verified result.
        finding_ids = {f["worker_id"] for f in task.get("findings", [])}
        seen = set()
        for wid in filter(None, map(_read_action_worker, task.get("user_actions", []))):
            w = workers_by_id.get(wid) or {}
            review_ok = not task.get("project_id") or (w.get("review") or {}).get("state") in ("approved", "waived")
            if (wid in seen or not task.get("investigation") or w.get("task_id") != tid
                    or w.get("intended_state") != "running" or w.get("observed_state") in ("archived", "missing")
                    or wid not in finding_ids or not review_ok):
                errors.append(f"{prefix}: read action for {wid} needs a live, approved or waived (project) worker "
                              "of an investigation task with findings, once")
            seen.add(wid)
        cwds = [w["cwd"] for w in workers if w.get("task_id") == tid and w.get("cwd")]
        for i, artifact in enumerate(task.get("artifacts", [])):
            errors += _validate_artifact(f"{prefix} artifact[{i}]", artifact, cwds)
        stub = stubs_by_id.get(tid)
        if stub is not None and (stub.get("cancelled") or stub.get("uid") != task.get("uid")):
            errors.append(f"{prefix}: id is an archived stub; revive only a non-cancelled stub with the same uid")

    occupants = []
    for w in workers:
        wid = w.get("id")
        prefix = f"worker {wid}"
        task = tasks_by_id.get(w.get("task_id"))
        if task is None or wid not in task.get("worker_ids", []):
            errors.append(f"{prefix}: task {w.get('task_id')} does not list it")
        for key, allowed in (
            ("kind", KINDS),
            ("delivery", DELIVERIES),
            ("intended_state", INTENDED_STATES),
            ("observed_state", OBSERVED_STATES),
        ):
            if w.get(key) not in allowed:
                errors.append(f"{prefix}: invalid {key} {w.get(key)!r}")
        if not w.get("cwd") or not os.path.isabs(os.path.expanduser(w["cwd"])):
            errors.append(f"{prefix}: cwd must be an absolute path")
            continue
        delivery = w.get("delivery")
        if (delivery == "files") != (w.get("kind") == "non-git"):
            errors.append(f"{prefix}: delivery {delivery!r} does not match kind {w.get('kind')!r}")
        if delivery == "push_pr" and task is not None:
            consent = task.get("push_consent") or {}
            text = consent.get("text") or ""
            # Quoting from the request is checked only when the consent changes (_consent_request_errors):
            # the request comes from the note, which the user may edit later.
            if not text or ("message" in consent and text not in (consent["message"] or "")):
                errors.append(CONSENT_QUOTE_ERROR.format(prefix=prefix))
        # A vanished session produces no deliverables, so giving it up requires none; nor does handing a worker to its replacement.
        exempt = (task is None or task.get("cancel") or w.get("observed_state") == "missing" or w.get("replaced_by")
                  or wid in _no_result_ids(task))
        if w.get("intended_state") == "archived" and not exempt and wid not in relief["workers"]:
            if not _delivery_artifacts_ok(delivery, task.get("artifacts", [])):
                errors.append(f"{prefix}: archive requested but task lacks {delivery} artifacts")
        errors += _validate_last_report(prefix, w.get("last_report"))
        if _occupies_cwd(w):
            occupants.append((_real(w["cwd"]), wid))
    for i, (cwd_a, wid_a) in enumerate(occupants):
        for cwd_b, wid_b in occupants[i + 1:]:
            if _cwds_overlap(cwd_a, cwd_b):
                where = cwd_a if cwd_a == cwd_b else f"{cwd_a} / {cwd_b} (nested)"
                errors.append(f"non-git cwd {where} is held by more than one worker: {[wid_a, wid_b]}")
    for w in workers:
        target = w.get("replaced_by")
        if target is None:
            continue
        prefix = f"worker {w.get('id')}"
        successor = workers_by_id.get(target)
        if successor is None or target == w.get("id") or successor.get("task_id") != w.get("task_id"):
            errors.append(f"{prefix}: replaced_by {target} must name another worker of the same task")
        if w.get("intended_state") != "archived":
            errors.append(f"{prefix}: replaced_by requires intended_state archived")
        seen, cur = {w.get("id")}, successor
        while cur is not None and cur.get("replaced_by") is not None:
            if cur.get("id") in seen:
                errors.append(f"{prefix}: replaced_by chain loops")
                break
            seen.add(cur.get("id"))
            cur = workers_by_id.get(cur["replaced_by"])

    for stub in state["archived"]:
        for i, artifact in enumerate(stub.get("artifacts", [])):
            errors += _validate_artifact(f"archived {stub.get('id')} artifact[{i}]", artifact, None)
    errors += _project_errors(state, tasks_by_id)
    return errors


def _project_errors(state, tasks_by_id):
    errors = []
    projects = _projects(state)
    projects_by_id = {p["id"]: p for p in projects}
    last = state["supervisor"].get("last_project_number", 0)
    ids = [p["id"] for p in projects]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        errors.append(f"duplicate project ids: {dupes}")
    for p in projects:
        if _id_number(PROJECT_ID_RE, p["id"]) > last:
            errors.append(f"project {p['id']}: number exceeds supervisor.last_project_number {last}")
        if p.get("close") and p["manager"].get("intended_state") != "archived":
            errors.append(f"project {p['id']}: a closed project's manager must be intended archived")
    for t in state["tasks"]:
        pid = t.get("project_id")
        if pid is not None and (pid not in projects_by_id or projects_by_id[pid].get("close")):
            errors.append(f"task {t['id']}: project_id {pid} must name an open project")
        if "proposal" in t and t["proposal"].get("from") != pid:
            errors.append(f"task {t['id']}: proposal.from must equal project_id")
        if t.get("redos") and pid is None:
            errors.append(f"task {t['id']}: redos belong to a project task")

    seen_reviews = {}
    for w in state["workers"]:
        prefix = f"worker {w['id']}"
        task = tasks_by_id.get(w.get("task_id")) or {}
        pid = task.get("project_id")
        review = w.get("review")
        if review is not None:
            if pid is None:
                errors.append(f"{prefix}: review belongs to a project task worker")
            elif pid in projects_by_id:
                key = (pid, review["id"])
                if key in seen_reviews:
                    errors.append(f"{prefix}: review id {review['id']} is also used by {seen_reviews[key]} in {pid}")
                seen_reviews[key] = w["id"]
                limit = projects_by_id[pid].get("last_review_number", 0)
                if _id_number(REVIEW_ID_RE, review["id"]) > limit:
                    errors.append(f"{prefix}: review id {review['id']} exceeds last_review_number {limit} of {pid}")
        # The approval gate is a state invariant, so a candidate that records a newer report while
        # keeping an approved worker intended archived is refused until the archive is rolled back.
        report = w.get("last_report") if isinstance(w.get("last_report"), dict) else None
        if (pid is not None and w.get("intended_state") == "archived" and not w.get("replaced_by")
                and not task.get("cancel")):
            state_ = (review or {}).get("state")
            ok = state_ == "waived" or (state_ == "approved" and (
                report is None or report.get("message_ref") == review.get("message_ref")))
            if not ok:
                errors.append(f"{prefix}: archived project worker needs an approved review of its latest report "
                              "(or waived / cancel / replaced_by)")
        awaiting = (report or {}).get("status") == "awaiting_manager"
        if awaiting and pid is None:
            errors.append(f"{prefix}: awaiting_manager is reported only by project task workers")
        if awaiting != (w.get("awaiting_since") is not None):
            errors.append(f"{prefix}: awaiting_since is required exactly while last_report.status is awaiting_manager")
        if w.get("kind") == "non-git" and isinstance(w.get("cwd"), str):
            for p in projects:
                if isinstance(p.get("dir"), str) and _cwds_overlap(w["cwd"], p["dir"]):
                    errors.append(f"{prefix}: cwd overlaps the directory of project {p['id']}")
    return errors


def _is_push_decision(user_action, worker):
    if user_action.get("follow_up") is not None:
        return False
    desc = user_action.get("description", "")
    branch = worker.get("branch")
    return "push" in desc.lower() and (not isinstance(branch, str) or not branch or branch in desc)


def _pushed_at_head(task, worker):
    """True when the worker's last group is recorded as pushed: its commit artifact URL names its own sha."""
    arts = task.get("artifacts", [])
    ref = f"{worker.get('cwd')}#{worker.get('branch')}"
    idx = [i for i, a in enumerate(arts) if a.get("kind") == "branch" and a.get("ref") == ref]
    if not idx or idx[-1] + 1 >= len(arts):
        return False
    nxt = arts[idx[-1] + 1]
    url = nxt.get("url")
    return nxt.get("kind") == "commit" and isinstance(url, str) and bool(nxt.get("ref")) and url.endswith(f"/commit/{nxt['ref']}")


def _deletion_errors(current, candidate):
    errors = []
    cand_tasks = {t["id"] for t in candidate["tasks"]}
    for task in current["tasks"]:
        if task["id"] not in cand_tasks:
            errors.append(f"task {task['id']} was removed; tasks leave only by being archived by commit")
    cand_workers = {w["id"] for w in candidate["workers"]}
    for w in current["workers"]:
        if w.get("session_id") and w["id"] not in cand_workers:
            errors.append(f"worker {w['id']} has a session_id and was removed")
    cand_projects = {p["id"]: p for p in _projects(candidate)}
    for p in _projects(current):
        if p["id"] not in cand_projects:
            errors.append(f"project {p['id']} was removed; projects leave only by being closed")
        elif cand_projects[p["id"]].get("last_review_number", 0) < p.get("last_review_number", 0):
            errors.append(f"project {p['id']}: last_review_number must not decrease")
    if candidate["supervisor"].get("last_project_number", 0) < current["supervisor"].get("last_project_number", 0):
        errors.append("supervisor.last_project_number must not decrease")
    return errors


def _transition_errors(current, candidate):
    errors = []
    cur_workers = {w["id"]: w for w in current["workers"]}
    tasks = {t["id"]: t for t in candidate["tasks"]}
    for w in candidate["workers"]:
        if w["intended_state"] != "archived":
            continue
        cur = cur_workers.get(w["id"], {})
        if cur.get("intended_state") == "archived":
            # Already finished with its result recorded (or cancelled): never replaced afterwards.
            if not cur.get("replaced_by") and w.get("replaced_by"):
                errors.append(f"worker {w['id']}: replaced_by cannot be added to a worker already intended archived")
            continue
        # Consent covers archives the supervisor performs; an archived or vanished session needs none.
        if w["observed_state"] not in ("archived", "missing") and not candidate["supervisor"].get("archive_consent"):
            errors.append(f"worker {w['id']}: archive requested without supervisor.archive_consent")
        task = tasks.get(w["task_id"], {})
        if w["delivery"] == "commit" and not task.get("cancel") and w["observed_state"] != "missing" and not w.get("replaced_by") and w["id"] not in _no_result_ids(task) and not _pushed_at_head(task, w) and not any(_is_push_decision(ua, w) for ua in task.get("user_actions", [])):
            errors.append(f"worker {w['id']}: commit delivery archive requires a push-decision user_action")
    return errors + _project_transition_errors(current, candidate)


def _project_transition_errors(current, candidate):
    """Numbers are issued once: a new P-id or R-id must be above the counter of the current state."""
    errors = []
    cur_projects = {p["id"]: p for p in _projects(current)}
    cur_last = current["supervisor"].get("last_project_number", 0)
    for p in _projects(candidate):
        cur = cur_projects.get(p["id"])
        if cur is None and _id_number(PROJECT_ID_RE, p["id"]) <= cur_last:
            errors.append(f"project {p['id']}: a new project id must be above last_project_number {cur_last}")
        manager, cur_manager = p["manager"], (cur or {}).get("manager", {})
        if (manager.get("intended_state") == "archived" and cur_manager.get("intended_state") != "archived"
                and not p.get("close") and manager.get("observed_state") not in ("archived", "missing")):
            errors.append(f"project {p['id']}: the manager is archived only when the project is closed or the session is gone")
    tasks = {t["id"]: t for t in candidate["tasks"]}
    for t in current["tasks"]:
        if t["id"] in tasks and tasks[t["id"]].get("project_id") != t.get("project_id"):
            errors.append(f"task {t['id']}: project_id must not change")
    cur_workers = {w["id"]: w for w in current["workers"]}
    for w in candidate["workers"]:
        rid = (w.get("review") or {}).get("id")
        if rid is None or rid == (cur_workers.get(w["id"], {}).get("review") or {}).get("id"):
            continue
        pid = tasks.get(w["task_id"], {}).get("project_id")
        limit = cur_projects.get(pid, {}).get("last_review_number", 0)
        if _id_number(REVIEW_ID_RE, rid) <= limit:
            errors.append(f"worker {w['id']}: a new review id must be above last_review_number {limit} of {pid}")
    return errors


def _consent_request_errors(current, candidate):
    """A consent without message must quote the request at the time it is given or changed."""
    errors = []
    cur_tasks = {t["id"]: t for t in current["tasks"]}
    for w in candidate["workers"]:
        task = next((t for t in candidate["tasks"] if t["id"] == w["task_id"]), {})
        consent = task.get("push_consent") or {}
        if (w.get("delivery") != "push_pr" or "message" in consent or not consent.get("text")
                or consent == (cur_tasks.get(task.get("id"), {}).get("push_consent") or {})):
            continue
        if consent["text"] not in (task.get("request") or ""):
            errors.append(CONSENT_QUOTE_ERROR.format(prefix=f"worker {w['id']}"))
    return errors


def _consent_message_errors(current, candidate):
    # A consent given after intake is accepted only while it resolves a pending push decision.
    errors = []
    cur_tasks = {t["id"]: t for t in current["tasks"]}
    cur_workers = {w["id"]: w for w in current["workers"]}
    for task in candidate["tasks"]:
        message = (task.get("push_consent") or {}).get("message")
        cur = cur_tasks.get(task["id"], {})
        if message is None or message == (cur.get("push_consent") or {}).get("message"):
            continue
        git_workers = [cur_workers[wid] for wid in cur.get("worker_ids", []) if cur_workers.get(wid, {}).get("kind") == "git"]
        pending = {ua["id"] for ua in cur.get("user_actions", []) if any(_is_push_decision(ua, w) for w in git_workers)}
        if not pending - {ua["id"] for ua in task.get("user_actions", [])}:
            errors.append(f"task {task['id']}: push_consent.message may be set only while resolving a pending push decision")
    return errors


def waiting_start_description(task_id):
    return f"Waiting task {task_id} can start now: send any message to the supervisor to start it."


def _startable_workers(state, task, workers_by_id):
    # A task the user paused or marked invalid, or whose artifacts are quarantined, starts nothing.
    if task.get("cancel") or task.get("user_hold") or task.get("artifacts_quarantine"):
        return []
    # A replacement is started on the next user message anyway, so it needs no user action.
    replacements = {w.get("replaced_by") for w in state["workers"]}
    return [
        w for w in (workers_by_id.get(wid) for wid in task["worker_ids"])
        if w and w["kind"] == "non-git" and w["intended_state"] == "pending" and w["observed_state"] == "none"
        and w["id"] not in replacements and not cwd_busy(state, w["cwd"])
    ]


def _sync_waiting_start_actions(current, state, now):
    # Event-driven turns cannot start sessions, so when a non-git cwd frees up the
    # waiting task is surfaced as a user action and started on the next user message.
    # The action is dropped as soon as no worker of the task is startable any more
    # (it was started, removed, or its cwd is held again), so it never pins needs_user.
    next_num = _next_ua_number(state)
    workers_by_id = {w["id"]: w for w in state["workers"]}
    for task in state["tasks"]:
        desc = waiting_start_description(task["id"])
        startable = _startable_workers(state, task, workers_by_id)
        actions = task.setdefault("user_actions", [])
        if not startable:
            task["user_actions"] = [ua for ua in actions if ua["description"] != desc]
            continue
        freed = any(cwd_busy(current, w["cwd"]) for w in startable)
        if freed and not any(ua["description"] == desc for ua in actions):
            actions.append({"id": f"UA-{next_num:04d}", "description": desc, "since": now})
            next_num += 1


def next_state(current, candidate, now):
    """Return the state to write for `candidate`, or raise StateError."""
    if not isinstance(candidate, dict) or candidate.get("rev") != current.get("rev"):
        raise StateError(f"candidate rev {candidate.get('rev') if isinstance(candidate, dict) else None!r} does not match current rev {current.get('rev')!r}")
    state = copy.deepcopy(candidate)
    relief = _quarantine_relief(current, state)
    errors = validate(state, relief)
    if errors:
        raise StateError(errors)
    errors = (_deletion_errors(current, state) + _transition_errors(current, state) + _consent_request_errors(current, state)
              + _consent_message_errors(current, state))
    if errors:
        raise StateError(errors)

    _sync_waiting_start_actions(current, state, now)
    workers_by_id = {w["id"]: w for w in state["workers"]}
    for task in state["tasks"]:
        task["status"] = derive_status(task, workers_by_id)

    done = [t for t in state["tasks"] if t["status"] == "done"]
    done_ids = {t["id"] for t in done}
    old_stubs = {s["id"]: s for s in state["archived"]}
    projects_by_id = {p["id"]: p for p in _projects(state)}
    stubs = []
    for t in done:
        # A revived task replaces its old stub; keeping the order makes the last group the newest generation.
        artifacts = old_stubs.get(t["id"], {}).get("artifacts", []) + t.get("artifacts", [])
        stub = {"id": t["id"], "title": t.get("title"), "request": t.get("request"), "artifacts": artifacts, "archived_at": now}
        if t.get("followups"):
            stub["followups"] = t["followups"]
        no_result = old_stubs.get(t["id"], {}).get("no_result", []) + t.get("no_result", [])
        if no_result:
            stub["no_result"] = no_result
        findings = old_stubs.get(t["id"], {}).get("findings", []) + t.get("findings", [])
        if findings:
            stub["findings"] = findings
        if t.get("investigation"):
            stub["investigation"] = True
        if t.get("cancel"):
            stub.update(cancelled=True, reason=t["cancel"]["reason"])
        project = projects_by_id.get(t.get("project_id"))
        if project is not None:
            stub["project"] = {"id": project["id"], "name": project["name"]}
        for key in ("proposal", "redos", "uid", "last_artifact_number"):
            if t.get(key):
                stub[key] = t[key]
        stubs.append(stub)
    state["archived"] = (list(reversed(stubs)) + [s for s in state["archived"] if s["id"] not in done_ids])[:ARCHIVED_KEEP]
    state["tasks"] = [t for t in state["tasks"] if t["id"] not in done_ids]
    state["workers"] = [w for w in state["workers"] if w["task_id"] not in done_ids]
    if "projects" in state:
        # The P-id stays used through last_project_number, so dropping a finished project never frees it.
        referenced = {t.get("project_id") for t in state["tasks"]}
        state["projects"] = [p for p in state["projects"]
                             if not (p.get("close") and _finished(p["manager"]) and p["id"] not in referenced)]
        _auto_close_projects(state, referenced, now)
        # Re-check what commit itself changed: waiting-start actions and automatic closes.
        errors = validate(state, relief)
        if errors:
            raise StateError(errors)

    state["rev"] = current["rev"] + 1
    state["updated_at"] = now
    return state


def _quarantine_relief(current, candidate):
    """What validate skips for quarantined tasks: the delivery check of workers already intended archived in current,
    and the url check of follow-up user actions current already has. New archives are checked in full."""
    ids = {t["id"] for t in candidate.get("tasks", []) if t.get("artifacts_quarantine")}
    workers = {w["id"] for w in current.get("workers", []) if w.get("task_id") in ids and w.get("intended_state") == "archived"}
    actions = {ua.get("id") for t in current.get("tasks", []) if t.get("id") in ids
               for ua in t.get("user_actions", []) if ua.get("follow_up") is not None}
    return {"workers": workers, "user_actions": actions}


def _auto_close_projects(state, referenced, now):
    """A project the user confirmed as done closes once no live task belongs to it (D-8)."""
    for p in state["projects"]:
        if p.get("confirmed") and not p.get("close") and p["id"] not in referenced:
            p["close"] = {"at": now, "reason": AUTO_CLOSE_REASON}
            p["manager"]["intended_state"] = "archived"
            p["user_actions"] = [ua for ua in p.get("user_actions", []) if ua.get("detector") != "children_closed"]


def _esc(value):
    # Braces are escaped too so state strings can never form a {{SLOT}} of the template.
    return html.escape("" if value is None else str(value)).replace("{", "&#123;").replace("}", "&#125;")


_MD_CODE = re.compile(r"(`[^`\n]+`)")
# A bare URL takes ASCII URL characters only, so Japanese text right after it stays outside,
# and never ends in punctuation that usually belongs to the sentence.
_MD_LINK_OR_URL = re.compile(
    r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)"
    r"|(https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()+,;=%]*[A-Za-z0-9\-_~/#=&%+])")
_MD_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_MD_ITEM = re.compile(r"^\s*(?:([-*])|\d+[.)])\s+(.*)$")


def _a(url, text):
    # The dashboard runs inside the artifact frame, where most sites refuse to load; open links in a new tab.
    return f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer">{_esc(text)}</a>'


def _md_links(text):
    out, pos = [], 0
    for m in _MD_LINK_OR_URL.finditer(text):
        out.append(_esc(text[pos:m.start()]))
        out.append(_a(m.group(2), m.group(1)) if m.group(2) else _a(m.group(3), m.group(3)))
        pos = m.end()
    out.append(_esc(text[pos:]))
    return "".join(out)


def _md_inline(text):
    out = []
    for part in _MD_CODE.split(text):
        if len(part) > 2 and part.startswith("`") and part.endswith("`"):
            out.append(f"<code>{_esc(part[1:-1])}</code>")
            continue
        out.append(_MD_BOLD.sub(r"<strong>\1</strong>", _md_links(part)))
    return "".join(out)


def _md(value):
    """Render a markdown subset (paragraphs, lists, `code`, **bold**, [text](http-url), bare http(s) URLs) as escaped HTML."""
    blocks, para, items, ordered = [], [], [], False

    def flush():
        nonlocal para, items
        if para:
            blocks.append(f'<p>{"<br>".join(_md_inline(line) for line in para)}</p>')
        if items:
            tag = "ol" if ordered else "ul"
            blocks.append(f'<{tag}>{"".join(f"<li>{_md_inline(i)}</li>" for i in items)}</{tag}>')
        para, items = [], []

    for line in ("" if value is None else str(value)).splitlines():
        item = _MD_ITEM.match(line)
        if not line.strip():
            flush()
        elif item:
            if para or (items and ordered != (item.group(1) is None)):
                flush()
            ordered = item.group(1) is None
            items.append(item.group(2))
        elif items:
            items[-1] += " " + line.strip()
        else:
            para.append(line.strip())
    flush()
    return "".join(blocks)


def _chip(state):
    return f'<span class="chip" data-state="{_esc(state)}">{_esc(state)}</span>'


def _link_or_text(url, text):
    if _is_http_url(url):
        return _a(url, text)
    return f"<code>{_esc(text)}</code>"


def _time(iso, cls="rel"):
    # HH:MM is the static fallback; the page script swaps in a relative time and keeps the ISO value as a tooltip.
    if not iso:
        return ""
    clock = iso[11:16] if len(iso) >= 16 else iso
    return f'<time class="{cls}" datetime="{_esc(iso)}" title="{_esc(iso)}">{_esc(clock)}</time>'


def _render_artifact(a):
    kind = a.get("kind")
    label = a.get("label") or a.get("ref") or a.get("path") or a.get("url")
    if kind == "file":
        return f"{_esc(kind)}: <code>{_esc(a.get('path'))}</code>"
    return f"{_esc(kind)}: {_link_or_text(a.get('url'), label)}"


_ARTIFACT_KIND_LABELS = {"pr": "PR", "ci": "CI"}


def _render_artifact_list(artifacts):
    # Collapsed by default; the summary keeps a per-kind tally visible.
    if not artifacts:
        return ""
    counts = {}
    for a in artifacts:
        counts[a.get("kind")] = counts.get(a.get("kind"), 0) + 1
    tally = " · ".join(f"{n} {_ARTIFACT_KIND_LABELS.get(k, k)}" for k, n in counts.items())
    items = "".join(f'<li data-kind="{_esc(a.get("kind"))}">{_render_artifact(a)}</li>' for a in artifacts)
    return (f'<details class="artifacts-fold"><summary><span class="label">Artifacts</span>'
            f'<span class="artifacts-fold__tally">{_esc(tally)}</span></summary>'
            f'<ul class="artifacts">{items}</ul></details>')


def _in_review(worker):
    return (worker.get("review") or {}).get("state") in ("requested", "superseded", "rejected")


def _task_attention(task, workers):
    """Everything in one task waiting on the user: its user actions, needs_input / missing workers, and workers archived outside the supervisor."""
    items = [("user", ua) for ua in task.get("user_actions", [])]
    for w in workers:
        # A worker under review keeps its review even when its session is gone, so it does not read as unchecked.
        if _in_review(w) and (w.get("observed_state") == "missing" or _archived_externally(w)):
            items.append(("review", w))
        elif w.get("observed_state") in ("missing", "needs_input"):
            items.append(("worker", w))
        elif _archived_externally(w):
            items.append(("external", w))
    return items


def _render_attention_row(kind, obj):
    if kind == "user":
        url = (obj.get("follow_up") or {}).get("url")
        open_link = ""
        if _is_http_url(url) and url not in (obj.get("description") or ""):
            open_link = f'<span>{_a(url, "open ↗")}</span>'
        return (f'<li class="act" data-kind="user"><span class="act__kind">Action</span>'
                f'<div class="act__text md">{_md(obj.get("description"))}</div>'
                f'<p class="act__meta"><span class="id">{_esc(obj.get("id"))}</span>'
                f'<span>since {_time(obj.get("since"))}</span>{open_link}</p></li>')
    if kind == "review":
        text = f'is waiting for review {_esc(obj["review"]["id"])} of the project manager'
    elif kind == "worker":
        text = f'is {_chip(obj["observed_state"])}'
    else:
        text = "was archived outside the supervisor; its results need checking"
    return (f'<li class="act" data-kind="{kind}"><span class="act__kind">Worker</span>'
            f'<p class="act__text"><span class="id">{_esc(obj["id"])}</span> {_esc(obj.get("title"))} {text} '
            f'<code>{_esc(obj.get("cwd"))}</code></p></li>')


def _segment_state(worker):
    if _finished(worker):
        return "done"
    # Archived outside the supervisor still needs a look, so it must not read as an empty slot.
    return "external" if _archived_externally(worker) else worker.get("observed_state")


def _render_report(r, awaiting_since=None):
    if r is None:
        return '<p class="wk__report" data-report="none">No report yet</p>'
    if r.get("status") == "unparsed":
        return f'<p class="wk__report" data-report="unparsed"><span class="wk__rs">unparsed</span> {_esc(r.get("error"))}</p>'
    since = f' <span class="wk__since">since {_time(awaiting_since)}</span>' if r.get("status") == "awaiting_manager" else ""
    return (f'<p class="wk__report" data-report="{_esc(r.get("status"))}"><span class="wk__rs">{_esc(r.get("status"))}</span> '
            f'{_esc(r.get("summary"))}{since}</p>')


def _render_finding(f, with_summary):
    sid = _esc(f["session_id"])
    summary = f'<p class="finding__summary">{_esc(f["summary"])}</p>' if with_summary else ""
    return (f'<div class="finding"><p class="finding__link"><span class="label">Result</span> '
            f'<a href="#local_{sid}">{_esc(f["title"])}</a> <code>{sid}</code></p>{summary}</div>')


def _copy_id(cls, text):
    # Click-to-copy control whose text is the ID itself (see the click handler in the template).
    return (f'<button type="button" class="{cls} copyable" data-copy aria-label="Copy {_esc(text)}" '
            f'title="クリックでコピー">{_esc(text)}</button>')


_REVIEW_CHIPS = {"requested": "review", "rejected": "rejected", "superseded": "superseded", "waived": "review waived"}


def _render_worker(w):
    seg = _segment_state(w)
    label = _REVIEW_CHIPS.get((w.get("review") or {}).get("state"))
    review = f' {_chip(label)} <span class="id">{_esc(w["review"]["id"])}</span>' if label else ""
    branch = f' · <code>{_esc(w.get("branch"))}</code>' if w.get("branch") else ""
    model = f' · {_esc(w.get("model"))}' if w.get("model") else ""
    return (
        f'<li class="wk" data-state="{_esc(seg)}"><span class="dot" data-state="{_esc(seg)}" aria-hidden="true"></span>'
        f'<div class="wk__body"><p class="wk__title"><span class="id">{_esc(w.get("id"))}</span> {_esc(w.get("title"))} {_chip(w.get("observed_state"))}{review}</p>'
        f'<p class="wk__meta"><code>{_esc(w.get("cwd"))}</code>{branch} · {_esc(w.get("delivery"))}{model}</p>'
        f'{_render_report(w.get("last_report"), w.get("awaiting_since"))}</div></li>')


def _render_tasks(state):
    tasks = state["tasks"]
    workers_by_id = {w["id"]: w for w in state["workers"]}
    projects = _projects(state)
    if projects:
        groups = [_render_project(p, [t for t in tasks if t.get("project_id") == p["id"]], workers_by_id) for p in projects]
        rest = [t for t in tasks if not t.get("project_id")]
        groups.append(f'<section class="project" id="project-none" data-project="none"><header class="project__head">'
                      f'<h3 class="project__title">Outside projects</h3></header>{_task_list(rest, workers_by_id)}</section>')
        body = f'<div class="project-list">{"".join(groups)}</div>'
    else:
        body = _task_list(tasks, workers_by_id)
    return (f'<section class="tasks" id="tasks" aria-labelledby="tasks-label"><h2 class="section-label" id="tasks-label">'
            f'Tasks <span class="count">{len(tasks)}</span></h2>{body}</section>')


def _render_index(state):
    """Jump links to each project group; empty without projects, so such dashboards keep their header."""
    projects = _projects(state)
    if not projects:
        return ""
    tasks = state["tasks"]
    links = [(f'project-{p["id"]}', f'{p["id"]} {p["name"]}', sum(1 for t in tasks if t.get("project_id") == p["id"]))
             for p in projects]
    links.append(("project-none", "Outside projects", sum(1 for t in tasks if not t.get("project_id"))))
    items = "".join(f'<a href="#{_esc(anchor)}" data-jump>{_esc(text)} <span class="count">{n}</span></a>'
                    for anchor, text, n in links)
    return f'<nav class="pindex" aria-label="Projects">{items}</nav>'


def _render_project(project, tasks, workers_by_id):
    m = project["manager"]
    sid = m.get("session_id")
    title = f'{project["id"]} {project["name"]}'
    manager = (f'manager <a href="#local_{_esc(sid)}">{_esc(title)}</a> <code>{_esc(sid)}</code>' if sid
               else f'manager {_esc(title)} <span class="id">not started</span>')
    closed = f' {_chip("closed")}' if project.get("close") else ""
    attention = ""
    if project.get("user_actions"):
        rows = "".join(_render_attention_row("user", ua) for ua in project["user_actions"])
        attention = (f'<div class="task__attention"><span class="label">Needs you</span>'
                     f'<ul class="task__attention-list">{rows}</ul></div>')
    return (f'<section class="project" id="project-{_esc(project["id"])}" data-project="{_esc(project["id"])}"><header class="project__head">'
            f'{_copy_id("id project__id", project["id"])}<h3 class="project__title">{_esc(project["name"])}</h3>'
            f'{_chip(m.get("observed_state"))}{closed}</header>'
            f'<p class="project__meta">{manager} · {_a(project["context_doc_url"], "context doc ↗")}</p>'
            f'{attention}{_task_list(tasks, workers_by_id)}</section>')


def _task_list(tasks, workers_by_id):
    if not tasks:
        cards = '<p class="empty">No active tasks.</p>'
    else:
        entries = []
        for task in tasks:
            workers = [workers_by_id[wid] for wid in task.get("worker_ids", []) if wid in workers_by_id]
            entries.append((task, workers, _task_attention(task, workers)))
        # Tasks waiting on the user come first; the sort is stable, so state.json order holds within each group.
        entries.sort(key=lambda e: not (e[2] or e[0].get("status") == "needs_user"))
        out = []
        for i, (task, workers, attention) in enumerate(entries):
            done = sum(1 for w in workers if _finished(w))
            segs = "".join(f'<span class="seg" data-state="{_esc(_segment_state(w))}"></span>' for w in workers)
            if attention:
                rows = "".join(_render_attention_row(kind, obj) for kind, obj in attention)
                next_or_attention = (f'<div class="task__attention"><span class="label">Needs you</span>'
                                     f'<ul class="task__attention-list">{rows}</ul></div>')
            else:
                next_or_attention = f'<p class="task__next"><span class="label">Next</span> {_esc(task.get("next_action"))}</p>'
            # The worker row already shows its latest summary; repeat the recorded conclusion only when it differs.
            summaries = {w["id"]: (w.get("last_report") or {}).get("summary") for w in workers}
            findings = "".join(_render_finding(f, f["summary"] != summaries.get(f["worker_id"]))
                               for f in task.get("findings", []))
            cancel = ""
            if task.get("cancel"):
                cancel = f'<p class="task__cancel">{_chip("cancelled")} {_esc(task["cancel"].get("reason"))}</p>'
            out.append(
                f'<article class="task" data-status="{_esc(task.get("status"))}"{" data-cancelled" if task.get("cancel") else ""} style="--i:{i}">'
                f'<header class="task__head">{_copy_id("id task__id", task["id"])}'
                f'<h3 class="task__title">{_esc(task.get("title"))}</h3>{_chip(task.get("status"))}</header>'
                f'{cancel}'
                f'<div class="task__progress"><div class="task__segments" role="img" aria-label="{done} of {len(workers)} workers finished">{segs}</div>'
                f'<p class="task__progress-label"><b>{done}</b>/{len(workers)} done</p>'
                f'<p class="task__elapsed">started {_time(task.get("created_at"))}</p></div>'
                f'{next_or_attention}'
                f'<ul class="task__workers">{"".join(_render_worker(w) for w in workers)}</ul>'
                f'{findings}{_render_artifact_list(task.get("artifacts", []))}</article>')
        cards = "".join(out)
    return f'<div class="task-list">{cards}</div>'


def _render_archived(state):
    stubs = state["archived"]
    if not stubs:
        rows = '<p class="empty">No archived tasks.</p>'
    else:
        items = []
        for stub in stubs:
            cancelled = ""
            if stub.get("cancelled"):
                cancelled = f' {_chip("cancelled")} <span class="arc__reason">{_esc(stub.get("reason"))}</span>'
            no_result = stub.get("no_result", [])
            chip = f' {_chip("no result")}' if no_result else ""
            findings = stub.get("findings", [])
            finding_ids = {f["worker_id"] for f in findings}
            notes = "".join(f'<p class="arc__note"><span class="id">{_esc(n["worker_id"])}</span> {_esc(n["summary"])}</p>'
                            for n in no_result if n["worker_id"] not in finding_ids)
            notes += "".join(_render_finding(f, True) for f in findings)
            project = f'<span class="id">{_esc(stub["project"]["id"])}</span> ' if stub.get("project") else ""
            items.append(
                f'<li class="arc"{" data-cancelled" if stub.get("cancelled") else ""}><p class="arc__title">{_copy_id("id", stub["id"])} {project}'
                f'{_esc(stub.get("title"))}{cancelled}{chip}</p><p class="arc__meta">archived {_time(stub.get("archived_at"))}</p>'
                f'{notes}{_render_artifact_list(stub.get("artifacts", []))}</li>')
        rows = f'<ul class="arc-list">{"".join(items)}</ul>'
    return (f'<section class="archived" id="archived" aria-labelledby="archived-label"><h2 class="section-label" id="archived-label">'
            f'Archived <span class="count">{len(stubs)}</span></h2>{rows}</section>')


def render(state, template):
    """Return (body, footer). body keeps the {{FOOTER}} slot so it can be hashed without rev/updated_at."""
    body = (
        template.replace("{{INDEX}}", _render_index(state))
        .replace("{{TASKS}}", _render_tasks(state))
        .replace("{{ARCHIVED}}", _render_archived(state))
    )
    rev = _esc(state.get("rev"))
    footer = (f'<!--sv-footer--><footer class="foot" data-rev="{rev}">rev {rev} · updated '
              f'{_time(state.get("updated_at"), "abs")}</footer><!--/sv-footer-->')

    return body, footer


def assemble(body, footer):
    return body.replace("{{FOOTER}}", footer)


def body_of(page):
    return FOOTER_RE.sub("{{FOOTER}}", page)


def _digest_follow_up(ua):
    fu = ua.get("follow_up")
    if not isinstance(fu, dict):
        return ""
    url = fu.get("url")
    tail = f" {url}" if url and url not in (ua.get("description") or "") else ""
    return f" [follow-up {fu.get('rule')} {fu.get('done')}]{tail}"


def digest(state, state_path, limit=DIGEST_LIMIT):
    sup = state.get("supervisor", {})
    dashboard = sup.get("dashboard") or {}
    workers_by_id = {w.get("id"): w for w in state.get("workers", [])}
    lines = [
        f"[session-supervisor] state rev {state.get('rev')} (updated {state.get('updated_at')})",
        "Before acting: use the session-supervisor skill, run its capability check, then reconcile workers with the live session list. "
        "If any worker is running, idle or needs_input, resume the periodic reconciliation loop.",
        f"state: {state_path}",
        f"dashboard html: {dashboard.get('path')}",
        f"dashboard url: {dashboard.get('url') or '(not published)'}",
        f"supervisor session_ids: {', '.join(sup.get('session_ids', [])) or '(none)'}",
        f"archive_consent: {'recorded' if sup.get('archive_consent') else 'missing (ask the user once)'}",
        f"archived tasks: {len(state.get('archived', []))}",
        "user actions:",
    ]
    projects = [p for p in _projects(state) if isinstance(p, dict)]
    actions = [f"- {p.get('id')} {ua.get('id')}: {ua.get('description')}" for p in projects for ua in p.get("user_actions", [])]
    actions += [f"- {t.get('id')} {ua.get('id')}: {ua.get('description')}{_digest_follow_up(ua)}"
                for t in state.get("tasks", []) for ua in t.get("user_actions", [])]
    lines += actions or ["- (none)"]
    holds = [f"- {o.get('id')} note {o.get('uid')} ({o.get('note')}): status/assignee outside the transition table; "
             "ask the user to set listed values" for o in projects + state.get("tasks", [])
             if isinstance(o, dict) and (o.get("user_hold") or {}).get("reason") == "invalid"]
    if holds:
        lines += ["notes on hold (invalid values):"] + holds
    if projects:
        lines.append("projects:")
        for p in projects:
            m = p.get("manager") or {}
            pid = p.get("id")
            mine = [t for t in state.get("tasks", []) if t.get("project_id") == pid]
            reviews = [(w.get("review") or {}).get("id") for w in state.get("workers", [])
                       if any(w.get("task_id") == t.get("id") for t in mine) and (w.get("review") or {}).get("state") == "requested"]
            closed = " | closed" if p.get("close") else ""
            lines.append(f"- {pid} {p.get('name')} | manager {m.get('session_id')} {m.get('observed_state')} | doc {p.get('context_doc_url')}"
                         f" | open reviews: {', '.join(reviews) or '(none)'} | tasks: {', '.join(t.get('id') for t in mine) or '(none)'}{closed}")
    lines.append("tasks:")
    task_lines = []
    for t in state.get("tasks", []):
        ws = "; ".join(
            f"{wid} {workers_by_id.get(wid, {}).get('session_id')} {workers_by_id.get(wid, {}).get('observed_state')}"
            for wid in t.get("worker_ids", [])
        )
        project = f"[{t['project_id']}] " if t.get("project_id") else ""
        task_lines.append(f"- {project}{t.get('id')} [{t.get('status')}] {t.get('title')} | next: {t.get('next_action')} | workers: {ws}")
    lines += task_lines or ["- (none)"]
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    marker = f"\n... (truncated; read {state_path})"
    return text[: limit - len(marker)] + marker


def parse_report(text, expect_task=None, expect_worker=None, allow_awaiting_manager=False):
    blocks = REPORT_RE.findall(text)
    if not blocks:
        return {"ok": False, "error": "no supervisor-report block found"}
    try:
        report = json.loads(blocks[-1])
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"invalid JSON in supervisor-report block: {e}"}
    if not isinstance(report, dict):
        return {"ok": False, "error": "supervisor-report must be a JSON object"}
    missing = [f for f in REPORT_FIELDS if f not in report]
    if missing:
        return {"ok": False, "error": f"missing fields: {missing}"}
    if (not isinstance(report["status"], str) or report["status"] not in REPORT_STATUSES
            or (report["status"] == "awaiting_manager" and not allow_awaiting_manager)):
        return {"ok": False, "error": f"invalid status {report['status']!r}"}
    for key in ("task_id", "worker_id", "summary", "next_action"):
        if not isinstance(report[key], str):
            return {"ok": False, "error": f"{key} must be a string"}
    if not isinstance(report["blockers"], list) or not all(isinstance(b, str) for b in report["blockers"]):
        return {"ok": False, "error": "blockers must be a list of strings"}
    artifacts = report["artifacts"]
    if not isinstance(artifacts, dict):
        return {"ok": False, "error": "artifacts must be an object"}
    for key in REPORT_ARTIFACT_KEYS:
        if not isinstance(artifacts.get(key, ""), str):
            return {"ok": False, "error": f"artifacts.{key} must be a string"}
    files = artifacts.get("files", [])
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        return {"ok": False, "error": "artifacts.files must be a list of strings"}
    if report["status"] in ("needs_input", "blocked", "awaiting_manager") and not report["blockers"]:
        return {"ok": False, "error": f"status {report['status']} requires at least one blocker"}
    confirmed = _read_confirmed_errors(report)
    if confirmed:
        return {"ok": False, "error": confirmed[0]}
    if expect_task is not None and report["task_id"] != expect_task:
        return {"ok": False, "error": f"task_id {report['task_id']!r} does not match {expect_task!r}"}
    if expect_worker is not None and report["worker_id"] != expect_worker:
        return {"ok": False, "error": f"worker_id {report['worker_id']!r} does not match {expect_worker!r}"}
    return {"ok": True, "report": report}


def _strip_ignored(lines):
    """Blank out fenced blocks and HTML comments, keeping line numbers."""
    out, fence, in_comment = [], None, False
    for line in lines:
        if fence is None and not in_comment:
            m = re.match(r"^\s*(`{3,}|~{3,})", line)
            if m:
                fence = m.group(1)
                out.append("")
                continue
        elif fence is not None:
            if line.strip().startswith(fence) and not line.strip()[len(fence):].strip():
                fence = None
            out.append("")
            continue
        kept, rest = "", line
        while rest:
            if in_comment:
                end = rest.find("-->")
                if end < 0:
                    rest = ""
                else:
                    rest, in_comment = rest[end + 3:], False
            else:
                start = rest.find("<!--")
                if start < 0:
                    kept, rest = kept + rest, ""
                else:
                    kept, rest, in_comment = kept + rest[:start], rest[start + 4:], True
        out.append(kept)
    return out


def _follow_up_value_problem(key, value):
    if key == "on" and value not in FOLLOW_UP_ON:
        return f"on must be one of {list(FOLLOW_UP_ON)}, got {value!r}"
    if key == "done" and value not in FOLLOW_UP_DONE:
        return f"done must be one of {list(FOLLOW_UP_DONE)}, got {value!r}"
    if key == "text":
        if not value:
            return "text must not be empty"
        if len(value) > FOLLOW_UP_TEXT_MAX:
            return f"text must be at most {FOLLOW_UP_TEXT_MAX} characters"
        unknown = [p for p in re.findall(r"\{[^{}]*\}", value) if p not in FOLLOW_UP_PLACEHOLDERS]
        if unknown:
            return f"text may use only {{label}} and {{url}}, got {unknown}"
    return None


def parse_follow_ups(text):
    """Parse the `## Follow-ups` section of CLAUDE.md. Returns (rules, errors); errors read `CLAUDE.md:<line>: <message>`.
    Any error makes the rule list empty."""
    lines = _strip_ignored(text.splitlines())
    heads = [i for i, line in enumerate(lines) if line.rstrip() == FOLLOW_UP_HEADING]
    if not heads:
        return [], []
    if len(heads) > 1:
        return [], [f"CLAUDE.md:{i + 1}: duplicate '{FOLLOW_UP_HEADING}' heading" for i in heads[1:]]
    errors, rules, current, seen = [], [], None, set()

    def err(n, message):
        errors.append(f"CLAUDE.md:{n}: {message}")

    def finish(rule):
        if rule is None or rule.get("_skip"):
            return
        for key in FOLLOW_UP_KEYS:
            if key not in rule["lines"]:
                err(rule["line"], f"rule {rule['id']!r} is missing '{key}'")
        rules.append({"id": rule["id"], **{k: rule.get(k) for k in FOLLOW_UP_KEYS}})

    for i in range(heads[0] + 1, len(lines)):
        n, line = i + 1, lines[i].rstrip()
        if re.match(r"^#{1,2} ", line):
            break
        if not line.strip():
            continue
        if line.startswith("### "):
            finish(current)
            rid = line[4:].strip()
            current = {"id": rid, "line": n, "lines": {}}
            if not FOLLOW_UP_RULE_RE.match(rid):
                err(n, f"invalid rule id {rid!r} (use lowercase letters, digits and '-', up to 32 characters)")
            elif rid in seen:
                err(n, f"duplicate rule id {rid!r}")
            seen.add(rid)
            continue
        if current is None:
            err(n, f"the first entry under '{FOLLOW_UP_HEADING}' must be a '### <rule-id>' heading")
            current = {"_skip": True}
            continue
        if current.get("_skip"):
            continue
        m = re.match(r"^- ([a-z]+):(.*)$", line)
        if not m:
            err(n, f"expected '- <key>: <value>', got {line.strip()!r}")
            continue
        key, value = m.group(1), m.group(2).strip()
        if key not in FOLLOW_UP_KEYS:
            err(n, f"unknown key {key!r}")
            continue
        if key in current["lines"]:
            err(n, f"duplicate key {key!r}")
            continue
        current["lines"][key] = n
        problem = _follow_up_value_problem(key, value)
        if problem:
            err(n, problem)
        current[key] = value
    finish(current)
    if errors:
        return [], errors
    return rules, []


def _section_heads(lines, heading):
    return [i for i, line in enumerate(lines) if line.rstrip() == heading]


def _section_end(lines, head):
    return next((i for i in range(head + 1, len(lines)) if re.match(r"^#{1,2} ", lines[i].rstrip())), len(lines))


def parse_locations(text):
    """Parse the `## Locations` section of CLAUDE.md. Returns (values, errors); a missing key is absent from values."""
    lines = _strip_ignored(text.splitlines())
    heads = _section_heads(lines, LOCATIONS_HEADING)
    if not heads:
        return {}, []
    if len(heads) > 1:
        return {}, [f"CLAUDE.md:{i + 1}: duplicate '{LOCATIONS_HEADING}' heading" for i in heads[1:]]
    values, errors = {}, []
    for i in range(heads[0] + 1, _section_end(lines, heads[0])):
        m = re.match(r"^- ([A-Za-z_]+):(.*)$", lines[i].rstrip())
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key not in LOCATION_KEYS:
            errors.append(f"CLAUDE.md:{i + 1}: unknown key {key!r} (use {list(LOCATION_KEYS)})")
        elif key in values:
            errors.append(f"CLAUDE.md:{i + 1}: duplicate key {key!r}")
        elif not (os.path.isabs(value) or value == "~" or value.startswith("~/")):
            errors.append(f"CLAUDE.md:{i + 1}: {key} must be an absolute path or start with ~, got {value!r}")
        else:
            values[key] = value
    return ({}, errors) if errors else (values, [])


def default_locations(supervisor_dir):
    sup = os.path.normpath(supervisor_dir)
    return {"clone_root": "~/src", "work_root": os.path.join(os.path.dirname(sup), os.path.basename(sup) + "-work")}


def add_missing_locations(text, supervisor_dir):
    """Return CLAUDE.md text with each missing `## Locations` key added at its default. Existing values are kept."""
    values, errors = parse_locations(text)
    if errors:
        raise StateError(errors)
    defaults = default_locations(supervisor_dir)
    missing = [f"- {key}: {defaults[key]}\n" for key in LOCATION_KEYS if key not in values]
    if not missing:
        return text
    heads = _section_heads(_strip_ignored(text.splitlines()), LOCATIONS_HEADING)
    if not heads:
        section = f"{LOCATIONS_HEADING}\n\n{LOCATIONS_NOTE}\n\n" + "".join(missing)
        return text.rstrip("\n") + "\n\n" + section if text.strip() else section
    lines = text.splitlines(keepends=True)
    end = _section_end(_strip_ignored(text.splitlines()), heads[0])
    last = max(i for i in range(heads[0], end) if lines[i].strip())
    if not lines[last].endswith("\n"):
        lines[last] += "\n"
    return "".join(lines[:last + 1] + missing + lines[last + 1:])


def clone_url_parts(url):
    """(host, owner, repo) of an https or scp-style ssh clone url whose parts are plain names, else None."""
    for pattern in CLONE_URL_RES:
        m = pattern.match(url)
        if m and all(CLONE_PART_RE.match(part) for part in m.groups()):
            return m.groups()
    return None


def _fm_scalar(raw):
    """(value, error) of one frontmatter value. Unquoted scalars other than null and booleans stay strings."""
    if raw is None or raw in ("", "null", "Null", "NULL", "~"):
        return None, None
    if raw in ("true", "false"):
        return raw == "true", None
    if raw[0] == '"':
        try:
            return json.loads(raw), None
        except ValueError:
            return None, f"invalid quoted value {raw!r}"
    if raw[0] == "'":
        if len(raw) < 2 or raw[-1] != "'":
            return None, f"invalid quoted value {raw!r}"
        return raw[1:-1].replace("''", "'"), None
    if raw[0] == "[":
        try:
            value = json.loads(raw)
        except ValueError:
            if raw[-1] != "]" or "[" in raw[1:-1]:
                return None, f"unsupported value {raw!r}"
            parts = [_fm_scalar(p.strip()) for p in raw[1:-1].split(",") if p.strip()]
            errors = [e for _, e in parts if e]
            return (None, errors[0]) if errors else ([v for v, _ in parts], None)
        return (value, None) if isinstance(value, list) else (None, f"unsupported value {raw!r}")
    if raw[0] in "{|>&*!":
        return None, f"unsupported value {raw!r}"
    return raw, None


def _frontmatter(text):
    """Line-oriented frontmatter subset, or None when the text does not start with `---`.
    entries: (key, start, end, value) with end exclusive, so a block list's `  - x` lines belong to its key."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None
    close = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if close is None:
        return {"lines": lines, "close": None, "entries": [], "errors": ["frontmatter is not closed"]}
    entries, errors, seen = [], [], set()
    i = 1
    while i < close:
        line = lines[i].rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = FM_KEY_RE.match(line)
        if not m:
            errors.append(f"frontmatter line {i + 1}: unsupported {line.strip()!r}")
            i += 1
            continue
        key, raw, j = m.group(1), m.group(2), i + 1
        value, error = _fm_scalar(raw)
        if raw is None or raw == "":
            items = []
            while j < close and (im := FM_ITEM_RE.match(lines[j].rstrip("\r\n"))):
                item, error = _fm_scalar(im.group(1))
                if error:
                    break
                items.append(item)
                j += 1
            if items:
                value = items
        if error:
            errors.append(f"frontmatter line {i + 1}: {error}")
        if key in seen:
            errors.append(f"frontmatter line {i + 1}: duplicate key {key!r}")
        seen.add(key)
        entries.append((key, i, j, value))
        i = j
    return {"lines": lines, "close": close, "entries": entries, "errors": errors}


def parse_frontmatter(text):
    """(props, body, errors) of a note. Any error means the frontmatter is outside the supported subset."""
    fm = _frontmatter(text)
    if fm is None:
        return {}, text, ["missing frontmatter"]
    if fm["close"] is None:
        return {}, text, fm["errors"]
    props = {}
    for key, _, _, value in fm["entries"]:
        props.setdefault(key, value)
    return props, "".join(fm["lines"][fm["close"] + 1:]), fm["errors"]


def _prop_line(key, value):
    if key in NOTE_RAW_KEYS and isinstance(value, str):
        return f"{key}: {value}"
    return f"{key}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"


def set_props(text, changes):
    """Return text with each changed note property rewritten. A property whose value is already equal keeps its bytes;
    a missing one is inserted at its NOTE_KEYS position. Every other line is left byte-identical."""
    unknown = [k for k in changes if k not in NOTE_KEYS]
    if unknown:
        raise StateError(f"unknown note keys {unknown}")
    fm = _frontmatter(text)
    if fm is None or fm["errors"]:
        raise StateError(fm["errors"] if fm else "missing frontmatter")
    lines, close = fm["lines"], fm["close"]
    nl = "\r\n" if lines[0].endswith("\r\n") else "\n"
    spans = {key: (start, end, value) for key, start, end, value in fm["entries"]}
    replace, inserts = {}, []
    for key, value in changes.items():
        line = _prop_line(key, value) + nl
        if key in spans:
            start, end, old = spans[key]
            if old != value:
                replace[start] = (end, line)
            continue
        rank = NOTE_KEYS.index(key)
        before = [spans[k][1] for k in NOTE_KEYS[:rank] if k in spans]
        after = [spans[k][0] for k in NOTE_KEYS[rank + 1:] if k in spans]
        if before:
            pos = max(before)
        elif after:
            pos = min(after)
        else:
            pos = close
            while pos > 1 and not lines[pos - 1].strip():
                pos -= 1
        inserts.append((pos, rank, line))
    out, i = [], 0
    while i < len(lines):
        out += [line for pos, _, line in sorted(inserts) if pos == i]
        if i in replace:
            end, line = replace[i]
            out.append(line)
            i = end
        else:
            out.append(lines[i])
            i += 1
    return "".join(out)


def new_note_text(uid, title, kind, status, assignee, now, request="", parent=None, follows=None):
    """Text of a new task or project note in the create-task format. parent and follows are note uids."""
    if kind not in ("task", "project"):
        raise StateError(f"note kind must be task or project, got {kind!r}")
    if status not in NOTE_STATUSES or assignee not in ASSIGNEES:
        raise StateError(f"invalid status/assignee {status!r}/{assignee!r}")
    values = {
        "uid": uid, "title": title, "para": "Project", "aliases": [], "created_at": f"{now[:10]} {now[11:19]}",
        "archived_at": None, "tags": [kind], "start_date": None, "due_date": None, "pending_until": None,
        "completed_at": None, "status": status, "assignee": assignee,
        "parent": f"[[{parent}]]" if parent else None, "follows": f"[[{follows}]]" if follows else None,
    }
    head = "".join(_prop_line(k, values[k]) + "\n" for k in NOTE_KEYS if k in values and (values[k] is not None or k not in ("parent", "follows")))
    content = (request or "").strip("\n")
    body = "\n".join(f"## {name}\n\n{content}\n" if name == "概要" and content else f"## {name}\n\n" for name in NOTE_SECTIONS)
    return f"---\n{head}---\n{body}"


def parse_sections(body):
    """{title: text} for every `## title` of a note body (first occurrence wins). A section runs to the next H1/H2;
    headings inside fenced blocks and comments do not count."""
    lines = body.split("\n")
    stripped = _strip_ignored(lines)
    sections = {}
    for i, line in enumerate(stripped):
        m = re.match(r"^## (.+)$", line.rstrip())
        if m and m.group(1).strip() not in sections:
            sections[m.group(1).strip()] = "\n".join(lines[i + 1:_section_end(stripped, i)])
    return sections


def _indent(line):
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())


def _sub_end(lines, i, limit):
    """End (exclusive) of the sub-items of the list item at lines[i]: following lines indented deeper than it.
    Blank lines count only when a deeper line follows them."""
    base, last = _indent(lines[i]), i
    for j in range(i + 1, limit):
        if not lines[j].strip():
            continue
        if _indent(lines[j]) <= base:
            break
        last = j
    return last + 1


def _ua_meta(meta):
    ua = {"kind": None, "detector": None, "answer_required": False, "since": None, "rule": None, "url": None,
          "done": None, "done_rev": None, "chk_rev": None}
    for token in meta.split():
        if token in ("ask", "slot"):
            ua["kind"] = token
        elif token.startswith("auto="):
            ua["kind"], ua["detector"] = "auto", token[5:]
        elif token == "answer":
            ua["answer_required"] = True
        elif re.fullmatch(r"chk=r\d+", token):
            ua["chk_rev"] = int(token[5:])
        elif re.fullmatch(r"r\d+", token):
            ua["done_rev"] = int(token[1:])
        elif "=" in token:
            key, value = token.split("=", 1)
            if key in UA_META_KEYS + ("done",):
                ua[key] = value
    return ua


def parse_user_actions(section_text):
    """User action items of a `## ユーザーアクション` section. Lines without `<!-- sv ... -->` metadata are the user's
    and are skipped. answer is the item's sub-items, dedented but otherwise verbatim."""
    lines = section_text.split("\n")
    items = []
    for i, line in enumerate(lines):
        m = UA_LINE_RE.match(line)
        if not m:
            continue
        ua = _ua_meta(m.group(5))
        if ua["kind"] is None:
            continue
        sub = lines[i + 1:_sub_end(lines, i, len(lines))]
        items.append({"id": m.group(3), "description": m.group(4).strip(), "checked": m.group(2) in "xX",
                      **ua, "block": m.group(6), "answer": textwrap.dedent("\n".join(sub)).strip()})
    return items


def ua_open(ua):
    """True for an unresolved user action: anything but a slot that has no done mark."""
    return ua.get("kind") != "slot" and not ua.get("done")


def render_ua_line(ua):
    meta = [f"auto={ua['detector']}" if ua["kind"] == "auto" else ua["kind"]]
    if ua.get("answer_required"):
        meta.append("answer")
    meta += [f"{key}={ua[key]}" for key in UA_META_KEYS if ua.get(key)]
    if ua.get("chk_rev") is not None:
        meta.append(f"chk=r{ua['chk_rev']}")
    if ua.get("done"):
        meta.append(f"done={ua['done']}")
        if ua.get("done_rev") is not None:
            meta.append(f"r{ua['done_rev']}")
    box = "x" if ua.get("checked") else " "
    return f"- [{box}] {ua['id']} {ua['description']} <!-- sv {' '.join(meta)} --> ^{ua.get('block') or ua['id'].lower()}"


def _ua_errors(ua):
    errors = []
    if not isinstance(ua.get("id"), str) or not UA_ID_RE.match(ua["id"]):
        errors.append(f"invalid user action id {ua.get('id')!r}")
    if ua.get("kind") not in UA_KINDS:
        errors.append(f"user action kind must be one of {list(UA_KINDS)}")
    elif (ua["kind"] == "auto") != (ua.get("detector") in UA_DETECTORS):
        errors.append(f"auto needs a detector in {list(UA_DETECTORS)}, got {ua.get('detector')!r}")
    if not isinstance(ua.get("description"), str) or not ua["description"].strip() or "\n" in ua["description"]:
        errors.append("user action description must be one non-empty line")
    errors += [f"{key} must not contain spaces" for key in UA_META_KEYS if ua.get(key) and re.search(r"\s", str(ua[key]))]
    return errors


def _art_json(data):
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _art_text(item):
    if item.get("label"):
        return item["label"]
    ref = item.get("ref") or ""
    if item["kind"] == "commit":
        ref = ref[:10]
    elif "#" in ref:
        ref = ref.rsplit("#", 1)[1]
    elif not ref and item["kind"] == "pr":
        ref = follow_up_label(None, item["url"])
    return f"{item['kind']} {ref or item.get('url')}"


def render_artifact_line(item):
    """One `## 成果物` line: display text, the sv-art JSON comment, and the block id ^sv-art-<n>."""
    data = {k: item[k] for k in ART_KEYS if item.get(k) is not None}
    if item["kind"] == "file":
        shown = f"`{item.get('path')}`"
    else:
        text = _art_text(item).replace("[", "\\[").replace("]", "\\]")
        shown = f"[{text}]({item['url']})" if item.get("url") else text
    return f"- {shown} <!-- sv-art {_art_json(data)} --> ^sv-art-{item['n']}"


def _art_errors(data):
    if not isinstance(data, dict):
        return ["sv-art data must be an object"]
    errors = [f"{key} must be a positive integer" for key in ("n", "g")
              if not isinstance(data.get(key), int) or isinstance(data.get(key), bool) or data[key] < 1]
    if "label" in data and not isinstance(data["label"], str):
        errors.append("label must be a string")
    return errors + _validate_artifact("sv-art", {k: data[k] for k in ART_FIELDS if k in data}, None)


def parse_artifacts(section_text):
    """(items, problems) of a `## 成果物` section. Items are sorted by (g, n), not by line position.
    A line with broken or invalid metadata, or sharing its n with another line, is left out and reported."""
    found, problems = {}, []
    for k, line in enumerate(section_text.split("\n"), 1):
        m = ART_COMMENT_RE.search(line)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
        except ValueError:
            problems.append(f"line {k}: invalid sv-art JSON")
            continue
        errors = _art_errors(data)
        if errors:
            problems += [f"line {k}: {e}" for e in errors]
            continue
        found.setdefault(data["n"], []).append((k, {key: data[key] for key in ART_KEYS if key in data}))
    items = []
    for n, rows in found.items():
        if len(rows) > 1:
            problems.append(f"lines {', '.join(str(k) for k, _ in rows)}: duplicate n {n}")
        else:
            items.append(rows[0][1])
    return sorted(items, key=lambda a: (a["g"], a["n"])), problems


def _group_incomplete(kinds):
    """True when a branch in the group's kinds is not directly followed by a commit."""
    return any(k == "branch" and kinds[j + 1:j + 2] != ["commit"] for j, k in enumerate(kinds))


def artifacts_view(items, art_from=None):
    """v1 artifacts of the items numbered art_from or later, in (g, n) order. A group whose branch is not directly
    followed by a commit is incomplete and left out whole."""
    items = sorted((i for i in items if art_from is None or i["n"] >= art_from), key=lambda a: (a["g"], a["n"]))
    groups = {}
    for item in items:
        groups.setdefault(item["g"], []).append(item)
    view = []
    for members in groups.values():
        kinds = [m["kind"] for m in members]
        if _group_incomplete(kinds):
            continue
        view += [{k: m[k] for k in ART_FIELDS if k in m} for m in members]
    return view


def art_sha(items):
    """Hash of the artifact data in (g, n) order. label and display text are excluded."""
    rows = [[i["n"], i["g"], i["kind"], i.get("ref"), i.get("url"), i.get("path")]
            for i in sorted(items, key=lambda a: (a["g"], a["n"]))]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def _split_note(text):
    fm = _frontmatter(text)
    if fm is None or fm["close"] is None:
        return "", text
    return "".join(fm["lines"][:fm["close"] + 1]), "".join(fm["lines"][fm["close"] + 1:])


def _section_span(lines, title):
    stripped = _strip_ignored(lines)
    heads = _section_heads(stripped, f"## {title}")
    return (heads[0], _section_end(stripped, heads[0])) if heads else None


def _ensure_section(lines, title):
    """(head, end) of the section, creating it before the next existing section in NOTE_SECTIONS order."""
    span = _section_span(lines, title)
    if span:
        return span
    later = [s for s in (_section_span(lines, t) for t in NOTE_SECTIONS[NOTE_SECTIONS.index(title) + 1:]) if s]
    if later:
        pos = min(head for head, _ in later)
        lines[pos:pos] = ([""] if pos > 0 and lines[pos - 1].strip() else []) + [f"## {title}", ""]
    else:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += (["", f"## {title}", ""] if lines else [f"## {title}", ""])
    return _section_span(lines, title)


def _insert_in_section(lines, title, new_lines):
    """Append new_lines after the last non-blank line of the section, keeping a blank line before the next heading."""
    head, end = _ensure_section(lines, title)
    last = max((i for i in range(head + 1, end) if lines[i].strip()), default=head)
    block = ([""] if last == head else []) + new_lines
    pos = last + 1
    if pos >= len(lines) or lines[pos].strip():
        block.append("")
    lines[pos:pos] = block


def _has_block(lines, block_id):
    pattern = re.compile(rf"\^{re.escape(block_id)}[ \t]*$")
    return any(pattern.search(line) for line in lines)


def _find_ua(lines, ua_id):
    span = _section_span(lines, UA_SECTION)
    for i in range(*span) if span else ():
        m = UA_LINE_RE.match(lines[i])
        if m and (m.group(3) == ua_id or m.group(6) == ua_id.lower()):
            return i, span[1]
    return None, None


def _edit_ua(line, tokens, checked=False):
    m = UA_LINE_RE.match(line)
    line = line[:m.start(5)] + " ".join(tokens) + " " + line[m.end(5):]
    return line[:m.start(2)] + "x" + line[m.end(2):] if checked else line


def _art_data(line):
    m = ART_COMMENT_RE.search(line)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _art_op_lines(op, g, rev):
    items = []
    for item in op.get("items") or []:
        item = {**item, "g": g, "r": rev}
        errors = _art_errors(item)
        if errors:
            raise StateError(f"{op['op']}: {errors}")
        items.append(item)
    if not items:
        raise StateError(f"{op['op']} needs items")
    return items


def _op_log(lines, op, now, rev, index, problems):
    block_id = op.get("id") or f"sv-log-r{rev}-{index}"
    if not isinstance(op.get("text"), str) or not op["text"].strip():
        raise StateError("log needs text")
    if _has_block(lines, block_id):
        return []
    first, *rest = op["text"].strip("\n").split("\n")
    entry = [f"- {now[:10]} {now[11:16]} {first} ^{block_id}"] + [f"    {line}".rstrip() for line in rest]
    _insert_in_section(lines, LOG_SECTION, entry)
    return [block_id]


def _op_ua_add(lines, op, now, rev, index, problems):
    ua = {"since": now, **(op.get("ua") or {}), "checked": False, "chk_rev": None, "done": None, "done_rev": None}
    errors = _ua_errors(ua)
    if errors:
        raise StateError(f"ua_add: {errors}")
    block_id = ua.get("block") or ua["id"].lower()
    if _has_block(lines, block_id):
        return []
    span = _section_span(lines, UA_SECTION)
    items = [i for i in range(*span) if UA_LINE_RE.match(lines[i])] if span else []
    if items:
        # Keep the checklist together: a user's ### heading later in the section stays below it.
        pos = _sub_end(lines, items[-1], span[1])
        lines[pos:pos] = [render_ua_line(ua)]
    else:
        _insert_in_section(lines, UA_SECTION, [render_ua_line(ua)])
    return [block_id]


def _op_ua_check(lines, op, now, rev, index, problems):
    i, _ = _find_ua(lines, op["id"])
    if i is None:
        problems.append(f"ua_check {op['id']}: item not found")
        return []
    tokens = UA_LINE_RE.match(lines[i]).group(5).split()
    # The rev mark makes a re-application a no-op, so a box the user unchecked afterwards stays unchecked.
    if f"chk=r{rev}" not in tokens:
        lines[i] = _edit_ua(lines[i], [t for t in tokens if not t.startswith("chk=")] + [f"chk=r{rev}"], checked=True)
    return []


def _op_ua_ack(lines, op, now, rev, index, problems):
    i, _ = _find_ua(lines, op["id"])
    if i is None:
        problems.append(f"ua_ack {op['id']}: item not found")
        return []
    tokens = UA_LINE_RE.match(lines[i]).group(5).split()
    if not any(t.startswith("done=") for t in tokens):
        lines[i] = _edit_ua(lines[i], tokens + [f"done={op.get('at') or now}", f"r{rev}"])
    return []


def _op_ua_answer(lines, op, now, rev, index, problems):
    i, end = _find_ua(lines, op["id"])
    if i is None:
        problems.append(f"ua_answer {op['id']}: item not found")
        return []
    if not isinstance(op.get("text"), str) or not op["text"].strip():
        raise StateError("ua_answer needs text")
    base = UA_LINE_RE.match(lines[i]).group(1) + "    "
    first, *rest = op["text"].strip("\n").split("\n")
    new = [f"{base}- {first}"] + [f"{base}  {line}".rstrip() for line in rest]
    sub_end = _sub_end(lines, i, end)
    if "\n".join(new) not in "\n".join(lines[i + 1:sub_end]):
        lines[sub_end:sub_end] = new
    return []


def _op_art_add(lines, op, now, rev, index, problems):
    items = op.get("items") or []
    g = op.get("g") or (items[0].get("n") if items else None)
    added, block = [], []
    for item in _art_op_lines(op, g, rev):
        block_id = f"sv-art-{item['n']}"
        if not _has_block(lines, block_id):
            block.append(render_artifact_line(item))
            added.append(block_id)
    if block:
        _insert_in_section(lines, ART_SECTION, block)
    return added


def _op_art_set(lines, op, now, rev, index, problems):
    span = _section_span(lines, ART_SECTION)
    i = next((i for i in range(*span) if (_art_data(lines[i]) or {}).get("n") == op["n"]), None) if span else None
    if i is None:
        problems.append(f"art_set {op['n']}: artifact line not found")
        return []
    data = _art_data(lines[i])
    if data.get("r") == rev:
        return []
    old_url = data.get("url")
    for key, value in (op.get("fields") or {}).items():
        if key not in ART_FIELDS:
            raise StateError(f"art_set cannot change {key!r}")
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    data["r"] = rev
    errors = _art_errors(data)
    if errors:
        raise StateError(f"art_set: {errors}")
    m = ART_COMMENT_RE.search(lines[i])
    shown, rest = lines[i][:m.start()], lines[i][m.end():]
    if old_url and data.get("url") and data["url"] != old_url:
        shown = shown.replace(f"]({old_url})", f"]({data['url']})")
    lines[i] = shown + f"<!-- sv-art {_art_json({k: data[k] for k in ART_KEYS if k in data})} -->" + rest
    return []


def _op_art_replace_group(lines, op, now, rev, index, problems):
    g = op.get("g")
    items = _art_op_lines(op, g, rev)
    block_ids = [f"sv-art-{item['n']}" for item in items]
    if any(_has_block(lines, b) for b in block_ids):
        return []
    new = [render_artifact_line(item) for item in items]
    head, end = _ensure_section(lines, ART_SECTION)
    group = [i for i in range(head + 1, end) if (_art_data(lines[i]) or {}).get("g") == g]
    if not group:
        _insert_in_section(lines, ART_SECTION, new)
        return block_ids
    for i in reversed(group):
        del lines[i]
    lines[group[0]:group[0]] = new
    return block_ids


_NOTE_BODY_OPS = {
    "log": _op_log, "ua_add": _op_ua_add, "ua_check": _op_ua_check, "ua_answer": _op_ua_answer, "ua_ack": _op_ua_ack,
    "art_add": _op_art_add, "art_set": _op_art_set, "art_replace_group": _op_art_replace_group,
}


def apply_note_ops(text, ops, now, rev):
    """Apply note ops to a note's text. Returns (text, new_ids, problems).
    Re-applying the same ops with the same rev is a no-op: lines are added once per block id, and edits of existing
    lines leave a rev mark (chk=r<rev>, done=<at> r<rev>, "r": <rev>). new_ids are the block ids this call added.
    create writes a note only into empty text; assign_uid sets uid only when the note has none."""
    new_ids, problems = [], []
    for index, op in enumerate(ops):
        name = op.get("op")
        if name == "create":
            if not text.strip():
                text = new_note_text(op["uid"], op["title"], op["kind"], op["status"], op["assignee"], now,
                                     op.get("request", ""), op.get("parent"), op.get("follows"))
            continue
        if name == "assign_uid":
            props, _, errors = parse_frontmatter(text)
            if errors:
                problems.append(f"assign_uid: {errors}")
            elif not props.get("uid"):
                text = set_props(text, {"uid": op["uid"]})
            continue
        if name not in _NOTE_BODY_OPS:
            raise StateError(f"unknown note op {name!r}")
        head, body = _split_note(text)
        lines = body.split("\n")
        new_ids += _NOTE_BODY_OPS[name](lines, op, now, rev, index, problems)
        text = head + "\n".join(lines)
    return text, new_ids, problems


def _ua_number(ua_id):
    return _id_number(UA_ID_RE, ua_id) or 0


def _note_view(text):
    """(props, sections, artifact items) of a note text."""
    props, body, _ = parse_frontmatter(text)
    sections = parse_sections(body)
    return props, sections, parse_artifacts(sections.get(ART_SECTION, ""))[0]


def _view_user_actions(section_text):
    """Open user actions in the v1 shape. Slots are left out: derive_status would count them."""
    actions = []
    for ua in parse_user_actions(section_text or ""):
        if not ua_open(ua):
            continue
        item = {"id": ua["id"], "description": ua["description"], "since": ua["since"]}
        if ua["rule"]:
            item["follow_up"] = {"rule": ua["rule"], "url": ua["url"],
                                 "done": "pr_closed" if ua["detector"] == "pr_closed" else "manual"}
        if ua["kind"] == "auto":
            item["detector"] = ua["detector"]
        if ua["answer_required"]:
            item["needs_answer"] = True
        actions.append(item)
    return actions


def _held_action(task, wid):
    """The v1 read-the-result action a held worker stands for, so the v1 held-worker invariants keep working."""
    return {"id": f"{HELD_UA_PREFIX}{wid}", "description": f"{READ_ACTION_PREFIX}{wid} (held)", "since": task.get("created_at")}


def _note_title(item, props):
    # digest must not fail when the vault cannot be read: show the uid and the note path instead.
    return props.get("title") or f"{item.get('uid')} ({item.get('note')})"


def join_view(state, notes):
    """v1 domain view of a v2 state. notes maps uid to note text (after this commit's ops).
    Tasks get title, request (## 概要), open user actions (plus one read action per held worker), artifacts
    (## 成果物 rows numbered art_from or later) and status; stubs get title, request and the rows before the art_from
    of a revived task with the same id (all rows otherwise); projects get name, context_doc_url and user actions."""
    view = copy.deepcopy(state)
    view["schema_version"] = VIEW_SCHEMA_VERSION
    parsed = {uid: _note_view(text) for uid, text in notes.items() if isinstance(text, str)}
    empty = ({}, {}, [])
    sup = view["supervisor"]
    numbers = [_ua_number(ua["id"]) for _, sections, _ in parsed.values()
               for ua in parse_user_actions(sections.get(UA_SECTION, ""))]
    sup["last_ua_number"] = max(numbers + [sup.get("last_ua_number", 0)])
    for project in view.get("projects", []):
        props, sections, _ = parsed.get(project.get("uid"), empty)
        urls = re.findall(r"https?://[^\s)>\]]+", sections.get("概要", "") + "\n" + sections.get("関連資料", ""))
        project.update(name=_note_title(project, props), context_doc_url=urls[0] if urls else None,
                       user_actions=_view_user_actions(sections.get(UA_SECTION)))
    names = {p["id"]: p["name"] for p in view.get("projects", [])}
    workers_by_id = {w["id"]: w for w in view["workers"]}
    live = {t["id"]: t for t in view["tasks"]}
    for task in view["tasks"]:
        props, sections, items = parsed.get(task.get("uid"), empty)
        task.update(title=_note_title(task, props), request=sections.get("概要", "").strip(), followups=[],
                    artifacts=artifacts_view(items, task.get("art_from")),
                    user_actions=(_view_user_actions(sections.get(UA_SECTION))
                                  + [_held_action(task, wid) for wid in task.get("held", [])]),
                    last_artifact_number=max([i["n"] for i in items] + [task.get("last_artifact_number", 0)]))
        task["status"] = derive_status(task, workers_by_id)
        if isinstance(task.get("proposal"), dict):
            # A task picked up from a proposal note stores only {from, message_ref}; the note is the proposal.
            task["proposal"] = dict({"title": task["title"], "text": task["request"]}, **task["proposal"])
    for stub in view["archived"]:
        props, sections, items = parsed.get(stub.get("uid"), empty)
        last = max([i["n"] for i in items] + [stub.get("last_artifact_number", 0)])
        if stub["id"] in live:
            items = [i for i in items if i["n"] < (live[stub["id"]].get("art_from") or 1)]
        stub.update(title=_note_title(stub, props), request=sections.get("概要", "").strip(),
                    artifacts=artifacts_view(items), last_artifact_number=last)
        if "project" in stub:
            stub["project"] = {"id": stub["project"]["id"], "name": names.get(stub["project"]["id"], stub["project"]["id"])}
    return view


def _ua_add_op(owner_id, ua):
    follow_up = ua.get("follow_up") or {}
    detector = ua.get("detector")
    if detector is None and ua["description"] == waiting_start_description(owner_id):
        detector = "start_possible"
    if detector is None and follow_up.get("done") == "pr_closed":
        detector = "pr_closed"
    item = {"id": ua["id"], "description": ua["description"], "kind": "auto" if detector else "ask"}
    if detector:
        item["detector"] = detector
    if ua.get("needs_answer"):
        item["answer_required"] = True
    if follow_up:
        item.update(rule=follow_up.get("rule"), url=follow_up.get("url"))
    if ua.get("since"):
        item["since"] = ua["since"]
    return {"op": "ua_add", "ua": item}


def split_view(view_cand, view_new, ops=None):
    """(state_v2, ops) for a view next_state returned. ops ({uid: [op, ...]}) gain a ua_add for each user action
    next_state added and a ua_ack for each it removed (including every open action of a task it archived).
    held keeps only workers whose read action is still in the view; last_ua_number rises to the highest number used."""
    state = copy.deepcopy(view_new)
    state["schema_version"] = SCHEMA_VERSION
    ops = {uid: list(items) for uid, items in (ops or {}).items()}
    numbers = [state["supervisor"].get("last_ua_number", 0)]
    for key in ("tasks", "projects"):
        after_by_id = {o["id"]: o for o in view_new.get(key, [])}
        for old in view_cand.get(key, []):
            before = {ua["id"] for ua in old.get("user_actions", []) if not _read_action_worker(ua)}
            after = [ua for ua in after_by_id.get(old["id"], {}).get("user_actions", []) if not _read_action_worker(ua)]
            numbers += [_ua_number(ua["id"]) for ua in after]
            added = [_ua_add_op(old["id"], ua) for ua in after if ua["id"] not in before]
            acked = [{"op": "ua_ack", "id": i} for i in sorted(before - {ua["id"] for ua in after})]
            if added or acked:
                ops.setdefault(old.get("uid"), []).extend(added + acked)
    state["supervisor"]["last_ua_number"] = max(numbers)
    for task in state["tasks"]:
        held = [wid for wid in task.pop("held", []) if wid in _held_ids(task)]
        if held:
            task["held"] = held
    cand_uids = {t["id"]: t.get("uid") for t in view_cand["tasks"]}
    for key, moved in NOTE_MOVED_KEYS.items():
        for item in state.get(key, []):
            for k in moved:
                item.pop(k, None)
    for stub in state["archived"]:
        if not stub.get("uid") and cand_uids.get(stub["id"]):
            stub["uid"] = cand_uids[stub["id"]]
        if "project" in stub:
            stub["project"] = {"id": stub["project"]["id"]}
    return state, ops


def _runtime_shape_errors(prefix, item):
    errors = []
    seen = item.get("note_seen")
    if seen is not None and not (isinstance(seen, dict) and set(seen) <= set(SEEN_KEYS)
                                 and all(v is None or isinstance(v, str) for v in seen.values())):
        errors.append(f"{prefix}: note_seen must be {{{', '.join(SEEN_KEYS)}}} with strings or null")
    hold = item.get("user_hold")
    if hold is not None and not (isinstance(hold, dict) and _str(hold.get("at")) and hold.get("reason") in HOLD_REASONS):
        errors.append(f"{prefix}: user_hold must be {{at, status, assignee, reason}} with reason in {list(HOLD_REASONS)}")
    for key, fields in (("confirmed", ("at",)), ("artifacts_quarantine", ("at", "ua")), ("done_proposed", ("at", "summary"))):
        if item.get(key) is not None and not _fields_ok(item[key], fields):
            errors.append(f"{prefix}: {key} must be {{{', '.join(fields)}}} with non-empty strings")
    if "art_from" in item and not (_nonneg_int(item["art_from"]) and item["art_from"] >= 1):
        errors.append(f"{prefix}: art_from must be a positive integer")
    if not _nonneg_int(item.get("last_artifact_number", 0)):
        errors.append(f"{prefix}: last_artifact_number must be a non-negative integer")
    return errors


def _storage_errors(state, notes):
    """Checks of the v2 storage form that the v1 view cannot see. notes maps uid to note text."""
    if state.get("schema_version") != SCHEMA_VERSION:
        return [f"schema_version must be {SCHEMA_VERSION}"]
    errors = []
    sup = state.get("supervisor") or {}
    if not _nonneg_int(sup.get("last_ua_number", 0)):
        errors.append("supervisor.last_ua_number must be a non-negative integer")
    if not isinstance(sup.get("note_warnings", {}), dict):
        errors.append("supervisor.note_warnings must be an object")
    journal = sup.get("note_journal")
    if journal is not None and not (isinstance(journal, dict) and _nonneg_int(journal.get("rev"))
                                    and isinstance(journal.get("entries"), list)):
        errors.append("supervisor.note_journal must be null or {rev, entries}")
    live_uids = {}
    for key in ("tasks", "projects", "archived"):
        for item in state.get(key, []):
            prefix = f"{key} {item.get('id')}"
            moved = [k for k in NOTE_MOVED_KEYS[key] if k in item]
            if moved:
                errors.append(f"{prefix}: {moved} belong to the note")
            uid = item.get("uid")
            if not _str(uid):
                errors.append(f"{prefix}: uid must be a non-empty string")
                continue
            # A stub's note may be gone (the user deleted it); only live items need theirs.
            if uid not in notes and key != "archived":
                errors.append(f"{prefix}: note {uid} not found")
            if key != "archived":
                if uid in live_uids:
                    errors.append(f"{prefix}: uid {uid} is also used by {live_uids[uid]}")
                live_uids[uid] = prefix
            errors += _runtime_shape_errors(prefix, item)
    stub_ids = {s.get("id") for s in state.get("archived", [])}
    for t in state.get("tasks", []):
        held = t.get("held", [])
        if not isinstance(held, list) or any(wid not in t.get("worker_ids", []) for wid in held):
            errors.append(f"tasks {t.get('id')}: held must list workers of the task")
        if t.get("id") in stub_ids and "art_from" not in t:
            errors.append(f"tasks {t.get('id')}: a revived task needs art_from")
    return errors


def note_props(kind, item, workers_by_id):
    """(status, assignee) commit derives for a live task or project of the view (3.4 (1)), or None to leave the
    note's props alone (stubs, user_hold). An archived result has assignee None: keep the note's assignee."""
    if kind not in ("task", "project"):
        return None
    if item.get("confirmed"):
        return ("done", "user")
    if item.get("cancel" if kind == "task" else "close"):
        return ("archived", None)
    if item.get("user_hold"):
        return None
    if kind == "project":
        if item.get("done_proposed"):
            return ("done", "agent")
        return ("in progress", "user") if item.get("user_actions") else ("in progress", "agent")
    actions = item.get("user_actions", [])
    status = derive_status(item, workers_by_id)
    if status == "done" or (actions and all(_read_action_worker(ua) for ua in actions)):
        return ("done", "agent")
    return ("in progress", "user") if status == "needs_user" else ("in progress", "agent")


def id_props(state):
    """{uid: {task_id, project_id}} commit and migrate-v2 write to the notes of a state or view, so the kanban shows
    them. A live task wins over its stub; a project note has no task_id."""
    out = {}
    for sb in state.get("archived", []):
        out[sb.get("uid")] = {"task_id": sb["id"], "project_id": (sb.get("project") or {}).get("id")}
    for t in state.get("tasks", []):
        out[t.get("uid")] = {"task_id": t["id"], "project_id": t.get("project_id")}
    for p in state.get("projects", []):
        out[p.get("uid")] = {"task_id": None, "project_id": p["id"]}
    out.pop(None, None)
    return out


def _id_changes(text, ids):
    props = parse_frontmatter(text)[0]
    return {k: v for k, v in ids.items() if props.get(k) != v}


NOTE_USER_VALUES = (("in progress", "user"), ("pending", "user"), ("backlog", "user"))
NOTE_AGENT_RESUME = (("in progress", "agent"), ("backlog", "agent"))


def user_transition(seen, cur, ctx):
    """Classify a user's change of a note (3.4 (2)). seen and cur are {status, assignee, ua_sha, art_sha}; seen is
    None for an unmanaged note, cur None when the frontmatter cannot be read. ctx: managed, live, active,
    hold (None, "pause", "invalid"), open_ua, kind ("task" or "project"), duplicate, artifacts_ok.
    Returns {event, row, ack, clear_invalid[, pause]}: row is the table row (0 for no change), ack the values to copy
    into note_seen once handled (an invalid change acks nothing; a status change leaves an artifact change for later)."""
    hold = ctx.get("hold")

    def result(event, row, ack, **extra):
        return {"event": event, "row": row, "ack": ack, "clear_invalid": hold == "invalid" and event != "invalid", **extra}

    if not ctx.get("managed"):
        if cur is None or cur.get("assignee") != "agent":
            return result("none", 2, None)
        return result("pickup", 1, cur) if cur.get("status") == "backlog" else result("invalid", 3, None)
    pair = ((cur or {}).get("status"), (cur or {}).get("assignee"))
    if (cur is None or ctx.get("duplicate") or pair[0] not in NOTE_STATUSES or pair[1] not in ASSIGNEES
            or pair == ("pending", "agent")):
        return result("invalid", 4, seen)
    prev = (seen.get("status"), seen.get("assignee"))
    art_changed = cur.get("art_sha") != seen.get("art_sha")
    if pair == prev:
        if art_changed:
            if ctx.get("artifacts_ok", True) or not (ctx.get("live") and ctx.get("kind") == "task"):
                return result("artifacts_edit", 22, cur)
            return result("artifacts_broken", 23, cur)
        # A checkbox or sub-item edit alone never needs handling: answers are read on resume.
        return result("none", 10 if pair == ("in progress", "user") and ctx.get("open_ua") else 0, cur)
    ack = dict(cur, art_sha=seen.get("art_sha")) if art_changed else cur
    task = ctx.get("kind") == "task"
    if prev[0] == "archived":
        return result("invalid", 19, seen)
    if pair[0] == "archived":
        return result("cancel", 17, ack) if task else result("close", 18, ack)
    found = None
    if hold == "pause":
        if pair in NOTE_AGENT_RESUME:
            found = ("resume", 8)
        elif pair in NOTE_USER_VALUES:
            found = ("none", 9)
        elif task and pair[0] == "done":
            found = ("cancel", 15)
        elif not task and pair == ("done", "user") and prev[0] in ("done", "in progress"):
            found = ("confirm", 14)
    elif prev[0] == "in progress":
        if prev[1] == "agent" and pair in NOTE_USER_VALUES:
            found = ("pause", 5 if ctx.get("active") else 7)
        elif prev[1] == "user" and pair in NOTE_USER_VALUES[1:]:
            found = ("pause", 6 if ctx.get("active") else 7)
        elif prev[1] == "agent" and pair == ("backlog", "agent"):
            found = ("restore", 12)
        elif prev[1] == "user" and pair in NOTE_AGENT_RESUME:
            found = ("resume", 11)
        elif task and pair[0] == "done":
            found = ("cancel", 15)
        elif not task and pair == ("done", "user"):
            found = ("confirm", 14)
    elif prev == ("done", "agent"):
        if pair == ("done", "user"):
            found = ("confirm", 13 if task else 14)
        elif pair == ("backlog", "agent"):
            found = ("follow_up", 16)
        elif not task and pair[0] == "in progress":
            return result("reject_done", 21, ack, pause=pair[1] == "user")
    elif prev == ("done", "user"):
        if pair == ("done", "agent"):
            found = ("restore", 20)
        elif pair == ("backlog", "agent"):
            found = ("follow_up", 16)
    return result(found[0], found[1], ack) if found else result("invalid", None, seen)


def _branch_group_ok(artifacts, worker):
    ref = f"{worker.get('cwd')}#{worker.get('branch')}"
    return any(a.get("kind") == "branch" and a.get("ref") == ref and artifacts[i + 1:i + 2]
               and artifacts[i + 1].get("kind") == "commit" for i, a in enumerate(artifacts))


def artifact_needs(view_task, workers, items):
    """Unmet needs of a live task on its `## 成果物` rows (3.9), as text for the quarantine user action.
    items are parse_artifacts rows, not the view, so a group broken by the user is seen as broken."""
    art_from = view_task.get("art_from")
    rows = [i for i in items if art_from is None or i["n"] >= art_from]
    artifacts = artifacts_view(rows)
    mine = [w for w in workers if w.get("task_id") == view_task["id"]]
    needs = []
    for w in mine:
        if (w.get("intended_state") == "archived" and not view_task.get("cancel") and w.get("observed_state") != "missing"
                and not w.get("replaced_by") and w["id"] not in _no_result_ids(view_task)
                and not _delivery_artifacts_ok(w.get("delivery"), artifacts)):
            needs.append(f"{w['id']} の完了の記録({w.get('delivery')} の成果物)")
    pr_urls = set(_pr_urls({"artifacts": artifacts}))
    for ua in view_task.get("user_actions", []):
        url = (ua.get("follow_up") or {}).get("url")
        if url and url not in pr_urls:
            needs.append(f"{ua['id']} の PR {url}")
        for w in mine:
            if w.get("kind") == "git" and _is_push_decision(ua, w) and not _branch_group_ok(artifacts, w):
                needs.append(f"{ua['id']} の対象 {w['id']} の branch {w.get('branch')} と直後の commit")
    for g in sorted({i["g"] for i in rows}):
        kinds = [i["kind"] for i in rows if i["g"] == g]
        if _group_incomplete(kinds):
            needs.append(f"グループ {g} の branch の直後の commit")
    return needs


def _text_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def note_values(text):
    """{status, assignee, ua_sha, art_sha} of a note (what note_seen tracks), or None when its frontmatter is unreadable."""
    props, body, errors = parse_frontmatter(text)
    if errors:
        return None
    sections = parse_sections(body)
    return {"status": props.get("status"), "assignee": props.get("assignee"),
            "ua_sha": _text_sha(sections.get(UA_SECTION, "")),
            "art_sha": art_sha(parse_artifacts(sections.get(ART_SECTION, ""))[0])}


def _pair(values):
    return ((values or {}).get("status"), (values or {}).get("assignee"))


def journal_reflected(text, entry, rev):
    """True when the note (None when missing) already shows a journal entry's write (3.5 step 1): its sha is after_sha,
    one of the block ids it created exists, one of its rev marks exists, or its props are the written after values.
    Block ids that existed before the write are not in new_ids, so they never count."""
    if text is None:
        return False
    if _text_sha(text) == entry.get("after_sha"):
        return True
    lines = text.split("\n")
    if any(_has_block(lines, block_id) for block_id in entry.get("new_ids", [])):
        return True
    if re.search(rf"(?:chk=r{rev}|\bdone=\S+ r{rev})(?=[ \t])|\"r\":{rev}[,}}]", text):
        return True
    after, before = _pair(entry.get("after")), _pair(entry.get("before"))
    return after != before and _pair(note_values(text)) == after


def number_note_ops(ops, next_ua, art_last):
    """Fill the user action ids and artifact numbers the supervisor left out (3.5 step 3). ops is {uid: [op, ...]},
    next_ua the first free UA number, art_last {uid: highest artifact number used}. Returns new ops."""
    out = {}
    for uid in sorted(ops):
        last = art_last.get(uid, 0)
        filled = []
        for op in copy.deepcopy(ops[uid]):
            if op.get("op") == "ua_add" and not (op.get("ua") or {}).get("id"):
                op.setdefault("ua", {})["id"] = f"UA-{next_ua:04d}"
                next_ua += 1
            if op.get("op") in ("art_add", "art_replace_group"):
                for item in op.get("items") or []:
                    if "n" not in item:
                        last += 1
                        item["n"] = last
                    last = max(last, item["n"]) if isinstance(item["n"], int) else last
            filled.append(op)
        out[uid] = filled
    return out


# v1 user action wording (SKILL.md section 5) -> (kind, detector) of the note item (3.2). Anything else is ask answer.
MIGRATE_UA_RULES = (
    (re.compile(r"^Answer w-\d+ \("), "auto", "worker_report"),
    (re.compile(r"^Hub wait w-\d+ \("), "auto", "worker_report"),
    (re.compile(r"^Review wait w-\d+ \("), "auto", "review_state"),
    (re.compile(r"^Pass review R-\d+ "), "auto", "review_state"),
    (re.compile(r"^Waiting task T-\d+ can start now"), "auto", "start_possible"),
    (re.compile(r"^Open repo "), "auto", "worker_started"),
    (re.compile(r"^(Close w-\d+ |Please archive )"), "auto", "archived_observed"),
    (re.compile(r"^(Answer the pending approval of w-|Approve the pending tool request )"), "auto", "approval_cleared"),
    (re.compile(r"^Answer the P-\d+ project manager"), "auto", "manager_report"),
    (re.compile(r"^Session \S+ was started by the P-\d+ project manager"), "auto", "foreign_session_gone"),
    (re.compile(r"^Tell w-\d+ in the app"), "ask", None),
)
V2_RUNTIME_KEYS = ("uid", "note", "note_seen", "user_hold", "confirmed", "held", "last_artifact_number", "art_from",
                   "artifacts_quarantine", "done_proposed")


def _free_uid(t, taken):
    """The note uid of time t, one second later while taken."""
    while t.strftime("%Y%m%d_%H%M%S") in taken:
        t += timedelta(seconds=1)
    return t.strftime("%Y%m%d_%H%M%S")


def _uid_at(iso, taken, fallback):
    """A note uid from an ISO time (its own clock, offset ignored), one second later while taken."""
    try:
        t = datetime.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        t = datetime.strptime(fallback[:19], "%Y-%m-%dT%H:%M:%S")
    uid = _free_uid(t, taken)
    taken.add(uid)
    return uid


def _migrated_ua(ua):
    item = {"id": ua["id"], "description": ua["description"].replace("\n", " ")}
    if ua.get("since"):
        item["since"] = ua["since"]
    follow_up = ua.get("follow_up")
    if follow_up:
        item.update(rule=follow_up.get("rule"), url=follow_up.get("url"))
        if follow_up.get("done") == "pr_closed":
            item.update(kind="auto", detector="pr_closed")
        else:
            item["kind"] = "ask"
        return item
    for pattern, kind, detector in MIGRATE_UA_RULES:
        if pattern.search(item["description"]):
            return dict(item, kind=kind, **({"detector": detector} if detector else {}))
    return dict(item, kind="ask", answer_required=True)


def _artifact_groups(artifacts, first_n, cwds):
    """Number artifacts in array order from first_n and infer groups by adjacency (3.9 migration): a commit right
    after a lone branch joins it; consecutive files join when they lie under the same worker cwd (any cwd for stubs)."""
    groups = []
    for i, a in enumerate(artifacts):
        item = dict({k: a[k] for k in ART_FIELDS if k in a}, n=first_n + i)
        last = groups[-1] if groups else []
        joins = last and (
            (item["kind"] == "commit" and len(last) == 1 and last[0]["kind"] == "branch")
            or (item["kind"] == "file" and last[-1]["kind"] == "file"
                and (cwds is None or any(_under(last[-1]["path"], c) and _under(item["path"], c) for c in cwds))))
        if joins:
            last.append(item)
        else:
            groups.append([item])
    return groups


def _migrated_note(uid, title, kind, status, assignee, at, request, rev, parent=None, uas=(), groups=(), followups=(), dates=None):
    text = new_note_text(uid, title or uid, kind, status, assignee, at, request or "", parent)
    ops = [{"op": "ua_add", "ua": ua} for ua in uas] + [{"op": "art_add", "g": g[0]["n"], "items": g} for g in groups]
    text = apply_note_ops(text, ops, at, rev)[0]
    for i, f in enumerate(followups):
        quote = "\n".join("> " + line for line in f["text"].split("\n"))
        text = apply_note_ops(text, [{"op": "log", "id": f"sv-log-v1-{i}", "text": "追加の依頼:\n" + quote}], f["at"], rev)[0]
    return set_props(text, dates) if dates else text


def migrate_v2(state, now):
    """(state_v2, note_creates) for a v1 state (3.3, 3.9 migration). note_creates are {uid, path, text}. Every live
    task, project and stub gets a note (a revived task and its stub share one); project proposal user actions become
    backlog/user task notes under the project; read user actions become held. The result is deterministic: now is used
    only where the state has no time of its own."""
    if not isinstance(state, dict) or state.get("schema_version") != VIEW_SCHEMA_VERSION:
        raise StateError(f"migrate-v2 needs a schema_version {VIEW_SCHEMA_VERSION} state")
    errors = validate(state)
    if errors:
        raise StateError(errors)
    out, taken, creates, rev = copy.deepcopy(state), set(), [], state["rev"]
    at_now = state.get("updated_at") or now
    workers_by_id = {w["id"]: w for w in state["workers"]}
    stubs = {s["id"]: s for s in state["archived"]}

    def create(uid, text):
        creates.append({"uid": uid, "path": _new_note_path(uid), "text": text})
        return text

    def store(item, key, uid, text):
        for k in NOTE_MOVED_KEYS[key]:
            item.pop(k, None)
        item.update(uid=uid, note_seen=note_values(text))
        if key != "archived":
            item["note"] = _new_note_path(uid)

    project_uids = {}
    for p in out.get("projects", []):
        uid = project_uids[p["id"]] = _uid_at(p.get("created_at"), taken, now)
        proposals = [ua for ua in p.get("user_actions", []) if isinstance(ua.get("proposal"), dict)]
        rest = [ua for ua in p.get("user_actions", []) if not isinstance(ua.get("proposal"), dict)]
        status, assignee = note_props("project", dict(p, user_actions=rest), {})
        overview = f"Context document: {p['context_doc_url']}" if p.get("context_doc_url") else ""
        text = create(uid, _migrated_note(uid, p.get("name"), "project", status, assignee or "agent", p["created_at"], overview, rev,
                                          uas=[_migrated_ua(ua) for ua in rest], dates=_date_props(status, at_now)))
        for ua in proposals:
            puid = _uid_at(ua.get("since"), taken, now)
            create(puid, _migrated_note(puid, ua["proposal"]["title"], "task", "backlog", "user", ua.get("since") or at_now,
                                        ua["proposal"]["text"], rev, parent=uid))
        store(p, "projects", uid, text)
    task_uids = {}
    for t in out["tasks"]:
        uid = task_uids[t["id"]] = _uid_at(t.get("created_at"), taken, now)
        stub = stubs.get(t["id"])
        held = [wid for wid in map(_read_action_worker, t.get("user_actions", [])) if wid]
        cwds = [w["cwd"] for w in state["workers"] if w.get("task_id") == t["id"]]
        old = stub.get("artifacts", []) if stub else []
        groups = _artifact_groups(old, 1, None) + _artifact_groups(t.get("artifacts", []), len(old) + 1, cwds)
        status, assignee = note_props("task", t, workers_by_id)
        followups = (stub.get("followups", []) if stub else []) + [f for f in t.get("followups", []) if f not in (stub or {}).get("followups", [])]
        text = create(uid, _migrated_note(
            uid, t.get("title"), "task", status, assignee or "agent", t["created_at"], t.get("request"), rev,
            parent=project_uids.get(t.get("project_id")), groups=groups, followups=followups, dates=_date_props(status, at_now),
            uas=[_migrated_ua(ua) for ua in t.get("user_actions", []) if not _read_action_worker(ua)]))
        store(t, "tasks", uid, text)
        t["last_artifact_number"] = sum(len(g) for g in groups)
        if held:
            t["held"] = held
        if stub is not None:
            t["art_from"] = len(old) + 1
    for sb in out["archived"]:
        if sb["id"] in task_uids:
            uid = task_uids[sb["id"]]
            text = next(c["text"] for c in creates if c["uid"] == uid)
            last = len(sb.get("artifacts", []))
        else:
            uid = _uid_at(sb.get("archived_at"), taken, now)
            status = "archived" if sb.get("cancelled") else "done"
            groups = _artifact_groups(sb.get("artifacts", []), 1, None)
            last = sum(len(g) for g in groups)
            text = create(uid, _migrated_note(
                uid, sb.get("title"), "task", status, "user", sb.get("archived_at") or at_now, sb.get("request"), rev,
                parent=project_uids.get((sb.get("project") or {}).get("id")), groups=groups,
                followups=sb.get("followups", []), dates=_date_props(status, sb.get("archived_at") or at_now)))
        store(sb, "archived", uid, text)
        sb["last_artifact_number"] = last
        if "project" in sb:
            sb["project"] = {"id": sb["project"]["id"]}
    out["schema_version"] = SCHEMA_VERSION
    out["supervisor"].update(last_ua_number=max([_ua_number(i) for i in _ua_ids(state)] + [0]), note_warnings={},
                             note_journal=None)
    ids = id_props(out)
    for c in creates:
        if c["uid"] in ids:
            c["text"] = set_props(c["text"], _id_changes(c["text"], ids[c["uid"]]))
    notes = {c["uid"]: c["text"] for c in creates}
    errors = _storage_errors(out, notes) + validate(join_view(out, notes))
    if errors:
        raise StateError(errors)
    return out, creates


def export_v1(state, notes):
    """The v1 state.json of a v2 state and its notes ({uid: text}): the v1 view without the v2 runtime keys.
    A held worker comes back as a `Read the result of w-NNNN (held)` user action; follow-up quotes stay in the notes."""
    view = join_view(state, notes)
    for key in ("last_ua_number", "note_warnings", "note_journal"):
        view["supervisor"].pop(key, None)
    for item in view["tasks"] + view.get("projects", []) + view["archived"]:
        for key in V2_RUNTIME_KEYS:
            item.pop(key, None)
        if "user_actions" in item:
            item["user_actions"] = [{k: v for k, v in ua.items() if k not in ("detector", "needs_answer")} for ua in item["user_actions"]]
        if not item.get("followups"):
            item.pop("followups", None)
    return view


def _pr_urls(task):
    urls = []
    for a in task.get("artifacts", []):
        if a.get("kind") == "pr" and isinstance(a.get("url"), str) and a["url"] not in urls:
            urls.append(a["url"])
    return urls


def new_pr_urls(current, candidate):
    """(task_id, url) for every pr artifact url in the candidate that the same task does not have in current."""
    cur_tasks = {t.get("id"): t for t in current.get("tasks", [])}
    found = []
    for task in candidate.get("tasks", []):
        old = set(_pr_urls(cur_tasks.get(task.get("id"), {})))
        found += [(task.get("id"), url) for url in _pr_urls(task) if url not in old]
    return found


def follow_up_label(artifact, url):
    label = (artifact or {}).get("label")
    if isinstance(label, str) and label.strip():
        return label
    m = PR_URL_RE.match(url)
    return f"{m.group(1)}/{m.group(2)}#{m.group(3)}" if m else url


def follow_up_actions(state, task_id, url, rules, now, closed=False):
    """New follow-up user actions for one pr url of a task. Does not modify state.
    A rule whose (rule, url) pair the task already has is skipped, and so is every pr_closed rule when closed."""
    task = next((t for t in state.get("tasks", []) if t.get("id") == task_id), None)
    if task is None:
        raise StateError(f"task {task_id} not found")
    artifact = next((a for a in task.get("artifacts", []) if a.get("kind") == "pr" and a.get("url") == url), None)
    label = follow_up_label(artifact, url)
    have = {(ua["follow_up"].get("rule"), ua["follow_up"].get("url"))
            for ua in task.get("user_actions", []) if isinstance(ua.get("follow_up"), dict)}
    next_num = _next_ua_number(state)
    actions = []
    for rule in rules:
        if (rule["id"], url) in have or (closed and rule["done"] == "pr_closed"):
            continue
        actions.append({
            "id": f"UA-{next_num:04d}",
            "description": re.sub(r"\{(label|url)\}", lambda m: label if m.group(1) == "label" else url, rule["text"]),
            "since": now,
            "follow_up": {"rule": rule["id"], "url": url, "done": rule["done"]},
        })
        next_num += 1
    return actions


def _project(state, project_id):
    project = next((p for p in _projects(state) if p.get("id") == project_id), None)
    if project is None:
        raise StateError(f"project {project_id} not found")
    return project


def parse_project_report(text, expect_project=None):
    """Extract the last project-report block of a project manager's message."""
    blocks = PROJECT_REPORT_RE.findall(text)
    if not blocks:
        return {"ok": False, "error": "no project-report block found"}
    try:
        report = json.loads(blocks[-1])
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"invalid JSON in project-report block: {e}"}
    if not isinstance(report, dict):
        return {"ok": False, "error": "project-report must be a JSON object"}
    missing = [f for f in ("project_id", "status", "summary", "reviews", "plans", "questions") if f not in report]
    if missing:
        return {"ok": False, "error": f"missing fields: {missing}"}
    if report["status"] not in MANAGER_REPORT_STATUSES:
        return {"ok": False, "error": f"invalid status {report['status']!r}"}
    if not isinstance(report["project_id"], str) or not isinstance(report["summary"], str):
        return {"ok": False, "error": "project_id and summary must be strings"}
    if not isinstance(report["questions"], list) or not all(isinstance(q, str) and q for q in report["questions"]):
        return {"ok": False, "error": "questions must be a list of non-empty strings"}
    if report["status"] == "needs_input" and not report["questions"]:
        return {"ok": False, "error": "status needs_input requires at least one question"}
    if not isinstance(report["reviews"], list) or not isinstance(report["plans"], list):
        return {"ok": False, "error": "reviews and plans must be lists"}
    for r in report["reviews"]:
        if not isinstance(r, dict) or _id_number(REVIEW_ID_RE, r.get("review_id")) is None:
            return {"ok": False, "error": "each review needs review_id R-NNNN"}
        if r.get("verdict") not in ("approve", "reject"):
            return {"ok": False, "error": f"review {r['review_id']}: verdict must be approve or reject"}
        if not all(isinstance(r.get(k, ""), str) for k in ("comment", "redo")):
            return {"ok": False, "error": f"review {r['review_id']}: comment and redo must be strings"}
        if r["verdict"] == "reject" and not r.get("redo", "").strip():
            return {"ok": False, "error": f"review {r['review_id']}: reject requires a non-empty redo"}
    for plan in report["plans"]:
        kind = plan.get("kind") if isinstance(plan, dict) else None
        keys = {"redo": ("task_id", "text"), "new": ("title", "text")}.get(kind)
        if keys is None:
            return {"ok": False, "error": "each plan needs kind redo or new"}
        if not _fields_ok(plan, keys) or not isinstance(plan.get("worker_id", ""), str):
            return {"ok": False, "error": f"a {kind} plan needs non-empty {' and '.join(keys)} (worker_id, when given, a string)"}
    if "done" in report and not (isinstance(report["done"], str) and report["done"].strip()):
        return {"ok": False, "error": "done, when given, must be a non-empty summary string"}
    if expect_project is not None and report["project_id"] != expect_project:
        return {"ok": False, "error": f"project_id {report['project_id']!r} does not match {expect_project!r}"}
    return {"ok": True, "report": report}


def _live(worker):
    # A worker that can take a message right now.
    return worker.get("intended_state") == "running" and worker.get("observed_state") in ("running", "idle", "needs_input")


def _review_action(worker, verdict, running):
    review = worker["review"]
    if review.get("state") == "requested" and not worker.get("replaced_by"):
        matches = ((worker.get("last_report") or {}).get("message_ref") == review.get("message_ref")
                   or worker.get("observed_state") in ("archived", "missing"))
        if matches and worker["id"] not in running:
            return f"{verdict}_ok"
        if review.get("stale_notified") != review.get("message_ref"):
            return "stale_notify"
    return "stale_silent"


def plan_actions(state, project_id, report, running=(), proposal_titles=()):
    """Turn a project-report's reviews and plans into the operations the supervisor performs. Does not modify state.
    `running` holds the worker ids the supervisor just found running with get. For a project with a note (uid), a new
    task proposal becomes proposal_note (a backlog/user task note with parent); proposal_titles are the titles of its
    backlog/user child notes, which make a proposal with the same title a duplicate."""
    project = _project(state, project_id)
    tasks = {t["id"]: t for t in state.get("tasks", [])}
    workers_by_id = {w["id"]: w for w in state.get("workers", [])}
    by_review = {w["review"]["id"]: w for w in state.get("workers", [])
                 if isinstance(w.get("review"), dict) and tasks.get(w.get("task_id"), {}).get("project_id") == project_id}
    actions = []
    for r in report.get("reviews", []):
        base = {"review_id": r["review_id"], "verdict": r["verdict"], "comment": r.get("comment", ""), "redo": r.get("redo", "")}
        w = by_review.get(r["review_id"])
        if w is None or tasks[w["task_id"]].get("cancel"):
            reason = "no such review in this project" if w is None else "the task was cancelled"
            actions.append(dict(base, kind="stale_silent", reason=reason))
            continue
        actions.append(dict(base, kind=_review_action(w, r["verdict"], set(running)), worker_id=w["id"], task_id=w["task_id"]))
    stubs = {s["id"]: s for s in state.get("archived", [])}
    open_titles = {ua["proposal"]["title"] for ua in project.get("user_actions", []) if isinstance(ua.get("proposal"), dict)}
    open_titles |= set(proposal_titles)
    seen_redos = set()
    for plan in report.get("plans", []):
        if plan["kind"] == "new":
            dup = plan["title"] in open_titles
            open_titles.add(plan["title"])
            action = {"kind": "proposal_dup" if dup else "proposal_add", "title": plan["title"], "text": plan["text"]}
            if project.get("uid") and not dup:
                action.update(kind="proposal_note", parent=project["uid"])
            actions.append(action)
            continue
        tid, text = plan["task_id"], plan["text"]
        action = {"task_id": tid, "text": text}
        task = tasks.get(tid)
        if task is None:
            mine = (stubs.get(tid, {}).get("project") or {}).get("id") == project_id
            actions.append(dict(action, kind="redo_archived" if mine else "redo_foreign"))
            continue
        if task.get("project_id") != project_id:
            actions.append(dict(action, kind="redo_foreign"))
            continue
        if (tid, text) in seen_redos or any(x.get("text") == text for x in task.get("redos", [])):
            actions.append(dict(action, kind="redo_dup"))
            continue
        seen_redos.add((tid, text))
        live = [w for w in (workers_by_id.get(wid) for wid in task.get("worker_ids", [])) if w and _live(w)]
        target = next((w for w in live if w["id"] == plan.get("worker_id")), None)
        if target is None and len(live) == 1:
            target = live[0]
        if target is not None:
            actions.append(dict(action, kind="redo_send", worker_id=target["id"]))
        else:
            actions.append(dict(action, kind="redo_ambiguous" if live else "redo_start",
                                worker_ids=[w["id"] for w in live]))
    if report.get("done"):
        actions.append({"kind": "project_done", "summary": report["done"]})
    return actions


def _parent_uids(value):
    """Note uids a parent property links: "[[uid]]", a path ending in uid.md, or a list of those."""
    return set(re.findall(r"\d{8}_\d{6}", json.dumps(value)))


def proposal_titles(texts, project_uid):
    """Titles of the backlog/user task notes whose parent is the project note: proposals waiting for the user."""
    titles = set()
    for text in texts.values():
        props = parse_frontmatter(text)[0]
        tags = props.get("tags") if isinstance(props.get("tags"), list) else [props.get("tags")]
        if ("task" in tags and (props.get("status"), props.get("assignee")) == ("backlog", "user")
                and project_uid in _parent_uids(props.get("parent")) and isinstance(props.get("title"), str)):
            titles.add(props["title"])
    return titles


def _cell(value):
    return ("-" if value in (None, "") else str(value)).replace("|", "\\|").replace("\n", " ")


def roster(state, project_id):
    """Markdown roster of a project's workers and open reviews, sent to its manager session."""
    project = _project(state, project_id)
    tasks = {t["id"]: t for t in state.get("tasks", []) if t.get("project_id") == project_id}
    workers = [w for w in state.get("workers", []) if w.get("task_id") in tasks]
    lines = [f"### Workers of {project_id} {project['name']}", ""]
    if not workers:
        lines.append("(no workers)")
    else:
        lines += ["| worker | task | task title | worker title | cwd | session_id | observed | report | review |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for w in workers:
            review = w.get("review") or {}
            rv = f"{review['id']} {review['state']}" if review else ""
            lines.append("| " + " | ".join(_cell(v) for v in (
                w["id"], w["task_id"], tasks[w["task_id"]].get("title"), w.get("title"), w.get("cwd"), w.get("session_id"),
                w.get("observed_state"), (w.get("last_report") or {}).get("status"), rv)) + " |")
    lines += ["", "### Open reviews", ""]
    open_reviews = [w for w in workers if (w.get("review") or {}).get("state") == "requested" and not w.get("replaced_by")
                    and not tasks[w["task_id"]].get("cancel")]
    if not open_reviews:
        lines.append("(none)")
    else:
        lines += ["| review | worker | task | ref | requested at |", "| --- | --- | --- | --- | --- |"]
        for w in open_reviews:
            r = w["review"]
            lines.append("| " + " | ".join(_cell(v) for v in (r["id"], w["id"], w["task_id"], r.get("ref"), r.get("at"))) + " |")
    return "\n".join(lines) + "\n"


def _parse_time(value):
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()


def waits(state, now, hub_minutes=HUB_WAIT_MINUTES, review_minutes=REVIEW_WAIT_MINUTES):
    """Workers whose wait for their project manager crossed a threshold and has no wait user action yet."""
    now_t = _parse_time(now)
    if now_t is None:
        raise StateError(f"--now {now!r} is not an ISO time")
    tasks = {t["id"]: t for t in state.get("tasks", []) if t.get("project_id")}
    found = []
    for w in state.get("workers", []):
        task = tasks.get(w.get("task_id"))
        if task is None or task.get("cancel") or w.get("replaced_by"):
            continue
        descs = [ua.get("description", "") for ua in task.get("user_actions", [])]
        base = {"worker_id": w["id"], "task_id": task["id"], "project_id": task["project_id"]}
        report = w.get("last_report") or {}
        if (report.get("status") == "awaiting_manager" and w.get("observed_state") in ("idle", "needs_input")
                and not any(d.startswith(HUB_WAIT_PREFIX.format(wid=w["id"])) for d in descs)):
            since = _parse_time(w.get("awaiting_since"))
            if since and (now_t - since).total_seconds() > hub_minutes * 60:
                found.append(dict(base, kind="hub", since=w["awaiting_since"]))
        review = w.get("review") or {}
        if review.get("state") == "requested" and not any(d.startswith(REVIEW_WAIT_PREFIX.format(wid=w["id"])) for d in descs):
            since = _parse_time(review.get("at"))
            if since and (now_t - since).total_seconds() > review_minutes * 60:
                found.append(dict(base, kind="review", review_id=review["id"], since=review["at"]))
    return found


def _row_value(row, *keys):
    return next((row[k] for k in keys if k in row), None)


def _row_is(row, flag):
    keys = {"archived": ("isArchived", "archived"), "running": ("isRunning", "running")}[flag]
    return bool(_row_value(row, *keys)) or row.get("status") == flag


def manager_reconcile(manager, rows, events=None):
    """Decisions for one manager session from list rows (include_archived) and an optional summary of its latest events.
    rows: objects with sessionId (or session_id / id), startedBy, and running / archived flags.
    events: {"last_message_ref": str | null, "pending_approval": bool}; without it, handle_turn and pending_approval are not judged."""
    sid = manager.get("session_id")
    previous = set(manager.get("previous_session_ids", []))
    decisions = []
    row = None
    for r in rows:
        rid = _row_value(r, "sessionId", "session_id", "id")
        archived = _row_is(r, "archived")
        # Listed until archived, so its user action is resolved by observing the archive.
        if _row_value(r, "startedBy", "started_by") in previous | {sid} - {None} and not archived:
            decisions.append({"decision": "foreign_session", "session_id": rid})
        if rid in previous and not archived:
            decisions.append({"decision": "stale_previous", "session_id": rid})
        if sid is not None and rid == sid:
            row = r
    if sid is None or manager.get("intended_state") == "pending":
        return decisions
    if row is None:
        if manager.get("intended_state") == "archived":
            if manager.get("observed_state") not in ("archived", "missing"):
                decisions.append({"decision": "update", "observed_state": "missing"})
        else:
            decisions.append({"decision": "missing", "session_id": sid})
        return decisions
    if _row_is(row, "archived"):
        if manager.get("intended_state") == "archived":
            decisions.append({"decision": "update", "observed_state": "archived"})
        else:
            decisions.append({"decision": "external_archive", "session_id": sid})
        return decisions
    running = _row_is(row, "running")
    observed = "running" if running else "idle"
    if not running and events and events.get("pending_approval"):
        observed = "needs_input"
        decisions.append({"decision": "pending_approval", "session_id": sid})
    decisions.append({"decision": "update", "observed_state": observed})
    last = (events or {}).get("last_message_ref")
    if not running and last and last != (manager.get("last_report") or {}).get("message_ref"):
        decisions.append({"decision": "handle_turn", "message_ref": last})
    return decisions


# ---------------------------------------------------------------- imperative shell


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_tmp(dir_path, text):
    fd, tmp = tempfile.mkstemp(dir=dir_path, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        os.unlink(tmp)
        raise
    return tmp


def _write_atomic(path, text):
    os.replace(_write_tmp(os.path.dirname(os.path.abspath(path)), text), path)


def _default_html(state_path):
    return os.path.join(os.path.dirname(os.path.abspath(state_path)), "dashboard.html")


def cmd_version(args):
    h = hashlib.sha256()
    for name in (Path(__file__).name, TEMPLATE_NAME):
        h.update((HERE / name).read_bytes())
    print(h.hexdigest()[:12])
    return 0


def _write_state_and_dashboard(state_path, html_path, new, view, template):
    """Write the dashboard temp file, replace state.json, then the dashboard. Returns the result dict."""
    body, footer = render(view, template)
    old_body = body_of(Path(html_path).read_text(encoding="utf-8")) if os.path.exists(html_path) else None
    html_tmp = _write_tmp(os.path.dirname(os.path.abspath(html_path)), assemble(body, footer))
    try:
        state_tmp = _write_tmp(os.path.dirname(os.path.abspath(state_path)), serialize(new))
        try:
            os.replace(state_tmp, state_path)
        except BaseException:
            os.unlink(state_tmp)
            raise
    except BaseException:
        os.unlink(html_tmp)
        raise
    result = {"rev": new["rev"], "dashboard_changed": old_body != body}
    try:
        os.replace(html_tmp, html_path)
    except OSError as e:
        # The state is already committed, so report success; the stale HTML makes the
        # next commit see a changed body and rewrite it.
        with contextlib.suppress(OSError):
            os.unlink(html_tmp)
        result.update(dashboard_changed=True, dashboard_error=str(e))
        print(f"warning: state committed at rev {new['rev']} but the dashboard was not written: {e}", file=sys.stderr)
    return result


def cmd_commit(args):
    html_path = args.html or _default_html(args.state)
    template = Path(args.template).read_text(encoding="utf-8")
    current = _read_json(args.state)
    if isinstance(current, dict) and current.get("schema_version") == SCHEMA_VERSION:
        result = _commit_v2(args, current, html_path, template)
    else:
        # A v1 state.json keeps the v1 flow until migrate-v2 converts it.
        if args.notes:
            raise StateError("--notes needs a schema_version 2 state")
        new = next_state(current, _read_json(args.candidate), _now())
        result = _write_state_and_dashboard(args.state, html_path, new, new, template)
    print(json.dumps(result, ensure_ascii=False))
    return 0


VAULT_DIR = "vault"
VAULT_SKIP = ("_templates",)
DASHBOARD_NOTE = "Supervisor.md"


def _vault_path(state_path):
    return os.path.join(os.path.dirname(os.path.abspath(state_path)), VAULT_DIR)


def _new_note_path(uid):
    return f"{uid[:4]}/{uid[4:6]}/{uid}.md"


def _index_vault(vault):
    """({relpath: text}, {uid: [relpath, ...]}) of the vault's notes, without _templates/, hidden folders and Supervisor.md."""
    texts, uids = {}, {}
    if not os.path.isdir(vault):
        return texts, uids
    for root, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (root == vault and d in VAULT_SKIP))
        for name in sorted(files):
            rel = Path(os.path.relpath(os.path.join(root, name), vault)).as_posix()
            if not name.endswith(".md") or rel == DASHBOARD_NOTE:
                continue
            try:
                text = Path(root, name).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            texts[rel] = text
            uid = parse_frontmatter(text)[0].get("uid")
            if isinstance(uid, str) and uid:
                uids.setdefault(uid, []).append(rel)
    return texts, uids


def _notes_by_uid(texts, uids):
    return {uid: texts[paths[0]] for uid, paths in uids.items()}


def _write_note(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_atomic(path, text)


def _load_view(state_path):
    """The v1 view of state.json: a v2 state joined with its vault notes, a v1 state as it is."""
    state = _read_json(state_path)
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        return state
    try:
        texts, uids = _index_vault(_vault_path(state_path))
    except OSError:
        texts, uids = {}, {}
    return join_view(state, _notes_by_uid(texts, uids))


def _entry_rev(entry, journal):
    # An entry carried over after a failed write keeps the rev of the commit that made its ops (ids and marks).
    return entry.get("rev", journal["rev"])


def _unreflected(state, texts, uids):
    """Journal entries of the last commit whose notes do not show their write yet, in journal order."""
    journal = (state.get("supervisor") or {}).get("note_journal")
    if not journal or journal.get("rev") != state.get("rev"):
        return []
    return [e for e in journal.get("entries", [])
            if not journal_reflected(texts.get((uids.get(e.get("uid")) or [e.get("path")])[0]), e, _entry_rev(e, journal))]


def _pending_entries(state, texts, uids):
    """The first unreflected journal entry of each note, by uid (its before values are what the note still shows)."""
    pending = {}
    for entry in _unreflected(state, texts, uids):
        pending.setdefault(entry.get("uid"), entry)
    return pending


def _replay_journal(state, vault, texts, uids, warnings):
    """Re-apply each unreflected entry of the last commit once (3.5 step 1). Returns the entries to carry over."""
    journal = (state.get("supervisor") or {}).get("note_journal")
    if not journal or journal.get("rev") != state.get("rev"):
        return []
    carried = []
    for entry in _unreflected(state, texts, uids):
        uid = entry.get("uid")
        rel = (uids.get(uid) or [entry["path"]])[0]
        text = texts.get(rel)
        new_text, _, _ = apply_note_ops(text or "", entry.get("ops", []), entry.get("at") or _now(), _entry_rev(entry, journal))
        if entry.get("props") and _pair(note_values(text or new_text)) == _pair(entry.get("before") or note_values(new_text)):
            new_text = set_props(new_text, entry["props"])
        try:
            _write_note(os.path.join(vault, rel), new_text)
        except OSError as e:
            tries = entry.get("tries", 0) + 1
            if tries >= 2:
                warnings.append(f"note {rel}: dropped the unwritten changes of rev {_entry_rev(entry, journal)} after {tries} tries ({e})")
            else:
                carried.append(dict(entry, tries=tries, rev=_entry_rev(entry, journal)))
            continue
        texts[rel] = new_text
        uids.setdefault(uid, [rel])
    return carried


def _ops_arg(path):
    ops = _read_json(path) if path else {}
    if not isinstance(ops, dict) or not all(isinstance(v, list) and all(isinstance(o, dict) for o in v) for v in ops.values()):
        raise StateError("--notes must be a JSON object {uid: [op, ...]}")
    return ops


def _numbered_ops(raw_ops, candidate, base, view_cur):
    next_ua = max(_next_ua_number(view_cur), (candidate.get("supervisor") or {}).get("last_ua_number", 0) + 1)
    art_last = {}
    for key in ("tasks", "archived"):
        for item in candidate.get(key, []):
            art_last[item.get("uid")] = max(art_last.get(item.get("uid"), 0), item.get("last_artifact_number", 0))
    for uid in raw_ops:
        rows = parse_artifacts(parse_sections(parse_frontmatter(base.get(uid) or "")[1]).get(ART_SECTION, ""))[0]
        art_last[uid] = max([art_last.get(uid, 0)] + [r["n"] for r in rows])
    return number_note_ops(raw_ops, next_ua, art_last)


def _date_props(status, now):
    if status == "done":
        return {"completed_at": now[:10]}
    if status == "archived":
        return {"archived_at": f"{now[:10]} {now[11:19]}"}
    return {}


def _commit_v2(args, current, html_path, template):
    now = _now()
    vault = _vault_path(args.state)
    candidate = _read_json(args.candidate)
    if not isinstance(candidate, dict):
        raise StateError("candidate must be a JSON object")
    raw_ops = _ops_arg(args.notes)
    rev = current["rev"] + 1
    warnings = []
    texts, uids = _index_vault(vault)
    # 1. Re-apply the last commit's note writes that did not happen.
    carried = _replay_journal(current, vault, texts, uids, warnings)
    pending = {e["uid"]: e for e in carried}

    # 2. Locate the notes and check the acks the supervisor copied into note_seen.
    paths = {uid: rels[0] for uid, rels in uids.items()}
    for uid, ops in raw_ops.items():
        creates = sum(o.get("op") == "create" for o in ops)
        if creates > 1:
            raise StateError(f"note {uid}: {creates} create ops; reserve one uid per note with new-note --count")
        if creates and (uid in uids or os.path.exists(os.path.join(vault, _new_note_path(uid)))):
            raise StateError(f"note {uid} already exists; reserve a new uid with new-note")
        if len(uids.get(uid, [])) > 1:
            raise StateError(f"note uid {uid} is used by {uids[uid]}")
        if uid in paths:
            continue
        given = next((o.get("path") for o in ops if o.get("op") == "assign_uid"), None)
        if given in texts:
            paths[uid] = given
        elif creates:
            paths[uid] = _new_note_path(uid)
        else:
            raise StateError(f"note {uid} not found in {vault}")
    notes = _notes_by_uid(texts, uids)
    base = {uid: texts.get(rel) for uid, rel in paths.items()}
    seen_store, cur_items = {}, {}
    for key in ("tasks", "projects", "archived"):
        cur_items.update({(key, i.get("id")): i for i in current.get(key, [])})
        for item in candidate.get(key, []):
            uid, seen = item.get("uid"), item.get("note_seen")
            if seen is not None:
                seen_store.setdefault(uid, seen)
            if seen is not None and seen != cur_items.get((key, item.get("id")), {}).get("note_seen"):
                values = note_values(base.get(uid) or "") if base.get(uid) else None
                if values != {k: seen.get(k) for k in SEEN_KEYS}:
                    raise StateError(f"note {uid} changed after notes-scan; run notes-scan again and rebuild the candidate")
    seen_cmp = dict(seen_store, **{uid: e.get("before") for uid, e in pending.items()})

    # 3. Apply the ops in memory.
    view_cur = join_view(current, notes)
    ops = _numbered_ops(raw_ops, candidate, base, view_cur)
    after, new_ids, problems = dict(base), {}, []
    for uid, items in ops.items():
        after[uid], new_ids[uid], probs = apply_note_ops(base.get(uid) or "", items, now, rev)
        problems += [f"{paths[uid]}: {p}" for p in probs]
    notes_after = dict(notes, **{uid: text for uid, text in after.items() if text})

    # 4. Views and validation.
    view_cand = join_view(candidate, notes_after)
    view_new = next_state(view_cur, view_cand, now)

    # 5. Note props, including the tasks this commit archives (they are stubs in view_new).
    live_ids = {t["id"] for t in view_new["tasks"]}
    workers_new = {w["id"]: w for w in view_new["workers"]}
    workers_cand = {w["id"]: w for w in view_cand["workers"]}
    targets = ([("task", t, workers_new) for t in view_new["tasks"]] + [("project", p, {}) for p in view_new.get("projects", [])]
               + [("task", t, workers_cand) for t in view_cand["tasks"] if t["id"] not in live_ids])
    props, deferred = {}, []
    for kind, item, workers in targets:
        uid = item.get("uid")
        if not after.get(uid):
            continue
        derived = note_props(kind, item, workers)
        if derived is None:
            continue
        cur_pair = _pair(note_values(after[uid]))
        seen = seen_cmp.get(uid)
        if seen is not None and base.get(uid) and _pair(note_values(base[uid])) != _pair(seen):
            deferred.append(uid)
            continue
        changes = {}
        if derived[0] != cur_pair[0]:
            changes["status"] = derived[0]
            if derived[0] not in ("done", "archived") and parse_frontmatter(after[uid])[0].get("completed_at"):
                changes["completed_at"] = None
        # The date is set when the note reaches done / archived, also when the user moved it there first.
        changes.update({k: v for k, v in _date_props(derived[0], now).items() if not parse_frontmatter(after[uid])[0].get(k)})
        if derived[1] and derived[1] != cur_pair[1]:
            changes["assignee"] = derived[1]
        if changes:
            try:
                after[uid] = set_props(after[uid], changes)
                props[uid] = changes
            except StateError as e:
                problems.append(f"{paths[uid]}: props not written ({e})")
    for uid, ids in id_props(view_new).items():
        changes = _id_changes(after[uid], ids) if after.get(uid) else {}
        if changes:
            try:
                after[uid] = set_props(after[uid], changes)
                props[uid] = dict(props.get(uid, {}), **changes)
            except StateError as e:
                problems.append(f"{paths[uid]}: props not written ({e})")

    # 6. Storage form, the ops commit itself adds, note_seen and the journal.
    state_new, ops_all = split_view(view_cand, view_new, ops)
    for uid, items in ops_all.items():
        extra = items[len(ops.get(uid, [])):]
        if extra and after.get(uid):
            after[uid], ids, probs = apply_note_ops(after[uid], extra, now, rev)
            new_ids[uid] = new_ids.get(uid, []) + ids
            problems += [f"{paths[uid]}: {p}" for p in probs]
    written = sorted(uid for uid in after if after[uid] and after[uid] != base.get(uid))
    new_seen = {}
    for uid in written:
        values, old, seen = note_values(after[uid]), note_values(base[uid]) if base.get(uid) else None, seen_cmp.get(uid)
        if seen is None or values is None:
            new_seen[uid] = values
            continue
        merged = dict(seen)
        if uid not in deferred:
            merged.update(status=values["status"], assignee=values["assignee"])
        # Our own section writes are acked, a user's unhandled section change is left for the next scan.
        for key in ("ua_sha", "art_sha"):
            if old is None or old[key] == seen.get(key):
                merged[key] = values[key]
        new_seen[uid] = merged
    cand_tasks = {t["id"]: t for t in candidate.get("tasks", [])}
    for key in ("tasks", "projects", "archived"):
        for item in state_new.get(key, []):
            uid = item.get("uid")
            if key == "archived" and "note_seen" not in item and cand_tasks.get(item["id"], {}).get("uid") == uid:
                item["note_seen"] = seen_store.get(uid)
            if uid in new_seen:
                item["note_seen"] = new_seen[uid]
            if key != "archived" and uid in paths:
                item["note"] = paths[uid]
    entries = list(carried) + [{
        "uid": uid, "path": paths[uid], "before": seen_cmp.get(uid), "after": new_seen.get(uid),
        "ops": ops_all.get(uid, []), "props": props.get(uid, {}), "new_ids": new_ids.get(uid, []),
        "after_sha": _text_sha(after[uid]), "tries": 0, "at": now, "rev": rev,
    } for uid in written]
    state_new["supervisor"]["note_journal"] = {"rev": rev, "entries": entries} if entries else None
    errors = _storage_errors(state_new, dict(notes_after, **{uid: after[uid] for uid in written}))
    if errors:
        raise StateError(errors)

    # 7. state.json (with the journal) first, then the notes.
    result = _write_state_and_dashboard(args.state, html_path, state_new, view_new, template)
    written_paths, failed = [], []
    for uid in written:
        target = os.path.join(vault, paths[uid])
        try:
            now_text = Path(target).read_text(encoding="utf-8") if os.path.exists(target) else None
            text = after[uid]
            if now_text != base.get(uid):
                # The user saved the note meanwhile: build on what is there now.
                text = apply_note_ops(now_text or "", ops_all.get(uid, []), now, rev)[0]
                if props.get(uid) and _pair(note_values(now_text or text)) == _pair(seen_cmp.get(uid) or note_values(text)):
                    text = set_props(text, props[uid])
            _write_note(target, text)
            written_paths.append(paths[uid])
        except (OSError, StateError):
            failed.append(paths[uid])
    result.update(notes_written=written_paths, notes_deferred=[paths[u] for u in deferred],
                  notes_failed=failed, note_problems=problems)
    if warnings:
        result["warnings"] = warnings
    return result


def cmd_digest(args):
    try:
        state = _load_view(args.state)
        if not isinstance(state, dict):
            raise ValueError("state is not a JSON object")
        print(digest(state, os.path.abspath(args.state)))
    except Exception as e:  # The hook must never fail the session start.
        print(f"[session-supervisor] state not readable at {args.state} ({type(e).__name__}: {e}); use the session-supervisor skill to recover.")
    return 0


def cmd_parse_report(args):
    text = Path(args.file).read_text(encoding="utf-8") if args.file != "-" else sys.stdin.read()
    result = parse_report(text, args.expect_task, args.expect_worker, args.allow_awaiting_manager)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def cmd_check_cwd(args):
    print("busy" if cwd_busy(_load_view(args.state), _real(args.cwd)) else "free")
    return 0


def _inside_git(path):
    p = Path(path).resolve()
    for candidate in (p, *p.parents):
        if (candidate / ".git").exists():
            return True
    return False


CLAUDE_MD = """# Session supervisor directory

This directory belongs to the session-supervisor skill. It is not a project.

- Use the session-supervisor skill first, and read the state with `python3 .supervisor/state_tool.py digest --state state.json`.
- Write `state.json` only through `python3 .supervisor/state_tool.py commit`.
- `supervisor.archive_consent` in `state.json` is the user's explicit consent that archiving requires. It covers only worker sessions whose `startedBy` is in `supervisor.session_ids`.
- Never delete sessions.
- The supervisor only operates sessions, changes state through commit, publishes the dashboard, and talks to the user. Workers edit, commit, push, and open PRs; observer subagents run read-only checks and investigations, write results under .supervisor/observations/, and return the path.
- A PreToolUse hook (state_tool.py guard) blocks edits outside this directory and mutating git / jj / gh commands. The one allowed exception is `state_tool.py prepare-dir`, which writes only under the `## Locations` roots. To disable it, remove its entry from .claude/settings.json by hand; the supervisor asks before restoring a missing entry.
- `vault/` is an Obsidian vault with one note per task and project: the note holds the meaning (title, `## 概要`, status, assignee, `## ユーザーアクション`, `## 成果物`). Only `python3 .supervisor/state_tool.py commit --notes <ops.json>` writes it (the guard blocks edits); read changes with `notes-scan` and a note with `note-show`. `vault/Supervisor.md` is the kanban dashboard: moving a card changes the note's status. Delegate a task by setting a note to `backlog` / `agent`.
- `## Follow-ups` (optional) turns each newly verified PR into a user action such as "Review org/repo#42". Format: references/follow-ups-format.md in the session-supervisor skill. Check it with `python3 .supervisor/state_tool.py validate-follow-ups --claude-md CLAUDE.md`.

<!-- Example: remove the comment markers to enable it.
## Follow-ups

### review-pr
- on: pr
- text: Review {label}
- done: pr_closed
-->
"""


def cmd_setup(args):
    target = Path(os.path.abspath(os.path.expanduser(args.dir)))
    if _inside_git(target):
        raise StateError(f"{target} is inside a git repository; choose a directory outside any repository")
    claude_md = target / "CLAUDE.md"
    old_md = claude_md.read_text(encoding="utf-8") if claude_md.exists() else None
    new_md = add_missing_locations(CLAUDE_MD if old_md is None else old_md, str(target))
    settings_path = target / ".claude" / "settings.json"
    settings = _read_settings(settings_path, ("SessionStart", "PreToolUse"), "setup")

    report = []
    sv_dir = target / ".supervisor"
    sv_dir.mkdir(parents=True, exist_ok=True)
    for name in (Path(__file__).name, TEMPLATE_NAME):
        dest = sv_dir / name
        src_bytes = (HERE / name).read_bytes()
        status = "exists" if dest.exists() and dest.read_bytes() == src_bytes else ("updated" if dest.exists() else "created")
        if status != "exists":
            shutil.copyfile(HERE / name, dest)
        report.append((status, dest))

    state_path = target / "state.json"
    script = sv_dir / Path(__file__).name
    wanted = (
        ("SessionStart", "startup|resume|clear|compact", f"python3 {shlex.quote(str(script))} digest --state {shlex.quote(str(state_path))}"),
        ("PreToolUse", GUARD_MATCHER, guard_hook_command(script, target)),
    )
    # A model the user already chose is kept; the Capability check reports a non-opus supervisor.
    report.append((_install_hooks(settings_path, settings, wanted, {"model": SUPERVISOR_MODEL}), settings_path))

    if state_path.exists():
        report.append(("exists", state_path))
    else:
        _write_atomic(str(state_path), serialize(empty_state(str(target))))
        report.append(("created", state_path))

    report += _setup_vault(target / VAULT_DIR)

    if old_md is None:
        claude_md.write_text(new_md, encoding="utf-8")
        report.append(("created", claude_md))
    elif new_md != old_md:
        _write_atomic(str(claude_md), new_md)
        report.append(("updated", claude_md))
    else:
        report.append(("exists", claude_md))

    for status, path in report:
        print(f"{status}: {path}")
    return 0


def _kanban_base(name, assignee):
    return f"""```base
filters:
  and:
    - or:
        - file.hasTag("task")
        - file.hasTag("project")
    - '!file.inFolder("_templates")'
    - 'assignee == "{assignee}"'
formulas:
  title: link(file.name, title)
views:
  - type: kanban
    name: {name}
    groupBy:
      property: status
      direction: ASC
    order: [formula.title, task_id, project_id, created_at]
    groupOrder: [backlog, in progress, pending, done, archived]
    hideEmptyGroups: false
```
"""


SUPERVISOR_NOTE = f"## ユーザーの番\n\n{_kanban_base('ユーザー', 'user')}\n## エージェントの番\n\n{_kanban_base('エージェント', 'agent')}"
ART_SECTION_NOTE = ("<!-- state_tool が書く行です。表示テキストは直せますが、メタデータ(sv-art)のある行に書き足した文字は、"
                    "成果物のグループを置き換えるときに行ごと消えます。 -->")


def note_template(kind):
    """A core Templates template for a task or project note: backlog / user, uid from the creation time."""
    text = new_note_text("UIDX", "TITLEX", kind, "backlog", "user", "2000-01-01T00:00:00")
    text = text.replace('"UIDX"', '"{{date:YYYYMMDD}}_{{time:HHmmss}}"').replace('"TITLEX"', '"{{title}}"')
    text = text.replace("2000-01-01 00:00:00", "{{date:YYYY-MM-DD}} {{time:HH:mm:ss}}")
    return text.replace(f"## {ART_SECTION}\n\n", f"## {ART_SECTION}\n\n{ART_SECTION_NOTE}\n\n")


def _setup_vault(vault):
    """Create the vault files that are missing; never overwrite one."""
    files = (
        (vault / DASHBOARD_NOTE, SUPERVISOR_NOTE),
        (vault / "_templates" / "task.md", note_template("task")),
        (vault / "_templates" / "project.md", note_template("project")),
        (vault / ".obsidian" / "templates.json", json.dumps({"folder": "_templates"}) + "\n"),
    )
    report = []
    for path, text in files:
        if path.exists():
            report.append(("exists", path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(str(path), text)
            report.append(("created", path))
    return report


def guard_hook_command(script, directory, role=None):
    # The guard exits 3 on a block; anything else (missing script, stale copy, crash) must not block the call.
    role_arg = f" --role {role}" if role else ""
    return (f"python3 {shlex.quote(str(script))} guard --dir {shlex.quote(str(directory))}{role_arg}; "
            "[ $? -eq 3 ] && exit 2; exit 0")


def _read_settings(settings_path, events, rerun):
    settings_path = Path(settings_path)
    if not settings_path.exists():
        return {}
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StateError(f"{settings_path} is not valid JSON ({e}); fix it and rerun {rerun}")
    if not isinstance(settings, dict):
        raise StateError(f"{settings_path} must contain a JSON object")
    hooks = settings.get("hooks", {})
    for event in events:
        entries = hooks.get(event, []) if isinstance(hooks, dict) else None
        if not isinstance(entries, list) or not all(
            isinstance(e, dict) and isinstance(e.get("hooks", []), list) and all(isinstance(h, dict) for h in e.get("hooks", []))
            for e in entries
        ):
            raise StateError(f"{settings_path}: hooks.{event} must be a list of hook entries; fix it and rerun {rerun}")
    return settings


def _install_hooks(settings_path, settings, wanted, defaults=None):
    """Add each missing (event, matcher, command) hook and each missing default key, keeping existing entries.
    Returns created, updated or exists."""
    changed = False
    for event, matcher, command in wanted:
        entries = settings.setdefault("hooks", {}).setdefault(event, [])
        if not any(h.get("command") == command for e in entries for h in e.get("hooks", [])):
            entries.append({"matcher": matcher, "hooks": [{"type": "command", "command": command}]})
            changed = True
    for key, value in (defaults or {}).items():
        if key not in settings:
            settings[key] = value
            changed = True
    if not changed:
        return "exists"
    settings_path = Path(settings_path)
    existed = settings_path.exists()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(str(settings_path), json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    return "updated" if existed else "created"


def cmd_guard(args):
    try:
        message = guard_decision(json.loads(sys.stdin.read()), args.dir, dict(os.environ), args.role)
        if message:
            print(message, file=sys.stderr)
            return 3
        return 0
    except BaseException as e:  # A broken guard must never block the session.
        print(f"[session-supervisor guard] warning: {type(e).__name__}: {e}", file=sys.stderr)
        return 0


def _read_follow_up_rules(claude_md):
    if not os.path.exists(claude_md):
        return []
    rules, errors = parse_follow_ups(Path(claude_md).read_text(encoding="utf-8"))
    if errors:
        raise StateError(errors)
    return rules


def cmd_validate_follow_ups(args):
    print(f"ok: {len(_read_follow_up_rules(args.claude_md))} rules")
    return 0


def _check_candidate_path(candidate, state):
    # Python writes are not seen by the guard, so the subcommand itself keeps to the supervisor directory.
    if _real(candidate) == _real(state):
        raise StateError(f"--candidate {candidate} is the state file; write follow-ups only to a candidate")
    directory = os.path.dirname(_real(candidate))
    if not (os.path.isfile(os.path.join(directory, "state.json")) and os.path.isdir(os.path.join(directory, ".supervisor"))):
        raise StateError(f"--candidate {candidate} is not in a supervisor directory (no state.json and .supervisor/ next to it)")


def cmd_follow_ups(args):
    manual = args.task is not None or args.url is not None
    if manual and (args.task is None or args.url is None):
        raise StateError("--task and --url go together")
    if manual and args.state:
        raise StateError("--state cannot be combined with --task / --url")
    if not manual and not args.state:
        raise StateError("--state is required unless --task and --url are given")
    state_path = args.state or os.path.join(os.path.dirname(os.path.abspath(args.candidate)), "state.json")
    _check_candidate_path(args.candidate, state_path)
    candidate = _read_json(args.candidate)
    closed = [url for group in args.closed for url in group]
    claude_md = args.claude_md or os.path.join(os.path.dirname(os.path.abspath(state_path)), "CLAUDE.md")
    rules = _read_follow_up_rules(claude_md)
    state = _read_json(state_path)
    if isinstance(state, dict) and state.get("schema_version") == SCHEMA_VERSION:
        return _follow_ups_v2(args, state, state_path, candidate, rules, closed, manual)
    if manual:
        task = next((t for t in candidate.get("tasks", []) if t.get("id") == args.task), None)
        if task is None or args.url not in _pr_urls(task):
            raise StateError(f"{args.url} is not a pr artifact of task {args.task} in the candidate")
        targets = [(args.task, args.url)]
        closed_urls = {args.url} if args.closed and (not closed or args.url in closed) else set()
    else:
        targets = new_pr_urls(_read_json(state_path), candidate)
        new_urls = {url for _, url in targets}
        for url in closed:
            if url not in new_urls:
                print(f"warning: --closed {url} is not a newly recorded pr artifact; ignored", file=sys.stderr)
        closed_urls = set(closed)
    added = []
    now = _now()
    for task_id, url in targets:
        actions = follow_up_actions(candidate, task_id, url, rules, now, closed=url in closed_urls)
        next(t for t in candidate["tasks"] if t.get("id") == task_id).setdefault("user_actions", []).extend(actions)
        added += [dict(a, task_id=task_id) for a in actions]
    if added:
        _write_atomic(args.candidate, serialize(candidate))
    print(json.dumps(added, ensure_ascii=False))
    return 0


def _follow_ups_v2(args, state, state_path, candidate, rules, closed, manual):
    """Follow-ups of a v2 candidate: new pr rows are found in the views with the note ops applied, and the follow-up
    user actions are added to the ops file as ua_add (the ops file is rewritten with its numbers filled)."""
    if not args.notes:
        raise StateError("--notes is required with a schema_version 2 state")
    _check_candidate_path(args.notes, state_path)
    raw_ops = _ops_arg(args.notes) if os.path.exists(args.notes) else {}
    texts, uids = _index_vault(_vault_path(state_path))
    notes = _notes_by_uid(texts, uids)
    view_cur = join_view(state, notes)
    ops = _numbered_ops(raw_ops, candidate, notes, view_cur)
    now = _now()
    after = dict(notes, **{uid: apply_note_ops(notes.get(uid, ""), items, now, state["rev"] + 1)[0] for uid, items in ops.items()})
    view_cand = join_view(candidate, after)
    if manual:
        task = next((t for t in view_cand["tasks"] if t.get("id") == args.task), None)
        if task is None or args.url not in _pr_urls(task):
            raise StateError(f"{args.url} is not a pr artifact of task {args.task} in the candidate")
        targets = [(args.task, args.url)]
        closed_urls = {args.url} if args.closed and (not closed or args.url in closed) else set()
    else:
        targets = new_pr_urls(view_cur, view_cand)
        closed_urls = set(closed)
    added = []
    for task_id, url in targets:
        task = next(t for t in view_cand["tasks"] if t.get("id") == task_id)
        actions = follow_up_actions(view_cand, task_id, url, rules, now, closed=url in closed_urls)
        task["user_actions"].extend(actions)
        ops.setdefault(task["uid"], []).extend(_ua_add_op(task_id, ua) for ua in actions)
        added += [dict(a, task_id=task_id) for a in actions]
    if added or ops != raw_ops:
        _write_atomic(args.notes, json.dumps(ops, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(added, ensure_ascii=False))
    return 0


def _git_output(argv):
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _locations_for(claude_md):
    """Resolved {key: (realpath, is_default)} and the supervisor directory, after the checks every prepare-dir mode shares."""
    sup = os.path.dirname(os.path.abspath(claude_md))
    text = Path(claude_md).read_text(encoding="utf-8") if os.path.exists(claude_md) else ""
    values, errors = parse_locations(text)
    if errors:
        raise StateError(errors)
    defaults = default_locations(sup)
    roots = {key: (_real(values.get(key, defaults[key])), key not in values) for key in LOCATION_KEYS}
    for key, (root, _) in roots.items():
        if _under(root, sup):
            errors.append(f"{key} {root} is inside the supervisor directory; workers would inherit its CLAUDE.md")
    if _inside_git(roots["work_root"][0]):
        errors.append(f"work_root {roots['work_root'][0]} is inside a git repository")
    if _cwds_overlap(roots["clone_root"][0], roots["work_root"][0]):
        errors.append(f"clone_root {roots['clone_root'][0]} and work_root {roots['work_root'][0]} are nested")
    if errors:
        raise StateError(errors)
    return roots, sup


def _check_dest(dest, root, sup):
    # Realpath on both sides, so a symlinked destination cannot lead outside the root.
    if not _under(dest, root) or _under(dest, sup):
        raise StateError(f"{dest} resolves outside {root}")


def _clone(url, root, sup):
    parts = clone_url_parts(url)
    if parts is None:
        raise StateError(f"--clone {url!r} must be https://<host>/<owner>/<repo> or git@<host>:<owner>/<repo> with plain names")
    dest = os.path.join(root, *parts)
    _check_dest(dest, root, sup)
    if os.path.exists(os.path.join(dest, ".git")):
        origin = _git_output(["git", "-C", dest, "remote", "get-url", "origin"])
        found = clone_url_parts(origin) if origin else None
        if found is None or [p.lower() for p in found] != [p.lower() for p in parts]:
            raise StateError(f"{dest} already exists with origin {origin!r}, which is not {url}")
        if _git_output(["git", "-C", dest, "rev-parse", "--verify", "HEAD"]) is None:
            raise StateError(f"{dest} has no HEAD (an interrupted clone?); ask the user whether to delete it, then rerun")
        print(f"exists: {dest}")
        return 0
    existed = os.path.exists(dest)
    if existed and (not os.path.isdir(dest) or os.listdir(dest)):
        raise StateError(f"{dest} exists and is neither an empty directory nor a clone of {url}")
    # Never wait for a password or a host-key prompt; the user's own ssh command is kept.
    ssh = os.environ.get("GIT_SSH_COMMAND") or _git_output(["git", "config", "--get", "core.sshCommand"]) or "ssh"
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND=f"{ssh} -o BatchMode=yes")
    try:
        proc = subprocess.run(["git", "clone", "--", url, dest], env=env, timeout=CLONE_TIMEOUT, capture_output=True, text=True)
        error = None if proc.returncode == 0 else f"git clone exited with {proc.returncode}: {proc.stderr.strip()}"
    except subprocess.TimeoutExpired:
        error = f"git clone timed out after {CLONE_TIMEOUT} seconds"
    if error:
        if not existed:
            shutil.rmtree(dest, ignore_errors=True)
        raise StateError(error)
    print(f"cloned: {dest}")
    return 0


def cmd_prepare_dir(args):
    # The only write outside the supervisor directory, so it keeps itself to the ## Locations roots.
    roots, sup = _locations_for(args.claude_md)
    if args.show:
        for key in LOCATION_KEYS:
            path, is_default = roots[key]
            print(f"{key}: {path}{' (default)' if is_default else ''}")
        return 0
    if args.clone is not None:
        return _clone(args.clone, roots["clone_root"][0], sup)
    if args.project is not None:
        return _prepare_project_dir(args.project, roots["work_root"][0], sup)
    if not WORK_NAME_RE.match(args.work):
        raise StateError(f"--work {args.work!r} must match T-<number>-<slug> (lowercase letters, digits and '-', up to 40 characters)")
    root = roots["work_root"][0]
    dest = os.path.join(root, args.work)
    _check_dest(dest, root, sup)
    existed = os.path.isdir(dest)
    os.makedirs(dest, exist_ok=True)
    print(f"{'exists' if existed else 'created'}: {dest}")
    return 0


def _prepare_project_dir(name, root, sup):
    if not PROJECT_NAME_RE.match(name):
        raise StateError(f"--project {name!r} must match P-<number>-<slug> (lowercase letters, digits and '-', up to 40 characters)")
    dest = os.path.join(root, name)
    _check_dest(dest, root, sup)
    settings_path = Path(dest) / ".claude" / "settings.json"
    settings = _read_settings(settings_path, ("PreToolUse",), "prepare-dir")
    existed = os.path.isdir(dest)
    os.makedirs(dest, exist_ok=True)
    print(f"{'exists' if existed else 'created'}: {dest}")
    script = Path(sup) / ".supervisor" / Path(__file__).name
    wanted = (("PreToolUse", GUARD_MATCHER, guard_hook_command(script, dest, "manager")),)
    print(f"{_install_hooks(settings_path, settings, wanted)}: {settings_path}")
    return 0


def _read_text_arg(path):
    return Path(path).read_text(encoding="utf-8") if path != "-" else sys.stdin.read()


def cmd_parse_project_report(args):
    result = parse_project_report(_read_text_arg(args.file), args.expect_project)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def cmd_plan_actions(args):
    result = parse_project_report(_read_text_arg(args.file), args.project)
    if not result["ok"]:
        raise StateError(result["error"])
    view = _load_view(args.state)
    project_uid = (_project(view, args.project) or {}).get("uid")
    titles = proposal_titles(_index_vault(_vault_path(args.state))[0], project_uid) if project_uid else ()
    print(json.dumps(plan_actions(view, args.project, result["report"], args.running, titles), ensure_ascii=False))
    return 0


def cmd_roster(args):
    print(roster(_load_view(args.state), args.project), end="")
    return 0


def cmd_waits(args):
    print(json.dumps(waits(_load_view(args.state), args.now or _now(), args.hub_minutes, args.review_minutes), ensure_ascii=False))
    return 0


def cmd_manager_reconcile(args):
    project = _project(_load_view(args.state), args.project)
    rows = _read_json(args.rows)
    if isinstance(rows, dict):
        rows = rows.get("sessions", rows.get("rows", []))
    events = _read_json(args.events) if args.events else None
    print(json.dumps(manager_reconcile(project["manager"], rows, events), ensure_ascii=False))
    return 0


def _require_v2(state, path):
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        raise StateError(f"{path} is not a schema_version {SCHEMA_VERSION} state")


def _ua_summary(ua):
    return {k: ua[k] for k in ("id", "kind", "detector", "answer_required", "checked", "done", "description", "answer", "rule", "url")}


def _scan_ctx(kind, item, view_item, workers, live, duplicate, items):
    if kind == "project":
        active = _occupies(item.get("manager") or {})
    else:
        active = any(_occupies(w) for w in workers if w.get("task_id") == item.get("id"))
    ctx = {"managed": True, "live": live, "kind": kind, "active": active, "duplicate": duplicate,
           "hold": (item.get("user_hold") or {}).get("reason"), "open_ua": bool((view_item or {}).get("user_actions"))}
    needs = artifact_needs(view_item, workers, items) if live and kind == "task" and view_item else []
    return dict(ctx, artifacts_ok=not needs), needs


def _proposed_uid(path, taken):
    st_ = os.stat(path)
    return _free_uid(datetime.fromtimestamp(getattr(st_, "st_birthtime", st_.st_mtime)), taken)


def notes_scan(state, texts, uids, vault=None):
    """Events for the notes the user changed (3.4 (2)), without writing anything."""
    view = join_view(state, _notes_by_uid(texts, uids))
    pending = _pending_entries(state, texts, uids)
    view_items = {(k, i["id"]): i for k in ("tasks", "projects", "archived") for i in view.get(k, [])}
    managed = {}
    for key, kind, live in (("tasks", "task", True), ("projects", "project", True), ("archived", "task", False)):
        for item in state.get(key, []):
            if item.get("uid") and item["uid"] not in managed:
                managed[item["uid"]] = (key, kind, item, live)
    events, warnings = [], []
    warned = (state.get("supervisor") or {}).get("note_warnings") or {}
    for uid, (key, kind, item, live) in managed.items():
        rels = uids.get(uid, [])
        if not rels:
            if live and uid not in pending:
                warnings.append(f"{item.get('id')}: note {uid} ({item.get('note')}) not found")
            continue
        text = texts[rels[0]]
        cur = note_values(text)
        seen = (pending[uid].get("before") if uid in pending else item.get("note_seen")) or cur
        body = parse_sections(parse_frontmatter(text)[1])
        rows = parse_artifacts(body.get(ART_SECTION, ""))[0]
        ctx, needs = _scan_ctx(kind, item, view_items.get((key, item["id"])), view["workers"], live, len(rels) > 1, rows)
        result = user_transition(seen, cur, ctx)
        # A change that needs nothing (an answer or checkbox edit, a paused note moved between user values) is not
        # printed; it is acked with the next event of the note. AC-12: nothing happens while the assignee is user.
        if result["event"] == "none" and not result["clear_invalid"]:
            continue
        hold = item.get("user_hold") or {}
        if result["event"] == "invalid" and hold.get("reason") == "invalid" and (hold.get("status"), hold.get("assignee")) == _pair(cur):
            continue
        warning = None
        if result["event"] == "invalid" and not live:
            # A stub has no user_hold (plan #19): its warning is recorded in note_warnings like an unmanaged note's.
            warning = _text_sha(json.dumps(_pair(cur), ensure_ascii=False))
            if warned.get(uid) == warning:
                continue
        event = dict(result, uid=uid, path=rels[0], id=item.get("id"), kind=kind, live=live, seen=seen, cur=cur,
                     user_actions=[_ua_summary(u) for u in parse_user_actions(body.get(UA_SECTION, ""))])
        if needs:
            event["needs"] = needs
        if warning:
            event["warning_key"], event["warning_sha"] = uid, warning
        events.append(event)
    taken = set(uids) | set(managed)
    for rel, text in texts.items():
        props, body_text, _ = parse_frontmatter(text)
        uid = props.get("uid") if isinstance(props.get("uid"), str) and props.get("uid") else None
        tags = props.get("tags") if isinstance(props.get("tags"), list) else [props.get("tags")]
        if "project" in tags:
            kind = "project"
        elif "task" in tags:
            kind = "task"
        else:
            continue
        if uid in managed:
            continue
        cur = note_values(text)
        if uid and _former_note(props, body_text):
            event = _former_follow_up(uid, rel, kind, props, body_text, cur)
            if event:
                events.append(event)
            continue
        result = user_transition(None, cur, {"managed": False})
        if result["event"] == "none":
            continue
        event = dict(result, uid=uid, path=rel, kind=kind, cur=cur)
        if result["event"] == "invalid":
            sha = _text_sha(json.dumps(_pair(cur), ensure_ascii=False))
            if warned.get(uid or rel) == sha:
                continue
            event["warning_key"], event["warning_sha"] = uid or rel, sha
        sections = parse_sections(body_text)
        event.update(title=props.get("title"), request=sections.get("概要", "").strip(),
                     parent=props.get("parent"), follows=props.get("follows"))
        if uid is None and vault:
            event["proposed_uid"] = _proposed_uid(os.path.join(vault, rel), taken)
            taken.add(event["proposed_uid"])
        events.append(event)
    return {"events": events, "warnings": warnings}


FORMER_MARK_RE = re.compile(r"\^(?:ua-\d+|sv-log-[\w-]+|sv-art-\d+)[ \t]*$|<!--[ \t]*sv-art ", re.M)


def _former_note(props, body):
    """A note state_tool wrote for a task that no longer is in state (its stub left the archived window)."""
    return bool(props.get("completed_at")) or bool(FORMER_MARK_RE.search(body))


def _former_follow_up(uid, rel, kind, props, body, cur):
    """done/archived or anything but backlog/agent: no event. backlog/agent: a follow-up (#16) without a stub, carrying
    the open slot's text and the highest artifact number, so the new T-id keeps the uid and starts its own rows."""
    if _pair(cur) != ("backlog", "agent"):
        return None
    sections = parse_sections(body)
    uas = parse_user_actions(sections.get(UA_SECTION, ""))
    slots = [u for u in uas if u["kind"] == "slot" and not u["done"]]
    rows = parse_artifacts(sections.get(ART_SECTION, ""))[0]
    return {"event": "follow_up", "row": 16, "ack": cur, "clear_invalid": False, "uid": uid, "path": rel, "kind": kind,
            "live": False, "former": True, "cur": cur, "title": props.get("title"), "request": sections.get("概要", "").strip(),
            "slot": slots[-1]["id"] if slots else None, "follow_up_text": slots[-1]["answer"] if slots else "",
            "last_artifact_number": max([r["n"] for r in rows] + [0]),
            "user_actions": [_ua_summary(u) for u in uas]}


def cmd_notes_scan(args):
    state = _read_json(args.state)
    _require_v2(state, args.state)
    vault = _vault_path(args.state)
    texts, uids = _index_vault(vault)
    print(json.dumps(notes_scan(state, texts, uids, vault), ensure_ascii=False))
    return 0


def cmd_note_show(args):
    vault = _vault_path(args.state)
    texts, uids = _index_vault(vault)
    if args.uid not in uids:
        raise StateError(f"note {args.uid} not found in {vault}")
    rel = uids[args.uid][0]
    props, body, errors = parse_frontmatter(texts[rel])
    sections = parse_sections(body)
    uas = parse_user_actions(sections.get(UA_SECTION, ""))
    items, problems = parse_artifacts(sections.get(ART_SECTION, ""))
    print(json.dumps({"uid": args.uid, "path": rel, "props": props, "frontmatter_errors": errors, "sections": sections,
                      "user_actions": [_ua_summary(u) for u in uas],
                      "slots": {u["id"]: u["answer"] for u in uas if u["kind"] == "slot" and not u["done"]},
                      "artifacts": items, "artifact_problems": problems}, ensure_ascii=False))
    return 0


def cmd_new_note(args):
    state = _read_json(args.state)
    _require_v2(state, args.state)
    _, uids = _index_vault(_vault_path(args.state))
    taken = set(uids) | {i.get("uid") for k in ("tasks", "projects", "archived") for i in state.get(k, [])}
    if args.count < 1:
        raise StateError("--count must be 1 or more")
    t, notes = datetime.now(), []
    while len(notes) < args.count:
        uid = t.strftime("%Y%m%d_%H%M%S")
        if uid not in taken:
            notes.append({"uid": uid, "path": _new_note_path(uid), "kind": args.kind})
        t += timedelta(seconds=1)
    print(json.dumps(notes[0] if args.count == 1 else notes))
    return 0


def cmd_migrate_v2(args):
    state = _read_json(args.state)
    vault = Path(_vault_path(args.state))
    new, creates = migrate_v2(state, _now())
    exists = [c for c in creates if (vault / c["path"]).exists()]
    conflicts = [c["path"] for c in exists if (vault / c["path"]).read_text(encoding="utf-8") != c["text"]]
    if conflicts:
        raise StateError(f"notes already exist with other content: {conflicts}")
    summary = {"dry_run": bool(args.dry_run), "notes": [c["path"] for c in creates], "tasks": len(new["tasks"]),
               "projects": len(new.get("projects", [])), "archived": len(new["archived"])}
    if not args.dry_run:
        # Notes first: an interrupted run leaves state.json at v1, and a rerun writes the same notes again.
        for c in creates:
            if c not in exists:
                _write_note(str(vault / c["path"]), c["text"])
        _write_atomic(args.state, serialize(new))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def cmd_export_v1(args):
    if _real(args.out) == _real(args.state):
        raise StateError("--out must not be the state file")
    state = _read_json(args.state)
    _require_v2(state, args.state)
    texts, uids = _index_vault(_vault_path(args.state))
    out = export_v1(state, _notes_by_uid(texts, uids))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(args.out, serialize(out))
    # Schema 1 requires every task to have a worker, so the previous version's commit rejects these tasks.
    print(json.dumps({"out": args.out, "tasks_without_workers": [t["id"] for t in out["tasks"] if not t.get("worker_ids")]}))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="state_tool.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    default_template = str(HERE / TEMPLATE_NAME)

    p = sub.add_parser("setup", help="install the supervisor directory (idempotent)")
    p.add_argument("--dir", required=True)
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("version", help="print the sha256 prefix of this script and its template")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("commit", help="validate a candidate and atomically write state.json and the dashboard")
    p.add_argument("--state", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--template", default=default_template)
    p.add_argument("--html", help="dashboard path (default: dashboard.html next to the state)")
    p.add_argument("--notes", help="JSON file {uid: [note op, ...]} applied to the vault notes (schema_version 2)")
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("notes-scan", help="list the changes the user made to vault notes as events (JSON, writes nothing)")
    p.add_argument("--state", required=True)
    p.set_defaults(func=cmd_notes_scan)

    p = sub.add_parser("note-show", help="print a vault note's props, sections, user actions and artifacts (JSON)")
    p.add_argument("--state", required=True)
    p.add_argument("--uid", required=True)
    p.set_defaults(func=cmd_note_show)

    p = sub.add_parser("migrate-v2", help="convert a schema_version 1 state.json into notes and a v2 state (idempotent until state is written)")
    p.add_argument("--state", required=True)
    p.add_argument("--dry-run", action="store_true", help="print what would be written and write nothing")
    p.set_defaults(func=cmd_migrate_v2)

    p = sub.add_parser("export-v1", help="write the v1 state.json of a v2 state and its notes to --out (rollback)")
    p.add_argument("--state", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_export_v1)

    p = sub.add_parser("new-note", help="reserve a uid and path for a new task or project note (writes nothing)")
    p.add_argument("--state", required=True)
    p.add_argument("--kind", required=True, choices=("task", "project"))
    p.add_argument("--count", type=int, default=1, help="reserve this many distinct uids (prints a list when above 1)")
    p.set_defaults(func=cmd_new_note)

    p = sub.add_parser("digest", help="print the SessionStart summary (always exits 0)")
    p.add_argument("--state", required=True)
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("parse-report", help="extract the last supervisor-report block")
    p.add_argument("--file", required=True, help="text file with the worker's latest messages, or - for stdin")
    p.add_argument("--expect-task")
    p.add_argument("--expect-worker")
    p.add_argument("--allow-awaiting-manager", action="store_true", help="accept awaiting_manager (project task workers only)")
    p.set_defaults(func=cmd_parse_report)

    p = sub.add_parser("parse-project-report", help="extract the last project-report block of a project manager")
    p.add_argument("--file", required=True, help="text file with the manager's latest message, or - for stdin")
    p.add_argument("--expect-project")
    p.set_defaults(func=cmd_parse_project_report)

    p = sub.add_parser("plan-actions", help="turn a project-report into the operations the supervisor performs (JSON)")
    p.add_argument("--state", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--file", required=True, help="text file with the manager's latest message, or - for stdin")
    p.add_argument("--running", action="append", default=[], metavar="WORKER_ID", help="a worker found running by get (repeatable)")
    p.set_defaults(func=cmd_plan_actions)

    p = sub.add_parser("roster", help="print the markdown roster of a project's workers and open reviews")
    p.add_argument("--state", required=True)
    p.add_argument("--project", required=True)
    p.set_defaults(func=cmd_roster)

    p = sub.add_parser("waits", help="list project workers whose hub or review wait crossed its threshold (JSON)")
    p.add_argument("--state", required=True)
    p.add_argument("--now", help="ISO time (default: now)")
    p.add_argument("--hub-minutes", type=int, default=HUB_WAIT_MINUTES)
    p.add_argument("--review-minutes", type=int, default=REVIEW_WAIT_MINUTES)
    p.set_defaults(func=cmd_waits)

    p = sub.add_parser("manager-reconcile", help="judge a project manager session from list rows and its latest events (JSON)")
    p.add_argument("--state", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--rows", required=True, help="JSON file with the list rows (include_archived)")
    p.add_argument("--events", help='JSON file {"last_message_ref": ..., "pending_approval": bool} summarizing the latest events')
    p.set_defaults(func=cmd_manager_reconcile)

    p = sub.add_parser("guard", help="PreToolUse hook: exit 3 with a reason on stderr when the call is blocked, else 0")
    p.add_argument("--dir", required=True)
    p.add_argument("--role", choices=("manager",), help="manager: the guard of a project directory")
    p.set_defaults(func=cmd_guard)

    p = sub.add_parser("validate-follow-ups", help="check the ## Follow-ups section of CLAUDE.md (exit 1 with error lines when invalid)")
    p.add_argument("--claude-md", required=True)
    p.set_defaults(func=cmd_validate_follow_ups)

    p = sub.add_parser("follow-ups", help="add follow-up user actions for newly recorded pr artifacts to a candidate")
    p.add_argument("--state", help="committed state.json; the candidate's new pr artifacts are found by comparing with it")
    p.add_argument("--candidate", required=True)
    p.add_argument("--closed", action="append", nargs="*", default=[],
                   help="pr urls already merged or closed (pr_closed rules are skipped); a bare --closed in --task mode")
    p.add_argument("--claude-md", help="default: CLAUDE.md next to the state")
    p.add_argument("--task", help="manual mode: add follow-ups for --url of this task without comparing (re-adds removed ones)")
    p.add_argument("--url")
    p.add_argument("--notes", help="note ops file (schema_version 2): follow-ups are added to it as ua_add ops")
    p.set_defaults(func=cmd_follow_ups)

    p = sub.add_parser("prepare-dir", help="show the ## Locations roots, or clone a repository / create a work directory under them")
    p.add_argument("--claude-md", required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--show", action="store_true", help="print clone_root and work_root as absolute paths (read-only)")
    mode.add_argument("--work", metavar="NAME", help="create work_root/NAME (NAME is T-NNNN-<slug>)")
    mode.add_argument("--clone", metavar="URL", help="clone URL into clone_root/<host>/<owner>/<repo>")
    mode.add_argument("--project", metavar="NAME", help="create work_root/NAME with the manager guard hook (NAME is P-NNNN-<slug>)")
    p.set_defaults(func=cmd_prepare_dir)

    p = sub.add_parser("check-cwd", help="print busy or free for a non-git cwd")
    p.add_argument("--state", required=True)
    p.add_argument("--cwd", required=True)
    p.set_defaults(func=cmd_check_cwd)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except StateError as e:
        for err in e.errors:
            print(f"error: {err}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # Malformed input must end in an error line, never a traceback.
        print(f"error: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
