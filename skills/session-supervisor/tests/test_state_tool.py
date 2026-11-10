import argparse
import contextlib
import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SKILL = TESTS.parent
SCRIPT = SKILL / "scripts" / "state_tool.py"
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

    def pushed_candidate(self):
        git_task(self.current, "T-0020", "w-0020")
        cand = self.candidate()
        commit_artifacts(cand, "T-0020")
        find(cand["tasks"], "T-0020")["artifacts"][1]["url"] = COMMIT_URL
        find(cand["workers"], "w-0020")["intended_state"] = "archived"
        return cand

    def test_commit_delivery_pushed_at_head_skips_push_decision(self):
        cand = self.pushed_candidate()
        find(cand["tasks"], "T-0020")["artifacts"].append({"kind": "pr", "url": PR43})
        new = st.next_state(self.current, cand, NOW)
        self.assertNotEqual(find(new["tasks"], "T-0020")["status"], "needs_user")

    def test_commit_delivery_stale_commit_url_requires_push_decision(self):
        cand = self.pushed_candidate()
        find(cand["tasks"], "T-0020")["artifacts"][1]["ref"] = "def456"
        self.assertRejected(cand, "push-decision")

    def test_commit_delivery_url_on_other_group_does_not_exempt(self):
        cand = self.pushed_candidate()
        find(cand["tasks"], "T-0020")["artifacts"][0]["ref"] = "/repo/b#x"
        self.assertRejected(cand, "push-decision")

    def test_commit_delivery_pushed_sibling_does_not_exempt_unpushed_worker(self):
        cand = self.pushed_candidate()
        task = find(cand["tasks"], "T-0020")
        task["worker_ids"].append("w-0021")
        task["artifacts"] += [
            {"kind": "branch", "label": "b", "ref": "/repo/b#claude/b-x1"},
            {"kind": "commit", "label": "b", "ref": "def456"},
        ]
        sibling = copy.deepcopy(find(cand["workers"], "w-0020"))
        sibling.update({"id": "w-0021", "session_id": "sess_w-0021", "handle": "w-0021", "title": "w-0021",
                        "cwd": "/repo/b", "branch": "claude/b-x1", "worktree_path": "/repo/b/.claude/worktrees/b-x1"})
        cand["workers"].append(sibling)
        with self.assertRaises(st.StateError) as ctx:
            st.next_state(self.current, cand, NOW)
        self.assertIn("worker w-0021: commit delivery archive requires a push-decision", str(ctx.exception))
        self.assertNotIn("worker w-0020: commit delivery", str(ctx.exception))

    def test_commit_delivery_pr_between_branch_and_commit_fails_closed(self):
        cand = self.pushed_candidate()
        find(cand["tasks"], "T-0020")["artifacts"].insert(1, {"kind": "pr", "url": PR43})
        self.assertRejected(cand, "push-decision")

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

    def test_request_change_accepted(self):
        # v2: the request is the note's 概要, which the user may edit; it is no longer compared across commits.
        cand = self.candidate()
        find(cand["tasks"], "T-0004")["request"] = "~/notes を整理して"
        st.next_state(self.current, cand, NOW)
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

    def test_revive_rejected_unless_non_cancelled_stub_with_same_uid(self):
        for stub, uid in ((self.stub(uid="20261101_000000"), "20261102_000000"), (self.stub(uid="20261101_000000"), None),
                          (self.stub(cancelled=True, reason="x", uid="u"), "u")):
            with self.subTest(stub=stub, uid=uid):
                self.current = fixture_state()
                self.current["archived"].insert(0, stub)
                cand = self.candidate()
                self.revived(cand)["uid"] = uid
                self.assertRejected(cand, "T-0019: id is an archived stub; revive only a non-cancelled stub with the same uid")
        self.current = fixture_state()
        self.current["archived"].insert(0, self.stub(request="別の依頼", uid="u"))
        cand = self.candidate()
        self.revived(cand)["uid"] = "u"
        st.next_state(self.current, cand, NOW)

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

    def test_replace_worker_already_archived_with_result_rejected(self):
        for observed in ("archived", "missing"):
            with self.subTest(observed=observed):
                self.setUp()
                git_task(self.current, "T-0020", "w-0020", intended="archived", observed=observed)
                commit_artifacts(self.current, "T-0020")
                push_decision(self.current, "T-0020")
                cand = self.candidate()
                self.replace(cand, "w-0020", "w-0021")
                self.assertRejected(cand, "w-0020: replaced_by cannot be added to a worker already intended archived")

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
        self.assertEqual(json.loads(out), {"rev": 13})
        self.assertEqual(self.current()["rev"], 13)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["state.json", "state.next.json"])

    def test_legacy_dashboard_record_is_dropped(self):
        cand = self.current()
        cand["supervisor"]["dashboard"] = {"url": "https://claude.ai/artifact/x", "path": str(self.dir / "dashboard.html")}
        self.state.write_text(st.serialize(cand), encoding="utf-8")
        code, _, err = self.run_commit(self.current())
        self.assertEqual(code, 0, err)
        self.assertNotIn("dashboard", self.current()["supervisor"])

    def test_failure_leaves_files_unchanged(self):
        self.run_commit(self.current())
        before_state = self.state.read_bytes()
        cand = self.current()
        cand["rev"] = 1
        code, out, err = self.run_commit(cand)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("error:", err)
        self.assertEqual(self.state.read_bytes(), before_state)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["state.json", "state.next.json"])

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


def no_result(worker_id, summary="investigated only, nothing kept"):
    return {"worker_id": worker_id, "summary": summary, "at": NOW}


class NoResultTest(unittest.TestCase):
    def setUp(self):
        self.current = fixture_state()

    def candidate(self):
        return copy.deepcopy(self.current)

    def assertRejected(self, candidate, pattern):
        with self.assertRaises(st.StateError) as ctx:
            st.next_state(self.current, candidate, NOW)
        self.assertRegex(str(ctx.exception), pattern)

    def archive_with_no_result(self, cand, task_id, worker_id):
        find(cand["workers"], worker_id)["intended_state"] = "archived"
        find(cand["tasks"], task_id)["no_result"] = [no_result(worker_id)]

    def second_git_worker(self, state, task_id, worker_id, branch="claude/license-x2"):
        first = find(state["workers"], find(state["tasks"], task_id)["worker_ids"][0])
        state["workers"].append(dict(first, id=worker_id, session_id="sess_" + worker_id, handle=worker_id,
                                     title=worker_id, branch=branch))
        find(state["tasks"], task_id)["worker_ids"].append(worker_id)

    def test_commit_no_result_archive_accepted(self):
        git_task(self.current, "T-0020", "w-0020")
        cand = self.candidate()
        self.archive_with_no_result(cand, "T-0020", "w-0020")
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "running")
        cand = copy.deepcopy(new)
        find(cand["workers"], "w-0020")["observed_state"] = "archived"
        new = st.next_state(new, cand, NOW)
        self.assertNotIn("T-0020", [t["id"] for t in new["tasks"]])
        self.assertEqual(new["archived"][0]["no_result"], [no_result("w-0020")])

    def test_files_no_result_archive_accepted(self):
        nongit_task(self.current, "T-0030", "w-0030", "/data/x", "running", "idle")
        cand = self.candidate()
        self.archive_with_no_result(cand, "T-0030", "w-0030")
        st.next_state(self.current, cand, NOW)

    def test_push_pr_no_result_archive_accepted(self):
        git_task(self.current, "T-0020", "w-0020", delivery="push_pr")
        find(self.current["tasks"], "T-0020")["push_consent"] = {"text": "fix", "at": NOW}
        cand = self.candidate()
        self.archive_with_no_result(cand, "T-0020", "w-0020")
        st.next_state(self.current, cand, NOW)

    def test_archive_without_artifacts_or_no_result_rejected(self):
        git_task(self.current, "T-0020", "w-0020")
        cand = self.candidate()
        push_decision(cand, "T-0020")
        find(cand["workers"], "w-0020")["intended_state"] = "archived"
        self.assertRejected(cand, "lacks commit artifacts")

    def test_no_result_of_other_worker_does_not_exempt(self):
        git_task(self.current, "T-0020", "w-0020")
        self.second_git_worker(self.current, "T-0020", "w-0021")
        cand = self.candidate()
        self.archive_with_no_result(cand, "T-0020", "w-0020")
        find(cand["workers"], "w-0021")["intended_state"] = "archived"
        self.assertRejected(cand, "w-0021: archive requested but task lacks commit artifacts")
        find(cand["tasks"], "T-0020")["artifacts"] = [
            {"kind": "branch", "label": "a", "ref": "/repo/a#claude/license-x2"},
            {"kind": "commit", "label": "a", "ref": "abc123"},
        ]
        self.assertRejected(cand, "w-0021: commit delivery archive requires a push-decision user_action")

    def test_no_result_requires_archived_non_replaced_worker(self):
        git_task(self.current, "T-0020", "w-0020")
        self.second_git_worker(self.current, "T-0020", "w-0021")
        cases = {
            "running": lambda c: None,
            "replaced": lambda c: find(c["workers"], "w-0020").update(intended_state="archived", replaced_by="w-0021"),
            "unknown": lambda c: find(c["tasks"], "T-0020").update(no_result=[no_result("w-0099")]),
            "duplicate": lambda c: find(c["tasks"], "T-0020").update(no_result=[no_result("w-0020"), no_result("w-0020")]),
            "other task": lambda c: find(c["tasks"], "T-0020").update(no_result=[no_result("w-0004")]),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                cand = self.candidate()
                find(cand["tasks"], "T-0020")["no_result"] = [no_result("w-0020")]
                if name != "running":
                    find(cand["workers"], "w-0020")["intended_state"] = "archived"
                mutate(cand)
                self.assertRejected(cand, "no_result w-00[0-9]+ must name an archived, non-replaced worker of the task once")

    def test_no_result_shape_validated(self):
        for bad in ("x", [1], [{"worker_id": "w-0004", "summary": "", "at": NOW}], [{"worker_id": "w-0004", "at": NOW}],
                    [{"worker_id": "w-0004", "summary": "s", "at": 1}]):
            for owner in ("task", "stub"):
                with self.subTest(bad=bad, owner=owner):
                    cand = self.candidate()
                    (find(cand["tasks"], "T-0004") if owner == "task" else cand["archived"][0])["no_result"] = bad
                    self.assertRejected(cand, "no_result must be a list of")

    def test_no_result_carried_into_stub_and_concatenated_on_revival(self):
        git_task(self.current, "T-0020", "w-0020", intended="archived", observed="idle")
        find(self.current["tasks"], "T-0020")["no_result"] = [no_result("w-0020", "first")]
        cand = self.candidate()
        find(cand["workers"], "w-0020")["observed_state"] = "archived"
        state = st.next_state(self.current, cand, NOW)
        self.assertEqual(state["archived"][0]["no_result"], [no_result("w-0020", "first")])
        cand = copy.deepcopy(state)
        git_task(cand, "T-0020", "w-0020", intended="archived", observed="archived")
        find(cand["tasks"], "T-0020")["no_result"] = [no_result("w-0020", "second")]
        state = st.next_state(state, cand, NOW)
        stubs = [s for s in state["archived"] if s["id"] == "T-0020"]
        self.assertEqual(len(stubs), 1)
        self.assertEqual([n["summary"] for n in stubs[0]["no_result"]], ["first", "second"])
        self.assertNotIn("w-0020", [w["id"] for w in state["workers"]])
        self.assertEqual(st.validate(state), [])
        cand = copy.deepcopy(state)
        find(cand["tasks"], "T-0004")["cancel"] = {"at": NOW, "reason": "x"}
        state = st.next_state(state, cand, NOW)
        self.assertNotIn("no_result", find(state["archived"], "T-0004"))

class DigestTest(unittest.TestCase):
    def test_contents(self):
        text = st.digest(fixture_state(), "/Users/handlename/supervisor/state.json")
        self.assertLessEqual(len(text), 10000)
        for needle in ("/Users/handlename/supervisor/state.json",
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
        env = dict(os.environ, HOME=str(self.root / "home"))
        return subprocess.run([sys.executable, str(SCRIPT), "setup", "--dir", str(target)], capture_output=True, text=True, env=env)

    def files(self, target):
        return sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())

    def hooks(self, target, event="SessionStart"):
        return json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"][event]

    def test_creates_five_files_with_matcher(self):
        target = self.root / "sup"
        proc = self.setup_dir(target)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.files(target), [".claude/settings.json", ".supervisor/state_tool.py", "CLAUDE.md", "state.json",
                                              "vault/.obsidian/templates.json", "vault/Supervisor.md",
                                              "vault/_templates/project.md", "vault/_templates/task.md"])
        entries = self.hooks(target)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["matcher"], "startup|resume|clear|compact")
        state = json.loads((target / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(st._storage_errors(state, {}), [])
        self.assertEqual(st.validate(st.join_view(state, {})), [])
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

    def settings(self, target):
        return json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))

    def test_new_setup_runs_supervisor_on_opus(self):
        target = self.root / "sup"
        self.assertEqual(self.setup_dir(target).returncode, 0)
        self.assertEqual(self.settings(target)["model"], "opus")

    def test_adds_model_to_existing_setup(self):
        target = self.root / "sup"
        self.setup_dir(target)
        settings_path = target / ".claude" / "settings.json"
        settings = self.settings(target)
        del settings["model"]
        settings_path.write_text(json.dumps(settings), encoding="utf-8")
        proc = self.setup_dir(target)
        self.assertIn(f"updated: {settings_path}", proc.stdout)
        self.assertEqual(self.settings(target)["model"], "opus")
        self.assertEqual(len(self.hooks(target)), 1)

    def test_keeps_model_chosen_by_user(self):
        target = self.root / "sup"
        (target / ".claude").mkdir(parents=True)
        (target / ".claude" / "settings.json").write_text(json.dumps({"model": "sonnet"}), encoding="utf-8")
        self.assertEqual(self.setup_dir(target).returncode, 0)
        self.assertEqual(self.settings(target)["model"], "sonnet")
        proc = self.setup_dir(target)
        self.assertTrue(all(line.startswith("exists:") for line in proc.stdout.splitlines()), proc.stdout)

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
        self.assertEqual(st.parse_locations(text), ({"clone_root": "~/src", "work_root": str(self.root / "sup-work"), "worktree_root": "~/.claude-worktrees"}, []))
        self.assertIn("prepare-dir` and `prepare-worktree` are the only commands that write outside", text)
        self.assertEqual(st.parse_herdr(text), ({"max_workers": 8}, []))
        self.assertIn("The allowed exceptions are `state_tool.py prepare-dir`, `prepare-worktree`, and `remove-worktree`", text)

    def test_appends_locations_to_existing_claude_md(self):
        target = self.root / "sup"
        target.mkdir()
        body = "# My supervisor\n\nKeep this text.\n"
        (target / "CLAUDE.md").write_text(body, encoding="utf-8")
        proc = self.setup_dir(target)
        self.assertIn(f"updated: {target / 'CLAUDE.md'}", proc.stdout)
        text = (target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith(body + "\n## Locations\n"), text)
        self.assertEqual(st.parse_locations(text)[0], {"clone_root": "~/src", "work_root": str(self.root / "sup-work"), "worktree_root": "~/.claude-worktrees"})

    def test_keeps_custom_values_and_adds_only_missing_keys(self):
        target = self.root / "sup"
        target.mkdir()
        body = "# Mine\n\n## Locations\n\n- clone_root: /code\n\n## Follow-ups\n"
        (target / "CLAUDE.md").write_text(body, encoding="utf-8")
        self.assertEqual(self.setup_dir(target).returncode, 0)
        text = (target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith(f"# Mine\n\n## Locations\n\n- clone_root: /code\n- work_root: {self.root / 'sup-work'}\n"
                                        "- worktree_root: ~/.claude-worktrees\n\n## Follow-ups\n"), text)
        mine = "## Locations\n- work_root: ~/work\n- clone_root: ~/code\n- worktree_root: ~/trees\n\n## Herdr\n- max_workers: 3\n"
        (target / "CLAUDE.md").write_text(mine, encoding="utf-8")
        proc = self.setup_dir(target)
        self.assertIn(f"exists: {target / 'CLAUDE.md'}", proc.stdout)
        self.assertEqual((target / "CLAUDE.md").read_text(encoding="utf-8"), mine)

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
                         {"clone_root": "~/src", "work_root": "/Users/me/supervisor-work", "worktree_root": "~/.claude-worktrees"})

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
        trees = st._real("~/.claude-worktrees")
        self.assertEqual(self.run_cli("--show"),
                         (0, f"clone_root: {self.clones}\nwork_root: {self.work}\nworktree_root: {trees} (default)\n", ""))
        home = self.root / "home"
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            trees = home / ".claude-worktrees"
            self.write_locations("- clone_root: ~/code\n")
            self.assertEqual(self.run_cli("--show")[1], f"clone_root: {home / 'code'}\nwork_root: {self.root / 'sup-work'} (default)\n"
                             f"worktree_root: {trees} (default)\n")
            self.md.write_text("# Supervisor\n", encoding="utf-8")
            self.assertEqual(self.run_cli("--show")[1], f"clone_root: {home / 'src'} (default)\nwork_root: {self.root / 'sup-work'} (default)\n"
                             f"worktree_root: {trees} (default)\n")
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
COMMIT_URL = "https://github.com/org/a/commit/abc123"


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

    def test_new_pr_urls_includes_commit_delivery_pr(self):
        cand = copy.deepcopy(self.state)
        git_task(cand, "T-0020", "w-0020")
        find(cand["tasks"], "T-0020")["artifacts"].append({"kind": "pr", "url": PR43})
        self.assertIn(("T-0020", PR43), st.new_pr_urls(self.state, cand))

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


class WorkerModelTest(unittest.TestCase):
    def state_with(self, **fields):
        state = fixture_state()
        find(state["workers"], "w-0005").update(fields)
        return state

    def test_model_is_optional(self):
        state = fixture_state()
        self.assertNotIn("model", find(state["workers"], "w-0005"))
        self.assertEqual(st.validate(state), [])
        self.assertEqual(st.validate(self.state_with(model=None)), [])

    def test_accepts_each_family(self):
        for model in st.MODELS:
            with self.subTest(model=model):
                self.assertEqual(st.validate(self.state_with(model=model)), [])

    def test_rejects_unknown_model(self):
        for model in ("gpt", "claude-sonnet-5-5", 3, True):
            with self.subTest(model=model):
                errors = st.validate(self.state_with(model=model))
                self.assertTrue(any("w-0005: model must be one of" in e for e in errors), errors)

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



PROJECT_DIR = "/Users/handlename/supervisor-work/P-0001-nature-mcp"


def add_project(state, pid="P-0001", name="Nature MCP", directory=PROJECT_DIR, last_review=0):
    state["supervisor"]["last_project_number"] = max(state["supervisor"].get("last_project_number", 0), int(pid[2:]))
    state.setdefault("projects", []).append({
        "id": pid, "name": name, "context_doc_url": "https://github.com/org/repo/issues/1", "dir": directory,
        "created_at": NOW, "last_review_number": last_review, "user_actions": [],
        "manager": {
            "session_id": f"sess_mgr_{pid}", "started_by": "sess_sup_01", "handle": f"{pid.lower().replace('-', '')}-manager",
            "title": f"{pid} {name}", "intended_state": "running", "observed_state": "idle", "observed_at": NOW,
            "last_report": None, "previous_session_ids": [], "replacements": 0,
        },
    })
    return state["projects"][-1]


def project_worker(state, task_id="T-0020", worker_id="w-0020", pid="P-0001", **kw):
    git_task(state, task_id, worker_id, **kw)
    find(state["tasks"], task_id)["project_id"] = pid
    return find(state["workers"], worker_id)


def rep(ref, status="done"):
    return {"message_ref": ref, "status": status, "summary": "s", "next_action": ""}


def review(rid="R-0001", state="requested", ref="m1", **extra):
    return dict({"id": rid, "state": state, "at": NOW, "message_ref": ref, "ref": "abc123"}, **extra)


class ProjectStateTest(unittest.TestCase):
    def setUp(self):
        self.current = fixture_state()
        add_project(self.current, last_review=1)
        w = project_worker(self.current)
        w["last_report"], w["review"] = rep("m1"), review()

    def cand(self):
        return copy.deepcopy(self.current)

    def assertRejected(self, candidate, pattern, current=None):
        with self.assertRaises(st.StateError) as ctx:
            st.next_state(current or self.current, candidate, NOW)
        self.assertRegex(str(ctx.exception), pattern)

    def archive(self, cand, wid="w-0020", tid="T-0020"):
        find(cand["workers"], wid)["intended_state"] = "archived"
        commit_artifacts(cand, tid)
        push_decision(cand, tid)
        return find(cand["workers"], wid)

    def test_states_without_projects_pass(self):
        state = fixture_state()
        self.assertNotIn("projects", state)
        self.assertEqual(st.validate(state), [])
        empty = st.empty_state("/sup")
        self.assertEqual((empty["projects"], empty["supervisor"]["last_project_number"]), ([], 0))
        self.assertEqual(st._storage_errors(empty, {}), [])
        self.assertEqual(st.validate(st.join_view(empty, {})), [])
        self.assertEqual(st.validate(self.current), [])

    def test_project_shape_errors(self):
        for mutate, pattern in (
            (lambda p: p.update(context_doc_url="file:///x"), "context_doc_url must be http"),
            (lambda p: p.update(dir="relative/dir"), "dir must be an absolute path"),
            (lambda p: p.update(id="P-0009"), "exceeds supervisor.last_project_number"),
            (lambda p: p["manager"].update(intended_state="gone"), "invalid intended_state"),
            (lambda p: p["manager"].update(last_report={"message_ref": "x", "status": "done", "summary": "", "at": NOW}), "manager.last_report"),
            (lambda p: p.update(user_actions=[{"id": "UA-0900", "description": "x", "proposal": {"title": "t"}}]), "proposal must be"),
        ):
            state = self.cand()
            mutate(state["projects"][0])
            self.assertRegex("; ".join(st.validate(state)), pattern)
        state = self.cand()
        state["projects"].append(copy.deepcopy(state["projects"][0]))
        self.assertRegex("; ".join(st.validate(state)), "duplicate project ids")

    def test_project_id_must_name_an_open_project(self):
        state = self.cand()
        find(state["tasks"], "T-0020")["project_id"] = "P-0002"
        self.assertIn("task T-0020: project_id P-0002 must name an open project", st.validate(state))
        state = self.cand()
        state["projects"][0]["close"] = {"at": NOW, "reason": "done"}
        state["projects"][0]["manager"]["intended_state"] = "archived"
        self.assertIn("task T-0020: project_id P-0001 must name an open project", st.validate(state))
        state = self.cand()
        find(state["tasks"], "T-0020")["proposal"] = {"title": "t", "text": "x", "from": "P-0002", "message_ref": "m"}
        self.assertRegex("; ".join(st.validate(state)), "proposal.from must equal project_id")
        state = self.cand()
        find(state["tasks"], "T-0003")["redos"] = [{"text": "again", "at": NOW, "from": "P-0001"}]
        self.assertRegex("; ".join(st.validate(state)), "redos belong to a project task")

    def test_user_action_numbers_are_shared(self):
        state = self.cand()
        state["projects"][0]["user_actions"] = [{"id": "UA-0002", "description": "x", "since": NOW}]
        self.assertRegex("; ".join(st.validate(state)), "duplicate user_action ids")
        state["projects"][0]["user_actions"][0]["id"] = "UA-0950"
        self.assertEqual(st.validate(state), [])
        self.assertEqual(st._next_ua_number(state), 951)
        state["supervisor"]["last_ua_number"] = 980
        self.assertEqual(st._next_ua_number(state), 981)
        state["supervisor"]["last_ua_number"] = 10
        actions = st.follow_up_actions(state, "T-0020", "https://github.com/o/r/pull/1",
                                       [{"id": "review-pr", "text": "Review {label}", "done": "pr_closed"}], NOW)
        self.assertEqual(actions[0]["id"], "UA-0951")

    def test_waiting_start_action_avoids_project_numbers(self):
        current = fixture_state()
        add_project(current)["user_actions"] = [{"id": "UA-0950", "description": "x", "since": NOW}]
        nongit_task(current, "T-0005", "w-0006", "/Users/handlename/notes", "archived", "idle")
        find(current["tasks"], "T-0005")["artifacts"] = [{"kind": "file", "path": "/Users/handlename/notes/a.txt"}]
        cand = copy.deepcopy(current)
        find(cand["workers"], "w-0006")["observed_state"] = "archived"
        new = st.next_state(current, cand, NOW)
        ids = [ua["id"] for ua in find(new["tasks"], "T-0004")["user_actions"]]
        self.assertEqual(ids, ["UA-0951"])

    def test_approval_gate(self):
        for state_, ok in (("requested", False), ("rejected", False), ("superseded", False), ("approved", True)):
            cand = self.cand()
            w = self.archive(cand)
            w["review"]["state"] = state_
            if ok:
                st.next_state(self.current, cand, NOW)
            else:
                self.assertRejected(cand, r"worker w-0020: archived project worker needs an approved review of its latest report")
        cand = self.cand()
        self.archive(cand)["review"] = review("R-0001", "waived", reason="the user said no review")
        st.next_state(self.current, cand, NOW)
        cand = self.cand()
        del self.archive(cand)["review"]
        self.assertRejected(cand, "needs an approved review")

    def test_gate_holds_for_archived_or_missing_sessions(self):
        for observed in ("archived", "missing"):
            cand = self.cand()
            w = self.archive(cand)
            w["observed_state"] = observed
            del w["review"]
            self.assertRejected(cand, "needs an approved review")

    def test_cancel_and_replaced_by_are_exempt(self):
        cand = self.cand()
        find(cand["tasks"], "T-0020")["cancel"] = {"at": NOW, "reason": "stop"}
        w = find(cand["workers"], "w-0020")
        w["intended_state"] = "archived"
        del w["review"]
        st.next_state(self.current, cand, NOW)
        cand = self.cand()
        w = find(cand["workers"], "w-0020")
        succ = copy.deepcopy(w)
        succ.update(id="w-0021", session_id=None, intended_state="pending", observed_state="none", last_report=None)
        del succ["review"]
        cand["workers"].append(succ)
        find(cand["tasks"], "T-0020")["worker_ids"].append("w-0021")
        w.update(intended_state="archived", replaced_by="w-0021")
        st.next_state(self.current, cand, NOW)

    def test_gate_is_not_applied_outside_projects(self):
        cand = self.cand()
        find(cand["workers"], "w-0004")["intended_state"] = "archived"
        self.assertNotRegex("; ".join(st.validate(cand)), "approved review")

    def test_approval_binds_to_the_latest_report(self):
        current = self.cand()
        w = find(current["workers"], "w-0020")
        w["review"]["state"] = "approved"
        commit_artifacts(current, "T-0020")
        push_decision(current, "T-0020")
        w["intended_state"] = "archived"
        self.assertEqual(st.validate(current), [])
        cand = copy.deepcopy(current)
        find(cand["workers"], "w-0020")["last_report"] = rep("m2", "in_progress")
        self.assertRejected(cand, "needs an approved review of its latest report", current)
        cand_w = find(cand["workers"], "w-0020")
        cand_w["intended_state"], cand_w["review"]["state"] = "running", "superseded"
        st.next_state(current, cand, NOW)
        ext = copy.deepcopy(current)
        ext_w = find(ext["workers"], "w-0020")
        ext_w.update(last_report=None, observed_state="archived")
        ext_w["review"]["message_ref"] = "external:" + NOW
        self.assertEqual(st.validate(ext), [])

    def test_push_only_waived(self):
        cand = self.cand()
        cand["projects"][0]["last_review_number"] = 2
        find(cand["tasks"], "T-0020")["push_consent"] = {"text": "fix the LICENSE"}
        w = find(cand["workers"], "w-0020")
        w.update(delivery="push_pr")
        w["review"] = {"id": "R-0002", "state": "waived", "at": NOW, "reason": "push-only after R-0001"}
        st.next_state(self.current, cand, NOW)
        bad = copy.deepcopy(cand)
        del find(bad["workers"], "w-0020")["review"]["at"]
        self.assertRegex("; ".join(st.validate(bad)), "review.at")
        bad = copy.deepcopy(cand)
        del find(bad["workers"], "w-0020")["review"]["id"]
        self.assertRegex("; ".join(st.validate(bad)), "review.id")

    def test_reuse_push_after_approval(self):
        current = self.cand()
        w = find(current["workers"], "w-0020")
        w["review"]["state"], w["intended_state"] = "approved", "archived"
        commit_artifacts(current, "T-0020")
        push_decision(current, "T-0020")
        find(current["tasks"], "T-0020")["push_consent"] = {"text": "push it", "message": "push it please"}
        reuse = copy.deepcopy(current)
        rw = find(reuse["workers"], "w-0020")
        rw.update(delivery="push_pr", intended_state="running")
        reuse["projects"][0]["last_review_number"] = 2
        rw["review"] = {"id": "R-0002", "state": "waived", "at": NOW, "reason": "push-only after R-0001"}
        reused = st.next_state(current, reuse, NOW)
        done = copy.deepcopy(reused)
        dw = find(done["workers"], "w-0020")
        dw["last_report"], dw["intended_state"] = rep("m3"), "archived"
        find(done["tasks"], "T-0020")["artifacts"][0]["url"] = "https://github.com/o/r/tree/claude/license-x1"
        st.next_state(reused, done, NOW)
        kept = copy.deepcopy(done)
        find(kept["workers"], "w-0020")["review"] = review("R-0001", "approved")
        self.assertRegex("; ".join(st.validate(kept)), "needs an approved review")

    def test_pending_verdict(self):
        state = self.cand()
        w = find(state["workers"], "w-0020")
        w["review"]["pending_verdict"] = {"verdict": "approve", "message_ref": "m1", "at": NOW}
        self.assertEqual(st.validate(state), [])
        w["review"]["pending_verdict"]["message_ref"] = "m0"
        self.assertRegex("; ".join(st.validate(state)), "pending_verdict")
        w["review"]["pending_verdict"]["message_ref"] = "m1"
        w["review"]["state"] = "rejected"
        self.assertRegex("; ".join(st.validate(state)), "pending_verdict")

    def test_review_shape_and_numbers(self):
        state = self.cand()
        find(state["workers"], "w-0004")["review"] = review()
        self.assertRegex("; ".join(st.validate(state)), "w-0004: review belongs to a project task worker")
        state = self.cand()
        del find(state["workers"], "w-0020")["review"]["message_ref"]
        self.assertRegex("; ".join(st.validate(state)), "requested review requires message_ref")
        state = self.cand()
        find(state["workers"], "w-0020")["review"] = {"id": "R-0001", "state": "waived", "at": NOW}
        self.assertRegex("; ".join(st.validate(state)), "waived review requires reason")
        state = self.cand()
        project_worker(state, "T-0021", "w-0021")["review"] = review()
        self.assertRegex("; ".join(st.validate(state)), "R-0001 is also used by w-0020")
        add_project(state, "P-0002", directory="/Users/handlename/supervisor-work/P-0002-other", last_review=1)
        find(state["tasks"], "T-0021")["project_id"] = "P-0002"
        self.assertEqual(st.validate(state), [])
        state = self.cand()
        find(state["workers"], "w-0020")["review"]["id"] = "R-0002"
        self.assertRegex("; ".join(st.validate(state)), "exceeds last_review_number 1")
        cand = self.cand()
        project_worker(cand, "T-0021", "w-0021")
        find(cand["workers"], "w-0020")["review"]["id"] = "R-0001"
        find(cand["workers"], "w-0021")["review"] = review("R-0001")
        del find(cand["workers"], "w-0020")["review"]
        self.assertRejected(cand, "w-0021: a new review id must be above last_review_number 1")

    def test_awaiting_manager(self):
        state = self.cand()
        w = find(state["workers"], "w-0020")
        w["last_report"] = rep("m2", "awaiting_manager")
        w["review"]["state"] = "superseded"
        self.assertRegex("; ".join(st.validate(state)), "awaiting_since is required")
        w["awaiting_since"] = NOW
        self.assertEqual(st.validate(state), [])
        other = find(state["workers"], "w-0004")
        other["last_report"], other["awaiting_since"] = rep("m9", "awaiting_manager"), NOW
        self.assertRegex("; ".join(st.validate(state)), "w-0004: awaiting_manager is reported only by project task workers")
        state = self.cand()
        find(state["workers"], "w-0020")["awaiting_since"] = NOW
        self.assertRegex("; ".join(st.validate(state)), "awaiting_since is required exactly")

    def test_nongit_cwd_must_not_overlap_project_dir(self):
        state = self.cand()
        nongit_task(state, "T-0030", "w-0030", PROJECT_DIR + "/sub", "pending", "none", session_id=None)
        self.assertRegex("; ".join(st.validate(state)), "w-0030: cwd overlaps the directory of project P-0001")
        self.assertTrue(st.cwd_busy(self.current, PROJECT_DIR))
        self.assertTrue(st.cwd_busy(self.current, PROJECT_DIR + "/x"))
        self.assertFalse(st.cwd_busy(self.current, "/Users/handlename/elsewhere"))

    def test_project_removal_and_numbering(self):
        cand = self.cand()
        cand["projects"] = []
        del find(cand["tasks"], "T-0020")["project_id"]
        del find(cand["workers"], "w-0020")["review"]
        self.assertRejected(cand, "project P-0001 was removed")
        cand = self.cand()
        cand["projects"][0]["last_review_number"] = 0
        find(cand["workers"], "w-0020")["review"]["id"] = "R-0000"
        self.assertRejected(cand, "last_review_number must not decrease")
        cand = self.cand()
        cand["supervisor"]["last_project_number"] = 0
        cand["projects"][0]["id"] = "P-0000"
        find(cand["tasks"], "T-0020")["project_id"] = "P-0000"
        self.assertRejected(cand, "last_project_number must not decrease")
        current = fixture_state()
        current["supervisor"]["last_project_number"] = 1
        cand = copy.deepcopy(current)
        add_project(cand)
        self.assertRejected(cand, "a new project id must be above last_project_number 1", current)

    def test_closed_project_is_dropped_when_finished(self):
        current = fixture_state()
        add_project(current)
        cand = copy.deepcopy(current)
        p = cand["projects"][0]
        p["close"] = {"at": NOW, "reason": "close it"}
        p["manager"]["intended_state"] = "archived"
        new = st.next_state(current, cand, NOW)
        self.assertEqual(len(new["projects"]), 1)
        cand = copy.deepcopy(new)
        cand["projects"][0]["manager"]["observed_state"] = "archived"
        newer = st.next_state(new, cand, NOW)
        self.assertEqual(newer["projects"], [])
        self.assertEqual(newer["supervisor"]["last_project_number"], 1)

    def test_manager_archive_needs_close_or_gone(self):
        cand = self.cand()
        cand["projects"][0]["manager"]["intended_state"] = "archived"
        self.assertRejected(cand, "the manager is archived only when")
        cand["projects"][0]["manager"]["observed_state"] = "missing"
        st.next_state(self.current, cand, NOW)

    def test_stub_carries_project_proposal_and_redos(self):
        cand = self.cand()
        t = find(cand["tasks"], "T-0020")
        t["proposal"] = {"title": "t", "text": "x", "from": "P-0001", "message_ref": "m0"}
        t["redos"] = [{"text": "again", "at": NOW, "from": "P-0001", "review_id": "R-0001"}]
        w = self.archive(cand)
        w["review"]["state"], w["observed_state"] = "approved", "archived"
        t["user_actions"] = []
        t["artifacts"][1]["url"] = "https://github.com/o/r/commit/abc123"
        new = st.next_state(self.current, cand, NOW)
        stub = find(new["archived"], "T-0020")
        self.assertEqual(stub["project"], {"id": "P-0001", "name": "Nature MCP"})
        self.assertEqual(stub["proposal"]["title"], "t")
        self.assertEqual(stub["redos"][0]["text"], "again")

    def test_reviewing_worker_gone_keeps_task_open(self):
        cand = self.cand()
        find(cand["workers"], "w-0020")["observed_state"] = "archived"
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "needs_user")
        cand = self.cand()
        find(cand["workers"], "w-0020")["observed_state"] = "missing"
        new = st.next_state(self.current, cand, NOW)
        self.assertEqual(find(new["tasks"], "T-0020")["status"], "running")


PROJECT_REPORT = """done.

```project-report
{body}
```
"""


def project_report(**overrides):
    body = {"project_id": "P-0001", "status": "idle", "summary": "ok", "reviews": [], "plans": [], "questions": []}
    body.update(overrides)
    return PROJECT_REPORT.format(body=json.dumps(body))


class ProjectCliTest(unittest.TestCase):
    def setUp(self):
        self.state = fixture_state()
        add_project(self.state, last_review=1)
        w = project_worker(self.state)
        w["last_report"], w["review"] = rep("m1"), review()

    def actions(self, report, running=(), state=None):
        parsed = st.parse_project_report(report, "P-0001")
        self.assertTrue(parsed["ok"], parsed)
        return st.plan_actions(state or self.state, "P-0001", parsed["report"], running)

    def test_parse_report_awaiting_manager(self):
        text = "```supervisor-report\n" + json.dumps({
            "task_id": "T-0020", "worker_id": "w-0020", "status": "awaiting_manager", "summary": "asked",
            "artifacts": {}, "blockers": ["which API?"], "next_action": ""}) + "\n```"
        self.assertEqual(st.parse_report(text)["error"], "invalid status 'awaiting_manager'")
        self.assertTrue(st.parse_report(text, allow_awaiting_manager=True)["ok"])
        self.assertIn("requires at least one blocker",
                      st.parse_report(text.replace('["which API?"]', "[]"), allow_awaiting_manager=True)["error"])

    def test_parse_project_report(self):
        self.assertTrue(st.parse_project_report(project_report(), "P-0001")["ok"])
        for report, pattern in (
            (project_report(reviews=[{"review_id": "R-0001", "verdict": "reject", "comment": ""}]), "reject requires a non-empty redo"),
            (project_report(status="needs_input"), "needs_input requires at least one question"),
            (project_report(project_id="P-0002"), "does not match"),
            ("no block", "no project-report block"),
            (project_report(plans=[{"kind": "redo", "text": "x"}]), "redo plan needs"),
        ):
            result = st.parse_project_report(report, "P-0001")
            self.assertFalse(result["ok"])
            self.assertRegex(result["error"], pattern)

    def kinds(self, actions):
        return [a["kind"] for a in actions]

    def test_review_verdicts(self):
        approve = project_report(reviews=[{"review_id": "R-0001", "verdict": "approve", "comment": "good"}])
        reject = project_report(reviews=[{"review_id": "R-0001", "verdict": "reject", "comment": "", "redo": "fix x"}])
        self.assertEqual(self.kinds(self.actions(approve)), ["approve_ok"])
        self.assertEqual(self.kinds(self.actions(reject)), ["reject_ok"])
        self.assertEqual(self.kinds(self.actions(approve, running=["w-0020"])), ["stale_notify"])
        w = find(self.state["workers"], "w-0020")
        w["review"]["stale_notified"] = "m1"
        self.assertEqual(self.kinds(self.actions(approve, running=["w-0020"])), ["stale_silent"])
        del w["review"]["stale_notified"]
        w["last_report"] = rep("m2", "in_progress")
        self.assertEqual(self.kinds(self.actions(approve)), ["stale_notify"])
        w["observed_state"] = "archived"
        self.assertEqual(self.kinds(self.actions(approve)), ["approve_ok"])
        for state_ in ("superseded", "rejected", "approved", "waived"):
            w["review"]["state"] = state_
            self.assertEqual(self.kinds(self.actions(approve)), ["stale_silent"])
        w["review"]["state"], w["replaced_by"] = "requested", "w-0099"
        self.assertEqual(self.kinds(self.actions(approve)), ["stale_silent"])
        unknown = project_report(reviews=[{"review_id": "R-0007", "verdict": "approve"}])
        self.assertEqual(self.kinds(self.actions(unknown)), ["stale_silent"])

    def test_superseded_review_drops_out(self):
        w = find(self.state["workers"], "w-0020")
        w["last_report"], w["awaiting_since"] = rep("m2", "awaiting_manager"), NOW
        w["review"]["state"] = "superseded"
        self.assertEqual(st.validate(self.state), [])
        approve = project_report(reviews=[{"review_id": "R-0001", "verdict": "approve"}])
        self.assertEqual(self.kinds(self.actions(approve)), ["stale_silent"])
        self.assertIn("(none)", st.roster(self.state, "P-0001").split("### Open reviews")[1])
        self.assertEqual([x for x in st.waits(self.state, "2026-10-17T15:00:00+09:00") if x["kind"] == "review"], [])

    def test_same_review_id_in_another_project_does_not_match(self):
        add_project(self.state, "P-0002", directory="/Users/handlename/supervisor-work/P-0002-other", last_review=1)
        w = project_worker(self.state, "T-0021", "w-0021", pid="P-0002")
        w["last_report"], w["review"] = rep("m5"), review(ref="m5")
        result = st.plan_actions(self.state, "P-0002", st.parse_project_report(
            project_report(project_id="P-0002", reviews=[{"review_id": "R-0001", "verdict": "approve"}]))["report"])
        self.assertEqual([(a["kind"], a["worker_id"]) for a in result], [("approve_ok", "w-0021")])

    def test_redo_plans(self):
        plan = lambda **kw: project_report(plans=[dict({"kind": "redo", "task_id": "T-0020", "text": "redo it"}, **kw)])
        self.assertEqual([(a["kind"], a.get("worker_id")) for a in self.actions(plan())], [("redo_send", "w-0020")])
        w2 = project_worker(self.state, "T-0022", "w-0022")
        task = find(self.state["tasks"], "T-0020")
        task["worker_ids"].append("w-0022")
        w2["task_id"] = "T-0020"
        self.state["tasks"].remove(find(self.state["tasks"], "T-0022"))
        self.assertEqual(self.kinds(self.actions(plan())), ["redo_ambiguous"])
        self.assertEqual([(a["kind"], a.get("worker_id")) for a in self.actions(plan(worker_id="w-0022"))], [("redo_send", "w-0022")])
        for w in (find(self.state["workers"], "w-0020"), w2):
            w["observed_state"] = "missing"
        self.assertEqual(self.kinds(self.actions(plan())), ["redo_start"])
        task["redos"] = [{"text": "redo it", "at": NOW, "from": "P-0001"}]
        self.assertEqual(self.kinds(self.actions(plan())), ["redo_dup"])
        self.assertEqual(self.kinds(self.actions(plan(task_id="T-0003"))), ["redo_foreign"])
        self.state["archived"].insert(0, {"id": "T-0040", "title": "old", "request": "r", "artifacts": [], "archived_at": NOW,
                                          "project": {"id": "P-0001", "name": "Nature MCP"}})
        self.assertEqual(self.kinds(self.actions(plan(task_id="T-0040"))), ["redo_archived"])
        self.assertEqual(self.kinds(self.actions(plan(task_id="T-9999"))), ["redo_foreign"])

    def test_proposals(self):
        new = project_report(plans=[{"kind": "new", "title": "Add docs", "text": "write docs"},
                                    {"kind": "new", "title": "Add docs", "text": "again"}])
        self.assertEqual(self.kinds(self.actions(new)), ["proposal_add", "proposal_dup"])
        self.state["projects"][0]["user_actions"] = [{"id": "UA-0900", "description": "Approve", "since": NOW,
                                                      "proposal": {"title": "Add docs", "text": "x", "message_ref": "m"}}]
        self.assertEqual(self.kinds(self.actions(new)), ["proposal_dup", "proposal_dup"])

    def test_roster(self):
        text = st.roster(self.state, "P-0001")
        self.assertIn("| w-0020 | T-0020 |", text)
        self.assertIn("| R-0001 | w-0020 | T-0020 | abc123 |", text)
        find(self.state["workers"], "w-0020")["review"]["state"] = "rejected"
        self.assertIn("(none)", st.roster(self.state, "P-0001").split("### Open reviews")[1])
        add_project(self.state, "P-0002", directory="/Users/handlename/supervisor-work/P-0002-other")
        self.assertIn("(no workers)", st.roster(self.state, "P-0002"))

    def test_waits(self):
        w = find(self.state["workers"], "w-0020")
        w["review"]["state"] = "superseded"
        w["last_report"], w["awaiting_since"] = rep("m2", "awaiting_manager"), "2026-10-17T11:00:00+09:00"
        self.assertEqual(st.waits(self.state, "2026-10-17T11:29:00+09:00"), [])
        found = st.waits(self.state, "2026-10-17T11:31:00+09:00")
        self.assertEqual([(x["kind"], x["worker_id"]) for x in found], [("hub", "w-0020")])
        w["observed_state"] = "running"
        self.assertEqual(st.waits(self.state, "2026-10-17T11:31:00+09:00"), [])
        w["observed_state"] = "idle"
        find(self.state["tasks"], "T-0020")["user_actions"].append(
            {"id": "UA-0900", "description": "Hub wait w-0020 (T-0020 `w-0020`): no answer", "since": NOW})
        self.assertEqual(st.waits(self.state, "2026-10-17T11:31:00+09:00"), [])
        find(self.state["tasks"], "T-0020")["user_actions"] = []
        w["last_report"], w["review"] = rep("m3"), review(ref="m3")
        del w["awaiting_since"]
        w["review"]["at"] = "2026-10-17T11:00:00+09:00"
        self.assertEqual(st.waits(self.state, "2026-10-17T12:59:00+09:00"), [])
        self.assertEqual([x["kind"] for x in st.waits(self.state, "2026-10-17T13:01:00+09:00")], ["review"])
        for state_ in ("rejected", "superseded"):
            w["review"]["state"] = state_
            self.assertEqual(st.waits(self.state, "2026-10-17T15:00:00+09:00"), [])

    def test_manager_reconcile(self):
        m = self.state["projects"][0]["manager"]
        row = {"sessionId": "sess_mgr_P-0001", "startedBy": "sess_sup_01", "isRunning": False, "isArchived": False}
        decide = lambda rows, events=None, manager=m: [d["decision"] for d in st.manager_reconcile(manager, rows, events)]
        self.assertEqual(decide([row]), ["update"])
        self.assertEqual(decide([row], {"last_message_ref": "mm1", "pending_approval": False}), ["update", "handle_turn"])
        m["last_report"] = {"message_ref": "mm1", "status": "idle", "summary": "", "at": NOW}
        self.assertEqual(decide([row], {"last_message_ref": "mm1"}), ["update"])
        self.assertEqual(decide([row], {"pending_approval": True}), ["pending_approval", "update"])
        self.assertEqual(decide([]), ["missing"])
        self.assertEqual(decide([dict(row, isArchived=True)]), ["external_archive"])
        m["previous_session_ids"] = ["sess_old"]
        self.assertEqual(decide([row, {"sessionId": "sess_old", "startedBy": "sess_sup_01", "isArchived": False}]),
                         ["stale_previous", "update"])
        self.assertEqual(decide([row, {"sessionId": "sess_rogue", "startedBy": "sess_mgr_P-0001"}]),
                         ["foreign_session", "update"])
        self.assertEqual(decide([dict(row, isRunning=True)], {"last_message_ref": "mm9"}), ["update"])

    def test_cli_commands(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            path.write_text(json.dumps(self.state), encoding="utf-8")
            msg = Path(d) / "msg.txt"
            msg.write_text(project_report(reviews=[{"review_id": "R-0001", "verdict": "approve"}]), encoding="utf-8")
            run = lambda *a: subprocess.run([sys.executable, str(SCRIPT), *a], capture_output=True, text=True)
            proc = run("plan-actions", "--state", str(path), "--project", "P-0001", "--file", str(msg), "--running", "w-0020")
            self.assertEqual(json.loads(proc.stdout)[0]["kind"], "stale_notify")
            proc = run("parse-project-report", "--file", str(msg), "--expect-project", "P-0001")
            self.assertEqual(proc.returncode, 0)
            self.assertIn("| w-0020 |", run("roster", "--state", str(path), "--project", "P-0001").stdout)
            self.assertEqual(json.loads(run("waits", "--state", str(path), "--now", NOW).stdout), [])
            rows = Path(d) / "rows.json"
            rows.write_text(json.dumps([]), encoding="utf-8")
            proc = run("manager-reconcile", "--state", str(path), "--project", "P-0001", "--rows", str(rows))
            self.assertEqual(json.loads(proc.stdout), [{"decision": "missing", "session_id": "sess_mgr_P-0001"}])
            self.assertEqual(run("roster", "--state", str(path), "--project", "P-0009").returncode, 1)


class ProjectReviewFixesTest(unittest.TestCase):
    def setUp(self):
        self.state = fixture_state()
        add_project(self.state, last_review=1)
        w = project_worker(self.state)
        w["last_report"], w["review"] = rep("m1"), review()

    def test_project_id_must_not_change(self):
        cand = copy.deepcopy(self.state)
        t = find(cand["tasks"], "T-0020")
        del t["project_id"]
        del find(cand["workers"], "w-0020")["review"]
        with self.assertRaises(st.StateError) as ctx:
            st.next_state(self.state, cand, NOW)
        self.assertIn("task T-0020: project_id must not change", str(ctx.exception))

    def test_cancelled_task_verdicts_are_silent(self):
        find(self.state["tasks"], "T-0020")["cancel"] = {"at": NOW, "reason": "stop"}
        report = st.parse_project_report(project_report(reviews=[{"review_id": "R-0001", "verdict": "approve"}]))["report"]
        self.assertEqual([a["kind"] for a in st.plan_actions(self.state, "P-0001", report)], ["stale_silent"])
        self.assertIn("(none)", st.roster(self.state, "P-0001").split("### Open reviews")[1])

    def test_malformed_values_are_rejected_cleanly(self):
        with self.assertRaises(st.StateError):
            st.waits(self.state, "not a time")
        bad = copy.deepcopy(self.state)
        bad["projects"][0]["id"] = 5
        self.assertRegex("; ".join(st.validate(bad)), "projects\\[0\\] must be an object with an id")
        bad = copy.deepcopy(self.state)
        find(bad["workers"], "w-0020")["review"]["id"] = 7
        self.assertRegex("; ".join(st.validate(bad)), "review.id must be R-NNNN")
        bad = copy.deepcopy(self.state)
        find(bad["workers"], "w-0020")["last_report"] = "oops"
        self.assertIn("last_report must be an object or null", "; ".join(st.validate(bad)))
        bad = copy.deepcopy(self.state)
        bad["projects"][0]["user_actions"] = [{"id": "UA-0900"}]
        self.assertIn("user_action requires id and a description string", "; ".join(st.validate(bad)))
        result = st.parse_project_report(project_report(reviews=[{"review_id": 3, "verdict": "approve"}]))
        self.assertFalse(result["ok"])

    def test_archived_foreign_session_is_not_reported(self):
        m = self.state["projects"][0]["manager"]
        rows = [{"sessionId": m["session_id"], "isRunning": False, "isArchived": False},
                {"sessionId": "sess_rogue", "startedBy": m["session_id"], "isArchived": True}]
        self.assertEqual([d["decision"] for d in st.manager_reconcile(m, rows)], ["update"])


class ProjectGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.sup = self.root / "sup"
        self.sup.mkdir()
        self.md = self.sup / "CLAUDE.md"
        self.work = self.root / "work"
        self.md.write_text(f"# Supervisor\n\n## Locations\n\n- clone_root: {self.root / 'clones'}\n- work_root: {self.work}\n", encoding="utf-8")
        (self.sup / ".supervisor").mkdir()
        shutil.copyfile(SCRIPT, self.sup / ".supervisor" / "state_tool.py")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = st.main(["prepare-dir", "--claude-md", str(self.md), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_hook_command(self):
        cmd = st.guard_hook_command("/s/state_tool.py", "/p dir", "manager")
        self.assertEqual(cmd, "python3 /s/state_tool.py guard --dir '/p dir' --role manager; [ $? -eq 3 ] && exit 2; exit 0")

    def test_prepare_project_dir_and_guard_exit_codes(self):
        dest = self.work / "P-0001-nature-mcp"
        settings = dest / ".claude" / "settings.json"
        code, out, _ = self.run_cli("--project", "P-0001-nature-mcp")
        self.assertEqual((code, out), (0, f"created: {dest}\ncreated: {settings}\n"))
        command = json.loads(settings.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertIn("--role manager", command)
        commit = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": str(dest)})
        proc = subprocess.run(["sh", "-c", command], input=commit, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("The project manager only reads", proc.stderr)
        read = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git log -1"}, "cwd": str(dest)})
        self.assertEqual(subprocess.run(["sh", "-c", command], input=read, capture_output=True, text=True).returncode, 0)
        self.assertEqual(self.run_cli("--project", "P-0001-nature-mcp")[1], f"exists: {dest}\nexists: {settings}\n")

    def test_existing_hooks_are_kept(self):
        dest = self.work / "P-0002-x"
        settings = dest / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}]}}))
        code, out, _ = self.run_cli("--project", "P-0002-x")
        self.assertEqual((code, out), (0, f"exists: {dest}\nupdated: {settings}\n"))
        entries = json.loads(settings.read_text())["hooks"]["PreToolUse"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["hooks"][0]["command"], "true")

    def test_invalid_names_and_outside_root(self):
        self.assertEqual(self.run_cli("--project", "T-0001-x")[0], 1)
        self.assertEqual(self.run_cli("--project", "P-0001-Bad")[0], 1)
        link = self.work / "P-0003-out"
        self.work.mkdir()
        link.symlink_to(self.root)
        code, _, err = self.run_cli("--project", "P-0003-out")
        self.assertEqual(code, 1)
        self.assertIn("resolves outside", err)

    def test_manager_block_message(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": "/p"}
        self.assertIn("The project manager only reads", st.guard_decision(payload, "/p", {}, "manager"))
        self.assertIn("The supervisor does not change repositories", st.guard_decision(payload, "/p", {}))


class SubcommandSurfaceTest(unittest.TestCase):
    def test_project_subcommands_exist(self):
        parser = st.build_parser()
        names = set(next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices)
        self.assertTrue({"setup", "commit", "digest", "parse-report", "guard", "prepare-dir", "check-cwd", "follow-ups",
                         "validate-follow-ups", "version", "parse-project-report", "plan-actions", "roster", "waits",
                         "manager-reconcile"} <= names, names)


class ProjectDigestTest(unittest.TestCase):
    def test_digest_projects(self):
        plain = st.digest(fixture_state(), "/s/state.json")
        self.assertNotIn("projects:", plain)
        state = fixture_state()
        add_project(state, last_review=1)["user_actions"] = [{"id": "UA-0900", "description": "Answer it", "since": NOW}]
        w = project_worker(state)
        w["last_report"], w["review"] = rep("m1"), review()
        text = st.digest(state, "/s/state.json")
        self.assertIn("- P-0001 Nature MCP | manager sess_mgr_P-0001 idle | doc https://github.com/org/repo/issues/1 | open reviews: R-0001 | tasks: T-0020", text)
        self.assertIn("- P-0001 UA-0900: Answer it", text)
        self.assertIn("- [P-0001] T-0020 [", text)
        self.assertLess(text.index("\nprojects:"), text.index("\ntasks:"))



def investigation_task(state, task_id, worker_id, kind="non-git", cwd="/data/inv"):
    if kind == "git":
        git_task(state, task_id, worker_id)
    else:
        nongit_task(state, task_id, worker_id, cwd, "running", "idle", session_id="sess_" + worker_id)
    task = find(state["tasks"], task_id)
    task["investigation"] = True
    return task


def finding(wid, ref="m1", summary="Cause: retry is skipped on 429"):
    return {"worker_id": wid, "session_id": "sess_" + wid, "title": f"T-0090|{wid} survey",
            "summary": summary, "message_ref": ref, "at": NOW}


def read_action(wid, ua_id="UA-0200"):
    return {"id": ua_id, "since": NOW,
            "description": f"Read the result of {wid} (T-0090|{wid} survey) in its session, then tell me or the worker that you have read it"}


class InvestigationTest(unittest.TestCase):
    def setUp(self):
        self.current = fixture_state()

    def assertRejected(self, candidate, pattern, current=None):
        with self.assertRaises(st.StateError) as ctx:
            st.next_state(current or self.current, candidate, NOW)
        self.assertRegex(str(ctx.exception), pattern)

    def assertInvalid(self, state, pattern):
        errors = st.validate(state)
        self.assertTrue(any(re.search(pattern, e) for e in errors), errors)

    def held(self, kind="non-git"):
        """An investigation worker verified and held for reading: findings, a read action, still running."""
        state = fixture_state()
        task = investigation_task(state, "T-0090", "w-0090", kind)
        w = find(state["workers"], "w-0090")
        if kind == "git":
            commit_artifacts(state, "T-0090")
            w["session_id"] = "sess_w-0090"
        else:
            task["artifacts"] = [{"kind": "file", "label": "notes", "path": "/data/inv/report.md"}]
        w["last_report"] = rep("m1")
        task["findings"] = [finding("w-0090")]
        task["user_actions"] = [read_action("w-0090")]
        return state

    # Stage 1: recording and carry-over

    def test_findings_invariants(self):
        state = fixture_state()
        investigation_task(state, "T-0090", "w-0090")["findings"] = [finding("w-0090")]
        self.assertEqual(st.validate(state), [])
        named = "findings w-0090 must name a worker of an investigation task by its session_id, once"
        shape = "findings must be a list of"
        for mutate, pattern in (
            (lambda t: t.pop("investigation"), named),
            (lambda t: t["findings"][0].update(session_id="sess_other"), named),
            (lambda t: t["findings"][0].update(worker_id="w-0999"), "findings w-0999 must name"),
            (lambda t: t["findings"].append(finding("w-0090")), named),
            (lambda t: t["findings"][0].pop("message_ref"), shape),
            (lambda t: t["findings"][0].update(summary=""), shape),
            (lambda t: t.update(findings="x"), shape),
            (lambda t: t.update(investigation="yes"), "investigation must be true when present"),
        ):
            bad = copy.deepcopy(state)
            mutate(find(bad["tasks"], "T-0090"))
            self.assertInvalid(bad, pattern)
        stub = {"id": "T-0091", "title": "old", "request": "x", "artifacts": [], "archived_at": NOW,
                "investigation": True, "findings": [finding("w-0999")]}
        state["archived"].insert(0, stub)
        self.assertEqual(st.validate(state), [])
        stub["findings"][0]["title"] = ""
        self.assertInvalid(state, r"archived T-0091: findings must be a list of")

    def test_findings_and_investigation_carried_into_stub_and_concatenated(self):
        investigation_task(self.current, "T-0090", "w-0090")
        cand = copy.deepcopy(self.current)
        task = find(cand["tasks"], "T-0090")
        task["no_result"], task["findings"] = [no_result("w-0090")], [finding("w-0090")]
        find(cand["workers"], "w-0090").update(intended_state="archived", observed_state="archived")
        new = st.next_state(self.current, cand, NOW)
        stub = find(new["archived"], "T-0090")
        self.assertEqual((stub["investigation"], stub["findings"]), (True, [finding("w-0090")]))

        investigation_task(new, "T-0090", "w-0091")
        cand = copy.deepcopy(new)
        task = find(cand["tasks"], "T-0090")
        task["no_result"], task["findings"] = [no_result("w-0091")], [finding("w-0091", "m2")]
        find(cand["workers"], "w-0091").update(intended_state="archived", observed_state="archived")
        new = st.next_state(new, cand, NOW)
        self.assertEqual(find(new["archived"], "T-0090")["findings"], [finding("w-0090"), finding("w-0091", "m2")])

        nongit_task(self.current, "T-0092", "w-0092", "/data/plain", "running", "idle")
        cand = copy.deepcopy(self.current)
        find(cand["tasks"], "T-0092")["no_result"] = [no_result("w-0092")]
        find(cand["workers"], "w-0092").update(intended_state="archived", observed_state="archived")
        stub = find(st.next_state(self.current, cand, NOW)["archived"], "T-0092")
        self.assertNotIn("findings", stub)
        self.assertNotIn("investigation", stub)

    def test_read_confirmed_in_report_and_last_report(self):
        def parse(status, **extra):
            report = dict({"task_id": "T-1", "worker_id": "w-1", "status": status, "summary": "s", "artifacts": {},
                           "blockers": [], "next_action": ""}, **extra)
            return st.parse_report("```supervisor-report\n" + json.dumps(report) + "\n```")
        self.assertTrue(parse("done", read_confirmed=True)["ok"])
        self.assertTrue(parse("done", read_confirmed=False)["ok"])
        self.assertTrue(parse("done")["ok"])
        self.assertEqual(parse("done", read_confirmed="true")["error"], "read_confirmed must be a boolean")
        self.assertEqual(parse("in_progress", read_confirmed=True)["error"], "read_confirmed is allowed only with status done")

        state = self.held()
        w = find(state["workers"], "w-0090")
        w["last_report"]["read_confirmed"] = True
        self.assertEqual(st.validate(state), [])
        w["last_report"]["read_confirmed"] = "true"
        self.assertInvalid(state, "w-0090: last_report read_confirmed must be a boolean")
        w["last_report"].update(read_confirmed=True, status="in_progress")
        self.assertInvalid(state, "w-0090: last_report read_confirmed is allowed only with status done")

    def test_derived_from_shape(self):
        state = fixture_state()
        nongit_task(state, "T-0093", "w-0093", "/data/fix", "running", "idle")
        task = find(state["tasks"], "T-0093")
        task["derived_from"] = {"task_id": "T-0001-gone", "context": "Investigated: retry is skipped on 429"}
        self.assertEqual(st.validate(state), [])
        for value in ({"task_id": "T-0090"}, {"task_id": "", "context": "c"}, "T-0090", []):
            task["derived_from"] = value
            self.assertInvalid(state, "task T-0093: derived_from must be {task_id, context} with non-empty strings")

    # Stage 2: holding for the read confirmation

    def test_hold_after_verification_derives_needs_user(self):
        state = self.held()
        new = st.next_state(fixture_state(), state, NOW)
        self.assertEqual(find(new["tasks"], "T-0090")["status"], "needs_user")
        self.assertEqual(find(new["workers"], "w-0090")["intended_state"], "running")

    def test_hold_with_no_result_and_fail_closed(self):
        state = self.held("git")
        task = find(state["tasks"], "T-0090")
        task["artifacts"], task["no_result"] = [], [no_result("w-0090")]
        st.next_state(fixture_state(), state, NOW)
        task["user_actions"] = []
        self.assertRejected(state, "no_result w-0090 must name an archived, non-replaced worker of the task once",
                            current=fixture_state())

    def test_read_action_invariants(self):
        pattern = "read action for w-0090 needs a live"
        base = self.held()
        self.assertEqual(st.validate(base), [])
        other = copy.deepcopy(base)
        # w-0090 has a findings entry here too, so the read action fails on task membership alone.
        investigation_task(other, "T-0095", "w-0095", cwd="/data/inv2")["findings"] = [finding("w-0095"), finding("w-0090")]
        find(other["tasks"], "T-0095")["user_actions"] = [read_action("w-0090", "UA-0201")]
        self.assertInvalid(other, pattern)
        for mutate in (
            lambda s: find(s["tasks"], "T-0090").pop("investigation"),
            lambda s: find(s["tasks"], "T-0090").update(findings=[]),
            lambda s: find(s["tasks"], "T-0090")["user_actions"].append(read_action("w-0090", "UA-0201")),
            lambda s: find(s["workers"], "w-0090").update(intended_state="archived"),
            lambda s: find(s["workers"], "w-0090").update(observed_state="archived"),
            lambda s: find(s["workers"], "w-0090").update(observed_state="missing"),
        ):
            bad = copy.deepcopy(base)
            mutate(bad)
            self.assertInvalid(bad, pattern)

        state = fixture_state()
        add_project(state, last_review=1)
        project_worker(state, "T-0090", "w-0090")
        task = find(state["tasks"], "T-0090")
        commit_artifacts(state, "T-0090")
        task.update(investigation=True, findings=[finding("w-0090")], user_actions=[read_action("w-0090")])
        w = find(state["workers"], "w-0090")
        w["last_report"] = rep("m1")
        for review_state, ok in (("requested", False), ("approved", True)):
            w["review"] = review(state=review_state)
            if ok:
                self.assertEqual(st.validate(state), [])
            else:
                self.assertInvalid(state, pattern)
        w["review"] = review(state="waived", reason="Review not needed")
        self.assertEqual(st.validate(state), [])

    def test_resolution_archives_and_requires_push_decision_for_unpushed_commit(self):
        current = self.held()
        cand = copy.deepcopy(current)
        find(cand["tasks"], "T-0090")["user_actions"] = []
        find(cand["workers"], "w-0090")["intended_state"] = "archived"
        new = st.next_state(current, cand, NOW)
        cand = copy.deepcopy(new)
        find(cand["workers"], "w-0090")["observed_state"] = "archived"
        new = st.next_state(new, cand, NOW)
        self.assertEqual(find(new["archived"], "T-0090")["findings"], [finding("w-0090")])

        current = self.held("git")
        cand = copy.deepcopy(current)
        find(cand["tasks"], "T-0090")["user_actions"] = []
        find(cand["workers"], "w-0090")["intended_state"] = "archived"
        self.assertRejected(cand, "commit delivery archive requires a push-decision user_action", current=current)
        push_decision(cand, "T-0090")
        st.next_state(current, cand, NOW)

    def test_held_missing_worker_finishes_task(self):
        current = self.held()
        cand = copy.deepcopy(current)
        find(cand["tasks"], "T-0090")["user_actions"] = []
        find(cand["workers"], "w-0090").update(intended_state="archived", observed_state="missing")
        new = st.next_state(current, cand, NOW)
        self.assertNotIn("T-0090", [t["id"] for t in new["tasks"]])
        self.assertEqual(find(new["archived"], "T-0090")["findings"], [finding("w-0090")])

        cand = copy.deepcopy(current)
        task = find(cand["tasks"], "T-0090")
        task["user_actions"], task["findings"] = [], []
        find(cand["workers"], "w-0090").update(intended_state="archived", observed_state="missing")
        self.assertEqual(find(st.next_state(current, cand, NOW)["tasks"], "T-0090")["status"], "needs_user")

    def test_project_hold_review_cycle(self):
        current = fixture_state()
        add_project(current, last_review=1)
        project_worker(current, "T-0090", "w-0090")
        commit_artifacts(current, "T-0090")
        task = find(current["tasks"], "T-0090")
        task.update(investigation=True, findings=[finding("w-0090")], user_actions=[read_action("w-0090")])
        w = find(current["workers"], "w-0090")
        w["last_report"], w["review"] = rep("m1"), review(state="approved")
        self.assertEqual(st.validate(current), [])  # (a)

        # (b) a later done returns to review: a new id, and the read action leaves with it.
        cand = copy.deepcopy(current)
        cand["projects"][0]["last_review_number"] = 2
        cw = find(cand["workers"], "w-0090")
        cw["last_report"], cw["review"] = rep("m2"), review("R-0002", "requested", "m2")
        self.assertRejected(cand, "read action for w-0090 needs a live", current=current)
        find(cand["tasks"], "T-0090")["user_actions"] = []
        st.next_state(current, cand, NOW)

        def resolve(review_value, observed):
            cand = copy.deepcopy(current)
            find(cand["tasks"], "T-0090")["user_actions"] = []
            push_decision(cand, "T-0090")
            cw = find(cand["workers"], "w-0090")
            cw.update(last_report=rep("m2"), review=review_value, intended_state="archived", observed_state=observed)
            return cand

        # (c) archiving on an approval of an older report fails the approval gate.
        self.assertRejected(resolve(review(state="approved"), "archived"),
                            "archived project worker needs an approved review of its latest report", current=current)
        # (d) closing the held session waives the same review id, so only the approved findings reach the stub.
        reason = "session closed while holding R-0001; later turns were not reviewed"
        for observed in ("archived", "missing"):
            cand = resolve(review(state="waived", reason=reason), observed)
            ctask = find(cand["tasks"], "T-0090")
            ctask["user_actions"] = []
            ctask["artifacts"][1]["url"] = "https://github.com/org/repo-a/commit/abc123"
            new = st.next_state(current, cand, NOW)
            self.assertEqual(find(new["archived"], "T-0090")["findings"], [finding("w-0090")])



NOTES = FIXTURES / "notes"
NOTE_NOW = "2026-11-10T09:00:00+09:00"


def note(name):
    return (NOTES / f"{name}.md").read_text(encoding="utf-8")


def note_section(text, title):
    return st.parse_sections(st.parse_frontmatter(text)[1])[title]


def note_uas(text):
    return {ua["id"]: ua for ua in st.parse_user_actions(note_section(text, "ユーザーアクション"))}


def apply_twice(test, text, ops, rev=13):
    once, ids, problems = st.apply_note_ops(text, ops, NOTE_NOW, rev)
    twice, ids2, _ = st.apply_note_ops(once, ops, NOTE_NOW, rev)
    test.assertEqual(twice, once)
    test.assertEqual(ids2, [])
    return once, ids, problems


class FrontmatterTest(unittest.TestCase):
    def test_reads_obsidian_reserialized_shapes(self):
        props, body, errors = st.parse_frontmatter(note("task_obsidian"))
        self.assertEqual(errors, [])
        self.assertEqual(props["uid"], "20250418_081420")
        self.assertIsNone(props["aliases"])
        self.assertIsNone(props["archived_at"])
        self.assertEqual(props["tags"], ["task", "hobby"])
        self.assertIsNone(props["due_date"])
        self.assertEqual(props["completed_at"], "2025-05-24T16:36:00")
        self.assertEqual(props["status"], "done")
        self.assertEqual(props["parent"], ["2025/04/20250418_080000.md"])
        self.assertTrue(body.startswith("## 概要\n"))

    def test_reads_json_shapes(self):
        props, _, errors = st.parse_frontmatter(note("task_managed"))
        self.assertEqual(errors, [])
        self.assertEqual(list(props), [k for k in st.NOTE_KEYS if k not in ("task_id", "project_id", "follows")])
        self.assertEqual((props["uid"], props["aliases"], props["tags"]), ("20261109_221500", [], ["task", "oss"]))
        self.assertEqual((props["status"], props["assignee"], props["parent"]), ("in progress", "user", "[[20261101_090000]]"))
        self.assertEqual(props["created_at"], "2026-11-09 22:15:00")

    def test_outside_the_subset_is_an_error_and_never_written(self):
        text = note("note_unsupported")
        _, _, errors = st.parse_frontmatter(text)
        self.assertEqual(len(errors), 3, errors)
        with self.assertRaises(st.StateError):
            st.set_props(text, {"status": "done"})
        self.assertEqual(st.parse_frontmatter("## 概要\n")[2], ["missing frontmatter"])
        self.assertEqual(st.parse_frontmatter("---\ntitle: x\n")[2], ["frontmatter is not closed"])
        self.assertTrue(st.parse_frontmatter('---\nuid: "a"\nuid: "b"\n---\n')[2])

    def test_set_props_changes_only_the_target_lines(self):
        text = note("task_obsidian")
        out = st.set_props(text, {"status": "in progress", "assignee": "agent"})
        self.assertEqual(out, text.replace("status: done\n", 'status: "in progress"\nassignee: "agent"\n'))

    def test_set_props_keeps_equal_values_byte_identical(self):
        text = note("task_obsidian")
        self.assertEqual(st.set_props(text, {"status": "done", "tags": ["task", "hobby"], "archived_at": None}), text)

    def test_set_props_inserts_missing_keys_in_note_key_order(self):
        text = note("task_obsidian")
        out = st.set_props(text, {"follows": "[[20250101_000000]]", "pending_until": "2026-01-01"})
        lines = out.splitlines()
        self.assertEqual(lines[lines.index("due_date:") + 1], 'pending_until: "2026-01-01"')
        self.assertEqual(lines[lines.index("  - 2025/04/20250418_080000.md") + 1], 'follows: "[[20250101_000000]]"')
        self.assertEqual(lines[lines.index('follows: "[[20250101_000000]]"') + 1], "")
        self.assertEqual(st.parse_frontmatter(out)[2], [])

    def test_set_props_replaces_a_block_list_and_writes_raw_and_json_values(self):
        text = note("task_obsidian")
        out = st.set_props(text, {"tags": ["task"], "archived_at": "2026-11-10 09:00:00", "completed_at": "2026-11-10"})
        self.assertIn('tags: ["task"]\n\ndue_date:', out)
        self.assertIn("archived_at: 2026-11-10 09:00:00\n", out)
        self.assertIn('completed_at: "2026-11-10"\n', out)
        self.assertNotIn("  - hobby", out)
        self.assertEqual(st.parse_frontmatter(out)[0]["archived_at"], "2026-11-10 09:00:00")
        with self.assertRaises(st.StateError):
            st.set_props(text, {"owner": "x"})

    def test_parse_sections_ignores_fenced_headings_and_keeps_h3(self):
        sections = st.parse_sections(st.parse_frontmatter(note("task_obsidian"))[1])
        self.assertEqual(list(sections), ["概要", "サブタスク", "関連ノート", "関連資料", "ログ"])
        self.assertIn("## not a heading inside a fence", sections["サブタスク"])
        self.assertIn("### メモ", note_section(note("task_managed"), "ユーザーアクション"))


class NoteTemplateTest(unittest.TestCase):
    def test_task_note_follows_the_create_task_format(self):
        text = st.new_note_text("20261110_090000", "README の typo を直す", "task", "in progress", "agent", NOTE_NOW,
                                "typo を直してください。\n\n- README.md", parent="20261101_090000")
        self.assertTrue(text.startswith('---\nuid: "20261110_090000"\ntitle: "README の typo を直す"\npara: "Project"\n'
                                        "aliases: []\ncreated_at: 2026-11-10 09:00:00\narchived_at: null\n"
                                        'tags: ["task"]\n'))
        props, body, errors = st.parse_frontmatter(text)
        self.assertEqual(errors, [])
        self.assertEqual(list(props), [k for k in st.NOTE_KEYS if k not in ("task_id", "project_id", "follows")])
        self.assertEqual((props["status"], props["assignee"], props["parent"]), ("in progress", "agent", "[[20261101_090000]]"))
        self.assertFalse(re.search(r"^# ", body, re.M))
        sections = st.parse_sections(body)
        self.assertEqual(list(sections), list(st.NOTE_SECTIONS))
        self.assertEqual(sections["概要"].strip(), "typo を直してください。\n\n- README.md")

    def test_project_note_and_optional_links(self):
        text = st.new_note_text("20261110_090001", "Nature MCP", "project", "backlog", "user", NOTE_NOW)
        props = st.parse_frontmatter(text)[0]
        self.assertEqual(props["tags"], ["project"])
        self.assertNotIn("parent", props)
        self.assertNotIn("follows", props)
        follows = st.parse_frontmatter(st.new_note_text("u", "t", "task", "backlog", "agent", NOTE_NOW, follows="20261101_000000"))[0]
        self.assertEqual(follows["follows"], "[[20261101_000000]]")
        for args in (("u", "t", "memo", "backlog", "agent"), ("u", "t", "task", "new", "agent"), ("u", "t", "task", "backlog", "bot")):
            with self.assertRaises(st.StateError):
                st.new_note_text(*args, NOTE_NOW)

    def test_create_op_writes_only_into_an_empty_note(self):
        op = {"op": "create", "uid": "20261110_090000", "title": "t", "kind": "task", "status": "in progress",
              "assignee": "agent", "request": "依頼"}
        text, ids, problems = apply_twice(self, "", [op, {"op": "log", "text": "受領しました"}])
        self.assertEqual(problems, [])
        self.assertEqual(ids, ["sv-log-r13-1"])
        self.assertTrue(text.startswith(st.new_note_text("20261110_090000", "t", "task", "in progress", "agent", NOTE_NOW, "依頼")[:200]))
        self.assertIn("- 2026-11-10 09:00 受領しました ^sv-log-r13-1", note_section(text, "ログ"))
        existing = note("task_managed")
        self.assertEqual(st.apply_note_ops(existing, [op], NOTE_NOW, 13)[0], existing)

    def test_assign_uid_only_when_missing(self):
        text = note("note_without_uid")
        out, _, problems = apply_twice(self, text, [{"op": "assign_uid", "uid": "20261110_090000"}])
        self.assertEqual(problems, [])
        self.assertEqual(out, text.replace("---\n", '---\nuid: "20261110_090000"\n', 1))
        managed = note("task_managed")
        self.assertEqual(st.apply_note_ops(managed, [{"op": "assign_uid", "uid": "x"}], NOTE_NOW, 13)[0], managed)
        _, _, problems = st.apply_note_ops(note("note_unsupported").replace('uid: "20261109_230000"\n', ""),
                                           [{"op": "assign_uid", "uid": "x"}], NOTE_NOW, 13)
        self.assertEqual(len(problems), 1)

    def test_log_entries_append_once_and_keep_the_rest(self):
        text = note("task_obsidian")
        out, ids, _ = apply_twice(self, text, [{"op": "log", "text": "検証しました\n詳細"}, {"op": "log", "id": "sv-log-x", "text": "次"}])
        self.assertEqual(ids, ["sv-log-r13-0", "sv-log-x"])
        self.assertEqual(out, text + "- 2026-11-10 09:00 検証しました ^sv-log-r13-0\n    詳細\n- 2026-11-10 09:00 次 ^sv-log-x\n")
        with self.assertRaises(st.StateError):
            st.apply_note_ops(text, [{"op": "nope"}], NOTE_NOW, 13)


class UserActionChecklistTest(unittest.TestCase):
    def test_kinds_and_open_state(self):
        uas = note_uas(note("task_managed"))
        self.assertEqual(list(uas), ["UA-0160", "UA-0161", "UA-0162", "UA-0163", "UA-0164"])
        self.assertEqual((uas["UA-0160"]["kind"], uas["UA-0160"]["answer_required"]), ("ask", True))
        self.assertEqual((uas["UA-0161"]["kind"], uas["UA-0161"]["detector"]), ("auto", "pr_closed"))
        self.assertEqual(uas["UA-0161"]["url"], "https://github.com/org/repo-a/pull/42")
        self.assertEqual(uas["UA-0162"]["kind"], "slot")
        self.assertEqual({k: st.ua_open(v) for k, v in uas.items()},
                         {"UA-0160": True, "UA-0161": True, "UA-0162": False, "UA-0163": False, "UA-0164": True})
        self.assertEqual({k: v["checked"] for k, v in uas.items()},
                         {"UA-0160": False, "UA-0161": True, "UA-0162": False, "UA-0163": True, "UA-0164": False})
        self.assertEqual((uas["UA-0163"]["done"], uas["UA-0163"]["done_rev"]), ("2026-11-09T22:40:00+09:00", 12))
        self.assertEqual(uas["UA-0164"]["answer"], "")

    def test_sub_items_are_read_verbatim(self):
        uas = note_uas(note("task_managed"))
        self.assertEqual(uas["UA-0160"]["answer"], "- push してください\n\n    - main への PR も作ってください")

    def test_render_round_trip(self):
        for ua in note_uas(note("task_managed")).values():
            line = st.render_ua_line(ua)
            self.assertEqual(st.parse_user_actions(line)[0], dict(ua, answer=""))
        self.assertIn(st.render_ua_line(note_uas(note("task_managed"))["UA-0163"]),
                      note_section(note("task_managed"), "ユーザーアクション"))

    def test_ua_add_is_idempotent_by_block_id_and_keeps_the_list_together(self):
        text = note("task_managed")
        op = {"op": "ua_add", "ua": {"id": "UA-0165", "kind": "auto", "detector": "worker_report",
                                     "description": "Answer w-0120 (which file?) in the app: which file?"}}
        out, ids, _ = apply_twice(self, text, [op])
        self.assertEqual(ids, ["ua-0165"])
        section = note_section(out, "ユーザーアクション")
        self.assertLess(section.index("^ua-0165"), section.index("自分用のチェック"))
        self.assertEqual(note_uas(out)["UA-0165"]["since"], NOTE_NOW)
        self.assertEqual(out.replace(st.render_ua_line(note_uas(out)["UA-0165"]) + "\n", ""), text)
        for bad in ({"id": "UA-0166", "kind": "auto", "detector": "nope", "description": "x"},
                    {"id": "UA-0166", "kind": "ask", "description": "a\nb"}, {"id": "166", "kind": "slot", "description": "x"}):
            with self.assertRaises(st.StateError):
                st.apply_note_ops(text, [{"op": "ua_add", "ua": bad}], NOTE_NOW, 13)

    def test_ua_add_creates_the_section_in_template_order(self):
        text = note("task_obsidian")
        out, _, _ = apply_twice(self, text, [{"op": "ua_add", "ua": {"id": "UA-0001", "kind": "slot", "description": "再開時の指示"}}])
        self.assertEqual(list(st.parse_sections(st.parse_frontmatter(out)[1]))[:3], ["概要", "ユーザーアクション", "サブタスク"])
        self.assertEqual(list(note_uas(out)), ["UA-0001"])
        self.assertIn("- [x] M3x10 (4pcs)\n\n## ユーザーアクション\n\n- [ ] UA-0001 ", out)

    def test_check_and_ack_leave_rev_marks(self):
        text = note("task_managed")
        out, _, problems = apply_twice(self, text, [{"op": "ua_check", "id": "UA-0164"}, {"op": "ua_ack", "id": "UA-0164"}])
        self.assertEqual(problems, [])
        ua = note_uas(out)["UA-0164"]
        self.assertEqual((ua["checked"], ua["chk_rev"], ua["done"], ua["done_rev"]), (True, 13, NOTE_NOW, 13))
        self.assertFalse(st.ua_open(ua))
        self.assertEqual(out.replace("- [x] UA-0164", "- [/] UA-0164").replace(f" chk=r13 done={NOTE_NOW} r13", ""), text)

    def test_a_box_the_user_unchecked_is_not_checked_again(self):
        ops = [{"op": "ua_check", "id": "UA-0160"}]
        out = st.apply_note_ops(note("task_managed"), ops, NOTE_NOW, 13)[0]
        unchecked = out.replace("- [x] UA-0160", "- [ ] UA-0160")
        self.assertEqual(st.apply_note_ops(unchecked, ops, NOTE_NOW, 13)[0], unchecked)
        self.assertTrue(note_uas(st.apply_note_ops(unchecked, ops, NOTE_NOW, 14)[0])["UA-0160"]["checked"])

    def test_ack_keeps_an_existing_done_mark(self):
        text = note("task_managed")
        self.assertEqual(st.apply_note_ops(text, [{"op": "ua_ack", "id": "UA-0163"}], NOTE_NOW, 13)[0], text)

    def test_answer_appends_after_the_users_sub_items(self):
        text = note("task_managed")
        out, _, _ = apply_twice(self, text, [{"op": "ua_answer", "id": "UA-0160", "text": "やはり push しないでください\n理由は後で"}])
        self.assertEqual(note_uas(out)["UA-0160"]["answer"],
                         "- push してください\n\n    - main への PR も作ってください\n- やはり push しないでください\n  理由は後で")
        self.assertEqual(out.replace("    - やはり push しないでください\n      理由は後で\n", ""), text)

    def test_missing_items_are_problems(self):
        text = note("task_managed")
        for name in ("ua_check", "ua_ack", "ua_answer"):
            out, _, problems = st.apply_note_ops(text, [{"op": name, "id": "UA-0999", "text": "x"}], NOTE_NOW, 13)
            self.assertEqual(out, text)
            self.assertEqual(problems, [f"{name} UA-0999: item not found"])

    def test_text_outside_managed_sections_is_kept(self):
        text = note("task_managed")
        ops = [{"op": "ua_add", "ua": {"id": "UA-0165", "kind": "ask", "answer": True, "description": "q"}},
               {"op": "ua_check", "id": "UA-0161"}, {"op": "ua_ack", "id": "UA-0161"}, {"op": "log", "text": "済"},
               {"op": "art_add", "items": [{"n": 4, "kind": "ci", "url": "https://ci.example.com/4"}]}]
        out = st.apply_note_ops(text, ops, NOTE_NOW, 13)[0]
        before, after = st.parse_frontmatter(text), st.parse_frontmatter(out)
        self.assertEqual(after[0], before[0])
        self.assertTrue(after[1].startswith("ユーザーが見出しの外に書いたメモ。\n\n## 概要\n"))
        for title in ("概要", "サブタスク", "関連ノート", "関連資料"):
            self.assertEqual(st.parse_sections(after[1])[title], st.parse_sections(before[1])[title])
        self.assertIn("### メモ\n\nユーザーの小見出し。", out)
        self.assertEqual(note_uas(out)["UA-0160"]["answer"], note_uas(text)["UA-0160"]["answer"])


BRANCH_REF = "/Users/example/src/github.com/org/repo-a#claude/t0120-fix-typo"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"


class ArtifactSectionTest(unittest.TestCase):
    def section(self, text):
        return note_section(text, "成果物")

    def test_reads_metadata_in_g_n_order_and_skips_user_lines(self):
        items, problems = st.parse_artifacts(self.section(note("task_managed")))
        self.assertEqual(problems, [])
        self.assertEqual([(i["g"], i["n"], i["kind"]) for i in items], [(1, 1, "branch"), (1, 2, "commit"), (3, 3, "pr")])
        self.assertEqual(items[0]["ref"], BRANCH_REF)
        self.assertEqual(st.artifacts_view(items), [
            {"kind": "branch", "ref": BRANCH_REF, "url": "https://github.com/org/repo-a/tree/claude/t0120-fix-typo",
             "label": "repo-a claude/t0120-fix-typo"},
            {"kind": "commit", "ref": COMMIT_SHA, "url": f"https://github.com/org/repo-a/commit/{COMMIT_SHA}", "label": "repo-a 0123456789"},
            {"kind": "pr", "url": "https://github.com/org/repo-a/pull/42", "label": "repo-a#42"},
        ])

    def test_reordering_lines_and_editing_display_text_do_not_change_meaning(self):
        section = self.section(note("task_managed"))
        items = st.parse_artifacts(section)[0]
        lines = section.split("\n")
        art = [l for l in lines if "sv-art" in l]
        reordered = "\n".join(reversed(art))
        self.assertEqual(st.parse_artifacts(reordered)[0], items)
        edited = section.replace("[repo-a#42](", "[マージ待ちの PR](")
        self.assertEqual(st.art_sha(st.parse_artifacts(edited)[0]), st.art_sha(items))
        relabeled = [dict(i, label="x") for i in items]
        self.assertEqual(st.art_sha(relabeled), st.art_sha(items))
        self.assertNotEqual(st.art_sha(items[:2]), st.art_sha(items))

    def test_problems_leave_lines_out(self):
        good = st.render_artifact_line({"n": 1, "g": 1, "kind": "pr", "url": "https://github.com/org/repo-a/pull/1"})
        cases = {
            "- x <!-- sv-art {\"n\":2,\"g\":2,\"kind\": --> ^sv-art-2": "invalid sv-art JSON",
            st.render_artifact_line({"n": 2, "g": 2, "kind": "tag", "ref": "v1"}): "unknown kind",
            "- x <!-- sv-art {\"n\":2,\"g\":2,\"kind\":\"pr\"} --> ^sv-art-2": "pr requires an http(s) url",
            st.render_artifact_line({"n": 2, "g": 2, "kind": "file", "path": "rel/path"}): "absolute path",
            st.render_artifact_line({"n": 0, "g": 2, "kind": "ci", "url": "https://ci"}): "n must be a positive integer",
            "- x <!-- sv-art [1] -->": "must be an object",
        }
        for line, message in cases.items():
            items, problems = st.parse_artifacts(f"{good}\n{line}")
            self.assertEqual([i["n"] for i in items], [1], line)
            self.assertEqual(len(problems), 1, problems)
            self.assertIn(message, problems[0])
        items, problems = st.parse_artifacts(f"{good}\n{good}\n" + st.render_artifact_line({"n": 2, "g": 2, "kind": "ci", "url": "https://ci"}))
        self.assertEqual([i["n"] for i in items], [2])
        self.assertEqual(problems, ["lines 1, 2: duplicate n 1"])

    def test_render_escapes_comment_breakers_and_round_trips(self):
        item = {"n": 4, "g": 4, "kind": "pr", "url": "https://github.com/org/repo-a/pull/43?a=1&b=2",
                "label": "<b>--> [x] & y", "r": 3}
        line = st.render_artifact_line(item)
        comment = line[line.index("<!-- sv-art ") + 12:line.rindex(" -->")]
        self.assertNotIn("<", comment)
        self.assertNotIn(">", comment)
        self.assertNotIn("&", comment)
        self.assertTrue(line.endswith(" ^sv-art-4"))
        self.assertTrue(line.startswith("- [<b>--> \\[x\\] & y](https://github.com/org/repo-a/pull/43?a=1&b=2) "))
        self.assertEqual(st.parse_artifacts(line), ([item], []))
        file_line = st.render_artifact_line({"n": 5, "g": 5, "kind": "file", "path": "/w/report.md"})
        self.assertTrue(file_line.startswith("- `/w/report.md` <!-- sv-art "))
        commit_line = st.render_artifact_line({"n": 6, "g": 6, "kind": "commit", "ref": COMMIT_SHA})
        self.assertTrue(commit_line.startswith("- commit 0123456789 <!-- "))

    def test_art_add_is_idempotent_and_creates_the_section(self):
        text = note("task_obsidian")
        items = [{"n": 1, "kind": "branch", "ref": BRANCH_REF}, {"n": 2, "kind": "commit", "ref": COMMIT_SHA}]
        out, ids, _ = apply_twice(self, text, [{"op": "art_add", "items": items}])
        self.assertEqual(ids, ["sv-art-1", "sv-art-2"])
        self.assertEqual(list(st.parse_sections(st.parse_frontmatter(out)[1]))[-2:], ["成果物", "ログ"])
        parsed = st.parse_artifacts(self.section(out))[0]
        self.assertEqual([(i["n"], i["g"], i["r"]) for i in parsed], [(1, 1, 13), (2, 1, 13)])
        with self.assertRaises(st.StateError):
            st.apply_note_ops(text, [{"op": "art_add", "items": [{"n": 3, "kind": "pr"}]}], NOTE_NOW, 13)

    def test_art_set_updates_data_and_keeps_user_link_text(self):
        text = note("task_managed").replace("[repo-a 0123456789](", "[直したコミット](")
        pushed = f"https://github.com/org/repo-a/commit/{COMMIT_SHA}?pushed"
        out, _, problems = apply_twice(self, text, [{"op": "art_set", "n": 2, "fields": {"url": pushed, "label": None}}])
        self.assertEqual(problems, [])
        line = next(l for l in out.split("\n") if l.endswith("^sv-art-2"))
        self.assertTrue(line.startswith(f"- [直したコミット]({pushed}) "))
        item = next(i for i in st.parse_artifacts(self.section(out))[0] if i["n"] == 2)
        self.assertEqual((item["url"], item["r"], "label" in item), (pushed, 13, False))
        moved = text.replace(f"[直したコミット](https://github.com/org/repo-a/commit/{COMMIT_SHA})", "[直したコミット](https://example.com/mine)")
        line = next(l for l in st.apply_note_ops(moved, [{"op": "art_set", "n": 2, "fields": {"url": pushed}}], NOTE_NOW, 13)[0].split("\n")
                    if l.endswith("^sv-art-2"))
        self.assertTrue(line.startswith("- [直したコミット](https://example.com/mine) "))
        out, _, problems = st.apply_note_ops(text, [{"op": "art_set", "n": 9, "fields": {"url": pushed}}], NOTE_NOW, 13)
        self.assertEqual((out, problems), (text, ["art_set 9: artifact line not found"]))

    def test_art_replace_group_keeps_position_and_user_lines(self):
        text = note("task_managed")
        items = [{"n": 4, "kind": "branch", "ref": BRANCH_REF, "url": "https://github.com/org/repo-a/tree/claude/t0120-fix-typo"},
                 {"n": 5, "kind": "commit", "ref": "fedcba9876"}]
        out, ids, _ = apply_twice(self, text, [{"op": "art_replace_group", "g": 1, "items": items}])
        self.assertEqual(ids, ["sv-art-4", "sv-art-5"])
        section = self.section(out)
        self.assertIn("- 自分用のメモ(メタデータの無い行)", section)
        self.assertNotIn("^sv-art-1", section)
        parsed = st.parse_artifacts(section)[0]
        self.assertEqual([(i["g"], i["n"], i["kind"]) for i in parsed], [(1, 4, "branch"), (1, 5, "commit"), (3, 3, "pr")])
        self.assertLess(section.index("^sv-art-5"), section.index("^sv-art-3"))

    def test_non_git_file_group_is_replaced_per_cwd(self):
        text = note("task_obsidian")
        files = [{"n": 1, "kind": "file", "path": "/work/T-0088/a.md"}, {"n": 2, "kind": "file", "path": "/work/T-0088/b.md"}]
        out, _, _ = apply_twice(self, text, [{"op": "art_add", "items": files},
                                             {"op": "art_add", "items": [{"n": 3, "kind": "ci", "url": "https://ci/3"}]}])
        self.assertEqual([(i["g"], i["n"]) for i in st.parse_artifacts(self.section(out))[0]], [(1, 1), (1, 2), (3, 3)])
        out, _, _ = apply_twice(self, out, [{"op": "art_replace_group", "g": 1, "items": [{"n": 4, "kind": "file", "path": "/work/T-0088/c.md"}]}])
        self.assertEqual(st.artifacts_view(st.parse_artifacts(self.section(out))[0]),
                         [{"kind": "file", "path": "/work/T-0088/c.md"}, {"kind": "ci", "url": "https://ci/3"}])

    def test_art_from_splits_generations(self):
        items = [{"n": 1, "g": 1, "kind": "pr", "url": "https://github.com/o/r/pull/1"},
                 {"n": 2, "g": 2, "kind": "branch", "ref": "/r#b"}, {"n": 3, "g": 2, "kind": "commit", "ref": "abc"},
                 {"n": 4, "g": 4, "kind": "pr", "url": "https://github.com/o/r/pull/2"}]
        self.assertEqual([a.get("url") or a["ref"] for a in st.artifacts_view(items, 2)], ["/r#b", "abc", "https://github.com/o/r/pull/2"])
        self.assertEqual(len(st.artifacts_view(items, 5)), 0)
        self.assertEqual(len(st.artifacts_view(items)), 4)

    def test_incomplete_groups_are_left_out_of_the_view(self):
        cwd, branch = "/Users/example/src/github.com/org/repo-a", "claude/t0120-fix-typo"
        worker = {"cwd": cwd, "branch": branch}
        old = [{"n": 1, "g": 1, "kind": "branch", "ref": f"{cwd}#{branch}"},
               {"n": 2, "g": 1, "kind": "commit", "ref": "aaa", "url": "https://github.com/org/repo-a/commit/aaa"}]
        newer_branch = {"n": 3, "g": 3, "kind": "branch", "ref": f"{cwd}#{branch}"}
        view = st.artifacts_view(old + [newer_branch])
        self.assertEqual(view, st.artifacts_view(old))
        self.assertTrue(st._pushed_at_head({"artifacts": view}, worker))
        self.assertFalse(st._pushed_at_head({"artifacts": st.artifacts_view(old + [newer_branch, {"n": 4, "g": 3, "kind": "commit", "ref": "bbb"}])}, worker))
        self.assertEqual(st.artifacts_view([{"n": 2, "g": 1, "kind": "commit", "ref": "aaa"}]), [{"kind": "commit", "ref": "aaa"}])



PUSH_DESC = "Decide whether to push branch claude/license-x1 of /repo/a (and open a PR)"
BRANCH_A = {"kind": "branch", "ref": "/repo/a#claude/license-x1"}
COMMIT_A = {"kind": "commit", "ref": "abc123"}


def v2_state():
    state = st.empty_state("/Users/example/supervisor")
    state["schema_version"] = st.SCHEMA_VERSION
    return state, {}


def v2_note(notes, uid, kind="task", uas=(), groups=(), request="fix the LICENSE year"):
    ops = [{"op": "ua_add", "ua": ua} for ua in uas]
    ops += [{"op": "art_add", "g": items[0]["n"], "items": items} for items in groups]
    text = st.new_note_text(uid, f"{kind} {uid}", kind, "in progress", "agent", NOTE_NOW, request)
    notes[uid] = st.apply_note_ops(text, ops, NOTE_NOW, 1)[0]


def to_v2(item, key, uid):
    for k in st.NOTE_MOVED_KEYS[key]:
        item.pop(k, None)
    item["uid"] = uid
    return item


def v2_git_task(state, notes, task_id, worker_id, uid, uas=(), groups=(), **kw):
    git_task(state, task_id, worker_id, **kw)
    v2_note(notes, uid, uas=uas, groups=groups)
    return to_v2(find(state["tasks"], task_id), "tasks", uid)


def ua(num, description, kind="ask", **extra):
    return dict({"id": f"UA-{num:04d}", "kind": kind, "description": description}, **extra)


def drop_line(text, block):
    return "\n".join(l for l in text.split("\n") if not l.endswith(f"^{block}"))


class ViewTest(unittest.TestCase):
    def setUp(self):
        self.state, self.notes = v2_state()
        self.task = v2_git_task(self.state, self.notes, "T-0020", "w-0020", "20261110_090000", uas=[
            ua(1, "where should this run?", answer_required=True), ua(2, "再開時の指示", kind="slot"),
            ua(3, "Review repo-a#42", kind="auto", detector="pr_closed", rule="review-pr", url="https://github.com/org/repo-a/pull/42"),
        ], groups=[[dict(BRANCH_A, n=1), dict(COMMIT_A, n=2)], [{"n": 3, "kind": "pr", "url": "https://github.com/org/repo-a/pull/42"}]])

    def test_join_view_builds_the_v1_task(self):
        self.assertEqual(st._storage_errors(self.state, self.notes), [])
        view = st.join_view(self.state, self.notes)
        self.assertEqual(st.validate(view), [])
        task = find(view["tasks"], "T-0020")
        self.assertEqual((task["title"], task["request"]), ("task 20261110_090000", "fix the LICENSE year"))
        self.assertEqual([ua["id"] for ua in task["user_actions"]], ["UA-0001", "UA-0003"])
        self.assertTrue(task["user_actions"][0]["needs_answer"])
        self.assertEqual(task["user_actions"][1]["follow_up"],
                         {"rule": "review-pr", "url": "https://github.com/org/repo-a/pull/42", "done": "pr_closed"})
        self.assertEqual([a["kind"] for a in task["artifacts"]], ["branch", "commit", "pr"])
        self.assertEqual((task["status"], task["last_artifact_number"]), ("needs_user", 3))
        self.assertEqual(view["supervisor"]["last_ua_number"], 3)
        self.assertEqual(st._next_ua_number(view), 4)

    def test_missing_note_falls_back_to_uid_and_path(self):
        self.task["note"] = "2026/11/20261110_090000.md"
        view = st.join_view(self.state, {})
        self.assertEqual(find(view["tasks"], "T-0020")["title"], "20261110_090000 (2026/11/20261110_090000.md)")
        self.assertIn("20261110_090000 (2026/11/20261110_090000.md)", st.digest(view, "/Users/example/supervisor/state.json"))

    def test_split_view_round_trip(self):
        view = st.join_view(self.state, self.notes)
        new = st.next_state(view, copy.deepcopy(view), NOW)
        state, ops = st.split_view(view, new)
        self.assertEqual(ops, {})
        self.assertEqual(st._storage_errors(state, self.notes), [])
        self.assertEqual(dict(state, rev=0, updated_at=None),
                         dict(self.state, tasks=[dict(self.task, last_artifact_number=3)],
                              supervisor=dict(self.state["supervisor"], last_ua_number=3)))
        self.assertEqual(state["supervisor"]["last_ua_number"], 3)

    def test_cancel_acks_every_open_action(self):
        view = st.join_view(self.state, self.notes)
        cand = copy.deepcopy(view)
        find(cand["tasks"], "T-0020")["cancel"] = {"at": NOW, "reason": "Obsidian で archived"}
        find(cand["workers"], "w-0020").update(intended_state="archived", observed_state="archived")
        new = st.next_state(view, cand, NOW)
        state, ops = st.split_view(cand, new)
        self.assertEqual(ops, {"20261110_090000": [{"op": "ua_ack", "id": "UA-0001"}, {"op": "ua_ack", "id": "UA-0003"}]})
        self.assertEqual(state["archived"][0]["uid"], "20261110_090000")
        self.assertNotIn("title", state["archived"][0])
        self.assertEqual(st._storage_errors(state, self.notes), [])

    def test_waiting_start_action_becomes_a_ua_add(self):
        state, notes = v2_state()
        nongit_task(state, "T-0005", "w-0006", "/Users/example/notes", "archived", "idle")
        nongit_task(state, "T-0007", "w-0007", "/Users/example/notes", "pending", "none", session_id=None)
        v2_note(notes, "u5", groups=[[{"n": 1, "kind": "file", "path": "/Users/example/notes/a.txt"}]])
        v2_note(notes, "u7")
        to_v2(find(state["tasks"], "T-0005"), "tasks", "u5")
        to_v2(find(state["tasks"], "T-0007"), "tasks", "u7")
        state["supervisor"]["last_ua_number"] = 40
        view = st.join_view(state, notes)
        cand = copy.deepcopy(view)
        find(cand["workers"], "w-0006")["observed_state"] = "archived"
        new = st.next_state(view, cand, NOW)
        out, ops = st.split_view(cand, new)
        self.assertEqual(ops, {"u7": [{"op": "ua_add", "ua": {
            "id": "UA-0041", "description": st.waiting_start_description("T-0007"), "kind": "auto",
            "detector": "start_possible", "since": NOW}}]})
        self.assertEqual(out["supervisor"]["last_ua_number"], 41)
        self.assertEqual(out["archived"][0]["uid"], "u5")
        applied = dict(notes, u7=st.apply_note_ops(notes["u7"], ops["u7"], NOW, 2)[0])
        self.assertEqual(find(st.join_view(out, applied)["tasks"], "T-0007")["user_actions"][0]["id"], "UA-0041")
        # Paused or quarantined tasks start nothing, so they get no waiting-start action either.
        for key, value in (("user_hold", {"at": NOW, "status": "pending", "assignee": "user", "reason": "pause"}),
                           ("artifacts_quarantine", {"at": NOW, "ua": "UA-0040"})):
            held = copy.deepcopy(state)
            find(held["tasks"], "T-0007")[key] = value
            view = st.join_view(held, notes)
            cand = copy.deepcopy(view)
            find(cand["workers"], "w-0006")["observed_state"] = "archived"
            self.assertNotIn("u7", st.split_view(cand, st.next_state(view, cand, NOW))[1])
            workers = {w["id"]: w for w in view["workers"]}
            self.assertEqual(st._startable_workers(view, find(view["tasks"], "T-0007"), workers), [])

    def test_held_worker_reads_as_a_read_action(self):
        self.task["held"] = ["w-0020"]
        view = st.join_view(self.state, self.notes)
        task = find(view["tasks"], "T-0020")
        self.assertEqual(st._held_ids(task), {"w-0020"})
        state, _ = st.split_view(view, view)
        self.assertEqual(find(state["tasks"], "T-0020")["held"], ["w-0020"])
        cleared = copy.deepcopy(view)
        find(cleared["tasks"], "T-0020")["user_actions"] = [u for u in task["user_actions"] if not st._read_action_worker(u)]
        state, ops = st.split_view(view, cleared)
        self.assertNotIn("held", find(state["tasks"], "T-0020"))
        self.assertEqual(ops, {})

    def test_task_waiting_for_its_location_needs_no_worker(self):
        view = st.join_view(self.state, self.notes)
        task = find(view["tasks"], "T-0020")
        view["workers"], task["worker_ids"] = [], []
        self.assertEqual(st.validate(view), [])
        task["user_actions"] = [u for u in task["user_actions"] if not u.get("needs_answer")]
        self.assertIn("task T-0020: worker_ids must not be empty", st.validate(view))

    def test_storage_errors(self):
        cases = [
            (lambda s: self.task.__setitem__("title", "x"), "['title'] belong to the note"),
            (lambda s: self.task.__setitem__("uid", "nope"), "note nope not found"),
            (lambda s: self.task.__setitem__("held", ["w-9999"]), "held must list workers of the task"),
            (lambda s: self.task.__setitem__("user_hold", {"at": NOW, "reason": "later"}), "user_hold must be"),
            (lambda s: self.task.__setitem__("note_seen", {"status": 1}), "note_seen must be"),
            (lambda s: s["archived"].append({"id": "T-0020", "uid": "20261110_090000", "archived_at": NOW}),
             "a revived task needs art_from"),
            (lambda s: s["supervisor"].__setitem__("note_journal", {"rev": -1}), "note_journal must be"),
        ]
        for mutate, message in cases:
            with self.subTest(message=message):
                self.setUp()
                mutate(self.state)
                self.assertIn(message, "; ".join(st._storage_errors(self.state, self.notes)))
        self.setUp()
        git_task(self.state, "T-0021", "w-0021")
        to_v2(find(self.state["tasks"], "T-0021"), "tasks", "20261110_090000")
        self.assertIn("is also used by", "; ".join(st._storage_errors(self.state, self.notes)))


def item(**kw):
    return dict({"id": "T-0020", "worker_ids": ["w-0020"], "user_actions": []}, **kw)


def wk(intended="running", observed="idle", **kw):
    return {"w-0020": dict({"id": "w-0020", "intended_state": intended, "observed_state": observed}, **kw)}


class NotePropsTest(unittest.TestCase):
    def test_task_rows(self):
        read = {"id": "held-w-0020", "description": "Read the result of w-0020 (held)", "since": NOW}
        rows = [
            (item(confirmed={"at": NOW}), wk(), ("done", "user")),
            (item(cancel={"at": NOW, "reason": "x"}), wk("archived", "idle"), ("archived", None)),
            (item(user_hold={"at": NOW, "reason": "pause"}), wk(), None),
            (item(user_hold={"at": NOW, "reason": "pause"}, artifacts_quarantine={"at": NOW, "ua": "UA-0001"},
                  user_actions=[{"id": "UA-0001", "description": "q", "since": NOW}]), wk(), None),
            (item(), wk("archived", "archived"), ("done", "agent")),
            (item(user_actions=[read]), wk(), ("done", "agent")),
            (item(user_actions=[read, {"id": "UA-0001", "description": "q", "since": NOW}]), wk(), ("in progress", "user")),
            (item(artifacts_quarantine={"at": NOW, "ua": "UA-0001"},
                  user_actions=[{"id": "UA-0001", "description": "q", "since": NOW}]), wk(), ("in progress", "user")),
            (item(), wk("archived", "archived", **{"intended_state": "running"}), ("in progress", "user")),
            (item(), wk("pending", "none"), ("in progress", "agent")),
            (item(), wk(), ("in progress", "agent")),
        ]
        for task, workers, expected in rows:
            with self.subTest(task=task, workers=workers):
                self.assertEqual(st.note_props("task", task, workers), expected)
        self.assertIsNone(st.note_props("stub", item(), wk()))

    def test_project_rows(self):
        rows = [
            ({"confirmed": {"at": NOW}, "close": {"at": NOW, "reason": "x"}}, ("done", "user")),
            ({"close": {"at": NOW, "reason": "x"}}, ("archived", None)),
            ({"user_hold": {"at": NOW, "reason": "pause"}, "done_proposed": {"at": NOW, "summary": "s"}}, None),
            ({"done_proposed": {"at": NOW, "summary": "s"}}, ("done", "agent")),
            ({"user_actions": [{"id": "UA-0001", "description": "q", "since": NOW}]}, ("in progress", "user")),
            ({}, ("in progress", "agent")),
        ]
        for project, expected in rows:
            with self.subTest(project=project):
                self.assertEqual(st.note_props("project", dict({"id": "P-0001", "user_actions": []}, **project), {}), expected)

    def test_archived_is_kept_once_confirmed_is_cleared(self):
        # #17 / #18 clear confirmed, so a cancel whose worker is still stopping, or a close, stays archived.
        cancelled = item(cancel={"at": NOW, "reason": "x"})
        self.assertEqual(st.note_props("task", cancelled, wk("archived", "idle")), ("archived", None))
        self.assertEqual(st.note_props("task", dict(cancelled, confirmed={"at": NOW}), wk("archived", "idle")), ("done", "user"))
        self.assertEqual(st.note_props("project", {"close": {"at": NOW, "reason": "x"}}, {}), ("archived", None))

    def project_state(self):
        current = fixture_state()
        project = add_project(current)
        project_worker(current, "T-0020", "w-0020")
        project_worker(current, "T-0021", "w-0021")
        project["confirmed"] = {"at": NOW}
        project["user_actions"] = [{"id": "UA-0900", "description": "配下の T-0020 が稼働中です", "since": NOW,
                                    "detector": "children_closed"}]
        return current

    def cancel(self, state, task_id, worker_id):
        find(state["tasks"], task_id)["cancel"] = {"at": NOW, "reason": "Obsidian で archived"}
        find(state["workers"], worker_id).update(intended_state="archived", observed_state="archived")

    def test_confirmed_project_closes_with_its_last_task(self):
        current = self.project_state()
        cand = copy.deepcopy(current)
        self.cancel(cand, "T-0020", "w-0020")
        new = st.next_state(current, cand, NOW)
        project = find(new["projects"], "P-0001")
        self.assertNotIn("close", project)
        self.assertEqual(len(project["user_actions"]), 1)
        cand = copy.deepcopy(new)
        self.cancel(cand, "T-0021", "w-0021")
        new = st.next_state(new, cand, NOW)
        project = find(new["projects"], "P-0001")
        self.assertEqual(project["close"], {"at": NOW, "reason": st.AUTO_CLOSE_REASON})
        self.assertEqual(project["manager"]["intended_state"], "archived")
        self.assertEqual(project["user_actions"], [])
        self.assertEqual(st.validate(new), [])


def nv(status, assignee, ua="u0", art="a0"):
    return {"status": status, "assignee": assignee, "ua_sha": ua, "art_sha": art}


IPA, IPU, PU, BU, BA = nv("in progress", "agent"), nv("in progress", "user"), nv("pending", "user"), nv("backlog", "user"), nv("backlog", "agent")
DA, DU, AR, PA = nv("done", "agent"), nv("done", "user"), nv("archived", "agent"), nv("pending", "agent")
LIVE = {"managed": True, "live": True, "kind": "task"}
PROJ = {"managed": True, "live": True, "kind": "project"}
STUB = {"managed": True, "live": False, "kind": "task"}
PAUSED = dict(LIVE, hold="pause")


class UserTransitionTest(unittest.TestCase):
    ROWS = [
        # (seen, cur, ctx, event, row)
        (None, BA, {}, "pickup", 1),
        (None, BU, {}, "none", 2),
        (None, nv("new", "user"), {}, "none", 2),
        (None, nv("todo", None), {}, "none", 2),
        (None, None, {}, "none", 2),
        (None, IPA, {}, "invalid", 3),
        (None, PA, {}, "invalid", 3),
        (None, nv("new", "agent"), {}, "invalid", 3),
        (IPA, PA, LIVE, "invalid", 4),
        (IPA, nv("new", "agent"), LIVE, "invalid", 4),
        (IPA, nv("in progress", "bot"), LIVE, "invalid", 4),
        (IPA, None, LIVE, "invalid", 4),
        (IPA, IPA, dict(LIVE, duplicate=True), "invalid", 4),
        (IPA, IPU, dict(LIVE, active=True), "pause", 5),
        (IPA, BU, dict(LIVE, active=True), "pause", 5),
        (IPA, PU, dict(LIVE, active=True), "pause", 5),
        (IPU, PU, dict(LIVE, active=True, open_ua=True), "pause", 6),
        (IPU, BU, dict(LIVE, active=True, open_ua=True), "pause", 6),
        (IPA, IPU, LIVE, "pause", 7),
        (IPA, BU, LIVE, "pause", 7),
        (IPU, PU, dict(LIVE, open_ua=True), "pause", 7),
        (PU, IPA, PAUSED, "resume", 8),
        (BU, BA, PAUSED, "resume", 8),
        (PU, BU, PAUSED, "none", 9),
        (IPU, PU, PAUSED, "none", 9),
        (IPU, nv("in progress", "user", ua="u1"), dict(LIVE, open_ua=True), "none", 10),
        (IPU, IPA, dict(LIVE, open_ua=True), "resume", 11),
        (IPU, BA, dict(LIVE, open_ua=True), "resume", 11),
        (IPA, BA, LIVE, "restore", 12),
        (DA, DU, LIVE, "confirm", 13),
        (DA, DU, STUB, "confirm", 13),
        (DA, DU, PROJ, "confirm", 14),
        (IPA, DU, PROJ, "confirm", 14),
        (IPU, DU, PROJ, "confirm", 14),
        (IPA, DU, LIVE, "cancel", 15),
        (IPA, DA, LIVE, "cancel", 15),
        (IPU, DU, dict(LIVE, open_ua=True), "cancel", 15),
        (PU, DU, PAUSED, "cancel", 15),
        (DA, BA, LIVE, "follow_up", 16),
        (DU, BA, STUB, "follow_up", 16),
        (IPA, AR, dict(LIVE, active=True), "cancel", 17),
        (DU, nv("archived", "user"), STUB, "cancel", 17),
        (IPU, AR, PROJ, "close", 18),
        (AR, BA, STUB, "invalid", 19),
        (AR, IPA, LIVE, "invalid", 19),
        (DU, DA, LIVE, "restore", 20),
        (DU, DA, STUB, "restore", 20),
        (DA, IPA, PROJ, "reject_done", 21),
        (DA, IPU, PROJ, "reject_done", 21),
        (IPA, nv("in progress", "agent", art="a1"), dict(LIVE, artifacts_ok=True), "artifacts_edit", 22),
        (DU, nv("done", "user", art="a1"), dict(STUB, artifacts_ok=False), "artifacts_edit", 22),
        (IPA, nv("in progress", "agent", art="a1"), dict(LIVE, artifacts_ok=False), "artifacts_broken", 23),
        (IPA, IPA, LIVE, "none", 0),
        (IPA, nv("in progress", "agent", ua="u1"), LIVE, "none", 0),
    ]

    def test_table(self):
        for seen, cur, ctx, event, row in self.ROWS:
            with self.subTest(seen=seen, cur=cur, ctx=ctx):
                got = st.user_transition(seen, cur, ctx)
                self.assertEqual((got["event"], got["row"]), (event, row))
        rows = {row for *_, row in self.ROWS}
        self.assertTrue(set(range(1, 24)) <= rows, set(range(1, 24)) - rows)

    def test_unlisted_combinations_are_invalid(self):
        for seen, cur, ctx in ((DU, IPU, LIVE), (DU, PU, LIVE), (DA, IPA, LIVE), (IPA, DA, PROJ), (BU, IPA, LIVE),
                               (PU, PA, PAUSED), (BU, DU, dict(PROJ, hold="pause"))):
            with self.subTest(seen=seen, cur=cur, ctx=ctx):
                got = st.user_transition(seen, cur, ctx)
                self.assertEqual(got["event"], "invalid")
                self.assertEqual(got["ack"], seen)

    def test_write_lost_regressions(self):
        # A UA answered and handed back is a resume; an agent value turned to user is a pause (v1 write_lost removed).
        self.assertEqual(st.user_transition(IPU, IPA, dict(LIVE, open_ua=True))["event"], "resume")
        self.assertEqual(st.user_transition(IPA, IPU, dict(LIVE, active=True))["event"], "pause")

    def test_acks(self):
        cur = nv("in progress", "user", art="a1")
        got = st.user_transition(IPA, cur, dict(LIVE, active=True))
        self.assertEqual(got["ack"], dict(cur, art_sha="a0"))
        self.assertEqual(st.user_transition(IPU, nv("in progress", "user", ua="u9"), LIVE)["ack"]["ua_sha"], "u9")
        self.assertEqual(st.user_transition(None, BA, {})["ack"], BA)
        self.assertIsNone(st.user_transition(None, IPA, {})["ack"])
        self.assertFalse(st.user_transition(DA, IPA, PROJ)["pause"])
        self.assertTrue(st.user_transition(DA, IPU, PROJ)["pause"])

    def test_fixing_an_invalid_value_clears_the_hold(self):
        ctx = dict(LIVE, hold="invalid", active=True)
        self.assertFalse(st.user_transition(IPA, PA, ctx)["clear_invalid"])
        self.assertTrue(st.user_transition(IPA, IPU, ctx)["clear_invalid"])
        self.assertTrue(st.user_transition(IPA, IPA, ctx)["clear_invalid"])


class ArtifactDependencyTest(unittest.TestCase):
    UID = "20261110_090000"

    def setUp(self):
        self.state, self.notes = v2_state()
        self.task = v2_git_task(self.state, self.notes, "T-0020", "w-0020", self.UID, intended="archived", observed="archived",
                                uas=[ua(1, PUSH_DESC), ua(2, "Review repo-a#42", kind="auto", detector="pr_closed",
                                                         rule="review-pr", url="https://github.com/org/repo-a/pull/42")],
                                groups=[[dict(BRANCH_A, n=1), dict(COMMIT_A, n=2)],
                                        [{"n": 3, "kind": "pr", "url": "https://github.com/org/repo-a/pull/42"}],
                                        [{"n": 4, "kind": "ci", "url": "https://ci.example.com/4"}]])
        self.state["workers"].append(dict(find(self.state["workers"], "w-0020"), id="w-0021", session_id="sess_w-0021",
                                          intended_state="running", observed_state="idle",
                                          branch="claude/license-x2", worktree_path="/repo/a/.claude/worktrees/license-x2"))
        self.task["worker_ids"].append("w-0021")

    def needs(self, text=None):
        notes = dict(self.notes, **({self.UID: text} if text else {}))
        view = st.join_view(self.state, notes)
        items = st.parse_artifacts(note_section(notes[self.UID], "成果物"))[0]
        return st.artifact_needs(find(view["tasks"], "T-0020"), view["workers"], items)

    def test_intact_rows_satisfy_every_need(self):
        self.assertEqual(st.validate(st.join_view(self.state, self.notes)), [])
        self.assertEqual(self.needs(), [])

    def test_missing_rows_are_reported(self):
        text = self.notes[self.UID]
        # (1) the commit of the push decision's group, (2) the branch an archived worker delivered, (3) the follow-up PR.
        cases = {
            "sv-art-2": ["w-0020 の完了の記録(commit の成果物)", "UA-0001 の対象 w-0020 の branch claude/license-x1 と直後の commit",
                         "グループ 1 の branch の直後の commit"],
            "sv-art-1": ["w-0020 の完了の記録(commit の成果物)", "UA-0001 の対象 w-0020 の branch claude/license-x1 と直後の commit"],
            "sv-art-3": ["UA-0002 の PR https://github.com/org/repo-a/pull/42"],
        }
        for block, expected in cases.items():
            with self.subTest(block=block):
                self.assertEqual(self.needs(drop_line(text, block)), expected)
        broken = text.replace('"kind":"pr"', '"kind":"pull"')
        self.assertEqual(self.needs(broken), ["UA-0002 の PR https://github.com/org/repo-a/pull/42"])
        # A row nothing depends on can go: that is an artifacts_edit.
        self.assertEqual(self.needs(drop_line(text, "sv-art-4")), [])
        seen, cur = nv("in progress", "user", art="a0"), nv("in progress", "user", art="a1")
        self.assertEqual(st.user_transition(seen, cur, dict(LIVE, artifacts_ok=False))["event"], "artifacts_broken")
        self.assertEqual(st.user_transition(seen, cur, dict(LIVE, artifacts_ok=True))["event"], "artifacts_edit")

    def quarantined(self, quarantine=True):
        self.notes[self.UID] = drop_line(self.notes[self.UID], "sv-art-1")
        if quarantine:
            self.task["artifacts_quarantine"] = {"at": NOW, "ua": "UA-0005"}
        return st.join_view(self.state, self.notes)

    def test_quarantine_relieves_only_what_current_already_has(self):
        self.assertIn("w-0020: archive requested but task lacks commit artifacts", "; ".join(st.validate(self.quarantined(False))))
        current = self.quarantined()
        cand = copy.deepcopy(current)
        find(cand["tasks"], "T-0020")["next_action"] = "wait for the user"
        st.next_state(current, cand, NOW)
        cand = copy.deepcopy(current)
        find(cand["workers"], "w-0021").update(intended_state="archived", observed_state="archived")
        with self.assertRaises(st.StateError) as ctx:
            st.next_state(current, cand, NOW)
        self.assertIn("w-0021: archive requested but task lacks commit artifacts", str(ctx.exception))
        self.assertNotIn("w-0020", str(ctx.exception))

    def test_follow_up_url_is_relieved_while_quarantined(self):
        self.notes[self.UID] = drop_line(self.notes[self.UID], "sv-art-3")
        self.assertIn("is not a pr artifact of the task", "; ".join(st.validate(st.join_view(self.state, self.notes))))
        self.task["artifacts_quarantine"] = {"at": NOW, "ua": "UA-0005"}
        current = st.join_view(self.state, self.notes)
        st.next_state(current, copy.deepcopy(current), NOW)

    def test_delete_answer_resolves_the_quarantine(self):
        current = self.quarantined()
        cand = copy.deepcopy(current)
        task = find(cand["tasks"], "T-0020")
        task["no_result"] = [{"worker_id": "w-0020", "summary": "成果物の記録をユーザーが削除(UA-0005)", "at": NOW}]
        task["user_actions"] = [u for u in task["user_actions"] if u["id"] != "UA-0001"]
        del task["artifacts_quarantine"]
        self.assertEqual(st.validate(cand), [])
        st.next_state(current, cand, NOW)

    def test_quarantined_task_starts_nothing(self):
        view = self.quarantined()
        view["workers"].append(dict(find(view["workers"], "w-0021"), id="w-0022", kind="non-git", delivery="files",
                                    cwd="/Users/example/notes", intended_state="pending", observed_state="none"))
        task = find(view["tasks"], "T-0020")
        task["worker_ids"].append("w-0022")
        self.assertEqual(st._startable_workers(view, task, {w["id"]: w for w in view["workers"]}), [])
        del task["artifacts_quarantine"]
        self.assertEqual(len(st._startable_workers(view, task, {w["id"]: w for w in view["workers"]})), 1)

    def test_revived_stub_keeps_only_rows_before_art_from(self):
        state, notes = v2_state()
        task = v2_git_task(state, notes, "T-0019", "w-0019", "u19", intended="pending", observed="none",
                           groups=[[dict(BRANCH_A, n=1), dict(COMMIT_A, n=2)], [dict(BRANCH_A, n=3), dict(COMMIT_A, n=4, ref="def456")]])
        task["art_from"] = 3
        state["archived"].append({"id": "T-0019", "uid": "u19", "archived_at": NOW, "last_artifact_number": 2})
        self.assertEqual(st._storage_errors(state, notes), [])
        view = st.join_view(state, notes)
        self.assertEqual([a["ref"] for a in view["archived"][0]["artifacts"]], ["/repo/a#claude/license-x1", "abc123"])
        self.assertEqual([a["ref"] for a in find(view["tasks"], "T-0019")["artifacts"]], ["/repo/a#claude/license-x1", "def456"])
        self.assertEqual(find(view["tasks"], "T-0019")["last_artifact_number"], 4)
        self.assertEqual(st.validate(view), [])

    def test_stub_row_deletion_falls_back_to_an_older_group(self):
        state, notes = v2_state()
        v2_note(notes, "u19", groups=[[dict(BRANCH_A, n=1), dict(COMMIT_A, n=2)], [dict(BRANCH_A, n=3), dict(COMMIT_A, n=4, ref="def456")]])
        state["archived"].append({"id": "T-0019", "uid": "u19", "archived_at": NOW})
        notes["u19"] = drop_line(notes["u19"], "sv-art-4")
        stub = st.join_view(state, notes)["archived"][0]
        self.assertEqual([a["ref"] for a in stub["artifacts"]], ["/repo/a#claude/license-x1", "abc123"])
        got = st.user_transition(nv("done", "user"), nv("done", "user", art="a1"), dict(STUB, artifacts_ok=False))
        self.assertEqual(got["event"], "artifacts_edit")



class Crash(BaseException):
    """Stands for the process being killed: nothing catches it."""


U1, U2, U3 = "20261110_090001", "20261110_090002", "20261110_090003"


class VaultEnv:
    def __init__(self, test):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.vault = self.dir / "vault"
        self.state_path = self.dir / "state.json"
        self.state = st.empty_state(str(self.dir))

    def add_task(self, task_id, worker_id, uid, uas=(), groups=(), **kw):
        notes = {}
        task = v2_git_task(self.state, notes, task_id, worker_id, uid, uas=uas, groups=groups, **kw)
        notes[uid] = st.set_props(notes[uid], {"task_id": task_id})
        self.put(uid, notes[uid])
        task.update(note=st._new_note_path(uid), note_seen=st.note_values(notes[uid]))
        return task

    def path(self, uid):
        return self.vault / st._new_note_path(uid)

    def put(self, uid, text, rel=None):
        path = self.vault / rel if rel else self.path(uid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def text(self, uid):
        return self.path(uid).read_text(encoding="utf-8")

    def values(self, uid):
        return st.note_values(self.text(uid))

    def save(self):
        self.state_path.write_text(st.serialize(self.state), encoding="utf-8")

    def load(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = st.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def commit(self, cand=None, ops=None):
        (self.dir / "state.next.json").write_text(st.serialize(cand if cand is not None else self.load()), encoding="utf-8")
        argv = ["commit", "--state", str(self.state_path), "--candidate", str(self.dir / "state.next.json")]
        if ops is not None:
            (self.dir / "state.next.notes.json").write_text(json.dumps(ops, ensure_ascii=False), encoding="utf-8")
            argv += ["--notes", str(self.dir / "state.next.notes.json")]
        code, out, err = self.run(*argv)
        return code, (json.loads(out) if code == 0 else None), err

    def scan(self):
        code, out, err = self.run("notes-scan", "--state", str(self.state_path))
        assert code == 0, err
        return json.loads(out)


def failing_writes(fail_on, exc=Crash):
    """Patch _write_note so that its calls numbered in fail_on (1-based) raise exc."""
    real, calls = st._write_note, []

    def write(path, text):
        calls.append(path)
        if len(calls) in fail_on:
            raise exc("stopped")
        return real(path, text)
    return mock.patch.object(st, "_write_note", write), calls


class NoteCommitTest(unittest.TestCase):
    def setUp(self):
        self.env = VaultEnv(self)
        self.env.add_task("T-0020", "w-0020", U1)
        self.env.add_task("T-0021", "w-0021", U2)
        find(self.env.state["workers"], "w-0021").update(cwd="/repo/b", branch="claude/b-x1", worktree_path="/repo/b/.claude/worktrees/b-x1")
        self.env.save()

    def test_chat_task_note_is_created_with_the_state(self):
        code, out, err = self.env.run("new-note", "--state", str(self.env.state_path), "--kind", "task")
        reserved = json.loads(out)
        self.assertEqual(reserved["path"], st._new_note_path(reserved["uid"]))
        cand = self.env.load()
        git_task(cand, "T-0022", "w-0022")
        find(cand["workers"], "w-0022").update(cwd="/repo/c", intended_state="pending", observed_state="none", session_id=None)
        to_v2(find(cand["tasks"], "T-0022"), "tasks", reserved["uid"])
        code, out, err = self.env.commit(cand, {reserved["uid"]: [
            {"op": "create", "uid": reserved["uid"], "title": "README の typo", "kind": "task", "status": "in progress",
             "assignee": "agent", "request": "README の typo を直して"}]})
        self.assertEqual(code, 0, err)
        self.assertEqual(out["notes_written"], [reserved["path"]])
        text = (self.env.vault / reserved["path"]).read_text(encoding="utf-8")
        self.assertEqual(note_section(text, "概要").strip(), "README の typo を直して")
        task = find(self.env.load()["tasks"], "T-0022")
        self.assertEqual((task["note"], task["note_seen"]), (reserved["path"], st.note_values(text)))
        self.assertEqual(self.env.scan(), {"events": [], "warnings": []})

    def test_commit_writes_the_id_props_once(self):
        self.env.put(U1, "\n".join(l for l in self.env.text(U1).split("\n") if not l.startswith("task_id:")))
        code, out, err = self.env.commit()
        self.assertEqual(code, 0, err)
        self.assertEqual(out["notes_written"], [st._new_note_path(U1)])
        props = st.parse_frontmatter(self.env.text(U1))[0]
        self.assertEqual((props["task_id"], "project_id" in props), ("T-0020", False))
        self.assertEqual(self.env.commit()[1]["notes_written"], [])
        self.assertEqual(self.env.scan(), {"events": [], "warnings": []})

    def test_pickup_writes_the_derived_props_with_the_ack(self):
        text = st.new_note_text(U3, "思いつき", "task", "backlog", "agent", NOTE_NOW, "やって")
        self.env.put(U3, text)
        events = self.env.scan()["events"]
        self.assertEqual([(e["event"], e["uid"], e["request"]) for e in events], [("pickup", U3, "やって")])
        cand = self.env.load()
        git_task(cand, "T-0022", "w-0022")
        find(cand["workers"], "w-0022").update(cwd="/repo/c")
        to_v2(find(cand["tasks"], "T-0022"), "tasks", U3)["note_seen"] = events[0]["ack"]
        code, out, err = self.env.commit(cand, {U3: [{"op": "log", "text": "受領しました"}]})
        self.assertEqual(code, 0, err)
        self.assertEqual(st.parse_frontmatter(self.env.text(U3))[0]["status"], "in progress")
        self.assertIn("受領しました", note_section(self.env.text(U3), "ログ"))
        self.assertEqual(self.env.scan()["events"], [])

    def test_uidless_note_gets_its_uid_at_pickup(self):
        text = "---\ntitle: 思いつき\ntags:\n  - task\nstatus: backlog\nassignee: agent\n---\n## 概要\n\nやって\n"
        self.env.put(None, text, rel="Untitled.md")
        event = self.env.scan()["events"][0]
        uid = event["proposed_uid"]
        cand = self.env.load()
        git_task(cand, "T-0022", "w-0022")
        find(cand["workers"], "w-0022").update(cwd="/repo/c")
        to_v2(find(cand["tasks"], "T-0022"), "tasks", uid)["note_seen"] = event["ack"]
        code, out, err = self.env.commit(cand, {uid: [{"op": "assign_uid", "uid": uid, "path": "Untitled.md"}]})
        self.assertEqual(code, 0, err)
        props = st.parse_frontmatter((self.env.vault / "Untitled.md").read_text(encoding="utf-8"))[0]
        self.assertEqual((props["uid"], props["status"], props["assignee"]), (uid, "in progress", "agent"))
        self.assertEqual(find(self.env.load()["tasks"], "T-0022")["note"], "Untitled.md")

    def test_a_note_changed_after_the_scan_is_deferred(self):
        self.env.put(U1, self.env.text(U1).replace('status: "in progress"', 'status: "pending"').replace('assignee: "agent"', 'assignee: "user"'))
        code, out, err = self.env.commit(None, {U1: [{"op": "ua_add", "ua": {"kind": "ask", "description": "どちらにしますか"}}]})
        self.assertEqual(code, 0, err)
        self.assertEqual(out["notes_deferred"], [st._new_note_path(U1)])
        self.assertEqual(st._pair(self.env.values(U1)), ("pending", "user"))
        self.assertIn("UA-0001 どちらにしますか", self.env.text(U1))
        events = self.env.scan()["events"]
        self.assertEqual([(e["uid"], e["event"]) for e in events], [(U1, "pause")])

    def test_ack_of_a_note_changed_after_the_scan_is_rejected(self):
        self.env.put(U1, self.env.text(U1).replace('status: "in progress"', 'status: "backlog"'))
        event = self.env.scan()["events"][0]
        self.assertEqual(event["event"], "restore")
        self.env.put(U1, self.env.text(U1).replace('status: "backlog"', 'status: "pending"'))
        cand = self.env.load()
        find(cand["tasks"], "T-0020")["note_seen"] = event["ack"]
        code, _, err = self.env.commit(cand)
        self.assertEqual(code, 1)
        self.assertIn("changed after notes-scan", err)
        self.env.put(U1, self.env.text(U1).replace('status: "pending"', 'status: "backlog"'))
        self.assertEqual(self.env.commit(cand)[0], 0)
        # #12: the same commit that acks a restore writes the derived in progress back.
        self.assertEqual(st._pair(self.env.values(U1)), ("in progress", "agent"))

    def test_resume_with_an_open_action_left_writes_in_progress_user_at_once(self):
        ops = {U1: [{"op": "ua_add", "ua": {"kind": "ask", "description": "q1"}},
                    {"op": "ua_add", "ua": {"kind": "ask", "description": "q2"}}]}
        self.assertEqual(self.env.commit(None, ops)[0], 0)
        self.assertEqual(st._pair(self.env.values(U1)), ("in progress", "user"))
        self.env.put(U1, self.env.text(U1).replace("- [ ] UA-0001", "- [x] UA-0001").replace('assignee: "user"', 'assignee: "agent"'))
        event = self.env.scan()["events"][0]
        self.assertEqual((event["event"], event["row"]), ("resume", 11))
        self.assertTrue(next(u for u in event["user_actions"] if u["id"] == "UA-0001")["checked"])
        cand = self.env.load()
        find(cand["tasks"], "T-0020")["note_seen"] = event["ack"]
        code, out, err = self.env.commit(cand, {U1: [{"op": "ua_ack", "id": "UA-0001"}, {"op": "log", "text": "q2 が残っています"}]})
        self.assertEqual(code, 0, err)
        self.assertEqual(st._pair(self.env.values(U1)), ("in progress", "user"))
        self.assertEqual(self.env.scan()["events"], [])

    def test_kanban_done_drag_cancels_and_confirms_in_the_same_commit(self):
        self.env.put(U1, self.env.text(U1).replace('status: "in progress"', 'status: "done"'))
        event = self.env.scan()["events"][0]
        self.assertEqual((event["event"], event["row"]), ("cancel", 15))
        cand = self.env.load()
        task = find(cand["tasks"], "T-0020")
        task.update(note_seen=event["ack"], cancel={"at": NOW, "reason": "Obsidian で done"}, confirmed={"at": NOW})
        find(cand["workers"], "w-0020").update(intended_state="archived", observed_state="archived")
        code, out, err = self.env.commit(cand)
        self.assertEqual(code, 0, err)
        props = st.parse_frontmatter(self.env.text(U1))[0]
        self.assertEqual((props["status"], props["assignee"]), ("done", "user"))
        self.assertRegex(props["completed_at"], r"^\d{4}-\d{2}-\d{2}$")
        stub = self.env.load()["archived"][0]
        self.assertEqual((stub["uid"], stub["note_seen"]["status"]), (U1, "done"))
        self.assertEqual(self.env.scan()["events"], [])

    def test_finished_task_gets_done_agent_when_archived(self):
        nongit = self.env.load()
        nongit_task(nongit, "T-0030", "w-0030", "/Users/example/notes", "archived", "idle")
        v2_note({}, U3)
        notes = {}
        v2_note(notes, U3, groups=[[{"n": 1, "kind": "file", "path": "/Users/example/notes/a.txt"}]])
        self.env.put(U3, notes[U3])
        to_v2(find(nongit["tasks"], "T-0030"), "tasks", U3)["note_seen"] = st.note_values(notes[U3])
        self.env.state = nongit
        self.env.save()
        cand = self.env.load()
        find(cand["workers"], "w-0030")["observed_state"] = "archived"
        code, out, err = self.env.commit(cand)
        self.assertEqual(code, 0, err)
        self.assertEqual(st._pair(self.env.values(U3)), ("done", "agent"))
        self.assertEqual(self.env.load()["archived"][0]["uid"], U3)

    def crash_ops(self):
        return {U1: [{"op": "log", "text": "検証しました"}, {"op": "ua_add", "ua": {"kind": "ask", "description": "push?"}}],
                U2: [{"op": "log", "text": "成果物を記録"}, {"op": "art_add", "items": [
                    {"kind": "pr", "url": "https://github.com/org/repo-b/pull/7"}]}]}

    def assert_replayed_once(self, before_rev):
        self.assertEqual(self.env.scan()["events"], [])
        writes, calls = failing_writes(set())
        with writes:
            code, out, err = self.env.commit()
        self.assertEqual(code, 0, err)
        for uid, needle in ((U1, "検証しました"), (U2, "成果物を記録")):
            self.assertEqual(self.env.text(uid).count(needle), 1, uid)
        self.assertEqual(self.env.text(U1).count("^ua-0001"), 1)
        self.assertEqual(self.env.text(U2).count("^sv-art-1"), 1)
        self.assertEqual(st._pair(self.env.values(U1)), ("in progress", "user"))
        self.assertEqual(self.env.scan()["events"], [])
        texts = (self.env.text(U1), self.env.text(U2))
        self.assertEqual(self.env.commit()[0], 0)
        self.assertEqual((self.env.text(U1), self.env.text(U2)), texts)
        self.assertEqual(self.env.load()["rev"], before_rev + 3)
        return calls

    def test_crash_after_the_state_write_before_any_note(self):
        before = (self.env.text(U1), self.env.text(U2))
        writes, _ = failing_writes({1})
        with writes, self.assertRaises(Crash):
            self.env.commit(None, self.crash_ops())
        self.assertEqual((self.env.text(U1), self.env.text(U2)), before)
        self.assertEqual(len(self.env.load()["supervisor"]["note_journal"]["entries"]), 2)
        self.assertEqual(self.env.scan()["warnings"], [])
        calls = self.assert_replayed_once(0)
        self.assertEqual(len(calls), 2)

    def test_crash_after_the_first_note(self):
        writes, _ = failing_writes({2})
        with writes, self.assertRaises(Crash):
            self.env.commit(None, self.crash_ops())
        self.assertIn("検証しました", self.env.text(U1))
        self.assertNotIn("成果物を記録", self.env.text(U2))
        calls = self.assert_replayed_once(0)
        self.assertEqual([Path(c).name for c in calls], [f"{U2}.md"])

    def test_replay_does_not_restore_what_the_user_removed(self):
        ops = {U1: [{"op": "ua_add", "ua": {"kind": "ask", "description": "q"}}, {"op": "ua_check", "id": "UA-0001"},
                    {"op": "log", "text": "追記"}]}
        writes, _ = failing_writes({1})
        with writes, self.assertRaises(Crash):
            self.env.commit(None, ops)
        # The user applies an older copy of the write by hand (e.g. sync), then deletes the log line and unchecks the box.
        text = st.apply_note_ops(self.env.text(U1), st.number_note_ops(ops, 1, {})[U1], NOW, 1)[0]
        text = "\n".join(l for l in text.split("\n") if "追記" not in l).replace("- [x] UA-0001", "- [ ] UA-0001")
        self.env.put(U1, text)
        self.assertEqual(self.env.commit()[0], 0)
        self.assertEqual(self.env.text(U1), text)

    def test_failed_replays_are_carried_then_dropped(self):
        writes, _ = failing_writes({1})
        with writes, self.assertRaises(Crash):
            self.env.commit(None, {U1: [{"op": "log", "text": "x"}]})
        for tries, expect_warning in ((1, False), (2, True)):
            writes, _ = failing_writes({1}, OSError)
            with writes:
                code, out, err = self.env.commit()
            self.assertEqual(code, 0, err)
            journal = self.env.load()["supervisor"]["note_journal"]
            self.assertEqual(bool(out.get("warnings")), expect_warning)
            self.assertEqual(journal and [e["tries"] for e in journal["entries"]], None if expect_warning else [tries])
        self.assertEqual(self.env.commit()[0], 0)
        self.assertNotIn("^sv-log-r1-0", self.env.text(U1))

    def test_note_saved_during_the_commit_is_built_upon(self):
        real = st._write_state

        def user_saves(*a, **kw):
            result = real(*a, **kw)
            self.env.put(U1, self.env.text(U1).replace("fix the LICENSE year", "fix the LICENSE year (edited)"))
            return result
        with mock.patch.object(st, "_write_state", user_saves):
            code, out, err = self.env.commit(None, {U1: [{"op": "log", "text": "済"}]})
        self.assertEqual(code, 0, err)
        self.assertIn("(edited)", self.env.text(U1))
        self.assertIn("済 ^sv-log-r1-0", self.env.text(U1))

    def test_v1_state_rejects_notes(self):
        self.env.state_path.write_text(st.serialize(fixture_state()), encoding="utf-8")
        code, _, err = self.env.commit(fixture_state(), {})
        self.assertEqual(code, 1)
        self.assertIn("--notes needs a schema_version 2 state", err)

    def test_artifact_rows_written_before_a_crash_do_not_quarantine(self):
        writes, _ = failing_writes({1})
        with writes, self.assertRaises(Crash):
            self.env.commit(None, {U2: [{"op": "art_add", "items": [{"kind": "pr", "url": "https://github.com/org/repo-b/pull/7"}]}]})
        self.assertEqual(self.env.scan()["events"], [])
        self.assertEqual(self.env.commit()[0], 0)
        self.assertEqual(self.env.scan()["events"], [])
        self.assertEqual(self.env.load()["tasks"][1]["last_artifact_number"], 1)


class NotesScanCliTest(unittest.TestCase):
    def setUp(self):
        self.env = VaultEnv(self)
        self.env.add_task("T-0020", "w-0020", U1)
        self.env.save()

    def test_unmanaged_notes(self):
        self.env.put(None, "---\ntitle: メモ\ntags: [idea]\nstatus: backlog\nassignee: agent\n---\n", rel="memo.md")
        self.env.put(None, "---\ntitle: 自分の\ntags: [task]\nstatus: new\nassignee: user\n---\n", rel="mine.md")
        bad = "---\nuid: \"20261110_100000\"\ntitle: x\ntags: [task]\nstatus: \"in progress\"\nassignee: agent\n---\n"
        self.env.put(None, bad, rel="bad.md")
        events = self.env.scan()["events"]
        self.assertEqual([(e["path"], e["event"], e["row"]) for e in events], [("bad.md", "invalid", 3)])
        state = self.env.load()
        state["supervisor"]["note_warnings"] = {events[0]["warning_key"]: events[0]["warning_sha"]}
        self.env.state = state
        self.env.save()
        self.assertEqual(self.env.scan()["events"], [])

    def test_duplicate_uid_and_missing_note(self):
        self.env.put(None, self.env.text(U1), rel="copy.md")
        events = self.env.scan()["events"]
        self.assertEqual([(e["event"], e["row"]) for e in events], [("invalid", 4)])
        for rel in ("copy.md", st._new_note_path(U1)):
            (self.env.vault / rel).unlink()
        self.assertEqual(self.env.scan(), {"events": [], "warnings": [f"T-0020: note {U1} ({st._new_note_path(U1)}) not found"]})

    def test_artifacts_broken_lists_the_needs(self):
        env = VaultEnv(self)
        env.add_task("T-0020", "w-0020", U1, intended="archived", observed="archived", uas=[ua(1, PUSH_DESC)],
                     groups=[[dict(BRANCH_A, n=1), dict(COMMIT_A, n=2)]])
        env.save()
        env.put(U1, drop_line(env.text(U1), "sv-art-2"))
        event = env.scan()["events"][0]
        self.assertEqual((event["event"], event["row"]), ("artifacts_broken", 23))
        self.assertIn("UA-0001 の対象 w-0020 の branch claude/license-x1 と直後の commit", event["needs"])

    def test_answers_while_the_assignee_is_user_print_nothing(self):
        env = VaultEnv(self)
        env.add_task("T-0020", "w-0020", U1, uas=[ua(1, "where?", answer_required=True)])
        env.state["tasks"][0]["note_seen"] = st.note_values(env.text(U1).replace('assignee: "agent"', 'assignee: "user"'))
        env.put(U1, env.text(U1).replace('assignee: "agent"', 'assignee: "user"'))
        env.save()
        env.put(U1, env.text(U1).replace("- [ ] UA-0001", "- [x] UA-0001").replace("^ua-0001", "^ua-0001\n    - ~/notes"))
        self.assertEqual(env.scan()["events"], [])
        env.put(U1, env.text(U1).replace('assignee: "user"', 'assignee: "agent"'))
        event = env.scan()["events"][0]
        self.assertEqual((event["event"], event["ack"]), ("resume", st.note_values(env.text(U1))))

    def test_a_recorded_invalid_hold_is_not_printed_again(self):
        self.env.put(U1, self.env.text(U1).replace('status: "in progress"', "status: doing"))
        event = self.env.scan()["events"][0]
        self.assertEqual((event["event"], event["row"]), ("invalid", 4))
        state = self.env.load()
        state["tasks"][0]["user_hold"] = {"at": NOW, "status": "doing", "assignee": "agent", "reason": "invalid"}
        self.env.state = state
        self.env.save()
        self.assertEqual(self.env.scan()["events"], [])
        self.env.put(U1, self.env.text(U1).replace("status: doing", "status: todo"))
        self.assertEqual([e["event"] for e in self.env.scan()["events"]], ["invalid"])
        self.env.put(U1, self.env.text(U1).replace("status: todo", 'status: "in progress"'))
        event = self.env.scan()["events"][0]
        self.assertEqual((event["event"], event["clear_invalid"]), ("none", True))

    def test_v1_state_is_refused(self):
        self.env.state_path.write_text(st.serialize(fixture_state()), encoding="utf-8")
        code, _, err = self.env.run("notes-scan", "--state", str(self.env.state_path))
        self.assertEqual(code, 1)
        self.assertIn("is not a schema_version 2 state", err)

    def test_note_show(self):
        code, out, _ = self.env.run("note-show", "--state", str(self.env.state_path), "--uid", U1)
        shown = json.loads(out)
        self.assertEqual((shown["path"], shown["props"]["uid"], shown["sections"]["概要"].strip()),
                         (st._new_note_path(U1), U1, "fix the LICENSE year"))
        self.assertEqual(self.env.run("note-show", "--state", str(self.env.state_path), "--uid", "nope")[0], 1)


class NewNoteCliTest(unittest.TestCase):
    def test_reserves_a_free_uid_without_writing(self):
        env = VaultEnv(self)
        env.add_task("T-0020", "w-0020", "20261110_090000")
        env.save()

        class Fixed(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 10, 9, 0, 0)
        with mock.patch.object(st, "datetime", Fixed):
            code, out, _ = env.run("new-note", "--state", str(env.state_path), "--kind", "project")
        self.assertEqual(json.loads(out), {"uid": "20261110_090001", "path": "2026/11/20261110_090001.md", "kind": "project"})
        self.assertFalse((env.vault / "2026/11/20261110_090001.md").exists())


class VaultGuardTest(unittest.TestCase):
    def test_vault_writes_are_blocked_and_the_rest_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            sup = os.path.realpath(tmp)
            for tool, key in (("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
                message = st.guard_decision({"tool_name": tool, "tool_input": {key: f"{sup}/vault/2026/11/x.md"}, "cwd": sup}, sup, {})
                self.assertIn("vault notes are written only by state_tool commit", message)
            self.assertIn("vault notes are written only by state_tool commit",
                          st.guard_decision({"tool_name": "Write", "tool_input": {"file_path": "vault/Supervisor.md"}, "cwd": sup}, sup, {}))
            self.assertIsNone(st.guard_decision({"tool_name": "Write", "tool_input": {"file_path": f"{sup}/notes.md"}, "cwd": sup}, sup, {}))
            self.assertIsNone(st.guard_decision({"tool_name": "Write", "tool_input": {"file_path": f"{sup}/vaults/x.md"}, "cwd": sup}, sup, {}))
            self.assertIn("written only by setup or commit",
                          st.guard_decision({"tool_name": "Write", "tool_input": {"file_path": f"{sup}/state.json"}, "cwd": sup}, sup, {}))


class SetupVaultTest(unittest.TestCase):
    def test_creates_the_vault_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sup"
            proc = subprocess.run([sys.executable, str(SCRIPT), "setup", "--dir", str(target)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            vault = target / "vault"
            dashboard = (vault / "Supervisor.md").read_text(encoding="utf-8")
            self.assertEqual(dashboard.count("type: kanban"), 2)
            self.assertIn("""'assignee == "user"'""", dashboard)
            self.assertIn("""'assignee == "agent"'""", dashboard)
            self.assertIn("groupOrder: [backlog, in progress, pending, done, archived]", (vault / "Supervisor.md").read_text(encoding="utf-8"))
            self.assertEqual(json.loads((vault / ".obsidian" / "templates.json").read_text(encoding="utf-8")), {"folder": "_templates"})
            for kind in ("task", "project"):
                template = (vault / "_templates" / f"{kind}.md").read_text(encoding="utf-8")
                self.assertIn("created_at: {{date:YYYY-MM-DD}} {{time:HH:mm:ss}}\n", template)
                template = template.replace("{{date:YYYY-MM-DD}} {{time:HH:mm:ss}}", "2026-11-10 09:00:00")
                props, body, errors = st.parse_frontmatter(template)
                self.assertEqual(errors, [])
                self.assertEqual((props["uid"], props["tags"], props["status"], props["assignee"]),
                                 ("{{date:YYYYMMDD}}_{{time:HHmmss}}", [kind], "backlog", "user"))
                self.assertEqual(list(st.parse_sections(body)), list(st.NOTE_SECTIONS))
            self.assertIn("vault/", (target / "CLAUDE.md").read_text(encoding="utf-8"))
            (vault / "Supervisor.md").write_text("mine\n", encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPT), "setup", "--dir", str(target)], capture_output=True, text=True)
            self.assertTrue(all(l.startswith("exists:") for l in proc.stdout.splitlines()), proc.stdout)
            self.assertEqual((vault / "Supervisor.md").read_text(encoding="utf-8"), "mine\n")
            self.assertEqual(st._index_vault(str(vault)), ({}, {}))


class DigestFallbackTest(unittest.TestCase):
    def test_missing_vault_and_held_notes(self):
        env = VaultEnv(self)
        task = env.add_task("T-0020", "w-0020", U1)
        task["user_hold"] = {"at": NOW, "status": "in progress", "assignee": "bot", "reason": "invalid"}
        env.save()
        proc = subprocess.run([sys.executable, str(SCRIPT), "digest", "--state", str(env.state_path)], capture_output=True, text=True)
        self.assertIn(f"task {U1}", proc.stdout)
        self.assertIn("notes on hold (invalid values):", proc.stdout)
        shutil.rmtree(env.vault)
        proc = subprocess.run([sys.executable, str(SCRIPT), "digest", "--state", str(env.state_path)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn(f"{U1} ({st._new_note_path(U1)})", proc.stdout)


class ProjectContextDocTest(unittest.TestCase):
    def test_project_waits_for_its_context_document(self):
        state, notes = v2_state()
        project = to_v2(add_project(state), "projects", U3)
        project["manager"].update(session_id=None, started_by=None, intended_state="pending", observed_state="none", observed_at=None)
        v2_note(notes, U3, kind="project", request="MCP サーバを作る", uas=[ua(1, "context document の URL を教えてください", answer_required=True)])
        view = st.join_view(state, notes)
        self.assertIsNone(view["projects"][0]["context_doc_url"])
        self.assertEqual(st.validate(view), [])
        notes[U3] = st.apply_note_ops(notes[U3], [{"op": "ua_ack", "id": "UA-0001"}], NOW, 2)[0]
        self.assertIn("project P-0001: context_doc_url must be http(s)", st.validate(st.join_view(state, notes)))
        notes[U3] = notes[U3].replace("MCP サーバを作る", "MCP サーバを作る https://github.com/org/repo/issues/1")
        self.assertEqual(st.join_view(state, notes)["projects"][0]["context_doc_url"], "https://github.com/org/repo/issues/1")



class ProposalNoteTest(unittest.TestCase):
    def setUp(self):
        self.state, self.notes = v2_state()
        self.project = to_v2(add_project(self.state), "projects", U3)
        v2_note(self.notes, U3, kind="project", request="https://github.com/org/repo/issues/1")

    def plan(self, plans=(), titles=(), **extra):
        report = dict({"project_id": "P-0001", "status": "idle", "summary": "s", "reviews": [], "plans": list(plans),
                       "questions": []}, **extra)
        text = "```project-report\n" + json.dumps(report, ensure_ascii=False) + "\n```"
        parsed = st.parse_project_report(text, "P-0001")
        self.assertTrue(parsed["ok"], parsed)
        return st.plan_actions(st.join_view(self.state, self.notes), "P-0001", parsed["report"], (), titles)

    def test_new_task_proposals_become_notes(self):
        actions = self.plan([{"kind": "new", "title": "Add docs", "text": "write docs"},
                             {"kind": "new", "title": "Add docs", "text": "again"}])
        self.assertEqual(actions, [{"kind": "proposal_note", "title": "Add docs", "text": "write docs", "parent": U3},
                                   {"kind": "proposal_dup", "title": "Add docs", "text": "again"}])
        self.assertEqual([a["kind"] for a in self.plan([{"kind": "new", "title": "Add docs", "text": "x"}], {"Add docs"})],
                         ["proposal_dup"])

    def test_proposal_titles_reads_backlog_user_children(self):
        child = st.new_note_text(U1, "Add docs", "task", "backlog", "user", NOTE_NOW, "write docs", parent=U3)
        texts = {"a.md": child,
                 "b.md": st.new_note_text(U2, "Started", "task", "in progress", "agent", NOTE_NOW, "x", parent=U3),
                 "c.md": st.new_note_text("20261110_090009", "Other", "task", "backlog", "user", NOTE_NOW, "x", parent="20261101_000000"),
                 "d.md": child.replace(f'parent: "[[{U3}]]"', f"parent:\n  - 2026/11/{U3}.md")}
        self.assertEqual(st.proposal_titles(texts, U3), {"Add docs"})
        op = {"op": "create", "uid": U1, "title": "Add docs", "kind": "task", "status": "backlog", "assignee": "user",
              "request": "write docs", "parent": U3}
        props = st.parse_frontmatter(st.apply_note_ops("", [op], NOW, 1)[0])[0]
        self.assertEqual((props["parent"], props["status"], props["assignee"]), (f"[[{U3}]]", "backlog", "user"))

    def test_task_from_a_proposal_note_takes_title_and_text_from_it(self):
        task = v2_git_task(self.state, self.notes, "T-0020", "w-0020", U1, intended="pending", observed="none")
        task.update(project_id="P-0001", proposal={"from": "P-0001", "message_ref": f"note:{U1}"})
        view = st.join_view(self.state, self.notes)
        self.assertEqual(find(view["tasks"], "T-0020")["proposal"],
                         {"title": f"task {U1}", "text": "fix the LICENSE year", "from": "P-0001", "message_ref": f"note:{U1}"})
        self.assertEqual(st.validate(view), [])

    def test_done_field_is_a_completion_proposal(self):
        self.assertEqual(self.plan(done="すべて完了しました")[-1], {"kind": "project_done", "summary": "すべて完了しました"})
        self.assertEqual(self.plan(), [])
        for bad in ("", "  ", 1):
            text = "```project-report\n" + json.dumps({"project_id": "P-0001", "status": "idle", "summary": "s", "reviews": [],
                                                      "plans": [], "questions": [], "done": bad}) + "\n```"
            self.assertFalse(st.parse_project_report(text)["ok"])
        self.project["done_proposed"] = {"at": NOW, "summary": "すべて完了しました"}
        self.assertEqual(st.note_props("project", st.join_view(self.state, self.notes)["projects"][0], {}), ("done", "agent"))


LIVE_SHAPE = FIXTURES / "state_v1_live_shape.json"
MIGRATE_NOW = "2026-11-10T09:00:00+09:00"


def live_shape():
    return json.loads(LIVE_SHAPE.read_text(encoding="utf-8"))


class MigrateExportTest(unittest.TestCase):
    def setUp(self):
        self.v1 = live_shape()
        self.v2, self.creates = st.migrate_v2(self.v1, MIGRATE_NOW)
        self.notes = {c["uid"]: c["text"] for c in self.creates}

    def note_of(self, task_id, key="tasks"):
        return self.notes[find(self.v2[key], task_id)["uid"]]

    def test_notes_carry_the_task_and_project_ids(self):
        ids = {}
        for key in ("archived", "tasks", "projects"):
            for item in self.v2.get(key, []):
                pid = item["id"] if key == "projects" else item.get("project_id") or (item.get("project") or {}).get("id")
                ids[item["uid"]] = (None if key == "projects" else item["id"], pid)
        for uid, (task_id, project_id) in ids.items():
            with self.subTest(uid=uid):
                props = st.parse_frontmatter(self.notes[uid])[0]
                self.assertEqual((props.get("task_id"), props.get("project_id")), (task_id, project_id))
        self.assertTrue(any(pid for _, pid in ids.values()))

    def test_fixture_covers_the_live_shapes(self):
        shapes = {"+".join(sorted(a)) for o in self.v1["tasks"] + self.v1["archived"] for a in o["artifacts"]}
        self.assertEqual(shapes, {"kind+label+url", "kind+label+ref+url", "kind+ref+url", "kind+ref", "kind+label+ref", "kind+path"})
        self.assertEqual(st.validate(self.v1), [])

    def test_storage_form_is_valid(self):
        self.assertEqual(self.v2["schema_version"], 2)
        self.assertEqual(st._storage_errors(self.v2, self.notes), [])
        self.assertEqual(st.validate(st.join_view(self.v2, self.notes)), [])
        self.assertEqual(len(self.creates), 13)
        self.assertEqual(self.v2["supervisor"]["last_ua_number"], 160)
        self.assertEqual(st.migrate_v2(self.v1, "2030-01-01T00:00:00+09:00")[1], self.creates)

    def test_round_trip_keeps_titles_requests_actions_and_artifacts(self):
        exported = st.export_v1(self.v2, self.notes)
        self.assertEqual(st.validate(exported), [])
        self.assertEqual(exported["schema_version"], 1)
        for key in ("tasks", "archived"):
            for old, new in zip(self.v1[key], exported[key]):
                with self.subTest(key=key, id=old["id"]):
                    self.assertEqual(new["id"], old["id"])
                    self.assertEqual((new["title"], new["request"]), (old["title"], old["request"]))
                    self.assertEqual(new["artifacts"], old["artifacts"])
                    keep = [(u["id"], u["description"], u.get("follow_up")) for u in old.get("user_actions", [])
                            if not u["description"].startswith(st.READ_ACTION_PREFIX)]
                    self.assertEqual([(u["id"], u["description"], u.get("follow_up")) for u in new.get("user_actions", [])
                                      if not u["id"].startswith(st.HELD_UA_PREFIX)], keep)
                    self.assertFalse(set(st.V2_RUNTIME_KEYS) & set(new))
        for old, new in zip(self.v1["projects"], exported["projects"]):
            self.assertEqual((new["name"], new["context_doc_url"]), (old["name"], old["context_doc_url"]))
            self.assertEqual([u["id"] for u in new["user_actions"]], [u["id"] for u in old["user_actions"] if "proposal" not in u])
        self.assertEqual(find(exported["tasks"], "T-0120")["proposal"], find(self.v1["tasks"], "T-0120")["proposal"])

    def test_uids_notes_and_props(self):
        self.assertEqual(find(self.v2["tasks"], "T-0102")["uid"], "20261028_110000")
        self.assertEqual(find(self.v2["tasks"], "T-0101")["uid"], "20261028_110001")
        expected = {"T-0102": ("in progress", "user"), "T-0090": ("done", "agent"), "T-0106": ("in progress", "agent"),
                    "T-0120": ("in progress", "agent")}
        for task_id, pair in expected.items():
            self.assertEqual(st._pair(st.note_values(self.note_of(task_id))), pair, task_id)
        self.assertEqual(find(self.v2["tasks"], "T-0090")["held"], ["w-0090"])
        self.assertEqual(st.parse_frontmatter(self.note_of("T-0120"))[0]["parent"], f"[[{find(self.v2['projects'], 'P-0001')['uid']}]]")
        for stub_id, pair, date_key in (("T-0088", ("done", "user"), "completed_at"), ("T-0050", ("archived", "user"), "archived_at")):
            props = st.parse_frontmatter(self.note_of(stub_id, "archived"))[0]
            self.assertEqual(((props["status"], props["assignee"])), pair)
            self.assertTrue(props[date_key])
        self.assertEqual(find(self.v2["archived"], "T-0050")["project"], {"id": "P-0002"})
        self.assertEqual(note_section(self.note_of("T-0102"), "概要").strip(), "repo-a の不具合を直して。push して PR を作ってよい")
        log = note_section(self.note_of("T-0095"), "ログ")
        self.assertIn("    > b.md も\n    > 見直して", log)

    def test_user_action_kinds(self):
        uas = note_uas(self.note_of("T-0102"))
        self.assertEqual((uas["UA-0121"]["kind"], uas["UA-0121"]["detector"], uas["UA-0121"]["rule"]), ("auto", "pr_closed", "merge-pr"))
        self.assertEqual((uas["UA-0122"]["kind"], uas["UA-0122"]["url"]), ("ask", "https://github.com/org/repo-a/pull/12"))
        self.assertEqual(uas["UA-0153"]["detector"], "worker_report")
        self.assertTrue(note_uas(self.note_of("T-0101"))["UA-0156"]["answer_required"])
        self.assertEqual(note_uas(self.note_of("T-0090")), {})
        project = note_uas(self.note_of("P-0001", "projects"))
        self.assertEqual(list(project), ["UA-0148"])
        self.assertEqual(project["UA-0148"]["detector"], "manager_report")
        child = next(c for c in self.creates if "ドキュメントを足す" in c["text"])
        props = st.parse_frontmatter(child["text"])[0]
        self.assertEqual((props["status"], props["assignee"], props["parent"]),
                         ("backlog", "user", f"[[{find(self.v2['projects'], 'P-0001')['uid']}]]"))
        self.assertEqual(note_section(child["text"], "概要").strip(), "README に使い方を書く")

    def test_artifact_groups_and_generations(self):
        def rows(task_id, key="tasks"):
            return [(i["g"], i["n"], i["kind"]) for i in st.parse_artifacts(note_section(self.note_of(task_id, key), "成果物"))[0]]
        self.assertEqual(rows("T-0102"), [(1, 1, "branch"), (1, 2, "commit"), (3, 3, "pr"), (4, 4, "branch"), (4, 5, "commit"), (6, 6, "pr")])
        self.assertEqual(rows("T-0095"), [(1, 1, "file"), (1, 2, "file")])
        self.assertEqual(rows("T-0088", "archived"), [(1, 1, "file"), (1, 2, "file")])
        self.assertEqual(rows("T-0096", "archived"), [(1, 1, "file"), (2, 2, "branch"), (2, 3, "commit"), (4, 4, "pr")])
        revived = find(self.v2["tasks"], "T-0103")
        self.assertEqual((revived["art_from"], revived["last_artifact_number"]), (4, 5))
        self.assertEqual(find(self.v2["archived"], "T-0103")["uid"], revived["uid"])
        self.assertEqual(rows("T-0103"), [(1, 1, "branch"), (1, 2, "commit"), (3, 3, "pr"), (4, 4, "branch"), (4, 5, "commit")])
        self.assertEqual(find(self.v2["tasks"], "T-0106")["art_from"], 1)

    def test_cli_migrate_and_export(self):
        env = VaultEnv(self)
        env.state_path.write_text(LIVE_SHAPE.read_text(encoding="utf-8"), encoding="utf-8")
        code, out, err = env.run("migrate-v2", "--state", str(env.state_path), "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual((json.loads(out)["dry_run"], len(json.loads(out)["notes"])), (True, 13))
        self.assertFalse(env.vault.exists())
        # An interrupted run (notes written, state still v1) is completed by running again.
        with mock.patch.object(st, "_write_atomic", side_effect=Crash):
            with self.assertRaises(Crash):
                env.run("migrate-v2", "--state", str(env.state_path))
        code, out, err = env.run("migrate-v2", "--state", str(env.state_path))
        self.assertEqual(code, 0, err)
        self.assertEqual(env.load()["schema_version"], 2)
        self.assertEqual(len(st._index_vault(str(env.vault))[0]), 13)
        self.assertNotIn("dashboard", env.load()["supervisor"])
        self.assertEqual(env.run("migrate-v2", "--state", str(env.state_path))[0], 1)
        self.assertEqual(env.scan(), {"events": [], "warnings": []})
        out_path = env.dir / "rollback" / "state.v1.json"
        self.assertEqual(env.run("export-v1", "--state", str(env.state_path), "--out", str(env.state_path))[0], 1)
        code, out, err = env.run("export-v1", "--state", str(env.state_path), "--out", str(out_path))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["tasks_without_workers"], [])
        exported = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual([t["title"] for t in exported["tasks"]], [t["title"] for t in self.v1["tasks"]])
        code, _, err = env.commit()
        self.assertEqual(code, 0, err)
        state = env.load()
        state["tasks"][0]["worker_ids"] = []
        env.state_path.write_text(st.serialize(state), encoding="utf-8")
        code, out, err = env.run("export-v1", "--state", str(env.state_path), "--out", str(out_path))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["tasks_without_workers"], [state["tasks"][0]["id"]])

    def test_main_digest_reads_the_export(self):
        main_tool = subprocess.run(["git", "-C", str(SKILL), "show", "main:skills/session-supervisor/scripts/state_tool.py"],
                                   capture_output=True, text=True)
        if main_tool.returncode != 0:
            self.skipTest("main branch not available")
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / "state_tool.py"
            tool.write_text(main_tool.stdout, encoding="utf-8")
            exported = Path(tmp) / "state.json"
            exported.write_text(st.serialize(st.export_v1(self.v2, self.notes)), encoding="utf-8")
            proc = subprocess.run([sys.executable, str(tool), "digest", "--state", str(exported)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("state not readable", proc.stdout)
        for needle in ("T-0102", "UA-0153", "作業 T-0090", "P-0001 サンプル基盤"):
            self.assertIn(needle, proc.stdout)



class VerifyFixesTest(unittest.TestCase):
    def setUp(self):
        self.env = VaultEnv(self)
        self.env.add_task("T-0020", "w-0020", U1)
        self.env.save()

    def create(self, uid, title):
        return {"op": "create", "uid": uid, "title": title, "kind": "task", "status": "backlog", "assignee": "user", "request": title}

    def test_new_note_count_reserves_distinct_uids(self):
        class Fixed(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 10, 9, 0, 0)
        with mock.patch.object(st, "datetime", Fixed):
            code, out, _ = self.env.run("new-note", "--state", str(self.env.state_path), "--kind", "task", "--count", "3")
        self.assertEqual([n["uid"] for n in json.loads(out)], ["20261110_090000", "20261110_090002", "20261110_090003"])
        self.assertEqual(self.env.run("new-note", "--state", str(self.env.state_path), "--kind", "task", "--count", "0")[0], 1)

    def test_a_second_create_for_one_uid_is_rejected(self):
        before = self.env.state_path.read_bytes()
        code, _, err = self.env.commit(None, {U3: [self.create(U3, "提案A"), self.create(U3, "提案B")]})
        self.assertEqual(code, 1)
        self.assertIn(f"note {U3}: 2 create ops", err)
        code, _, err = self.env.commit(None, {U1: [self.create(U1, "上書き")]})
        self.assertEqual(code, 1)
        self.assertIn(f"note {U1} already exists", err)
        self.assertEqual(self.env.state_path.read_bytes(), before)
        self.assertFalse(self.env.path(U3).exists())
        self.assertEqual(self.env.commit(None, {U3: [self.create(U3, "提案A")]})[0], 0)
        self.assertIn("提案A", self.env.text(U3))

    def finished_note_without_stub(self):
        cand = self.env.load()
        find(cand["workers"], "w-0020").update(intended_state="archived", observed_state="archived")
        commit_ops = {U1: [{"op": "art_add", "items": [{"kind": "branch", "ref": "/repo/a#claude/license-x1"}, {"kind": "commit", "ref": "abc123",
                                                                                                                  "url": "https://github.com/org/repo-a/commit/abc123"}]},
                           {"op": "ua_add", "ua": {"kind": "slot", "description": "追加の依頼(任意。サブアイテムに書いて backlog/agent にしてください)"}}]}
        self.assertEqual(self.env.commit(cand, commit_ops)[0], 0)
        self.assertEqual(st._pair(self.env.values(U1)), ("done", "agent"))
        state = self.env.load()
        state["archived"] = []  # the stub left the ARCHIVED_KEEP window
        self.env.state = state
        self.env.save()

    def test_a_note_whose_stub_aged_out_is_quiet_until_followed_up(self):
        self.finished_note_without_stub()
        self.assertEqual(self.env.scan()["events"], [])
        self.env.put(U1, self.env.text(U1).replace('status: "done"', 'status: "pending"').replace('assignee: "agent"', 'assignee: "user"'))
        self.assertEqual(self.env.scan()["events"], [])
        text = self.env.text(U1).replace('status: "pending"', 'status: "backlog"').replace('assignee: "user"', 'assignee: "agent"')
        self.env.put(U1, text.replace("^ua-0001", "^ua-0001\n    - README の別の typo も直して"))
        event = self.env.scan()["events"][0]
        self.assertEqual((event["event"], event["row"], event["live"], event["former"]), ("follow_up", 16, False, True))
        self.assertEqual((event["slot"], event["follow_up_text"], event["last_artifact_number"]), ("UA-0001", "- README の別の typo も直して", 2))
        cand = self.env.load()
        git_task(cand, "T-0030", "w-0030", intended="pending", observed="none")
        find(cand["workers"], "w-0030")["session_id"] = None
        task = to_v2(find(cand["tasks"], "T-0030"), "tasks", U1)
        task.update(note_seen=event["ack"], art_from=event["last_artifact_number"] + 1, last_artifact_number=event["last_artifact_number"])
        code, out, err = self.env.commit(cand, {U1: [{"op": "ua_ack", "id": "UA-0001"}, {"op": "log", "text": "追加の依頼:\n> README の別の typo も直して"}]})
        self.assertEqual(code, 0, err)
        props = st.parse_frontmatter(self.env.text(U1))[0]
        self.assertEqual((props["status"], props["assignee"], props["completed_at"]), ("in progress", "agent", None))
        view = st.join_view(self.env.load(), {U1: self.env.text(U1)})
        self.assertEqual(find(view["tasks"], "T-0030")["artifacts"], [])
        self.assertEqual(self.env.scan()["events"], [])

    def test_unmarked_unmanaged_notes_are_still_picked_up(self):
        self.env.put(U3, st.new_note_text(U3, "新しい依頼", "task", "backlog", "agent", NOTE_NOW, "やって"))
        self.assertEqual([e["event"] for e in self.env.scan()["events"]], ["pickup"])

    def test_invalid_change_on_a_stub_uses_note_warnings(self):
        self.finished_note_without_stub()
        state = self.env.load()
        state["archived"] = [{"id": "T-0020", "uid": U1, "archived_at": NOW, "note_seen": self.env.values(U1)}]
        self.env.state = state
        self.env.save()
        self.env.put(U1, self.env.text(U1).replace('status: "done"', "status: doing"))
        event = self.env.scan()["events"][0]
        self.assertEqual((event["event"], event["warning_key"]), ("invalid", U1))
        state["supervisor"]["note_warnings"] = {U1: event["warning_sha"]}
        self.env.save()
        self.assertEqual(self.env.scan()["events"], [])

    def test_a_carried_entry_replays_under_its_own_rev(self):
        writes, _ = failing_writes({1}, OSError)
        with writes:
            code, out, _ = self.env.commit(None, {U1: [{"op": "log", "text": "最初の記録"}]})
        self.assertEqual(out["notes_failed"], [st._new_note_path(U1)])
        # The next commit cannot write the replay either (carried), but writes its own log on the same note.
        writes, _ = failing_writes({1}, OSError)
        with writes:
            code, out, err = self.env.commit(None, {U1: [{"op": "log", "text": "二回目の記録"}]})
        self.assertEqual(code, 0, err)
        self.assertEqual([e.get("rev") for e in self.env.load()["supervisor"]["note_journal"]["entries"]], [1, 2])
        self.assertEqual(self.env.commit()[0], 0)
        log = note_section(self.env.text(U1), "ログ")
        self.assertEqual((log.count("最初の記録"), log.count("二回目の記録")), (1, 1))


SID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def herdr_worker(state, task_id="T-0030", worker_id="w-0030", **kw):
    git_task(state, task_id, worker_id)
    w = find(state["workers"], worker_id)
    w.update(runtime="herdr", session_id=SID, handle=worker_id, title=f"{task_id}|{worker_id} fix it",
             cwd="/trees/a/fix", model="sonnet", **kw)
    return w


class FakeHc:
    """Records hc argv and answers ls / get / send from canned data."""

    def __init__(self, terminals=(), sv_address="t1"):
        self.terminals, self.sv_address, self.calls = list(terminals), sv_address, []

    def __call__(self, argv):
        self.calls.append(argv)
        out = {"ls": {"terminals": self.terminals}, "get": {"address": self.sv_address}}.get(argv[1], {"started": True})
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")

    def sent(self):
        return [c[-1] for c in self.calls if c[1] == "send"]


class HerdrSettingsTest(unittest.TestCase):
    def test_parse_and_defaults(self):
        self.assertEqual(st.parse_herdr("## Herdr\n- max_workers: 3\n- worker_path: /a:/b\n"),
                         ({"max_workers": 3, "worker_path": "/a:/b"}, []))
        self.assertEqual(st.parse_herdr("# T\n"), ({}, []))
        for bad in ("- max_workers: 0\n", "- max_workers: x\n", "- worker_path:\n", "- color: red\n"):
            with self.subTest(bad=bad):
                self.assertTrue(st.parse_herdr("## Herdr\n" + bad)[1])
        self.assertEqual(st.default_herdr("/usr/bin", None), {"max_workers": 8})
        self.assertEqual(st.default_herdr("/usr/bin", "/s"), {"max_workers": 8, "worker_path": "/s:/usr/bin"})
        self.assertEqual(st.default_herdr("/s:/usr/bin", "/s"), {"max_workers": 8})

    def test_add_missing_herdr_keeps_values_and_is_idempotent(self):
        once = st.add_missing_herdr("# T\n", "/usr/bin", "/s")
        self.assertEqual(st.parse_herdr(once), ({"max_workers": 8, "worker_path": "/s:/usr/bin"}, []))
        self.assertEqual(st.add_missing_herdr(once, "/usr/bin", "/s"), once)
        mine = "## Herdr\n- max_workers: 2\n"
        self.assertEqual(st.parse_herdr(st.add_missing_herdr(mine, "/usr/bin", None))[0], {"max_workers": 2})


class HerdrSchemaTest(unittest.TestCase):
    def test_herdr_worker_is_valid(self):
        state = fixture_state()
        herdr_worker(state)
        self.assertEqual(st.validate(state), [])
        find(state["workers"], "w-0030")["session_id"] = None
        self.assertEqual(st.validate(state), [])

    def test_herdr_worker_needs_uuid_and_matching_handle(self):
        for change, needle in (({"session_id": "local_abc"}, "uuid"), ({"handle": "x"}, "handle"),
                               ({"runtime": "tmux"}, "runtime")):
            with self.subTest(change=change):
                state = fixture_state()
                herdr_worker(state).update(change)
                self.assertTrue(any(needle in e for e in st.validate(state)), st.validate(state))

    def test_herdr_manager_handle(self):
        state = fixture_state()
        add_project(state)
        state["projects"][0]["manager"].update(runtime="herdr", session_id=SID, handle="m-p-0001")
        self.assertEqual(st.validate(state), [])
        state["projects"][0]["manager"]["handle"] = "m-P-0001"
        self.assertTrue(any("handle" in e for e in st.validate(state)))


class LaunchPlanTest(unittest.TestCase):
    def test_worker_argv(self):
        state = fixture_state()
        herdr_worker(state)
        argv = st.launch_argv(state, "w-0030", "Fix it.", {"worker_path": "/s:/usr/bin"})
        self.assertEqual(argv, [
            "hc", "run", "--bg", "--json", "--name", "w-0030", "--cwd", "/trees/a/fix", "--label", "sv=worker",
            "--label", "task=T-0030", "--outlive-parent", "env", "PATH=/s:/usr/bin", "claude", "--session-id", SID,
            "--model", "sonnet", "--permission-mode", "auto", "-n", "T-0030|w-0030 fix it",
            "--allowedTools=Bash(hc send sv:*)", "Fix it."])
        self.assertEqual(argv[-1], "Fix it.")

    def test_manager_argv(self):
        state = fixture_state()
        add_project(state)
        state["projects"][0]["manager"].update(runtime="herdr", session_id=SID, handle="m-p-0001")
        argv = st.launch_argv(state, "m-p-0001", "Review.", {})
        self.assertIn("sv=manager", argv)
        self.assertIn("project=P-0001", argv)
        self.assertEqual(argv[argv.index("--cwd") + 1], PROJECT_DIR)
        self.assertNotIn("env", argv)

    def test_refusals(self):
        state = fixture_state()
        w = herdr_worker(state)
        w["session_id"] = None
        with self.assertRaisesRegex(st.StateError, "uuid4"):
            st.launch_argv(state, "w-0030", "x", {})
        w["session_id"] = SID
        with self.assertRaisesRegex(st.StateError, "empty"):
            st.launch_argv(state, "w-0030", "  ", {})
        with self.assertRaisesRegex(st.StateError, "neither"):
            st.launch_argv(state, "w-9999", "x", {})
        herdr_worker(state, "T-0031", "w-0031", intended_state="running", observed_state="running")
        herdr_worker(state, "T-0032", "w-0032", intended_state="running", observed_state="missing")
        with self.assertRaisesRegex(st.StateError, "max_workers 1 reached \\(w-0031\\)"):
            st.launch_argv(state, "w-0030", "x", {"max_workers": 1})
        self.assertTrue(st.launch_argv(state, "w-0030", "x", {"max_workers": 2}))

    def test_cli(self):
        env = VaultEnv(self)
        state = fixture_state()
        herdr_worker(state)
        env.state_path.write_text(st.serialize(state), encoding="utf-8")
        (env.dir / "CLAUDE.md").write_text("## Herdr\n- max_workers: 4\n", encoding="utf-8")
        (env.dir / "prompt.md").write_text("Fix it.\nThen report.", encoding="utf-8")
        code, out, err = env.run("launch-plan", "--state", str(env.state_path), "--claude-md", str(env.dir / "CLAUDE.md"),
                                 "--worker", "w-0030", "--prompt-file", str(env.dir / "prompt.md"))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["argv"][-1], "Fix it.\nThen report.")


class ObserveHcTest(unittest.TestCase):
    def test_mapping(self):
        state = fixture_state()
        for i, intended in enumerate(("running",) * 5 + ("archived",)):
            herdr_worker(state, f"T-004{i}", f"w-004{i}", intended_state=intended, observed_state="idle")
        terminals = [
            {"address": "t1", "name": "w-0040", "state": "live", "status": "working", "turn": 2},
            {"address": "t2", "name": "w-0041", "state": "live", "status": {"app": "claude-code", "state": "blocked",
                                                                           "kind": "permission", "msg": "approve Bash: ls"}},
            {"address": "t3", "name": "w-0042", "state": "live", "status": None},
            {"address": "t4", "name": "w-0043", "state": "finished", "exit_code": 129, "reason": "killed"},
            {"address": "t6", "name": "w-0045", "state": "finished", "exit_code": 129, "reason": "killed"},
            {"address": "t9", "name": None, "state": "live", "status": "done"},
        ]
        got = {o["id"]: o for o in st.observe_hc(state, terminals)}
        self.assertEqual({k: v["observed_state"] for k, v in got.items()},
                         {"w-0040": "running", "w-0041": "needs_input", "w-0042": "idle", "w-0043": "missing",
                          "w-0044": "missing", "w-0045": "archived"})
        self.assertEqual(got["w-0041"]["blocked_msg"], "approve Bash: ls")
        self.assertEqual((got["w-0043"]["exit_code"], got["w-0043"]["reason"]), (129, "killed"))
        self.assertNotIn("w-0030", got)

    def test_app_workers_are_ignored_and_cli_reads_a_file(self):
        env = VaultEnv(self)
        state = fixture_state()
        herdr_worker(state)
        env.state_path.write_text(st.serialize(state), encoding="utf-8")
        (env.dir / "ls.json").write_text(json.dumps({"terminals": [{"address": "t5", "name": "w-0030", "state": "live",
                                                                    "status": "done"}]}), encoding="utf-8")
        code, out, err = env.run("observe-hc", "--state", str(env.state_path), "--from", str(env.dir / "ls.json"))
        self.assertEqual(code, 0, err)
        self.assertEqual([(o["id"], o["observed_state"]) for o in json.loads(out)], [("w-0030", "idle")])


class WatchVaultTest(unittest.TestCase):
    def test_messages_dedupe_and_parent_rule(self):
        scan = {"events": [{"uid": "u1", "event": "pickup", "ack": None}]}
        memo = {}
        terminals = [
            {"address": "t5", "name": "w-0001", "state": "live", "status": "blocked", "parent": "t9"},
            {"address": "t6", "name": "w-0002", "state": "finished", "parent": "t1"},
            {"address": "t7", "name": "build", "state": "finished", "parent": "t9"},
        ]
        self.assertEqual(st.watch_messages(scan, terminals, "t1", memo),
                         ["notes: u1", "worker: w-0001 blocked (hc read t5)"])
        self.assertEqual(st.watch_messages(scan, terminals, "t1", memo), [])
        terminals[0]["status"] = "working"
        self.assertEqual(st.watch_messages({"events": []}, terminals, "t1", memo), [])
        terminals[0]["status"] = "blocked"
        self.assertEqual(st.watch_messages(scan, terminals, "t1", memo), ["notes: u1", "worker: w-0001 blocked (hc read t5)"])

    def test_tick_scans_notes_and_sends(self):
        env = VaultEnv(self)
        env.save()
        env.put("20261111_090000", st.new_note_text("20261111_090000", "Fix README", "task", "backlog", "agent", NOTE_NOW,
                                                    request="Fix the README typo."))
        hc = FakeHc([{"address": "t4", "name": "w-0009", "state": "finished", "parent": "t2"}])
        memo = {}
        sent = st.watch_tick(str(env.state_path), "sv", hc, memo)
        self.assertEqual(sent, ["notes: 20261111_090000", "worker: w-0009 finished (hc read t4)"])
        self.assertEqual(hc.sent(), sent)
        self.assertEqual([c[:3] for c in hc.calls if c[1] == "send"], [["hc", "send", "sv"]] * 2)
        self.assertEqual(st.watch_tick(str(env.state_path), "sv", hc, memo), [])

    def test_cli_once_writes_nothing(self):
        env = VaultEnv(self)
        env.save()
        before = env.state_path.read_bytes()
        hc = FakeHc()
        args = st.build_parser().parse_args(["watch-vault", "--state", str(env.state_path), "--once"])
        self.assertEqual(st.cmd_watch_vault(args, runner=hc), 0)
        self.assertEqual(env.state_path.read_bytes(), before)
        self.assertEqual(hc.sent(), [])


class HcGuardTest(unittest.TestCase):
    BLOCK = [
        "hc keys w-0001 enter",
        "hc exec 'ls'",
        "hc attach sv",
        "hc server stop",
        "hc update",
        "hc machine add vps",
        "hc file read /etc/hosts",
        "hc put w-0001 x.txt",
        "hc connect",
        "sh -c 'hc keys w-0001 down enter'",
        "python3 -c \"import subprocess; subprocess.run(['hc', 'keys', 'w-0001', 'enter'])\"",
        "hc run --bg --name w-0001 claude 'x'",
        "hc kill t1",
        "hc rm build",
        "hc send --force w-0001 yes",
    ]
    ALLOW = [
        "hc run --bg --json --name w-0001 --cwd /t --label sv=worker --label task=T-0001 --outlive-parent claude 'x'",
        "hc run --bg --name sv-watch --label=sv=watch python3 state_tool.py watch-vault --state s.json",
        "hc kill w-0001",
        "hc rm m-p-0006",
        "hc kill sv-watch",
        "hc send w-0001 'continue'",
        "hc ls --all --json",
        "hc get w-0001 --json",
        "hc read --lines 50 w-0001",
        "hc events --label sv=worker",
    ]

    def test_block(self):
        for command in self.BLOCK:
            with self.subTest(command=command):
                self.assertTrue(st.bash_violations(command))

    def test_allow(self):
        for command in self.ALLOW:
            with self.subTest(command=command):
                self.assertEqual(st.bash_violations(command), [])


def git(*argv, cwd):
    subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1"))


class WorktreeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.sup = self.root / "sup"
        self.sup.mkdir()
        self.trees = self.root / "trees"
        self.md = self.sup / "CLAUDE.md"
        self.md.write_text(f"## Locations\n- clone_root: {self.root / 'src'}\n- work_root: {self.root / 'work'}\n"
                           f"- worktree_root: {self.trees}\n", encoding="utf-8")
        self.repo = self.root / "src" / "repo-a"
        self.repo.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=self.repo)
        (self.repo / "a.txt").write_text("hello\n", encoding="utf-8")
        git("add", "a.txt", cwd=self.repo)
        git("-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init",
            cwd=self.repo)
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = st.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def prepare(self, branch="claude/t0001-fix", *extra):
        return self.run_cli("prepare-worktree", "--claude-md", str(self.md), "--repo", str(self.repo), "--branch", branch,
                            "--base", "main", *extra)

    def test_prepare_then_exists_then_remove(self):
        code, out, err = self.prepare()
        self.assertEqual(code, 0, err)
        got = json.loads(out)
        dest = self.trees / "repo-a" / "t0001-fix"
        self.assertEqual((got["status"], got["path"], got["branch"]), ("created", str(dest), "claude/t0001-fix"))
        self.assertTrue((dest / "a.txt").exists())
        self.assertEqual(json.loads(self.prepare()[1])["status"], "exists")
        (dest / "b.txt").write_text("x", encoding="utf-8")
        code, _, err = self.run_cli("remove-worktree", "--claude-md", str(self.md), "--path", str(dest))
        self.assertEqual(code, 1)
        self.assertIn("uncommitted", err)
        (dest / "b.txt").unlink()
        code, out, err = self.run_cli("remove-worktree", "--claude-md", str(self.md), "--path", str(dest))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["branch"], "claude/t0001-fix")
        self.assertFalse(dest.exists())
        branches = subprocess.run(["git", "-C", str(self.repo), "branch", "--list", "claude/t0001-fix"],
                                  capture_output=True, text=True).stdout
        self.assertIn("claude/t0001-fix", branches)

    def test_prepare_refusals(self):
        self.assertEqual(self.prepare("bad..branch")[0], 1)
        self.assertEqual(self.prepare("claude/x", "--slug", "../escape")[0], 1)
        (self.trees / "repo-a" / "taken").mkdir(parents=True)
        code, _, err = self.prepare("claude/taken")
        self.assertEqual(code, 1)
        self.assertIn("is not a worktree", err)
        code, _, err = self.run_cli("prepare-worktree", "--claude-md", str(self.md), "--repo", str(self.root),
                                    "--branch", "claude/y", "--base", "main")
        self.assertEqual(code, 1)
        self.assertIn("not a git repository", err)

    def test_remove_only_under_worktree_root(self):
        code, _, err = self.run_cli("remove-worktree", "--claude-md", str(self.md), "--path", str(self.repo))
        self.assertEqual(code, 1)
        self.assertIn("not under worktree_root", err)

    def test_worktree_root_must_not_nest(self):
        self.md.write_text(f"## Locations\n- clone_root: {self.root / 'src'}\n- worktree_root: {self.root / 'src' / 't'}\n",
                           encoding="utf-8")
        code, _, err = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("nested", err)


if __name__ == "__main__":
    unittest.main()
