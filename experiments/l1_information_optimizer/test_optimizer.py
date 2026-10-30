import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "l1opt", Path(__file__).with_name("optimizer.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class OptimizerTest(unittest.TestCase):
    def test_zero_centered_per_example_and_update(self):
        a = m.relative_advantages([0.9, 0.6, 0.3])
        self.assertAlmostEqual(sum(a), 0)
        self.assertGreater(a[0], 0)
        self.assertLess(a[2], 0)
        weights = m.probabilities(m.logits_update([0, 0, 0], a, 0.5))
        self.assertGreater(weights[0], weights[2])
        self.assertAlmostEqual(sum(weights), 1)

    def test_omission_contradiction_and_hallucination(self):
        facts = ["x"]
        good = m.score_judgment(
            {"facts": ["supported"], "unsupported_claims": []}, facts, "x", "x"
        )
        missing = m.score_judgment(
            {"facts": ["missing"], "unsupported_claims": []}, facts, "x", "x"
        )
        wrong = m.score_judgment(
            {"facts": ["contradicted"], "unsupported_claims": ["invented"]},
            facts,
            "x",
            "x",
        )
        self.assertEqual(good["loss"], 0)
        self.assertGreater(wrong["loss"], missing["loss"])

    def test_invalid_judge_results_fail_closed(self):
        with self.assertRaises(ValueError):
            m.score_judgment({"facts": [], "unsupported_claims": []}, ["x"], "a", "x")
        with self.assertRaises(ValueError):
            m.score_judgment(
                {"facts": ["maybe"], "unsupported_claims": []}, ["x"], "a", "x"
            )

    def test_baseline_exact_and_no_template_injection(self):
        e = {"new_dialogue": "literal {date}", "previous_summary": "old"}
        template = "{previous_summary} / {new_dialogue}"
        self.assertEqual(m.prompt_for(template, e, ""), "old / literal {date}")
        self.assertIn("literal {date}", m.prompt_for(template, e, "preserve dates"))

    def test_splits_are_disjoint(self):
        rows = m.load_examples(m.DATA)
        self.assertEqual({r["split"] for r in rows}, {"train", "validation", "test"})
        self.assertEqual(len({r["new_dialogue"] for r in rows}), len(rows))


if __name__ == "__main__":
    unittest.main()
