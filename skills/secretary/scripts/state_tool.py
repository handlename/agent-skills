#!/usr/bin/env python3
"""State tool for the secretary skill.

Functional core: parse_sources, due_sources, classify_supervisor_ua, is_follow_up, worker_overlap, validate, next_state,
merge_supervisor, merge_source, apply_report, relay_badges, parse_report, sort_decisions, render, digest, serialize.
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
import sys
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path

SCHEMA_VERSION = 1
RELAYS_KEEP = 50
DIGEST_LIMIT = 10000
DIGEST_DECISIONS = 20
MIN_INTERVAL = 15 * 60
ERROR_BACKOFF = timedelta(minutes=15)
QUERY_MAX = 500
PASTE_DESC = 60
HERE = Path(__file__).resolve().parent
TEMPLATE_NAME = "dashboard.template.html"
RESERVED_SOURCE = "supervisor"

RELAY_MODES = ("paste", "app", "start", "none")
RELAY_ALLOWED = {"paste": ("paste",), "start": ("start",), "app": (), "none": ()}
RELAY_KINDS = ("paste", "start")
RELAY_STATUSES = ("pending", "sent", "awaiting_user", "failed", "confirmed", "unconfirmed")
RELAY_NEW_STATUS = {"paste": "awaiting_user", "start": "pending"}
SESSION_PURPOSES = ("query", "relay")
SESSION_STATES = ("pending", "running", "closing")
IN_FLIGHT = ("pending", "running")
OBSERVED_STATES = ("none", "running", "idle", "needs_input", "archived", "missing")
REPORT_MODES = ("query", "relay")
REPORT_STATUSES = ("done", "failed", "needs_input")
REPORT_FIELDS = ("source_id", "mode", "status", "summary", "blockers")
SOURCE_KEYS = ("directory", "query", "interval", "relay")
# Rows of the relay_mode classification table in references/state-schema.md; the first match wins, anything else is paste.
SUPERVISOR_UA_RULES = (
    ("app", re.compile(r"in the app|unpin|please archive|open repo")),
    ("paste", re.compile(r"waiting task|push|pull request|new worker|replace|\brestart\b|\bstart\b|cancel|archive")),
)

FOOTER_RE = re.compile(r"<!--sec-footer-->.*?<!--/sec-footer-->", re.S)
REPORT_RE = re.compile(r"```secretary-report[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.S)
SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
INTERVAL_RE = re.compile(r"^(\d+)(m|h|d)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RES = {"decision": re.compile(r"^D-(\d{4,})$"), "relay": re.compile(r"^R-(\d{4,})$"), "session": re.compile(r"^S-(\d{4,})$")}
COLLECTIONS = {"decision": "decisions", "relay": "relays", "session": "sessions"}


class StateError(Exception):
    def __init__(self, errors):
        self.errors = errors if isinstance(errors, list) else [errors]
        super().__init__("; ".join(self.errors))


# ---------------------------------------------------------------- functional core


def serialize(state):
    """Copied from session-supervisor's state_tool.py."""
    return json.dumps(state, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def empty_state(secretary_dir, supervisor_dir):
    return {
        "schema_version": SCHEMA_VERSION,
        "rev": 0,
        "updated_at": None,
        "counters": {"decision": 0, "relay": 0, "session": 0},
        "secretary": {
            "dir": secretary_dir,
            "session_ids": [],
            "archive_consent": None,
            "dashboard": {"url": None, "path": os.path.join(secretary_dir, "dashboard.html")},
            "sources_error": [],
        },
        "supervisor": {"dir": supervisor_dir, "read_at": None, "state_rev": None, "dashboard_url": None, "error": None},
        "sources": [],
        "today": [],
        "decisions": [],
        "relays": [],
        "sessions": [],
    }


def _real(path):
    """Copied from session-supervisor's state_tool.py."""
    return os.path.realpath(os.path.expanduser(path))


def _is_http_url(value):
    """Copied from session-supervisor's state_tool.py."""
    return isinstance(value, str) and re.match(r"^https?://", value, re.I) is not None


def _under(path, root):
    """Copied from session-supervisor's state_tool.py."""
    path, root = _real(path), _real(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _overlap(a, b):
    return _under(a, b) or _under(b, a)


def _parse_instant(value):
    """Parse an ISO 8601 timestamp with an offset; None when it is not one."""
    if not isinstance(value, str) or DATE_RE.match(value):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def parse_interval(text):
    m = INTERVAL_RE.match(text or "")
    if not m:
        return None
    return int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]


def parse_due(due, tz):
    """Return the deadline instant of `due`, or None when due is null. Raises ValueError on a bad format.

    A date-only due lasts until 23:59:59 of that day in `tz` (the zone of the caller's `now`, which is local time).
    """
    if due is None:
        return None
    if isinstance(due, str) and DATE_RE.match(due):
        try:
            day = datetime.strptime(due, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"invalid date {due!r}") from None
        return datetime.combine(day, time(23, 59, 59), tzinfo=tz)
    dt = _parse_instant(due)
    if dt is None:
        raise ValueError(f"due must be null, YYYY-MM-DD or ISO 8601 with an offset, got {due!r}")
    return dt


def is_overdue(due, now):
    now_dt = datetime.fromisoformat(now)
    try:
        deadline = parse_due(due, now_dt.tzinfo)
    except ValueError:
        return False
    return deadline is not None and deadline < now_dt


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


def parse_sources(text, secretary_dir=None, supervisor_dir=None):
    """Parse the `## Sources` section of CLAUDE.md. Returns (sources, errors); errors read `CLAUDE.md:<line>: <message>`."""
    lines = _strip_ignored(text.splitlines())
    heads = [i for i, line in enumerate(lines) if line.rstrip() == "## Sources"]
    if not heads:
        return [], []
    if len(heads) > 1:
        return [], [f"CLAUDE.md:{i + 1}: duplicate '## Sources' heading" for i in heads[1:]]
    errors, sources, current, seen = [], [], None, set()

    def err(n, message):
        errors.append(f"CLAUDE.md:{n}: {message}")

    def reject(n, message):
        err(n, message)
        current["_ok"] = False

    def finish(src):
        if src is None or src.get("_skip"):
            return
        for key in SOURCE_KEYS:
            if key not in src["lines"]:
                err(src["line"], f"source {src['id']!r} is missing '{key}'")
        if all(k in src["lines"] for k in SOURCE_KEYS) and src.pop("_ok"):
            sources.append(src)

    for i in range(heads[0] + 1, len(lines)):
        n, line = i + 1, lines[i].rstrip()
        if re.match(r"^#{1,2} ", line):
            break
        if not line.strip():
            continue
        if line.startswith("### "):
            finish(current)
            sid = line[4:].strip()
            current = {"id": sid, "line": n, "lines": {}, "_ok": True}
            if not SOURCE_ID_RE.match(sid):
                reject(n, f"invalid source id {sid!r} (use lowercase letters, digits and '-', up to 32 characters)")
            elif sid == RESERVED_SOURCE:
                reject(n, f"source id {sid!r} is reserved")
            elif sid in seen:
                reject(n, f"duplicate source id {sid!r}")
            seen.add(sid)
            continue
        if current is None:
            err(n, "the first entry under '## Sources' must be a '### <source-id>' heading")
            current = {"_skip": True}
            continue
        if current.get("_skip"):
            continue
        m = re.match(r"^- ([a-z]+):(.*)$", line)
        if not m:
            reject(n, f"expected '- <key>: <value>', got {line.strip()!r}")
            continue
        key, value = m.group(1), m.group(2).strip()
        if key not in SOURCE_KEYS:
            reject(n, f"unknown key {key!r}")
            continue
        if key in current["lines"]:
            reject(n, f"duplicate key {key!r}")
            continue
        current["lines"][key] = n
        problem = None
        if key == "directory":
            path = os.path.expanduser(value)
            if not os.path.isabs(path):
                problem = f"directory must be an absolute path, got {value!r}"
            elif secretary_dir and _overlap(path, secretary_dir):
                problem = "directory must not be the secretary directory, inside it, or one of its ancestors"
            elif supervisor_dir and _under(path, supervisor_dir):
                problem = "directory must not be the supervisor directory or inside it"
            value = os.path.normpath(path)
        elif key == "query":
            if not value:
                problem = "query must not be empty"
            elif len(value) > QUERY_MAX:
                problem = f"query must be at most {QUERY_MAX} characters"
        elif key == "interval":
            seconds = parse_interval(value)
            if seconds is None:
                problem = f"interval must look like 30m, 2h or 1d, got {value!r}"
            elif seconds < MIN_INTERVAL:
                problem = "interval must be at least 15m"
        elif key == "relay":
            if value not in ("yes", "no"):
                problem = f"relay must be yes or no, got {value!r}"
            value = value == "yes"
        if problem:
            reject(n, problem)
        current[key] = value
    finish(current)
    if errors:
        return [], errors
    return sources, []


def _in_flight(state, source_id, exclude=None):
    return [s for s in state.get("sessions", []) if s.get("source_id") == source_id and s.get("state") in IN_FLIGHT and s.get("id") != exclude]


def due_sources(sources, state, now):
    """Ids of sources whose interval has elapsed and that have no pending or running session. Directory existence is not checked here."""
    now_dt = datetime.fromisoformat(now)
    timing = {s.get("id"): s for s in state.get("sources", [])}
    due = []
    for src in sources:
        if _in_flight(state, src["id"]):
            continue
        t = timing.get(src["id"], {})
        if t.get("last_error"):
            attempt = _parse_instant(t.get("last_attempt_at"))
            if attempt is not None and now_dt < attempt + ERROR_BACKOFF:
                continue
        fetched = _parse_instant(t.get("last_fetched_at"))
        if fetched is None or fetched + timedelta(seconds=parse_interval(src["interval"])) <= now_dt:
            due.append(src["id"])
    return due


def classify_supervisor_ua(description):
    text = (description or "").lower()
    return next((mode for mode, pattern in SUPERVISOR_UA_RULES if pattern.search(text)), "paste")


def is_follow_up(decision):
    """A supervisor user action that carries `follow_up` is the user's own task, shown under Your tasks; a missing key means null."""
    return decision.get("follow_up") is not None


def _supervisor_relay_mode(decision):
    # A follow-up is closed by pasting a completion report, whatever its wording says.
    return "paste" if is_follow_up(decision) else classify_supervisor_ua(decision["description"])


def _expected_relay_mode(decision, defs):
    if decision["source"] == RESERVED_SOURCE:
        return _supervisor_relay_mode(decision)
    src = defs.get(decision["source"])
    return "start" if src and src["relay"] else "none"


def _occupies_cwd(worker):
    # Same rule as session-supervisor's _occupies / _occupies_cwd: a non-git worker holds its cwd until its archive is observed.
    if worker.get("kind") != "non-git":
        return False
    intended, observed = worker.get("intended_state"), worker.get("observed_state")
    return intended == "running" or (intended == "archived" and observed not in ("archived", "missing"))


def worker_overlap(sv_state, directory):
    return any(_occupies_cwd(w) and _overlap(w["cwd"], directory) for w in sv_state.get("workers", []))


def _follow_up_ok(value):
    return isinstance(value, dict) and all(isinstance(value.get(k), str) for k in ("rule", "url", "done"))


def supervisor_errors(sv):
    """Minimal read-only schema check of the supervisor's state.json."""
    if not isinstance(sv, dict):
        return ["supervisor state must be an object"]
    errors = []
    if sv.get("schema_version") != 1:
        errors.append("supervisor schema_version must be 1")
    if not isinstance(sv.get("rev"), int) or isinstance(sv.get("rev"), bool):
        errors.append("supervisor rev must be an integer")
    sup = sv.get("supervisor")
    if not isinstance(sup, dict):
        return errors + ["supervisor.supervisor must be an object"]
    ids = sup.get("session_ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        errors.append("supervisor.session_ids must be a list of strings")
    if sup.get("dashboard") is not None and not isinstance(sup.get("dashboard"), dict):
        errors.append("supervisor.dashboard must be an object or null")
    tasks, workers = sv.get("tasks"), sv.get("workers")
    if not isinstance(tasks, list) or not isinstance(workers, list):
        return errors + ["supervisor tasks and workers must be lists"]
    for i, t in enumerate(tasks):
        if not isinstance(t, dict) or not all(isinstance(t.get(k), str) for k in ("id", "title", "status")):
            errors.append(f"supervisor tasks[{i}] needs string id, title and status")
            continue
        uas = t.get("user_actions", [])
        if not isinstance(uas, list) or not all(isinstance(ua, dict) and all(isinstance(ua.get(k), str) for k in ("id", "description", "since")) for ua in uas):
            errors.append(f"supervisor task {t['id']}: user_actions must be {{id, description, since}} strings")
            continue
        for ua in uas:
            if ua.get("follow_up") is not None and not _follow_up_ok(ua["follow_up"]):
                errors.append(f"supervisor task {t['id']} {ua['id']}: follow_up must be {{rule, url, done}} strings or null")
    for i, w in enumerate(workers):
        if not isinstance(w, dict) or not all(isinstance(w.get(k), str) for k in ("kind", "cwd", "intended_state", "observed_state")):
            errors.append(f"supervisor workers[{i}] needs string kind, cwd, intended_state and observed_state")
    return errors


def _id_num(kind, value):
    m = ID_RES[kind].match(value) if isinstance(value, str) else None
    return int(m.group(1)) if m else None


def _next_id(state, kind):
    state["counters"][kind] += 1
    return f"{kind[0].upper()}-{state['counters'][kind]:04d}"


def _list_of_dicts(state, key):
    return isinstance(state.get(key), list) and all(isinstance(i, dict) for i in state[key])


def _shape_errors(state):
    """Type checks that let the remaining validation index fields safely (same policy as session-supervisor's _shape_errors)."""
    errors = []
    sec, sup = state["secretary"], state["supervisor"]
    for key in ("archive_consent", "dashboard"):
        if sec.get(key) is not None and not isinstance(sec.get(key), dict):
            errors.append(f"secretary.{key} must be an object or null")
    if not isinstance(sec.get("sources_error", []), list) or not all(isinstance(e, str) for e in sec.get("sources_error", [])):
        errors.append("secretary.sources_error must be a list of strings")
    if sup.get("error") is not None and not isinstance(sup.get("error"), str):
        errors.append("supervisor.error must be a string or null")
    counters = state["counters"]
    for kind in ID_RES:
        v = counters.get(kind)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            errors.append(f"counters.{kind} must be a non-negative integer")
    for key in ("sources", "today", "decisions", "relays", "sessions"):
        if not _list_of_dicts(state, key):
            errors.append(f"{key} must be a list of objects")
    if errors:
        return errors
    for s in state["sources"]:
        if not isinstance(s.get("id"), str):
            errors.append("sources[].id must be a string")
        elif s.get("directory") is not None and not isinstance(s.get("directory"), str):
            errors.append(f"source {s['id']}: directory must be a string or null")
    for t in state["today"]:
        if not all(isinstance(t.get(k), str) for k in ("key", "source", "title")):
            errors.append("today[] needs string key, source and title")
    for d in state["decisions"]:
        if not all(isinstance(d.get(k), str) for k in ("id", "key", "source", "description")):
            errors.append("decisions[] needs string id, key, source and description")
        elif d.get("relay_mode") not in RELAY_MODES:
            errors.append(f"decision {d['id']}: invalid relay_mode {d.get('relay_mode')!r}")
        elif d.get("due") is not None and not isinstance(d.get("due"), str):
            errors.append(f"decision {d['id']}: due must be a string or null")
        elif d.get("relay_id") is not None and not isinstance(d.get("relay_id"), str):
            errors.append(f"decision {d['id']}: relay_id must be a string or null")
        elif d.get("follow_up") is not None and not _follow_up_ok(d["follow_up"]):
            errors.append(f"decision {d['id']}: follow_up must be {{rule, url, done}} strings or null")
    for r in state["relays"]:
        if not all(isinstance(r.get(k), str) for k in ("id", "decision_id", "mode", "status")):
            errors.append("relays[] needs string id, decision_id, mode and status")
        elif r["status"] not in RELAY_STATUSES:
            errors.append(f"relay {r['id']}: invalid status {r['status']!r}")
        elif not isinstance(r.get("words"), str):
            errors.append(f"relay {r['id']}: words must be a string")
    for s in state["sessions"]:
        if not isinstance(s.get("id"), str):
            errors.append("sessions[].id must be a string")
        elif s.get("purpose") not in SESSION_PURPOSES:
            errors.append(f"session {s['id']}: invalid purpose {s.get('purpose')!r}")
        elif s.get("state") not in SESSION_STATES:
            errors.append(f"session {s['id']}: invalid state {s.get('state')!r}")
        elif s.get("observed_state", "none") not in OBSERVED_STATES:
            errors.append(f"session {s['id']}: invalid observed_state {s.get('observed_state')!r}")
        elif not isinstance(s.get("source_id"), str):
            errors.append(f"session {s['id']}: source_id must be a string")
        elif s["purpose"] == "relay" and not isinstance(s.get("relay_id"), str):
            errors.append(f"session {s['id']}: a relay session requires relay_id")
        elif not isinstance(s.get("resend_requested", False), bool):
            errors.append(f"session {s['id']}: resend_requested must be a boolean")
    return errors


def validate(state):
    """Check the single-state invariants. Returns a list of error strings."""
    if not isinstance(state, dict):
        return ["state must be an object"]
    errors = []
    if state.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if not isinstance(state.get("rev"), int) or isinstance(state.get("rev"), bool) or state.get("rev") < 0:
        errors.append("rev must be a non-negative integer")
    for key in ("counters", "secretary", "supervisor"):
        if not isinstance(state.get(key), dict):
            errors.append(f"{key} must be an object")
    if not errors and not isinstance(state["secretary"].get("session_ids"), list):
        errors.append("secretary.session_ids must be a list")
    if errors:
        return errors
    errors = _shape_errors(state)
    if errors:
        return errors

    for kind, key in COLLECTIONS.items():
        ids = [i["id"] for i in state[key]]
        for i in ids:
            if _id_num(kind, i) is None:
                errors.append(f"{key}: malformed id {i!r}")
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            errors.append(f"duplicate {kind} ids: {dupes}")
    for label, key in (("decision", "decisions"), ("today", "today")):
        keys = [i["key"] for i in state[key]]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            errors.append(f"duplicate {label} keys: {dupes}")
    src_ids = [s["id"] for s in state["sources"]]
    if len(src_ids) != len(set(src_ids)):
        errors.append("duplicate source ids")
    urls = [("secretary.dashboard.url", (state["secretary"].get("dashboard") or {}).get("url")),
            ("supervisor.dashboard_url", state["supervisor"].get("dashboard_url"))]
    urls += [(f"today {t['key']}: link", t.get("link")) for t in state["today"]]
    for label, url in urls:
        if url is not None and not _is_http_url(url):
            errors.append(f"{label} must be http(s) or null")
    return errors


def _by_id(items):
    return {i["id"]: i for i in items}


def _transition_errors(current, candidate, now, sources, sources_errors, sv_state):
    """Checks on what the candidate adds or changes; entries already in `current` are not rechecked."""
    errors = []
    tz = datetime.fromisoformat(now).tzinfo
    for kind in ID_RES:
        if candidate["counters"][kind] < current["counters"][kind]:
            errors.append(f"counters.{kind} must not decrease")
        old = _by_id(current[COLLECTIONS[kind]])
        for item in candidate[COLLECTIONS[kind]]:
            num = _id_num(kind, item["id"])
            if item["id"] not in old and not (current["counters"][kind] < num <= candidate["counters"][kind]):
                errors.append(f"new id {item['id']} must be allocated by advancing counters.{kind}")
    if errors:
        return errors

    defs = {s["id"]: s for s in sources}
    sources_ok = not sources_errors
    cur_decisions = _by_id(current["decisions"])
    decisions = _by_id(candidate["decisions"])
    for d in candidate["decisions"]:
        old = cur_decisions.get(d["id"], {})
        if d.get("due") != old.get("due"):
            try:
                parse_due(d.get("due"), tz)
            except ValueError as e:
                errors.append(f"decision {d['id']}: {e}")
        # Only the merges change relay_mode, and they always derive it; with ## Sources broken, external modes stay as they were.
        if d["relay_mode"] != old.get("relay_mode") and (sources_ok or d["source"] == RESERVED_SOURCE):
            expected = _expected_relay_mode(d, defs)
            if d["relay_mode"] != expected:
                errors.append(f"decision {d['id']}: relay_mode must be {expected!r}, got {d['relay_mode']!r}")

    cur_relays = _by_id(current["relays"])
    relays = _by_id(candidate["relays"])
    for r in candidate["relays"]:
        prefix = f"relay {r['id']}"
        if r["id"] in cur_relays:
            changed = [k for k in ("mode", "decision_id", "decision_key", "words") if r.get(k) != cur_relays[r["id"]].get(k)]
            if changed:
                errors.append(f"{prefix}: {', '.join(changed)} cannot change after the relay is recorded")
            continue
        if not r["words"].strip():
            errors.append(f"{prefix}: words must quote the user's own message")
        if r["mode"] not in RELAY_KINDS:
            errors.append(f"{prefix}: unknown mode {r['mode']!r}")
            continue
        d = decisions.get(r["decision_id"])
        if d is None:
            errors.append(f"{prefix}: decision {r['decision_id']} does not exist")
            continue
        if r["mode"] == "start" and not sources_ok:
            errors.append(f"{prefix}: ## Sources has errors; fix CLAUDE.md before relaying")
            continue
        relay_mode = _expected_relay_mode(d, defs)
        if r["mode"] not in RELAY_ALLOWED[relay_mode]:
            errors.append(f"{prefix}: mode {r['mode']!r} is not allowed for a {relay_mode!r} decision")
            continue
        if r["status"] != RELAY_NEW_STATUS[r["mode"]]:
            errors.append(f"{prefix}: a new {r['mode']} relay starts as {RELAY_NEW_STATUS[r['mode']]!r}")
        if d.get("relay_id") != r["id"]:
            errors.append(f"{prefix}: decision {d['id']}.relay_id must point to it")

    cur_sessions = _by_id(current["sessions"])
    due_now = None
    for s in candidate["sessions"]:
        prefix = f"session {s['id']}"
        old = cur_sessions.get(s["id"])
        if old is not None:
            if old["state"] == "pending" and s["state"] == "running" and not s.get("session_id"):
                errors.append(f"{prefix}: running requires session_id")
            continue
        if s["state"] != "pending":
            errors.append(f"{prefix}: a new session must be committed as pending before it is started")
        if not sources_ok:
            errors.append(f"{prefix}: ## Sources has errors; fix CLAUDE.md before starting sessions")
            continue
        if s["source_id"] not in defs:
            errors.append(f"{prefix}: source {s['source_id']!r} is not defined in ## Sources")
            continue
        if _in_flight(candidate, s["source_id"], exclude=s["id"]):
            errors.append(f"{prefix}: source {s['source_id']!r} already has a pending or running session")
            continue
        if s["purpose"] == "query":
            if due_now is None:
                due_now = due_sources(sources, current, now)
            if s["source_id"] not in due_now:
                errors.append(f"{prefix}: source {s['source_id']!r} is not due yet")
            continue
        r = relays.get(s.get("relay_id"))
        if r is None or r["mode"] != "start" or r["status"] != "pending":
            errors.append(f"{prefix}: relay {s.get('relay_id')} must be a pending start relay")
        elif decisions.get(r["decision_id"], {}).get("source") != s["source_id"]:
            errors.append(f"{prefix}: relay {r['id']} targets another source")
        elif sv_state is None:
            errors.append(f"{prefix}: the supervisor state is unreadable, so the directory overlap cannot be checked")
        elif worker_overlap(sv_state, defs[s["source_id"]]["directory"]):
            errors.append(f"{prefix}: {defs[s['source_id']]['directory']} overlaps a running non-git worker of the supervisor")
    return errors


def _sync_sources(state, sources):
    """Align sources[] with ## Sources; items of a removed source go with it, the relay history stays."""
    timing = {s["id"]: s for s in state["sources"]}
    ids = [s["id"] for s in sources]
    state["sources"] = [
        {**{"last_fetched_at": None, "last_attempt_at": None, "last_error": None}, **timing.get(s["id"], {}),
         "id": s["id"], "directory": s["directory"], "interval": s["interval"], "relay": s["relay"]}
        for s in sources
    ]
    state["today"] = [t for t in state["today"] if t["source"] in ids]
    state["decisions"] = [d for d in state["decisions"] if d["source"] == RESERVED_SOURCE or d["source"] in ids]
    relay_of = {s["id"]: s["relay"] for s in sources}
    for d in state["decisions"]:
        if d["source"] in relay_of:
            d["relay_mode"] = "start" if relay_of[d["source"]] else "none"


def next_state(current, candidate, now, sources=None, sources_errors=None, sv_state=None):
    """Return the state to write for `candidate`, or raise StateError.

    `sources` / `sources_errors` come from parse_sources of CLAUDE.md (None leaves sources[] as they are);
    `sv_state` is the supervisor's state.json, or None when it could not be read.
    """
    if not isinstance(candidate, dict) or candidate.get("rev") != current.get("rev"):
        raise StateError(f"candidate rev {candidate.get('rev') if isinstance(candidate, dict) else None!r} does not match current rev {current.get('rev')!r}")
    state = copy.deepcopy(candidate)
    errors = validate(state)
    if errors:
        raise StateError(errors)
    known = sources if sources is not None else [
        {"id": s["id"], "directory": s.get("directory"), "interval": s.get("interval"), "relay": s.get("relay")}
        for s in state["sources"] if s.get("interval")]
    errors = _transition_errors(current, state, now, known, sources_errors or [], sv_state)
    if errors:
        raise StateError(errors)

    if sources is not None or sources_errors:
        state["secretary"]["sources_error"] = list(sources_errors or [])
    if sources is not None and not sources_errors:
        _sync_sources(state, sources)
    # Removing a relay session from sessions[] means its archive (or disappearance) was observed.
    kept = {s["id"] for s in state["sessions"]}
    relays = _by_id(state["relays"])
    for s in current["sessions"]:
        r = relays.get(s.get("relay_id"))
        if s["purpose"] == "relay" and s["id"] not in kept and r is not None and not r.get("session_closed_at"):
            r["session_closed_at"] = now
    # A relay session that has started (including one recovered from pending) has delivered or is delivering its relay.
    for s in state["sessions"]:
        r = relays.get(s.get("relay_id"))
        if s["purpose"] == "relay" and s["state"] != "pending" and r is not None and r["status"] == "pending":
            r["status"] = "sent"
    ordered = sorted(state["relays"], key=lambda r: r.get("at") or "", reverse=True)
    live = {d.get("relay_id") for d in state["decisions"]}
    keep = {r["id"] for r in ordered if r["id"] in live}
    others = [r["id"] for r in ordered if r["id"] not in live]
    keep.update(others[: max(0, RELAYS_KEEP - len(keep))])
    state["relays"] = [r for r in ordered if r["id"] in keep]
    state["rev"] = current["rev"] + 1
    state["updated_at"] = now
    return state


def _confirm_relay(state, relay_id):
    for r in state["relays"]:
        if r["id"] == relay_id:
            r["status"] = "confirmed"


def _merge_decisions(state, source, items, relay_mode_of):
    """Upsert decisions of one source from (key, ref, description, since, follow_up) and drop the keys the source no longer reports."""
    existing = {d["key"]: d for d in state["decisions"] if d["source"] == source}
    seen = set()
    for key, ref, description, since, follow_up in items:
        seen.add(key)
        d = existing.get(key)
        if d is None:
            d = {"id": _next_id(state, "decision"), "key": key, "source": source, "ref": ref,
                 "description": description, "since": since, "due": None, "relay_mode": None, "relay_id": None}
            state["decisions"].append(d)
        d.update(description=description, ref=ref, since=since, follow_up=copy.deepcopy(follow_up))
        d["relay_mode"] = relay_mode_of(d)
    for key, d in existing.items():
        if key not in seen and d.get("relay_id"):
            _confirm_relay(state, d["relay_id"])
    state["decisions"] = [d for d in state["decisions"] if d["source"] != source or d["key"] in seen]


def merge_supervisor(state, sv_state, now):
    """Fold the supervisor's user_actions into decisions. A malformed supervisor state keeps the previous items."""
    state = copy.deepcopy(state)
    sup = state["supervisor"]
    errors = supervisor_errors(sv_state)
    if errors:
        sup.update(error="; ".join(errors), read_at=now)
        return state
    items = [(f"supervisor/{t['id']}/{ua['id']}@{ua['since']}", {"task_id": t["id"], "ua_id": ua["id"]}, ua["description"], ua["since"],
              ua.get("follow_up"))
             for t in sv_state["tasks"] for ua in t.get("user_actions", [])]
    _merge_decisions(state, RESERVED_SOURCE, items, _supervisor_relay_mode)
    url = (sv_state["supervisor"].get("dashboard") or {}).get("url")
    sup.update(read_at=now, state_rev=sv_state["rev"], dashboard_url=url if _is_http_url(url) else None, error=None)
    return state


def _source_entry(state, source_id):
    for s in state["sources"]:
        if s["id"] == source_id:
            return s
    entry = {"id": source_id, "last_fetched_at": None, "last_attempt_at": None, "last_error": None}
    state["sources"].append(entry)
    return entry


def merge_source(state, source_id, report, session, now, relay=None):
    """Fold a `mode: query` report into today and decisions. `relay` is the source's relay setting (None keeps it)."""
    state = copy.deepcopy(state)
    state["today"] = [t for t in state["today"] if t["source"] != source_id] + [
        {"key": f"{source_id}/{t['id']}", "source": source_id, "title": t["title"], "link": t.get("link") or None}
        for t in report.get("today", [])]
    items = [(f"{source_id}/{d['id']}", {"item_id": d["id"]}, d["description"], d.get("since") or None, None)
             for d in report.get("decisions", [])]

    def mode_of(d):
        if relay is None:
            return d["relay_mode"] or "none"
        return "start" if relay else "none"

    _merge_decisions(state, source_id, items, mode_of)
    started = _parse_instant(session.get("created_at"))
    relays = _by_id(state["relays"])
    for d in state["decisions"]:
        r = relays.get(d.get("relay_id"))
        if d["source"] != source_id or r is None or r["mode"] != "start" or r["status"] != "sent":
            continue
        # Only a query started after the relay session's archive was observed can see the relayed change.
        closed = _parse_instant(r.get("session_closed_at"))
        if closed is not None and started is not None and started > closed:
            r["status"] = "unconfirmed"
    entry = _source_entry(state, source_id)
    entry.update(last_fetched_at=now, last_attempt_at=now, last_error=None)
    return state


def apply_report(state, session_id, message_ref, result, now, relay=None):
    """Record a parse_report result for one session. Returns (state, outcome)."""
    state = copy.deepcopy(state)
    session = next((s for s in state["sessions"] if s["id"] == session_id), None)
    if session is None:
        raise StateError(f"session {session_id} is not in sessions[]")
    if session["state"] != "running":
        raise StateError(f"session {session_id} is {session['state']}, not running")
    session["last_message_ref"] = message_ref
    report = result.get("report") if result.get("ok") else None
    if report is not None and report["mode"] != session["purpose"]:
        result, report = {"ok": False, "error": f"report mode {report['mode']!r} does not match a {session['purpose']} session"}, None
    relay_entry = next((r for r in state["relays"] if r["id"] == session.get("relay_id")), None)

    def fail(error, blockers=()):
        if session["purpose"] == "query":
            _source_entry(state, session["source_id"]).update(last_error=error, last_attempt_at=now)
        elif relay_entry is not None:
            relay_entry["status"] = "failed"
            relay_entry["blockers"] = list(blockers) or [error]
        session["state"] = "closing"

    if report is None:
        if not session.get("resend_requested"):
            session["resend_requested"] = True
            return state, "resend_requested"
        fail(result.get("error", "report not parsed"))
        return state, "parse_failed"
    if report["status"] != "done":
        fail("; ".join(report["blockers"]) or report["summary"] or report["status"], report["blockers"])
        return state, f"{session['purpose']}_failed"
    if session["purpose"] == "query":
        state = merge_source(state, session["source_id"], report, session, now, relay)
        next(s for s in state["sessions"] if s["id"] == session_id)["state"] = "closing"
        return state, "merged"
    # A relay that ran makes its source due at once so the next user turn can check the change.
    _source_entry(state, session["source_id"])["last_fetched_at"] = None
    session["state"] = "closing"
    return state, "relay_done"


def relay_badges(state):
    """decision id -> {"status", "label"} for decisions whose latest relay is still in progress or failed."""
    relays = _by_id(state.get("relays", []))
    badges = {}
    for d in state.get("decisions", []):
        r = relays.get(d.get("relay_id"))
        if r is None:
            continue
        if r["status"] == "awaiting_user":
            label = "paste pending"
        elif r["status"] in ("pending", "sent"):
            label = "relayed; checked at next refresh"
        elif r["status"] == "unconfirmed":
            label = "relay not reflected"
        elif r["status"] == "failed":
            label = "relay failed"
        else:
            continue
        badges[d["id"]] = {"status": r["status"], "label": label, "blockers": r.get("blockers", [])}
    return badges


def paste_text(decision, words=""):
    """Text the user pastes into the supervisor chat; None for anything but a supervisor `paste` decision."""
    ref = decision.get("ref") or {}
    if decision.get("source") != RESERVED_SOURCE or decision.get("relay_mode") != "paste":
        return None
    return f'{ref.get("task_id")} {ref.get("ua_id")} ("{decision["description"][:PASTE_DESC]}"): {words}'


def _valid_since(value):
    return value == "" or (isinstance(value, str) and DATE_RE.match(value) is not None) or _parse_instant(value) is not None


def _report_items_error(report):
    for key, text_key in (("today", "title"), ("decisions", "description")):
        items = report.get(key)
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            return f"{key} must be a list of objects"
        ids = []
        for i in items:
            if not isinstance(i.get("id"), str) or not i["id"]:
                return f"{key}[].id must be a non-empty string"
            if not isinstance(i.get(text_key), str) or not i[text_key].strip():
                return f"{key}[].{text_key} must be a non-empty string"
            ids.append(i["id"])
        if len(ids) != len(set(ids)):
            return f"{key}[].id must be unique"
    for t in report["today"]:
        link = t.get("link", "")
        if link != "" and not _is_http_url(link):
            return "today[].link must be empty or http(s)"
    for d in report["decisions"]:
        if not _valid_since(d.get("since", "")):
            return "decisions[].since must be empty or a date"
    return None


def parse_report(text, expect_source=None):
    blocks = REPORT_RE.findall(text)
    if not blocks:
        return {"ok": False, "error": "no secretary-report block found"}
    try:
        report = json.loads(blocks[-1])
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"invalid JSON in secretary-report block: {e}"}
    if not isinstance(report, dict):
        return {"ok": False, "error": "secretary-report must be a JSON object"}
    missing = [f for f in REPORT_FIELDS if f not in report]
    if missing:
        return {"ok": False, "error": f"missing fields: {missing}"}
    for key, allowed in (("mode", REPORT_MODES), ("status", REPORT_STATUSES)):
        if not isinstance(report[key], str) or report[key] not in allowed:
            return {"ok": False, "error": f"invalid {key} {report[key]!r}"}
    for key in ("source_id", "summary"):
        if not isinstance(report[key], str):
            return {"ok": False, "error": f"{key} must be a string"}
    if not isinstance(report["blockers"], list) or not all(isinstance(b, str) for b in report["blockers"]):
        return {"ok": False, "error": "blockers must be a list of strings"}
    if report["status"] in ("failed", "needs_input") and not report["blockers"]:
        return {"ok": False, "error": f"status {report['status']} requires at least one blocker"}
    if report["mode"] == "query":
        error = _report_items_error(report)
        if error:
            return {"ok": False, "error": error}
    else:
        report = {k: v for k, v in report.items() if k not in ("today", "decisions")}
    if expect_source is not None and report["source_id"] != expect_source:
        return {"ok": False, "error": f"source_id {report['source_id']!r} does not match {expect_source!r}"}
    return {"ok": True, "report": report}


def _sort_instant(value, tz, end_of_day):
    if isinstance(value, str) and DATE_RE.match(value):
        try:
            day = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
        return datetime.combine(day, time(23, 59, 59) if end_of_day else time(0, 0), tzinfo=tz)
    return _parse_instant(value)


def sort_decisions(decisions, now):
    """Due ascending with no due last; ties by since ascending."""
    tz = datetime.fromisoformat(now).tzinfo

    def key(d):
        due = _sort_instant(d.get("due"), tz, True)
        since = _sort_instant(d.get("since"), tz, False)
        return (due is None, due or datetime.min.replace(tzinfo=tz), since is None, since or datetime.min.replace(tzinfo=tz), d["id"])

    return sorted(decisions, key=key)


def _esc(value):
    """Copied from session-supervisor's state_tool.py."""
    # Braces are escaped too so state strings can never form a {{SLOT}} of the template.
    return html.escape("" if value is None else str(value)).replace("{", "&#123;").replace("}", "&#125;")


def _time(iso, cls="rel"):
    """Copied from session-supervisor's state_tool.py."""
    # HH:MM is the static fallback; the page script swaps in a relative time and keeps the ISO value as a tooltip.
    if not iso:
        return ""
    clock = iso[11:16] if len(iso) >= 16 else iso
    return f'<time class="{cls}" datetime="{_esc(iso)}" title="{_esc(iso)}">{_esc(clock)}</time>'


def _copy(cls, text, label=None):
    """Click-to-copy control, copied from session-supervisor's _copy_id."""
    return (f'<button type="button" class="{cls} copyable" data-copy aria-label="Copy {_esc(label or text)}" '
            f'title="クリックでコピー">{_esc(text)}</button>')


def _link_or_text(url, text):
    if _is_http_url(url):
        return f'<a href="{_esc(url)}">{_esc(text)}</a>'
    return _esc(text)


def _deadline_iso(due, tz):
    try:
        deadline = parse_due(due, tz)
    except ValueError:
        return None
    return deadline.isoformat() if deadline else None


def _session_requests(state):
    """Per-session messages that need the user."""
    consent = (state.get("secretary") or {}).get("archive_consent")
    out = []
    for s in state.get("sessions", []):
        name = f"sec-{s.get('id')}"
        if s.get("observed_state") == "needs_input":
            out.append(f"Answer the pending approval of {name} in the app")
        if s.get("state") == "closing":
            if s.get("archive_refused"):
                out.append(f"Close {name} from the screen or unpin it")
            elif not consent:
                out.append(f"Please archive {name}")
    return out


def _render_hero(state, now):
    n = sum(1 for d in state["decisions"] if not is_follow_up(d))
    tasks = len(state["decisions"]) - n
    overdue = sum(1 for d in state["decisions"] if is_overdue(d.get("due"), now))
    attr = f' data-overdue="{overdue}"' if overdue else ""
    waiting = []
    if n:
        waiting.append(f"{n} decision{'s' if n != 1 else ''}")
    if tasks:
        waiting.append(f"{tasks} task{'s' if tasks != 1 else ''}")
    sub = f"{' and '.join(waiting)} waiting on you" if waiting else "Nothing waits on you."
    return (f'<section class="hero" id="summary"{attr} aria-labelledby="hero-label">'
            f'<div class="hero__stat"><p class="hero__num" data-overdue-count>{overdue}</p><p class="hero__cap">overdue</p></div>'
            f'<div class="hero__stat"><p class="hero__num">{n}</p><p class="hero__cap">decisions</p></div>'
            f'<div class="hero__stat"><p class="hero__num">{tasks}</p><p class="hero__cap">your tasks</p></div>'
            f'<div class="hero__words"><h2 class="hero__label" id="hero-label">{"Overdue" if overdue else ("Waiting" if waiting else "All clear")}</h2>'
            f'<p class="hero__sub">{sub}</p></div></section>')


def _due_parts(d, now, tz):
    """(row attributes, due text) shared by Decisions and Your tasks rows."""
    overdue = " data-overdue" if is_overdue(d.get("due"), now) else ""
    deadline = _deadline_iso(d.get("due"), tz)
    due_attr = f' data-due="{_esc(deadline)}"' if deadline else ""
    due = f'due <b>{_esc(d["due"])}</b>' if d.get("due") else '<span class="nodue">no due</span>'
    return overdue + due_attr, due


def _badge_html(badges, decision_id):
    b = badges.get(decision_id)
    if b is None:
        return ""
    blockers = "".join(f"<li>{_esc(x)}</li>" for x in b["blockers"]) if b["status"] == "failed" else ""
    return (f'<span class="badge" data-relay="{_esc(b["status"])}">{_esc(b["label"])}</span>'
            + (f'<ul class="dec__blockers">{blockers}</ul>' if blockers else ""))


def _render_your_tasks(state, now):
    """Supervisor follow-ups (e.g. reviewing a PR); empty string when there are none so the section is not shown."""
    tasks = [d for d in state["decisions"] if is_follow_up(d)]
    if not tasks:
        return ""
    tz = datetime.fromisoformat(now).tzinfo
    relays = _by_id(state["relays"])
    badges = relay_badges(state)
    rows = []
    for d in sort_decisions(tasks, now):
        attrs, due = _due_parts(d, now, tz)
        since = f' · since {_esc(d["since"])}' if d.get("since") else ""
        hint = "" if d.get("due") else '<p class="task__hint">Tell me a due date to set one</p>'
        relay = relays.get(d.get("relay_id"))
        words = relay["words"] if relay and relay["status"] == "awaiting_user" else ""
        label = "Paste into the supervisor chat" if words else "When it is done, tell me, or paste this into the supervisor chat"
        url = d["follow_up"]["url"]
        rows.append(
            f'<li class="task"{attrs}>'
            f'<p class="task__head">{_copy("id", d["id"])}<span class="task__text">{_esc(d["description"])}</span>{_badge_html(badges, d["id"])}</p>'
            f'<p class="task__meta">{_link_or_text(url, url)} · {due}{since}</p>{hint}'
            f'<div class="task__done" data-mode="paste"><span class="label">{label}</span>'
            f'{_copy("paste", paste_text(d, words or "done"), "paste text for " + d["id"])}</div></li>')
    return (f'<section class="your-tasks" id="your-tasks" aria-labelledby="your-tasks-label"><h2 class="section-label" id="your-tasks-label">'
            f'Your tasks <span class="count">{len(rows)}</span></h2><ul class="task-list">{"".join(rows)}</ul></section>')


def _relay_guide(d, relay):
    mode = d["relay_mode"]
    if mode == "paste":
        words = relay["words"] if relay and relay["status"] == "awaiting_user" else ""
        hint = "Paste into the supervisor chat" if words else "Tell me your decision, or paste this with your answer into the supervisor chat"
        return (f'<div class="dec__guide" data-mode="paste"><span class="label">{hint}</span>'
                f'{_copy("paste", paste_text(d, words), "paste text for " + d["id"])}</div>')
    if mode == "app":
        return '<p class="dec__guide" data-mode="app">Act on this in the app; the supervisor dashboard shows the details.</p>'
    if mode == "start":
        return f'<p class="dec__guide" data-mode="start">Tell me your decision; I relay it to {_esc(d["source"])}.</p>'
    return f'<p class="dec__guide" data-mode="none">Handle this in {_esc(d["source"])} yourself.</p>'


def _render_decisions(state, now):
    tz = datetime.fromisoformat(now).tzinfo
    relays = _by_id(state["relays"])
    badges = relay_badges(state)
    rows = []
    for d in sort_decisions([d for d in state["decisions"] if not is_follow_up(d)], now):
        attrs, due = _due_parts(d, now, tz)
        since = f' · since {_esc(d["since"])}' if d.get("since") else ""
        rows.append(
            f'<li class="dec" data-mode="{_esc(d["relay_mode"])}"{attrs}>'
            f'<p class="dec__head">{_copy("id", d["id"])}<span class="dec__text">{_esc(d["description"])}</span>{_badge_html(badges, d["id"])}</p>'
            f'<p class="dec__meta"><span class="dec__src">{_esc(d["source"])}</span> · {due}{since}</p>'
            f'{_relay_guide(d, relays.get(d.get("relay_id")))}</li>')
    body = f'<ul class="dec-list">{"".join(rows)}</ul>' if rows else '<p class="empty">No decisions waiting.</p>'
    return (f'<section class="decisions" id="decisions" aria-labelledby="decisions-label"><h2 class="section-label" id="decisions-label">'
            f'Decisions <span class="count">{len(rows)}</span></h2>{body}</section>')


def _render_today(state):
    groups = {}
    for t in state["today"]:
        groups.setdefault(t["source"], []).append(t)
    blocks = []
    for source in sorted(groups):
        items = "".join(f"<li>{_link_or_text(t.get('link'), t['title'])}</li>" for t in groups[source])
        blocks.append(f'<div class="today__group"><h3 class="today__src">{_esc(source)}</h3><ul class="today__items">{items}</ul></div>')
    body = "".join(blocks) or '<p class="empty">Nothing collected for today.</p>'
    return (f'<section class="today" id="today" aria-labelledby="today-label"><h2 class="section-label" id="today-label">'
            f'Today <span class="count">{len(state["today"])}</span></h2>{body}</section>')


def _render_sources(state, now, missing):
    now_dt = datetime.fromisoformat(now)
    parts = []
    errors = state["secretary"].get("sources_error") or []
    if errors:
        items = "".join(f"<li><code>{_esc(e)}</code></li>" for e in errors)
        parts.append(f'<div class="notice" data-kind="error"><p>## Sources has errors; new queries and relays wait until CLAUDE.md is fixed.</p><ul>{items}</ul></div>')
    requests = _session_requests(state)
    if requests:
        parts.append('<ul class="requests">' + "".join(f'<li data-kind="request">{_esc(r)}</li>' for r in requests) + "</ul>")
    rows = []
    for s in state["sources"]:
        fetched = s.get("last_fetched_at")
        seconds = parse_interval(s.get("interval") or "")
        fetched_dt = _parse_instant(fetched)
        stale = seconds is not None and (fetched_dt is None or fetched_dt + timedelta(seconds=seconds) <= now_dt)
        flags = []
        if s["id"] in missing:
            flags.append('<span class="badge" data-relay="failed">directory not found</span>')
        if _in_flight(state, s["id"]):
            flags.append('<span class="badge" data-relay="sent">querying</span>')
        elif stale:
            flags.append('<span class="badge" data-relay="stale">stale: refreshed on your next message</span>')
        if s.get("last_error"):
            flags.append(f'<span class="badge" data-relay="failed">error</span> <span class="src__err">{_esc(s["last_error"])}</span>')
        next_due = ""
        if fetched_dt is not None and seconds is not None:
            next_due = f" · next {_time((fetched_dt + timedelta(seconds=seconds)).isoformat(), 'abs')}"
        rows.append(f'<li class="src"><p><b>{_esc(s["id"])}</b> <code>{_esc(s.get("directory"))}</code> {" ".join(flags)}</p>'
                    f'<p class="src__meta">every {_esc(s.get("interval"))} · fetched {_time(fetched) or "never"}{next_due}</p></li>')
    sup = state["supervisor"]
    link = f' · <a href="{_esc(sup["dashboard_url"])}">supervisor dashboard</a>' if _is_http_url(sup.get("dashboard_url")) else ""
    status = (f'<span class="badge" data-relay="failed">error</span> <span class="src__err">{_esc(sup["error"])}</span>' if sup.get("error")
              else f'read {_time(sup.get("read_at")) or "never"} · rev {_esc(sup.get("state_rev"))}')
    rows.append(f'<li class="src"><p><b>supervisor</b> <code>{_esc(sup.get("dir"))}</code></p><p class="src__meta">{status}{link}</p></li>')
    parts.append(f'<ul class="src-list">{"".join(rows)}</ul>')
    return (f'<section class="sources" id="sources" aria-labelledby="sources-label"><h2 class="section-label" id="sources-label">'
            f'Sources <span class="count">{len(state["sources"]) + 1}</span></h2>{"".join(parts)}</section>')


def render(state, template, now, missing=frozenset()):
    """Return (body, footer). body keeps the {{FOOTER}} slot so it can be hashed without rev/updated_at.

    `missing` holds the ids of sources whose directory does not exist.
    """
    tasks = _render_your_tasks(state, now)
    body = (
        template.replace("{{MAIN_ATTRS}}", "" if tasks else " data-no-tasks")
        .replace("{{HERO}}", _render_hero(state, now))
        .replace("{{YOUR_TASKS}}", tasks)
        .replace("{{DECISIONS}}", _render_decisions(state, now))
        .replace("{{TODAY}}", _render_today(state))
        .replace("{{SOURCES}}", _render_sources(state, now, missing))
    )
    rev = _esc(state.get("rev"))
    footer = (f'<!--sec-footer--><footer class="foot" data-rev="{rev}">rev {rev} · updated '
              f'{_time(state.get("updated_at"), "abs")}</footer><!--/sec-footer-->')
    return body, footer


def assemble(body, footer):
    """Copied from session-supervisor's state_tool.py."""
    return body.replace("{{FOOTER}}", footer)


def body_of(page):
    """Copied from session-supervisor's state_tool.py."""
    return FOOTER_RE.sub("{{FOOTER}}", page)


def digest(state, state_path, now, limit=DIGEST_LIMIT):
    sec = state.get("secretary", {})
    sup = state.get("supervisor", {})
    dashboard = sec.get("dashboard") or {}
    decisions = [d for d in state.get("decisions", []) if isinstance(d, dict)]
    lines = [
        f"[secretary] state rev {state.get('rev')} (updated {state.get('updated_at')}, now {now})",
        "Before acting: use the secretary skill, run its capability check, then refresh (merge-supervisor and reconcile sessions). "
        "If sessions[] is not empty, resume the self-paced /loop.",
        f"state: {state_path}",
        f"dashboard html: {dashboard.get('path')}",
        f"dashboard url: {dashboard.get('url') or '(not published)'}",
        f"secretary session_ids: {', '.join(sec.get('session_ids', [])) or '(none)'}",
        f"archive_consent: {'recorded' if sec.get('archive_consent') else 'missing (ask the user once)'}",
        f"sources_error: {'; '.join(sec.get('sources_error') or []) or '(none)'}",
        f"supervisor: {sup.get('dir')} read {sup.get('read_at') or 'never'} rev {sup.get('state_rev')}"
        + (f" error: {sup.get('error')}" if sup.get("error") else ""),
        "sources:",
    ]
    now_dt = datetime.fromisoformat(now)
    src_lines = []
    for s in state.get("sources", []):
        seconds = parse_interval(s.get("interval") or "")
        fetched = _parse_instant(s.get("last_fetched_at"))
        due = seconds is not None and (fetched is None or fetched + timedelta(seconds=seconds) <= now_dt)
        src_lines.append(f"- {s.get('id')} fetched {s.get('last_fetched_at') or 'never'}{' (stale: refreshed on your next message)' if due else ''}"
                         + (f" error: {s.get('last_error')}" if s.get("last_error") else ""))
    lines += src_lines or ["- (none)"]
    lines.append("sessions:")
    lines += [f"- {s.get('id')} {s.get('purpose')} {s.get('source_id')} {s.get('state')} {s.get('session_id')} {s.get('observed_state')}"
              for s in state.get("sessions", [])] or ["- (none)"]
    lines.append("requests to the user:")
    lines += [f"- {r}" for r in _session_requests(state)] or ["- (none)"]
    lines.append("overdue decisions:")
    lines += [f"- {d.get('id')}{' [your task]' if is_follow_up(d) else ''} due {d.get('due')}: {d.get('description')}"
              for d in decisions if is_overdue(d.get("due"), now)] or ["- (none)"]
    try:
        ordered = sort_decisions(decisions, now)
    except (KeyError, TypeError):
        ordered = decisions
    lines.append(f"decisions by due (top {DIGEST_DECISIONS}):")
    lines += [f"- {d.get('id')} [{d.get('relay_mode')}] due {d.get('due') or '-'} {d.get('source')}: {d.get('description')}"
              for d in [d for d in ordered if not is_follow_up(d)][:DIGEST_DECISIONS]] or ["- (none)"]
    lines.append(f"your tasks by due (top {DIGEST_DECISIONS}):")
    lines += [f"- {d.get('id')} due {d.get('due') or '-'}: {d.get('description')}"
              + (f" <{d['follow_up'].get('url')}>" if isinstance(d["follow_up"], dict) else "")
              for d in [d for d in ordered if is_follow_up(d)][:DIGEST_DECISIONS]] or ["- (none)"]
    lines.append("unconfirmed relays:")
    lines += [f"- {r.get('id')} {r.get('mode')} {r.get('status')} for {r.get('decision_id')}"
              for r in state.get("relays", []) if r.get("status") in ("pending", "sent", "awaiting_user", "failed", "unconfirmed")] or ["- (none)"]
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    marker = f"\n... (truncated; read {state_path})"
    return text[: limit - len(marker)] + marker


# ---------------------------------------------------------------- imperative shell


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_tmp(dir_path, text):
    """Copied from session-supervisor's state_tool.py."""
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
    """Copied from session-supervisor's state_tool.py."""
    os.replace(_write_tmp(os.path.dirname(os.path.abspath(path)), text), path)


def _beside(state_path, name):
    return os.path.join(os.path.dirname(os.path.abspath(state_path)), name)


def _load_sources(claude_md, state):
    try:
        text = Path(claude_md).read_text(encoding="utf-8")
    except OSError as e:
        return None, [f"CLAUDE.md: not readable ({e})"]
    return parse_sources(text, state["secretary"].get("dir"), state["supervisor"].get("dir"))


def _load_supervisor(state):
    """The supervisor's state.json opened read-only; (state, error)."""
    path = os.path.join(os.path.expanduser(state["supervisor"].get("dir") or ""), "state.json")
    if not os.path.exists(path):
        return None, f"supervisor not found at {path}"
    try:
        return _read_json(path), None
    except (OSError, ValueError) as e:
        return None, f"supervisor state not readable: {e}"


def _commit(state_path, candidate, claude_md, now, template_path, html_path):
    """Normalize sources, run next_state, and write the dashboard and state in two stages."""
    current = _read_json(state_path)
    errors = validate(current)
    if errors:
        raise StateError(["current state is invalid"] + (errors if isinstance(current, dict) else []))
    sources, sources_errors = _load_sources(claude_md, current)
    sv_state, _ = _load_supervisor(current)
    if sv_state is not None and supervisor_errors(sv_state):
        sv_state = None
    new = next_state(current, candidate, now, sources, sources_errors, sv_state)
    template = Path(template_path).read_text(encoding="utf-8")
    missing = {s["id"] for s in new["sources"] if s.get("directory") and not os.path.isdir(s["directory"])}
    body, footer = render(new, template, now, missing)
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


def _paths(args):
    return (args.claude_md or _beside(args.state, "CLAUDE.md"), args.template, args.html or _beside(args.state, "dashboard.html"))


def cmd_version(args):
    h = hashlib.sha256()
    for name in (Path(__file__).name, TEMPLATE_NAME):
        h.update((HERE / name).read_bytes())
    print(h.hexdigest()[:12])
    return 0


def cmd_commit(args):
    claude_md, template, html_path = _paths(args)
    print(json.dumps(_commit(args.state, _read_json(args.candidate), claude_md, _now(), template, html_path)))
    return 0


def cmd_merge_supervisor(args):
    claude_md, template, html_path = _paths(args)
    current = _read_json(args.state)
    now = _now()
    sv_state, error = _load_supervisor(current)
    if error:
        candidate = copy.deepcopy(current)
        candidate["supervisor"].update(error=error, read_at=now)
    else:
        candidate = merge_supervisor(current, sv_state, now)
    sup, new_sup = current["supervisor"], candidate["supervisor"]
    unchanged = new_sup.get("error") == sup.get("error") and (new_sup.get("error") or new_sup.get("state_rev") == sup.get("state_rev"))
    if unchanged:
        print(json.dumps({"rev": current["rev"], "committed": False}))
        return 0
    result = _commit(args.state, candidate, claude_md, now, template, html_path)
    print(json.dumps({**result, "committed": True}))
    return 0


def cmd_ingest_report(args):
    claude_md, template, html_path = _paths(args)
    current = _read_json(args.state)
    session = next((s for s in current.get("sessions", []) if s.get("id") == args.session), None)
    if session is None:
        raise StateError(f"session {args.session} is not in sessions[]")
    if session.get("last_message_ref") == args.message_ref:
        print(json.dumps({"rev": current["rev"], "outcome": "noop"}))
        return 0
    text = Path(args.file).read_text(encoding="utf-8")
    result = parse_report(text, session["source_id"])
    sources, _ = _load_sources(claude_md, current)
    relay = next((s["relay"] for s in sources or [] if s["id"] == session["source_id"]), None)
    now = _now()
    candidate, outcome = apply_report(current, args.session, args.message_ref, result, now, relay)
    committed = _commit(args.state, candidate, claude_md, now, template, html_path)
    print(json.dumps({**committed, "outcome": outcome}))
    if not result["ok"]:
        print(f"error: {result['error']}", file=sys.stderr)
        return 1
    return 0


def cmd_digest(args):
    try:
        state = _read_json(args.state)
        if not isinstance(state, dict):
            raise ValueError("state is not a JSON object")
        print(digest(state, os.path.abspath(args.state), _now()))
    except Exception as e:  # The hook must never fail the session start.
        print(f"[secretary] state not readable at {args.state} ({type(e).__name__}: {e}); use the secretary skill to recover.")
    return 0


def cmd_parse_report(args):
    text = Path(args.file).read_text(encoding="utf-8") if args.file != "-" else sys.stdin.read()
    result = parse_report(text, args.expect_source)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def _context_dirs(claude_md):
    state_path = _beside(claude_md, "state.json")
    sec_dir, sv_dir = os.path.dirname(os.path.abspath(claude_md)), None
    with contextlib.suppress(OSError, ValueError, AttributeError):
        state = _read_json(state_path)
        sec_dir, sv_dir = state["secretary"].get("dir") or sec_dir, state["supervisor"].get("dir")
    return sec_dir, sv_dir


def cmd_validate_sources(args):
    claude_md = args.claude_md or "CLAUDE.md"
    sources, errors = parse_sources(Path(claude_md).read_text(encoding="utf-8"), *_context_dirs(claude_md))
    errors += [f"CLAUDE.md:{s['lines']['directory']}: directory {s['directory']} does not exist" for s in sources if not os.path.isdir(s["directory"])]
    for err in errors:
        print(f"error: {err}", file=sys.stderr)
    if errors:
        return 1
    print(f"ok: {len(sources)} source{'s' if len(sources) != 1 else ''}")
    return 0


def cmd_due_sources(args):
    state = _read_json(args.state)
    sources, errors = _load_sources(args.claude_md or _beside(args.state, "CLAUDE.md"), state)
    for err in errors:
        print(f"warning: {err}", file=sys.stderr)
    if errors:
        print("[]")
        return 0
    due = due_sources(sources, state, _now())
    defs = {s["id"]: s for s in sources}
    missing = [i for i in due if not os.path.isdir(defs[i]["directory"])]
    for i in missing:
        print(f"warning: source {i}: directory {defs[i]['directory']} does not exist", file=sys.stderr)
    print(json.dumps([i for i in due if i not in missing]))
    return 0


def cmd_check_overlap(args):
    sv_state, error = _load_supervisor(_read_json(args.state))
    errors = [error] if error else supervisor_errors(sv_state)
    if errors:
        raise StateError(errors)
    print("busy" if worker_overlap(sv_state, _real(args.directory)) else "free")
    return 0


def _inside_git(path):
    """Copied from session-supervisor's state_tool.py."""
    p = Path(path).resolve()
    for candidate in (p, *p.parents):
        if (candidate / ".git").exists():
            return True
    return False


CLAUDE_MD = """# Secretary directory

This directory belongs to the secretary skill. It is not a project.
The rules live in the secretary skill and in the digest that the SessionStart hook prints; this file only lists the sources.

## Sources

<!--
Add one block per source. Every key is required.

### obsidian
- directory: ~/notes
- query: List today's tasks from the daily note and every item that waits on my decision.
- interval: 2h
- relay: yes
-->
"""


def cmd_setup(args):
    target = Path(os.path.abspath(os.path.expanduser(args.dir)))
    sv_dir = Path(os.path.abspath(os.path.expanduser(args.supervisor_dir)))
    if _inside_git(target):
        raise StateError(f"{target} is inside a git repository; choose a directory outside any repository")
    if _overlap(str(target), str(sv_dir)):
        raise StateError(f"{target} overlaps the supervisor directory {sv_dir}; choose a separate directory")
    settings_path = target / ".claude" / "settings.json"
    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise StateError(f"{settings_path} is not valid JSON ({e}); fix it and rerun setup")
        if not isinstance(settings, dict):
            raise StateError(f"{settings_path} must contain a JSON object")
        hooks = settings.get("hooks", {})
        session_start = hooks.get("SessionStart", []) if isinstance(hooks, dict) else None
        if not isinstance(session_start, list) or not all(
            isinstance(e, dict) and isinstance(e.get("hooks", []), list) and all(isinstance(h, dict) for h in e.get("hooks", []))
            for e in session_start
        ):
            raise StateError(f"{settings_path}: hooks.SessionStart must be a list of hook entries; fix it and rerun setup")

    report = []
    tool_dir = target / ".secretary"
    tool_dir.mkdir(parents=True, exist_ok=True)
    for name in (Path(__file__).name, TEMPLATE_NAME):
        dest = tool_dir / name
        src_bytes = (HERE / name).read_bytes()
        status = "exists" if dest.exists() and dest.read_bytes() == src_bytes else ("updated" if dest.exists() else "created")
        if status != "exists":
            shutil.copyfile(HERE / name, dest)
        report.append((status, dest))

    state_path = target / "state.json"
    command = f"python3 {shlex.quote(str(tool_dir / Path(__file__).name))} digest --state {shlex.quote(str(state_path))}"
    entries = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
    if any(h.get("command") == command for e in entries for h in e.get("hooks", [])):
        report.append(("exists", settings_path))
    else:
        existed = settings_path.exists()
        entries.append({"matcher": "startup|resume|clear|compact", "hooks": [{"type": "command", "command": command}]})
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(str(settings_path), json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
        report.append(("updated" if existed else "created", settings_path))

    if state_path.exists():
        report.append(("exists", state_path))
    else:
        _write_atomic(str(state_path), serialize(empty_state(str(target), str(sv_dir))))
        report.append(("created", state_path))

    claude_md = target / "CLAUDE.md"
    if claude_md.exists():
        report.append(("exists", claude_md))
    else:
        claude_md.write_text(CLAUDE_MD, encoding="utf-8")
        report.append(("created", claude_md))

    for status, path in report:
        print(f"{status}: {path}")
    if not (sv_dir / "state.json").exists():
        print(f"note: no supervisor state at {sv_dir / 'state.json'}; the secretary runs on external sources only until it exists", file=sys.stderr)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="state_tool.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    default_template = str(HERE / TEMPLATE_NAME)

    def committing(p):
        p.add_argument("--state", required=True)
        p.add_argument("--claude-md", help="CLAUDE.md with ## Sources (default: next to the state)")
        p.add_argument("--template", default=default_template)
        p.add_argument("--html", help="dashboard path (default: dashboard.html next to the state)")

    p = sub.add_parser("setup", help="install the secretary directory (idempotent)")
    p.add_argument("--dir", required=True)
    p.add_argument("--supervisor-dir", default="~/supervisor")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("version", help="print the sha256 prefix of this script and its template")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("validate-sources", help="check ## Sources and that each directory exists")
    p.add_argument("--claude-md", help="default: ./CLAUDE.md")
    p.set_defaults(func=cmd_validate_sources)

    p = sub.add_parser("due-sources", help="print the ids of sources that may be queried now (JSON list)")
    p.add_argument("--state", required=True)
    p.add_argument("--claude-md", help="default: next to the state")
    p.set_defaults(func=cmd_due_sources)

    p = sub.add_parser("check-overlap", help="print busy or free for a directory against the supervisor's non-git workers")
    p.add_argument("--state", required=True)
    p.add_argument("--directory", required=True)
    p.set_defaults(func=cmd_check_overlap)

    p = sub.add_parser("merge-supervisor", help="fold the supervisor's user actions in and commit when it changed")
    committing(p)
    p.set_defaults(func=cmd_merge_supervisor)

    p = sub.add_parser("ingest-report", help="record a session's secretary-report and commit")
    committing(p)
    p.add_argument("--session", required=True, help="S-NNNN")
    p.add_argument("--message-ref", required=True)
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_ingest_report)

    p = sub.add_parser("commit", help="validate a candidate and atomically write state.json and the dashboard")
    committing(p)
    p.add_argument("--candidate", required=True)
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("digest", help="print the SessionStart summary (always exits 0)")
    p.add_argument("--state", required=True)
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("parse-report", help="extract the last secretary-report block")
    p.add_argument("--file", required=True, help="text file with the session's latest message, or - for stdin")
    p.add_argument("--expect-source")
    p.set_defaults(func=cmd_parse_report)
    return parser


def main(argv=None):
    """Copied from session-supervisor's state_tool.py."""
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
