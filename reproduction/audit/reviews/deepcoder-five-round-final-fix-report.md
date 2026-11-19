# Task 7 final review fix report

## Status

IMPLEMENTED; scoped verification passed. Repository-wide full suite, final website/JSON/static/secret/HTTP checks and main-checkout audit ledger remain with the main agent as assigned.

Implemented all three Important areas at base `5071b705273975c5ee2657ed8c21595fd84e8465`, without subagents, commits, experiment launch/stop, real evidence mutation or GPU lease edits.

- MODEL execute: `reproduction/scripts/deepcoder_model_smoke.py` now rejects noncanonical/symlink execute roots before resolution and rechecks inside the stage lock. Completed SFT/recovery receipts must match the loaded plan identity, expected stage/optional operation, canonical root/output paths and SFT output seals; recovery must match the bound SFT child. Existing v2 receipts without an `operation` field remain valid.
- Selection/activation: `reproduction/scripts/summarize_deepcoder_five_round.py` delegates production-field parsing to `experiment_progress._selection_choice`, preserving `accept_or_retain` priority and its exact whitelist. Decision-only legacy evidence is accepted only when neither production field exists. Accept requires one unique, fully bound successful activation across the complete round inventory; earlier failures are permitted. Retain rejects any successful activation.
- Docs/web: published the accepted bounded five-round result, actual per-round counts, B2 SFT, selected-state chain, attempts/recoveries, cleanup and occupier `stopped/restore_pending`; preserved formal five-domain/3000/694/744/held-out/author-reproduction boundaries. Corrected MODEL smoke v1 Retry 5 wording to `not executed again` while retaining Retry 4's executed failure.

Files changed:

- `reproduction/scripts/deepcoder_model_smoke.py`
- `reproduction/scripts/summarize_deepcoder_five_round.py`
- `RSIFlow/tests/test_training_side_smoke.py`
- `RSIFlow/tests/test_deepcoder_five_round.py`
- `reproduction/audit/reviews/deepcoder-five-round-real-results.md` (new)
- `reproduction/audit/reviews/deepcoder-model-smoke-report.md`
- `reproduction/audit/reviews/deepcoder-five-round-runbook.md`
- `reproduction/PROGRESS.md`
- `reproduction/REPRODUCTION.md`
- `AGENTS.md`
- `web/data/reproduction.json`
- `web/RESULTS.md`
- `web/OPERATIONS.md`
- `web/tests/test_web.py`
- `.superpowers/sdd/2026-11-18-deepcoder-five-round-canary/task-7-final-review-fix-report.md` (this report)

The four modified `RSIFlow/runtime/local_baseline/resource_leases/gpu_*.lock` files were pre-existing at task start and were not read, edited or staged by this wave.

## Tests

All commands ran from `/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round/RSIFlow` unless noted. Python is `/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python`; pytest environment is `PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts"`.

1. MODEL RED, before production fix:

   ```bash
   PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests/test_training_side_smoke.py -k 'valid_final_receipts_resume_without_retraining or stale_plan_stage_receipt or foreign_root_stage_receipt or post_plan_root_symlink_swap'
   ```

   Result: **5 failed, 1 passed, 151 deselected**. Both stale-plan cases and both foreign-root cases returned `already_completed`; the root symlink swap was followed into SFT. The valid resume control passed.

2. MODEL GREEN and complete model-smoke regression:

   ```bash
   PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests/test_training_side_smoke.py -k 'valid_final_receipts_resume_without_retraining or stale_plan_stage_receipt or foreign_root_stage_receipt or post_plan_root_symlink_swap'
   PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests/test_training_side_smoke.py -k 'model_smoke'
   ```

   Results: **6 passed, 151 deselected**, then **66 passed, 91 deselected**. An intermediate missing-plan diagnostic regression was caught by the 66-test run and fixed while preserving fail-closed root handling; the reported 66-test result is after that fix.

3. Real v2 receipt compatibility control (read-only; no execute or evidence writes):

   ```bash
   PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python - <<'PY'
   # Load Rollout_logs/deepcoder_model_smoke_v2/receipts/{plan,sft,recovery}.json.
   # Call _validate_completed_stage(root, plan, 'sft', sft), then
   # _validate_completed_stage(root, plan, 'recovery', recovery, sft=sft).
   # Assert both plan identities equal 8398911273705e2cbb1b812e01f27588f1daf8f9509dfa27b1ce59c768567865.
   PY
   ```

   Result: `real v2 receipt rebinding controls: PASS`. The real root is a real directory; all canonical output paths and hashes passed.

4. Selection/activation RED, before production fix:

   ```bash
   PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests/test_deepcoder_five_round.py -k 'conflicting_dual_selection_fields or later_success_after_earlier_activation_failure or multiple_successful_activations'
   ```

   Result: **4 failed, 200 deselected**. Conflicting fields used the wrong precedence, an earlier failed activation hid a later success, and duplicate successes were accepted.

5. Selection/activation GREEN and isolated MODEL guards:

   ```bash
   PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests/test_deepcoder_five_round.py -k 'conflicting_dual_selection_fields or later_success_after_earlier_activation_failure or multiple_successful_activations or complete_run_is_legal_and_complete or real_shaped_model_retain_round'
   PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests/test_deepcoder_five_round.py -k 'model_retain_rejects_successful_activation or model_false_accept_isolated or model_retain_tampering_fails_closed'
   ```

   Results: **6 passed, 198 deselected**, then **9 passed, 196 deselected**. False-accept now tests only the positive-gain guard with a valid activation; retain-with-activation independently tests only the no-activation guard.

6. Legacy decision-only regression discovered and resolved:

   ```bash
   PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests/test_deepcoder_five_round.py
   PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests/test_deepcoder_five_round.py -k 'i8_realistic_zero_call_overlap or real_single_run_observed_facts_readonly or conflicting_dual_selection_fields'
   ```

   Initial full-file result: **2 failed, 203 passed** (`test_i8_realistic_zero_call_overlap`, `test_real_single_run_observed_facts_readonly`) because both rewrite/read decision-only evidence. After restricting legacy fallback to receipts with neither production field, targeted result: **4 passed, 201 deselected**. The final two-file run below supersedes the intermediate full-file failure.

7. Final complete affected-file run:

   ```bash
   PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests/test_training_side_smoke.py tests/test_deepcoder_five_round.py
   ```

   Result: **362 passed in 138.29s**.

8. Real accepted final-summary assertions (read-only, in-memory summary):

   ```bash
   PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python - <<'PY'
   # summarize(run, config_path=config, cleanup_receipt_path=supervision/0008 cleanup receipt)
   # Assert run_complete, completed_rounds=5, protocol_complete, launcher_exited,
   # external_cleanup.ok, and exact per-round success/activation tuples.
   PY
   ```

   Result: `real five-round final summary assertions: PASS`; B1–B5 respectively `ARTIFACTS 2/16->3/16 accept activated`, `MODEL 4/16->3/16 retain_parent not activated`, `ARTIFACTS 2/16->3/16 accept_candidate activated`, `ARTIFACTS 2/16->3/16 accept_candidate activated`, `ARTIFACTS 4/16->5/16 accept_candidate activated`.

9. Documentation package syntax/static checks from worktree root:

   ```bash
   python3 -m json.tool web/data/reproduction.json >/dev/null
   git diff --check
   ```

   Result: both exit 0. `git status --short` shows only the intended files above plus the four pre-existing GPU lease modifications; no test output files were created in the worktree.

Not run by this wave, by explicit ownership boundary: repository-wide full suite, website unittest/Node/final JSON-schema/static/secret/HTTP checks, and main-checkout audit ledger updates. The accepted base full-suite evidence remains **1020 passed, 2 skipped** and is not presented as a post-fix full-suite result.

## Concerns

- Main agent must run/record the final repository-wide suite and final web/static/secret/HTTP checks, then update the main-checkout audit ledger. This report does not claim those checks.
- Occupier remains **stopped/`restore_pending`** because NVML PID ownership is unresolved. Four GPUs were free at final cleanup; this does not prove occupier restoration. No bypass was attempted.
- Public results remain bounded to the repeated 16-task single-domain cohort. Only 6–8 valid submissions per side entered full verifier; ARTIFACTS is zero-new-Qwen-call Meta-assisted output repair, B2's SFT candidate was not activated, and ENOENT/EAGAIN/zero-call overlaps limit attribution. No held-out, formal five-domain/3000/694/744, author reproduction or general capability claim is made.
- Brief-listed nonblocking follow-ups were intentionally not expanded into this wave: numeric policy type consistency, real v4 scheduler/ToyModel mkdir coverage, unequal round-alias/changed-inherited-payload additions, cross-mount `st_dev`, legacy cosmetics, zero-call error-label wording and old-review trailing whitespace.
