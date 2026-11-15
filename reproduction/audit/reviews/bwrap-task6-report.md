# Task 6 report: Real Codex Dynamic Tool and Restart/Resume Smoke on SCO

Status: **DONE_WITH_CONCERNS** (offline implementation, tests, self-review, and commit are complete; the single mandatory SCO job did not start a worker within the bounded ten-minute wait).

- Base: `a28e44077a2ae86ca29b093cab898255bf3e6379`.
- Commit: `e9f56cbb6d376efa9c185a6fd7e126c6a65b1950` (`test: verify persistent Codex tools and resume in bubblewrap`).
- Branch/worktree: `reproduction/4b-bwrap`, `/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/bwrap-isolation`.
- SCO: exactly one job, `pt-f8joesmo`; it remains `STARTING` with a Pending worker. No duplicate or replacement was submitted.
- Audit root: `/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit`.

## Implementation

- Created `reproduction/scripts/persistent_meta_bwrap_smoke.py`.
  - Uses the real Task 5 `PersistentMetaSandbox`, host `ResponsesTransport`, `CodexAppServer`, `dynamic_tools()`, `NativeExperimentTools`, `ExperimentJobs`, and `SubprocessToolExecutor` interfaces.
  - Creates a fresh run and public JSON fixture, starts a real bwrap app-server with native dynamic tools, and performs bounded `read_json`, one `/bin/sh -c 'sleep 20'` durable-job, post-resume `job_status`, and boolean-only native-shell key-hygiene turns.
  - Closes the first app-server/sandbox without stopping the host job, reconstructs all controller objects, resumes the saved thread with `excludeTurns=True`, and reconciles the durable job set and PID before any new turn.
  - Requires exactly one original job/PID, no new job or bootstrap receipt, the same thread, exact returned model `gpt-6-astra`, complete provider receipts, a successful terminal job result, and app/process-group/relay cleanup while `codex_home` and provider evidence persist.
  - Snapshots provider evidence before and after restart so the second transport cannot overwrite first-phase evidence. Reports only sanitized metadata, IDs, booleans, and hashes; raw prompts and provider/app responses are not copied into the report or sanitized event ledger.
  - Uses the existing audit sanitizer plus explicit parent-only value replacement. Native shell output is parsed only as two booleans; environment values are never printed.
- Created `reproduction/configs/meta_bwrap_smoke.json` with backend `linux_bwrap_seccomp_v1` selected by the launch command, provider `OpenAI`, model `gpt-6-astra`, upstream `http://token.acerobotics.com/v1`, no model/provider fallback, pinned Codex/catalog identities, a 480-second budget, and at most 16 requests.
- Added six reconciliation tests to `RSIFlow/tests/test_persistent_meta_sandbox.py`: same thread/original job acceptance, changed thread, new job, second bootstrap, missing dynamic-tool receipt, and provider model mismatch.
- No existing controller, transport, sandbox, website, data protocol, training, or evaluation file was changed. The pre-existing modified `reproduction/audit/operations.jsonl` was preserved and excluded from the commit.

## TDD RED/GREEN evidence

All verification commands ran through `reproduction/scripts/audit_exec.py` with the absolute audit root. The common test environment was:

```bash
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
  /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q <tests>
```

| Audit sequence | Phase | Observed output |
|---|---|---|
| 942 | RED before the smoke module existed: `pytest -q RSIFlow/tests/test_persistent_meta_sandbox.py` | **6 failed, 17 passed**. All six new tests failed with `ModuleNotFoundError: persistent_meta_bwrap_smoke`, the expected missing-feature reason. |
| 943 | GREEN after minimal `reconcile_resume` / `validate_smoke_report` implementation | **23 passed**. |
| 944 | Focused suite after the bounded real harness was added | **23 passed**. |
| 945 | `persistent_meta_bwrap_smoke.py --check`; no provider call or real key | Exit 0; every pinned hash, exact provider/relay/model, dynamic-tool, workspace-write, network-disabled, no-danger-full-access, and budget check was `true`. |
| 949 | Focused recheck | **23 passed**. |
| 950 | Final Ruff on the new script and modified test file | `All checks passed!` |
| 951 | Full suite, run once from `RSIFlow`: `pytest -q tests` | **270 passed, 2 skipped, 1 failed**; only the known unrelated failure below. |
| 953 | `git diff --cached --check` | Exit 0. |
| 954 | Commit | `e9f56cbb6d376efa9c185a6fd7e126c6a65b1950`. |

Ruff initially rejected three broad top-level exception handlers (audits 946 and 948). They were narrowed from `BaseException` and then retained as explicit `Exception` handlers with targeted `BLE001` suppressions because the harness must always attempt cleanup and write a sanitized failure report. Final audit 950 is clean.

Audit key results: `00000959-persistent_meta_reconciliation_tests`, `00000961-persistent_meta_bwrap_offline_check`, and `00000963-full_suite`.

## Real SCO smoke evidence

### Submission and bounded observation

- Job `pt-f8joesmo`, display name `rsiflow-meta-bwrap-task6-20261113T133415Z`; submission audit 956.
- Approved workspace/AEC2: `sensebot-a100-a800-liujianbo`.
- Image: `registry.cn-sh-01.sensecore.cn/ccr-sensebot-ccr/starvla7:lihao-dev-20260426172526`.
- Worker: one node `N3lS.Ii.I60.8`, NORMAL/reserved.
- Volume: `019fdaf3-33db-7bbf-992b-bc2f64d0b2fd:/data`.
- Container command: 480-second TERM/KILL bound and fresh output root `reproduction/logs/meta-bwrap-task6-20261113T133415Z`.
- Audit 957 performed exactly 20 state polls from `2026-11-13T13:35:51Z` through `2026-11-13T13:45:30Z` (579 seconds). Every poll returned `STARTING`.
- Audit 965 final state: `STARTING`, `create_time=2026-11-13T13:34:53Z`, `complete_time=null`, `replica_count=0`.
- Audit 966 worker `pt-11170ceae7644a5ca3a3da07028040e7-worker-0`: `Pending`, with no host or POD IP.
- Audit 967: `needs_context.txt`, worker stdout/stderr, exit-code file, `run/report.json`, and sanitized events all have `exists=false` and no SHA256.

The command sources only `assets/private/credentials.sh` inside the SCO shell with tracing disabled, checks only key presence, and would write `NEEDS_CONTEXT` if the approved file/key were unavailable. No credential content is in `--env`, the submission, tracked config, prompt, report, or command log. No other secret file is consulted. Because no worker started, the credential-file check did not execute; availability is therefore unknown, not reported as `NEEDS_CONTEXT`.

### Sanitized evidence hashes

- Submission stdout (audit 956): `bf47e6c0cfd0a73b0addf3b81207d490e38efff8a4321545ecaf4dd30b174393`.
- Twenty-poll monitor stdout (audit 957): `daeccd6e698ed4919b22b25a47577e44826b2c78620181f26873d8a2c40a38c2`.
- Final state stdout (audit 965): `2961903b084c79f07c02cc6da82c5e905ebbe73aac92f1b41c55e64fb3c3654a`.
- Final worker stdout (audit 966): `225d5a2f674cbc1ddca548411b3576c39edefee0a4f11b8f7446c37e9e8f8a90`.
- Output-presence stdout (audit 967): `9f5d5b8295cdc11fcfc050a2cdf719a1bc9651d014a3776ab72fbc60285c8dae`.
- Empty stderr for audits 956/957/965/966/967: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

Audit key result: `00000969-persistent_meta_bwrap_real_sco` = `STARTING_pending_after_20_polls_no_execution`.

The job is left intact as instructed. No worker compute was assigned, so there is no terminal job/worker or resource-release claim. Real returned model, thread IDs, durable job ID/PID, dynamic-tool receipts, provider-phase receipts, key-hygiene output, terminal durable-job result, cleanup evidence, and their hashes were **not produced**. The configured model/provider identity is not represented as a real returned-model observation. Offline tests and earlier direct Responses success are not represented as substitutes for this mandatory real smoke.

## Full-diff self-review

Reviewed the complete tracked test diff and both new files before staging. Checked exact provider/model/upstream and no-fallback identity; real dynamic-tool and native-shell paths; host-only real key use and dummy relay configuration; durable-job adoption before any resumed turn; no repeated PID/job/bootstrap; provider-phase evidence preservation; boolean-only hygiene output; prompt/response-free sanitized event/report schemas; process-group/relay/job cleanup; and persistent home/receipts. The commit contains exactly the three intended files. The audit ledger remains modified and uncommitted. No subagent or independent reviewer was used, and no push occurred.

## Concerns

1. **Mandatory real SCO smoke unmet:** `pt-f8joesmo` remained `STARTING` with a Pending worker throughout the authorized ten-minute wait. It is intact with no duplicate. Dynamic-tool execution, restart/resume, durable-job reconciliation, returned model, key hygiene, terminal result, cleanup, and run-output hashes remain unverified on SCO.
2. The full suite retains the pre-existing unrelated `tests/test_runtime_adapter_regressions.py::test_real_isolated_harness_validation_ignores_host_directory_name` failure (`stage_error` instead of `completed`). The same failure was observed at base in Task 4 audit 863 and in Task 5 audits 905/933; it was not modified or hidden. Task 6 focused tests are green.
3. Task 7 was not started. No Qwen/model loading, A0+B1–B5, SFT, final evaluation, 8B run, 3000/744/five-round/694 change, website update, or push occurred.


---

## Task 6 r3 fresh-run Meta storage isolation fix (2026-11-14)

Status: **DONE_WITH_CONCERNS** (the scoped smoke-storage fix, offline regression verification, self-review, and local commit are complete; the full suite retains only the explicitly allowed pre-existing Landlock failure).

- Base: `9cb618846ce5b152204365bb3bd82badd6c84a7d`.
- Commit: `3a51a7675731d6ea59c3a5193b9f64b865ffa737` (`fix: isolate smoke meta storage per run`).
- Worktree/branch: `/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/bwrap-isolation`, `reproduction/4b-bwrap`.
- No SCO job, real provider smoke, training, evaluation, credential read, subagent, or push was used.

### Root cause and minimal fix

The r2/r3 failure was caused by two fresh directories with the same basename, `run`, both calling `prepare_meta_storage(run)` with its default centralized root. Their `meta_session` links therefore resolved to the same `Meta_logs/run/meta_session`, sharing `codex_home/config.toml` and app-server state. Provider networking was not investigated or changed.

`reproduction/scripts/persistent_meta_bwrap_smoke.py:407` now calls:

```python
prepare_meta_storage(run, meta_logs_root=run / "Meta_logs")
```

Thus each fresh smoke run has its own Meta storage root beneath that run, while repeated initialization with the same run and arguments resolves to the same storage for app-server restart/resume. Production `prepare_meta_storage()` and its centralized storage/recovery semantics were not changed. No bwrap, seccomp, Codex provider, or host configuration was changed, and no old log or `Meta_logs` content was deleted.

### Regression test

Added `test_smoke_meta_storage_isolated_between_fresh_runs_and_stable_on_restart` to `RSIFlow/tests/test_persistent_meta_sandbox.py`.

The test executes the real `run_smoke()` initialization through real `prepare_meta_storage()` and `prepare_codex_home()` for `r2/run` and `r3/run`. A controlled fake app-server stops execution only at `first_app_server`; no provider call occurs. The production default centralized root is redirected into the pytest temporary directory, so the RED run cannot touch the worktree's existing `Meta_logs/`. A recording wrapper delegates to the real initializer and replays the exact same run/arguments to verify restart stability. Assertions cover both resolved `meta_session` and resolved `meta_session/codex_home` paths.

The existing sanitized-diagnostics test double was updated from `lambda run` to `lambda run, **kwargs` so it accepts the real initializer's new call argument. Its behavior and assertions were otherwise unchanged.

### Commands and observed output

All Bash commands used this audit wrapper with actor `task6-r3-fixer`, audit root `/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit`, and cwd `/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/bwrap-isolation`:

```bash
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  /data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/scripts/audit_exec.py \
  --audit-root /data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit \
  --actor task6-r3-fixer --phase <phase> \
  --cwd /data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/bwrap-isolation -- <command>
```

The test environment was exactly:

```bash
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow:$PWD" \
  /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q <tests>
```

1. **RED**, phase `meta-storage-regression-red`:

   ```bash
   env PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow:$PWD" \
     /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q \
     RSIFlow/tests/test_persistent_meta_sandbox.py::test_smoke_meta_storage_isolated_between_fresh_runs_and_stable_on_restart
   ```

   Output: **1 failed in 1.81s**. The failure was the intended root-cause assertion, not an import or syntax error: both `r3/run/meta_session` and the recorded first session resolved to the same temporary `project/Meta_logs/run/meta_session`; `!=` failed because both `PosixPath` values were identical. The same-run repeated-initialization assertions before this final isolation assertion passed.

2. **GREEN**, phase `meta-storage-regression-green`: the same new-test command after the one-line smoke fix. Output: **1 passed in 1.07s**.

3. **Focused initial compatibility check**, phase `task6-focused-after-storage-fix`:

   ```bash
   env PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow:$PWD" \
     /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q \
     RSIFlow/tests/test_persistent_meta_sandbox.py
   ```

   Output: **1 failed, 28 passed in 1.81s**. The new regression passed; the existing diagnostics test received `TypeError` instead of its controlled `RuntimeError` because its `prepare_meta_storage` lambda did not accept `meta_logs_root`. Only that test-double signature was broadened.

4. **Focused final**, phase `task6-focused-final`: the same focused command. Output: **29 passed in 1.47s**.

5. **Full project tests**, phase `full-suite-after-meta-storage-fix`:

   ```bash
   env PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow:$PWD" \
     /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q RSIFlow/tests
   ```

   Output: **1 failed, 276 passed, 2 skipped in 53.70s**. The only failure was the explicitly allowed pre-existing `RSIFlow/tests/test_runtime_adapter_regressions.py::test_real_isolated_harness_validation_ignores_host_directory_name`, with `receipt["status"] == "stage_error"` instead of `"completed"`. It was not deleted, skipped, modified, or otherwise hidden.

6. **Ruff**, phase `ruff-meta-storage-fix`:

   ```bash
   /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m ruff check \
     reproduction/scripts/persistent_meta_bwrap_smoke.py \
     RSIFlow/tests/test_persistent_meta_sandbox.py
   ```

   Output: **All checks passed!**

7. **Self-review**, phase `self-review-meta-storage-diff`: `git diff --check` on the two intended files exited 0. The complete diff showed only the one-line smoke fix, the new regression, and the compatible test-double signature. `git status --short` continued to show the pre-existing/runtime-modified `reproduction/audit/operations.jsonl` and untracked `Meta_logs/`; neither was staged, reset, or deleted.

8. **Stage and commit**, phases `stage-meta-storage-fix`, `commit-meta-storage-fix`, and `verify-meta-storage-commit`: exactly the two intended files were staged; cached diff check exited 0. Commit output was `2 files changed, 47 insertions(+), 2 deletions(-)`. `git rev-parse HEAD` returned `3a51a7675731d6ea59c3a5193b9f64b865ffa737`; `git show --stat` confirmed only the two intended files. Final status still showed only modified `reproduction/audit/operations.jsonl` and untracked `Meta_logs/` outside the commit.

### Files changed by the commit

- `reproduction/scripts/persistent_meta_bwrap_smoke.py`
- `RSIFlow/tests/test_persistent_meta_sandbox.py`

This report was appended at `.superpowers/sdd/2026-11-12-bwrap-isolation/task-6-report.md` and was not included in the code commit, per the instruction to commit only the smoke script and corresponding test.

### Concerns

1. The full suite is not wholly green because of the single known unrelated Landlock failure named above; the allowed result is reported exactly rather than suppressed.
2. Real provider/SCO behavior was intentionally not rerun in this fix task. The verified behavior here is the real offline storage/symlink initialization and same-run restart stability, not a new end-to-end provider smoke claim.


---

## Controller evidence addendum for independent review (2026-11-15)

The earlier report sections are historical and do not describe the final evidence state. The final source head is `3a51a7675731d6ea59c3a5193b9f64b865ffa737`.

### Accepted real-SCO result and changed resource gate

The user explicitly authorized A100 on 2026-11-15. The controller therefore accepts the existing real-SCO success `pt-zj1l9q1t` as the formal smoke gate rather than submitting a duplicate job. This job was submitted by external actor `huangsiyuan1`, not `main-kimi`; it used N3lS+N2lS candidate worker specs, actually ran on 8xA100, and had `root_mapping=true`. These differences from the original fixed A800/`N3lS.Ii.I60.8` shape are intentional disclosed limitations, not hidden equivalences. The A800 jobs r5/r6 failed before startup and remain unresolved infrastructure failures.

Accepted report: `reproduction/logs/meta-bwrap-task6-r4-20261114T173158Z/run/report.json`, SHA256 `b1f1dbf35c09ee192c2199028ee984ba7068889bdf3f126cd52965732220f920`. Controller verification found `ok=true`, `stage=completed`, the same thread across restart, no duplicate jobs or repeated bootstrap, exactly `gpt-6-astra` before and after restart, three dynamic-tool calls, all 11 key-hygiene and 7 cleanup checks true, durable job exited 0, `shell_tool=false`, and Meta storage isolated beneath the fresh run. The worker and SCO job reached successful terminal states.

### Vendor native-shell limitation

The committed smoke disables Codex's additional vendor shell tool (`shell_tool=false`). The plan brief's Step 4 originally described a boolean-only native-shell hygiene turn; the implemented replacement uses construction-level sandbox/config/resume marker assertions plus host-job key-absence evidence. The reason is environmental: this kernel cannot execute Codex legacy Landlock, while nested vendor bwrap is denied by the reviewed outer anti-escape seccomp. Relaxing that seccomp or using `danger-full-access` was rejected. The required Responses API, native dynamic tools, thread resume, durable-job reconciliation, provider identity, and cleanup were exercised for real. Production `persistent_meta.py` was not changed to disable shell globally.

### Verification already present

Agent-14's scoped storage fix had 29 focused passes; the full suite had 276 passes, 2 skips, and only the known pre-existing `test_real_isolated_harness_validation_ignores_host_directory_name` failure; Ruff was clean. A controller-run local real smoke using the same `.../run` basename shape also passed at `reproduction/logs/meta-bwrap-task6-local-r3-fix-20261114T162847Z/run/report.json`, SHA256 `1162f11ae4f73df93ea9dd05c773c937fa07f382efa704dcfd34e469bdf77483`.

No five-round evolution, SFT, model benchmark, final 694/744 evaluation, 8B run, or push occurred.
