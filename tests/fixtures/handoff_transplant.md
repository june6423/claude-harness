---
{
  "kind": "handoff",
  "experiment_id": "VLM-OPSD-TRANSPLANT-001",
  "revision": 1,
  "predecessor": "NONE",
  "status": "APPROVED",
  "authorization": "FULL_RUN",
  "approval": "handoff를 수정했어. base 4B를 full run으로 실행하자. A6000 GPU 6,7번 2장을 사용해줘",
  "gates": [
    {
      "id": "G1",
      "phase": "PRE_RUN",
      "check": "The smoke run produces a scored output for each of the eight smoke rows."
    },
    {
      "id": "G2",
      "phase": "PRE_RUN",
      "check": "The bound commit changes only the code that the NATIVE_BASE condition needs."
    },
    {
      "id": "G3",
      "phase": "POST_RUN",
      "check": "The main run produces 700 candidates and 700 scores, with 100 for each benchmark."
    }
  ],
  "full_run_condition": "NONE"
}
---
## Authorization and decision delta

Direct USER instruction (2026-10-07) requests the missing BASE 4B benchmark scores on Experiment 2's700 samples.
Authorize minimal LOCAL implementation/config changes, affected-path validation, deployment, BASE generation,
fresh scoring and delivery. After scoped readiness VALID, complete700 without another user confirmation.
A small code change is expected by the user, not yet verified by ET.

Add one scored condition, NATIVE_BASE: native BASE vision encoder, main merger/projector, DeepStack and decoder;
NO transplant hooks, OPSD weights or recipient computation. Generate its OWN autoregressive continuation,
not donor activations or responses conditioned on OPSD prefixes.
This USER addition overrides the prior no-extra-scored-arm restriction only for NATIVE_BASE.
NATIVE_OPSD / PP_BASE_DS / PRE_BASE_DS / POST_BASE_DS definitions/results remain unchanged.
Do not cancel, restart or regenerate those four runs for this addition.

## Bound specifications and precedence

Read shared/TRANSPLANT_EVALUATION_SPEC.md, TRANSPLANT-EVAL-SPEC-2026-10-03-01,
SHA-256 1c5fdfe1ed2b35233a58b57e082d178d2da5e4148ab5198bfc25a428fff35d74;
and shared/TRANSPLANT_EXECUTION_GATES.md, TRANSPLANT-RUN-GATES-2026-10-03-01,
SHA-256 2ae9fce13c0f05b5265a8955344e3c1df1055035c2f1e8584639533796e5cbd3.
Input/decoding/scorer/evidence/numeric requirements remain fixed. This handoff overrides their four-arm-only
workload and older implementation-only authority for NATIVE_BASE; transplant-specific G2/four-arm smoke is
not required for this native addition. Readiness is scoped below, not a repeat of the whole original experiment.

The predecessor four-arm contract remains byte-identical in shared/TRANSPLANT_IMPLEMENTATION_HANDOFF.md,
SHA-256 e9437d354b77009b941dc1ecb3f1037678ee5e0e0ee09732778d5af3fa7331be.
It is now a preserved predecessor, NOT a current delivery copy. Preserve original source/bindings/results/
failures; unfinished predecessor work retains its own authority and provenance. Do not repeat2800 candidates.

## Frozen evaluation contract

BASE: Qwen3-VL-4B-Instruct@ebb281ec70b05090aa6165b016eac8ec08e71b17.
Manifest: e4485f83c1f3fa467d1fd9b0a53e9afaffe388689b5b27ebb45e8e6264306dfe.
BLINK, MMStar, Vstar-Bench, MathVista, HRBench-4K, HRBench-8K, HallusionBench:100 each.
Keep exact700 IDs/order/images/questions/options/dataset membership, including HR4K/8K correspondence.
No resampling or selection by outcomes; reference answers are scorer-only.
Use actual accepted Experiment 2 processor/chat-template/input and dtype/backend/runtime bindings, not defaults.
Raw greedy argmax, do_sample=False, max_new_tokens=2048, seed0, eval, native EOS/end-of-turn, use_cache=False.
No sampling/repetition transforms or custom stopping; persist tokens/text/count/EOS/finish/cap metadata.

Existing native BASE candidates may be reused only with complete provenance proving exact compatibility
with this model/input/generation/runtime contract and termination metadata. Bind original receipts; determine
eligibility independently of correctness. Sample-ID matching, donor forwards, teacher-forced logits and old
scores are insufficient. Otherwise generate missing/incompatible BASE candidates. Do not reopen Experiment 1
maps/panels or undertake a separate restoration project for reuse.

Freshly score all700 BASE responses using the SAME bound Experiment 2 procedures:
BLINK/MMStar/Vstar lmms task-specific judge-free paths; MathVista/HR4K/HR8K/Hallusion LOCAL-MISTRAL-JUDGE.
Judge: mistralai/Mistral-Small-3.2-24B-Instruct-2506@95a6d26c4bfb886c58daf9d3f7332c857cb27b43,
unquantized BF16, unchanged prompts/parsers/rules; hide arm names from the judge.
Retain MathVista true-cap incorrect/no-extraction and other accepted cap/extraction policies.
Technical/judge-transport failures are missing evidence, not incorrect answers. No historical score imports.

## Accepted evidence and scoped ET sequence

ET report SHA-256 64d8c1ad490ee2c5c18f9bf4e1c045a65a8c5c59e9b1996483bdc167ff58283d,
dated2026-10-03 12:59 KST:86 tests PASS, pushed commit a40cbafa997872230d13caae79689709370c1a2d,
original G1–G6 ALL VALID. Required r7 stages exit0; Bridge overall FAIL from optional missing /usr/bin/time
remains recorded. MAIN1338/2800 and32 smoke scores are DATED, not current progress/full-run validity.
Establish current task/resource state before scheduling; DT has not inspected server/code.

1. Reuse accepted loader/runner/decoder/scorers; change only native BASE/config/reporting paths locally.
   Test affected paths, commit/push, bind exact source; remote fetch/verify/execute only. No broad refactor.
2. Use A6000 physical GPUs6,7 ONLY, including judge; verify UUID/container visibility and
   CUDA_VISIBLE_DEVICES mapping. Reuse compatible container/image when task-owned resources are released.
   Queue if predecessor work is active; do not interrupt it or mutate its mounted checkout/environment.
   Isolate source/output paths and run candidate/judge phases sequentially if needed. No other server/GPU.
3. Bind new run/scorer manifests and revision-specific output root. Use existing fixed7/8 smoke IDs for BASE
   only: check model/own-prefix/native DeepStack/hook-free execution, resume identity, fresh scoring across
   seven paths and aggregate replay. Carry forward unchanged accepted runtime/scorer validation, rechecking
   materially affected components only. A new generation path must pass inherited G3 last-logit/greedy/EOS/
   cap checks and numeric tolerances; fully compatible validated candidate reuse may rely on bound provenance.
   Explicitly adjudicate scoped readiness VALID/PARTIAL/INVALID; only VALID releases remaining700 work.
4. Finish missing BASE generation and fresh scoring; preserve accepted candidates/failures, prevent duplicate
   or incompatible resume, supervise through terminal evidence. Repairs remain local-first and in budget.

## Deliverables and boundaries

Deliver per-item BASE predictions/scores/raw judge evidence and seven SUBSET-100 scores: correct/scorable/
planned100, accuracy, cap/failure counts; labelled unweighted seven-benchmark macro only with all seven cells.
Native group/category metrics stay separate and NOT_ESTIMABLE if required members are absent; no extra rows.
Provide a BASE supplement table alongside available validated four-arm results with provenance/coverage labels.
Do not overwrite their tables or treat unadjudicated scores as validated. Original transplant drops remain
NATIVE_OPSD minus transplant on the original common-FOUR mask; NEVER use common-FIVE or BASE as that reference.
Replay saved counts/aggregates under inherited tolerances; report generation/scoring/aggregation validity.
Completion requires700 valid primary scores; technical gaps remain PARTIAL, not silent exclusions.

No REF8B, new training/checkpoint, attention/EGG rerun, alternative judge or new scientific metric.
No researcher choice remains for this bounded request; escalate genuine semantic/authority conflicts or hard
limits, not fixed choices or Codex CLI versions. Interpretation remains OPEN; subset scores do not establish
perception equivalence. ET must not edit handoffs/specs/PROJECT_STATE/controller budgets; preserve task lineage.
Saved controller remains old Experiment 1 PAUSED/GOAL_STOPPED; external NEW cycle must bind this revision,
not resume the old binding. DT does not dispatch ET or start/restart infrastructure.
