"""Consumer-driven contract test: the secretary reads the session-supervisor's state.json and user-action wording.

Runs only when the session-supervisor skill is installed next to this skill.
"""
import json
import re
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SKILL = TESTS.parent
SUPERVISOR = SKILL.parent / "session-supervisor"
SV_FIXTURE = SUPERVISOR / "tests" / "fixtures" / "state_valid.json"
SV_SKILL_MD = SUPERVISOR / "SKILL.md"
sys.path.insert(0, str(SKILL / "scripts"))

import state_tool as st  # noqa: E402

NOW = "2026-10-18T09:30:00+09:00"

# (phrase that identifies a user action quoted in the supervisor's SKILL.md, expected relay_mode)
QUOTED_UAS = (
    ("Please archive", "app"),
    ("once in the app", "app"),
    ("is not progressing", "paste"),
    ("Decide whether to push", "paste"),
    ("or unpin it", "app"),
    ("Answer the pending approval", "app"),
    ("Tell w-xxxx in the app", "app"),
    ("can start now", "paste"),
)


@unittest.skipUnless(SV_FIXTURE.is_file(), "session-supervisor skill is not installed alongside")
class SupervisorContractTest(unittest.TestCase):
    def setUp(self):
        self.sv = json.loads(SV_FIXTURE.read_text(encoding="utf-8"))

    def test_fixture_passes_supervisor_errors(self):
        self.assertEqual(st.supervisor_errors(self.sv), [])

    def test_merge_supervisor_takes_fixture_user_actions(self):
        expected = [(t["id"], ua["id"], ua["description"], ua["since"]) for t in self.sv["tasks"] for ua in t.get("user_actions", [])]
        self.assertTrue(expected, "supervisor fixture should carry at least one user action")
        merged = st.merge_supervisor(st.empty_state("/sec", "/sv"), self.sv, NOW)
        self.assertIsNone(merged["supervisor"]["error"])
        self.assertEqual(merged["supervisor"]["state_rev"], self.sv["rev"])
        got = [(d["ref"]["task_id"], d["ref"]["ua_id"], d["description"], d["since"]) for d in merged["decisions"]]
        self.assertEqual(sorted(got), sorted(expected))
        self.assertTrue(all(d["source"] == st.RESERVED_SOURCE for d in merged["decisions"]))

    def test_quoted_user_actions_are_classified(self):
        quotes = re.findall(r'"([^"\n]+)"', SV_SKILL_MD.read_text(encoding="utf-8"))
        for phrase, mode in QUOTED_UAS:
            with self.subTest(phrase=phrase):
                matches = [q for q in quotes if phrase in q]
                self.assertTrue(matches, f"no quoted user action containing {phrase!r} in the supervisor's SKILL.md")
                for q in matches:
                    self.assertEqual(st.classify_supervisor_ua(q), mode, q)


if __name__ == "__main__":
    unittest.main()
