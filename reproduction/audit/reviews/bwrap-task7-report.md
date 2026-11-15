# Task 7 report — Full Regression and Audited Publication

## Status

**DONE_WITH_CONCERNS** — implementation, real regression, publication, audit, self-review, and local commit are complete. The controller's fresh independent security review remains a mandatory open gate; no claim is made that it has run or closed.

- Branch: `reproduction/4b-bwrap`
- Base: `3a51a7675731d6ea59c3a5193b9f64b865ffa737`
- Commit: `96714edf37339001b923dd38129d0c0f66df1d49`
- Subject: `docs: publish validated bubblewrap isolation evidence`
- Push: not performed
- New SCO jobs: none
- Experiments started: none of A0+B1–B5, SFT, final 694/744, ACE 50, 8B, Qwen services, or formal evolution training

All shell verification used the required `audit_exec.py` wrapper, audit root `/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit`, actor `task7-implementer`, worktree cwd, and a unique phase. Commands below show the wrapped command payload for readability.

## 1. Baseline and real RED/GREEN

### Baseline RED

At the requested base, `git status --short --branch` showed only the protected pre-existing `reproduction/audit/operations.jsonl` modification and untracked `Meta_logs/`. `git rev-parse HEAD` returned exactly the requested base.

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests
```

Result: exit 1, **276 passed, 2 skipped, 1 failed in 73.31s**. The sole failure was the required existing test:

```text
tests/test_runtime_adapter_regressions.py::test_real_isolated_harness_validation_ignores_host_directory_name
receipt status=stage_error
```

### Explicit-bwrap RED and root cause

The test name and every assertion were preserved. The only test change was adding the `monkeypatch` fixture and:

```python
monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "linux_bwrap_seccomp_v1")
```

Focused result before production repair: exit 1. Verbose diagnostics showed:

```text
RSIFlow validation isolation failed: OSError: [Errno 38] landlock_create_ruleset
```

Root cause: `rsiflow_validation._worker` did not use `LinuxSandbox`; it directly called `_restrict_filesystem` and `_restrict_syscalls`, so selecting bwrap in the parent did not select it in this worker.

### Minimal implementation and second RED

`rsiflow_validation.py` now constructs a `BwrapRequest` only when the backend is explicitly `linux_bwrap_seccomp_v1`. It reuses the pinned `BubblewrapBackend` and candidate profile, mounts only the trusted runtime/upstream trees, interpreter/system runtime dependencies, and one dedicated writable validation scratch. Owned mount descriptors pass through `Popen` and close immediately after spawn. The existing process-group timeout/kill, report bounds, candidate protocol, and legacy no-selection path remain intact. The worker skips only its duplicate Landlock installation when launched by the bwrap inner launcher; no seccomp rule is weakened.

The first implementation still failed correctly, now as an ordinary validation result:

```text
ValueError: credential environment variable denied: TOKENIZERS_PARALLELISM
```

The backend rejects environment names containing `TOKEN`. Removing only this nonessential tokenizer setting from the bwrap-specific environment (not weakening backend validation) produced the real GREEN:

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q \
  tests/test_runtime_adapter_regressions.py::test_real_isolated_harness_validation_ignores_host_directory_name
```

Result: **1 passed in 32.98s**. This exercised the real materialization, isolated validation, candidate state, manifest, and report assertions; no mock or source-text assertion replaced them.

### Focused and full regression

```bash
cd RSIFlow
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 PYTHONPATH="$PWD/runtime:$PWD" \
  /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q \
  tests/test_runtime_adapter_regressions.py tests/test_bwrap_candidate.py tests/test_bwrap_backend.py
```

Result: **79 passed in 43.97s**.

A diagnostic run that globally injected bwrap into every test produced **276 passed, 2 skipped, 1 failed**: `CustomProviderTests.test_custom_codex_home_and_catalog` intentionally expects the no-selection host catalog path, while global injection selects `/catalog.json`. This was not hidden or relabeled as the final result. Real-isolation tests select bwrap individually, and the brief's exact suite command preserves the legacy no-selection compatibility test.

Final exact upstream command on the staged deliverable:

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests
```

Result: exit 0, **277 passed, 2 skipped in 84.25s**. An earlier complete GREEN after the fix was **277 passed, 2 skipped in 88.68s**.

## 2. Website, static, secret, and asset checks

Final deliverable verification also ran:

```bash
python3 -m unittest discover -s web/tests -v
node --check web/app.js
git diff --cached --check
```

Results:

- Website: **11/11 passed** (`Ran 11 tests ... OK`), including the repository's existing assigned-credential secret scan and raw-log/client-metadata checks.
- `node --check`: exit 0.
- Staged diff check: exit 0.
- `git diff --check` before staging: exit 0.

Asset checks used `git check-ignore -v`, `git ls-files`, and a public-symlink check. Confirmed:

- `assets/isolation/` and `assets/private/` are ignored.
- `reproduction/logs/` and `Rollout_logs/` are ignored, covering raw provider requests, private trajectories, and large outputs.
- No files under `assets/isolation`, `assets/private`, `reproduction/logs`, `Rollout_logs`, or `Meta_logs` are tracked.
- `web/` contains no symlinks.
- The regenerated process/public files contain no raw prompt/response payload matches or private-key material; the existing website secret scan passes.

The first ignore check named a path beyond a runtime symlink and Git returned exit 128 (`pathspec ... is beyond a symbolic link`). No file content was read. The corrected check used ignored log paths without traversing that symlink and passed.

Ruff was also run on the two old Python files. It reported five pre-existing findings: import ordering in both old files, legacy `try/except/pass`, an old mutable class fixture, and ordering of the pre-existing local imports. These files were not previously Ruff-clean; no unrelated lint rewrite was included. Ruff is not claimed as a passing gate.

Status generation after the audited verification entries:

```bash
python3 reproduction/scripts/build_status.py \
  --audit-root /data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit \
  --output web/data/process.json
```

Final committed snapshot: **1,056 operations, 96 results**. The post-commit audit record is intentionally not retroactively inserted into that committed pre-commit snapshot.

## 3. Recomputed real smoke evidence

The following SHA256 values were recomputed locally with `sha256sum`; terminal/resource states and scopes match the prior audited evidence.

### Candidate

- SCO: `pt-gyfupguq`, SUCCEEDED; worker Succeeded.
- Report: `reproduction/logs/candidate_bwrap_smoke_sco_r3.json`
- SHA256: `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`
- Evidence: 7 functional, 18 negative, 5 timeout, and 3 session checks true; `infrastructure_error=null`.
- Scope: infrastructure smoke, not a model score.

### EvalPlus

- SCO: `pt-bfkp5k0d`, SUCCEEDED; worker Succeeded.
- Report: `reproduction/logs/evalplus-bwrap-smoke/20261113T035651Z/smoke_report.json`
- Report SHA256: `537ed60c8ed385ecab81197fb218a6e8117bfc3a0871d5e2445779daf524bfc0`
- Canonical official SHA256: `3125b42c1d1f8ef7ba4a6a4ee263ab653485c11a0b8e087327a9987ea8a2ffa2`
- Wrong official SHA256: `f41bea3178f193a2228b196fe3709fe8f1aa6321302ff04c0ef29a3bf2427514`
- Evidence: one HumanEval+ task; canonical Base/Plus pass, wrong Base/Plus fail, both explicit bwrap with null infrastructure errors.
- Scope: official one-task comparator smoke, not a HumanEval+ benchmark score.

### BFCL

- SCO: `pt-fdvsxqax`, SUCCEEDED; worker Succeeded.
- Receipt: `reproduction/logs/bfcl-bwrap-check-r2/check.json`
- SHA256: `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4`
- Evidence: pinned source `ea13468e4423454d0c213704fb87cf7cb3990433`; exactly two controlled RPC calls; existing key hygiene evidence retained.
- Scope: native infrastructure smoke, not a BFCL benchmark score.

### Persistent Meta

- Formal accepted SCO evidence: `pt-zj1l9q1t`, SUCCEEDED at `2026-11-14T17:33:42Z`; worker Succeeded.
- Report: `reproduction/logs/meta-bwrap-task6-r4-20261114T173158Z/run/report.json`
- SHA256: `b1f1dbf35c09ee192c2199028ee984ba7068889bdf3f126cd52965732220f920`
- Evidence: same thread, no duplicate jobs, no repeated bootstrap, three dynamic-tool calls, all 11 key-hygiene and 7 cleanup checks true, durable job exited 0, exact `OpenAI/gpt-6-astra` before/after restart.
- Submission actor: **external `huangsiyuan1`**, not `main-kimi`.
- Actual resource: **8xA100**, N3lS+N2lS candidate specs, `root_mapping=true`; accepted after explicit user authorization of A100.
- A800 r5/r6 `pt-7i4qertj` and `pt-44m6zkmx` failed before startup command execution. No A800 pass is claimed.
- Scope: infrastructure/resume smoke, not a model-quality result.

## 4. Documentation and audit delivery

Updated documentation covers explicit backend selection, pinned asset installation, candidate/native/Meta profile families, keyless host relay, infrastructure-vs-model failure classification, smoke commands, exact test counts, job IDs, report hashes, terminal/worker evidence, A100/external-actor/root-mapping distinctions, and native-shell limitations.

Public Markdown and JSON distinguish infrastructure smoke from scores and preserve five rounds, SFT, final 694/744, ACE 50, 8B, and Qwen services as unrun. They also retain the remaining five-benchmark-smoke, healthy-four-GPU, current-694-`--check`, and independent-experiment-preflight gates.

Audit key results recorded:

- `00001147-full_upstream_regression`
- `00001149-website_and_publication_checks`
- `00001151-candidate_bwrap_terminal_evidence`
- `00001153-evalplus_bwrap_terminal_evidence`
- `00001155-bfcl_bwrap_terminal_evidence`
- `00001157-persistent_meta_bwrap_terminal_evidence`

The daily audit `reproduction/audit/daily/2026-11-12.md` has a dated 2026-11-15 Task 7 section. Commit recording used `record_operation.py`; resulting audit sequence: **1174**, bound to full commit `96714edf37339001b923dd38129d0c0f66df1d49`.

## 5. Files changed and self-review

Committed exactly 13 intended files:

1. `AGENTS.md`
2. `RSIFlow/runtime/rsiflow/task_meta/rsiflow_validation.py`
3. `RSIFlow/tests/test_runtime_adapter_regressions.py`
4. `reproduction/PROGRESS.md`
5. `reproduction/REPRODUCTION.md` (new; the brief said Modify but no file existed at base)
6. `reproduction/audit/daily/2026-11-12.md`
7. `web/OPERATIONS.md`
8. `web/REPRODUCTION.md`
9. `web/RESULTS.md`
10. `web/data/process.json`
11. `web/data/reproduction.json`
12. `web/index.html` (required to remove stale public queued-Landlock/missing-credential claims)
13. `web/tests/test_web.py` (updated public-status contract to the new evidence)

Self-review checked the full source/test diff, documentation diff, generated JSON, staged file list, and final status:

- The old test is not deleted, renamed, skipped, or assertion-weakened.
- Explicit bwrap has no host/Landlock fallback and no `danger-full-access` path.
- Candidate/native protocols and scoring assertions are unchanged.
- Validation scratch is the only writable mount; runtime/upstream are read-only; provider credentials are not propagated.
- Owned mount FDs are closed after spawn; existing process-group timeout cleanup remains.
- Public pages contain sanitized evidence only and retain all unrun boundaries.
- `reproduction/audit/operations.jsonl` and `Meta_logs/` were never staged, committed, reset, or deleted.
- Historical PROGRESS audit rulings removed by an initial concise rewrite were restored before commit.
- Final `git status --short` contains only:
  - ` M reproduction/audit/operations.jsonl` (protected pre-existing runtime/audit churn)
  - `?? Meta_logs/` (protected pre-existing untracked runtime output)

Commit result:

```text
[reproduction/4b-bwrap 96714ed] docs: publish validated bubblewrap isolation evidence
 13 files changed, 1798 insertions(+), 524 deletions(-)
 create mode 100644 reproduction/REPRODUCTION.md
```

## 6. Concerns and remaining gates

### Mandatory independent review

The fresh independent security review was **not dispatched by this implementer**, as instructed. It remains the controller's post-commit gate and must review mount breadth/symlinks, namespace/seccomp staging, descriptor inheritance, key hygiene, `/proc` failure classification, timeout cleanup, persistent adoption, and any mock/skipped evidence. Important/Critical findings remain blocking.

### Native shell limitation

Codex native-shell descendant hygiene was not executed. Legacy Landlock is unavailable and nested vendor bwrap conflicts with the reviewed anti-escape seccomp. Only the approved `shell_tool=false` construction-level gate is claimed. Seccomp was not weakened.

### Deferred minors for controller triage

1. Task 2 smoke accepts any nonempty all-true negative mapping rather than requiring an exact named key set; the accepted real report contains the expected 18 keys.
2. Task 2 returncode -9/scratch cleanup does not directly prove every inner candidate descendant exited.
3. Task 3 outer smoke input/config/JSON errors can traceback instead of producing structured `infrastructure_error`.
4. Task 4 exceptional RPC `thread.start` failure may leak request/response descriptors.
5. Task 5 persistent-mount tests mostly inspect fake backend requests; candidate/Harness real tests do not replace a dedicated persistent-mount probe.
6. Task 5 exact provider/model regression assertions remain narrower than the controller's direct base-config verification.
7. Task 6 shared `codex_host.toml` shell disable is justified by the approved construction-level amendment but could be moved to a smoke-private template.
8. Task 6 dynamic-tool app-server completion events are not each fail-closed matched by call ID/tool/arguments hash.

### Experiment boundary

Isolation infrastructure is not presented as experiment completion. Five rounds still require all five benchmark minimum smokes, healthy four-GPU Qwen services on logical GPU 0–3, current 694 `python RSIFlow_4B/launch.py --check`, and final independent experiment preflight review. No five-round launch is authorized by this report.


---

# Task 7 fix round 1/5 — fail-closed validation backend selection

## Finding and scope

Independent reviewer Important finding:

> `RSIFlow/runtime/rsiflow/task_meta/rsiflow_validation.py:90-96` — every value other than exact `linux_bwrap_seccomp_v1`, including an unknown or misspelled nonempty backend ID, silently selects the legacy Landlock command. This bypasses `LinuxSandbox`’s unknown-backend rejection and violates fail-closed backend selection; on a Landlock-capable host, an intended bwrap run could execute under a different backend. Permit legacy only when the variable is unset or explicitly `linux_landlock_seccomp`, use bwrap only for its exact ID, and raise `SandboxUnavailable` for every other value before spawning. Add a regression proving an invalid ID invokes neither legacy isolation nor `Popen`.

Only the selector and one regression test were changed. Deferred Minor website wording was not touched.

## RED

Added `test_validation_rejects_unknown_backend_before_isolation_or_spawn`. It invokes the real `validate_candidate()` selector with `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_typo`, expects `SandboxUnavailable`, and uses forbidden spies for `_worker` (legacy isolation), `_bwrap_command`, and `subprocess.Popen`; the final assertion requires no forbidden call.

The initial fixture failed before selection because the fake materializer did not create package directories (`FileNotFoundError`). Correcting only that fixture produced the intended RED:

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q \
  tests/test_runtime_adapter_regressions.py::test_validation_rejects_unknown_backend_before_isolation_or_spawn
```

Output: exit 1, **1 failed in 0.61s**:

```text
AssertionError: Popen must not be called for an invalid backend
runtime/rsiflow/task_meta/rsiflow_validation.py:99: in validate_candidate
    process = subprocess.Popen(
```

This proves the invalid ID previously reached the legacy spawn path.

## Implementation and GREEN

The selector now defaults to `linux_landlock_seccomp` only when the variable is unset, accepts exact `linux_landlock_seccomp` as legacy, uses bwrap only for exact `linux_bwrap_seccomp_v1`, and otherwise raises `SandboxUnavailable("unknown sandbox backend: ...")` before opening process logs or calling `Popen`.

The same regression command then returned:

```text
1 passed in 0.30s
```

The spy list remained empty, proving neither legacy isolation, bwrap command construction, nor `Popen` was invoked.

## Focused and full verification

Focused command:

```bash
cd RSIFlow
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 PYTHONPATH="$PWD/runtime:$PWD" \
  /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q \
  tests/test_runtime_adapter_regressions.py tests/test_bwrap_candidate.py tests/test_bwrap_backend.py
```

Output: **80 passed in 41.19s**.

Full project command:

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests
```

Output: **278 passed, 2 skipped in 79.17s**. This is exactly the prior 277 passes plus the new regression; both prior skips remain unchanged. `git diff --check` and staged `git diff --cached --check` both exited 0.

## Files changed and self-review

- `RSIFlow/runtime/rsiflow/task_meta/rsiflow_validation.py`: minimal exact three-way selector and unknown-ID exception.
- `RSIFlow/tests/test_runtime_adapter_regressions.py`: one real-selector negative regression with forbidden-side-effect spies.

Self-review confirmed that unset and explicit legacy behavior remains, exact bwrap behavior remains, empty/misspelled/unknown values fail closed, no existing test names or assertions were changed, no website/docs/runtime products were added to the commit, and protected `reproduction/audit/operations.jsonl` and `Meta_logs/` remain unstaged and untouched by the commit.

## Commit and remaining review

- Commit: `32ede4739479f2271b72eb7176e3b4084df3f31a`
- Subject: `fix: reject unknown validation sandbox backends`
- Commit contents: exactly two files, 36 insertions and 2 deletions.
- Audit commit operation: `1188`.
- Final status: only protected pre-existing `reproduction/audit/operations.jsonl` churn and untracked `Meta_logs/`.

The Important implementation finding is addressed by RED/GREEN and full-suite evidence. Independent reviewer closure remains the controller's gate; no review approval is claimed by the implementer.


---

# Consolidated final-review fix wave — production startup, ownership, and trusted assets

## Status and commit

**DONE_WITH_CONCERNS** — all three Important production findings and both Minor publication findings are implemented with RED/GREEN evidence and committed locally. Offline gates pass. New affected-path SCO execution and independent re-review remain pending with the controller.

- Base: `32ede4739479f2271b72eb7176e3b4084df3f31a`
- Commit: `313b6bcc00ee641ce99eee04fb9e4490dbb63929`
- Subject: `fix: harden production startup and sandbox trust`
- Commit contents: exactly 23 intended files, 914 insertions and 422 deletions
- Audit key result: `00001210-final_fix_offline_gate`
- Audit commit operation: `1219`
- Final worktree status: only protected pre-existing `reproduction/audit/operations.jsonl` churn and untracked `Meta_logs/`
- No subagent, SCO job, real provider smoke, training/evaluation, or push was used.

All shell verification used the required audit wrapper with actor `task7-final-fixer`, the original audit root, worktree cwd, and unique phases. Commands below show the audited payload.

## Important 1 — production fresh Codex startup

### Finding and RED

The smoke previously created/chmodded `installation_id`, but production `prepare_codex_home()` did not. New tests cover fresh production home under umask 077, valid same-path restart with inode/content preservation, symlink, directory, and invalid UUID state. They catch missing production initialization and unsafe overwrite/follow behavior.

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  -m pytest -q tests/test_launch_meta.py -k installation_id
```

RED output: **5 failed, 12 deselected in 1.38s**:

- fresh and restart: `FileNotFoundError ... installation_id`
- symlink/directory/invalid: `DID NOT RAISE`

### Implementation and GREEN

`launch_meta.prepare_codex_home()` now calls the sole production `prepare_codex_installation_id()` helper. It:

- creates a fresh UUID in a same-home temporary regular file, fsyncs and sets mode 0644 before atomically hard-linking it to `installation_id` without overwriting an existing destination;
- opens existing state with `O_NOFOLLOW|O_NONBLOCK`, requires a regular file and canonical UUID, and applies `fchmod(0644)` to that open descriptor;
- preserves valid content and inode on same-path restart and rejects symlink, non-regular, or invalid state without rewriting it;
- leaves launcher umask and all seccomp rules unchanged.

The smoke-only helper was deleted. The smoke now relies exclusively on production `prepare_codex_home()`; a controlled fresh smoke startup stops at fake app-server start and verifies the production-created file rather than calling a smoke helper.

GREEN commands and output:

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  -m pytest -q tests/test_launch_meta.py -k installation_id
# 5 passed, 12 deselected in 0.19s

PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  -m pytest -q tests/test_persistent_meta_sandbox.py -k installation_id
# 1 passed, 28 deselected in 1.19s
```

The second test exercises fresh `prepare_meta_storage()` + production `prepare_codex_home()` in the actual smoke control flow. No real provider call was made.

## Important 2 — same-basename production storage collision

### Finding and RED

The old central storage key was only `run_dir.name`. New tests cover two different fresh `r1/run` and `r2/run` paths, same-path restart with codex/thread state, pre-existing unowned legacy same-path links, conflicting ownership, central-directory symlink, and owner-file symlink.

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q \
  tests/test_launch_meta.py -k "same_basename or preexisting_legacy or conflicting_storage or storage_directory_symlink or storage_owner_symlink"
```

RED output: **5 failed, 17 deselected in 0.83s**. The second fresh run did not raise, legacy adoption had no owner marker, conflicting ownership did not raise, and central/owner symlink cases were not rejected.

### Implementation and GREEN

`prepare_meta_storage()` now maintains central `.run-owner.json` with exactly `{"schema_version": 1, "run_dir": "<resolved-full-path>"}`:

- a different fresh run using an owned same-basename central directory raises `FileExistsError` before run links are created;
- matching owner returns the same central storage and preserves state;
- an unowned pre-existing central directory is adopted only when existing run/central links prove same-path legacy ownership; a fresh run cannot claim an unowned directory;
- owner files are written via fsynced temporary regular file plus no-overwrite hard link and read with no-follow regular-file checks;
- central-directory and owner symlinks fail closed; no artifacts are deleted, migrated, or rewritten.

Combined storage/installation verification:

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q \
  tests/test_launch_meta.py tests/test_persistent_meta_sandbox.py \
  -k "storage or installation_id or new_run_writes_meta_records or live_legacy_records or central_paths"
```

Output: **14 passed, 37 deselected in 1.28s**. Existing live-legacy open-file and same-path central progress tests also passed.

## Important 3 — trusted assets for every profile

### Finding and RED

Candidate, native, and Harness used the default backend from writable source without mandatory manifest/launcher identities. New backend tests use whitespace-only tampering so the test catches identity drift rather than JSON syntax failure. Consumer tests install forbidden `Popen` spies and require `SandboxUnavailable` with no calls.

Backend RED:

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  -m pytest -q tests/test_bwrap_backend.py -k default_backend
```

Output after correcting the manifest fixture to whitespace-only tampering: **3 failed, 44 deselected**. Launcher and manifest tampering were accepted and a missing receipt was accepted.

Combined consumer RED:

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q \
  tests/test_bwrap_backend.py tests/test_bwrap_candidate.py tests/test_bwrap_native.py \
  tests/test_runtime_adapter_regressions.py -k "default_backend or tampered_security_asset"
```

Output: **9 failed, 89 deselected in 2.17s**. Candidate, native, and Harness manifest/launcher cases all reached forbidden process creation instead of rejecting the tamper.

### Implementation and GREEN

The existing installer now adds these fields to ignored `assets/isolation/install-receipt.json` using no-follow regular-file digest reads:

- `manifest_path` and `manifest_sha256`
- `launcher_path` and `launcher_sha256`

Every default `BubblewrapBackend` now requires that receipt and validates both fixed relative paths and both hashes on every launch. Missing, malformed, non-regular, symlink, or mismatched trust fails before consumer spawn. The manifest's binary path/hash, profile matrix, launcher identity, mount identity, and seccomp checks remain in force. Persistent Meta's stronger static trusted copies and mandatory launcher hash remain unchanged; diagnostics are not production trust inputs.

The actual ignored receipt was regenerated through the audited production installer, not hand-written. Candidate/native/Harness now share the default pinned backend path; module-level project roots only make the consumer tests able to use isolated temporary projects.

GREEN command:

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q \
  tests/test_bwrap_backend.py tests/test_bwrap_candidate.py tests/test_bwrap_native.py \
  tests/test_runtime_adapter_regressions.py \
  -k "default_backend or tampered_security_asset or installer_copies_regular_file_atomically"
```

Output: **10 passed, 88 deselected in 1.71s**. This includes installer receipt identity, missing-receipt rejection, and all six consumer tamper/no-spawn cases.

## Combined focused and full regression

Focused command:

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q \
  tests/test_launch_meta.py tests/test_custom_provider.py tests/test_persistent_meta_sandbox.py \
  tests/test_bwrap_backend.py tests/test_bwrap_candidate.py tests/test_bwrap_native.py \
  tests/test_runtime_adapter_regressions.py tests/test_evalplus_isolated.py
```

Output: **156 passed in 50.41s**.

A prior diagnostic run globally injected bwrap into every test and returned **1 failed, 155 passed in 52.15s**. Only the intentional no-selection legacy catalog test failed, exactly as in earlier tasks. The accepted focused run above does not globally inject; real-isolation tests explicitly select bwrap themselves.

Full suite command:

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests
```

Output: **297 passed, 2 skipped in 90.22s**. This adds 19 tests to the pre-wave 278-pass baseline while preserving both skips and all existing behavior.

## Ruff, website, Node, diff, and secret/asset gates

Ruff was run on all changed Python files:

```bash
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m ruff check \
  RSIFlow/launch_meta.py RSIFlow/tool_sandbox.py \
  RSIFlow/runtime/rsiflow/task_meta/bwrap_backend.py \
  RSIFlow/runtime/rsiflow/task_meta/rsiflow_validation.py \
  reproduction/scripts/install_bwrap.py reproduction/scripts/persistent_meta_bwrap_smoke.py \
  RSIFlow/tests/test_launch_meta.py RSIFlow/tests/test_persistent_meta_sandbox.py \
  RSIFlow/tests/test_bwrap_backend.py RSIFlow/tests/test_bwrap_candidate.py \
  RSIFlow/tests/test_bwrap_native.py RSIFlow/tests/test_runtime_adapter_regressions.py
```

Initial result: 20 findings. Two new import-order findings in added tests were fixed. Final result: **18 findings**, all in pre-existing legacy code/style: old import blocks, old blind `except`, legacy `exec`/`preexec_fn`, old `try/except/pass`, old mutable test fixture, and an old local-import order. No unrelated lint rewrite was made; Ruff is not claimed clean.

Final website/static commands:

```bash
python3 -m unittest discover -s web/tests -v
node --check web/app.js
git diff --check
```

Results: **11/11 website tests passed** (`Ran 11 tests in 0.140s`, `OK`), Node exit 0, diff check exit 0. The same website suite also passed before the final process snapshot. The public contract now asserts the 297/156 counts, pending SCO reruns, installer-receipt trust, and distinct host OpenAI versus separately approved ACE credentials. `web/index.html` no longer mentions DeepSeek credentials; ACE remains skipped.

Secret/asset checks confirmed:

- ignored binary and installer receipt under `assets/isolation/`;
- ignored credentials, provider requests, trajectories, and Rollout logs;
- no tracked sensitive/runtime files and no `web/` symlinks;
- trusted receipt exists and is not a symlink;
- existing public assigned-credential and raw-log scans pass.

The final process snapshot was regenerated after audit entries: **1,098 operations, 97 results**.

## Minor publication updates

Current (not historical) counts were updated together in `AGENTS.md`, `reproduction/PROGRESS.md`, `reproduction/REPRODUCTION.md`, `web/RESULTS.md`, `web/OPERATIONS.md`, `web/data/reproduction.json`, and `web/tests/test_web.py`. The dated historical RED/GREEN entries retain their original counts as audit history; current status now reports 297 full passes and 156 focused passes.

Architecture notes now describe production installation-ID handling, resolved-run ownership with legacy resume, and mandatory installer-pinned manifest/launcher hashes. All affected-path SCO reruns are explicitly marked pending; no new SCO IDs or fresh success claims were invented.

## Files committed

1. `AGENTS.md`
2. `RSIFlow/launch_meta.py`
3. `RSIFlow/runtime/rsiflow/task_meta/bwrap_backend.py`
4. `RSIFlow/runtime/rsiflow/task_meta/rsiflow_validation.py`
5. `RSIFlow/tests/test_bwrap_backend.py`
6. `RSIFlow/tests/test_bwrap_candidate.py`
7. `RSIFlow/tests/test_bwrap_native.py`
8. `RSIFlow/tests/test_launch_meta.py`
9. `RSIFlow/tests/test_persistent_meta_sandbox.py`
10. `RSIFlow/tests/test_runtime_adapter_regressions.py`
11. `RSIFlow/tool_sandbox.py`
12. `reproduction/PROGRESS.md`
13. `reproduction/REPRODUCTION.md`
14. `reproduction/audit/daily/2026-11-12.md`
15. `reproduction/scripts/install_bwrap.py`
16. `reproduction/scripts/persistent_meta_bwrap_smoke.py`
17. `web/OPERATIONS.md`
18. `web/REPRODUCTION.md`
19. `web/RESULTS.md`
20. `web/data/process.json`
21. `web/data/reproduction.json`
22. `web/index.html`
23. `web/tests/test_web.py`

## Self-review and remaining gate

Self-review verified that production—not just smoke—initializes fresh IDs atomically; valid restart state and legacy same-path storage remain intact; conflicting fresh runs cannot share thread/config/job state; all default candidate/native/Harness launches require trusted external receipt identities; persistent Meta static pinning is unchanged; no umask/seccomp/scoring/protocol fallback changed; public counts and credential wording are coherent; and protected runtime products were not staged.

Remaining mandatory gate: the controller must run fresh affected-path SCO validation for candidate, native/BFCL, Harness, and persistent Meta on commit `313b6bc`, then obtain independent re-review. Existing SCO IDs and hashes in public docs belong only to earlier accepted evidence and must not be presented as validation of this new commit. Five-round training/evaluation remains stopped behind the separate preflight gates.


---

# Final A100 SCO evidence publication

## Status and commit

**DONE_WITH_CONCERNS** — affected-path real SCO gates are now published with their exact terminal distinctions. This step changed only publication/audit artifacts and the public website contract. Independent final re-review remains pending.

- Production/test commit being evidenced: `313b6bcc00ee641ce99eee04fb9e4490dbb63929`
- Evidence publication commit: `861debd3436c83d0580601a667eb286440b17277`
- Subject: `docs: record final A100 isolation gates`
- Commit contents: exactly 10 publication/audit/generated files, 555 insertions and 387 deletions
- Audit results: `00001241-combined_candidate_harness_bfcl_gates`, `00001243-combined_meta_external_failure`, `00001245-final_meta_a100_gate`
- Audit commit operation: `1253`
- Final status: only protected `reproduction/audit/operations.jsonl` and untracked `Meta_logs/`
- No production code or upstream test changed; no new job, subagent, training/evaluation, provider rerun, or push occurred in this publication step.

## Artifact verification

The following audited hash command was run:

```bash
sha256sum \
  reproduction/logs/final-bwrap-a100-20261115T043242Z/candidate.json \
  reproduction/logs/final-bwrap-a100-20261115T043242Z/harness.stdout.log \
  reproduction/logs/final-bwrap-a100-20261115T043242Z/bfcl/check.json \
  reproduction/logs/final-bwrap-a100-20261115T043242Z/bfcl/key_hygiene.log \
  reproduction/logs/final-bwrap-a100-20261115T043242Z/meta/run/report.json \
  reproduction/logs/final-meta-a100-20261115T043755Z/run/report.json
```

Recomputed results exactly matched the supplied evidence:

- Candidate: `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`
- Harness log: `808285dc5c05709785e4d669e28882e5c496644bb1191a6301866325b2735f31`
- BFCL receipt: `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4`
- BFCL key hygiene: `fbe9b6386dd64f78e5eb72b7bbad9f88f2c39e218b93bd6ab049a0b97b20749d`
- Combined Meta failed report: `4f123a27dc415e2b99d63569fee30af2fce68bf3fda68da3653ee7e9e1db3d9c`
- Meta retry report: `a177657dbf1e744f2d2be64d16c33bcda08102052a7ecd7486aebdd48cf64b66`

## Combined job — overall FAILED, three named stages passed

`pt-v6m10c4t` ran on 8xA100 with `root_mapping=false`. The combined job and worker are terminally **FAILED** only because the Meta stage failed. It is not described as SUCCEEDED.

Directly read exit-code artifacts:

- `candidate.exit_code.txt`: 0
- `harness.exit_code.txt`: 0
- `bfcl.exit_code.txt`: 0
- `meta.exit_code.txt`: 1
- `overall.exit_code.txt`: 1

Stage evidence:

- Candidate `ok=true`, `infrastructure_error=null`, with exactly 7 functional, 18 negative, 5 timeout, and 3 session checks true.
- Harness explicit-bwrap `test_real_isolated_harness_validation_ignores_host_directory_name`: **1 passed in 23.26s**.
- BFCL exactly two controlled RPCs; all five key-hygiene booleans true.

## Combined Meta external failure and retry

The combined Meta failed at `read_json_turn` before a completed provider response. A sanitized field-only query over `transport_records.json` printed:

```text
{'ordinal': 0, 'http_status': 502, 'completed': False,
 'error_type': 'terminal_provider_failure', 'provider_outcome': 'failed',
 'terminal_failure': {'code': 'http_502', 'usage': None,
                      'provider_completion_unknown': True, 'request_rejected': False}}
{'ordinal': 1, 'http_status': 200, 'completed': False,
 'error_type': 'ValueError',
 'validation_error': 'Upstream stream ended without a complete response',
 'upstream_event_counts': {}}
```

This is recorded as an external relay/provider completion failure. No code was changed to hide it and the combined job remains FAILED.

Exactly one Meta-only retry, `pt-x6b4ista`, ran on 8xA100 with `root_mapping=false`: SUCCEEDED, worker Succeeded and terminal, exit code 0, with resources released. Its report at `reproduction/logs/final-meta-a100-20261115T043755Z/run/report.json` has SHA256 `a177657dbf1e744f2d2be64d16c33bcda08102052a7ecd7486aebdd48cf64b66` and verifies:

- `ok=true`, `stage=completed`;
- same thread, `duplicate_jobs=[]`, `bootstrap_repeated=false`;
- exact `gpt-6-astra` before and after restart;
- three dynamic-tool calls;
- all 11 key-hygiene and 7 cleanup checks true;
- durable job return code 0 and process cleanup;
- `shell_tool=false`.

The production-state check printed:

```text
{'installation_id_valid': True, 'mode': '0o644', 'owner_exact': True}
```

The UUID parses successfully, the file mode is 0644, and `.run-owner.json` exactly equals `{"schema_version": 1, "run_dir": "<resolved fresh run path>"}`. An initial one-line diagnostic had a Python syntax error; the corrected read-only command produced the above result without modifying artifacts.

Historical external-actor Task 6 evidence `pt-zj1l9q1t` remains historical only. The current final Meta gate is `pt-x6b4ista` on `313b6bc`. EvalPlus remains the earlier accepted one-task smoke on its recorded historical commit and was not rerun on `313b6bc`. Native-shell descendant validation remains unexecuted.

## Audit and process snapshot

Three separate results preserve the terminal distinction:

1. `00001241-combined_candidate_harness_bfcl_gates`: combined job overall FAILED but candidate/Harness/BFCL exit 0, with all report/log hashes and terminal resource state.
2. `00001243-combined_meta_external_failure`: ordinal 0 HTTP 502 and retry HTTP 200 incomplete SSE, failed report hash, no code change.
3. `00001245-final_meta_a100_gate`: Meta retry SUCCEEDED/worker Succeeded/exit 0, full report invariants, production ID/owner checks, and resource release.

After these entries:

```bash
python3 reproduction/scripts/build_status.py \
  --audit-root /data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit \
  --output web/data/process.json
```

Output: `{"output": "web/data/process.json", "operations": 1128, "results": 100}`.

## Publication checks

Commands:

```bash
python3 -m unittest discover -s web/tests -v
node --check web/app.js
git diff --check
```

Results: **11/11 website tests passed** (`Ran 11 tests in 0.128s`, `OK`), Node exit 0, diff check exit 0. The public test contract now requires combined terminal FAILED with three stage exit-0 results, Meta retry SUCCEEDED/worker Succeeded, `root_mapping=false`, production 0644/owner invariants, and EvalPlus not rerun on `313b6bc`.

The existing assigned-credential/raw-log scan passed. Explicit ignore checks also confirmed the binary, installer receipt, credentials, final SCO artifacts, provider requests, and Rollout trajectories remain ignored; no sensitive/runtime files are tracked and `web/` has no symlinks.

Offline test evidence was not rerun for this docs-only step and remains exactly **297 passed, 2 skipped in 90.22s** full and **156 passed in 50.41s** focused on `313b6bc`.

## Files committed

1. `AGENTS.md`
2. `reproduction/PROGRESS.md`
3. `reproduction/REPRODUCTION.md`
4. `reproduction/audit/daily/2026-11-12.md`
5. `web/OPERATIONS.md`
6. `web/REPRODUCTION.md`
7. `web/RESULTS.md`
8. `web/data/process.json`
9. `web/data/reproduction.json`
10. `web/tests/test_web.py`

Self-review confirmed no pending-SCO wording remains in current publication, the combined job is never called SUCCEEDED, current Meta evidence is the successful retry rather than historical external-actor evidence, and no fresh EvalPlus/native-shell/training/evaluation claim was introduced. Independent final re-review and the separate five-round preflight remain open.
