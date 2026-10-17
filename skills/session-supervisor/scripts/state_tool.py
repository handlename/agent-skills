#!/usr/bin/env python3
"""State tool for the session-supervisor skill.

Functional core: validate, next_state, render, digest, parse_report, cwd_busy, serialize.
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
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
ARCHIVED_KEEP = 20
DIGEST_LIMIT = 10000
HERE = Path(__file__).resolve().parent
TEMPLATE_NAME = "dashboard.template.html"

KINDS = ("git", "non-git")
DELIVERIES = ("push_pr", "commit", "files")
INTENDED_STATES = ("pending", "running", "archived")
OBSERVED_STATES = ("none", "running", "idle", "needs_input", "archived", "missing")
ACTIVE_OBSERVED = ("running", "idle", "needs_input", "missing")
TASK_STATUSES = ("needs_user", "running", "waiting", "done")
ARTIFACT_KINDS = ("branch", "commit", "pr", "ci", "file")
REPORT_STATUSES = ("done", "in_progress", "needs_input", "blocked", "failed")
REPORT_FIELDS = ("task_id", "worker_id", "status", "summary", "artifacts", "blockers", "next_action")
REPORT_ARTIFACT_KEYS = ("branch", "commit", "pr", "ci")

FOOTER_RE = re.compile(r"<!--sv-footer-->.*?<!--/sv-footer-->", re.S)
REPORT_RE = re.compile(r"```supervisor-report[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.S)
UA_ID_RE = re.compile(r"^UA-(\d+)$")


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
        },
        "tasks": [],
        "workers": [],
        "archived": [],
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


def cwd_busy(state, cwd):
    return any(_occupies_cwd(w) and _cwds_overlap(w["cwd"], cwd) for w in state.get("workers", []))


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
    # Workers given up as missing deliver nothing; without one verified archive the task needs a replacement or a cancel.
    if not any(w.get("observed_state") == "archived" for w in workers):
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
    return errors


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
    for t in state["tasks"]:
        if not isinstance(t.get("worker_ids", []), list) or not all(isinstance(i, str) for i in t.get("worker_ids", [])):
            errors.append(f"task {t['id']}: worker_ids must be a list of strings")
        if t.get("push_consent") is not None and not isinstance(t.get("push_consent"), dict):
            errors.append(f"task {t['id']}: push_consent must be an object or null")
        cancel = t.get("cancel")
        if cancel is not None and not (isinstance(cancel, dict) and all(isinstance(cancel.get(k), str) and cancel.get(k) for k in ("at", "reason"))):
            errors.append(f"task {t['id']}: cancel must be null or {{at, reason}} with non-empty strings")
    for w in state["workers"]:
        for key in ("task_id", "cwd"):
            if not isinstance(w.get(key), str):
                errors.append(f"worker {w['id']}: {key} must be a string")
        continues = w.get("continues", 0)
        if not isinstance(continues, int) or isinstance(continues, bool) or continues < 0:
            errors.append(f"worker {w['id']}: continues must be a non-negative integer")
    if not errors:
        ua_ids = [ua.get("id") for t in state["tasks"] for ua in t.get("user_actions", [])]
        if not all(isinstance(i, str) for i in ua_ids):
            errors.append("user_action ids must be strings")
    return errors


def validate(state):
    """Check the single-state invariants. Returns a list of error strings."""
    if not isinstance(state, dict):
        return ["state must be an object"]
    errors = []
    if state.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
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
    ua_ids = [ua.get("id") for t in tasks for ua in t.get("user_actions", [])]
    for label, ids in (("task", task_ids), ("worker", worker_ids), ("user_action", ua_ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1}, key=str)
        if dupes:
            errors.append(f"duplicate {label} ids: {dupes}")
    tasks_by_id = {t.get("id"): t for t in tasks}
    workers_by_id = {w.get("id"): w for w in workers}

    for task in tasks:
        tid = task.get("id")
        prefix = f"task {tid}"
        if task.get("status") is not None and task.get("status") not in TASK_STATUSES:
            errors.append(f"{prefix}: invalid status {task.get('status')!r}")
        if not task.get("worker_ids"):
            errors.append(f"{prefix}: worker_ids must not be empty")
        for wid in task.get("worker_ids", []):
            w = workers_by_id.get(wid)
            if w is None or w.get("task_id") != tid:
                errors.append(f"{prefix}: worker {wid} does not point back to the task")
        for ua in task.get("user_actions", []):
            if not ua.get("id") or not isinstance(ua.get("description"), str) or not ua["description"]:
                errors.append(f"{prefix}: user_action requires id and a description string")
        if task.get("cancel"):
            for wid in task.get("worker_ids", []):
                if workers_by_id.get(wid, {}).get("intended_state") == "running":
                    errors.append(f"{prefix}: cancelled but worker {wid} is still intended running")
        cwds = [w["cwd"] for w in workers if w.get("task_id") == tid and w.get("cwd")]
        for i, artifact in enumerate(task.get("artifacts", [])):
            errors += _validate_artifact(f"{prefix} artifact[{i}]", artifact, cwds)

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
            consent = (task.get("push_consent") or {}).get("text") or ""
            if not consent or consent not in (task.get("request") or ""):
                errors.append(f"{prefix}: push_pr requires push_consent.text quoted from the task request")
        # A vanished session produces no deliverables, so giving it up requires none.
        exempt = task is None or task.get("cancel") or w.get("observed_state") == "missing"
        if w.get("intended_state") == "archived" and not exempt:
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

    for stub in state["archived"]:
        for i, artifact in enumerate(stub.get("artifacts", [])):
            errors += _validate_artifact(f"archived {stub.get('id')} artifact[{i}]", artifact, None)
    return errors


def _is_push_decision(user_action, worker):
    desc = user_action.get("description", "")
    branch = worker.get("branch")
    return "push" in desc.lower() and (not isinstance(branch, str) or not branch or branch in desc)


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
    return errors


def _transition_errors(current, candidate):
    errors = []
    cur_workers = {w["id"]: w for w in current["workers"]}
    tasks = {t["id"]: t for t in candidate["tasks"]}
    for w in candidate["workers"]:
        if w["intended_state"] != "archived":
            continue
        if cur_workers.get(w["id"], {}).get("intended_state") == "archived":
            continue
        # Consent covers archives the supervisor performs; an archived or vanished session needs none.
        if w["observed_state"] not in ("archived", "missing") and not candidate["supervisor"].get("archive_consent"):
            errors.append(f"worker {w['id']}: archive requested without supervisor.archive_consent")
        task = tasks.get(w["task_id"], {})
        if w["delivery"] == "commit" and not task.get("cancel") and w["observed_state"] != "missing" and not any(_is_push_decision(ua, w) for ua in task.get("user_actions", [])):
            errors.append(f"worker {w['id']}: commit delivery archive requires a push-decision user_action")
    return errors


def waiting_start_description(task_id):
    return f"Waiting task {task_id} can start now: send any message to the supervisor to start it."


def _startable_workers(state, task, workers_by_id):
    if task.get("cancel"):
        return []
    return [
        w for w in (workers_by_id.get(wid) for wid in task["worker_ids"])
        if w and w["kind"] == "non-git" and w["intended_state"] == "pending" and w["observed_state"] == "none"
        and not cwd_busy(state, w["cwd"])
    ]


def _sync_waiting_start_actions(current, state, now):
    # Event-driven turns cannot start sessions, so when a non-git cwd frees up the
    # waiting task is surfaced as a user action and started on the next user message.
    # The action is dropped as soon as no worker of the task is startable any more
    # (it was started, removed, or its cwd is held again), so it never pins needs_user.
    used = [int(m.group(1)) for t in state["tasks"] for ua in t.get("user_actions", []) if (m := UA_ID_RE.match(ua["id"]))]
    next_num = max(used, default=0) + 1
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
    errors = validate(state)
    if errors:
        raise StateError(errors)
    errors = _deletion_errors(current, state) + _transition_errors(current, state)
    if errors:
        raise StateError(errors)

    _sync_waiting_start_actions(current, state, now)
    workers_by_id = {w["id"]: w for w in state["workers"]}
    for task in state["tasks"]:
        task["status"] = derive_status(task, workers_by_id)

    done = [t for t in state["tasks"] if t["status"] == "done"]
    done_ids = {t["id"] for t in done}
    stubs = []
    for t in done:
        stub = {"id": t["id"], "title": t.get("title"), "artifacts": t.get("artifacts", []), "archived_at": now}
        if t.get("cancel"):
            stub.update(cancelled=True, reason=t["cancel"]["reason"])
        stubs.append(stub)
    state["archived"] = (list(reversed(stubs)) + state["archived"])[:ARCHIVED_KEEP]
    state["tasks"] = [t for t in state["tasks"] if t["id"] not in done_ids]
    state["workers"] = [w for w in state["workers"] if w["task_id"] not in done_ids]

    state["rev"] = current["rev"] + 1
    state["updated_at"] = now
    return state


def _esc(value):
    # Braces are escaped too so state strings can never form a {{SLOT}} of the template.
    return html.escape("" if value is None else str(value)).replace("{", "&#123;").replace("}", "&#125;")


def _chip(state):
    return f'<span class="chip" data-state="{_esc(state)}">{_esc(state)}</span>'


def _link_or_text(url, text):
    if _is_http_url(url):
        return f'<a href="{_esc(url)}">{_esc(text)}</a>'
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


def _render_artifact_list(artifacts):
    if not artifacts:
        return ""
    items = "".join(f'<li data-kind="{_esc(a.get("kind"))}">{_render_artifact(a)}</li>' for a in artifacts)
    return f'<ul class="artifacts">{items}</ul>'


def _attention_items(state):
    """Everything waiting on the user: user actions, needs_input / missing workers, and workers archived outside the supervisor."""
    items = []
    for task in state["tasks"]:
        for ua in task.get("user_actions", []):
            items.append(("user", task, ua))
    for w in state["workers"]:
        if w.get("observed_state") in ("missing", "needs_input"):
            items.append(("worker", None, w))
        elif _archived_externally(w):
            items.append(("external", None, w))
    return items


def _render_hero(state):
    items = _attention_items(state)
    n = len(items)
    # data-pending only when something waits: it drives the only looping animation on the page.
    pending = f' data-pending="{n}"' if n else ""
    if n:
        label, sub = "Needs you", f"{n} item{'s' if n != 1 else ''} waiting on you"
    else:
        label, sub = "All clear", "Workers are on it. Nothing needs you right now."
    rows = []
    for i, (kind, task, obj) in enumerate(items):
        if kind == "user":
            rows.append(
                f'<li class="act" data-kind="user" style="--i:{i}"><span class="act__kind">Action</span>'
                f'<p class="act__text">{_esc(obj.get("description"))}</p>'
                f'<p class="act__meta"><span class="id">{_esc(task["id"])}</span><span class="id">{_esc(obj.get("id"))}</span>'
                f'<span>since {_time(obj.get("since"))}</span></p></li>')
            continue
        text = (f'is {_chip(obj["observed_state"])}' if kind == "worker"
                else "was archived outside the supervisor; its results need checking")
        rows.append(
            f'<li class="act" data-kind="{kind}" style="--i:{i}"><span class="act__kind">Worker</span>'
            f'<p class="act__text"><span class="id">{_esc(obj["id"])}</span> {_esc(obj.get("title"))} {text}</p>'
            f'<p class="act__meta"><span class="id">{_esc(obj["task_id"])}</span><code>{_esc(obj.get("cwd"))}</code></p></li>')
    body = (f'<ul class="hero__list">{"".join(rows)}</ul>' if rows
            else '<p class="hero__clear">Nothing to review, approve or answer.</p>')
    return (
        f'<section class="hero" id="actions"{pending} aria-labelledby="hero-label">'
        f'<span class="hero__sig" aria-hidden="true"></span>'
        f'<div class="hero__head"><p class="hero__count">{n}</p>'
        f'<div class="hero__words"><h2 class="hero__label" id="hero-label">{label}</h2><p class="hero__sub">{sub}</p></div></div>'
        f'{body}</section>')


def _segment_state(worker):
    if _finished(worker):
        return "done"
    # Archived outside the supervisor still needs a look, so it must not read as an empty slot.
    return "external" if _archived_externally(worker) else worker.get("observed_state")


def _render_report(r):
    if r is None:
        return '<p class="wk__report" data-report="none">No report yet</p>'
    if r.get("status") == "unparsed":
        return f'<p class="wk__report" data-report="unparsed"><span class="wk__rs">unparsed</span> {_esc(r.get("error"))}</p>'
    return (f'<p class="wk__report" data-report="{_esc(r.get("status"))}"><span class="wk__rs">{_esc(r.get("status"))}</span> '
            f'{_esc(r.get("summary"))}</p>')


def _render_worker(w):
    seg = _segment_state(w)
    branch = f' · <code>{_esc(w.get("branch"))}</code>' if w.get("branch") else ""
    return (
        f'<li class="wk" data-state="{_esc(seg)}"><span class="dot" data-state="{_esc(seg)}" aria-hidden="true"></span>'
        f'<div class="wk__body"><p class="wk__title"><span class="id">{_esc(w.get("id"))}</span> {_esc(w.get("title"))} {_chip(w.get("observed_state"))}</p>'
        f'<p class="wk__meta"><code>{_esc(w.get("cwd"))}</code>{branch} · {_esc(w.get("delivery"))}</p>'
        f'{_render_report(w.get("last_report"))}</div></li>')


def _render_tasks(state):
    tasks = state["tasks"]
    if not tasks:
        cards = '<p class="empty">No active tasks.</p>'
    else:
        workers_by_id = {w["id"]: w for w in state["workers"]}
        out = []
        for i, task in enumerate(tasks):
            workers = [workers_by_id[wid] for wid in task.get("worker_ids", []) if wid in workers_by_id]
            done = sum(1 for w in workers if _finished(w))
            segs = "".join(f'<span class="seg" data-state="{_esc(_segment_state(w))}"></span>' for w in workers)
            cancel = ""
            if task.get("cancel"):
                cancel = f'<p class="task__cancel">{_chip("cancelled")} {_esc(task["cancel"].get("reason"))}</p>'
            out.append(
                f'<article class="task" data-status="{_esc(task.get("status"))}"{" data-cancelled" if task.get("cancel") else ""} style="--i:{i}">'
                f'<header class="task__head"><span class="id task__id">{_esc(task["id"])}</span>'
                f'<h3 class="task__title">{_esc(task.get("title"))}</h3>{_chip(task.get("status"))}</header>'
                f'{cancel}'
                f'<div class="task__progress"><div class="task__segments" role="img" aria-label="{done} of {len(workers)} workers finished">{segs}</div>'
                f'<p class="task__progress-label"><b>{done}</b>/{len(workers)} done</p>'
                f'<p class="task__elapsed">started {_time(task.get("created_at"))}</p></div>'
                f'<p class="task__next"><span class="label">Next</span> {_esc(task.get("next_action"))}</p>'
                f'<ul class="task__workers">{"".join(_render_worker(w) for w in workers)}</ul>'
                f'{_render_artifact_list(task.get("artifacts", []))}</article>')
        cards = "".join(out)
    return (f'<section class="tasks" id="tasks" aria-labelledby="tasks-label"><h2 class="section-label" id="tasks-label">'
            f'Tasks <span class="count">{len(tasks)}</span></h2><div class="task-list">{cards}</div></section>')


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
            items.append(
                f'<li class="arc"{" data-cancelled" if stub.get("cancelled") else ""}><p class="arc__title"><span class="id">{_esc(stub["id"])}</span> '
                f'{_esc(stub.get("title"))}{cancelled}</p><p class="arc__meta">archived {_time(stub.get("archived_at"))}</p>'
                f'{_render_artifact_list(stub.get("artifacts", []))}</li>')
        rows = f'<ul class="arc-list">{"".join(items)}</ul>'
    return (f'<section class="archived" id="archived" aria-labelledby="archived-label"><h2 class="section-label" id="archived-label">'
            f'Archived <span class="count">{len(stubs)}</span></h2>{rows}</section>')


def render(state, template):
    """Return (body, footer). body keeps the {{FOOTER}} slot so it can be hashed without rev/updated_at."""
    body = (
        template.replace("{{HERO}}", _render_hero(state))
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
    actions = [f"- {t.get('id')} {ua.get('id')}: {ua.get('description')}" for t in state.get("tasks", []) for ua in t.get("user_actions", [])]
    lines += actions or ["- (none)"]
    lines.append("tasks:")
    task_lines = []
    for t in state.get("tasks", []):
        ws = "; ".join(
            f"{wid} {workers_by_id.get(wid, {}).get('session_id')} {workers_by_id.get(wid, {}).get('observed_state')}"
            for wid in t.get("worker_ids", [])
        )
        task_lines.append(f"- {t.get('id')} [{t.get('status')}] {t.get('title')} | next: {t.get('next_action')} | workers: {ws}")
    lines += task_lines or ["- (none)"]
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    marker = f"\n... (truncated; read {state_path})"
    return text[: limit - len(marker)] + marker


def parse_report(text, expect_task=None, expect_worker=None):
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
    if not isinstance(report["status"], str) or report["status"] not in REPORT_STATUSES:
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
    if report["status"] in ("needs_input", "blocked") and not report["blockers"]:
        return {"ok": False, "error": f"status {report['status']} requires at least one blocker"}
    if expect_task is not None and report["task_id"] != expect_task:
        return {"ok": False, "error": f"task_id {report['task_id']!r} does not match {expect_task!r}"}
    if expect_worker is not None and report["worker_id"] != expect_worker:
        return {"ok": False, "error": f"worker_id {report['worker_id']!r} does not match {expect_worker!r}"}
    return {"ok": True, "report": report}


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


def cmd_commit(args):
    html_path = args.html or _default_html(args.state)
    template = Path(args.template).read_text(encoding="utf-8")
    new = next_state(_read_json(args.state), _read_json(args.candidate), _now())
    body, footer = render(new, template)
    old_body = body_of(Path(html_path).read_text(encoding="utf-8")) if os.path.exists(html_path) else None
    html_tmp = _write_tmp(os.path.dirname(os.path.abspath(html_path)), assemble(body, footer))
    try:
        state_tmp = _write_tmp(os.path.dirname(os.path.abspath(args.state)), serialize(new))
        try:
            os.replace(state_tmp, args.state)
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
    print(json.dumps(result))
    return 0


def cmd_digest(args):
    try:
        state = _read_json(args.state)
        if not isinstance(state, dict):
            raise ValueError("state is not a JSON object")
        print(digest(state, os.path.abspath(args.state)))
    except Exception as e:  # The hook must never fail the session start.
        print(f"[session-supervisor] state not readable at {args.state} ({type(e).__name__}: {e}); use the session-supervisor skill to recover.")
    return 0


def cmd_parse_report(args):
    text = Path(args.file).read_text(encoding="utf-8") if args.file != "-" else sys.stdin.read()
    result = parse_report(text, args.expect_task, args.expect_worker)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def cmd_check_cwd(args):
    print("busy" if cwd_busy(_read_json(args.state), _real(args.cwd)) else "free")
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
"""


def cmd_setup(args):
    target = Path(os.path.abspath(os.path.expanduser(args.dir)))
    if _inside_git(target):
        raise StateError(f"{target} is inside a git repository; choose a directory outside any repository")
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
    command = f"python3 {shlex.quote(str(sv_dir / Path(__file__).name))} digest --state {shlex.quote(str(state_path))}"
    entries = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
    has_hook = any(h.get("command") == command for e in entries for h in e.get("hooks", []))
    if has_hook:
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
        _write_atomic(str(state_path), serialize(empty_state(str(target))))
        report.append(("created", state_path))

    claude_md = target / "CLAUDE.md"
    if claude_md.exists():
        report.append(("exists", claude_md))
    else:
        claude_md.write_text(CLAUDE_MD, encoding="utf-8")
        report.append(("created", claude_md))

    for status, path in report:
        print(f"{status}: {path}")
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
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("digest", help="print the SessionStart summary (always exits 0)")
    p.add_argument("--state", required=True)
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("parse-report", help="extract the last supervisor-report block")
    p.add_argument("--file", required=True, help="text file with the worker's latest messages, or - for stdin")
    p.add_argument("--expect-task")
    p.add_argument("--expect-worker")
    p.set_defaults(func=cmd_parse_report)

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
