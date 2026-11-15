### Task 7: Full Regression, Independent Security Review, and Audited Publication

**Files:**
- Modify: `AGENTS.md`
- Modify: `reproduction/PROGRESS.md`
- Modify: `reproduction/REPRODUCTION.md`
- Modify: `reproduction/audit/daily/2026-11-12.md`
- Modify: `web/RESULTS.md`
- Modify: `web/OPERATIONS.md`
- Modify: `web/REPRODUCTION.md`
- Modify: `web/data/reproduction.json`
- Regenerate: `web/data/process.json`

**Interfaces:**
- Consumes: all prior task commits, local pytest output, SCO terminal evidence, candidate/EvalPlus/BFCL/Codex reports, and reviewer findings.
- Produces: an evidence-backed Task 3 completion or a precisely named blocker; public pages contain only sanitized status and no claim that five rounds or benchmark scoring ran.

- [ ] **Step 1: Run the complete upstream test suite**

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests
```

Expected: all tests pass, except that a pre-existing failure may be reported separately only if it is demonstrably unrelated and still fails on the parent commit. Do not delete or rename the old Landlock test to make it green; port it to explicit backend coverage or preserve the failure as a blocker.

- [ ] **Step 2: Run static and website verification**

```bash
python3 -m unittest discover -s web/tests -v
node --check web/app.js
git diff --check
python3 reproduction/scripts/build_status.py \
  --audit-root reproduction/audit \
  --output web/data/process.json
```

Run the existing secret scan before publication. Confirm `assets/isolation/`, credentials, raw provider requests, private trajectories, and large outputs remain ignored/unlinked.

- [ ] **Step 3: Obtain a fresh independent security review**

Dispatch a reviewer who did not implement the branch. Provide the spec, this plan, the complete diff, and real evidence. Require review of mount breadth/symlinks, namespace and seccomp staging, RPC descriptor inheritance, provider key hygiene, native `/proc` failure classification, timeout/process-group cleanup, persistent home/thread/job recovery, and any mocks or skipped tests presented as real evidence.

File every Important/Critical finding as a blocking change request. Return to the owning task for a failing regression test and minimal fix, then rerun its real SCO smoke and affected full tests. Do not mark Task 3 complete with open Important/Critical findings.

- [ ] **Step 4: Update project guidance and evidence-backed status**

Document explicit backend selection, asset installation, three profiles, keyless relay, failure classification, and smoke commands in `AGENTS.md` and `reproduction/REPRODUCTION.md`. Update current progress from “Landlock hard blocker” to either “bwrap validated by named evidence” or the exact remaining failure. Keep the engineering difference from upstream Landlock explicit.

Update public Markdown/JSON with test counts, SCO job IDs, and sanitized evidence. Candidate/EvalPlus/BFCL/Codex smoke are infrastructure validation, not model benchmark results; five rounds, SFT, 694 evaluation, ACE 50, and 8B remain untouched.

- [ ] **Step 5: Record final verification and commit**

Record full pytest, website tests, real smoke report hashes, reviewer closure, SCO terminal states, and resource cleanup. Commit `docs: publish validated bubblewrap isolation evidence`. Rebuild `web/data/process.json` after the audit entry if needed and include that generated update in the commit. Confirm `git status --short` contains only pre-existing audit churn explicitly explained in the handoff.

- [ ] **Step 6: Stop at the next experiment gate**

Report that isolation infrastructure is ready only if every previous gate passed. The separate five-round preflight still requires all five benchmark minimum smokes, healthy four-GPU Qwen services on logical GPU 0–3, current 694 `python RSIFlow_4B/launch.py --check`, and a final independent experiment preflight review. Do not launch the five-round job under this plan.

## Self-Review Record

- Spec coverage: Tasks 1–2 implement backend/candidate invariants; Task 3 covers EvalPlus; Task 4 covers native RPC/BFCL; Tasks 5–6 cover persistent Codex, keyless transport, thread resume, and durable jobs; Task 7 covers negative evidence review, full regression, audit, and public delivery.
- Placeholder scan: all tasks name exact files, interfaces, commands, expected red/green evidence, and commit boundaries. No model, endpoint, data-release, or scoring fallback is introduced.
- Type consistency: `BwrapRequest`, `BwrapMount`, `BwrapLimits`, `PersistentMetaSandbox.spawn()`, and existing `LinuxSandbox`, `run_native`, `CodexAppServer`, and `ResponsesTransport` names are used consistently across task boundaries.
- Review focus: mount identity, descriptor leakage, secret propagation, `/proc` failure classification, and restart job duplication each have both a focused test and a real-control-flow gate.
- Residual boundary: if Codex `workspace-write` still requires unavailable Landlock inside the outer bwrap, Task 6 fails closed and the design must be amended; this plan does not permit `danger-full-access` as an escape hatch.
