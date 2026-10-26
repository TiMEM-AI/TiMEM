"""Offline regression tests using the two real cross-question collisions."""
import json
import unittest

from full_answer import FULL, QUESTIONS, question_item, score_cache_key


class QuestionCacheRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
        cls.config = {"answer_base": "https://example.invalid/v1", "judge_base": "https://example.invalid/v1",
                      "answer_model": "deepseek-v4.1-flash", "judge_model": "deepseek-v4.1-flash"}

    def evidence(self, qid, arm="B"):
        return json.loads((FULL / "evidence" / arm / f"{qid.replace('#','_')}.json").read_text(encoding="utf-8"))["evidence"]

    def test_real_collisions_do_not_share_an_answer(self):
        for earlier, later in (("conv-26#q0025", "conv-26#q0026"), ("conv-47#q0016", "conv-47#q0066")):
            with self.subTest(later=later):
                self.assertEqual(self.evidence(earlier), self.evidence(later))
                cache = {}
                for qid in (earlier, later):
                    key = score_cache_key(question_item(self.questions[qid]), self.evidence(qid), self.config)
                    cache.setdefault(key, {"answered_question": qid})
                    self.assertEqual(cache[key]["answered_question"], qid)
                self.assertEqual(len(cache), 2)

    def test_identical_arms_of_one_question_still_share(self):
        for qid in ("conv-26#q0026", "conv-47#q0066"):
            with self.subTest(qid=qid):
                keys = {score_cache_key(question_item(self.questions[qid]), self.evidence(qid, arm), self.config)
                        for arm in ("B", "L", "H")}
                self.assertEqual(len(keys), 1)

    def test_changed_scoring_input_invalidates_cached_result(self):
        item = question_item(self.questions["conv-47#q0066"])
        evidence = self.evidence(item["question_id"])
        original = score_cache_key(item, evidence, self.config)
        variants = [({**item, "question": item["question"] + " in another year"}, evidence, self.config),
                    ({**item, "gold": "another gold"}, evidence, self.config),
                    (item, evidence + "\nAdditional fact.", self.config),
                    (item, evidence, {**self.config, "answer_model": "another-model"}),
                    (item, evidence, {**self.config, "judge_model": "another-judge"}),
                    (item, evidence, {**self.config, "judge_base": "https://another.invalid/v1"})]
        for altered_item, altered_evidence, altered_config in variants:
            with self.subTest(item=altered_item["gold"], model=altered_config["answer_model"]):
                self.assertNotEqual(original, score_cache_key(altered_item, altered_evidence, altered_config))


if __name__ == "__main__":
    unittest.main()
