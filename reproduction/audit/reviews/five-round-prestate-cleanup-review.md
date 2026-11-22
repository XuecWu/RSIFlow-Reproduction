# Five-round pre-state cleanup review

Date: 2026-11-19 UTC
Base: `beebd9b42b734c0f5a1dd6ba927931ee76b81f43`
Scope: uncommitted pre-PersistentMeta missing-key cleanup proof.

## Verdict

Spec compliance: FAIL. Task quality: Needs fixes. Critical: 0. Important: 3. Minor: 0.

1. `summarize_deepcoder_five_round.py`: job terminal `result.json`/`request.json` artifacts must be compared with prior cleanup snapshots, including async running turn receipts with independent terminal results.
2. `summarize_deepcoder_five_round.py`: turn immutability needs a previous complete hash inventory, not mtime-only checks.
3. `summarize_deepcoder_five_round.py`: launcher log validation must reject additional errors and ambiguous traceback structure, rather than only finding one expected traceback.

The full repository suite at the initial implementation passed, but the identified evidence-integrity gaps remain release blockers. No real 0004 cleanup or 0005 launch was performed.

Evidence: worktree review diff/report `task-6-prestate-launch-cleanup-review.diff`, `task-6-prestate-launch-cleanup-report.md`; independent reviewer agent-59. A bounded fix and scoped re-review are required.

## Fix 1 re-review

The full suite passed: **980 passed, 2 skipped**. Job result/request hashing and canonical traceback validation were accepted. Actual 0004 remains **Not Ready** (reviewer agent-60):

- Real 0003 has only nine turn snapshots and no complete `protocol.turn_inventory`; current history has 75 turns. Hashing current files cannot create a historical baseline.
- Real 0003 sealed A0 while current workflow is B2 after the reviewed production-history recovery fix. This transition must be validated, not accepted by weakening stage equality.
- Nested validation of consecutive pre-state receipts must use an explicit historical supervision prefix, not global latest-attempt discovery.

A further bounded implementation is addressing these real-schema compatibility cases. No real cleanup or launch has occurred. Current hashing of all turns is not being represented as historical proof; if a sufficient independent anchor cannot be established, the corresponding invariant remains blocked.

## Final scoped verdict

Reviewer agent-60 reports **Critical=0, Important=0, Minor=0; Ready for real 0004 cleanup** under the explicitly disclosed `causal_no_new_0004_work` scope. The previous job-owned turn set is exactly the nine non-contiguous turns `0018,0035,0037,0042,0045,0049,0052,0061,0065`; all 18 command/receipt snapshots and current job hashes match. The legacy path does not claim full historical turn-byte immutability. New-format receipts still require the complete turn hash baseline.

This is code-review readiness only. A completed cleanup still requires live owner-exit checks, two GPU observations and closed ports. The final repository full suite is running before commit and real cleanup.
