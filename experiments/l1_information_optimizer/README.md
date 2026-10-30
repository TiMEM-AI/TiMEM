# L0 → L1 information-preservation optimizer (experimental)

This is an offline optimizer and measurement harness for the AML branch’s `MemoryGenerator.generate_l1_content` prompt, not an automatic production trainer. The bundled pilot did **not** establish lower loss: the baseline and shortlisted candidate each measured 0.0 proxy loss on the small synthetic validation sets. The baseline therefore wins the strict loss gate, and no new policy is enabled by default.

## Method

A dialogue window is one evaluation case. Its manually supplied, atomic `facts` are a reference, not an exhaustive account of human memory. For each candidate on the **same** L0 case, an independent LLM judge labels each fact `supported`, `partial`, `missing`, or `contradicted`, and lists unsupported additions. Per case:

- omission = (missing + contradicted + 0.5 × partial) / reference fact count;
- contradiction = contradicted / reference fact count;
- hallucination = 1 if any unsupported addition exists, otherwise 0;
- proxy loss = 0.7 × omission + 0.2 × contradiction + 0.1 × hallucination;
- utility = 1 − proxy loss − 0.05 × min(summary/source character ratio, 2).

Contradiction is deliberately penalized as both lost reference information and an explicit contradiction. These weights are design choices, not information-theoretic entropy. The length term encourages concision without allowing it to override the validation loss gate. The judge sees the same source, facts and candidate memory for every policy, but not the candidate identity. JSON/schema errors or truncated model output fail the run rather than being scored as zero. The evaluation output can include sensitive source content: **use only synthetic or explicitly authorized data, protect the output directory, and never commit private traces, prompts or credentials.**

A generator LLM proposes two extra instructions from **training aggregates only**. For every training case the fixed cohort is evaluated head-to-head; reward minus its per-case cohort mean yields a zero-centered advantage. Advantage-weighted updates adjust a discrete distribution over candidates; its top two form a shortlist with the baseline. Validation chooses the lowest measured loss, with ties retaining the baseline. A disjoint test split is evaluated only after selection. This is a small RAAS-inspired discrete controller, not a reproduction of RAAS’s full supernet or gradient estimator; no LLM weights are trained. Candidate order rotates between cases.

The original AML template can vary by dataset. The default training template is `config/datasets/locomo/prompts.yaml`; specify the actual dataset prompt with `--prompt-config`. Both L1 generation paths accept an optional explicitly selected artifact via `TIMEM_L1_OPT_POLICY_PATH`; its stored SHA-256 must match the exact active template or generation fails before calling the LLM. Unset the environment variable to preserve the original behavior. **Do not enable a candidate until a larger consented, independently annotated held-out evaluation and downstream regression tests support promotion.**

## Running

Requires Python, `httpx`, `PyYAML`, and an OpenAI-compatible endpoint. Supply credentials through the environment only. The optimization model and generator default to `deepseek-v4.1-flash`; the independent evaluator defaults to `glm-5.3-flash` with `reasoning_effort=low`. Models and base URL can be overridden on the CLI. Endpoint support for the evaluator setting is required.

```powershell
$env:TIMEM_OPT_BASE_URL = "https://example.invalid/v1"
$env:TIMEM_OPT_API_KEY = "<set securely>"
python experiments/l1_information_optimizer/optimizer.py --data experiments/l1_information_optimizer/examples_dense.jsonl --out <private-output-dir>
```

Input JSONL entries contain unique `id`, `split` (`train`, `validation`, or `test`), `new_dialogue`, optional `previous_summary`, and a nonempty list of reference `facts`. Use independently authored, grouped splits to avoid shared people, sessions, source conversations and templates. The script checks exact duplicate IDs/dialogues, not semantic or group leakage. Local request caches and the report contain model outputs; `report.json` includes per-case judgments and `winner.json` contains the template-bound policy.

The offline harness calls the configured endpoint directly with a 4,096-token budget; the deployed generator uses its configured adapter and streaming behavior. The pilot therefore tests prompt-level behavior, not full production-path performance. Apply a promoted artifact only through the existing runtime and remeasure end-to-end `Add`/`Search`.

## Pilot and limits

`pilot_results.json` records two small 2026-10-29 synthetic pilots using DeepSeek generation/optimizer and GLM judgment. The dense pilot has 12/6/6 train/validation/test examples and reused four test examples from an earlier exploratory pilot whose results were already observed. The stress pilot has 5/2/2 and shares a repeated template across splits. Neither is an untouched, out-of-distribution test of generalization. In both, baseline and shortlisted candidate had zero estimated validation loss; the baseline was retained. The selected baseline’s held-out estimate was also zero. Four simple diagnostic fixtures showed that the judge could identify one omission, one contradiction, and one unsupported addition, but do not establish judge reliability.

Future work: collect and independently annotate realistic L0 windows, group splits by dialogue/user, measure inter-rater agreement, calibrate the judge against human labels, use confidence intervals and cost budgets, and evaluate both information loss and downstream task accuracy on an untouched held-out set.
