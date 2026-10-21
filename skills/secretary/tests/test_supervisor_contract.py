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
TEMPLATE = (SKILL / "scripts" / "dashboard.template.html").read_text(encoding="utf-8")
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
    ("went missing or unverified", "paste"),
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

    def follow_up_decision(self):
        merged = st.merge_supervisor(st.empty_state("/sec", "/sv"), self.sv, NOW)
        ua = next(ua for t in self.sv["tasks"] for ua in t.get("user_actions", []) if ua["id"] == "UA-0003")
        d = next(d for d in merged["decisions"] if d["ref"]["ua_id"] == "UA-0003")
        return merged, ua, d

    def test_follow_up_survives_merge_and_shows_under_your_tasks(self):
        merged, ua, d = self.follow_up_decision()
        self.assertIsNotNone(ua.get("follow_up"), "supervisor fixture UA-0003 should carry follow_up")
        self.assertEqual(d["follow_up"], ua["follow_up"])
        self.assertTrue(st.is_follow_up(d))
        self.assertEqual(d["relay_mode"], "paste")
        page = st.assemble(*st.render(merged, TEMPLATE, NOW))
        tasks = re.search(r'<section class="your-tasks".*?</section>', page, re.S).group(0)
        decisions = re.search(r'<section class="decisions".*?</section>', page, re.S).group(0)
        self.assertIn(ua["description"], tasks)
        self.assertIn(ua["follow_up"]["url"], tasks)
        self.assertNotIn(ua["description"], decisions)

    def test_follow_up_paste_text_is_quoted_in_supervisor_skill(self):
        _, _, d = self.follow_up_decision()
        text = st.paste_text(d, "done")
        self.assertEqual(text, 'T-0003 UA-0003 ("Review repo-a#42"): done')
        self.assertLessEqual(len(d["description"]), st.PASTE_DESC)
        self.assertIn(text, SV_SKILL_MD.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
