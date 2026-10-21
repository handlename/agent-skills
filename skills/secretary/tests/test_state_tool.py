import contextlib
import copy
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SKILL = TESTS.parent
SCRIPT = SKILL / "scripts" / "state_tool.py"
TEMPLATE = (SKILL / "scripts" / "dashboard.template.html").read_text(encoding="utf-8")
FIXTURES = TESTS / "fixtures"
sys.path.insert(0, str(SKILL / "scripts"))

import state_tool as st  # noqa: E402

NOW = "2026-10-18T09:30:00+09:00"
DUE_AT = "2026-10-18T10:05:00+09:00"
SEC_DIR = "/Users/handlename/secretary"
SV_DIR = "/Users/handlename/supervisor"


def fixture_state():
    return json.loads((FIXTURES / "state_valid.json").read_text(encoding="utf-8"))


def fixture_sv():
    return json.loads((FIXTURES / "supervisor_state.json").read_text(encoding="utf-8"))


def fixture_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def defs():
    sources, errors = st.parse_sources(fixture_text("claude_md_valid.md"), SEC_DIR, SV_DIR)
    assert not errors, errors
    return sources


def free_sv():
    sv = fixture_sv()
    for w in sv["workers"]:
        w["observed_state"] = "archived"
    return sv


def find(items, item_id):
    return next(i for i in items if i["id"] == item_id)


def add_session(state, purpose="query", source_id="obsidian", sid=None, status="pending", relay_id=None, created_at=NOW, **extra):
    if sid is None:
        state["counters"]["session"] += 1
        sid = f"S-{state['counters']['session']:04d}"
    session = {"id": sid, "purpose": purpose, "source_id": source_id, "relay_id": relay_id, "session_id": None,
               "state": status, "created_at": created_at, "started_by": "sess_sec_01", "observed_state": "none",
               "last_message_ref": None, "resend_requested": False}
    session.update(extra)
    state["sessions"].append(session)
    return session


def add_relay(state, decision_id, mode, status=None, words="今週やる", at=NOW, rid=None, **extra):
    if rid is None:
        state["counters"]["relay"] += 1
        rid = f"R-{state['counters']['relay']:04d}"
    d = find(state["decisions"], decision_id)
    relay = {"id": rid, "decision_id": decision_id, "decision_key": d["key"], "mode": mode,
             "target": {"kind": "supervisor"} if mode == "paste" else {"kind": "source", "source_id": d["source"]},
             "words": words, "at": at, "status": status or st.RELAY_NEW_STATUS.get(mode, "pending"), "session_closed_at": None}
    relay.update(extra)
    state["relays"].insert(0, relay)
    d["relay_id"] = rid
    return relay


def commit(current, candidate, now=NOW, sources=None, errors=None, sv=None):
    return st.next_state(current, candidate, now, defs() if sources is None else sources, errors or [], free_sv() if sv is None else sv)


def query_report(today=(), decisions=(), status="done", blockers=()):
    return {"source_id": "obsidian", "mode": "query", "status": status, "today": list(today), "decisions": list(decisions),
            "summary": "", "blockers": list(blockers)}


def at(base, minutes):
    return (datetime.fromisoformat(base) + timedelta(minutes=minutes)).isoformat()


PR_URL = "https://github.com/org/repo-a/pull/42"


def follow_up_sv(description="Review org/repo-a#42", task_id="T-0003", ua_id="UA-0004", since="2026-10-18T09:25:00+09:00", url=PR_URL):
    sv = fixture_sv()
    find(sv["tasks"], task_id)["user_actions"].append(
        {"id": ua_id, "description": description, "since": since, "follow_up": {"rule": "review-pr", "url": url, "done": "pr_closed"}})
    return sv


def by_ref(decisions, task_id, ua_id):
    return next(d for d in decisions if d["ref"] == {"task_id": task_id, "ua_id": ua_id})


class SourcesTest(unittest.TestCase):
    def test_sources_valid(self):
        sources = defs()
        self.assertEqual([s["id"] for s in sources], ["obsidian", "mail"])
        obs = sources[0]
        self.assertEqual((obs["directory"], obs["interval"], obs["relay"]), ("/Users/handlename/notes", "2h", True))
        self.assertFalse(sources[1]["relay"])
        self.assertEqual(st.parse_sources("# no sources here\n"), ([], []))
        home = os.path.expanduser("~")
        tilde, errors = st.parse_sources("## Sources\n### n\n- directory: ~/notes\n- query: q\n- interval: 15m\n- relay: no\n", SEC_DIR, SV_DIR)
        self.assertEqual(errors, [])
        self.assertEqual(tilde[0]["directory"], os.path.join(home, "notes"))
        self.assertEqual(st.parse_sources(st.CLAUDE_MD), ([], []))

    def test_sources_errors_with_line_numbers(self):
        _, errors = st.parse_sources(fixture_text("claude_md_invalid_entries.md"), SEC_DIR, SV_DIR)
        expected = {
            5: "invalid source id", 11: "reserved", 22: "unknown key 'colour'", 24: "missing 'interval'",
            32: "at least 15m", 33: "relay must be yes or no", 36: "secretary directory", 42: "supervisor directory",
            47: "duplicate source id 'short'", 52: "duplicate key 'query'",
        }
        for line, needle in expected.items():
            self.assertTrue(any(e.startswith(f"CLAUDE.md:{line}: ") and needle in e for e in errors), (line, needle, errors))
        self.assertEqual(len(errors), len(expected), errors)
        sources, errors = st.parse_sources(fixture_text("claude_md_invalid_duplicate_heading.md"))
        self.assertEqual(sources, [])
        self.assertEqual(errors, ["CLAUDE.md:11: duplicate '## Sources' heading"])
        _, errors = st.parse_sources(fixture_text("claude_md_invalid_no_heading.md"))
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("CLAUDE.md:2: "), errors)
        _, errors = st.parse_sources("## Sources\n### a\n- directory: relative/x\n- query: q\n- interval: 1h\n- relay: no\n")
        self.assertIn("absolute path", errors[0])
        _, errors = st.parse_sources("## Sources\n### a\n- directory: /Users\n- query: q\n- interval: 1h\n- relay: no\n", SEC_DIR, SV_DIR)
        self.assertIn("ancestors", errors[0])

    def test_sources_error_does_not_block_unrelated_commit(self):
        current = fixture_state()
        cand = copy.deepcopy(current)
        find(cand["decisions"], "D-0011")["due"] = "2026-10-20"
        _, errors = st.parse_sources(fixture_text("claude_md_invalid_entries.md"), SEC_DIR, SV_DIR)
        new = commit(current, cand, sources=[], errors=errors)
        self.assertEqual(find(new["decisions"], "D-0011")["due"], "2026-10-20")
        self.assertEqual(new["secretary"]["sources_error"], errors)
        self.assertEqual(new["sources"], current["sources"])
        self.assertEqual(new["today"], current["today"])

    def test_sources_error_blocks_new_query(self):
        current = fixture_state()
        cand = copy.deepcopy(current)
        add_session(cand, source_id="obsidian")
        with self.assertRaisesRegex(st.StateError, "Sources has errors"):
            commit(current, cand, now=DUE_AT, sources=[], errors=["CLAUDE.md:3: x"])
        cand = copy.deepcopy(current)
        add_relay(cand, "D-0010", "start")
        with self.assertRaisesRegex(st.StateError, "Sources has errors"):
            commit(current, cand, sources=[], errors=["CLAUDE.md:3: x"])

    def test_source_removed_from_claude_md_drops_its_items(self):
        current = fixture_state()
        new = commit(current, copy.deepcopy(current), sources=[s for s in defs() if s["id"] == "mail"])
        self.assertEqual([s["id"] for s in new["sources"]], ["mail"])
        self.assertNotIn("D-0010", [d["id"] for d in new["decisions"]])
        self.assertEqual([t["source"] for t in new["today"]], ["mail"])
        self.assertEqual(len(new["relays"]), len(current["relays"]))


class DueSourcesTest(unittest.TestCase):
    def test_due_sources_boundaries(self):
        state = fixture_state()
        self.assertEqual(st.due_sources(defs(), state, at(DUE_AT, -1)), [])
        self.assertEqual(st.due_sources(defs(), state, DUE_AT), ["obsidian"])
        find(state["sources"], "obsidian")["last_fetched_at"] = None
        self.assertEqual(st.due_sources(defs(), state, NOW), ["obsidian"])
        find(state["sources"], "obsidian").update(last_error="start refused", last_attempt_at=NOW)
        self.assertEqual(st.due_sources(defs(), state, at(NOW, 14)), [])
        self.assertEqual(st.due_sources(defs(), state, at(NOW, 15)), ["obsidian"])
        state["sources"] = []
        self.assertEqual(st.due_sources(defs(), state, NOW), ["obsidian", "mail"])

    def test_query_session_rejected_before_interval(self):
        current = fixture_state()
        cand = copy.deepcopy(current)
        add_session(cand)
        with self.assertRaisesRegex(st.StateError, "not due yet"):
            commit(current, cand, now=at(DUE_AT, -1))
        new = commit(current, cand, now=DUE_AT)
        self.assertEqual(new["sessions"][0]["state"], "pending")

    def test_second_query_for_same_source_rejected(self):
        current = fixture_state()
        cand = copy.deepcopy(current)
        add_session(cand)
        first = commit(current, cand, now=DUE_AT)
        cand = copy.deepcopy(first)
        add_session(cand)
        with self.assertRaisesRegex(st.StateError, "already has a pending or running session"):
            commit(first, cand, now=DUE_AT)
        cand = copy.deepcopy(current)
        add_session(cand)
        add_session(cand)
        with self.assertRaisesRegex(st.StateError, "already has a pending or running session"):
            commit(current, cand, now=DUE_AT)

    def test_new_session_must_start_pending_and_running_needs_session_id(self):
        current = fixture_state()
        cand = copy.deepcopy(current)
        add_session(cand, status="running")
        with self.assertRaisesRegex(st.StateError, "committed as pending"):
            commit(current, cand, now=DUE_AT)
        cand = copy.deepcopy(current)
        add_session(cand)
        first = commit(current, cand, now=DUE_AT)
        cand = copy.deepcopy(first)
        cand["sessions"][0]["state"] = "running"
        with self.assertRaisesRegex(st.StateError, "running requires session_id"):
            commit(first, cand, now=DUE_AT)
        cand["sessions"][0]["session_id"] = "sess_q_09"
        self.assertEqual(commit(first, cand, now=DUE_AT)["sessions"][0]["state"], "running")


class SupervisorMergeTest(unittest.TestCase):
    def test_merge_supervisor_add_update_remove(self):
        state = fixture_state()
        sv = fixture_sv()
        find(sv["tasks"], "T-0003")["user_actions"][0]["description"] = "Decide whether to push branch claude/license-x1 (rebased)"
        find(sv["tasks"], "T-0004")["user_actions"] = []
        merged = st.merge_supervisor(state, sv, NOW)
        ids = [d["id"] for d in merged["decisions"]]
        self.assertNotIn("D-0012", ids)
        d11 = find(merged["decisions"], "D-0011")
        self.assertEqual(d11["description"], "Decide whether to push branch claude/license-x1 (rebased)")
        self.assertEqual((d11["due"], d11["relay_id"]), ("2026-10-19", "R-0003"))
        new = find(merged["decisions"], "D-0013")
        self.assertEqual(new["key"], "supervisor/T-0005/UA-0003@2026-10-18T09:20:00+09:00")
        self.assertEqual((new["ref"], new["relay_mode"], new["due"]), ({"task_id": "T-0005", "ua_id": "UA-0003"}, "paste", None))
        self.assertEqual(merged["counters"]["decision"], 13)
        self.assertEqual(merged["supervisor"]["state_rev"], 42)
        self.assertEqual(merged["supervisor"]["read_at"], NOW)
        self.assertEqual([t["key"] for t in merged["today"]], [t["key"] for t in state["today"]])
        self.assertEqual(commit(state, merged)["rev"], 8)

    def test_supervisor_key_includes_since(self):
        state = fixture_state()
        sv = fixture_sv()
        find(sv["tasks"], "T-0003")["user_actions"][0]["since"] = "2026-10-18T09:00:00+09:00"
        merged = st.merge_supervisor(state, sv, NOW)
        self.assertNotIn("D-0011", [d["id"] for d in merged["decisions"]])
        reused = [d for d in merged["decisions"] if d["ref"] == {"task_id": "T-0003", "ua_id": "UA-0002"}]
        self.assertEqual(len(reused), 1)
        self.assertEqual(reused[0]["due"], None)
        self.assertEqual(find(merged["relays"], "R-0003")["status"], "confirmed")

    def test_supervisor_errors_keep_previous_items(self):
        state = fixture_state()
        for broken in ({"schema_version": 2}, [], {**fixture_sv(), "tasks": {}},
                       {**fixture_sv(), "tasks": [{"id": "T-1", "title": "t", "status": "x", "user_actions": [{"id": 1}]}]}):
            merged = st.merge_supervisor(state, broken, NOW)
            self.assertEqual(merged["decisions"], state["decisions"])
            self.assertTrue(merged["supervisor"]["error"])
            self.assertEqual(merged["supervisor"]["state_rev"], 41)

    def test_merge_keeps_user_due(self):
        state = fixture_state()
        find(state["decisions"], "D-0012")["due"] = "2026-10-21T18:00:00+09:00"
        sv = fixture_sv()
        find(sv["tasks"], "T-0004")["user_actions"][0]["description"] = "Please archive w-0007 from the app (retry)"
        merged = st.merge_supervisor(state, sv, NOW)
        self.assertEqual(find(merged["decisions"], "D-0012")["due"], "2026-10-21T18:00:00+09:00")
        merged = st.merge_source(state, "obsidian", query_report(decisions=[{"id": "inbox#12", "description": "x", "since": ""}]),
                                 {"created_at": NOW}, NOW, True)
        self.assertEqual(find(merged["decisions"], "D-0010")["due"], "2026-10-17")

    def test_reclassify_on_description_update(self):
        state = fixture_state()
        sv = fixture_sv()
        find(sv["tasks"], "T-0003")["user_actions"][0]["description"] = "Please archive w-0004 in the app"
        merged = st.merge_supervisor(state, sv, NOW)
        self.assertEqual(find(merged["decisions"], "D-0011")["relay_mode"], "app")
        find(sv["tasks"], "T-0003")["user_actions"][0]["description"] = "w-0004 is blocked: which colour?"
        merged = st.merge_supervisor(merged, sv, NOW)
        self.assertEqual(find(merged["decisions"], "D-0011")["relay_mode"], "paste")

    def test_classify_supervisor_ua(self):
        cases = {
            "Please archive w-0004": "app",
            "Unpin w-0004 so it can be archived": "app",
            "Open repo /repo/a in the app to accept the worktree": "app",
            "Decide whether to push branch claude/license-x1 of /repo/a (and whether to open a PR)": "paste",
            "Waiting task T-0004 can start now: send any message to the supervisor to start it.": "paste",
            "Replace it or drop the task?": "paste",
            "w-0005 is not progressing": "paste",
            "Which colour should the button be? (blocker from w-0005)": "paste",
            "Restart w-0008 or cancel T-0005": "paste",
            "": "paste",
        }
        for text, expected in cases.items():
            self.assertEqual(st.classify_supervisor_ua(text), expected, text)
        self.assertNotIn("send", {st.classify_supervisor_ua(t) for t in cases})

    def test_paste_relay_confirmed_when_supervisor_drops_ua(self):
        state = fixture_state()
        sv = fixture_sv()
        find(sv["tasks"], "T-0003")["user_actions"] = []
        merged = st.merge_supervisor(state, sv, NOW)
        self.assertNotIn("D-0011", [d["id"] for d in merged["decisions"]])
        self.assertEqual(find(merged["relays"], "R-0003")["status"], "confirmed")
        self.assertNotIn("D-0011", st.relay_badges(merged))

    def test_relayed_item_stays_listed_with_badge(self):
        state = fixture_state()
        merged = st.merge_supervisor(state, fixture_sv(), NOW)
        self.assertIn("D-0011", [d["id"] for d in merged["decisions"]])
        self.assertEqual(st.relay_badges(merged)["D-0011"]["label"], "paste pending")
        page = st.assemble(*st.render(merged, TEMPLATE, NOW))
        self.assertIn("paste pending", page)

    def test_paste_pending_never_times_out(self):
        current = fixture_state()
        later = "2026-12-31T23:00:00+09:00"
        new = commit(current, st.merge_supervisor(current, fixture_sv(), later), now=later)
        self.assertEqual(find(new["relays"], "R-0003")["status"], "awaiting_user")
        self.assertEqual(st.relay_badges(new)["D-0011"]["label"], "paste pending")


class SupervisorFileTest(unittest.TestCase):
    def test_supervisor_state_untouched(self):
        with Env() as env:
            sv_path = env.sv_dir / "state.json"
            before = (sv_path.read_bytes(), sv_path.stat().st_mtime_ns, sorted(os.listdir(env.sv_dir)))
            code, out, err = env.run("merge-supervisor", "--state", str(env.state))
            self.assertEqual(code, 0, err)
            self.assertTrue(json.loads(out)["committed"])
            self.assertEqual((sv_path.read_bytes(), sv_path.stat().st_mtime_ns, sorted(os.listdir(env.sv_dir))), before)
            code, out, _ = env.run("merge-supervisor", "--state", str(env.state))
            self.assertEqual(json.loads(out), {"rev": 8, "committed": False})

    def test_missing_supervisor_is_recorded(self):
        with Env() as env:
            (env.sv_dir / "state.json").unlink()
            code, out, _ = env.run("merge-supervisor", "--state", str(env.state))
            self.assertEqual(code, 0)
            state = env.current()
            self.assertIn("supervisor not found", state["supervisor"]["error"])
            self.assertEqual(len(state["decisions"]), 3)


class SourceMergeTest(unittest.TestCase):
    def report(self):
        return st.parse_report(fixture_text("report_query_ok.txt"), "obsidian")["report"]

    def test_merge_source_replaces_today(self):
        state = fixture_state()
        merged = st.merge_source(state, "obsidian", self.report(), {"created_at": NOW}, NOW, True)
        self.assertEqual([t["key"] for t in merged["today"]], ["mail/thread-77", "obsidian/daily-2026-10-18#5", "obsidian/daily-2026-10-18#6"])
        self.assertEqual(find(merged["sources"], "obsidian")["last_fetched_at"], NOW)
        self.assertIsNone([t for t in merged["today"] if t["key"].endswith("#6")][0]["link"])
        d10 = find(merged["decisions"], "D-0010")
        self.assertEqual((d10["description"], d10["relay_mode"]), ("打ち合わせを今週やるか(更新)", "start"))
        d13 = find(merged["decisions"], "D-0013")
        self.assertEqual((d13["key"], d13["since"], d13["relay_mode"]), ("obsidian/inbox#13", None, "start"))
        self.assertEqual(commit(state, merged)["rev"], 8)

    def test_item_removed_when_source_drops_it(self):
        state = fixture_state()
        add_relay(state, "D-0010", "start", status="sent")
        merged = st.merge_source(state, "obsidian", query_report(), {"created_at": NOW}, NOW, True)
        self.assertNotIn("D-0010", [d["id"] for d in merged["decisions"]])
        self.assertEqual(find(merged["relays"], "R-0004")["status"], "confirmed")
        self.assertIn("D-0011", [d["id"] for d in merged["decisions"]])


class RelayTest(unittest.TestCase):
    def test_relay_mode_allowed_sets(self):
        current = fixture_state()
        cand = copy.deepcopy(current)
        find(cand["decisions"], "D-0011")["relay_id"] = None
        cand["relays"] = [r for r in cand["relays"] if r["id"] != "R-0003"]
        base = commit(current, cand)
        cand = copy.deepcopy(base)
        add_relay(cand, "D-0011", "paste")
        self.assertEqual(commit(base, cand)["relays"][0]["mode"], "paste")
        cand = copy.deepcopy(base)
        add_relay(cand, "D-0010", "start")
        self.assertEqual(commit(base, cand)["relays"][0]["status"], "pending")
        cand = copy.deepcopy(base)
        add_relay(cand, "D-0011", "start", status="pending")
        with self.assertRaisesRegex(st.StateError, "not allowed for a 'paste' decision"):
            commit(base, cand)
        cand = copy.deepcopy(base)
        add_relay(cand, "D-0010", "paste")
        with self.assertRaisesRegex(st.StateError, "not allowed for a 'start' decision"):
            commit(base, cand)
        cand = copy.deepcopy(base)
        add_relay(cand, "D-0011", "paste", words="  ")
        with self.assertRaisesRegex(st.StateError, "words"):
            commit(base, cand)

    def test_send_mode_rejected(self):
        current = fixture_state()
        cand = copy.deepcopy(current)
        add_relay(cand, "D-0011", "send", status="sent")
        with self.assertRaisesRegex(st.StateError, "unknown mode 'send'"):
            commit(current, cand)

    def test_relay_mode_comes_from_the_script_not_the_candidate(self):
        current = fixture_state()
        cand = copy.deepcopy(current)
        find(cand["decisions"], "D-0012")["relay_mode"] = "paste"
        add_relay(cand, "D-0012", "paste")
        with self.assertRaisesRegex(st.StateError, "D-0012: relay_mode must be 'app', got 'paste'"):
            commit(current, cand)
        cand = copy.deepcopy(current)
        find(cand["decisions"], "D-0010")["relay_mode"] = "none"
        with self.assertRaisesRegex(st.StateError, "D-0010: relay_mode must be 'start', got 'none'"):
            commit(current, cand)

    def test_recorded_relay_mode_and_words_cannot_change(self):
        current = fixture_state()
        for key, value in (("mode", "send"), ("words", "やっぱりやめる"), ("decision_id", "D-0010")):
            cand = copy.deepcopy(current)
            find(cand["relays"], "R-0003")[key] = value
            with self.assertRaisesRegex(st.StateError, f"relay R-0003: {key} cannot change"):
                commit(current, cand)
        cand = copy.deepcopy(current)
        find(cand["relays"], "R-0003")["status"] = "failed"
        self.assertEqual(find(commit(current, cand)["relays"], "R-0003")["status"], "failed")

    def test_app_and_none_cannot_be_relayed(self):
        current = fixture_state()
        current["decisions"].append({"id": "D-0012", "key": "mail/thread-80", "source": "mail", "ref": {"item_id": "thread-80"},
                                     "description": "返信するか", "since": "", "due": None, "relay_mode": "none", "relay_id": None})
        current["decisions"] = [d for d in current["decisions"] if d["key"] != "supervisor/T-0004/UA-0001@2026-10-17T11:00:00+09:00"] + [
            {**find(fixture_state()["decisions"], "D-0012"), "id": "D-0005"}]
        self.assertEqual(st.validate(current), [])
        for decision_id, mode in (("D-0005", "paste"), ("D-0012", "start")):
            cand = copy.deepcopy(current)
            add_relay(cand, decision_id, mode)
            with self.assertRaisesRegex(st.StateError, "not allowed"):
                commit(current, cand)

    def test_start_relay_rejected_while_query_running(self):
        current = fixture_state()
        add_session(current, sid="S-0005", status="running", session_id="sess_q")
        cand = copy.deepcopy(current)
        relay = add_relay(cand, "D-0010", "start")
        add_session(cand, purpose="relay", relay_id=relay["id"])
        with self.assertRaisesRegex(st.StateError, "already has a pending or running session"):
            commit(current, cand)

    def test_relay_session_rejected_when_supervisor_state_unreadable(self):
        current = fixture_state()
        cand = copy.deepcopy(current)
        relay = add_relay(cand, "D-0010", "start")
        add_session(cand, purpose="relay", relay_id=relay["id"])
        self.assertEqual(commit(current, cand)["sessions"][0]["purpose"], "relay")
        with self.assertRaisesRegex(st.StateError, "supervisor state is unreadable"):
            st.next_state(current, cand, NOW, defs(), [], None)
        with self.assertRaisesRegex(st.StateError, "overlaps a running non-git worker"):
            commit(current, cand, sv=fixture_sv())
        other = copy.deepcopy(current)
        find(other["decisions"], "D-0011")["due"] = "2026-10-25"
        self.assertEqual(st.next_state(current, other, NOW, defs(), [], None)["rev"], 8)
        with Env() as env:
            (env.sv_dir / "state.json").write_text("{broken", encoding="utf-8")
            cand = env.current()
            relay = add_relay(cand, "D-0010", "start")
            add_session(cand, purpose="relay", relay_id=relay["id"])
            code, _, err = env.commit(cand)
            self.assertEqual(code, 1)
            self.assertIn("unreadable", err)
            cand = env.current()
            find(cand["decisions"], "D-0011")["due"] = "2026-10-25"
            code, out, err = env.commit(cand)
            self.assertEqual(code, 0, err)

    def test_relay_ingest_does_not_touch_today(self):
        state = fixture_state()
        relay = add_relay(state, "D-0010", "start", status="sent")
        add_session(state, purpose="relay", sid="S-0005", relay_id=relay["id"], status="running", session_id="sess_r")
        result = st.parse_report(fixture_text("report_relay_done.txt"), "obsidian")
        new, outcome = st.apply_report(state, "S-0005", "msg_1", result, NOW, True)
        self.assertEqual(outcome, "relay_done")
        self.assertEqual((new["today"], new["decisions"]), (state["today"], state["decisions"]))
        self.assertEqual(find(new["relays"], relay["id"])["status"], "sent")
        self.assertEqual(new["sessions"][0]["state"], "closing")

    def test_start_relay_done_resets_last_fetched_at(self):
        state = fixture_state()
        relay = add_relay(state, "D-0010", "start", status="sent")
        add_session(state, purpose="relay", sid="S-0005", relay_id=relay["id"], status="running", session_id="sess_r")
        result = st.parse_report(fixture_text("report_relay_done.txt"), "obsidian")
        new, _ = st.apply_report(state, "S-0005", "msg_1", result, NOW, True)
        self.assertIsNone(find(new["sources"], "obsidian")["last_fetched_at"])
        self.assertIn("obsidian", st.due_sources(defs(), new, NOW))

    def test_pending_relay_becomes_sent_once_its_session_runs(self):
        current = fixture_state()
        relay = add_relay(current, "D-0010", "start")
        add_session(current, purpose="relay", sid="S-0005", relay_id=relay["id"])
        cand = copy.deepcopy(current)
        cand["sessions"][0].update(state="running", session_id="sess_recovered")
        new = commit(current, cand)
        self.assertEqual(find(new["relays"], relay["id"])["status"], "sent")
        result = st.parse_report(fixture_text("report_relay_done.txt"), "obsidian")
        reported, outcome = st.apply_report(cand, "S-0005", "msg_1", result, NOW, True)
        self.assertEqual(outcome, "relay_done")
        self.assertEqual(find(commit(current, reported)["relays"], relay["id"])["status"], "sent")

    def test_relay_failure_marks_failed_with_blockers(self):
        state = fixture_state()
        relay = add_relay(state, "D-0010", "start", status="sent")
        add_session(state, purpose="relay", sid="S-0005", relay_id=relay["id"], status="running", session_id="sess_r")
        result = {"ok": True, "report": {"source_id": "obsidian", "mode": "relay", "status": "needs_input", "summary": "",
                                         "blockers": ["inbox#12 was already deleted"]}}
        new, outcome = st.apply_report(state, "S-0005", "msg_1", result, NOW, True)
        self.assertEqual(outcome, "relay_failed")
        self.assertEqual(st.relay_badges(new)["D-0010"], {"status": "failed", "label": "relay failed", "blockers": ["inbox#12 was already deleted"]})

    def relayed_state(self, closed_at):
        state = fixture_state()
        add_relay(state, "D-0010", "start", status="sent", session_closed_at=closed_at)
        return state

    def test_start_relay_not_timed_out_before_next_query(self):
        current = self.relayed_state(NOW)
        later = "2026-11-30T09:00:00+09:00"
        new = commit(current, copy.deepcopy(current), now=later)
        self.assertEqual(find(new["relays"], "R-0004")["status"], "sent")
        self.assertEqual(st.relay_badges(new)["D-0010"]["label"], "relayed; checked at next refresh")

    def test_start_relay_unconfirmed_after_first_query_after_relay(self):
        state = self.relayed_state(NOW)
        report = query_report(decisions=[{"id": "inbox#12", "description": "打ち合わせを今週やるか", "since": "2026-10-16"}])
        merged = st.merge_source(state, "obsidian", report, {"created_at": at(NOW, 5)}, at(NOW, 10), True)
        self.assertEqual(find(merged["relays"], "R-0004")["status"], "unconfirmed")
        self.assertEqual(st.relay_badges(merged)["D-0010"]["label"], "relay not reflected")
        page = st.assemble(*st.render(merged, TEMPLATE, NOW))
        row = re.search(r'<li class="dec"[^>]*>(?:(?!</li>).)*D-0010.*?</li>', page, re.S).group(0)
        self.assertNotIn('class="paste copyable"', row)

    def test_start_relay_not_judged_by_query_started_while_relay_closing(self):
        report = query_report(decisions=[{"id": "inbox#12", "description": "打ち合わせを今週やるか", "since": "2026-10-16"}])
        for closed_at, created_at in ((None, at(NOW, 5)), (at(NOW, 10), at(NOW, 5))):
            state = self.relayed_state(closed_at)
            merged = st.merge_source(state, "obsidian", report, {"created_at": created_at}, at(NOW, 20), True)
            self.assertEqual(find(merged["relays"], "R-0004")["status"], "sent")

    def test_removing_relay_session_records_session_closed_at(self):
        current = fixture_state()
        relay = add_relay(current, "D-0010", "start", status="sent")
        add_session(current, purpose="relay", sid="S-0005", relay_id=relay["id"], status="closing", session_id="sess_r")
        cand = copy.deepcopy(current)
        cand["sessions"] = []
        new = commit(current, cand, now=at(NOW, 3))
        self.assertEqual(find(new["relays"], relay["id"])["session_closed_at"], at(NOW, 3))

    def test_paste_text_only_for_supervisor_relays(self):
        state = fixture_state()
        d = {x["id"]: x for x in state["decisions"]}
        self.assertEqual(st.paste_text(d["D-0011"], "push して PR を作ってよい"),
                         'T-0003 UA-0002 ("Decide whether to push branch claude/license-x1 of /repo/a ("): push して PR を作ってよい')
        self.assertIsNone(st.paste_text(d["D-0010"]))
        self.assertIsNone(st.paste_text(d["D-0012"]))
        page = st.assemble(*st.render(state, TEMPLATE, NOW))
        self.assertEqual(page.count('class="paste copyable"'), 1)


class SessionTest(unittest.TestCase):
    def test_archive_refused_keeps_session_closing(self):
        current = fixture_state()
        add_session(current, sid="S-0005", status="closing", session_id="sess_q", observed_state="idle")
        cand = copy.deepcopy(current)
        cand["sessions"][0]["archive_refused"] = True
        new = commit(current, cand)
        self.assertEqual(new["sessions"][0]["state"], "closing")
        page = st.assemble(*st.render(new, TEMPLATE, NOW))
        self.assertIn("Close sec-S-0005 from the screen or unpin it", page)

    def test_closing_kept_without_archive_consent(self):
        current = fixture_state()
        current["secretary"]["archive_consent"] = None
        add_session(current, sid="S-0005", status="closing", session_id="sess_q", observed_state="idle")
        cand = copy.deepcopy(current)
        find(cand["decisions"], "D-0011")["due"] = None
        new = commit(current, cand)
        self.assertEqual([s["state"] for s in new["sessions"]], ["closing"])
        self.assertIn("Please archive sec-S-0005", st.assemble(*st.render(new, TEMPLATE, NOW)))
        self.assertIn("S-0005 query obsidian closing", st.digest(new, "/s/state.json", NOW))

    def test_needs_input_session_is_surfaced(self):
        state = fixture_state()
        add_session(state, sid="S-0005", status="running", session_id="sess_q", observed_state="needs_input")
        self.assertIn("Answer the pending approval of sec-S-0005 in the app", st.assemble(*st.render(state, TEMPLATE, NOW)))

    def test_closing_not_counted_for_new_query(self):
        current = fixture_state()
        add_session(current, sid="S-0005", status="closing", session_id="sess_q")
        find(current["sources"], "obsidian")["last_fetched_at"] = None
        cand = copy.deepcopy(current)
        add_session(cand)
        self.assertEqual(len(commit(current, cand)["sessions"]), 2)

    def test_query_rejected_while_relay_session_running(self):
        current = fixture_state()
        relay = add_relay(current, "D-0010", "start", status="sent")
        add_session(current, purpose="relay", sid="S-0005", relay_id=relay["id"], status="running", session_id="sess_r")
        cand = copy.deepcopy(current)
        add_session(cand)
        with self.assertRaisesRegex(st.StateError, "already has a pending or running session"):
            commit(current, cand, now=DUE_AT)
        self.assertEqual(st.due_sources(defs(), current, DUE_AT), [])

    def test_resend_requested_once(self):
        state = fixture_state()
        add_session(state, sid="S-0005", status="running", session_id="sess_q")
        bad = st.parse_report("no block here", "obsidian")
        first, outcome = st.apply_report(state, "S-0005", "msg_1", bad, NOW, True)
        self.assertEqual(outcome, "resend_requested")
        self.assertEqual((first["sessions"][0]["state"], first["sessions"][0]["resend_requested"]), ("running", True))
        second, outcome = st.apply_report(first, "S-0005", "msg_2", bad, NOW, True)
        self.assertEqual(outcome, "parse_failed")
        self.assertEqual(second["sessions"][0]["state"], "closing")
        self.assertIn("no secretary-report block", find(second["sources"], "obsidian")["last_error"])
        self.assertEqual(find(second["sources"], "obsidian")["last_attempt_at"], NOW)

    def test_ingest_same_message_ref_is_noop(self):
        with Env() as env:
            state = env.current()
            add_session(state, sid="S-0005", status="running", session_id="sess_q", created_at=NOW)
            state["counters"]["session"] = 5
            env.state.write_text(st.serialize(state), encoding="utf-8")
            args = ("ingest-report", "--state", str(env.state), "--session", "S-0005", "--message-ref", "msg_9",
                    "--file", str(FIXTURES / "report_query_ok.txt"))
            code, out, err = env.run(*args)
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["outcome"], "merged")
            after = env.state.read_bytes()
            code, out, _ = env.run(*args)
            self.assertEqual((code, json.loads(out)), (0, {"rev": 8, "outcome": "noop"}))
            self.assertEqual(env.state.read_bytes(), after)


class ValidationTest(unittest.TestCase):
    def test_fixture_is_valid(self):
        self.assertEqual(st.validate(fixture_state()), [])
        self.assertEqual(st.supervisor_errors(fixture_sv()), [])

    def test_due_formats(self):
        current = fixture_state()
        for due in (None, "2026-10-19", "2026-10-19T18:00:00+09:00", "2026-10-19T09:00:00Z"):
            cand = copy.deepcopy(current)
            find(cand["decisions"], "D-0011")["due"] = due
            self.assertEqual(find(commit(current, cand)["decisions"], "D-0011")["due"], due)
        for due in ("2026-13-01", "tomorrow", "2026-10-19T18:00:00", "Friday"):
            cand = copy.deepcopy(current)
            find(cand["decisions"], "D-0011")["due"] = due
            with self.assertRaisesRegex(st.StateError, "D-0011"):
                commit(current, cand)

    def test_due_date_only_uses_local_tz(self):
        self.assertEqual(st.parse_due("2026-10-18", timezone(timedelta(hours=9))).isoformat(), "2026-10-18T23:59:59+09:00")
        self.assertFalse(st.is_overdue("2026-10-18", "2026-10-18T23:59:00+09:00"))
        self.assertTrue(st.is_overdue("2026-10-18", "2026-10-19T00:00:00+09:00"))
        self.assertFalse(st.is_overdue("2026-10-18", "2026-10-18T23:59:00-05:00"))
        self.assertTrue(st.is_overdue("2026-10-18T12:00:00+00:00", "2026-10-18T21:01:00+09:00"))
        self.assertFalse(st.is_overdue(None, NOW))

    def test_counters_monotonic(self):
        current = fixture_state()
        cand = copy.deepcopy(current)
        cand["counters"]["decision"] = 11
        with self.assertRaisesRegex(st.StateError, "must not decrease"):
            commit(current, cand)
        cand = copy.deepcopy(current)
        cand["decisions"].append({**find(cand["decisions"], "D-0012"), "id": "D-0005", "key": "supervisor/x"})
        with self.assertRaisesRegex(st.StateError, "D-0005 must be allocated"):
            commit(current, cand)
        cand["counters"]["decision"] = 13
        with self.assertRaisesRegex(st.StateError, "D-0005 must be allocated"):
            commit(current, cand)
        cand["decisions"][-1]["id"] = "D-0013"
        self.assertEqual(commit(current, cand)["counters"]["decision"], 13)

    def test_rev_mismatch_and_bad_shapes_rejected(self):
        current = fixture_state()
        cand = copy.deepcopy(current)
        cand["rev"] = 6
        with self.assertRaisesRegex(st.StateError, "does not match"):
            commit(current, cand)
        for mutate in (lambda s: s.update(decisions="x"), lambda s: s["decisions"].append(1),
                       lambda s: s["decisions"][0].update(relay_mode="send"), lambda s: s["today"][0].update(link="javascript:x"),
                       lambda s: s["secretary"]["dashboard"].update(url="ftp://x"), lambda s: s["counters"].update(relay=-1)):
            cand = copy.deepcopy(current)
            mutate(cand)
            with self.assertRaises(st.StateError):
                commit(current, cand)

    def test_non_string_source_directory_rejected(self):
        current = fixture_state()
        cand = copy.deepcopy(current)
        cand["sources"][0]["directory"] = 123
        with self.assertRaisesRegex(st.StateError, "source obsidian: directory must be a string or null"):
            st.next_state(current, cand, NOW, sv_state=free_sv())

    def test_relays_trimmed_to_newest_50(self):
        current = fixture_state()
        current["relays"] += [{**current["relays"][1], "id": f"R-{i:04d}", "at": f"2026-01-{(i % 28) + 1:02d}T00:00:00+09:00"} for i in range(100, 160)]
        current["counters"]["relay"] = 200
        new = commit(current, copy.deepcopy(current))
        self.assertEqual(len(new["relays"]), 50)
        self.assertEqual(new["relays"][0]["id"], "R-0003")
        find(current["relays"], "R-0003")["at"] = "2025-01-01T00:00:00+09:00"
        current["relays"] += [{**current["relays"][1], "id": f"R-{i:04d}", "at": "2026-10-01T00:00:00+09:00"} for i in range(160, 200)]
        new = commit(current, copy.deepcopy(current))
        self.assertEqual(len(new["relays"]), 50)
        self.assertIn("R-0003", [r["id"] for r in new["relays"]])
        self.assertEqual(st.relay_badges(new)["D-0011"]["label"], "paste pending")

    def test_worker_overlap(self):
        sv = fixture_sv()
        self.assertTrue(st.worker_overlap(sv, "/Users/handlename/notes"))
        self.assertTrue(st.worker_overlap(sv, "/Users/handlename/notes/sub"))
        self.assertTrue(st.worker_overlap(sv, "/Users/handlename"))
        self.assertFalse(st.worker_overlap(sv, "/Users/handlename/notes2"))
        self.assertFalse(st.worker_overlap(sv, "/repo/a"))
        self.assertTrue(st.worker_overlap(sv, "/Users/handlename/logs"))
        self.assertFalse(st.worker_overlap(free_sv(), "/Users/handlename/notes"))

    def test_new_entries_only_checked(self):
        current = fixture_state()
        self.assertEqual(find(current["relays"], "R-0002")["mode"], "start")
        add_session(current, sid="S-0004", source_id="gone", status="closing", session_id="sess_old")
        cand = copy.deepcopy(current)
        find(cand["decisions"], "D-0011")["due"] = "2026-10-22"
        self.assertEqual(commit(current, cand)["rev"], 8)


class ReportTest(unittest.TestCase):
    def test_last_block_wins_and_fields_checked(self):
        result = st.parse_report(fixture_text("report_query_ok.txt"), "obsidian")
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["report"]["today"]), 2)
        self.assertIn("today[].link", st.parse_report(fixture_text("report_invalid.txt"))["error"])
        self.assertIn("does not match", st.parse_report(fixture_text("report_query_ok.txt"), "mail")["error"])
        relay = st.parse_report(fixture_text("report_relay_done.txt"))
        self.assertTrue(relay["ok"])
        self.assertNotIn("today", relay["report"])
        for body, needle in (
            ('{"source_id": "o", "mode": "query", "status": "failed", "today": [], "decisions": [], "summary": "", "blockers": []}', "requires at least one blocker"),
            ('{"source_id": "o", "mode": "query", "status": "done", "today": [{"id": "", "title": "t"}], "decisions": [], "summary": "", "blockers": []}', "id must be"),
            ('{"source_id": "o", "mode": "query", "status": "done", "today": [], "decisions": [{"id": "a", "description": "d", "since": "soon"}], "summary": "", "blockers": []}', "since"),
            ('{"source_id": "o", "mode": "query", "status": "done", "today": [{"id": "a", "title": "t"}, {"id": "a", "title": "u"}], "decisions": [], "summary": "", "blockers": []}', "unique"),
            ('{"source_id": "o", "mode": "send", "status": "done", "summary": "", "blockers": []}', "invalid mode"),
            ('[1]', "JSON object"),
        ):
            result = st.parse_report(f"```secretary-report\n{body}\n```")
            self.assertFalse(result["ok"])
            self.assertIn(needle, result["error"])


class Env:
    """A secretary directory, a supervisor directory and a source directory under one temp dir."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.sec_dir, self.sv_dir, self.notes = root / "secretary", root / "supervisor", root / "notes"
        for d in (self.sec_dir, self.sv_dir, self.notes):
            d.mkdir()
        self.state = self.sec_dir / "state.json"
        self.claude_md = self.sec_dir / "CLAUDE.md"
        state = fixture_state()
        state["secretary"]["dir"], state["supervisor"]["dir"] = str(self.sec_dir), str(self.sv_dir)
        state["secretary"]["dashboard"]["path"] = str(self.sec_dir / "dashboard.html")
        find(state["sources"], "obsidian")["directory"] = str(self.notes)
        find(state["sources"], "mail")["directory"] = str(root / "mail")
        self.state.write_text(st.serialize(state), encoding="utf-8")
        text = fixture_text("claude_md_valid.md").replace("/Users/handlename/notes", str(self.notes))
        self.claude_md.write_text(text.replace("/Users/handlename/mail-agent", str(root / "mail")), encoding="utf-8")
        sv = free_sv()
        sv["supervisor"]["dir"] = str(self.sv_dir)
        (self.sv_dir / "state.json").write_text(json.dumps(sv), encoding="utf-8")
        return self

    def __exit__(self, *exc):
        self.tmp.cleanup()

    def run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = st.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def current(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def commit(self, candidate):
        cand = self.sec_dir / "state.next.json"
        cand.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
        return self.run("commit", "--state", str(self.state), "--candidate", str(cand))


class CommitCliTest(unittest.TestCase):
    def test_commit_writes_and_rev_only_is_not_dashboard_change(self):
        with Env() as env:
            code, out, err = env.commit(env.current())
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out), {"rev": 8, "dashboard_changed": True})
            self.assertEqual(sorted(p.name for p in env.sec_dir.iterdir()), ["CLAUDE.md", "dashboard.html", "state.json", "state.next.json"])
            _, out, _ = env.commit(env.current())
            self.assertEqual(json.loads(out)["rev"], 9)

    def test_missing_claude_md_is_recorded_as_sources_error(self):
        with Env() as env:
            env.claude_md.unlink()
            code, _, err = env.commit(env.current())
            self.assertEqual(code, 0, err)
            state = env.current()
            self.assertTrue(state["secretary"]["sources_error"][0].startswith("CLAUDE.md: not readable"), state["secretary"]["sources_error"])
            self.assertEqual([s["id"] for s in state["sources"]], ["obsidian", "mail"])
            self.assertIn("CLAUDE.md: not readable", st.digest(state, str(env.state), NOW))

    def test_missing_source_directory_is_shown(self):
        with Env() as env:
            code, _, err = env.commit(env.current())
            self.assertEqual(code, 0, err)
            page = (env.sec_dir / "dashboard.html").read_text(encoding="utf-8")
            self.assertEqual(page.count("directory not found"), 1)
            mail = page[page.index("<b>mail</b>"):]
            self.assertLess(mail.index("directory not found"), mail.index("</li>"))

    def test_commit_records_sources_error(self):
        with Env() as env:
            env.claude_md.write_text(fixture_text("claude_md_invalid_duplicate_heading.md"), encoding="utf-8")
            code, _, err = env.commit(env.current())
            self.assertEqual(code, 0, err)
            self.assertEqual(env.current()["secretary"]["sources_error"], ["CLAUDE.md:11: duplicate '## Sources' heading"])
            self.assertIn("duplicate", (env.sec_dir / "dashboard.html").read_text(encoding="utf-8"))


class RenderTest(unittest.TestCase):
    def page(self, state, now=NOW):
        return st.assemble(*st.render(state, TEMPLATE, now))

    def rows(self, page):
        return re.findall(r'<li class="dec"[^>]*>.*?aria-label="Copy (D-\d+)"', page)

    def test_render_deterministic(self):
        self.assertEqual(self.page(fixture_state()), self.page(fixture_state()))
        page = self.page(fixture_state())
        self.assertNotIn("{{", page)
        self.assertLess(page.index('id="summary"'), page.index('id="decisions"'))
        self.assertLess(page.index('id="decisions"'), page.index('id="today"'))
        self.assertLess(page.index('id="today"'), page.index('id="sources"'))

    def test_decisions_sorted_by_due(self):
        state = fixture_state()
        state["decisions"].append({**find(state["decisions"], "D-0012"), "id": "D-0004", "key": "supervisor/z", "since": "2026-10-16T00:00:00+09:00"})
        state["decisions"].append({**find(state["decisions"], "D-0011"), "id": "D-0003", "key": "supervisor/y", "due": "2026-10-19T09:00:00+09:00", "relay_id": None})
        self.assertEqual(self.rows(self.page(state)), ["D-0010", "D-0003", "D-0011", "D-0004", "D-0012"])

    def test_overdue_rows_marked(self):
        page = self.page(fixture_state())
        self.assertRegex(page, r'<li class="dec" data-mode="start" data-overdue data-due="2026-10-17T23:59:59\+09:00">')
        self.assertRegex(page, r'<li class="dec" data-mode="paste" data-due="2026-10-19T23:59:59\+09:00">')
        self.assertIn('data-overdue="1"', page)
        self.assertIn("no due", page)
        self.assertNotIn('data-overdue="', self.page(fixture_state(), "2026-10-16T09:00:00+09:00"))
        self.assertIn("[data-due]", TEMPLATE)

    def test_paste_text_is_copyable(self):
        state = fixture_state()
        page = self.page(state)
        text = 'T-0003 UA-0002 (&quot;Decide whether to push branch claude/license-x1 of /repo/a (&quot;): push して PR を作ってよい'
        self.assertIn(f'class="paste copyable" data-copy aria-label="Copy paste text for D-0011" title="クリックでコピー">{text}</button>', page)
        find(state["decisions"], "D-0011")["relay_id"] = None
        self.assertIn('/repo/a (&quot;): </button>', self.page(state))
        self.assertIn('aria-label="Copy D-0011"', page)
        self.assertIn("navigator.clipboard.writeText", page)
        self.assertIn("Act on this in the app", page)

    def test_rev_only_in_footer(self):
        a, b = fixture_state(), fixture_state()
        b["rev"], b["updated_at"] = 99, "2026-10-18T09:59:00+09:00"
        body_a, foot_a = st.render(a, TEMPLATE, NOW)
        body_b, foot_b = st.render(b, TEMPLATE, NOW)
        self.assertEqual(body_a, body_b)
        self.assertNotEqual(foot_a, foot_b)
        self.assertEqual(st.body_of(st.assemble(body_b, foot_b)), body_b)

    def test_escapes_html_and_braces(self):
        state = fixture_state()
        find(state["decisions"], "D-0011")["description"] = "<script>alert(1)</script>{{HERO}}"
        state["today"][0].update(title="<b>x</b>", link="javascript:alert(1)")
        page = self.page(state)
        self.assertEqual(page.count("<script>"), TEMPLATE.count("<script>"))
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn('href="javascript:', page)
        self.assertNotIn("{{HERO}}", page)
        self.assertIn("&#123;&#123;HERO&#125;&#125;", page)
        self.assertIn('href="https://mail.example.com/t/77"', page)

    def test_external_resources_are_google_fonts_only(self):
        page = self.page(fixture_state())
        self.assertIsNone(re.search(r"<script\b[^>]*\bsrc=", page))
        for href in re.findall(r'<link\b[^>]*\bhref="([^"]+)"', page):
            self.assertRegex(href, r"^https://fonts\.(googleapis|gstatic)\.com")
        self.assertIn("prefers-color-scheme: dark", page)
        self.assertIn(':root[data-theme="dark"]', page)

    def test_hero_surface_is_neutral(self):
        hero = " ".join(re.findall(r"\.hero \{([^}]*)\}", TEMPLATE))
        self.assertNotIn("background", hero)
        self.assertNotRegex(hero, r"red|amber|blue")

    def test_copyable_hover_changes_no_box_property(self):
        rules = re.findall(r"([^{}]*\.(?:copyable|paste)[^{}]*:hover[^{}]*)\{([^}]*)\}", TEMPLATE)
        self.assertTrue(rules)
        for selector, body in rules:
            self.assertNotRegex(body, r"\b(padding|margin|border(?!-radius)|font|letter-spacing|width)[a-z-]*\s*:", selector)


class DigestTest(unittest.TestCase):
    def test_digest_limit_and_broken_state_exit_zero(self):
        state = fixture_state()
        text = st.digest(state, "/Users/handlename/secretary/state.json", NOW)
        for needle in ("/Users/handlename/secretary/state.json", "/Users/handlename/secretary/dashboard.html", "sess_sec_01",
                       "archive_consent: recorded", "secretary skill", "D-0011", "obsidian fetched", "R-0003 paste awaiting_user"):
            self.assertIn(needle, text)
        find(state["decisions"], "D-0011")["description"] = "x" * 20000
        text = st.digest(state, "/s/state.json", NOW)
        self.assertLessEqual(len(text), 10000)
        self.assertIn("truncated; read /s/state.json", text)
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "state.json"
            broken.write_text("{not json", encoding="utf-8")
            weird = Path(tmp) / "weird.json"
            weird.write_text(json.dumps({"decisions": [{"due": 5}, "x"], "sources": [{}]}), encoding="utf-8")
            for path in (broken, Path(tmp) / "missing.json", weird):
                proc = subprocess.run([sys.executable, str(SCRIPT), "digest", "--state", str(path)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(proc.stdout.startswith("[secretary]"), proc.stdout)

    def test_digest_carries_requests_and_stale_text(self):
        state = fixture_state()
        state["secretary"]["archive_consent"] = None
        add_session(state, sid="S-0004", status="closing", session_id="sess_a")
        add_session(state, sid="S-0005", status="running", session_id="sess_b", observed_state="needs_input", source_id="mail")
        text = st.digest(state, "/s/state.json", DUE_AT)
        self.assertIn("- Please archive sec-S-0004", text)
        self.assertIn("- Answer the pending approval of sec-S-0005 in the app", text)
        self.assertIn("- obsidian fetched 2026-10-18T08:05:00+09:00 (stale: refreshed on your next message)", text)
        self.assertNotIn("mail fetched 2026-10-18T09:00:00+09:00 (stale", text)

    def test_digest_overdue_uses_now(self):
        state = fixture_state()
        overdue = st.digest(state, "/s", NOW).split("overdue decisions:\n")[1].split("\ndecisions by due")[0]
        self.assertEqual(overdue, "- D-0010 due 2026-10-17: 打ち合わせを今週やるか")
        earlier = st.digest(state, "/s", "2026-10-16T09:00:00+09:00").split("overdue decisions:\n")[1].split("\ndecisions by due")[0]
        self.assertEqual(earlier, "- (none)")


class FollowUpTest(unittest.TestCase):
    def page(self, state, now=NOW):
        return st.assemble(*st.render(state, TEMPLATE, now))

    def section(self, page, sid):
        m = re.search(rf'<section class="[^"]*" id="{sid}".*?</section>', page, re.S)
        return m.group(0) if m else ""

    def merged(self, sv=None):
        current = fixture_state()
        return current, st.merge_supervisor(current, sv or follow_up_sv(), NOW)

    def test_merge_carries_follow_up(self):
        current, merged = self.merged()
        d = by_ref(merged["decisions"], "T-0003", "UA-0004")
        self.assertEqual(d["follow_up"], {"rule": "review-pr", "url": PR_URL, "done": "pr_closed"})
        self.assertTrue(st.is_follow_up(d))
        self.assertEqual(d["relay_mode"], "paste")
        self.assertIsNone(by_ref(merged["decisions"], "T-0003", "UA-0002")["follow_up"])
        self.assertEqual(commit(current, merged)["rev"], 8)

    def test_follow_up_is_paste_whatever_its_wording(self):
        current, merged = self.merged(follow_up_sv("Review org/repo-a#42 in the app, then archive it"))
        d = by_ref(merged["decisions"], "T-0003", "UA-0004")
        self.assertEqual(st.classify_supervisor_ua(d["description"]), "app")
        self.assertEqual(d["relay_mode"], "paste")
        new = commit(current, merged)
        cand = copy.deepcopy(new)
        find(cand["decisions"], d["id"])["relay_mode"] = "app"
        with self.assertRaisesRegex(st.StateError, "relay_mode must be 'paste'"):
            commit(new, cand)

    def test_missing_key_is_null(self):
        state = fixture_state()
        self.assertNotIn("follow_up", find(state["decisions"], "D-0011"))
        self.assertFalse(st.is_follow_up(find(state["decisions"], "D-0011")))
        self.assertEqual(st.validate(state), [])
        find(state["decisions"], "D-0011")["follow_up"] = None
        self.assertEqual(st.validate(state), [])
        for bad in ("x", {"rule": "review-pr", "url": PR_URL}, {"rule": "review-pr", "url": PR_URL, "done": 1}):
            find(state["decisions"], "D-0011")["follow_up"] = bad
            self.assertTrue(any("follow_up" in e for e in st.validate(state)), bad)

    def test_merge_source_sets_null(self):
        merged = st.merge_source(fixture_state(), "obsidian", query_report(decisions=[{"id": "inbox#12", "description": "x", "since": ""}]),
                                 {"created_at": NOW}, NOW, True)
        d = find(merged["decisions"], "D-0010")
        self.assertIn("follow_up", d)
        self.assertIsNone(d["follow_up"])

    def test_supervisor_errors_check_follow_up_shape(self):
        sv = follow_up_sv()
        self.assertEqual(st.supervisor_errors(sv), [])
        find(sv["tasks"], "T-0003")["user_actions"][1]["follow_up"] = None
        self.assertEqual(st.supervisor_errors(sv), [])
        for bad in ("x", [], {"rule": "review-pr", "url": PR_URL}, {"rule": "review-pr", "url": 42, "done": "manual"}):
            find(sv["tasks"], "T-0003")["user_actions"][1]["follow_up"] = bad
            errors = st.supervisor_errors(sv)
            self.assertEqual(errors, ["supervisor task T-0003 UA-0004: follow_up must be {rule, url, done} strings or null"], bad)
            state = fixture_state()
            self.assertEqual(st.merge_supervisor(state, sv, NOW)["decisions"], state["decisions"])

    def test_completion_relay_is_paste(self):
        current, merged = self.merged()
        new = commit(current, merged)
        d = by_ref(new["decisions"], "T-0003", "UA-0004")
        cand = copy.deepcopy(new)
        add_relay(cand, d["id"], "paste", words="done")
        relayed = commit(new, cand)
        self.assertEqual(st.relay_badges(relayed)[d["id"]]["label"], "paste pending")

    def test_your_tasks_section(self):
        _, merged = self.merged()
        d = by_ref(merged["decisions"], "T-0003", "UA-0004")
        page = self.page(merged)
        tasks, decisions = self.section(page, "your-tasks"), self.section(page, "decisions")
        self.assertLess(page.index('id="summary"'), page.index('id="your-tasks"'))
        self.assertLess(page.index('id="your-tasks"'), page.index('id="decisions"'))
        self.assertIn(f'<a href="{PR_URL}">{PR_URL}</a>', tasks)
        self.assertIn(f'aria-label="Copy paste text for {d["id"]}" title="クリックでコピー">T-0003 UA-0004 (&quot;Review org/repo-a#42&quot;): done</button>', tasks)
        self.assertIn('<span class="nodue">no due</span>', tasks)
        self.assertIn("Tell me a due date to set one", tasks)
        self.assertIn('Your tasks <span class="count">1</span>', tasks)
        self.assertNotIn(d["id"], decisions)
        self.assertNotIn("Review org/repo-a#42", decisions)
        self.assertIn('<main>', page)
        self.assertNotIn("data-no-tasks", page.split("<main")[1].split(">")[0])
        self.assertRegex(page, r'<p class="hero__num">1</p><p class="hero__cap">your tasks</p>')
        self.assertIn("4 decisions and 1 task waiting on you", page)

    def test_your_tasks_due_and_order(self):
        sv = follow_up_sv("Review org/repo-b#7", task_id="T-0005", ua_id="UA-0005", url="https://github.com/org/repo-b/pull/7")
        find(sv["tasks"], "T-0003")["user_actions"].append(
            {"id": "UA-0004", "description": "Review org/repo-a#42", "since": "2026-10-18T09:25:00+09:00",
             "follow_up": {"rule": "review-pr", "url": PR_URL, "done": "manual"}})
        _, merged = self.merged(sv)
        a = by_ref(merged["decisions"], "T-0003", "UA-0004")
        b = by_ref(merged["decisions"], "T-0005", "UA-0005")
        b["due"] = "2026-10-18"
        page = self.page(merged)
        tasks = self.section(page, "your-tasks")
        self.assertLess(tasks.index(f'Copy {b["id"]}"'), tasks.index(f'Copy {a["id"]}"'))
        self.assertIn('<li class="task" data-due="2026-10-18T23:59:59+09:00">', tasks)
        self.assertIn("due <b>2026-10-18</b>", tasks)
        a["due"] = "2026-10-17"
        page = self.page(merged)
        tasks = self.section(page, "your-tasks")
        self.assertLess(tasks.index(f'Copy {a["id"]}"'), tasks.index(f'Copy {b["id"]}"'))
        self.assertIn('<li class="task" data-overdue data-due="2026-10-17T23:59:59+09:00">', tasks)
        self.assertIn('data-overdue="2"', page)

    def test_your_tasks_relay_badge_and_words(self):
        current, merged = self.merged()
        new = commit(current, merged)
        d = by_ref(new["decisions"], "T-0003", "UA-0004")
        cand = copy.deepcopy(new)
        add_relay(cand, d["id"], "paste", words="レビューした")
        tasks = self.section(self.page(commit(new, cand)), "your-tasks")
        self.assertIn('<span class="badge" data-relay="awaiting_user">paste pending</span>', tasks)
        self.assertIn("T-0003 UA-0004 (&quot;Review org/repo-a#42&quot;): レビューした</button>", tasks)
        self.assertIn("Paste into the supervisor chat", tasks)

    def test_no_tasks_hides_section(self):
        page = self.page(fixture_state())
        self.assertIn("<main data-no-tasks>", page)
        self.assertNotIn('id="your-tasks"', page)
        self.assertNotIn("{{", page)
        self.assertRegex(page, r'<p class="hero__num">0</p><p class="hero__cap">your tasks</p>')

    def test_template_grid_has_tasks_area(self):
        self.assertIn(".your-tasks { grid-area: tasks; }", TEMPLATE)
        self.assertIn('grid-template-areas: "hero hero" "tasks side" "dec side"', TEMPLATE)
        self.assertIn('main[data-no-tasks] { grid-template-areas: "hero hero" "dec side"', TEMPLATE)
        self.assertIn('grid-template-areas: "hero" "tasks" "dec" "side"', TEMPLATE)
        self.assertIn('main[data-no-tasks] { grid-template-areas: "hero" "dec" "side"', TEMPLATE)
        self.assertLess(TEMPLATE.index("{{YOUR_TASKS}}"), TEMPLATE.index("{{DECISIONS}}"))

    def test_digest_lists_your_tasks_separately(self):
        _, merged = self.merged()
        d = by_ref(merged["decisions"], "T-0003", "UA-0004")
        text = st.digest(merged, "/s", NOW)
        decisions = text.split("decisions by due (top 20):\n")[1].split("\nyour tasks by due")[0]
        tasks = text.split("your tasks by due (top 20):\n")[1].split("\nunconfirmed relays:")[0]
        self.assertNotIn(d["id"], decisions)
        self.assertEqual(tasks, f"- {d['id']} due -: Review org/repo-a#42 <{PR_URL}>")
        d["due"] = "2026-10-17"
        overdue = st.digest(merged, "/s", NOW).split("overdue decisions:\n")[1].split("\ndecisions by due")[0]
        self.assertIn(f"- {d['id']} [your task] due 2026-10-17: Review org/repo-a#42", overdue)
        self.assertIn("your tasks by due (top 20):\n- (none)", st.digest(fixture_state(), "/s", NOW))


class CliTest(unittest.TestCase):
    def test_cli_exit_codes(self):
        with Env() as env:
            (Path(env.tmp.name).resolve() / "mail").mkdir()
            code, out, err = env.run("validate-sources", "--claude-md", str(env.claude_md))
            self.assertEqual((code, out.strip()), (0, "ok: 2 sources"), err)
            (Path(env.tmp.name).resolve() / "mail").rmdir()
            code, _, err = env.run("validate-sources", "--claude-md", str(env.claude_md))
            self.assertEqual(code, 1)
            self.assertRegex(err, r"^error: CLAUDE\.md:\d+: directory .*mail does not exist")
            env.claude_md.write_text(fixture_text("claude_md_invalid_entries.md"), encoding="utf-8")
            code, _, err = env.run("validate-sources", "--claude-md", str(env.claude_md))
            self.assertEqual(code, 1)
            self.assertIn("error: CLAUDE.md:5:", err)

        with Env() as env:
            state = env.current()
            add_session(state, sid="S-0005", status="running", session_id="sess_q")
            env.state.write_text(st.serialize(state), encoding="utf-8")
            base = ("ingest-report", "--state", str(env.state), "--session", "S-0005")
            code, out, err = env.run(*base, "--message-ref", "m1", "--file", str(FIXTURES / "report_invalid.txt"))
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(out)["outcome"], "resend_requested")
            self.assertIn("error:", err)
            code, out, err = env.run(*base, "--message-ref", "m2", "--file", str(FIXTURES / "report_query_ok.txt"))
            self.assertEqual((code, json.loads(out)["outcome"]), (0, "merged"), err)
            code, _, err = env.run(*base, "--message-ref", "m3", "--file", str(FIXTURES / "report_query_ok.txt"))
            self.assertEqual(code, 1)
            self.assertIn("not running", err)

            code, out, _ = env.run("parse-report", "--file", str(FIXTURES / "report_relay_done.txt"), "--expect-source", "obsidian")
            self.assertEqual(code, 0)
            code, _, _ = env.run("parse-report", "--file", str(FIXTURES / "report_invalid.txt"))
            self.assertEqual(code, 1)

            code, out, _ = env.run("check-overlap", "--state", str(env.state), "--directory", str(env.notes))
            self.assertEqual((code, out.strip()), (0, "free"))
            sv = fixture_sv()
            find(sv["workers"], "w-0007")["cwd"] = str(env.notes)
            (env.sv_dir / "state.json").write_text(json.dumps(sv), encoding="utf-8")
            code, out, _ = env.run("check-overlap", "--state", str(env.state), "--directory", str(env.notes / "sub"))
            self.assertEqual((code, out.strip()), (0, "busy"))

            state = env.current()
            find(state["sources"], "obsidian")["last_fetched_at"] = None
            state["sessions"] = []
            env.state.write_text(st.serialize(state), encoding="utf-8")
            code, out, err = env.run("due-sources", "--state", str(env.state))
            self.assertEqual((code, json.loads(out)), (0, ["obsidian"]), err)
            self.assertNotIn("mail", out)

            proc = subprocess.run([sys.executable, str(SCRIPT), "send", "--state", str(env.state)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def setup_dir(self, target, sv=None):
        return subprocess.run([sys.executable, str(SCRIPT), "setup", "--dir", str(target), "--supervisor-dir", str(sv or self.root / "supervisor")],
                              capture_output=True, text=True)

    def files(self, target):
        return sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())

    def hooks(self, target):
        return json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"]

    def test_setup_creates_files_and_hook(self):
        target = self.root / "my secretary"
        proc = self.setup_dir(target)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no supervisor state", proc.stderr)
        self.assertEqual(self.files(target), [".claude/settings.json", ".secretary/dashboard.template.html",
                                              ".secretary/state_tool.py", "CLAUDE.md", "state.json"])
        entries = self.hooks(target)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["matcher"], "startup|resume|clear|compact")
        command = entries[0]["hooks"][0]["command"]
        self.assertIn(f"'{target / '.secretary' / 'state_tool.py'}'", command)
        run = subprocess.run(command, shell=True, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0)
        self.assertIn("[secretary] state rev 0", run.stdout)
        state = json.loads((target / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(st.validate(state), [])
        self.assertEqual((state["secretary"]["dir"], state["supervisor"]["dir"]), (str(target), str(self.root / "supervisor")))
        claude_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(st.parse_sources(claude_md), ([], []))
        self.assertIn("## Sources", claude_md)
        bundled = subprocess.run([sys.executable, str(SCRIPT), "version"], capture_output=True, text=True).stdout
        copied = subprocess.run([sys.executable, str(target / ".secretary" / "state_tool.py"), "version"], capture_output=True, text=True).stdout
        self.assertRegex(bundled.strip(), r"^[0-9a-f]{12}$")
        self.assertEqual(bundled, copied)

    def test_setup_idempotent(self):
        target = self.root / "sec"
        self.setup_dir(target)
        (target / "CLAUDE.md").write_text("## Sources\n", encoding="utf-8")
        proc = self.setup_dir(target)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(self.hooks(target)), 1)
        self.assertTrue(all(line.startswith("exists:") for line in proc.stdout.splitlines()), proc.stdout)
        self.assertEqual((target / "CLAUDE.md").read_text(encoding="utf-8"), "## Sources\n")

    def test_setup_aborts_inside_git_or_supervisor_dir(self):
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True)
        proc = self.setup_dir(repo / "sec")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("git repository", proc.stderr)
        self.assertFalse((repo / "sec").exists())
        sv = self.root / "supervisor"
        for target in (sv, sv / "sec", self.root):
            proc = self.setup_dir(target, sv)
            self.assertEqual(proc.returncode, 1, target)
            self.assertIn("overlaps the supervisor directory", proc.stderr)
            self.assertFalse((target / "state.json").exists())


class StdlibOnlyTest(unittest.TestCase):
    def test_imports_are_stdlib(self):
        source = SCRIPT.read_text(encoding="utf-8")
        modules = set(re.findall(r"^(?:import|from) ([a-zA-Z_]+)", source, re.M))
        self.assertTrue(modules <= set(sys.stdlib_module_names), modules - set(sys.stdlib_module_names))


if __name__ == "__main__":
    unittest.main()
