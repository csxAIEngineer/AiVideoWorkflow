import json
import unittest
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
ASSETS = SUITE / "skills/short-drama-assets"


class RuleTests(unittest.TestCase):
    def test_one_spelling_per_pronunciation_blocks(self) -> None:
        # A second spelling is invisible in text review and only audible in the
        # finished cut, so it cannot wait for a judgement call.
        contract = (ASSETS / "references/stage-contract.md").read_text(encoding="utf-8")
        row = next(r for r in contract.splitlines() if r.startswith("| AST-10 "))
        self.assertIn("structural_invariant", row)


class RecordShapeTests(unittest.TestCase):
    """The shipped example must stay a legal record, since it is what a run copies."""

    def test_example_carries_a_reference_first_voice_direction(self) -> None:
        records = [
            json.loads(line)
            for line in (ASSETS / "assets/character-look.example.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        sources = records[0]["sources"]
        record = next(row for row in records if "voice_direction" in row)
        direction = record["voice_direction"]

        reference = direction["reference"]
        # Timbre rides on a recording, and the recording stays in creator inputs.
        artifact = sources[reference["artifact_ref"]["src"]]["artifact"]
        self.assertTrue(artifact.startswith("输入/"))
        self.assertIn(reference["admission_status"], {"creator_described", "audibly_inspected", "unverified"})
        self.assertTrue(reference["may_control"])
        # Present in every recording, belonging to none of them.
        excluded = "".join(reference["must_not_control"])
        self.assertIn("情绪", excluded)
        self.assertIn("混响", excluded)

        # Criteria judge a candidate; an unbounded one gets executed further
        # every take, so each carries its own upper bound.
        self.assertTrue(direction["selection_criteria"])
        for criterion in direction["selection_criteria"]:
            self.assertTrue(criterion["counter_example"])

        self.assertTrue(direction["distinction"]["nearest_character_id"])
        # Excluded and simply-absent must stay distinguishable downstream.
        self.assertTrue(direction["not_voice_identity"])


if __name__ == "__main__":
    unittest.main()
