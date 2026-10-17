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
        self.assertEqual(ua["id"], "UA-0003")
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
        self.assertEqual(new["archived"][0], {"id": "T-0004", "title": "Tidy notes folder", "artifacts": [],
                                              "archived_at": NOW, "cancelled": True, "reason": "no longer needed"})

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
        # UA-0002, w-0005 needs_input, w-0004 archived outside the supervisor
        self.assertIn('data-pending="3"', hero)
        self.assertIn('<p class="hero__count">3</p>', hero)

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

    def test_zed_command_prefers_worktree_path_over_cwd(self):
        state = fixture_state()
        git_task(state, "T-0100", "w-0100")
        nongit_task(state, "T-0101", "w-0101", "/Users/handlename/notes", "running", "running")
        self.assertEqual(st.zed_command(find(state["workers"], "w-0100")), "zed '/repo/a/.claude/worktrees/license-x1'")
        self.assertEqual(st.zed_command(find(state["workers"], "w-0101")), "zed '/Users/handlename/notes'")

    def test_zed_command_quotes_single_quotes_for_posix_shell(self):
        worker = {"cwd": "/tmp/it's here", "worktree_path": None}
        cmd = st.zed_command(worker)
        self.assertEqual(cmd, "zed '/tmp/it'\\''s here'")
        self.assertEqual(subprocess.run(["sh", "-c", f"printf %s {cmd[4:]}"], capture_output=True, text=True).stdout,
                         "/tmp/it's here")

    def test_every_worker_row_has_escaped_zed_command_and_copy_button(self):
        state = fixture_state()
        find(state["workers"], "w-0004")["worktree_path"] = "/tmp/<b>&'x'{{TASKS}}"
        page = self.page(state)
        tasks = page[page.index('id="tasks"'):page.index('id="archived"')]
        self.assertEqual(tasks.count('class="wk"'), tasks.count('class="wk__cmd"'))
        self.assertEqual(tasks.count('class="wk__cmd"'), tasks.count('aria-label="Copy zed command">Copy</button>'))
        self.assertIn('<code class="wk__cmd">zed &#x27;/tmp/&lt;b&gt;&amp;&#x27;\\&#x27;&#x27;x&#x27;\\&#x27;&#x27;&#123;&#123;TASKS&#125;&#125;&#x27;</code>', tasks)
        self.assertNotIn("zed://", page)
        self.assertIn("navigator.clipboard.writeText", page)

    def test_task_and_archived_ids_have_escaped_copy_button(self):
        state = fixture_state()
        find(state["tasks"], "T-0004")["id"] = "T-0004"
        state["archived"] = [{"id": "T-0900", "title": "old", "artifacts": [], "archived_at": "2026-01-01T00:00:00Z"}]
        page = self.page(state)
        tasks = page[page.index('id="tasks"'):page.index('id="archived"')]
        archived = page[page.index('id="archived"'):]
        for t in state["tasks"]:
            self.assertIn(f'<span class="id task__id">{t["id"]}</span><button type="button" class="copy" data-copy aria-label="Copy {t["id"]}">Copy</button>', tasks)
        self.assertEqual(tasks.count('class="id task__id"'), len(state["tasks"]))
        self.assertIn('<span class="id">T-0900</span><button type="button" class="copy" data-copy aria-label="Copy T-0900">Copy</button>', archived)

    def test_copy_button_label_is_html_escaped(self):
        self.assertEqual(st._copy_button('T-<1>"'),
                         '<button type="button" class="copy" data-copy aria-label="Copy T-&lt;1&gt;&quot;">Copy</button>')

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

    def hooks(self, target):
        return json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"]

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
        self.assertTrue(all(line.startswith("exists:") for line in proc.stdout.splitlines()), proc.stdout)

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
        for settings in ({"hooks": []}, {"hooks": {"SessionStart": {}}}, {"hooks": {"SessionStart": ["x"]}}):
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

    def test_version_matches_copy(self):
        target = self.root / "sup"
        self.setup_dir(target)
        bundled = subprocess.run([sys.executable, str(SCRIPT), "version"], capture_output=True, text=True).stdout
        copied = subprocess.run([sys.executable, str(target / ".supervisor" / "state_tool.py"), "version"], capture_output=True, text=True).stdout
        self.assertRegex(bundled.strip(), r"^[0-9a-f]{12}$")
        self.assertEqual(bundled, copied)


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
