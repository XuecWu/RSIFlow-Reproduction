# RSI-Flow 调研、隔离修复与 SCO 交付复盘

本文沉淀本次 RSI-Flow 源码调研、4B 复现准备、bubblewrap 隔离改造、SCO 排障与公开交付的经验，供后续正式五轮实验与同类基础设施任务复用。

## 0. 文档定位与事实边界

- 工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/bwrap-isolation`；分支 `reproduction/4b-bwrap`；上游调研基线 `496d3cfb9ff39997d107d0ff29e746b07e638180`（`main`）。
- 写作时 HEAD：`6c2049b002ad6c4214af34e8ecfb0331cb03adeb`（`docs: archive isolation review ledger`）。**本阶段 docs-only 提交**按时间顺序为 `d706d32`（关闭独立审阅）→ `68cb220`（刷新 cpolar 服务）→ `6c2049b`（归档审阅 ledger，即写作时 HEAD 自身）。
- 其前的 `861debd`（记录最终 A100 gates）属**发布/审计/生成物提交**：10 个文件为 docs、audit daily、`web/data/*.json` 与网站契约测试 `web/tests/test_web.py`，**未改生产代码，也未改上游 `RSIFlow/tests`**，因此不能与上述三个 docs-only 提交混为一类。最后一个改动生产代码/上游测试的 commit 是 `313b6bcc00ee641ce99eee04fb9e4490dbb63929`。
- 归类提醒：`96714ed` 的 subject 虽写作 `docs:`，实际改了 `rsiflow_validation.py` 与上游回归测试，**不得按 docs-only 归类**；判断提交性质以 `git show --stat` 的实际文件为准，不以 subject 前缀为准。
- 本文只新建本文件，未修改任何其他文件，未 commit/push，未提交 SCO 作业，未启动实验，未删除或清理任何文件，未改动 `operations.jsonl`、`key_results.jsonl` 或 `Meta_logs/`。
- **本文没有重跑测试或 smoke**。所有计数、job 终态与哈希均引自下列已记录证据：`AGENTS.md`、`reproduction/PROGRESS.md`、`reproduction/REPRODUCTION.md`、`reproduction/audit/reviews/bwrap-sdd-progress.md`、`bwrap-task6-report.md`、`bwrap-task7-report.md`、`web/RESULTS.md`、`web/OPERATIONS.md`、`web/data/reproduction.json`、`web/data/process.json`，以及写作时对 git 历史与文件存在性的只读核对。
- 全文不含凭据、密钥内容或私有轨迹；只引用脱敏报告路径、SHA256、job ID 与布尔化检查结论。

### 不可逾越的结论口径

1. combined job `pt-v6m10c4t` **整体/worker FAILED**，只有 Candidate、Harness、BFCL 三个阶段 exit 0 通过；不得写成 SUCCEEDED。
2. `pt-x6b4ista` 是 **8×A100、`root_mapping=false` 的 Meta-only retry，SUCCEEDED/worker Succeeded/exit 0**，它是当前最终 Meta gate。
3. **没有五轮演化、没有 SFT、没有最终模型分数、没有 8B 运行、没有 A800 Meta pass。**
4. Codex **native-shell descendant hygiene 未验证**；当前只允许 `shell_tool=false` 的 construction-level gate，不得为此放宽 seccomp。
5. EvalPlus 是**历史单任务 official comparator smoke**，未在最终 HEAD（`313b6bc` 及其后）重跑，不是 HumanEval+ 分数。
6. 数据是**公开数据同协议重构**，不是作者原始 3000/744 队列的精确复现；744 冻结、计划评分 694、ACE 50 pending。

## 1. 阶段历程与关键决策

### 1.1 时间线

| 阶段 | 时间 | 主要内容 | 关键产物 |
| --- | --- | --- | --- |
| 源码调研与网站 | 2026-11-11 | 克隆上游、通读控制流/协议/GPU 代码、新建 `AGENTS.md`、静态站 TDD 与 cpolar 发布 | 基线 `496d3cf`、网站 9/9→10/10、`web-runtime/logs` 隔离 |
| 4B 复现准备 | 2026-11-11 | 环境/资产核验、v4 冻结数据、official spec 重生成、skip-ACE 694/744 修复、Qwen 独立审阅 CHANGES 及修复 | `rounds_3000_5x600_v4/READY.json`、`cohort-release-v4.json`、`preflight-qwen-r1.md` |
| Provider 与隔离路线诊断 | 2026-11-12 | 中转代理分段计时、`persistent_meta` 硬编码身份修复、Landlock 不可用确认、bwrap 最小可行性探针 | `pt-4hdyar4r`（errno 38）、`pt-vcq25ldi`（六项最小检查） |
| SDD Task 1–5 | 2026-11-12 → 11-14 | pinned bwrap backend/seccomp、candidate 集成、EvalPlus、BFCL native、persistent Meta keyless relay | `7da7d43`、`0e01c1a`、`d551bb6`、`33ba0bd`、`a28e440` |
| SDD Task 6 | 2026-11-13 → 11-15 | 真实 Codex dynamic tools + restart/resume；六轮 SCO 排障；native-shell 环境限制与计划增补 | `e9f56cb`→`bf4befd`→`9cb6188`→`3a51a76`；历史 gate `pt-zj1l9q1t` |
| SDD Task 7 与最终修复 | 2026-11-15 | 真实 Harness 回归 RED→GREEN、发布、fail-closed backend、consolidated 生产修复、最终 A100 gates、独立 re-review 关闭 | `96714ed`→`32ede47`→`313b6bc`→`861debd`→`d706d32`/`68cb220`/`6c2049b` |

### 1.2 关键决策（Ruling）与其代价

| 决策 | 理由 | 若判断错误的代价与缓解 |
| --- | --- | --- |
| 明知 baseline 有 1 个 Landlock 失败仍推进 bwrap 计划 | Task 1–2 正是补齐缺失后端，Task 7 要求完整套件通过 | 可能掩盖第二根因；缓解：同一个真实 Harness 测试始终是最终强制门 |
| 显式 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`，legacy 仅在未设置时保留 | 上游兼容与真实隔离必须可区分 | 误设即失去隔离；缓解：三态选择器对未知值 fail closed（`32ede47`） |
| 不全局注入 bwrap 跑整套测试 | `test_custom_codex_home_and_catalog` 有意验证 no-selection legacy 路径 | legacy 默认覆盖变少；缓解：保留 brief 原命令，真实隔离用例逐个显式选择 |
| candidate scratch 固定挂载 `/workspace` 而非 host 绝对路径 | 绑定 host 路径会让 bwrap 在沙箱内创建 `/data/...` 父目录，违反 SCO 上 `/data` 不可见要求 | 调用方不能假设 host scratch 路径；缓解：协议只用相对 `candidate.py`，真实测试覆盖 |
| `start_new_session=True` + `--die-with-parent`，不用 host `preexec_fn` | 否则外层 bwrap monitor 继承控制器 PGID，清理会误杀/漏杀 | 超时清理可能残留后代；缓解：真实 timeout 测试与进程组检查 |
| 只安装 pinned EvalPlus 声明的缺失依赖，不换解释器、不降级生产 | 官方 smoke 无法在缺 `datasets` 时运行 | 环境冲突风险；缓解：先查官方 manifest 与已装状态，只补 `datasets==5.2.0`、`dill==0.4.1`、`multiprocess==0.70.19`、`xxhash==4.1.0`，`pip check` 干净 |
| shared `/data` 上保留既有 root owner，不忽略错误 | `os.chown` 对既有目录 EPERM 会阻断 native 启动 | 所有权不一致被放过；缓解：mismatch 仍 fail/reclaim |
| SCO 启动脚本引用主仓库绝对凭据路径，不把密钥复制进 worktree | `assets/private/` 被 gitignore，worktree checkout 中不存在；共享 `/data` 已挂载且同 uid | 主仓库私有文件成为该类作业的外部依赖；缓解：缺失时写 `needs_context.txt` 并 exit 76 |
| 拒绝为 vendor native shell 放宽 anti-escape seccomp | 内层 bwrap 需要不带 `CLONE_THREAD` 的 clone，放宽会让模型 payload 在沙箱内创建 user namespace | native-shell 后代环境未验证；缓解：`shell_tool=false` + construction-level 断言，并公开声明未验证 |
| 把 native-shell 缺口作为**计划内部冲突**处理，增补 Step 4 而非改代码绕过 | binding spec 禁止 Landlock/host/`danger-full-access` 回退，与已审阅 seccomp 冲突 | 真实 shell 后代可能继承意外环境；缓解：公开明确不宣称 native-shell 通过，未来需单独批准 inner/outer 设计 |
| smoke 使用 per-run `meta_logs_root`，不改生产 `prepare_meta_storage()` 语义（Task 6 阶段） | 不同 fresh run 绝不能共享 Codex 状态，同 run 重启仍须恢复 | smoke 记录落在 run 下而非集中目录；缓解：用 SCO 完全相同的 `.../run` basename 先本地验证 |
| 最终修复波次**推翻**上一条"生产不动"的决定 | smoke-only workaround 不能证明真实入口可运行；信任 pin 不一致属安全问题 | 生产存储/受信资产初始化成为新增兼容面；缓解：same-path resume 与 legacy 收养由测试保留，不删除/重写历史 artifacts |
| A800 r5/r6 启动前失败后停止重试，接受用户授权的 A100 证据 | 两次同 host 无执行失败，外部同 commit A100 成功证明真实路径可执行 | A800 控制面根因仍未解决、root_mapping 差异未覆盖；缓解：披露 actor/资源/形状差异，不宣称 A800 pass |
| combined 失败后只允许**一次 Meta-only retry** | 失败是外部 provider，其余三阶段已通过 | 多一次有界 8 卡/API smoke；缓解：若再失败则保留 blocker，不无限重试、不改代码掩盖 |
| 审计单根放在主 checkout，源码 commit 留在 worktree | 避免 `sequence.state` 分裂与操作丢失 | 两处审计路径需 reconciliation；缓解：每条记录带绝对 cwd/commit 证据 |
| 历史操作用独立 SHA256 sidecar，新操作加 `evidence_sha256`，不回写追加账本 | 保留审计历史同时提高可核验性 | sidecar 无法恢复被删内容；缓解：原始日志安全保留 |
| 原始日志不批量公开、不强制纳入 git | 可能含敏感路径/输出 | 证据持久性依赖本地文件与 hash 清单；缓解：公开页只展示脱敏快照与 `as_of_sequence` |

## 2. 已交付内容与真实验证证据

### 2.1 离线回归与发布检查（最终计数）

| 项目 | 结果 | 证据出处 |
| --- | --- | --- |
| 完整上游套件 | **297 passed, 2 skipped**。**实际执行完整 pytest 的 commit 是 `861debd`**（controller 复核，85.30s）；该计数首次在 `313b6bc` 达成（90.22s）。其后 `d706d32`/`68cb220`/`6c2049b` 为 docs-only，只重检 website 11/11，**不等于重跑完整套件**；写作时 HEAD `6c2049b` 未重新执行完整 pytest | `bwrap-task7-report.md` consolidated fix wave 节、`bwrap-sdd-progress.md` 末条（"Final controller verification on `861debd`: 297 passed + 2 skipped in 85.30s"）、`web/data/reproduction.json` `experiment_stages.offline_tests` |
| 综合聚焦回归 | **156 passed**（50.41s） | 同上 |
| 网站契约测试 | **11/11 passed**（含既有 assigned-credential secret scan 与 raw-log/client-metadata 检查） | `bwrap-task7-report.md`、`web/RESULTS.md` |
| `node --check web/app.js` | exit 0 | 同上 |
| `git diff --check` / `git diff --cached --check` | exit 0 | 同上 |
| Ruff | 修掉 2 个新增 import-order 后仍有 **18 个既有 legacy findings**，**不宣称 clean** | `bwrap-task7-report.md` |
| 资产/公开面检查 | `assets/isolation/`、`assets/private/`、`reproduction/logs/`、`Rollout_logs/`、`Meta_logs/` 均 ignored 且无 tracked 文件；`web/` 无 symlink；trusted receipt 存在且非 symlink | 同上 |

**计数归属与执行者（避免误读）**

- `web/OPERATIONS.md`「controller 在最终 HEAD 再次运行完整测试：297 passed、2 skipped」一句，按 SDD ledger 的精确记录应归属到 **`861debd`** 那次完整 pytest；`d706d32`、`68cb220`、`6c2049b` 是 docs-only 提交，只重检了 website 11/11，**docs 提交本身不构成完整套件重跑**。引用该计数时必须写出实际执行 commit，不能写成"每个后续 HEAD 都跑过"。
- **本文作者（Qwen 复盘 implementer）没有运行任何 pytest、website 测试或 smoke**，上述全部数字均为转引已记录证据；本文只做了 git 元数据、文件存在性、账本逐行比对与服务存活性的只读核对。
- 审阅反馈指出：主 agent 其后已在功能工作树另行取得 297 passed / 2 skipped。该次执行发生在**本文写作之后**，属 controller 的后续行为，应由其自身的 commit 与审计条目记录；本文作为写作时历史快照，不把它计入本文证据，也不据此声称任何新的验证。

计数演进（保留为历史，不覆盖）：baseline `169 passed/2 skipped/1 failed` → Task 1 `203`→`211` → Task 2 `223`→`224`→`227` → Task 3 `231` → Task 4 `238`→`242` → Task 5 `254` → Task 6 `270`→`274`→`275`→`276` → Task 7 `277`→`278` → 最终修复波次 `297`。公开计数曾停留在 277/79，被独立审阅列为 Minor 后统一同步为 297/156。

### 2.2 真实 SCO 证据（全部为基础设施 smoke，不是模型分数）

| 用途 | Job | 终态 | 报告/回执 SHA256 | 口径 |
| --- | --- | --- | --- | --- |
| Candidate（最终 gate 之一） | `pt-gyfupguq` | SUCCEEDED / worker Succeeded | `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63` | 7 functional + 18 negative + 5 timeout + 3 session 全 true，`infrastructure_error=null` |
| EvalPlus（历史） | `pt-bfkp5k0d` | SUCCEEDED / worker Succeeded | smoke `537ed60c8ed385ecab81197fb218a6e8117bfc3a0871d5e2445779daf524bfc0`；official canonical `3125b42c1d1f8ef7ba4a6a4ee263ab653485c11a0b8e087327a9987ea8a2ffa2` / wrong `f41bea3178f193a2228b196fe3709fe8f1aa6321302ff04c0ef29a3bf2427514` | 一个 HumanEval+ 任务 canonical Base/Plus pass、wrong Base/Plus fail；**未在最终 HEAD 重跑，不是分数** |
| BFCL（历史） | `pt-fdvsxqax` | SUCCEEDED / worker Succeeded | `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4` | pinned source `ea13468e4423454d0c213704fb87cf7cb3990433`，恰好两次受控 CPU RPC，key hygiene 通过；不是分数 |
| Persistent Meta（历史 Task 6） | `pt-zj1l9q1t` | SUCCEEDED / worker Succeeded，2026-11-14T17:33:42Z | `b1f1dbf35c09ee192c2199028ee984ba7068889bdf3f126cd52965732220f920` | 外部 actor `huangsiyuan1` 提交、8×A100、N3lS+N2lS、`root_mapping=true`，经用户授权接受；**仅历史证据** |
| 四卡模型诊断 | `pt-ox3o5he2` | SUCCEEDED | `reproduction/audit/smoke/model-service-r3-result.json` | 四个 BF16 A800 服务、checkpoint/weights 绑定、短生成后释放；`current_service_running=false`，不是 benchmark |
| combined 最终 gate | `pt-v6m10c4t` | **整体/worker FAILED** | candidate `56b3fc6a…`；Harness log `808285dc5c05709785e4d669e28882e5c496644bb1191a6301866325b2735f31`；BFCL `ac659bf1…`；key hygiene `fbe9b6386dd64f78e5eb72b7bbad9f88f2c39e218b93bd6ab049a0b97b20749d`；Meta failed report `4f123a27dc415e2b99d63569fee30af2fce68bf3fda68da3653ee7e9e1db3d9c` | 8×A100、`root_mapping=false`、commit `313b6bc`；`candidate/harness/bfcl.exit_code=0`、`meta.exit_code=1`、`overall.exit_code=1`；Harness `1 passed in 23.26s` |
| Meta-only retry（当前最终 Meta gate） | `pt-x6b4ista` | **SUCCEEDED / worker Succeeded / exit 0**，资源已释放 | `a177657dbf1e744f2d2be64d16c33bcda08102052a7ecd7486aebdd48cf64b66` | 8×A100、`root_mapping=false`、unchanged `313b6bc`；`ok=true/stage=completed`、same thread、`duplicate_jobs=[]`、`bootstrap_repeated=false`、前后均精确 `gpt-6-astra`、3 次 dynamic tools、11 项 key hygiene + 7 项 cleanup 全 true、durable job returncode 0、`shell_tool=false`、`installation_id` 0644、`.run-owner.json` 精确匹配 |

combined 输出目录：`reproduction/logs/final-bwrap-a100-20261115T043242Z/`；Meta retry 输出目录：`reproduction/logs/final-meta-a100-20261115T043755Z/`。

### 2.3 生产路径修复（最终修复波次，`313b6bc`，23 文件 / 19 新测试）

1. **fresh Codex 启动**：`launch_meta.prepare_codex_home()` 调用唯一生产 helper，原子 no-follow 创建并校验 0644 `installation_id`；same-path restart 保留 inode/content；symlink、非普通文件、非法 UUID 一律拒绝且不改写。RED `5 failed` → GREEN `5 passed` + smoke 生产路径 `1 passed`。
2. **same-basename 存储冲突**：central `.run-owner.json` 绑定 resolved full run path；不同 fresh run 在创建 run link 前 `FileExistsError`；same-path 保留状态；无 owner 的 legacy 目录只有在既有链接可证明 same-path 时才收养；owner/central symlink fail closed。RED `5 failed` → 组合 storage/installation `14 passed`。
3. **全 profile 受信资产**：ignored `assets/isolation/install-receipt.json` 固定 `manifest_path/manifest_sha256` 与 `launcher_path/launcher_sha256`；candidate/native/Harness 每次默认 launch 重新验证，tamper/missing/symlink 在 spawn 前失败。RED backend `3 failed` + consumers `9 failed` → GREEN `10 passed`。persistent Meta 的 static trusted copies 与强制 hash 未改。
4. **fail-closed backend 选择器**（`32ede47`）：未设置→legacy，精确 `linux_landlock_seccomp`→legacy，精确 `linux_bwrap_seccomp_v1`→bwrap，其他任何值在开日志/`Popen` 前抛 `SandboxUnavailable`。RED 证明非法 ID 曾到达 `Popen`，GREEN 无任何禁止副作用。
5. **真实 Harness 回归**（`96714ed`）：保留测试名与全部断言，只加显式 backend 选择；根因是 `rsiflow_validation._worker` 绕过 `LinuxSandbox` 自装 Landlock；接入 pinned backend 后再 RED 于被 backend 拒绝的 `TOKENIZERS_PARALLELISM`，仅移除该非必要变量后真实 GREEN `1 passed in 32.98s`。

### 2.4 数据与协议交付

- v4 冻结 release：3000 = 5×600，独立验证 744；每轮 Code 120 STDIO + 80 function；READY 记录 744 frozen / 694 scored / 50 pending，config hash `22305bf10342e11f76aab19df033ce6fedacee033ef4acbd043249b5c3caa231`。
- `reproduction/audit/cohort-release-v4.json` 连接 source build、READY 与逐源对账；`data-audit-v4.json` 记录只读泄漏审计：Code train/dev overlap = 0、final exact matches = 0；EnvScaler train/heldout 共享 **191** 个环境定义，上游总状态 `failed_overlap`，属明确保留差异。
- 保留意见必须随证据公开：`clean_independence_audited=false`、`exposure_unknown`、`version=reconstructed-v1`、无语义改写保证、未标注变体可能漏检、不对预训练污染下结论；两次隔离重建 `assets/cohort-mixed-qa-20261111T151857Z`、`assets/cohort-qa-id-missing-20261111T154900Z`。
- 四类 official spec 从 v4 重生成：164/178/100/100；HotpotQA 与 2Wiki 官方 v1 单条金答案 smoke 按 100 固定分母得 EM=F1=0.01，**只证明评测链路**。2Wiki 保持上游 v1，不为使用 aliases 改协议。
- skip-ACE 用 `RSIFlow/validation_skip.py` 按 settings/manifest 计算 partial 计数，不复用旧 250/300 硬编码，完整缓存不得当作 skip-ACE 结果复用。

### 2.5 独立审阅结论

- Task 级审阅 + 修复轮次全部关闭；全分支安全审阅（未参与实现的 reviewer）初判 0 Critical / 3 Important / 2 Minor，consolidated 修复后 scoped re-review **五项全部 ADDRESSED，无新增 Critical/Important，Ready to merge Yes**。
- 十项历史 deferred minor 统一 triage 为 ACCEPTABLE DEFERRAL，其中旧 Landlock Harness 项已随真实修复关闭。
- 保留一个诊断性 Minor：非 UTF-8 既有 `installation_id` 可能抛 `TypeError` 而非预期 `ValueError`（`RSIFlow/launch_meta.py:186-193`）；仍在 Codex 启动前拒绝且不改写文件，不影响隔离与状态安全，按 SDD 最终审阅政策不开第二修复波次。

## 3. 失败、根因与重试历史

### 3.1 已确认根因（有可复现证据）

| # | 现象 | 已确认根因 | 修复 |
| --- | --- | --- | --- |
| 1 | 本机与 SCO worker 上真实隔离失败 | Landlock 不可用：本机 ENOSYS，SCO `_apply_landlock()` errno 38；pinned Codex legacy helper 在 `linux_run_main.rs:328` 返回 `Sandbox(LandlockRestrict)` | 显式 bwrap 后端替代，无回退 |
| 2 | Task 2 SCO negative matrix 空白/-9 | 强制 negative 矩阵共享 1 秒 wall budget，未改动本地运行实测 0.9608–1.0491s | `9b1c00d`：normal/negative/session 10s，刻意 timeout 仍 1s，加逐阶段诊断 |
| 3 | EvalPlus SCO 在官方比较前失败 | 共享 rsiflow 环境缺 pinned EvalPlus 声明的 `datasets`（本地 import probe `datasets_available=false`） | 只补声明缺失的四个包，不降级生产 |
| 4 | BFCL SCO 在 native 启动前失败 | `tool_sandbox.py:184` `os.chown(..., follow_symlinks=False)` 对 shared `/data` 输出目录 EPERM | `33ba0bd`：保留匹配条目的既有 root owner，mismatch 仍 fail/reclaim；同时修 fixed-launcher 解释器挂载与 `BFCL_PROJECT_ROOT` 输出重定向 |
| 5 | Meta r1 exit 76 `NEEDS_CONTEXT` | 启动脚本按 worktree 相对路径找 `assets/private/credentials.sh`，而该目录被 gitignore、worktree 中不存在 | 改用主仓库绝对路径（共享 `/data` 已挂载、同 uid），不复制密钥 |
| 6 | Meta r2 `first_app_server` RuntimeError | 外层 launcher `umask 077` 使 `installation_id` 为 0600/size 0，Codex 归一化到 0644 的 `fchmod` 被 persistent_meta seccomp 拒绝（EPERM）；direct-host 对照为 0644/size 36 | `bf4befd`：进入 bwrap 前在 host 预创建有效 0644 ID，并补齐 sanitized `error_message`、app-server 诊断与 provider records 快照 |
| 7 | Meta r3 errno 38，未达 thread/start | r2/r3 都用 `RUN_DIR=$OUT/run`，`prepare_meta_storage()` 只按 basename 键控，两个 `run/meta_session` 指向同一 `Meta_logs/run/meta_session`，r3 复用 r2 的 `shell_tool=true` 配置与陈旧 journal（path/inode/mtime 证据） | `3a51a76`：smoke 传 `meta_logs_root=run/"Meta_logs"`；最终波次进一步把生产 ownership 改为 resolved full path |
| 8 | 显式选 bwrap 后真实 Harness 仍 errno 38 | `rsiflow_validation._worker` 直接调用 `_restrict_filesystem`/`_restrict_syscalls`，父进程选择传不进去 | 接入 pinned `BubblewrapBackend` + candidate profile，owned mount FD 透传后关闭 |
| 9 | 接入后仍失败 | backend 拒绝名字含 `TOKEN` 的环境变量，`TOKENIZERS_PARALLELISM` 被误传 | 只从 bwrap 专用环境移除该非必要变量，不放宽 backend 校验 |
| 10 | 未知/拼错 backend 静默走 legacy | 选择器把"非精确 bwrap"一律当 legacy | `32ede47` 三态 fail-closed + no-spawn 回归 |
| 11 | 生产 fresh Meta 启动会失败 | `installation_id` 初始化只存在于 smoke，生产 `prepare_codex_home()` 没有 | 生产原子 no-follow helper，删除 smoke 专用 helper |
| 12 | 不同 fresh run 共享线程/配置/job | 生产 central storage 仅以 `run_dir.name` 为键 | `.run-owner.json` 绑定 resolved full path，legacy 仅在可证明 same-path 时收养 |
| 13 | 安全资产可被 writable source 篡改 | candidate/native/Harness 默认 backend 未强制外部 manifest/launcher 身份 | installer receipt 强制两类 hash，每次 launch 校验，spawn 前 fail closed |
| 14 | Task 1 审阅 2 Critical + 3 Important | mount `..`、native clone 语义、Meta namespace flags、错误分类、精确可写根 | `7da7d43`：保留 `MASKED_EQ(CLONE_THREAD,0)`，对 clone3 返回 ENOSYS 让 glibc 回退到受检 clone |
| 15 | Task 2 审阅 2 Important | 校验与 bwrap open 之间的 mount 身份竞争；自定义 `runner.python` 可能在原路径执行 | `0e01c1a`：`O_PATH|O_NOFOLLOW` 固定 device+inode、`/proc/self/fd` 来源、owned FD 传递与关闭、candidate 固定用挂载的 `/usr/bin/python3` |
| 16 | Task 5 审阅 1 Critical + 4 Important | 可写 RSIFlow 控制 host-trusted 校验、leader 退出后后代清理、resume 后端降级、`writable_outputs` 无约束、partial-spawn JSON-RPC 日志泄漏 | `a28e440`：static trusted copies、PGID 清理、resume backend 匹配、输出限制、partial 日志清理 |
| 17 | 本地/公网服务突然不可达 | 两个后台任务在默认 `timeout_ms=600000` 到点被终止，不是网页内容问题 | `disable_timeout=true` 重启持久任务 |
| 18 | 公开根目录泄漏运行元数据 | `web/logs/cpolar.log` 含 client ID 与网络端点 | 新增安全测试 RED→GREEN，原始日志/PID 移到 `web-runtime/logs/` |
| 19 | SDK `/models` 90 秒审计上限超时被误判为代理无响应 | 冷缓存 `import openai` 耗时 67.18s | 分段计时定位后延长上限；非流式 200/completed、流式 15 SSE 以 `response.completed` 结束 |
| 20 | Meta 原生线程身份错误 | `persistent_meta.py` 写死旧 `autodl/DeepSeek` | RED→GREEN 改为继承有效 base 配置，`test_persistent_meta.py` 8/8 |
| 21 | 早期探针在隔离前失败 | `/usr/lib/python3.12` 不存在；`/etc/python3.10` 版本化符号链接白名单过宽/过窄 | 标准库路径发现测试 + 只允许所选解释器对应的 `/etc/pythonX.Y` |

### 3.2 外部 provider 故障（不是本仓库代码缺陷）

combined `pt-v6m10c4t` 的 Meta 阶段在 `read_json_turn` 未取得完整 provider response：

```text
ordinal 0: http_status=502, completed=False, error_type=terminal_provider_failure,
           provider_outcome=failed, terminal_failure.code=http_502,
           provider_completion_unknown=True, request_rejected=False
ordinal 1: http_status=200, completed=False, error_type=ValueError,
           validation_error="Upstream stream ended without a complete response",
           upstream_event_counts={}
```

- 分类：**外部 relay/provider completion failure**。传输层事实已确认（HTTP 状态与流结束方式），provider 侧内部根因未证实。
- 处置：不改代码掩盖、不重跑已通过的三阶段、combined 保持 FAILED；只允许一次 Meta-only retry `pt-x6b4ista`，成功。
- 失败报告与成功报告分别留哈希（`4f123a27…` / `a177657d…`），审计分成三条结果：`00001241-combined_candidate_harness_bfcl_gates`、`00001243-combined_meta_external_failure`、`00001245-final_meta_a100_gate`。

### 3.3 控制面/调度问题（假设，未证实根因）

- `pt-xvokh89h`：QUEUEING→STARTING（replicas 始终 0）→SUSPENDED，`jobs start` 恢复后再次 SUSPENDED；无 start_time、worker Deleted、无任何输出产物。属**无执行的控制面挂起**，既不支持也不反驳存储修复。
- `pt-7i4qertj`（r5）/`pt-44m6zkmx`（r6）：均在启动命令执行前 FAILED，无输出目录/报告/exit code；r6 在创建后 19 秒失败，worker 落在与 r5 相同的 host。**同 host 节点/控制面故障是假设，不是已证明根因**；describe 无 error reason，bounded stream-logs 只有 `job scheduled successfully` 与 `job execution has failed`。
- 已排除的"显然解释"：cluster ACTIVE、`node_exception_number=0`；容量报告 248 reserved / 212 used / 36 idle（另一路 216/32，worker 归因因 SenseCore 查询错误不可得）；用户 205 个历史 job、0 个 active（49 FAILED / 122 SUCCEEDED / 34 SUSPENDED），未触发文档中的 ≤200 ACTIVE 限制。API 未暴露挂起 actor 或原因。
- 触发停止规则：连续两次无执行失败后不再 r7、不再盲目 resume，改为上报外部基础设施 blocker；随后由用户显式授权改用 A100 证据。

### 3.4 排队、超时与监控历史（必须保留，不得美化）

- 长时间 STARTING/replicas=0 排队：`pt-jwftosra` 约 6 小时后 FAILED；`pt-mgrcqlpw` 约 12 小时后 SUCCEEDED；`pt-f8joesmo` 约 14 小时后 FAILED；`pt-5jkp7z0s` 约 8 小时监控后 FAILED。
- 监控退出码 **124 是本地 cap，不是 SCO 终态**：`bash-oj4h670x`（工具自身 60s 限制）、`bash-p8uhl424`（内部 4h cap，238 次连续 STARTING 采样）、`bash-pxw3n2m2`/`bash-aiu5xvqv`、`bash-zvoxm3si`/`bash-tre1rps5`、`bash-hy3uhbx6`（480 次采样）/`bash-cv0udusj` 等，均按"同一 job、单一 watcher、终态或产物触发"规则接替。
- 一次 implementer 前台派发在等待 SCO 时 2 小时超时；改为后台有界收集，**未产生重复 job**。
- 作业级失败/挂起清单（保留全部历史）：`pt-vqm5sa3h`、`pt-03lf4y6x`（诊断暴露白名单缺陷）、`pt-b1r8t0uk`（早期 GPU 诊断失败）、`pt-l379djx0`（首次模型超时）、`pt-jnjtat52`（第二次模型 alias 失败）、`pt-if57mo0b`（缺 bwrap）、`pt-texqw4a8`（Landlock 失败）、`pt-jwftosra`、`pt-ytdsulda`、`pt-y28qppv6`、`pt-f8joesmo`、`pt-r3dmul4n`、`pt-5jkp7z0s`、`pt-xvokh89h`（两次 SUSPENDED）、`pt-7i4qertj`、`pt-44m6zkmx`、`pt-v6m10c4t`（整体 FAILED）。
- 操作级失败计数（公开快照 `web/data/process.json`，`generated_at=2026-11-15T05:07:57Z`、`as_of_sequence=1257`）：**success 876、failed 243、blocked 2、timeout 17、failure 2，合计 1140 operations / 101 results**。这些 failed/timeout 包含本地命令与探针失败，是刻意保留的历史，不做删改。
- 写作时只读核对：主 checkout 审计根 `operations.jsonl` 已 1151 行、`key_results.jsonl` 101 行，说明公开快照之后仍有新操作，页面需要重新生成（快照自带 `is_snapshot=true` 与 `snapshot_note`）。

## 4. 可复用经验

### 4.1 Landlock / bwrap 边界

- 真实隔离必须**显式**设置 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`；未设置时只为上游兼容保留 legacy 默认。选择器必须三态 fail closed：未知/拼错值在 spawn 前抛 `SandboxUnavailable`，绝不静默降级。
- **禁止任何回退**：host、Landlock、`danger-full-access` 都不允许。bwrap 失败归类为 infrastructure error，**不能记成模型零分**——这是协议正确性问题，不只是工程问题。
- 环境能力要先用最小探针证实，再谈集成：`pt-4hdyar4r` 证明"其余前置阶段都过、只差 Landlock"，`pt-vcq25ldi` 证明无 `/proc` 条件下 bwrap 的隐藏/只读/可写/断网六项成立。路线筛选证据不能替代集成证据。
- 不要为了跑通而全局注入后端：真实隔离用例逐个显式选择，保留 no-selection legacy 契约测试。诊断性全局注入的失败必须如实记录，不能当作最终结果。
- 嵌套沙箱是硬边界：外层 anti-escape seccomp 拒绝不带 `CLONE_THREAD` 的 clone，内层 vendor bwrap 因此 EPERM。**宁可公开"未验证"，也不放宽已审阅的 seccomp。**
- 环境变量命名也会被安全策略拒绝（含 `TOKEN` 的名字）。修法是移除不必要的变量，而不是放宽校验。

### 4.2 shared `/data` 与 worktree

- SCO worker 与 dev 机共享同一 `/data` 卷，这既是便利也是陷阱：
  - 对既有 root-owned 目录 `os.chown` 会 EPERM。正确做法是**保留匹配条目的既有 owner**，mismatch 仍然 fail/reclaim，而不是忽略错误或强行改所有权。
  - **gitignored 资产不会出现在 worktree checkout 里**（`assets/private/`、`assets/isolation/`）。引用主仓库绝对路径，而不是把密钥复制进 worktree；并用显式 `needs_context.txt` + 非零 exit 表达"外部依赖缺失"。
  - 输出目录要显式重定向（如 `BFCL_PROJECT_ROOT`），否则官方工具会往共享盘的不期望位置写。
- candidate 的可写 scratch 固定挂载到 `/workspace`，不要绑定 host 绝对路径，否则 bwrap 会在沙箱内创建 `/data/...` 父目录，破坏"host 层级不可见"的负向断言。
- 安全资产身份必须来自 **writable source 之外**：ignored installer receipt 固定相对路径 + SHA256，每次默认 launch 重新验证；diagnostics 或可写源码里的常量不能当信任输入。

### 4.3 A100 / A800 证据不可混用

- 每条真实证据必须同时披露：**提交 actor、实际资源形状、candidate specs、`root_mapping`、commit、终态与 worker 终态、报告哈希**。
- 外部 actor（`huangsiyuan1`）提交的成功不能记到 `main-kimi` 名下；`root_mapping=true` 与 `false` 不是等价基础设施。
- **启动前失败 ≠ 通过，也 ≠ 代码缺陷**：A800 r5/r6 没有执行任何启动命令，因此既不能宣称 A800 Meta pass，也不能据此推断 smoke 代码有问题。
- 用户授权改变资源门槛时，要写成显式 ruling 并保留差异披露；授权只覆盖该次门槛，不自动推广到正式实验资源方案（正式仍是主分区 8×A800 仅用 GPU 0–3）。
- 当前最终 Meta gate 只指 `pt-x6b4ista`；历史 `pt-zj1l9q1t` 永远标注为 historical。

### 4.4 fresh run 状态与恢复

- **basename 不是 run 身份**。`r2/run` 与 `r3/run` 会别名到同一集中目录，导致后一次复用前一次的 `codex_home/config.toml`、app-server journal 与 stderr——表现为"本地能过、SCO 不过"的假环境差异。
- 正确模型：central `.run-owner.json` 绑定 **resolved full run path**；不同 fresh run 在创建链接前 fail closed；same-path restart 保留状态；无 owner 的 legacy 目录只有在既有链接可证明 same-path 时才收养。
- `installation_id` 必须是原子 no-follow 创建的 0644 普通文件、合法 UUID；symlink/目录/非法内容拒绝且**不改写**；有效重启保留 inode/content。外层 `umask 077` + seccomp 拒绝 `fchmod` 的组合会让"事后归一化"必然失败，所以要在进入沙箱前于 host 侧准备好。
- 恢复必须同一 thread/job：`same_thread=true`、`duplicate_jobs=[]`、`bootstrap_repeated=false`、durable job 先 reconcile 再发新 turn；不重复 bootstrap、不覆盖历史 attempts、不删除或迁移历史 artifacts。
- smoke 与生产要共用同一套初始化路径。smoke-only workaround 会掩盖真实入口不可运行——这是最终审阅把它升级为生产 Important 的原因。

### 4.5 manifest / receipt / provenance

- 固定并校验：pinned bwrap 0.6.1（SHA256 `d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca`）、Codex tag `rust-v0.153.4` / commit `3d2ee51ca2d5db578f328aa75e20aa22c0197c9a` / binary SHA256 `56ef98ab4032d317ab26e9b5e5a175650717351edb16ed9cde0cb6d1734d62da`、BFCL `ea13468e4423454d0c213704fb87cf7cb3990433`。
- receipt 由**真实 installer 生成**，不手写；tamper 用"仅空白字符"改动来测，才能测出身份漂移而不是 JSON 语法失败。
- 数据侧同理：`prepare_600_744.py` 只重打包既有 source manifests，校验 hash 与角色；重复准备只允许 `base_config`/`run_name` 差异，其他协议变化必须新 frozen release，不覆盖旧目录。
- 哈希语义要写清楚：`config_hash` 是规范化 JSON 的 fingerprint，`source_*_hash` 取自 manifest 内部 `manifest_hash` 字段，**不是文件字节 sha256**。不注明会让复核者得到"不匹配"的假警报。

### 4.6 key hygiene 与公开面

- 真实 `OPENAI_API_KEY` **只在 host `ResponsesTransport`**；sandbox 内只有 `RSIFLOW_RELAY_KEY=pipe-only` 与私有 loopback relay。host job 环境会继承非密钥父变量（executor 只剥离 API key），因此**不要对 host job 做"标记不存在"的断言**。
- 报告只输出脱敏 metadata、ID、布尔与哈希；native shell 输出只解析为布尔；绝不打印 env 值、raw prompt/response。provider 证据要在重启前后分别快照，避免第二阶段覆盖第一阶段。
- 公开前固定检查：既有 assigned-credential secret scan、raw-log/client-metadata 测试、`git check-ignore`/`git ls-files` 确认敏感与运行时文件未被跟踪、`web/` 无 symlink、原始日志在公开根目录之外（`web-runtime/logs/`）。
- 服务生命周期也是交付的一部分：后台任务默认 600s 超时会"静默交付失败"，必须 `disable_timeout=true` 并在文档中记录真实 PID/URL。

### 4.7 combined job 与 Meta-only retry 的结论口径

- **整体终态优先**：combined `pt-v6m10c4t` 整体/worker FAILED 就必须这么写，哪怕三个阶段 exit 0。分阶段结论用独立字段/独立审计结果表达，不能合并成"job 成功"。
- 外部 provider 失败不改代码掩盖、不重跑已通过阶段；retry 必须**恰好一次**、同 commit、新输出目录、有界预算，并预先写明"再失败就保留 blocker"。
- retry 成功 ≠ combined 成功。当前 Meta gate 是 retry 的 report 哈希，combined 的失败报告哈希同样保留。
- 未在最终 commit 重跑的证据必须显式标注（EvalPlus `historical_one_task_smoke_not_rerun_on_313b6bc`）。旧 commit 的 SCO ID/哈希不得当作新代码的验证。
- 基础设施 smoke 永远不能转成模型质量或 benchmark 分数；单任务 comparator、两次 RPC、短生成答案都只是链路证据。

### 4.8 流程与审阅方法

- TDD 的 RED 必须是**目标根因失败**：fixture 缺目录导致的 `FileNotFoundError`、import 错误都要先修好，再取真正的 RED（本次多次出现"先修 fixture 才拿到预期 RED"）。
- 审阅分层有效：task-scoped 审阅 → 全分支独立安全审阅 → 修复后 scoped re-review。deferred minor 统一 triage，避免每个任务重复争论。
- 计划内部冲突要显式增补并披露（native-shell Step 4），而不是悄悄改实现或放宽安全约束。
- 排队与监控纪律：一个 job 一个 watcher；本地 cap 退出（124）不等于 SCO 终态；终态确认后才允许替换；不重复提交；无执行失败连续两次即停止并上报。
- 文档计数必须随最后一次代码/测试变更同步（277/79 → 278/80 → 297/156），历史 RED/GREEN 条目保留原计数作为审计历史。
- 审计单根 + 每条记录带绝对 cwd/commit，避免 worktree 与主 checkout 的 `sequence.state` 分裂。

## 5. 尚未完成事项与正式五轮前的 preflight

### 5.1 明确未运行 / 未验证

- **未运行**：五轮演化（A0 + B1–B5）、SFT、最终 694/744 评估、ACE 50、8B 全流程、正式 Qwen 四卡服务。**没有任何模型 benchmark 分数。**
- **未验证**：Codex native-shell descendant hygiene。当前只有 `shell_tool=false` 的 construction-level gate；要关闭该缺口需另行批准 inner/outer sandbox 设计，不得放宽 seccomp 或使用 `danger-full-access`。
- **未重跑**：EvalPlus 在 `313b6bc` 及最终 HEAD；BFCL/Candidate 的历史 accepted 证据属各自 commit，最终 A100 gates 已覆盖 candidate/Harness/BFCL/Meta 受影响路径。
- **未解决**：A800 启动前失败与两次 SUSPENDED 的控制面根因；不存在 A800 Meta pass。
- **保留 Minor**：非 UTF-8 `installation_id` 的异常类型（`RSIFlow/launch_meta.py:186-193`，诊断性）；十项 deferred minor 已 triage 为可接受延后，其中值得后续处理的包括：negative 矩阵应要求精确命名键集、returncode -9/scratch 清理不直接证明所有内层后代退出、外层 smoke 输入/配置/JSON 错误应结构化而非 traceback、异常 `thread.start` 失败可能泄漏 RPC FD、persistent-mount 缺专用真实探针、dynamic-tool completion 事件未逐个按 call ID/tool/arguments hash fail-closed 匹配、共享 `codex_host.toml` 的 shell 关闭可改为 smoke-private 模板。
- **Ruff**：18 个既有 legacy findings，未做无关 lint 重写，不宣称 clean。
- **数据保留意见**：`clean_independence_audited=false`、`exposure_unknown`、`reconstructed-v1`、EnvScaler 191 个共享环境定义（上游 `failed_overlap`）、两次隔离重建历史。这些必须随 `frozen_data` 一起公开，不能只写 completed。
- **配置预检**：`launch_preflight` 当前状态是 `historical_precheck_current_694_check_pending`，当前 694 配置的 `--check` 尚未在本轮重新执行。

### 5.2 五轮前四道强制门（缺一不可）

1. **五类 benchmark 最小 smoke**（当前 `benchmark_smoke` 仅 partial：candidate、HumanEval+ 单任务 official comparator、BFCL 两次 RPC、Codex dynamic/resume；`model_benchmark_scores=false`）。
2. **逻辑 GPU 0–3 上健康的四卡 Qwen 服务**：每个推理 replica 启动前对应卡 ≥20,000 MiB 空闲；MODEL 更新前停服务并要求 GPU 0–3 每卡 ≥40,000 MiB；健康检查必须同时校验 GPU、checkpoint 路径与 weights manifest，不能只看端口存活。
3. **当前 694 配置预检**：`python RSIFlow_4B/launch.py --check`（返回 0 不代表模型/API/评测器可用）。
4. **最终独立 experiment preflight review**（与已关闭的隔离安全审阅是两道不同的门）。

### 5.3 资源与运行方式

- 正式方案仍是**主分区 8×A800、仅用 GPU 0–3、4B/8B 顺序执行**；低风险替代是 dev `sensebot-a800-dev-liujianbo` / workspace `sensebot-a100-a800-liujianbo` / 单节点 `N3lS.Ii.I60.4`（4×A800 80GB、48 vCPU、480 GiB）。
- 若要在主分区 8 卡上并行两套四卡流水线，必须先实现**可配置物理 GPU 映射**并配套测试，同时隔离 ports、socket、output_root 与服务 registry；仅在父进程设 `CUDA_VISIBLE_DEVICES=4,5,6,7` 不可靠，因为服务/训练子进程会自行重写。`conditional_execution.verified=false`。
- `start_5round_600.sh` 默认 `--detach`；在 ACP 容器中应让控制器**前台**运行，否则脚本返回后作业可能结束并清理进程。
- 每个 backbone 用新 run dir；resume 用相同命令 + 相同 run dir，先检查 active state、回执与 running jobs，不重复 bootstrap、不覆盖历史 attempts。
- 提交纪律：主分区只提交合规 8 卡及倍数任务；提交前短时 probe；不用 `sleep inf`；多次提交至少间隔 5 秒；结束或失败后确认资源释放。
- 仍待用户决策的 blocking decisions：原始 3000/744 source manifests 可得性、精确复现 vs 同协议重构、GPU 执行 profile 与映射改动、安全 API 注入与预算、长跑与输出存储审批。

## 6. 新内容如何被追踪

### 6.1 Git 分支与 commit

- 分支 `reproduction/4b-bwrap`，独立 worktree `.worktrees/bwrap-isolation`，上游基线 `496d3cf`。**只本地 commit，未 push。**
- 关键 commit 链（新→旧）：

```text
6c2049b docs: archive isolation review ledger        <- 写作时 HEAD（docs-only）
68cb220 docs: refresh current cpolar service         （docs-only）
d706d32 docs: close independent isolation review     （docs-only）
861debd docs: record final A100 isolation gates      （发布/审计/生成物 + 网站契约测试，10 文件；无生产代码、无上游测试）
313b6bc fix: harden production startup and sandbox trust  <- 最后的生产代码/上游测试变更（23 文件，19 新测试）
32ede47 fix: reject unknown validation sandbox backends
96714ed docs: publish validated bubblewrap isolation evidence（13 文件；subject 是 docs 但实际改了 rsiflow_validation.py 与上游回归测试，非 docs-only）
3a51a76 fix: isolate smoke meta storage per run
9cb6188 fix: scope task6 smoke around vendor shell limitation
bf4befd fix: diagnose persistent Codex smoke failures
e9f56cb test: verify persistent Codex tools and resume in bubblewrap
a28e440 fix: harden persistent Codex sandbox lifecycle
aa912b0 feat: isolate persistent Codex with credential-free relay
33ba0bd fix: support native benchmarks on shared filesystems
```

- 每次 commit 都只包含意图内文件；受保护的 `reproduction/audit/operations.jsonl` 运行时改动与未跟踪 `Meta_logs/` 始终不 staged、不 reset、不删除。commit 操作本身用 `record_operation.py` 入账（如 Task 7 `1174`、fail-closed 修复 `1188`、最终修复 `1219`、证据发布 `1253`）。
- 本文 `reproduction/RETROSPECTIVE.md` 按任务约束**未 commit**；后续由 controller 决定纳入哪个 docs commit。

### 6.2 JSONL 审计

- **单一审计根在主 checkout**：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit`。**核对、对账与快照生成一律以主 checkout 为准。**
- **worktree 账本不是主账本的"子集"，`operations.jsonl` 已经分叉**（写作时逐行比对结果）：
  - `operations.jsonl`：worktree 521 行 / 主 checkout 1151 行，**只有前 519 行逐行完全相同**。第 520、521 行的 sequence 同为 `581`、`582`，但语义完全不同——worktree 是 `sha256sum` / phase `task3-sdd-task2-r2-hashes` 与 `bash` / phase `task3-sdd-task2-review-package`；主账本同号是 `env` / phase `task1-step2-red` 与 `env` / phase `task1-step2-red-rsiflow-python`。
  - **相同 sequence ≠ 相同操作**。这是两条独立演化的账本，必须**逐条语义对账**：按 `sequence + actor + phase + action + cwd + git_commit + evidence_sha256` 比对，确认每条记录属于哪条工作线；不得按行数、前缀或同号假设包含关系，不得把两处同号记录合并计数，也不得用 worktree 账本推断主账本内容。
  - `key_results.jsonl` 才是真正的前缀关系：worktree 47 行与主 checkout 101 行的**前 47 行逐行相同**（严格前缀）。即便如此，引用关键结果仍应以主 checkout 为准。
  - 成因与教训：源码 commit 留在 worktree、审计记录写主 checkout 的单根 ruling 只约束了**新**记录；worktree 内被 git 跟踪的历史账本副本会随各自提交独立演化，因此"单根"不等于"两处一致"。任何跨工作树引用审计证据前，都要先做上述前缀/分叉判定。
- 所有 shell 验证经 `reproduction/scripts/audit_exec.py` 包装，带绝对 `--audit-root`、`--actor`、唯一 `--phase` 与 worktree `--cwd`；记录含 commit/cwd 与证据 SHA256。
- 新操作带 `evidence_sha256`；历史操作用独立 sidecar `reproduction/audit/evidence-hashes.json`，**不回写追加账本**。原始日志保留在 ignored 的 `reproduction/audit/evidence/`、`reproduction/logs/`，不批量公开。
- 关键结果账本 `key_results.jsonl` 保存阶段性结论（如 `00001210-final_fix_offline_gate`、`00001241/1243/1245` 三条终态区分）。
- 每日审计 `reproduction/audit/daily/2026-11-12.md` 含 2026-11-15 Task 7 与最终审阅追加节；`reproduction/audit/schema.md` 定义字段与核对规则。

### 6.3 报告哈希

- 每份真实报告/回执都用 `sha256sum` 重算并写入文档与审计：candidate `56b3fc6a…`、Harness log `808285dc…`、BFCL `ac659bf1…`、BFCL key hygiene `fbe9b638…`、combined Meta failed `4f123a27…`、Meta retry `a177657d…`、历史 Task 6 Meta `b1f1dbf3…`、EvalPlus smoke `537ed60c…` 与 official canonical/wrong `3125b42c…`/`f41bea31…`。
- 哈希 + job 终态 + worker 终态 + commit + 资源形状共同构成"可核验证据"；缺任一项都不能宣称通过。
- 数据侧用 `READY.json` 的 `config_hash`/`source_*_hash` 与 `cohort-release-v4.json`、`data-audit-v4.json` 对账；注意哈希语义（fingerprint / manifest 内部字段，而非文件字节）。

### 6.4 网页与公开交付

- `web/data/reproduction.json`：阶段状态、`remaining_experiment_gates`、`not_run`、`blocking_decisions`、`bwrap_isolation.final_a100_gates`（combined FAILED + 三阶段 exit 0 + Meta retry SUCCEEDED + `root_mapping=false` + EvalPlus 未重跑）。
- `web/data/process.json`：由 `python3 reproduction/scripts/build_status.py --audit-root <主 checkout audit> --output web/data/process.json` 生成的**静态快照**，带 `generated_at`、`as_of_sequence`、`is_snapshot`、`snapshot_note`；当前快照 1140 operations / 101 results / sequence 1257，之后有新操作需重新生成。
- `web/tests/test_web.py` 11 项契约把公开口径钉死：297/156 计数、combined 整体 FAILED 与三阶段 exit 0、Meta retry SUCCEEDED/worker Succeeded、`root_mapping=false`、生产 0644/owner 不变量、EvalPlus 未在 `313b6bc` 重跑、host OpenAI 与另行批准的 ACE 用户模拟凭据区分、secret scan 与 raw-log/client-metadata 不公开。
- **历史发布入口与历史 PID（写作时已不可用，不是当前入口）**：公网 `https://5340b001.r36.cpolar.top` 及其 `/process.html`、本地 `http://127.0.0.1:18000/`，以及 HTTP PID `2157843`、cpolar PID `2000586`，全部只是 **2026-11-15 交付当时**的入口与进程号。
  - 写作时只读核对：`ps -p 2157843` 与 `ps -p 2000586` 均**进程不存在**；`curl -m 4 http://127.0.0.1:18000/` 返回 **`http_code=000`（不可达）**。因此本文不得把它们写成当前可用服务，`web/RESULTS.md`/`web/OPERATIONS.md` 中的同名 URL/PID 也应读作历史交付记录。
  - cpolar 临时地址本身会变化，即使进程恢复，旧 URL 也不保证有效；**当前无可用公开入口**。
  - 恢复条件：合并后由**主工作树**重新拉起持久静态 HTTP 与 cpolar（`disable_timeout=true`，避免默认 600s 超时静默终止），重新实测本地与公网各资源返回 200 并做首页关键内容检查，再把**新的 URL、PID、实测时间与结果**写入 `web/RESULTS.md`、`web/OPERATIONS.md` 与审计账本；在此之前任何"发布可用"的表述都不成立。
  - 原始日志始终位于公开根目录之外的 `web-runtime/logs/`，不随恢复流程进入 `web/`。
- 维护命令：`python3 -m unittest discover -s web/tests -v`、`node --check web/app.js`、`python3 -m http.server <port> --bind 127.0.0.1 --directory web`；公开前跑密钥扫描，不链入私有轨迹、数据集、凭据或大体积 checkpoint。

## 7. 附录：本文写作时的只读核对

```bash
# HEAD 与提交性质确认（使用临时 GIT_CONFIG_GLOBAL，未改动用户 git 配置）
git rev-parse HEAD                       # 6c2049b002ad6c4214af34e8ecfb0331cb03adeb
git show --stat --oneline 6c2049b        # 6 文件，均为 reviews 归档 + web/RESULTS.md
git show --stat --oneline d706d32 68cb220
git show --stat --oneline 861debd        # 10 文件：docs/audit/web JSON + web/tests/test_web.py，无生产代码与上游测试
git show --stat --oneline 96714ed        # 13 文件：含 rsiflow_validation.py 与上游回归测试，故非 docs-only

# 审计根位置、前缀/分叉判定（不能只比行数）
wc -l reproduction/audit/operations.jsonl reproduction/audit/key_results.jsonl                 # worktree: 521 / 47
wc -l /data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit/operations.jsonl \
      /data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit/key_results.jsonl        # 主 checkout: 1151 / 101
python3 -B - <<'PY'
import json
from pathlib import Path
wt = Path("reproduction/audit")
main = Path("/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit")
for name in ("operations.jsonl", "key_results.jsonl"):
    a = [l for l in (wt / name).read_text(encoding="utf-8").splitlines() if l.strip()]
    b = [l for l in (main / name).read_text(encoding="utf-8").splitlines() if l.strip()]
    n = 0
    for x, y in zip(a, b):
        if x == y:
            n += 1
        else:
            break
    print(name, "worktree", len(a), "main", len(b), "identical_prefix", n, "strict_prefix", n == len(a))
    for i in range(n, min(len(a), n + 2)):
        ra, rb = json.loads(a[i]), json.loads(b[i])
        print("  DIVERGE line", i + 1, "seq", ra.get("sequence"),
              "worktree:", ra.get("action"), ra.get("phase"),
              "| main:", rb.get("action"), rb.get("phase"))
PY
# 实测结果：operations identical_prefix=519、strict_prefix=False，第 520/521 行同为 seq 581/582 但 action/phase 不同；
#           key_results identical_prefix=47、strict_prefix=True。

# 历史发布入口是否仍存活（写作时实测：两个 PID 均不存在，本地端口不可达）
ps -o pid=,stat=,cmd= -p 2157843 || echo "PID 2157843 not present"
ps -o pid=,stat=,cmd= -p 2000586 || echo "PID 2000586 not present"
curl -s -o /dev/null -m 4 -w "http_code=%{http_code}\n" http://127.0.0.1:18000/   # http_code=000

# 公开快照口径
python3 -B -c 'import json;p=json.load(open("web/data/process.json"));print(p["generated_at"],p["as_of_sequence"],p["operations_total"],p["results_total"],p["operation_counts"])'

# v4 数据证据在主 checkout（worktree 中数据目录被 gitignore，不存在）
ls /data/wuxuecheng/Hierarchical_level/RSI-Flow/RSIFlow/runtime/data/rounds_3000_5x600_v4/READY.json
```

上述核对只读取文件与 git 元数据；未运行测试、未提交作业、未修改除本文件以外的任何内容。
