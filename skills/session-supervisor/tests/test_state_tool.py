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
from unittest import mock
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SKILL = TESTS.parent
SCRIPT = SKILL / "scripts" / "state_tool.py"
TEMPLATE = (SKILL / "scripts" / "dashboard.template.html").read_text(encoding="utf-8")
FIXTURES = TESTS / "fixtures"
sys.path.insert(0, str(SKILL / "scripts"))

import state_tool as st  # noqa: E402

NOW = "2026-10-17T11:00:00+09:00"


def fixture_state():
    return json.loads((FIXTURES / "state_valid.json").read_text(encoding="utf-8"))


def find(items, item_id):
    return next(i for i in items if i["id"] == item_id)


def nongit_task(state, task_id, worker_id, cwd, intended, observed, session_id="sess_x"):
    state["tasks"].append({
        "id": task_id, "title": f"task {task_id}", "request": "do it", "push_consent": None,
        "status": "running", "next_action": "", "user_actions": [], "artifacts": [],
        "worker_ids": [worker_id], "created_at": NOW,
    })
    state["workers"].append({
        "id": worker_id, "task_id": task_id, "session_id": session_id, "started_by": "sess_sup_01",
        "handle": worker_id, "title": worker_id, "cwd": cwd, "kind": "non-git", "delivery": "files",
        "branch": None, "worktree_path": None, "intended_state": intended, "observed_state": observed,
        "observed_at": NOW, "last_report": None,
    })


def git_task(state, task_id, worker_id, delivery="commit", intended="running", observed="idle"):
    state["tasks"].append({
        "id": task_id, "title": f"task {task_id}", "request": "fix the LICENSE year", "push_consent": None,
        "status": "running", "next_action": "", "user_actions": [], "artifacts": [],
        "worker_ids": [worker_id], "created_at": NOW,
    })
    state["workers"].append({
        "id": worker_id, "task_id": task_id, "session_id": "sess_" + worker_id, "started_by": "sess_sup_01",
        "handle": worker_id, "title": worker_id, "cwd": "/repo/a", "kind": "git", "delivery": delivery,
        "branch": "claude/license-x1", "base_branch": "main", "worktree_path": "/repo/a/.claude/worktrees/license-x1",
        "intended_state": intended, "observed_state": observed, "observed_at": NOW, "last_report": None,
    })


def commit_artifacts(state, task_id):
    find(state["tasks"], task_id)["artifacts"] = [
        {"kind": "branch", "label": "a", "ref": "/repo/a#claude/license-x1"},
        {"kind": "commit", "label": "a", "ref": "abc123"},
    ]


def push_decision(state, task_id, ua_id="UA-0100"):
    find(state["tasks"], task_id)["user_actions"].append(
        {"id": ua_id, "description": "Decide whether to push branch claude/license-x1 of /repo/a (and open a PR)", "since": NOW}
    )


class NextStateTest(unittest.TestCase):
    def setUp(self):
        self.current = fixture_state()

    def candidate(self):
        return copy.deepcopy(self.current)

    def assertRejected(self, candidate, pattern):
        with self.assertRaises(st.StateError) as ctx:
            st.next_state(self.current, candidate, NOW)
        self.assertRegex(str(ctx.exception), pattern)

    def test_fixture_is_valid(self):
        self.assertEqual(st.validate(self.current), [])

    def test_rev_increments_and_updated_at(self):
        new = st.next_state(self.current, self.candidate(), NOW)
        self.assertEqual(new["rev"], 13)
        self.assertEqual(new["updated_at"], NOW)
        self.assertEqual(self.current["rev"], 12)

    def test_stale_rev_rejected(self):
        cand = self.candidate()
        cand["rev"] = 11
        self.assertRejected(cand, "rev")

    def test_two_running_nongit_in_same_cwd_rejected(self):
        cand = self.candidate()
        nongit_task(cand, "T-0010", "w-0010", "/Users/handlename/notes", "running", "running")
        w7 = find(cand["workers"], "w-0007")
        w7["intended_state"], w7["observed_state"], w7["session_id"] = "running", "running", "sess_7"
        self.assertRejected(cand, "held by more than one worker")

    def test_cwd_normalized_for_busy_check(self):
        cand = self.candidate()
        nongit_task(cand, "T-0010", "w-0010", "/Users/handlename/notes/", "running", "running")
        w7 = find(cand["workers"], "w-0007")
        w7["cwd"] = "/Users/handlename/./notes"
        w7["intended_state"], w7["observed_state"], w7["session_id"] = "running", "running", "sess_7"
        self.assertRejected(cand, "held by more than one worker")

    def test_silent_task_removal_rejected(self):
        cand = self.candidate()
        cand["tasks"] = [t for t in cand["tasks"] if t["id"] != "T-0004"]
        cand["workers"] = [w for w in cand["workers"] if w["task_id"] != "T-0004"]
        self.assertRejected(cand, "T-0004 was removed")

    def test_worker_with_session_id_removal_rejected(self):
        cand = self.candidate()
        cand["workers"] = [w for w in cand["workers"] if w["id"] != "w-0005"]
        find(cand["tasks"], "T-0003")["worker_ids"] = ["w-0004"]
        self.assertRejected(cand, "w-0005 has a session_id")

    def test_pending_worker_without_session_id_can_be_removed(self):
        cand = self.candidate()
        nongit_task(cand, "T-0010", "w-0010", "/tmp/x", "pending", "none", session_id=None)
        cand["workers"].append(dict(find(cand["workers"], "w-0010"), id="w-0011"))
        find(cand["tasks"], "T-0010")["worker_ids"].append("w-0011")
        cur = st.next_state(self.current, cand, NOW)
        cand2 = copy.deepcopy(cur)
        cand2["workers"] = [w for w in cand2["workers"] if w["id"] != "w-0011"]
        find(cand2["tasks"], "T-0010")["worker_ids"] = ["w-0010"]
        st.next_state(cur, cand2, NOW)

    def test_push_pr_without_consent_rejected(self):
        cand = self.candidate()
        find(cand["tasks"], "T-0003")["push_consent"] = None
        self.assertRejected(cand, "push_consent")

    def test_push_pr_consent_not_substring_rejected(self):
        cand = self.candidate()
        find(cand["tasks"], "T-0003")["push_consent"]["text"] = "push してよい"
        self.assertRejected(cand, "push_consent")

    def test_archive_without_push_pr_artifacts_rejected(self):
        cand = self.candidate()
        task = find(cand["tasks"], "T-0003")
        task["artifacts"] = [a for a in task["artifacts"] if a["kind"] not in ("pr",)]
        find(cand["workers"], "w-0004")["intended_state"] = "archived"
        self.assertRejected(cand, "lacks push_pr artifacts")

    def test_archive_push_pr_with_pushed_branch_url_accepted(self):
        cand = self.candidate()
        task = find(cand["tasks"], "T-0003")
        task["artifacts"] = [a for a in task["artifacts"] if a["kind"] in ("branch", "commit")]
        task["user_actions"] = [ua for ua in task["user_actions"] if not ua.get("follow_up")]
        task["artifacts"][0]["url"] = "https://github.com/org/repo-a/tree/hn/uploader-retry-x1"
        find(cand["workers"], "w-0004")["intended_state"] = "archived"
        st.next_state(self.current, cand, NOW)

    def test_archive_commit_without_artifacts_rejected(self):
        git_task(self.current, "T-0020", "w-0020")
        cand = self.candidate()
        push_decision(cand, "T-0020")
        find(cand["workers"], "w-0020")["intended_state"] = "archived"
        self.assertRejected(cand, "lacks commit artifacts")

    def test_archive_files_without_file_rejected(self):
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "idle")
        cand = self.candidate()
        find(cand["workers"], "w-0030")["intended_state"] = "archived"
        self.assertRejected(cand, "lacks files artifacts")

    def test_archive_without_archive_consent_rejected(self):
        cand = self.candidate()
        cand["supervisor"]["archive_consent"] = None
        find(cand["workers"], "w-0004")["intended_state"] = "archived"
        self.assertRejected(cand, "archive_consent")

    def test_already_archived_worker_skips_consent_check(self):
        self.current["supervisor"]["archive_consent"] = None
        find(self.current["workers"], "w-0004")["intended_state"] = "archived"
        cand = self.candidate()
        find(cand["tasks"], "T-0003")["next_action"] = "changed"
        st.next_state(self.current, cand, NOW)

    def test_commit_delivery_archive_requires_push_decision(self):
        git_task(self.current, "T-0020", "w-0020")
        cand = self.candidate()
        commit_artifacts(cand, "T-0020")
        find(cand["workers"], "w-0020")["intended_state"] = "archived"
        self.assertRejected(cand, "push-decision")
        push_decision(cand, "T-0020")
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "needs_user")

    def test_commit_delivery_stays_needs_user_after_archive(self):
        git_task(self.current, "T-0020", "w-0020", intended="archived", observed="idle")
        commit_artifacts(self.current, "T-0020")
        push_decision(self.current, "T-0020")
        cand = self.candidate()
        find(cand["workers"], "w-0020")["observed_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "needs_user")
        self.assertIn("w-0020", [w["id"] for w in new["workers"]])

    def test_removing_push_decision_after_archive_moves_task(self):
        git_task(self.current, "T-0020", "w-0020", intended="archived", observed="archived")
        commit_artifacts(self.current, "T-0020")
        push_decision(self.current, "T-0020")
        cand = self.candidate()
        find(cand["tasks"], "T-0020")["user_actions"] = []
        new = st.next_state(self.current, cand, NOW)
        self.assertNotIn("T-0020", [t["id"] for t in new["tasks"]])
        self.assertNotIn("w-0020", [w["id"] for w in new["workers"]])
        self.assertEqual(new["archived"][0]["id"], "T-0020")
        self.assertEqual(new["archived"][0]["archived_at"], NOW)

    def test_removing_push_decision_while_archive_refused_accepted(self):
        git_task(self.current, "T-0020", "w-0020", intended="archived", observed="idle")
        commit_artifacts(self.current, "T-0020")
        push_decision(self.current, "T-0020")
        cand = self.candidate()
        find(cand["tasks"], "T-0020")["user_actions"] = []
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "running")

    def test_file_artifact_with_url_rejected(self):
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "idle")
        cand = self.candidate()
        find(cand["tasks"], "T-0030")["artifacts"] = [{"kind": "file", "path": "/data/x/a.txt", "url": "https://x"}]
        self.assertRejected(cand, "must not have url")

    def test_file_artifact_outside_cwd_rejected(self):
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "idle")
        cand = self.candidate()
        find(cand["tasks"], "T-0030")["artifacts"] = [{"kind": "file", "path": "/data/xy/a.txt"}]
        self.assertRejected(cand, "outside the worker cwd")

    def test_non_http_url_rejected(self):
        cand = self.candidate()
        find(cand["tasks"], "T-0003")["artifacts"][2]["url"] = "javascript:alert(1)"
        self.assertRejected(cand, "http")

    def test_status_derivation(self):
        cand = self.candidate()
        nongit_task(cand, "T-0040", "w-0040", "/d/run", "running", "running")
        cand["workers"].append(dict(find(cand["workers"], "w-0040"), id="w-0041", cwd="/d/pend", intended_state="pending", observed_state="none", session_id=None))
        find(cand["tasks"], "T-0040")["worker_ids"].append("w-0041")
        nongit_task(cand, "T-0041", "w-0042", "/d/wait", "pending", "none", session_id=None)
        nongit_task(cand, "T-0042", "w-0043", "/d/done", "archived", "archived")
        find(cand["tasks"], "T-0042")["artifacts"] = [{"kind": "file", "path": "/d/done/out.txt"}]
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0003")["status"], "needs_user")
        self.assertEqual(find(new["tasks"], "T-0040")["status"], "running")
        self.assertEqual(find(new["tasks"], "T-0041")["status"], "waiting")
        self.assertNotIn("T-0042", [t["id"] for t in new["tasks"]])
        self.assertEqual(new["archived"][0]["id"], "T-0042")

    def test_intended_archived_observed_idle_does_not_move(self):
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "idle")
        cand = self.candidate()
        find(cand["tasks"], "T-0030")["artifacts"] = [{"kind": "file", "path": "/data/x/a.txt"}]
        find(cand["workers"], "w-0030")["intended_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0030")["status"], "running")

    def test_archived_truncated_to_20(self):
        self.current["archived"] = [{"id": f"T-9{i:03d}", "title": "x", "artifacts": [], "archived_at": NOW} for i in range(20)]
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "archived", "idle")
        find(self.current["tasks"], "T-0030")["artifacts"] = [{"kind": "file", "path": "/data/x/a.txt"}]
        cand = self.candidate()
        find(cand["workers"], "w-0030")["observed_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(len(new["archived"]), 20)
        self.assertEqual(new["archived"][0]["id"], "T-0030")
        self.assertEqual(new["archived"][-1]["id"], "T-9018")

    def test_last_report_shapes(self):
        cand = self.candidate()
        st.next_state(self.current, cand, NOW)
        del find(cand["workers"], "w-0004")["last_report"]["message_ref"]
        self.assertRejected(cand, "message_ref")
        cand = self.candidate()
        del find(cand["workers"], "w-0005")["last_report"]["error"]
        self.assertRejected(cand, "requires error")

    def test_waiting_nongit_task_gets_start_action_when_cwd_frees(self):
        nongit_task(self.current, "T-0005", "w-0006", "/Users/handlename/notes", "archived", "idle")
        find(self.current["tasks"], "T-0005")["artifacts"] = [{"kind": "file", "path": "/Users/handlename/notes/a.txt"}]
        cand = self.candidate()
        find(cand["workers"], "w-0006")["observed_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        task = find(new["tasks"], "T-0004")
        self.assertEqual(task["status"], "needs_user")
        self.assertEqual(len(task["user_actions"]), 1)
        ua = task["user_actions"][0]
        self.assertEqual(ua["id"], "UA-0004")
        self.assertIn("send any message", ua["description"])
        again = st.next_state(new, copy.deepcopy(new), NOW)
        self.assertEqual(len(find(again["tasks"], "T-0004")["user_actions"]), 1)

    def test_waiting_start_action_cleared_on_start_and_task_archives(self):
        # Start the waiting worker after the auto action, then finish it.
        nongit_task(self.current, "T-0005", "w-0006", "/Users/handlename/notes", "archived", "idle")
        find(self.current["tasks"], "T-0005")["artifacts"] = [{"kind": "file", "path": "/Users/handlename/notes/a.txt"}]
        cand = self.candidate()
        find(cand["workers"], "w-0006")["observed_state"] = "archived"
        state = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(state["tasks"], "T-0004")["status"], "needs_user")

        cand = copy.deepcopy(state)
        w7 = find(cand["workers"], "w-0007")
        w7.update(session_id="sess_7", intended_state="running", observed_state="running")
        state = st.next_state(state, cand, NOW)
        task = find(state["tasks"], "T-0004")
        self.assertEqual(task["user_actions"], [])
        self.assertEqual(task["status"], "running")

        cand = copy.deepcopy(state)
        find(cand["tasks"], "T-0004")["artifacts"] = [{"kind": "file", "path": "/Users/handlename/notes/b.txt"}]
        find(cand["workers"], "w-0007")["intended_state"] = "archived"
        state = st.next_state(state, cand, NOW)
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0007")["observed_state"] = "archived"
        state = st.next_state(state, cand, NOW)
        self.assertNotIn("T-0004", [t["id"] for t in state["tasks"]])
        self.assertEqual(state["archived"][0]["id"], "T-0004")

    def test_waiting_start_action_on_every_waiting_task_then_cleared_for_the_rest(self):
        nongit_task(self.current, "T-0005", "w-0006", "/Users/handlename/notes", "archived", "idle")
        find(self.current["tasks"], "T-0005")["artifacts"] = [{"kind": "file", "path": "/Users/handlename/notes/a.txt"}]
        nongit_task(self.current, "T-0006", "w-0008", "/Users/handlename/notes", "pending", "none", session_id=None)
        cand = self.candidate()
        find(cand["workers"], "w-0006")["observed_state"] = "archived"
        state = st.next_state(self.current, cand, NOW)
        self.assertEqual(len(find(state["tasks"], "T-0004")["user_actions"]), 1)
        self.assertEqual(len(find(state["tasks"], "T-0006")["user_actions"]), 1)
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0007").update(session_id="sess_7", intended_state="running", observed_state="running")
        state = st.next_state(state, cand, NOW)
        self.assertEqual(find(state["tasks"], "T-0004")["user_actions"], [])
        self.assertEqual(find(state["tasks"], "T-0006")["user_actions"], [])
        self.assertEqual(find(state["tasks"], "T-0006")["status"], "waiting")

    def test_malformed_shapes_rejected_without_exceptions(self):
        mutations = [
            lambda c: find(c["tasks"], "T-0003")["artifacts"].append("oops"),
            lambda c: find(c["tasks"], "T-0003")["user_actions"].append(["x"]),
            lambda c: find(c["tasks"], "T-0003")["user_actions"][0].update(description=["x"]),
            lambda c: find(c["workers"], "w-0004").update(kind=["git"]),
            lambda c: find(c["workers"], "w-0004").update(cwd=None),
            lambda c: c["workers"].append(7),
            lambda c: c["tasks"][0].update(id=["T"]),
            lambda c: c["tasks"][0].update(worker_ids="w-0004"),
            lambda c: c["supervisor"].update(archive_consent="yes"),
            lambda c: c["archived"][0].update(artifacts={"kind": "pr"}),
        ]
        for mutate in mutations:
            cand = self.candidate()
            mutate(cand)
            with self.assertRaises(st.StateError):
                st.next_state(self.current, cand, NOW)

    def test_no_start_action_while_cwd_still_busy(self):
        nongit_task(self.current, "T-0005", "w-0006", "/Users/handlename/notes", "running", "idle")
        cand = self.candidate()
        find(cand["tasks"], "T-0005")["artifacts"] = [{"kind": "file", "path": "/Users/handlename/notes/a.txt"}]
        find(cand["workers"], "w-0006")["intended_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0004")["user_actions"], [])
        self.assertTrue(st.cwd_busy(new, "/Users/handlename/notes"))

    def test_worktree_refused_git_task_needs_user_then_waits(self):
        git_task(self.current, "T-0050", "w-0050", intended="pending", observed="none")
        w = find(self.current["workers"], "w-0050")
        w["session_id"] = None
        cand = self.candidate()
        find(cand["tasks"], "T-0050")["user_actions"].append(
            {"id": "UA-0050", "description": "Open the repo /repo/a once in the app, then tell the supervisor", "since": NOW}
        )
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0050")["status"], "needs_user")
        cand2 = copy.deepcopy(new)
        find(cand2["tasks"], "T-0050")["user_actions"] = []
        self.assertEqual(find(st.next_state(new, cand2, NOW)["tasks"], "T-0050")["status"], "waiting")

    def test_unknown_observed_state_rejected(self):
        cand = self.candidate()
        find(cand["workers"], "w-0004")["observed_state"] = "unknown"
        self.assertRejected(cand, "invalid observed_state")

    def test_continues_validated(self):
        cand = self.candidate()
        find(cand["workers"], "w-0004")["continues"] = 2
        st.next_state(self.current, cand, NOW)
        for bad in (-1, "1", 1.5, True, None):
            cand = self.candidate()
            find(cand["workers"], "w-0004")["continues"] = bad
            self.assertRejected(cand, "continues must be a non-negative integer")

    def test_nested_nongit_cwds_conflict(self):
        cand = self.candidate()
        nongit_task(cand, "T-0010", "w-0010", "/Users/handlename/notes/sub", "running", "running")
        w7 = find(cand["workers"], "w-0007")
        w7["intended_state"], w7["observed_state"], w7["session_id"] = "running", "running", "sess_7"
        self.assertRejected(cand, "nested.*held by more than one worker")

    def test_sibling_prefix_cwds_do_not_conflict(self):
        cand = self.candidate()
        nongit_task(cand, "T-0010", "w-0010", "/Users/handlename/notesx", "running", "running")
        w7 = find(cand["workers"], "w-0007")
        w7["intended_state"], w7["observed_state"], w7["session_id"] = "running", "running", "sess_7"
        st.next_state(self.current, cand, NOW)

    def test_external_archive_without_user_action_keeps_task_needs_user(self):
        # Reviewer scenario: a commit-delivery worker is archived by hand and reconciliation
        # records observed archived without a user action or artifacts.
        git_task(self.current, "T-0020", "w-0020", intended="running", observed="idle")
        cand = self.candidate()
        find(cand["workers"], "w-0020")["observed_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "needs_user")
        self.assertIn("w-0020", [w["id"] for w in new["workers"]])
        self.assertNotIn("T-0020", [s["id"] for s in new["archived"]])

        cand = copy.deepcopy(new)
        find(cand["workers"], "w-0020")["intended_state"] = "archived"
        commit_artifacts(cand, "T-0020")
        with self.assertRaises(st.StateError) as ctx:
            st.next_state(new, cand, NOW)
        self.assertIn("push-decision", str(ctx.exception))
        push_decision(cand, "T-0020")
        verified = st.next_state(new, cand, NOW)
        self.assertEqual(find(verified["tasks"], "T-0020")["status"], "needs_user")

        cand = copy.deepcopy(verified)
        find(cand["tasks"], "T-0020")["user_actions"] = []
        done = st.next_state(verified, cand, NOW)
        self.assertEqual(done["archived"][0]["id"], "T-0020")
        self.assertEqual(len(done["archived"][0]["artifacts"]), 2)

    def test_external_archive_of_pending_worker_is_not_done(self):
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "pending", "none", session_id=None)
        cand = self.candidate()
        find(cand["workers"], "w-0030")["observed_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0030")["status"], "needs_user")

    def test_cancel_shape_validated(self):
        for bad in ("yes", {"at": NOW}, {"at": NOW, "reason": ""}, {"at": 1, "reason": "x"}):
            cand = self.candidate()
            find(cand["tasks"], "T-0004")["cancel"] = bad
            self.assertRejected(cand, "cancel must be")

    def test_cancel_rejected_while_worker_intended_running(self):
        cand = self.candidate()
        find(cand["tasks"], "T-0003")["cancel"] = {"at": NOW, "reason": "no longer needed"}
        self.assertRejected(cand, "cancelled but worker w-0004 is still intended running")

    def test_cancel_unstarted_task_archives_immediately(self):
        cand = self.candidate()
        find(cand["tasks"], "T-0004")["cancel"] = {"at": NOW, "reason": "no longer needed"}
        new = st.next_state(self.current, cand, NOW)
        self.assertNotIn("T-0004", [t["id"] for t in new["tasks"]])
        self.assertNotIn("w-0007", [w["id"] for w in new["workers"]])
        self.assertEqual(new["archived"][0], {"id": "T-0004", "title": "Tidy notes folder", "request": "~/notes の重複ファイルを整理して",
                                              "artifacts": [], "archived_at": NOW, "cancelled": True, "reason": "no longer needed"})

    def test_cancel_waits_for_archive_observed_without_artifact_or_push_checks(self):
        git_task(self.current, "T-0020", "w-0020", intended="running", observed="idle")
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "idle")
        cand = self.candidate()
        for tid, wid in (("T-0020", "w-0020"), ("T-0030", "w-0030")):
            find(cand["tasks"], tid)["cancel"] = {"at": NOW, "reason": "failed"}
            find(cand["tasks"], tid)["user_actions"].append({"id": "UA-0" + wid[-3:], "description": "Answer the approval", "since": NOW})
            find(cand["workers"], wid)["intended_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "needs_user")
        self.assertTrue(st.cwd_busy(new, "/data/x"))
        cand = copy.deepcopy(new)
        for wid in ("w-0020", "w-0030"):
            find(cand["workers"], wid)["observed_state"] = "archived"
        done = st.next_state(new, cand, NOW)
        self.assertEqual([t["id"] for t in done["tasks"]], ["T-0003", "T-0004"])
        self.assertEqual({s["id"] for s in done["archived"][:2]}, {"T-0020", "T-0030"})
        self.assertTrue(all(s["cancelled"] and s["reason"] == "failed" for s in done["archived"][:2]))

    def test_cancel_still_requires_archive_consent(self):
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "idle")
        cand = self.candidate()
        cand["supervisor"]["archive_consent"] = None
        find(cand["tasks"], "T-0030")["cancel"] = {"at": NOW, "reason": "x"}
        find(cand["workers"], "w-0030")["intended_state"] = "archived"
        self.assertRejected(cand, "archive_consent")

    def test_cancelled_waiting_task_gets_no_start_action(self):
        nongit_task(self.current, "T-0005", "w-0006", "/Users/handlename/notes", "archived", "idle")
        find(self.current["tasks"], "T-0005")["artifacts"] = [{"kind": "file", "path": "/Users/handlename/notes/a.txt"}]
        nongit_task(self.current, "T-0006", "w-0008", "/Users/handlename/notes", "running", "idle")
        cand = self.candidate()
        find(cand["workers"], "w-0006")["observed_state"] = "archived"
        state = st.next_state(self.current, cand, NOW)
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0008")["intended_state"] = "archived"
        find(cand["tasks"], "T-0006")["cancel"] = {"at": NOW, "reason": "x"}
        find(cand["tasks"], "T-0004")["cancel"] = {"at": NOW, "reason": "x"}
        find(cand["tasks"], "T-0004")["user_actions"] = [
            {"id": "UA-0009", "description": st.waiting_start_description("T-0004"), "since": NOW}]
        state = st.next_state(state, cand, NOW)
        self.assertNotIn("T-0004", [t["id"] for t in state["tasks"]])
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0008")["observed_state"] = "archived"
        state = st.next_state(state, cand, NOW)
        self.assertEqual([t["id"] for t in state["tasks"]], ["T-0003"])


    def test_cancel_with_missing_worker_completes(self):
        # Re-review D: a cancelled task whose session vanished must not stay running.
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "missing")
        cand = self.candidate()
        find(cand["tasks"], "T-0030")["cancel"] = {"at": NOW, "reason": "session lost"}
        find(cand["workers"], "w-0030")["intended_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        self.assertNotIn("T-0030", [t["id"] for t in new["tasks"]])
        self.assertEqual(new["archived"][0]["id"], "T-0030")
        self.assertTrue(new["archived"][0]["cancelled"])
        self.assertFalse(st.cwd_busy(new, "/data/x"))

    def test_replacement_worker_finishes_task_with_missing_worker(self):
        # Re-review C3: the old missing worker is given up (intended archived) and a new worker completes.
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "missing")
        cand = self.candidate()
        find(cand["workers"], "w-0030")["intended_state"] = "archived"
        cand["workers"].append(dict(find(cand["workers"], "w-0030"), id="w-0031", session_id="sess_31",
                                    intended_state="running", observed_state="running"))
        find(cand["tasks"], "T-0030")["worker_ids"].append("w-0031")
        find(cand["tasks"], "T-0030")["artifacts"] = [{"kind": "file", "path": "/data/x/out.txt"}]
        state = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(state["tasks"], "T-0030")["status"], "running")
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0031")["intended_state"] = "archived"
        state = st.next_state(state, cand, NOW)
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0031")["observed_state"] = "archived"
        state = st.next_state(state, cand, NOW)
        self.assertNotIn("T-0030", [t["id"] for t in state["tasks"]])
        self.assertEqual(state["archived"][0]["id"], "T-0030")

    def test_verifying_external_archive_needs_no_archive_consent(self):
        # Re-review A2: the user archived the worker; recording it needs no consent.
        self.current["supervisor"]["archive_consent"] = None
        git_task(self.current, "T-0020", "w-0020", intended="running", observed="archived")
        cand = self.candidate()
        find(cand["workers"], "w-0020")["intended_state"] = "archived"
        commit_artifacts(cand, "T-0020")
        push_decision(cand, "T-0020")
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "needs_user")

    def test_cancel_after_external_archive_needs_no_archive_consent(self):
        # Re-review B: cancel a task whose worker the user archived in the app.
        self.current["supervisor"]["archive_consent"] = None
        git_task(self.current, "T-0020", "w-0020", intended="running", observed="archived")
        cand = self.candidate()
        find(cand["tasks"], "T-0020")["cancel"] = {"at": NOW, "reason": "abandoned"}
        find(cand["workers"], "w-0020")["intended_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(new["archived"][0]["id"], "T-0020")
        self.assertTrue(new["archived"][0]["cancelled"])

    def test_replace_missing_commit_worker_without_artifacts(self):
        # Re-review 2 C2: the missing worker is given up in the same candidate that adds its replacement.
        self.current["supervisor"]["archive_consent"] = None
        git_task(self.current, "T-0020", "w-0020", intended="running", observed="missing")
        cand = self.candidate()
        find(cand["workers"], "w-0020")["intended_state"] = "archived"
        cand["workers"].append(dict(find(cand["workers"], "w-0020"), id="w-0021", session_id="sess_21",
                                    intended_state="running", observed_state="running"))
        find(cand["tasks"], "T-0020")["worker_ids"].append("w-0021")
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "running")

    def test_replace_missing_nongit_worker_without_artifacts(self):
        # Re-review 2 C2b.
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "missing")
        cand = self.candidate()
        find(cand["workers"], "w-0030")["intended_state"] = "archived"
        cand["workers"].append(dict(find(cand["workers"], "w-0030"), id="w-0031", session_id="sess_31",
                                    intended_state="running", observed_state="running"))
        find(cand["tasks"], "T-0030")["worker_ids"].append("w-0031")
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0030")["status"], "running")
        self.assertTrue(st.cwd_busy(new, "/data/x"))

    def test_cancel_missing_worker_without_archive_consent(self):
        # Re-review 2 D.
        self.current["supervisor"]["archive_consent"] = None
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "missing")
        cand = self.candidate()
        find(cand["tasks"], "T-0030")["cancel"] = {"at": NOW, "reason": "session lost"}
        find(cand["workers"], "w-0030")["intended_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        self.assertTrue(new["archived"][0]["cancelled"])

    def test_running_worker_archive_still_requires_consent_and_artifacts(self):
        self.current["supervisor"]["archive_consent"] = None
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "idle")
        cand = self.candidate()
        find(cand["workers"], "w-0030")["intended_state"] = "archived"
        self.assertRejected(cand, "lacks files artifacts")
        find(cand["tasks"], "T-0030")["artifacts"] = [{"kind": "file", "path": "/data/x/out.txt"}]
        self.assertRejected(cand, "archive_consent")

    def test_only_missing_archived_workers_do_not_finish_task(self):
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "missing")
        cand = self.candidate()
        find(cand["workers"], "w-0030")["intended_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0030")["status"], "needs_user")
        self.assertNotIn("T-0030", [s["id"] for s in new["archived"]])
        self.assertFalse(st.cwd_busy(new, "/data/x"))

    def test_missing_archived_plus_archived_replacement_completes(self):
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "archived", "missing")
        cand = self.candidate()
        cand["workers"].append(dict(find(cand["workers"], "w-0030"), id="w-0031", session_id="sess_31",
                                    intended_state="archived", observed_state="idle"))
        find(cand["tasks"], "T-0030")["worker_ids"].append("w-0031")
        find(cand["tasks"], "T-0030")["artifacts"] = [{"kind": "file", "path": "/data/x/out.txt"}]
        state = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(state["tasks"], "T-0030")["status"], "running")
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0031")["observed_state"] = "archived"
        state = st.next_state(state, cand, NOW)
        self.assertEqual(state["archived"][0]["id"], "T-0030")
        self.assertNotIn("cancelled", state["archived"][0])

    def pending_push(self):
        git_task(self.current, "T-0020", "w-0020", intended="archived", observed="archived")
        commit_artifacts(self.current, "T-0020")
        push_decision(self.current, "T-0020")

    def push_worker_candidate(self, text="push して PR を作って", message="claude/license-x1 を push して PR を作って"):
        cand = self.candidate()
        task = find(cand["tasks"], "T-0020")
        task["push_consent"] = {"text": text, "at": NOW}
        if message is not None:
            task["push_consent"]["message"] = message
        task["user_actions"] = []
        cand["workers"].append(dict(find(cand["workers"], "w-0020"), id="w-0021", session_id=None, delivery="push_pr",
                                    worktree_path=None, intended_state="pending", observed_state="none"))
        task["worker_ids"].append("w-0021")
        return cand

    def test_consent_message_accepted_while_resolving_push_decision(self):
        self.pending_push()
        new = st.next_state(self.current, self.push_worker_candidate(), NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "waiting")

    def test_consent_message_without_pending_push_decision_rejected(self):
        cand = self.candidate()
        find(cand["tasks"], "T-0003")["push_consent"]["message"] = "もう一度 push して PR を作ってよい"
        self.assertRejected(cand, "T-0003: push_consent.message may be set only while resolving a pending push decision")

    def test_consent_message_with_push_decision_kept_rejected(self):
        self.pending_push()
        cand = self.push_worker_candidate()
        push_decision(cand, "T-0020")
        self.assertRejected(cand, "only while resolving a pending push decision")

    def test_consent_text_not_in_message_rejected(self):
        self.pending_push()
        self.assertRejected(self.push_worker_candidate(text="PR は不要"), "push_consent.text quoted from push_consent.message")

    def test_consent_text_not_in_request_without_message_rejected(self):
        self.pending_push()
        self.assertRejected(self.push_worker_candidate(message=None), "push_consent.text quoted")

    def test_non_string_consent_message_rejected(self):
        self.pending_push()
        for bad in (["push"], 1, ""):
            self.assertRejected(self.push_worker_candidate(message=bad), "push_consent.message must be a non-empty string")

    def test_switching_running_commit_worker_with_message_rejected(self):
        git_task(self.current, "T-0020", "w-0020", intended="running", observed="running")
        cand = self.candidate()
        find(cand["tasks"], "T-0020")["push_consent"] = {"text": "push して", "at": NOW, "message": "push して"}
        find(cand["workers"], "w-0020")["delivery"] = "push_pr"
        self.assertRejected(cand, "only while resolving a pending push decision")

    def test_new_push_worker_flow_archives_task(self):
        self.pending_push()
        state = st.next_state(self.current, self.push_worker_candidate(), NOW)
        w = find(state["workers"], "w-0021")
        self.assertEqual((w["branch"], w["base_branch"]), ("claude/license-x1", "main"))
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0021").update(session_id="sess_21", intended_state="running", observed_state="running")
        state = st.next_state(state, cand, NOW)
        self.assertEqual(find(state["tasks"], "T-0020")["status"], "running")
        cand = copy.deepcopy(state)
        find(cand["tasks"], "T-0020")["artifacts"].append({"kind": "pr", "url": "https://github.com/org/a/pull/7"})
        find(cand["workers"], "w-0021")["intended_state"] = "archived"
        state = st.next_state(state, cand, NOW)
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0021")["observed_state"] = "archived"
        state = st.next_state(state, cand, NOW)
        self.assertNotIn("T-0020", [t["id"] for t in state["tasks"]])
        self.assertEqual(state["archived"][0]["id"], "T-0020")

    def test_reuse_push_worker_flow(self):
        git_task(self.current, "T-0020", "w-0020", intended="archived", observed="idle")
        commit_artifacts(self.current, "T-0020")
        push_decision(self.current, "T-0020")
        find(self.current["tasks"], "T-0020")["user_actions"].append(
            {"id": "UA-0101", "description": "Close w-0020 from the screen or unpin it", "since": NOW})
        cand = self.candidate()
        task = find(cand["tasks"], "T-0020")
        task["push_consent"] = {"text": "push して", "at": NOW, "message": "やっぱり push して"}
        task["user_actions"] = []
        find(cand["workers"], "w-0020").update(delivery="push_pr", intended_state="running", continues=0)
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "running")

    def test_archived_stub_push_flow(self):
        self.current["archived"].insert(0, {"id": "T-0019", "title": "old", "archived_at": NOW, "artifacts": [
            {"kind": "branch", "label": "a", "ref": "/repo/a#claude/license-x1"},
            {"kind": "commit", "label": "a", "ref": "abc123"},
        ]})
        cand = self.candidate()
        git_task(cand, "T-0060", "w-0060", delivery="push_pr", intended="pending", observed="none")
        task = find(cand["tasks"], "T-0060")
        task["request"] = "T-0019 の claude/license-x1 を push して PR を作ってよい"
        task["push_consent"] = {"text": "push して PR を作ってよい", "at": NOW}
        task["artifacts"] = copy.deepcopy(cand["archived"][0]["artifacts"])
        find(cand["workers"], "w-0060")["session_id"] = None
        state = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(state["tasks"], "T-0060")["status"], "waiting")
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0060").update(session_id="sess_60", intended_state="archived", observed_state="idle")
        with self.assertRaisesRegex(st.StateError, "lacks push_pr artifacts"):
            st.next_state(state, cand, NOW)
        find(cand["tasks"], "T-0060")["artifacts"].append({"kind": "pr", "url": "https://github.com/org/a/pull/8"})
        state = st.next_state(state, cand, NOW)
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0060")["observed_state"] = "archived"
        state = st.next_state(state, cand, NOW)
        self.assertEqual(state["archived"][0]["id"], "T-0060")

    def test_followups_shape(self):
        cand = self.candidate()
        find(cand["tasks"], "T-0004")["followups"] = [{"text": "重複は消さずに移動して", "at": NOW}]
        cand["archived"][1]["followups"] = [{"text": "typo をもう 1 つ直して", "at": NOW}]
        st.next_state(self.current, cand, NOW)
        for bad in ("x", [1], [{"text": "", "at": NOW}], [{"text": "a"}], [{"text": "a", "at": 1}]):
            for owner in ("task", "stub"):
                with self.subTest(bad=bad, owner=owner):
                    cand = self.candidate()
                    (find(cand["tasks"], "T-0004") if owner == "task" else cand["archived"][0])["followups"] = bad
                    self.assertRejected(cand, "followups must be a list of")
        cand = self.candidate()
        cand["archived"][0]["request"] = ["x"]
        self.assertRejected(cand, "archived T-0002: request must be a string")

    def test_request_change_rejected(self):
        cand = self.candidate()
        find(cand["tasks"], "T-0004")["request"] = "~/notes を整理して"
        self.assertRejected(cand, "task T-0004: request is verbatim and must not change")
        cand = self.candidate()
        find(cand["tasks"], "T-0004")["followups"] = [{"text": "~/notes を整理して", "at": NOW}]
        st.next_state(self.current, cand, NOW)

    def stub(self, task_id="T-0019", request="fix the LICENSE year", **extra):
        stub = {"id": task_id, "title": "old", "request": request, "archived_at": NOW, "artifacts": [
            {"kind": "branch", "label": "a", "ref": "/repo/a#claude/license-old"},
            {"kind": "commit", "label": "a", "ref": "old111"},
            {"kind": "pr", "url": PR42},
        ], "followups": [{"text": "年を 2026 にして", "at": NOW}]}
        stub.update(extra)
        return stub

    def revived(self, state, followups=True):
        git_task(state, "T-0019", "w-0019", intended="pending", observed="none")
        find(state["workers"], "w-0019")["session_id"] = None
        if followups:
            find(state["tasks"], "T-0019")["followups"] = [{"text": "年を 2026 にして", "at": NOW}, {"text": "README も直して", "at": NOW}]
        return find(state["tasks"], "T-0019")

    def test_revive_stub_with_same_request(self):
        self.current["archived"].insert(0, self.stub())
        cand = self.candidate()
        self.revived(cand)
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0019")["status"], "waiting")
        self.assertEqual(new["archived"][0]["id"], "T-0019")

    def test_revive_rejected_unless_non_cancelled_stub_with_same_request(self):
        no_request = self.stub()
        del no_request["request"]
        for stub in (self.stub(request="別の依頼"), no_request, self.stub(cancelled=True, reason="x")):
            with self.subTest(stub=stub):
                self.current = fixture_state()
                self.current["archived"].insert(0, stub)
                cand = self.candidate()
                self.revived(cand)
                self.assertRejected(cand, "T-0019: id is an archived stub; revive only a non-cancelled stub with the same request")

    def test_rearchive_replaces_stub_and_concatenates_artifacts(self):
        self.current["archived"].insert(0, self.stub())
        task = self.revived(self.current)
        find(self.current["workers"], "w-0019").update(session_id="sess_19", intended_state="archived", observed_state="idle")
        task["artifacts"] = [{"kind": "branch", "label": "a", "ref": "/repo/a#claude/license-new"},
                             {"kind": "commit", "label": "a", "ref": "new222"}]
        cand = self.candidate()
        find(cand["workers"], "w-0019")["observed_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        self.assertNotIn("T-0019", [t["id"] for t in new["tasks"]])
        stubs = [s for s in new["archived"] if s["id"] == "T-0019"]
        self.assertEqual(len(stubs), 1)
        self.assertIs(new["archived"][0], stubs[0])
        refs = [a.get("ref") or a.get("url") for a in stubs[0]["artifacts"]]
        self.assertEqual(refs, ["/repo/a#claude/license-old", "old111", PR42, "/repo/a#claude/license-new", "new222"])
        self.assertEqual([a["ref"] for a in stubs[0]["artifacts"] if a["kind"] == "branch"][-1], "/repo/a#claude/license-new")
        self.assertEqual(stubs[0]["request"], "fix the LICENSE year")
        self.assertEqual([f["text"] for f in stubs[0]["followups"]], ["年を 2026 にして", "README も直して"])

    def test_stub_without_followups_has_no_followups_key(self):
        cand = self.candidate()
        find(cand["tasks"], "T-0004")["cancel"] = {"at": NOW, "reason": "x"}
        new = st.next_state(self.current, cand, NOW)
        self.assertNotIn("followups", new["archived"][0])
        cand = self.candidate()
        find(cand["tasks"], "T-0004").update(cancel={"at": NOW, "reason": "x"}, followups=[{"text": "やっぱり不要", "at": NOW}])
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(new["archived"][0]["followups"], [{"text": "やっぱり不要", "at": NOW}])

    def test_revived_task_new_pr_urls_returns_only_new_pr(self):
        self.current["archived"].insert(0, self.stub())
        cand = self.candidate()
        self.revived(cand)["artifacts"] = [{"kind": "branch", "label": "a", "ref": "/repo/a#claude/license-new"},
                                           {"kind": "commit", "label": "a", "ref": "new222"},
                                           {"kind": "pr", "url": PR43}]
        self.assertEqual(st.new_pr_urls(self.current, cand), [("T-0019", PR43)])
        state = st.next_state(self.current, cand, NOW)
        self.assertEqual(st.new_pr_urls(state, copy.deepcopy(state)), [])

    def replace(self, cand, old_id, new_id, **successor):
        old = find(cand["workers"], old_id)
        fresh = dict(old, id=new_id, session_id=None, intended_state="pending", observed_state="none", observed_at=None,
                     last_report=None)
        fresh.update(successor)
        old.update(intended_state="archived", replaced_by=new_id)
        cand["workers"].append(fresh)
        find(cand["tasks"], old["task_id"])["worker_ids"].append(new_id)
        return fresh

    def test_replace_missing_nongit_worker_gets_no_waiting_action(self):
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "missing")
        cand = self.candidate()
        self.replace(cand, "w-0030", "w-0031")
        new = st.next_state(self.current, cand, NOW)
        task = find(new["tasks"], "T-0030")
        self.assertEqual(task["user_actions"], [])
        self.assertEqual(task["status"], "waiting")

    def test_replace_missing_git_worker_gets_no_waiting_action(self):
        git_task(self.current, "T-0020", "w-0020", intended="running", observed="missing")
        cand = self.candidate()
        self.replace(cand, "w-0020", "w-0021")
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["user_actions"], [])
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "waiting")

    def test_replace_external_archive_of_commit_worker_needs_no_push_decision_or_artifacts(self):
        self.current["supervisor"]["archive_consent"] = None
        git_task(self.current, "T-0020", "w-0020", intended="running", observed="archived")
        cand = self.candidate()
        self.replace(cand, "w-0020", "w-0021")
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "waiting")

    def test_replace_still_requires_archive_consent_for_a_live_worker(self):
        self.current["supervisor"]["archive_consent"] = None
        git_task(self.current, "T-0020", "w-0020", intended="running", observed="idle")
        cand = self.candidate()
        self.replace(cand, "w-0020", "w-0021")
        self.assertRejected(cand, "archive requested without supervisor.archive_consent")

    def test_replace_push_only_worker(self):
        self.pending_push()
        state = st.next_state(self.current, self.push_worker_candidate(), NOW)
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0021").update(session_id="sess_21", intended_state="running", observed_state="running")
        state = st.next_state(state, cand, NOW)
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0021")["observed_state"] = "archived"
        fresh = self.replace(cand, "w-0021", "w-0022")
        self.assertEqual((fresh["delivery"], fresh["branch"]), ("push_pr", "claude/license-x1"))
        state = st.next_state(state, cand, NOW)
        self.assertEqual(find(state["tasks"], "T-0020")["status"], "waiting")

    def test_replacement_keeps_inherited_base_branch(self):
        git_task(self.current, "T-0020", "w-0020", intended="running", observed="missing")
        find(self.current["workers"], "w-0020")["base_branch"] = "origin/claude/license-x0"
        cand = self.candidate()
        self.replace(cand, "w-0020", "w-0021", branch=None, worktree_path=None)
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["workers"], "w-0021")["base_branch"], "origin/claude/license-x0")
        self.assertEqual(find(new["workers"], "w-0020")["replaced_by"], "w-0021")

    def test_invalid_replaced_by_rejected(self):
        git_task(self.current, "T-0020", "w-0020", intended="running", observed="missing")
        git_task(self.current, "T-0021", "w-0030", intended="running", observed="running")
        cases = [
            ("w-0030", "archived", "replaced_by w-0030 must name another worker of the same task"),
            ("w-0020", "archived", "replaced_by w-0020 must name another worker of the same task"),
            ("w-9999", "archived", "replaced_by w-9999 must name another worker of the same task"),
            ("w-0021", "running", "w-0020: replaced_by requires intended_state archived"),
            (7, "archived", "replaced_by must be a string"),
        ]
        for target, intended, pattern in cases:
            with self.subTest(target=target, intended=intended):
                cand = self.candidate()
                self.replace(cand, "w-0020", "w-0021")
                find(cand["workers"], "w-0020").update(replaced_by=target, intended_state=intended)
                self.assertRejected(cand, pattern)

    def test_replaced_by_cycle_rejected(self):
        git_task(self.current, "T-0020", "w-0020", intended="running", observed="missing")
        cand = self.candidate()
        self.replace(cand, "w-0020", "w-0021")
        find(cand["workers"], "w-0021").update(intended_state="archived", observed_state="missing", replaced_by="w-0020")
        self.assertRejected(cand, "replaced_by chain loops")

    def test_replaced_external_archive_and_given_up_successor_need_user(self):
        git_task(self.current, "T-0020", "w-0020", intended="running", observed="archived")
        cand = self.candidate()
        self.replace(cand, "w-0020", "w-0021", session_id="sess_21", intended_state="archived", observed_state="missing")
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "needs_user")
        self.assertNotIn("T-0020", [s["id"] for s in new["archived"]])

    def test_reappeared_replaced_nongit_worker_must_stay_missing(self):
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "missing")
        cand = self.candidate()
        self.replace(cand, "w-0030", "w-0031", session_id="sess_31", intended_state="running", observed_state="running")
        state = st.next_state(self.current, cand, NOW)
        cand = copy.deepcopy(state)
        find(cand["workers"], "w-0030")["observed_state"] = "idle"
        with self.assertRaisesRegex(st.StateError, "held by more than one worker"):
            st.next_state(state, cand, NOW)
        cand = copy.deepcopy(state)
        find(cand["tasks"], "T-0030")["next_action"] = "w-0030 reappeared; archive it"
        st.next_state(state, cand, NOW)


GUARD_TAIL = "The supervisor does not change repositories"

BASH_BLOCK = [
    "git commit -m x",
    "git -C /repo push origin b",
    "cd /repo && git push",
    "FOO=1 git add .",
    "/usr/bin/git reset --hard",
    "echo a#b; git push",
    "git status\ngit push",
    "git status &&\n git push",
    "git status;\ngit push",
    "git status\n\ngit push",
    "git -C /repo \\\n push",
    "echo ok\ngit push",
    "git worktree add ../w",
    "git tag v1",
    "git branch -D b",
    "git symbolic-ref -d HEAD",
    "git symbolic-ref HEAD refs/heads/x",
    "git config set user.name x",
    "git config --unset user.name",
    "git config user.name x",
    "git diff --output=/tmp/x",
    "git log --output /tmp/x",
    "git reflog expire --all",
    "git fetch",
    "jj commit -m x",
    "jj describe -m x",
    "jj -R ~/supervisor git push",
    "gh pr create --title t --body b",
    "gh pr merge 1",
    "gh issue create",
    "gh api -X POST repos/o/r/issues",
    "gh api repos/o/r/issues -f title=x",
    "bash -c 'git push'",
    "bash -lc 'git push'",
    "sh -ec 'git commit -m x'",
    "echo $(git push)",
    'echo "$(git push)"',
    "echo `git commit`",
    'echo "unbalanced git push',
]

BASH_ALLOW = [
    "git -C /repo status --porcelain",
    "git status\ngit log -1",
    "git -C /repo \\\n status --porcelain",
    "git rev-parse @{u}",
    "git log --format=%H -1",
    "git diff --output-indicator-new=+ HEAD",
    "git rev-list --count main..HEAD",
    "git ls-remote origin refs/heads/b",
    "git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null",
    "git remote -v 2>&1",
    "git remote get-url origin",
    "git branch --list b",
    "git stash show",
    "git config get user.email",
    "git config user.email",
    "git config --get user.email",
    "git log --grep=push",
    "gh pr view 1 --json state | grep MERGED",
    "gh api repos/o/r/pulls/1",
    "gh api -X GET search/issues -f q=x",
    "jj st",
    "jj -R ~/supervisor log",
    'echo "git push"',
    "git ci -m x",
    'echo "unbalanced',
]


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.dir = root / "sup"
        self.config = root / "config"
        self.outside = root / "outside"
        for d in (self.dir / ".supervisor", self.config, self.outside):
            d.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, path, cwd=None, tmp_dirs=()):
        return st.path_violation(str(path), str(cwd or self.dir), str(self.dir), str(self.config), list(tmp_dirs))

    def decide(self, tool, value, env=None, **extra):
        key = st.GUARD_INPUT_KEYS.get(tool, "file_path")
        payload = dict({"tool_name": tool, "tool_input": {key: value}, "cwd": str(self.dir)}, **extra)
        return st.guard_decision(payload, str(self.dir), env or {"CLAUDE_CONFIG_DIR": str(self.config)})

    def test_bash_block(self):
        for command in BASH_BLOCK:
            with self.subTest(command=command):
                self.assertTrue(st.bash_violations(command))
                message = self.decide("Bash", command)
                self.assertTrue(message.startswith("[session-supervisor guard] blocked: "), message)
                self.assertIn(GUARD_TAIL, message)

    def test_bash_allow(self):
        commands = BASH_ALLOW + [
            f"python3 {self.dir}/.supervisor/state_tool.py commit --state {self.dir}/state.json --candidate {self.dir}/state.next.json",
            f"cp {self.dir}/state.json {self.dir}/state.next.json",
            f"zed '{self.dir}'",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(st.bash_violations(command), [])
                self.assertIsNone(self.decide("Bash", command))

    def test_prepare_dir_commands_are_allowed(self):
        script = self.dir / ".supervisor" / "state_tool.py"
        for url in ("https://github.com/org/repo-a", "git@github.com:org/repo-a.git", "https://github.com/org/git"):
            command = f"python3 {script} prepare-dir --claude-md {self.dir}/CLAUDE.md --clone {url}"
            with self.subTest(command=command):
                self.assertEqual(st.bash_violations(command), [])
                self.assertIsNone(self.decide("Bash", command))
        self.assertIsNone(self.decide("Bash", f"python3 {script} prepare-dir --claude-md {self.dir}/CLAUDE.md --work T-0001-notes"))

    def test_block_message_names_the_subcommand(self):
        self.assertIn("blocked: git push (", self.decide("Bash", "git -C /repo push origin b"))
        self.assertIn("gh pr create (", self.decide("Bash", "gh pr create"))

    def test_path_block(self):
        link = self.dir / "link"
        link.symlink_to(self.outside, target_is_directory=True)
        cases = [
            (self.outside / "x.txt", None),
            ("../x", self.dir),
            (link / "x.txt", None),
            (self.dir / ".claude" / "settings.json", None),
            (self.dir / "state.json", None),
            (self.dir / ".supervisor" / "state_tool.py", None),
            (self.dir / ".supervisor" / "dashboard.template.html", None),
            (self.config / "plans" / "p.md", None),
            (self.config / "settings.json", None),
        ]
        for path, cwd in cases:
            with self.subTest(path=str(path)):
                self.assertIsNotNone(self.path(path, cwd))

    def test_path_allow(self):
        cases = [
            (self.dir / "state.next.json", ()),
            (self.dir / ".supervisor" / "observations" / "verify-w-0001.json", ()),
            ("state.next.json", ()),
            (self.config / "projects" / "x" / "memory" / "m.md", ()),
            (self.outside / "scratch" / "n.txt", (str(self.outside),)),
        ]
        for path, tmp_dirs in cases:
            with self.subTest(path=str(path)):
                self.assertIsNone(self.path(path, tmp_dirs=tmp_dirs))

    def test_tool_routing(self):
        outside = "/nonexistent-sv-guard/x.ipynb"
        for tool in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
            with self.subTest(tool=tool):
                self.assertIn(f"blocked: {tool} /nonexistent-sv-guard/x.ipynb (outside", self.decide(tool, outside))
        self.assertIsNone(self.decide("Edit", str(self.dir / "state.next.json")))
        self.assertIsNone(self.decide("Write", "/tmp/sv-guard-scratch.txt"))

    def test_memory_dir_with_and_without_config_env(self):
        self.assertIsNone(self.decide("Write", str(self.config / "projects" / "x" / "memory" / "m.md")))
        self.assertIsNotNone(self.decide("Write", str(self.config / "plans" / "p.md")))
        home = self.config.parent / "home"
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.assertIsNone(self.decide("Write", str(home / ".claude" / "projects" / "x" / "memory" / "m.md"), env={"TMPDIR": ""}))
            self.assertIsNotNone(self.decide("Write", str(home / ".claude" / "plans" / "p.md"), env={"TMPDIR": ""}))

    def test_unknown_tool_missing_fields_and_agent_id(self):
        self.assertIsNone(self.decide("Read", "/etc/hosts"))
        self.assertIsNone(st.guard_decision({"tool_name": "Bash"}, str(self.dir), {}))
        self.assertIsNone(st.guard_decision({"tool_name": "Edit", "tool_input": "x"}, str(self.dir), {}))
        self.assertIsNone(self.decide("Bash", "git status", agent_id="a1", agent_type="observer"))
        self.assertIsNotNone(self.decide("Bash", "git push", agent_id="a1", agent_type="observer"))

    def installed_command(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), "setup", "--dir", str(self.dir)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        settings = json.loads((self.dir / ".claude" / "settings.json").read_text(encoding="utf-8"))
        return settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]

    def run_hook(self, command, stdin):
        return subprocess.run(command, shell=True, input=stdin, capture_output=True, text=True)

    def test_wrapper_blocks_with_exit_2(self):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": str(self.dir)})
        proc = self.run_hook(self.installed_command(), payload)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("[session-supervisor guard] blocked: git push", proc.stderr)
        allowed = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": str(self.dir)})
        self.assertEqual(self.run_hook(self.installed_command(), allowed).returncode, 0)

    def test_wrapper_fails_open(self):
        command = self.installed_command()
        proc = self.run_hook(command, "{not json")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("[session-supervisor guard] warning:", proc.stderr)
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push"}})
        script = self.dir / ".supervisor" / "state_tool.py"
        script.write_text("import argparse\np = argparse.ArgumentParser()\np.add_subparsers(required=True).add_parser('digest')\np.parse_args()\n", encoding="utf-8")
        self.assertEqual(self.run_hook(command, payload).returncode, 0)
        script.unlink()
        self.assertEqual(self.run_hook(command, payload).returncode, 0)


class CommitCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.state = self.dir / "state.json"
        self.cand = self.dir / "state.next.json"
        self.html = self.dir / "dashboard.html"
        self.state.write_text(st.serialize(fixture_state()), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_commit(self, candidate):
        self.cand.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = st.main(["commit", "--state", str(self.state), "--candidate", str(self.cand)])
        return code, out.getvalue(), err.getvalue()

    def current(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def test_commit_writes_and_leaves_no_temp_files(self):
        code, out, _ = self.run_commit(self.current())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"rev": 13, "dashboard_changed": True})
        self.assertEqual(self.current()["rev"], 13)
        self.assertTrue(self.html.exists())
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["dashboard.html", "state.json", "state.next.json"])

    def test_rev_only_change_is_not_dashboard_change(self):
        self.run_commit(self.current())
        code, out, _ = self.run_commit(self.current())
        self.assertEqual(json.loads(out), {"rev": 14, "dashboard_changed": False})
        self.assertIn("rev 14", self.html.read_text(encoding="utf-8"))

    def test_visible_change_is_dashboard_change(self):
        self.run_commit(self.current())
        cand = self.current()
        find(cand["tasks"], "T-0003")["next_action"] = "Review PR #42"
        _, out, _ = self.run_commit(cand)
        self.assertTrue(json.loads(out)["dashboard_changed"])

    def test_failure_leaves_files_unchanged(self):
        self.run_commit(self.current())
        before_state = self.state.read_bytes()
        before_html = self.html.read_bytes()
        cand = self.current()
        cand["rev"] = 1
        code, out, err = self.run_commit(cand)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("error:", err)
        self.assertEqual(self.state.read_bytes(), before_state)
        self.assertEqual(self.html.read_bytes(), before_html)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["dashboard.html", "state.json", "state.next.json"])


    def test_html_rename_failure_after_state_replace_reports_success(self):
        self.run_commit(self.current())
        real_replace = os.replace

        def failing_replace(src, dst):
            if str(dst) == str(self.html):
                raise OSError("disk full")
            return real_replace(src, dst)

        before_html = self.html.read_bytes()
        with mock.patch.object(st.os, "replace", failing_replace):
            code, out, err = self.run_commit(self.current())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"rev": 14, "dashboard_changed": True, "dashboard_error": "disk full"})
        self.assertIn("warning: state committed at rev 14", err)
        self.assertEqual(self.current()["rev"], 14)
        self.assertEqual(self.html.read_bytes(), before_html)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["dashboard.html", "state.json", "state.next.json"])
        _, out, _ = self.run_commit(self.current())
        self.assertEqual(json.loads(out), {"rev": 15, "dashboard_changed": False})

    def test_malformed_candidate_is_an_error_line(self):
        cand = self.current()
        cand["tasks"][0]["artifacts"] = ["oops"]
        code, out, err = self.run_commit(cand)
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("error:"), err)
        self.assertNotIn("Traceback", err)
        for bad in ([], "x", None):
            code, _, err = self.run_commit(bad)
            self.assertEqual(code, 1)
            self.assertTrue(err.startswith("error:"), err)


class RenderTest(unittest.TestCase):
    def page(self, state):
        return st.assemble(*st.render(state, TEMPLATE))

    def test_deterministic(self):
        self.assertEqual(self.page(fixture_state()), self.page(fixture_state()))

    def test_section_order_and_title(self):
        page = self.page(fixture_state())
        self.assertIn("<title>Supervisor Dashboard</title>", page)
        self.assertLess(page.index('id="actions"'), page.index('id="tasks"'))
        self.assertLess(page.index('id="tasks"'), page.index('id="archived"'))
        self.assertNotIn('id="artifacts"', page)
        self.assertNotIn("{{", page)

    def test_task_card_lists_its_artifacts(self):
        page = self.page(fixture_state())
        tasks = page[page.index('id="tasks"'):page.index('id="archived"')]
        self.assertIn('href="https://github.com/org/repo-a/pull/42"', tasks)

    def test_archived_stubs_show_artifacts_and_cancelled_marker(self):
        page = self.page(fixture_state())
        archived = page[page.index('id="archived"'):]
        self.assertIn('href="https://github.com/org/repo-a/pull/40"', archived)
        self.assertIn("T-0002", archived)
        self.assertIn('data-state="cancelled"', archived)
        self.assertIn("ユーザーが不要と判断", archived)
        self.assertEqual(archived.count('data-state="cancelled"'), 1)

    def test_hero_counts_every_attention_source(self):
        state = fixture_state()
        find(state["workers"], "w-0004")["observed_state"] = "archived"
        hero = self.page(state).split('id="actions"', 1)[1].split('id="tasks"', 1)[0]
        # UA-0002, UA-0003 (follow-up), w-0005 needs_input, w-0004 archived outside the supervisor
        self.assertIn('data-pending="4"', hero)
        self.assertIn('<p class="hero__count">4</p>', hero)

    def test_hero_has_no_pending_marker_when_clear(self):
        state = fixture_state()
        find(state["tasks"], "T-0003")["user_actions"] = []
        find(state["workers"], "w-0005")["observed_state"] = "running"
        page = self.page(state)
        self.assertNotIn("data-pending=", page.split("<main>", 1)[1])
        self.assertIn("All clear", page)

    def test_task_card_shows_progress_reports_and_cancel(self):
        state = fixture_state()
        find(state["tasks"], "T-0003")["cancel"] = {"at": "2026-10-17T11:00:00+09:00", "reason": "今回は不要"}
        find(state["workers"], "w-0004")["intended_state"] = "archived"
        tasks = self.page(state).split('id="tasks"', 1)[1].split('id="archived"', 1)[0]
        self.assertIn("<b>0</b>/2 done", tasks)
        self.assertIn('<span class="seg" data-state="idle"></span><span class="seg" data-state="needs_input"></span>', tasks)
        self.assertIn('data-report="unparsed"><span class="wk__rs">unparsed</span> no supervisor-report block found', tasks)
        self.assertIn('data-report="none">No report yet', tasks)
        self.assertIn('<p class="task__cancel"><span class="chip" data-state="cancelled">cancelled</span> 今回は不要</p>', tasks)

    def test_externally_archived_worker_segment_is_not_empty(self):
        state = fixture_state()
        find(state["workers"], "w-0004")["observed_state"] = "archived"
        self.assertIn('<span class="seg" data-state="external"></span>', self.page(state))

    def test_rev_and_updated_at_only_in_footer(self):
        body, footer = st.render(fixture_state(), TEMPLATE)
        self.assertNotIn("2026-10-17T10:42:00", body)
        self.assertNotIn("rev 12", body)
        self.assertIn('data-rev="12">rev 12', footer)

    def test_actions_section_lists_externally_archived_worker(self):
        state = fixture_state()
        find(state["workers"], "w-0004")["observed_state"] = "archived"
        page = self.page(state)
        actions = page[page.index('id="actions"'):page.index('id="tasks"')]
        self.assertIn("archived outside the supervisor", actions)

    def test_actions_section_lists_missing_and_needs_input(self):
        state = fixture_state()
        find(state["workers"], "w-0004")["observed_state"] = "missing"
        page = self.page(state)
        actions = page[page.index('id="actions"'):page.index('id="tasks"')]
        self.assertIn("w-0004", actions)
        self.assertIn("w-0005", actions)
        self.assertIn("needs_input", actions)
        self.assertIn("UA-0002", actions)

    def test_escapes_script_and_blocks_javascript_links(self):
        state = fixture_state()
        find(state["tasks"], "T-0003")["title"] = "<script>alert(1)</script>{{TASKS}}"
        find(state["tasks"], "T-0003")["artifacts"].append({"kind": "branch", "label": "x", "ref": "r", "url": "javascript:alert(1)"})
        page = self.page(state)
        self.assertEqual(page.count("<script>"), TEMPLATE.count("<script>"))
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn('href="javascript:', page)
        self.assertNotIn("{{TASKS}}", page)

    def test_file_artifact_is_not_a_link(self):
        state = fixture_state()
        find(state["tasks"], "T-0004")["artifacts"] = [{"kind": "file", "path": "/Users/handlename/notes/a.txt"}]
        page = self.page(state)
        self.assertIn("<code>/Users/handlename/notes/a.txt</code>", page)
        self.assertNotIn('href="/Users', page)

    def test_task_and_archived_ids_are_click_to_copy_controls(self):
        state = fixture_state()
        find(state["tasks"], "T-0004")["id"] = "T-0004"
        state["archived"] = [{"id": "T-0900", "title": "old", "artifacts": [], "archived_at": "2026-01-01T00:00:00Z"}]
        page = self.page(state)
        tasks = page[page.index('id="tasks"'):page.index('id="archived"')]
        archived = page[page.index('id="archived"'):]
        ctl = '<button type="button" class="{cls} copyable" data-copy aria-label="Copy {id}" title="クリックでコピー">{id}</button>'
        for t in state["tasks"]:
            self.assertIn(ctl.format(cls="id task__id", id=t["id"]), tasks)
        self.assertEqual(tasks.count('class="id task__id copyable"'), len(state["tasks"]))
        self.assertIn(ctl.format(cls="id", id="T-0900"), archived)
        self.assertNotIn(">Copy</button>", page)
        self.assertIn("navigator.clipboard.writeText", page)

    def test_copy_control_text_is_html_escaped(self):
        self.assertEqual(st._copy_id("id", 'T-<1>"'),
                         '<button type="button" class="id copyable" data-copy aria-label="Copy T-&lt;1&gt;&quot;" '
                         'title="クリックでコピー">T-&lt;1&gt;&quot;</button>')

    def test_external_resources_are_google_fonts_only(self):
        page = self.page(fixture_state())
        self.assertIsNone(re.search(r"<script\b[^>]*\bsrc=", page))
        for href in re.findall(r'<link\b[^>]*\bhref="([^"]+)"', page):
            self.assertRegex(href, r"^https://fonts\.(googleapis|gstatic)\.com")
        self.assertIn("prefers-color-scheme: dark", page)
        self.assertIn(':root[data-theme="dark"]', page)

    def test_only_pending_hero_animates_forever(self):
        for selector in re.findall(r"([^{}]+)\{[^}]*\binfinite\b", TEMPLATE):
            self.assertIn(".hero[data-pending]", selector)
        self.assertIn("@media (prefers-reduced-motion: reduce)", TEMPLATE)

    def test_copyable_hover_changes_no_box_property(self):
        rules = re.findall(r"([^{}]*\.copyable[^{}]*:hover[^{}]*)\{([^}]*)\}", TEMPLATE)
        self.assertTrue(rules)
        for selector, body in rules:
            self.assertNotRegex(body, r"\b(padding|margin|border(?!-radius)|font|letter-spacing|width)[a-z-]*\s*:", selector)

    def test_copyable_rest_background_has_matching_hover_fill(self):
        for selector, body in re.findall(r"([^{}]*\.copyable[^{}:]*)\{([^}]*)\}", TEMPLATE):
            if re.search(r"\bbackground\s*:", body):
                self.assertRegex(TEMPLATE, re.escape(selector.strip()) + r":hover\s*\{[^}]*background:\s*var\(--hover-bg\)")


class DigestTest(unittest.TestCase):
    def test_contents(self):
        text = st.digest(fixture_state(), "/Users/handlename/supervisor/state.json")
        self.assertLessEqual(len(text), 10000)
        for needle in ("/Users/handlename/supervisor/state.json", "/Users/handlename/supervisor/dashboard.html",
                       "sess_sup_01", "sess_sup_02", "periodic reconciliation", "UA-0002", "T-0004", "sess_abc123 idle"):
            self.assertIn(needle, text)

    def test_limit(self):
        state = fixture_state()
        state["tasks"][0]["next_action"] = "x" * 20000
        text = st.digest(state, "/s/state.json")
        self.assertLessEqual(len(text), 10000)
        self.assertIn("/s/state.json", text)
        self.assertIn("truncated", text)

    def test_broken_or_missing_state_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "state.json"
            broken.write_text("{not json", encoding="utf-8")
            for path in (broken, Path(tmp) / "missing.json"):
                proc = subprocess.run([sys.executable, str(SCRIPT), "digest", "--state", str(path)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(len(proc.stdout.strip().splitlines()), 1)


class ParseReportTest(unittest.TestCase):
    def setUp(self):
        self.events = (FIXTURES / "events_with_reports.txt").read_text(encoding="utf-8")

    def test_last_block_wins(self):
        result = st.parse_report(self.events, "T-0003", "w-0004")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["report"]["status"], "done")
        self.assertEqual(result["report"]["artifacts"]["pr"], "https://github.com/org/repo-a/pull/42")

    def test_no_block(self):
        self.assertFalse(st.parse_report("just text")["ok"])

    def test_invalid_json(self):
        result = st.parse_report("```supervisor-report\n{bad}\n```")
        self.assertFalse(result["ok"])
        self.assertIn("invalid JSON", result["error"])

    def test_missing_fields(self):
        result = st.parse_report('```supervisor-report\n{"task_id": "T-1", "status": "done"}\n```')
        self.assertIn("missing fields", result["error"])

    def test_id_mismatch(self):
        self.assertIn("task_id", st.parse_report(self.events, expect_task="T-9999")["error"])
        self.assertIn("worker_id", st.parse_report(self.events, expect_worker="w-9999")["error"])

    def test_blocked_requires_blockers(self):
        report = {"task_id": "T-1", "worker_id": "w-1", "status": "blocked", "summary": "", "artifacts": {}, "blockers": [], "next_action": ""}
        result = st.parse_report("```supervisor-report\n" + json.dumps(report) + "\n```")
        self.assertIn("blocker", result["error"])

    def test_unhashable_status(self):
        report = {"task_id": "T-1", "worker_id": "w-1", "status": ["done"], "summary": "", "artifacts": {}, "blockers": [], "next_action": ""}
        self.assertFalse(st.parse_report("```supervisor-report\n" + json.dumps(report) + "\n```")["ok"])

    def test_field_types_checked(self):
        base = {"task_id": "T-1", "worker_id": "w-1", "status": "done", "summary": "s", "next_action": "n",
                "artifacts": {"branch": "", "commit": "", "pr": "", "ci": "", "files": []}, "blockers": []}
        cases = [
            ({"summary": None}, "summary"),
            ({"next_action": 3}, "next_action"),
            ({"task_id": 1}, "task_id"),
            ({"status": "finished"}, "invalid status"),
            ({"blockers": [1]}, "blockers"),
            ({"blockers": "x"}, "blockers"),
            ({"artifacts": []}, "artifacts must be an object"),
            ({"artifacts": {"pr": None}}, "artifacts.pr"),
            ({"artifacts": {"files": ["a", 2]}}, "artifacts.files"),
            ({"artifacts": {"files": "a"}}, "artifacts.files"),
        ]
        for change, needle in cases:
            report = dict(base, **change)
            result = st.parse_report("```supervisor-report\n" + json.dumps(report) + "\n```")
            self.assertFalse(result["ok"], change)
            self.assertIn(needle, result["error"])
        self.assertTrue(st.parse_report("```supervisor-report\n" + json.dumps(base) + "\n```")["ok"])

    def test_cli_exit_codes(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), "parse-report", "--file", str(FIXTURES / "events_with_reports.txt"),
                               "--expect-task", "T-0003", "--expect-worker", "w-0004"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(json.loads(proc.stdout)["ok"])
        proc = subprocess.run([sys.executable, str(SCRIPT), "parse-report", "--file", "-"], input="nothing", capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(json.loads(proc.stdout)["ok"])


class CheckCwdTest(unittest.TestCase):
    def test_busy_and_free(self):
        state = fixture_state()
        nongit_task(state, "T-0010", "w-0010", "/Users/handlename/notes", "running", "running")
        self.assertTrue(st.cwd_busy(state, "/Users/handlename/notes/"))
        self.assertFalse(st.cwd_busy(state, "/Users/handlename/other"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(st.serialize(state), encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPT), "check-cwd", "--state", str(path), "--cwd", "/Users/handlename/notes"], capture_output=True, text=True)
            self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "busy"))

    def test_nested_cwds_are_busy(self):
        state = fixture_state()
        nongit_task(state, "T-0010", "w-0010", "/Users/handlename/notes", "running", "running")
        self.assertTrue(st.cwd_busy(state, "/Users/handlename/notes/sub"))
        self.assertTrue(st.cwd_busy(state, "/Users/handlename"))
        self.assertFalse(st.cwd_busy(state, "/Users/handlename/notesx"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(st.serialize(state), encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPT), "check-cwd", "--state", str(path), "--cwd", "/Users/handlename/notes/sub"], capture_output=True, text=True)
            self.assertEqual(proc.stdout.strip(), "busy")


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def setup_dir(self, target):
        return subprocess.run([sys.executable, str(SCRIPT), "setup", "--dir", str(target)], capture_output=True, text=True)

    def files(self, target):
        return sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())

    def hooks(self, target, event="SessionStart"):
        return json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"][event]

    def test_creates_five_files_with_matcher(self):
        target = self.root / "sup"
        proc = self.setup_dir(target)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.files(target), [".claude/settings.json", ".supervisor/dashboard.template.html",
                                              ".supervisor/state_tool.py", "CLAUDE.md", "state.json"])
        entries = self.hooks(target)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["matcher"], "startup|resume|clear|compact")
        state = json.loads((target / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(st.validate(state), [])
        self.assertEqual(state["rev"], 0)

    def test_idempotent(self):
        target = self.root / "sup"
        self.setup_dir(target)
        proc = self.setup_dir(target)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(self.hooks(target)), 1)
        self.assertEqual(len(self.hooks(target, "PreToolUse")), 1)
        self.assertTrue(all(line.startswith("exists:") for line in proc.stdout.splitlines()), proc.stdout)

    def test_installs_guard_hook(self):
        target = self.root / "sup"
        self.assertEqual(self.setup_dir(target).returncode, 0)
        entries = self.hooks(target, "PreToolUse")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["matcher"], "Edit|MultiEdit|Write|NotebookEdit|Bash")
        self.assertEqual(entries[0]["hooks"][0]["command"],
                         f"python3 {target / '.supervisor' / 'state_tool.py'} guard --dir {target}; [ $? -eq 3 ] && exit 2; exit 0")
        self.assertIn("state_tool.py guard", (target / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_adds_guard_to_existing_setup(self):
        target = self.root / "sup"
        self.setup_dir(target)
        settings_path = target / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        del settings["hooks"]["PreToolUse"]
        settings_path.write_text(json.dumps(settings), encoding="utf-8")
        proc = self.setup_dir(target)
        self.assertIn(f"updated: {settings_path}", proc.stdout)
        self.assertEqual(len(self.hooks(target)), 1)
        self.assertEqual(len(self.hooks(target, "PreToolUse")), 1)

    def test_preserves_existing_pre_tool_use(self):
        target = self.root / "sup"
        (target / ".claude").mkdir(parents=True)
        user_entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
        (target / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [user_entry]}}), encoding="utf-8")
        self.assertEqual(self.setup_dir(target).returncode, 0)
        entries = self.hooks(target, "PreToolUse")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0], user_entry)

    def test_guard_entry_predicate_matches_quoted_and_unquoted(self):
        for name in ("sup", "my sup"):
            target = self.root / name
            self.assertEqual(self.setup_dir(target).returncode, 0)
            commands = [h["command"] for e in self.hooks(target, "PreToolUse") for h in e["hooks"]]
            self.assertTrue(any("state_tool.py" in c and " guard --dir" in c for c in commands), commands)

    def test_preserves_existing_keys(self):
        target = self.root / "sup"
        (target / ".claude").mkdir(parents=True)
        (target / ".claude" / "settings.json").write_text(json.dumps({
            "permissions": {"allow": ["Bash(ls)"]},
            "hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo hi"}]}]},
        }), encoding="utf-8")
        self.assertEqual(self.setup_dir(target).returncode, 0)
        settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(len(settings["hooks"]["SessionStart"]), 2)

    def test_invalid_settings_json_aborts(self):
        target = self.root / "sup"
        (target / ".claude").mkdir(parents=True)
        (target / ".claude" / "settings.json").write_text("{oops", encoding="utf-8")
        proc = self.setup_dir(target)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not valid JSON", proc.stderr)
        self.assertFalse((target / "state.json").exists())

    def test_malformed_hooks_abort(self):
        for settings in ({"hooks": []}, {"hooks": {"SessionStart": {}}}, {"hooks": {"SessionStart": ["x"]}},
                         {"hooks": {"PreToolUse": {}}}, {"hooks": {"PreToolUse": ["x"]}}):
            target = self.root / "sup"
            (target / ".claude").mkdir(parents=True, exist_ok=True)
            (target / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
            proc = self.setup_dir(target)
            self.assertEqual(proc.returncode, 1)
            self.assertTrue(proc.stderr.startswith("error:"), proc.stderr)
            self.assertFalse((target / "state.json").exists())

    def test_inside_git_aborts(self):
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True)
        proc = self.setup_dir(repo / "sup")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("git repository", proc.stderr)
        self.assertFalse((repo / "sup").exists())

    def test_quotes_paths_with_spaces(self):
        target = self.root / "my sup"
        self.assertEqual(self.setup_dir(target).returncode, 0)
        command = self.hooks(target)[0]["hooks"][0]["command"]
        self.assertIn(f"'{target / '.supervisor' / 'state_tool.py'}'", command)
        self.assertIn(f"'{target / 'state.json'}'", command)
        proc = subprocess.run(command, shell=True, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("rev 0", proc.stdout)
        guard = self.hooks(target, "PreToolUse")[0]["hooks"][0]["command"]
        self.assertIn(f"'{target / '.supervisor' / 'state_tool.py'}' guard --dir '{target}'", guard)
        payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/nonexistent-sv-guard/x"}, "cwd": str(target)})
        proc = subprocess.run(guard, shell=True, input=payload, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("outside the supervisor directory", proc.stderr)

    def test_new_claude_md_has_default_locations(self):
        target = self.root / "sup"
        self.assertEqual(self.setup_dir(target).returncode, 0)
        text = (target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(st.parse_locations(text), ({"clone_root": "~/src", "work_root": str(self.root / "sup-work")}, []))
        self.assertIn("prepare-dir` is the only command that writes outside this directory", text)
        self.assertIn("The one allowed exception is `state_tool.py prepare-dir`", text)

    def test_appends_locations_to_existing_claude_md(self):
        target = self.root / "sup"
        target.mkdir()
        body = "# My supervisor\n\nKeep this text.\n"
        (target / "CLAUDE.md").write_text(body, encoding="utf-8")
        proc = self.setup_dir(target)
        self.assertIn(f"updated: {target / 'CLAUDE.md'}", proc.stdout)
        text = (target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith(body + "\n## Locations\n"), text)
        self.assertEqual(st.parse_locations(text)[0], {"clone_root": "~/src", "work_root": str(self.root / "sup-work")})

    def test_keeps_custom_values_and_adds_only_missing_keys(self):
        target = self.root / "sup"
        target.mkdir()
        body = "# Mine\n\n## Locations\n\n- clone_root: /code\n\n## Follow-ups\n"
        (target / "CLAUDE.md").write_text(body, encoding="utf-8")
        self.assertEqual(self.setup_dir(target).returncode, 0)
        text = (target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(text, f"# Mine\n\n## Locations\n\n- clone_root: /code\n- work_root: {self.root / 'sup-work'}\n\n## Follow-ups\n")
        (target / "CLAUDE.md").write_text("## Locations\n- work_root: ~/work\n- clone_root: ~/code\n", encoding="utf-8")
        proc = self.setup_dir(target)
        self.assertIn(f"exists: {target / 'CLAUDE.md'}", proc.stdout)
        self.assertEqual((target / "CLAUDE.md").read_text(encoding="utf-8"), "## Locations\n- work_root: ~/work\n- clone_root: ~/code\n")

    def test_second_run_keeps_claude_md_bytes(self):
        target = self.root / "sup"
        target.mkdir()
        (target / "CLAUDE.md").write_text("# Mine\n", encoding="utf-8")
        self.setup_dir(target)
        before = (target / "CLAUDE.md").read_bytes()
        proc = self.setup_dir(target)
        self.assertIn(f"exists: {target / 'CLAUDE.md'}", proc.stdout)
        self.assertEqual((target / "CLAUDE.md").read_bytes(), before)

    def test_invalid_locations_abort(self):
        target = self.root / "sup"
        target.mkdir()
        (target / "CLAUDE.md").write_text("## Locations\n- clone_root: src\n", encoding="utf-8")
        proc = self.setup_dir(target)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("error: CLAUDE.md:2: clone_root must be an absolute path or start with ~", proc.stderr)
        self.assertFalse((target / "state.json").exists())

    def test_version_matches_copy(self):
        target = self.root / "sup"
        self.setup_dir(target)
        bundled = subprocess.run([sys.executable, str(SCRIPT), "version"], capture_output=True, text=True).stdout
        copied = subprocess.run([sys.executable, str(target / ".supervisor" / "state_tool.py"), "version"], capture_output=True, text=True).stdout
        self.assertRegex(bundled.strip(), r"^[0-9a-f]{12}$")
        self.assertEqual(bundled, copied)


class LocationsTest(unittest.TestCase):
    def test_parse(self):
        text = "# T\n\n## Locations\n\nprose line\n- clone_root: ~/code\n- work_root: /w <!-- x -->\n\n## Other\n- clone_root: x\n"
        self.assertEqual(st.parse_locations(text), ({"clone_root": "~/code", "work_root": "/w"}, []))
        self.assertEqual(st.parse_locations("# T\n"), ({}, []))
        self.assertEqual(st.parse_locations("```\n## Locations\n- clone_root: x\n```\n"), ({}, []))

    def test_errors_carry_line_numbers(self):
        cases = [
            ("## Locations\n- clone_root: src\n", "CLAUDE.md:2: clone_root must be an absolute path or start with ~"),
            ("## Locations\n- work_root: ~user/w\n", "CLAUDE.md:2: work_root must be an absolute path or start with ~"),
            ("## Locations\n- clone_root: /a\n- clone_root: /b\n", "CLAUDE.md:3: duplicate key 'clone_root'"),
            ("## Locations\n- cache_root: /a\n", "CLAUDE.md:2: unknown key 'cache_root'"),
            ("## Locations\n## Locations\n", "CLAUDE.md:2: duplicate '## Locations' heading"),
        ]
        for text, expected in cases:
            with self.subTest(expected=expected):
                values, errors = st.parse_locations(text)
                self.assertEqual(values, {})
                self.assertTrue(any(e.startswith(expected) for e in errors), errors)

    def test_defaults(self):
        self.assertEqual(st.default_locations("/Users/me/supervisor/"),
                         {"clone_root": "~/src", "work_root": "/Users/me/supervisor-work"})

    def test_add_missing_locations_is_idempotent(self):
        once = st.add_missing_locations(st.CLAUDE_MD, "/x/sup")
        self.assertEqual(st.add_missing_locations(once, "/x/sup"), once)
        self.assertTrue(once.startswith(st.CLAUDE_MD))

    def test_clone_url_parts(self):
        ok = {
            "https://github.com/org/repo": ("github.com", "org", "repo"),
            "https://github.com/org/repo.git": ("github.com", "org", "repo"),
            "https://github.com/org/repo/": ("github.com", "org", "repo"),
            "git@github.com:org/repo.git": ("github.com", "org", "repo"),
            "git@ghe.example.com:team/my.repo": ("ghe.example.com", "team", "my.repo"),
        }
        for url, parts in ok.items():
            self.assertEqual(st.clone_url_parts(url), parts, url)
        for url in ("file:///tmp/repo", "http://github.com/o/r", "ssh://git@github.com/o/r", "https://github.com/../r",
                    "https://github.com/o/..", "https://github.com/o/r/extra", "https://u@github.com/o/r", "git@github.com:o/-r",
                    "https://github.com/o/.git", "-u https://github.com/o/r", "https://github.com/o/r?x=1"):
            self.assertIsNone(st.clone_url_parts(url), url)


def fake_git(responses, clone=None):
    """subprocess.run stand-in: responses maps an argv prefix tuple (after "git") to (returncode, stdout)."""
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[:2] == ["git", "clone"]:
            if clone is not None:
                return clone(argv, **kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")
        for prefix, (code, out) in responses.items():
            if tuple(a for a in argv[1:] if a != "-C" and not a.startswith("/"))[:len(prefix)] == prefix:
                return subprocess.CompletedProcess(argv, code, out, "")
        return subprocess.CompletedProcess(argv, 1, "", "")
    return run, calls


class PrepareDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.sup = self.root / "sup"
        self.sup.mkdir()
        self.md = self.sup / "CLAUDE.md"
        self.clones = self.root / "clones"
        self.work = self.root / "work"
        self.write_locations(f"- clone_root: {self.clones}\n- work_root: {self.work}\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write_locations(self, body):
        self.md.write_text("# Supervisor\n\n## Locations\n\n" + body, encoding="utf-8")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = st.main(["prepare-dir", "--claude-md", str(self.md), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_show_expands_and_marks_defaults(self):
        self.assertEqual(self.run_cli("--show"), (0, f"clone_root: {self.clones}\nwork_root: {self.work}\n", ""))
        home = self.root / "home"
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.write_locations("- clone_root: ~/code\n")
            self.assertEqual(self.run_cli("--show")[1], f"clone_root: {home / 'code'}\nwork_root: {self.root / 'sup-work'} (default)\n")
            self.md.write_text("# Supervisor\n", encoding="utf-8")
            self.assertEqual(self.run_cli("--show")[1], f"clone_root: {home / 'src'} (default)\nwork_root: {self.root / 'sup-work'} (default)\n")
            self.md.unlink()
            self.assertEqual(self.run_cli("--show")[0], 0)

    def test_work_creates_directory(self):
        code, out, _ = self.run_cli("--work", "T-0012-notes-tidy")
        self.assertEqual((code, out), (0, f"created: {self.work / 'T-0012-notes-tidy'}\n"))
        self.assertTrue((self.work / "T-0012-notes-tidy").is_dir())
        self.assertEqual(self.run_cli("--work", "T-0012-notes-tidy")[1], f"exists: {self.work / 'T-0012-notes-tidy'}\n")

    def test_work_rejects_bad_name(self):
        for name in ("notes", "T-12-x", "T-0012-Notes", "T-0012-../x", "T-0012-" + "a" * 41, "../T-0012-x"):
            with self.subTest(name=name):
                code, _, err = self.run_cli("--work", name)
                self.assertEqual(code, 1)
                self.assertIn("must match T-<number>-<slug>", err)
        self.assertFalse(self.work.exists())

    def test_rejects_roots_inside_supervisor_git_or_each_other(self):
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True)
        cases = [
            (f"- clone_root: {self.clones}\n- work_root: {self.sup / 'work'}\n", "work_root .* is inside the supervisor directory"),
            (f"- clone_root: {self.sup}\n- work_root: {self.work}\n", "clone_root .* is inside the supervisor directory"),
            (f"- clone_root: {self.clones}\n- work_root: {repo / 'work'}\n", "work_root .* is inside a git repository"),
            (f"- clone_root: {self.clones}\n- work_root: {self.clones / 'work'}\n", "are nested"),
            (f"- clone_root: {self.work / 'src'}\n- work_root: {self.work}\n", "are nested"),
        ]
        for body, pattern in cases:
            with self.subTest(pattern=pattern):
                self.write_locations(body)
                for argv in (("--show",), ("--work", "T-0001-x"), ("--clone", "https://github.com/o/r")):
                    code, _, err = self.run_cli(*argv)
                    self.assertEqual(code, 1)
                    self.assertRegex(err, pattern)
        self.assertFalse((self.sup / "work").exists())
        self.assertFalse((repo / "work").exists())

    def test_work_rejects_symlink_out_of_root(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.work.mkdir()
        (self.work / "T-0001-x").symlink_to(outside, target_is_directory=True)
        code, _, err = self.run_cli("--work", "T-0001-x")
        self.assertEqual(code, 1)
        self.assertIn("resolves outside", err)

    def test_clone_paths(self):
        for url, dest in (("https://github.com/org/repo-a", "github.com/org/repo-a"),
                          ("git@github.com:org/repo-b.git", "github.com/org/repo-b")):
            with self.subTest(url=url):
                run, calls = fake_git({})
                with mock.patch.object(st.subprocess, "run", run):
                    code, out, err = self.run_cli("--clone", url)
                self.assertEqual((code, out), (0, f"cloned: {self.clones / dest}\n"), err)
                clone = [c for c in calls if c[0][:2] == ["git", "clone"]]
                self.assertEqual(clone[0][0], ["git", "clone", "--", url, str(self.clones / dest)])

    def test_clone_rejects_unsafe_urls(self):
        run, calls = fake_git({})
        for url in ("file:///etc", "https://github.com/../x", "https://github.com/o/..", "ext::sh -c touch% /tmp/x", "--upload-pack=x"):
            with self.subTest(url=url), mock.patch.object(st.subprocess, "run", run):
                code, _, err = self.run_cli(f"--clone={url}")
                self.assertEqual(code, 1)
                self.assertIn("must be https://<host>/<owner>/<repo>", err)
        self.assertEqual(calls, [])

    def test_clone_rejects_symlinked_destination(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.clones / "github.com").mkdir(parents=True)
        (self.clones / "github.com" / "org").symlink_to(outside, target_is_directory=True)
        run, calls = fake_git({})
        with mock.patch.object(st.subprocess, "run", run):
            code, _, err = self.run_cli("--clone", "https://github.com/org/repo")
        self.assertEqual(code, 1)
        self.assertIn("resolves outside", err)
        self.assertEqual(calls, [])

    def existing_repo(self):
        dest = self.clones / "github.com" / "org" / "repo"
        (dest / ".git").mkdir(parents=True)
        return dest

    def test_existing_clone_with_matching_origin_and_head(self):
        dest = self.existing_repo()
        run, calls = fake_git({("remote", "get-url", "origin"): (0, "git@github.com:Org/repo.git\n"),
                               ("rev-parse", "--verify", "HEAD"): (0, "abc\n")})
        with mock.patch.object(st.subprocess, "run", run):
            self.assertEqual(self.run_cli("--clone", "https://github.com/org/repo")[:2], (0, f"exists: {dest}\n"))
        self.assertFalse(any(c[0][:2] == ["git", "clone"] for c in calls))

    def test_existing_clone_with_other_origin_or_no_head(self):
        self.existing_repo()
        cases = [
            ({("remote", "get-url", "origin"): (0, "https://github.com/fork/repo\n"), ("rev-parse", "--verify", "HEAD"): (0, "abc")},
             "already exists with origin 'https://github.com/fork/repo'"),
            ({("rev-parse", "--verify", "HEAD"): (0, "abc")}, "already exists with origin None"),
            ({("remote", "get-url", "origin"): (0, "https://github.com/org/repo")}, "has no HEAD"),
        ]
        for responses, pattern in cases:
            with self.subTest(pattern=pattern):
                run, calls = fake_git(responses)
                with mock.patch.object(st.subprocess, "run", run):
                    code, _, err = self.run_cli("--clone", "https://github.com/org/repo")
                self.assertEqual(code, 1)
                self.assertIn(pattern, err)
                self.assertFalse(any(c[0][:2] == ["git", "clone"] for c in calls))
        self.assertTrue((self.clones / "github.com" / "org" / "repo" / ".git").is_dir())

    def test_non_empty_non_repo_destination_rejected(self):
        dest = self.clones / "github.com" / "org" / "repo"
        dest.mkdir(parents=True)
        (dest / "file.txt").write_text("x", encoding="utf-8")
        run, calls = fake_git({})
        with mock.patch.object(st.subprocess, "run", run):
            code, _, err = self.run_cli("--clone", "https://github.com/org/repo")
        self.assertEqual(code, 1)
        self.assertIn("is neither an empty directory nor a clone", err)
        self.assertTrue((dest / "file.txt").exists())

    def failing_clone(self, exc=None):
        def clone(argv, **kwargs):
            os.makedirs(os.path.join(argv[-1], ".git"))
            if exc:
                raise exc
            return subprocess.CompletedProcess(argv, 128, "", "fatal: could not read Username")
        return clone

    def test_failed_or_timed_out_clone_removes_destination(self):
        dest = self.clones / "github.com" / "org" / "repo"
        for exc, pattern in ((None, "git clone exited with 128: fatal: could not read Username"),
                             (subprocess.TimeoutExpired(["git"], 300), "git clone timed out after 300 seconds")):
            with self.subTest(pattern=pattern):
                run, _ = fake_git({}, clone=self.failing_clone(exc))
                with mock.patch.object(st.subprocess, "run", run):
                    code, out, err = self.run_cli("--clone", "https://github.com/org/repo")
                self.assertEqual((code, out), (1, ""))
                self.assertIn(f"error: {pattern}", err)
                self.assertFalse(dest.exists())

    def test_failed_clone_keeps_preexisting_empty_destination(self):
        dest = self.clones / "github.com" / "org" / "repo"
        dest.mkdir(parents=True)
        run, _ = fake_git({}, clone=lambda argv, **kw: subprocess.CompletedProcess(argv, 128, "", "fatal"))
        with mock.patch.object(st.subprocess, "run", run):
            self.assertEqual(self.run_cli("--clone", "https://github.com/org/repo")[0], 1)
        self.assertTrue(dest.is_dir())

    def clone_kwargs(self, responses, environ):
        run, calls = fake_git(responses)
        with mock.patch.object(st.subprocess, "run", run), mock.patch.dict(os.environ, environ, clear=False):
            if "GIT_SSH_COMMAND" not in environ:
                os.environ.pop("GIT_SSH_COMMAND", None)
            self.assertEqual(self.run_cli("--clone", "git@github.com:org/repo.git")[0], 0)
        return next(kw for argv, kw in calls if argv[:2] == ["git", "clone"])

    def test_clone_is_non_interactive_and_keeps_ssh_settings(self):
        kwargs = self.clone_kwargs({}, {})
        self.assertEqual(kwargs["timeout"], 300)
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(kwargs["env"]["GIT_SSH_COMMAND"], "ssh -o BatchMode=yes")
        kwargs = self.clone_kwargs({("config", "--get", "core.sshCommand"): (0, "ssh -i ~/.ssh/work\n")}, {})
        self.assertEqual(kwargs["env"]["GIT_SSH_COMMAND"], "ssh -i ~/.ssh/work -o BatchMode=yes")
        kwargs = self.clone_kwargs({("config", "--get", "core.sshCommand"): (0, "ssh -i ~/.ssh/work\n")},
                                   {"GIT_SSH_COMMAND": "ssh -F /etc/sv"})
        self.assertEqual(kwargs["env"]["GIT_SSH_COMMAND"], "ssh -F /etc/sv -o BatchMode=yes")


RULES_MD = """# Session supervisor directory

## Follow-ups

### review-pr
- on: pr
- text: Review {label}
- done: pr_closed

### check-deploy
- on: pr
- text: Check the deploy of {url}
- done: manual
"""

PR42 = "https://github.com/org/repo-a/pull/42"
PR43 = "https://github.com/org/repo-a/pull/43"


def follow_up_ua(ua_id, url, rule="review-pr", done="pr_closed", description="Review it"):
    return {"id": ua_id, "description": description, "since": NOW, "follow_up": {"rule": rule, "url": url, "done": done}}


def pr_task(state, task_id="T-0040", worker_id="w-0040", intended="archived", observed="archived"):
    git_task(state, task_id, worker_id, delivery="push_pr", intended=intended, observed=observed)
    task = find(state["tasks"], task_id)
    task["request"] = "LICENSE の年を直して。push して PR を作ってよい"
    task["push_consent"] = {"text": "push して PR を作ってよい", "at": NOW}
    commit_artifacts(state, task_id)
    task["artifacts"].append({"kind": "pr", "url": PR43})
    return task


class ParseFollowUpsTest(unittest.TestCase):
    def test_valid(self):
        rules, errors = st.parse_follow_ups(RULES_MD)
        self.assertEqual(errors, [])
        self.assertEqual(rules, [
            {"id": "review-pr", "on": "pr", "text": "Review {label}", "done": "pr_closed"},
            {"id": "check-deploy", "on": "pr", "text": "Check the deploy of {url}", "done": "manual"},
        ])

    def test_no_section(self):
        self.assertEqual(st.parse_follow_ups("# Title\n\n## Sources\n"), ([], []))

    def test_setup_claude_md_has_no_rules(self):
        self.assertEqual(st.parse_follow_ups(st.add_missing_locations(st.CLAUDE_MD, "/x/sup")), ([], []))

    def test_fenced_block_and_comment_are_ignored(self):
        text = ("# T\n\n```markdown\n## Follow-ups\n### x\n- on: ci\n```\n"
                "<!--\n## Follow-ups\n-->\n## Follow-ups\n\n### a\n- on: pr <!-- inline -->\n- text: Review {label}\n- done: manual\n")
        rules, errors = st.parse_follow_ups(text)
        self.assertEqual(errors, [])
        self.assertEqual([r["id"] for r in rules], ["a"])

    def test_section_ends_at_next_heading(self):
        text = "## Follow-ups\n\n### a\n- on: pr\n- text: t\n- done: manual\n\n## Other\n\nfree text\n"
        self.assertEqual(len(st.parse_follow_ups(text)[0]), 1)

    def test_errors_carry_line_numbers(self):
        cases = [
            ("## Follow-ups\n## Follow-ups\n", "CLAUDE.md:2: duplicate '## Follow-ups' heading"),
            ("## Follow-ups\n- on: pr\n", "CLAUDE.md:2: the first entry under '## Follow-ups' must be a '### <rule-id>' heading"),
            ("## Follow-ups\n### Bad_Id\n- on: pr\n- text: t\n- done: manual\n", "CLAUDE.md:2: invalid rule id 'Bad_Id'"),
            ("## Follow-ups\n### a\n- on: pr\n- text: t\n- done: manual\n### a\n- on: pr\n- text: t\n- done: manual\n", "CLAUDE.md:6: duplicate rule id 'a'"),
            ("## Follow-ups\n### a\n- on: ci\n- text: t\n- done: manual\n", "CLAUDE.md:3: on must be one of ['pr']"),
            ("## Follow-ups\n### a\n- on: pr\n- text: t\n- done: later\n", "CLAUDE.md:5: done must be one of ['pr_closed', 'manual']"),
            ("## Follow-ups\n### a\n- on: pr\n- text:\n- done: manual\n", "CLAUDE.md:4: text must not be empty"),
            ("## Follow-ups\n### a\n- on: pr\n- text: " + "x" * 201 + "\n- done: manual\n", "CLAUDE.md:4: text must be at most 200 characters"),
            ("## Follow-ups\n### a\n- on: pr\n- text: Review {title}\n- done: manual\n", "CLAUDE.md:4: text may use only {label} and {url}"),
            ("## Follow-ups\n### a\n- on: pr\n- on: pr\n- text: t\n- done: manual\n", "CLAUDE.md:4: duplicate key 'on'"),
            ("## Follow-ups\n### a\n- on: pr\n- when: now\n- text: t\n- done: manual\n", "CLAUDE.md:4: unknown key 'when'"),
            ("## Follow-ups\n### a\n- on: pr\n- text: t\n", "CLAUDE.md:2: rule 'a' is missing 'done'"),
            ("## Follow-ups\n### a\n- on: pr\nfree text\n- text: t\n- done: manual\n", "CLAUDE.md:4: expected '- <key>: <value>'"),
        ]
        for text, expected in cases:
            with self.subTest(expected=expected):
                rules, errors = st.parse_follow_ups(text)
                self.assertEqual(rules, [])
                self.assertTrue(any(e.startswith(expected) for e in errors), errors)

    def test_any_error_drops_every_rule(self):
        text = RULES_MD + "\n### bad\n- on: ci\n"
        rules, errors = st.parse_follow_ups(text)
        self.assertEqual(rules, [])
        self.assertEqual(len(errors), 3, errors)


class FollowUpCoreTest(unittest.TestCase):
    def setUp(self):
        self.state = fixture_state()
        self.rules = st.parse_follow_ups(RULES_MD)[0]

    def test_new_pr_urls_returns_only_new_urls(self):
        cand = copy.deepcopy(self.state)
        find(cand["tasks"], "T-0003")["artifacts"].append({"kind": "pr", "url": PR43})
        pr_task(cand, "T-0040", "w-0040")
        self.assertEqual(st.new_pr_urls(self.state, cand), [("T-0003", PR43), ("T-0040", PR43)])
        self.assertEqual(st.new_pr_urls(cand, cand), [])

    def test_label_from_artifact_then_url_then_raw(self):
        self.assertEqual(st.follow_up_label({"label": "repo-a#42"}, PR42), "repo-a#42")
        self.assertEqual(st.follow_up_label({"label": ""}, PR42), "org/repo-a#42")
        self.assertEqual(st.follow_up_label(None, "https://ghe.example.com/team/svc/pull/7/"), "team/svc#7")
        self.assertEqual(st.follow_up_label(None, "https://example.com/review/7"), "https://example.com/review/7")

    def test_actions_substitute_and_number(self):
        find(self.state["tasks"], "T-0003")["artifacts"].append({"kind": "pr", "url": PR43})
        actions = st.follow_up_actions(self.state, "T-0003", PR43, self.rules, NOW)
        self.assertEqual(actions, [
            {"id": "UA-0004", "description": "Review org/repo-a#43", "since": NOW,
             "follow_up": {"rule": "review-pr", "url": PR43, "done": "pr_closed"}},
            {"id": "UA-0005", "description": f"Check the deploy of {PR43}", "since": NOW,
             "follow_up": {"rule": "check-deploy", "url": PR43, "done": "manual"}},
        ])
        self.assertEqual(len(find(self.state["tasks"], "T-0003")["user_actions"]), 2)

    def test_actions_use_artifact_label(self):
        find(self.state["tasks"], "T-0003")["user_actions"] = []
        actions = st.follow_up_actions(self.state, "T-0003", PR42, self.rules[:1], NOW)
        self.assertEqual(actions[0]["description"], "Review repo-a#42")
        self.assertEqual(actions[0]["id"], "UA-0001")

    def test_actions_skip_existing_pair(self):
        actions = st.follow_up_actions(self.state, "T-0003", PR42, self.rules, NOW)
        self.assertEqual([a["follow_up"]["rule"] for a in actions], ["check-deploy"])

    def test_closed_skips_pr_closed_rules(self):
        find(self.state["tasks"], "T-0003")["user_actions"] = []
        actions = st.follow_up_actions(self.state, "T-0003", PR42, self.rules, NOW, closed=True)
        self.assertEqual([a["follow_up"]["done"] for a in actions], ["manual"])

    def test_no_rules_no_actions(self):
        self.assertEqual(st.follow_up_actions(self.state, "T-0003", PR42, [], NOW), [])
        self.assertEqual(st.follow_up_actions(self.state, "T-0003", PR42, self.rules[:1], NOW), [])

    def test_unknown_task_raises(self):
        with self.assertRaises(st.StateError):
            st.follow_up_actions(self.state, "T-9999", PR42, self.rules, NOW)

    def test_validate_rejects_bad_follow_ups(self):
        cases = [
            ({"rule": "Bad Rule", "url": PR42, "done": "manual"}, None, "follow_up.rule must be a rule id"),
            ({"rule": "x", "url": PR42, "done": "later"}, None, "follow_up.done must be one of"),
            ({"rule": "x", "url": "ftp://x/1", "done": "manual"}, None, "follow_up.url must be http"),
            ({"rule": "x", "url": PR43, "done": "manual"}, None, "is not a pr artifact of the task"),
            ({"rule": "review-pr", "url": PR42, "done": "manual"}, None, "duplicates rule 'review-pr'"),
            ({"rule": "x", "url": PR42, "done": "manual"}, "", "requires a non-empty since"),
            ("review-pr", None, "follow_up must be an object or absent"),
        ]
        for follow_up, since, pattern in cases:
            with self.subTest(pattern=pattern):
                state = fixture_state()
                ua = {"id": "UA-0009", "description": "x", "since": NOW if since is None else since, "follow_up": follow_up}
                find(state["tasks"], "T-0003")["user_actions"].append(ua)
                self.assertTrue(any(re.search(pattern, e) for e in st.validate(state)), st.validate(state))

    def test_is_push_decision_excludes_follow_ups(self):
        worker = {"branch": "claude/license-x1"}
        ua = {"id": "UA-1", "description": "Decide whether to push branch claude/license-x1"}
        self.assertTrue(st._is_push_decision(ua, worker))
        self.assertFalse(st._is_push_decision(dict(ua, follow_up={"rule": "r", "url": PR43, "done": "manual"}), worker))


class FollowUpStateTest(unittest.TestCase):
    def setUp(self):
        self.current = fixture_state()

    def test_follow_up_keeps_task_needs_user_then_archives(self):
        task = pr_task(self.current)
        task["user_actions"].append(follow_up_ua("UA-0040", PR43))
        cand = copy.deepcopy(self.current)
        state = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(state["tasks"], "T-0040")["status"], "needs_user")
        self.assertIn("w-0040", [w["id"] for w in state["workers"]])
        cand = copy.deepcopy(state)
        find(cand["tasks"], "T-0040")["user_actions"] = []
        state = st.next_state(state, cand, NOW)
        self.assertNotIn("T-0040", [t["id"] for t in state["tasks"]])
        self.assertEqual(state["archived"][0]["id"], "T-0040")

    def test_follow_up_makes_idle_worker_task_needs_user(self):
        task = pr_task(self.current, intended="running", observed="idle")
        task["user_actions"].append(follow_up_ua("UA-0040", PR43))
        state = st.next_state(self.current, copy.deepcopy(self.current), NOW)
        self.assertEqual(find(state["tasks"], "T-0040")["status"], "needs_user")

    def test_removing_follow_up_does_not_resolve_push_decision(self):
        git_task(self.current, "T-0020", "w-0020", intended="archived", observed="archived")
        commit_artifacts(self.current, "T-0020")
        find(self.current["tasks"], "T-0020")["artifacts"].append({"kind": "pr", "url": PR43})
        find(self.current["tasks"], "T-0020")["user_actions"].append(
            follow_up_ua("UA-0100", PR43, description="Decide whether to push branch claude/license-x1 again"))
        cand = copy.deepcopy(self.current)
        task = find(cand["tasks"], "T-0020")
        task["user_actions"] = []
        task["push_consent"] = {"text": "push して", "at": NOW, "message": "claude/license-x1 を push して"}
        with self.assertRaises(st.StateError) as ctx:
            st.next_state(self.current, cand, NOW)
        self.assertIn("only while resolving a pending push decision", str(ctx.exception))

    def test_replacing_pr_artifact_requires_removing_old_follow_up(self):
        cand = copy.deepcopy(self.current)
        task = find(cand["tasks"], "T-0003")
        task["artifacts"] = [a for a in task["artifacts"] if a["kind"] != "pr"] + [{"kind": "pr", "url": PR43}]
        with self.assertRaises(st.StateError) as ctx:
            st.next_state(self.current, cand, NOW)
        self.assertIn("UA-0003", str(ctx.exception))
        task["user_actions"] = [ua for ua in task["user_actions"] if not ua.get("follow_up")]
        st.next_state(self.current, cand, NOW)

    def test_digest_marks_follow_ups(self):
        text = st.digest(fixture_state(), "/s/state.json")
        self.assertIn(f"- T-0003 UA-0003: Review repo-a#42 [follow-up review-pr pr_closed] {PR42}", text)
        self.assertIn("- T-0003 UA-0002: repo-b の PR をレビュー\n", text)
        state = fixture_state()
        find(state["tasks"], "T-0003")["user_actions"][1]["description"] = f"Review {PR42}"
        self.assertIn(f"Review {PR42} [follow-up review-pr pr_closed]\n", st.digest(state, "/s/state.json"))


class FollowUpsCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve()
        (self.dir / ".supervisor").mkdir()
        self.state = self.dir / "state.json"
        self.cand = self.dir / "state.next.json"
        self.md = self.dir / "CLAUDE.md"
        self.state.write_text(st.serialize(fixture_state()), encoding="utf-8")
        self.md.write_text(RULES_MD, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = st.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def write_candidate(self, mutate=None):
        cand = fixture_state()
        if mutate:
            mutate(cand)
        self.cand.write_text(json.dumps(cand, ensure_ascii=False), encoding="utf-8")

    def candidate(self):
        return json.loads(self.cand.read_text(encoding="utf-8"))

    def diff(self, *extra):
        return self.run_cli("follow-ups", "--state", str(self.state), "--candidate", str(self.cand), *extra)

    def add_pr43(self, cand):
        find(cand["tasks"], "T-0003")["artifacts"].append({"kind": "pr", "url": PR43})

    def drop_user_actions(self, cand):
        find(cand["tasks"], "T-0003")["user_actions"] = []

    def test_validate_follow_ups(self):
        self.assertEqual(self.run_cli("validate-follow-ups", "--claude-md", str(self.md))[:2], (0, "ok: 2 rules\n"))
        self.assertEqual(self.run_cli("validate-follow-ups", "--claude-md", str(self.dir / "none.md"))[:2], (0, "ok: 0 rules\n"))
        self.md.write_text(RULES_MD + "\n### bad\n- on: ci\n", encoding="utf-8")
        code, _, err = self.run_cli("validate-follow-ups", "--claude-md", str(self.md))
        self.assertEqual(code, 1)
        self.assertIn("error: CLAUDE.md:16: on must be one of", err)

    def test_adds_for_new_pr_once_and_commits(self):
        self.write_candidate(self.add_pr43)
        code, out, _ = self.diff()
        self.assertEqual(code, 0)
        added = json.loads(out)
        self.assertEqual([(a["task_id"], a["id"], a["description"]) for a in added],
                         [("T-0003", "UA-0004", "Review org/repo-a#43"), ("T-0003", "UA-0005", f"Check the deploy of {PR43}")])
        self.assertEqual(len(find(self.candidate()["tasks"], "T-0003")["user_actions"]), 4)
        code, out, _ = self.diff()
        self.assertEqual((code, json.loads(out)), (0, []))
        self.assertEqual(len(find(self.candidate()["tasks"], "T-0003")["user_actions"]), 4)
        code, _, err = self.run_cli("commit", "--state", str(self.state), "--candidate", str(self.cand))
        self.assertEqual(code, 0, err)

    def test_existing_pr_artifact_gets_nothing(self):
        self.write_candidate(self.drop_user_actions)
        before = self.cand.read_bytes()
        code, out, _ = self.diff()
        self.assertEqual((code, json.loads(out)), (0, []))
        self.assertEqual(self.cand.read_bytes(), before)

    def test_rule_errors_leave_candidate_unchanged(self):
        self.write_candidate(self.add_pr43)
        before = self.cand.read_bytes()
        self.md.write_text(RULES_MD + "\n### bad\n- on: ci\n", encoding="utf-8")
        code, out, err = self.diff()
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("error: CLAUDE.md:", err)
        self.assertEqual(self.cand.read_bytes(), before)

    def test_no_claude_md_or_section_is_a_no_op(self):
        self.write_candidate(self.add_pr43)
        before = self.cand.read_bytes()
        self.md.unlink()
        self.assertEqual(self.diff()[:2], (0, "[]\n"))
        self.md.write_text("# Session supervisor directory\n", encoding="utf-8")
        self.assertEqual(self.diff()[:2], (0, "[]\n"))
        self.assertEqual(self.cand.read_bytes(), before)

    def test_closed_skips_pr_closed_and_warns_on_old_url(self):
        self.write_candidate(self.add_pr43)
        code, out, err = self.diff("--closed", PR43, "--closed", PR42)
        self.assertEqual(code, 0)
        self.assertEqual([a["follow_up"]["rule"] for a in json.loads(out)], ["check-deploy"])
        self.assertIn(f"warning: --closed {PR42} is not a newly recorded pr artifact", err)

    def test_manual_mode_re_adds(self):
        self.write_candidate(self.drop_user_actions)
        code, out, _ = self.run_cli("follow-ups", "--candidate", str(self.cand), "--task", "T-0003", "--url", PR42)
        self.assertEqual(code, 0)
        self.assertEqual([a["description"] for a in json.loads(out)], ["Review repo-a#42", f"Check the deploy of {PR42}"])
        self.write_candidate(self.drop_user_actions)
        code, out, _ = self.run_cli("follow-ups", "--candidate", str(self.cand), "--task", "T-0003", "--url", PR42, "--closed")
        self.assertEqual([a["follow_up"]["done"] for a in json.loads(out)], ["manual"])

    def test_manual_mode_rejects_unknown_pr_and_mixed_flags(self):
        self.write_candidate()
        code, _, err = self.run_cli("follow-ups", "--candidate", str(self.cand), "--task", "T-0003", "--url", PR43)
        self.assertEqual(code, 1)
        self.assertIn("is not a pr artifact of task T-0003", err)
        code, _, err = self.run_cli("follow-ups", "--state", str(self.state), "--candidate", str(self.cand), "--task", "T-0003", "--url", PR42)
        self.assertEqual(code, 1)
        self.assertIn("--state cannot be combined", err)
        self.assertEqual(self.run_cli("follow-ups", "--candidate", str(self.cand))[0], 1)

    def test_refuses_state_file_or_outside_candidate(self):
        before = self.state.read_bytes()
        code, _, err = self.run_cli("follow-ups", "--state", str(self.state), "--candidate", str(self.state))
        self.assertEqual(code, 1)
        self.assertIn("is the state file", err)
        code, _, err = self.run_cli("follow-ups", "--candidate", str(self.state), "--task", "T-0003", "--url", PR42)
        self.assertEqual(code, 1)
        self.assertIn("is the state file", err)
        self.assertEqual(self.state.read_bytes(), before)
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "state.next.json"
            outside.write_text(json.dumps(fixture_state()), encoding="utf-8")
            outside_before = outside.read_bytes()
            code, _, err = self.run_cli("follow-ups", "--state", str(self.state), "--candidate", str(outside))
            self.assertEqual(code, 1)
            self.assertIn("is not in a supervisor directory", err)
            self.assertEqual(outside.read_bytes(), outside_before)

    def test_guard_allows_follow_ups_and_read_only_gh(self):
        script = self.dir / ".supervisor" / "state_tool.py"
        commands = [
            f"python3 {script} follow-ups --state {self.state} --candidate {self.cand} --closed {PR42}",
            f"python3 {script} validate-follow-ups --claude-md {self.md}",
            f'gh pr view "{PR42}" --json url,state',
            "gh auth status",
        ]
        for command in commands:
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(self.dir)}
                self.assertIsNone(st.guard_decision(payload, str(self.dir), {}))
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(self.dir / ".supervisor/observations/pr-state-follow-ups.json")},
                   "cwd": str(self.dir)}
        self.assertIsNone(st.guard_decision(payload, str(self.dir), {}))
        payload = {"tool_name": "Bash", "tool_input": {"command": f"gh pr merge {PR42}"}, "cwd": str(self.dir)}
        self.assertIsNotNone(st.guard_decision(payload, str(self.dir), {}))


class CliSurfaceTest(unittest.TestCase):
    def test_init_and_render_are_gone(self):
        for command in ("init", "render"):
            proc = subprocess.run([sys.executable, str(SCRIPT), command, "--state", "/nonexistent"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("invalid choice", proc.stderr)


class SchemaDocTest(unittest.TestCase):
    def test_no_agent_specific_field_names(self):
        doc = SKILL / "references" / "state-schema.md"
        if not doc.exists():
            self.skipTest("references/state-schema.md not written yet")
        hits = re.findall(r'"(?:claude|desktop|anthropic)[a-z_]*"', doc.read_text(encoding="utf-8"), re.I)
        self.assertEqual(hits, [])


class StdlibOnlyTest(unittest.TestCase):
    def test_imports_are_stdlib(self):
        source = SCRIPT.read_text(encoding="utf-8")
        modules = set(re.findall(r"^(?:import|from) ([a-zA-Z_]+)", source, re.M))
        self.assertTrue(modules <= set(sys.stdlib_module_names), modules - set(sys.stdlib_module_names))


if __name__ == "__main__":
    unittest.main()
