"""Suggested repair yields a candidate, then repair_story_graph writes a new version."""

import json
import tempfile
import unittest
from pathlib import Path

from end.script_repair import load_candidate, official_script, run_script_repair
from end.script_review import content_hash, save_reviews

_REQUIREMENTS = "古塔长发困人。柳公子手掌和衣袖粘着发束悬在半空。阿青在塔顶。"
_REACH = "柳公子已经悬在发束旁边，手掌就贴着发根。"
_SCRIPT = "古塔里长发把人困住。柳公子的手掌和衣袖粘在发束上，人悬在半空。阿青站在塔顶。"
_CANDIDATE = _SCRIPT + "他还没碰到发根。"
_NEW_VERSION = _SCRIPT + _REACH
_ISSUE = {
    "location": "塔外",
    "evidence": "手掌和衣袖粘在发束上",
    "category": "causal",
    "severity": "block",
    "reason": "洗发没有交代怎么够到",
    "owner": "script",
    "revision": "可交代他已经够得到发根",
}


class RepairStoryGraphTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "project"
        internal = self.folder / "_内部_"
        internal.mkdir(parents=True)
        (internal / "剧本.md").write_text(_SCRIPT + "\n", encoding="utf-8")
        save_reviews(self.folder, {
            "script": {
                "status": "blocked",
                "issues": [_ISSUE],
                "script_hash": content_hash(_SCRIPT),
                "context_hash": "x",
            }
        })
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def _complete(self, node, messages):
        self.calls.append((node, messages))
        payload = json.loads(messages[1]["content"])
        draft = str(payload.get("script") or payload.get("original") or "")
        first_pass = _SCRIPT in draft and _CANDIDATE not in draft
        if node == "locate":
            paragraph = "手掌和衣袖粘在发束上" if first_pass else "手掌就贴着发根"
            return json.dumps({"locations": [{"issue_id": "S01", "paragraphs": [paragraph]}]}, ensure_ascii=False)
        if node == "plan":
            return json.dumps({
                "changes": [{"issue_id": "S01", "direction": "补触达", "linked": True}],
                "constraints": [],
            }, ensure_ascii=False)
        if node == "revise":
            text = _CANDIDATE if first_pass else _NEW_VERSION
            added = [{"issue_id": "S01", "text": _REACH, "basis": "悬在半空"}] if not first_pass else [{"issue_id": "S01", "text": "他还没碰到发根。", "basis": "悬在半空"}]
            return json.dumps({"script": text, "added": added}, ensure_ascii=False)
        if node == "review":
            passed = not first_pass
            evidence = _REACH if passed else ""
            return json.dumps({
                "passed": passed,
                "theme_ok": passed,
                "resolutions": [{"id": "S01", "resolved": passed, "evidence": evidence, "change": evidence, "aligned": passed}],
                "unsupported": [],
                "new_contradictions": [],
            }, ensure_ascii=False)
        raise AssertionError(node)

    def test_suggested_repair_sends_candidate_to_story_graph_for_a_new_version(self):
        row = run_script_repair(self.folder, _REQUIREMENTS, self._complete, model="fake-model")
        self.assertEqual(official_script(self.folder).strip(), _SCRIPT)
        saved = load_candidate(self.folder)
        self.assertEqual(saved.get("status"), "passed")
        self.assertEqual((saved.get("candidate") or saved.get("rejected_text") or "").strip(), _NEW_VERSION)
        self.assertEqual(row.get("candidate", "").strip(), _NEW_VERSION)
        drafts = []
        for node, messages in self.calls:
            payload = json.loads(messages[1]["content"])
            drafts.append((node, str(payload.get("script") or payload.get("original") or "")))
        second_locate = [draft for node, draft in drafts if node == "locate"][1]
        self.assertEqual(second_locate, _CANDIDATE)
        issue_lists = []
        for node, messages in self.calls:
            if node != "locate":
                continue
            payload = json.loads(messages[1]["content"])
            issue_lists.append(payload.get("issues") or [])
        self.assertEqual(issue_lists[1][0]["id"], "S01")
        self.assertGreaterEqual(len(self.calls), 8)

    def test_retry_starts_from_rejected_text_not_the_official_script(self):
        from end.script_repair import _save_candidate, budget_identity, issue_rows

        identity = budget_identity(official_script(self.folder), _REQUIREMENTS, issue_rows(self.folder))
        _save_candidate(self.folder, {
            "status": "blocked",
            "rejected_text": _CANDIDATE,
            "issues": issue_rows(self.folder),
            "attempts": 1,
            **identity,
        })
        row = run_script_repair(self.folder, _REQUIREMENTS, self._complete, model="fake-model")
        self.assertEqual(official_script(self.folder).strip(), _SCRIPT)
        self.assertEqual([item[0] for item in self.calls], ["locate", "plan", "revise", "review"])
        first = json.loads(self.calls[0][1][1]["content"])
        self.assertEqual(first.get("script"), _CANDIDATE)
        self.assertEqual(first.get("issues")[0]["id"], "S01")
        self.assertEqual((row.get("candidate") or "").strip(), _NEW_VERSION)
        self.assertEqual(row.get("status"), "passed")
        self.assertEqual(load_candidate(self.folder)["attempts"], 2)



if __name__ == "__main__":
    unittest.main()
