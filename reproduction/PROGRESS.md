# RSI-Flow 4B 复现进度报告

# SDD ledger — plan: 用户已确认的 4B 主分区八卡顺序执行计划；无独立 plan 文件，以本文件和 audit/schema.md 为准

## 当前执行边界

- 只做 Qwen3-4B；正式实验规划仍是主分区 8×A800 顺序执行、当前仅用 GPU 0–3。Task 6 的隔离 smoke 例外使用用户后来授权的 8×A100，不改变正式实验资源方案。
- 公开数据同协议重构，不是作者原始 3000/744 队列的精确复现。
- Meta 使用自定义 `gpt-6-astra`；ACE 50 跳过并标记 pending，计划评分 694/744。
- 4B 审阅后才决定是否准备和运行 8B；固定 16 条 DeepCoder 单域五轮及其中 B2 positive-only SFT 已完成，但正式五域五轮、最终 694/744 评估和模型 benchmark 评分仍未运行。
- 当前交付分支为 `reproduction/4b-main8`：隔离分支 `reproduction/4b-bwrap` 已 fast-forward 合入。**合并与初次备份点为 `3a66b655b764224acb21a108e59e7066f8503867`**（该点的复盘/审查提交为 `3a66b65`），初次备份推送当时 `ls-remote` 两个远端复现分支同为该 SHA。两个复现分支已备份到内部 GitLab（`gitlab` remote，项目 https://gitlab.acerobotics.com/wuxuecheng/rsiflow ）；`origin` 仍是 GitHub 上游且未向其推送，GitLab 既有 `main`（`0c47d98df09327ba09f4fad9aaa9914837ffa280`）未强推/未覆盖/未建 MR，GitLab CI 未验证。承载发布文档更新的提交在合并与初次备份点之后由主 agent 纳入后续文档提交，**当前分支 HEAD 与远端 SHA 以 `git log` / `git ls-remote` 实测为准，本页不写死后续 SHA**。本页只发布脱敏状态，不公开 raw prompts/responses、私有轨迹、凭据或大日志。

## 已完成并验证

- **DeepCoder function 16 真实 canary（2026-11-17）**：固定 16 条 function、code 单域、单轮、一个 HARNESS 候选已完成。Astra 选择 HARNESS，同一 candidate 在 2/3 次检查后 passed；父代 51、候选 93 次真实 Qwen calls，父 7/候选 8 个有效 submission 进入 full verifier。完整同批配对为 **3/16=0.1875 → 4/16=0.25，delta +0.0625**，并有 4 个新成功和 3 个回归；原 3 个成功全部回归，4 个新成功全部来自父代零调用失败，3 个回归全为候选零调用失败，因此只接受固定批净正增益，不宣称普遍 Harness 因果改进。accept/activate、skills/context/snapshot、state COMPLETE、jobs 调和和四卡 cleanup 均完成；该单轮 run 的 MODEL 未选择、SFT 未运行。独立结果审阅为 Spec PASS / Quality DONE_WITH_CONCERNS，详见 `reproduction/audit/reviews/deepcoder-canary-real-results.md`。522 passed/2 skipped 是该历史阶段计数；最终修复后完整套件为 **1031 passed, 2 skipped in 723.43s**，详见 `reproduction/audit/reviews/deepcoder-five-round-final-verification.md`。
- **DeepCoder function 16 单域五轮（2026-11-19）**：同一 16 条、80 rollout positions 的 A0+B1–B5 已完成，`run_complete=true`、launcher exit 0、external cleanup ok。B1/B3/B4 ARTIFACTS 均为 2/16→3/16，B5 为 4/16→5/16，均是零新增 Qwen calls 的 Meta-assisted direct-submission output repair；B2 MODEL 为 4/16→3/16，真实 positive-only SFT 20 samples、4 ranks、1 epoch、3 optimizer steps，retain_parent 且未激活。最终 active 为 base Qwen3-4B + 四个累计 artifacts。每侧仅 6–8 个有效 submissions 进入 full verifier；ENOENT/EAGAIN 与零调用重叠限制归因。四卡最终 cleanup 时空闲；自动 occupier restore 当时因 NVML PID ownership 未解决而 pending。随后经用户明确授权，在四卡空闲复核后直接以 `tmux` 启动，当前四 rank 持续运行、每卡日志约 2519 MB。正式五域、3000、694/744、独立 benchmark 和作者复现仍未运行。详见 `reproduction/audit/reviews/deepcoder-five-round-real-results.md`。
- **学习专题（2026-11-22）**：新增 `reproduction/STUDY_GUIDE.md`，提供分层学习路线、代码阅读顺序、真实五轮案例、证据核验和离线练习；根 README、`LEARNING.md`、AGENTS 与公开结果页已链接，旧“五轮未运行”当前表述已修正。
- 历史复现分支 `reproduction/4b-main8` 的阶段 commit、追加式操作/关键结果账本、每日审计和 Herdr 三方通道已有记录；bwrap 隔离工作已 fast-forward 合入该分支，并完成内部 GitLab 备份。
- 合并复验（2026-11-16）：功能工作树预检 `297 passed, 2 skipped in 92.22s`、website `11/11`。**合并后主工作树首次完整 pytest 为 `13 failed, 284 passed, 2 skipped`**，逐项确认根因是主工作树缺少 Git-ignored 的 `assets/isolation/`，不是代码或测试回归。用既有 `reproduction/scripts/install_bwrap.py` 从功能工作树同一 pinned binary（SHA256 `d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca`）安装并校验 manifest/launcher receipt 后，受影响测试 `51 passed in 48.77s`、完整 pytest **`297 passed, 2 skipped in 89.15s`**，website `11/11`、`node --check`、`git diff --check` 通过；全程未改生产代码或测试。
- 账本保护：主工作树两份未提交追加 JSONL 在 merge 前后 SHA256 完全不变（`operations.jsonl` `ea9403380b77b5f1100c6bce5b66578cc3cf4e218ec9985bea8d2b7f0749858e`、`key_results.jsonl` `82c10d582e9459d62d03c3b42c6ba9efaabdfd1254114519389259fd1ba92c0a`）。两个工作树的 operations 在 **sequence 581 之后分叉**，原件均保留，禁止覆盖或直接拼接；bwrap 功能工作树与 `Meta_logs/` 一并保留。
- 公网恢复：HTTP 与 cpolar 从主工作树 `web/` 重新拉起，公网 10 个资源全部 200，UTF-8 JSON 与 `RESULTS.md` 关键事实检查通过；旧 URL/PID 只作历史留档。
- Python 3.11 生产与离线环境、依赖 lock、Qwen3-4B 13 文件/3 分片/hash、Codex 0.153.4 provenance hash 已核验。
- 冻结 release `rounds_3000_5x600_v4`：3000 个训练任务（五轮各 600），744 个验证任务；Code 每轮 120 STDIO + 80 function。真实 release 与机器配置测试 4/4 通过，没有依赖上游占位配置中的 skip。v4 READY 记录 744 frozen、694 scored、50 pending，config hash `22305bf10342e11f76aab19df033ce6fedacee033ef4acbd043249b5c3caa231`。
- `reproduction/audit/cohort-release-v4.json` 连接 source build、READY 和逐源对账；保留 `clean_independence_audited=false`、`exposure_unknown` 和两次隔离重建历史。
- 只读泄漏审计：Code train/dev overlap=0、final exact matches=0；EnvScaler train/heldout 共享 191 个环境定义，上游总状态 `failed_overlap`。该复用是明确保留差异，不能宣称完整独立性审计通过。
- 检索索引、`RoundStore`、`select_specs()`、ACE 多轮记录与 record offsets/hash 已验证。
- 四类 official spec 已从 v4 重新生成：164/178/100/100；HotpotQA 与 2Wiki 官方 v1 单条金答案 smoke 均按 100 固定分母得到 EM=F1=0.01，只证明评测链路，不是模型分数。2Wiki 保持上游 v1，不擅自改用需要 aliases 的 v1.1。
- skip-ACE 新 694/744 与旧 250/300 兼容修复、自定义 provider 配置覆盖和过程页已验证。
- Qwen 独立审阅 `preflight-qwen-r1.md` 的 CHANGES 已处理：v4 证据链、694/744 公开口径、真实 release 测试、操作证据 SHA256、历史 sidecar、快照 sequence 与数据保留意见。
- `pt-ox3o5he2` 四卡真实模型 smoke 完成四个 BF16 A800 服务、checkpoint/weights 绑定、短生成和资源释放；它是诊断，不是 benchmark 分数。
- 旧 Landlock 环境阻断已由显式 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1` 路径替代；没有 host fallback、Landlock fallback 或 `danger-full-access`。真实 Harness 回归先复现 errno 38，再在 bwrap 下通过；最终修复后的完整上游测试为 **297 passed, 2 skipped**，综合聚焦回归为 **156 passed**。
- 最终修复已在生产路径验证：fresh `prepare_codex_home()` 原子创建并校验 0644 `installation_id`；same-basename 不同 fresh run 以 `.run-owner.json` fail closed，same-path 与可证明 legacy resume 保持；candidate/native/Harness 默认 backend 强制使用 ignored installer receipt 中的 manifest/launcher hash，tamper 在 spawn 前失败。
- `313b6bc` combined `pt-v6m10c4t`（8xA100/root_mapping=false）整体/worker **FAILED** 仅因 Meta 外部 provider completion failure；candidate/Harness/BFCL exit 均 0。candidate report SHA256 `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`，Harness `1 passed in 23.26s`（log SHA256 `808285dc5c05709785e4d669e28882e5c496644bb1191a6301866325b2735f31`），BFCL receipt SHA256 `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4`、key hygiene SHA256 `fbe9b6386dd64f78e5eb72b7bbad9f88f2c39e218b93bd6ab049a0b97b20749d`。
- combined Meta 在完整 provider response 前失败：ordinal 0 HTTP 502，retry HTTP 200 incomplete SSE；failed report SHA256 `4f123a27dc415e2b99d63569fee30af2fce68bf3fda68da3653ee7e9e1db3d9c`。这是外部 relay/provider completion failure，未改代码掩盖。
- 唯一 Meta-only retry `pt-x6b4ista`（8xA100/root_mapping=false）SUCCEEDED/worker Succeeded/exit 0；report `reproduction/logs/final-meta-a100-20261115T043755Z/run/report.json` SHA256 `a177657dbf1e744f2d2be64d16c33bcda08102052a7ecd7486aebdd48cf64b66`。ok/completed、same thread、无重复 job/bootstrap、exact gpt-6-astra、3 dynamic calls、11 key hygiene + 7 cleanup、durable job 0；production installation_id=0644 且 run-owner 精确匹配。当前最终 Meta gate 以此次为准，Task 6 外部 actor 证据仅属历史。EvalPlus 仍为历史 one-task smoke，未在 313b6bc 重跑。
- Candidate SCO `pt-gyfupguq` SUCCEEDED（worker Succeeded）：7 项 functional、18 项 negative、5 项 timeout、3 项 session 检查全部通过，report SHA256 `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`。
- EvalPlus SCO `pt-bfkp5k0d` SUCCEEDED（worker Succeeded）：一个 HumanEval+ 任务的 canonical Base/Plus pass、wrong Base/Plus fail，两者均为 bwrap 且无 infrastructure error。smoke report SHA256 `537ed60c8ed385ecab81197fb218a6e8117bfc3a0871d5e2445779daf524bfc0`；official canonical/wrong SHA256 分别为 `3125b42c1d1f8ef7ba4a6a4ee263ab653485c11a0b8e087327a9987ea8a2ffa2` / `f41bea3178f193a2228b196fe3709fe8f1aa6321302ff04c0ef29a3bf2427514`。这不是 HumanEval+ 分数。
- BFCL SCO `pt-fdvsxqax` SUCCEEDED（worker Succeeded）：pinned source `ea13468e4423454d0c213704fb87cf7cb3990433`，官方 `check_native` 完成恰好两次受控 CPU RPC，key hygiene 通过。`check.json` SHA256 `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4`。这不是 BFCL 分数。
- Persistent Meta SCO `pt-zj1l9q1t` SUCCEEDED at 2026-11-14T17:33:42Z（worker Succeeded）：同一 thread、无重复 job/bootstrap、三次 dynamic tools、11 项 key hygiene、7 项 cleanup、durable job exit 0，provider/model 保持 `OpenAI/gpt-6-astra`。report SHA256 `b1f1dbf35c09ee192c2199028ee984ba7068889bdf3f126cd52965732220f920`。

## Task 6 证据的适用边界

- 上述正式接受的 Meta smoke 由外部 actor `huangsiyuan1` 提交，不是 `main-kimi`；实际运行在 **8×A100**，使用 N3lS+N2lS candidate specs 且 `root_mapping=true`。用户在 A800 启动失败后授权接受 A100 证据。
- A800 r5/r6 `pt-7i4qertj` / `pt-44m6zkmx` 均在启动命令执行前 FAILED；没有 smoke report。不得宣称 A800 Meta smoke 通过，也不得把外部提交记到本任务 audit actor 名下。
- Codex native-shell descendant hygiene **未执行**。旧 Landlock 不可用，nested vendor bwrap 与已审阅的 anti-escape seccomp 冲突；批准的替代门槛是 `shell_tool=false` 下的 construction-level hygiene。不得宣称 native-shell 验证通过，也不得为此放宽 seccomp。

## 当前剩余门槛

- Task 7 最终独立安全审阅及 consolidated fix-wave re-review 已由未参与实现的 reviewer 关闭；全部 Critical/Important 已修复，结论为 Ready to merge Yes。保留一个仅影响非 UTF-8 installation_id 报错类型的 Minor 诊断项，不影响启动前拒绝和状态安全。
- 正式五域五轮 preflight 仍需五类 benchmark 最小 smoke、逻辑 GPU 0–3 上健康的四卡 Qwen 服务、当前 694 配置 `python RSIFlow_4B/launch.py --check`，以及最终独立 experiment preflight review；已完成的固定 16 条单域五轮不替代这些门。
- 正式五域五轮、最终 694/744、ACE 50、8B 和持久正式 Qwen 服务均未运行；没有独立模型 benchmark 分数。单域 B2 SFT 已发生但未激活，不能写成模型质量结果。

## 过程与证据

- Candidate report：`reproduction/logs/candidate_bwrap_smoke_sco_r3.json`
- EvalPlus report：`reproduction/logs/evalplus-bwrap-smoke/20261113T035651Z/smoke_report.json`
- BFCL receipt：`reproduction/logs/bfcl-bwrap-check-r2/check.json`
- Meta report：`reproduction/logs/meta-bwrap-task6-r4-20261114T173158Z/run/report.json`
- cpolar 过程页：https://webshow.vip.cpolar.top/rsiflow/process.html （公网首页 https://webshow.vip.cpolar.top/rsiflow/ ；本地 http://127.0.0.1:18000/ ，由主工作树 `web/` 提供，并通过共享 webshow 静态站点的 `rsiflow` 挂载发布。历史随机入口及旧 PID 已停止，仅留档）
- 交付复盘：`reproduction/RETROSPECTIVE.md`（阶段历程与 ruling、真实证据、失败根因、可复用经验、五轮前置门、追踪方式；经独立 Kimi 审阅 CHANGES→修复→scoped re-review 全 ADDRESSED，Spec/质量 PASS）
- 数据 READY：`RSIFlow/runtime/data/rounds_3000_5x600_v4/READY.json`
- 操作账本：`reproduction/audit/operations.jsonl`；关键结果：`reproduction/audit/key_results.jsonl`
- 历史证据 hash：`reproduction/audit/evidence-hashes.json`；新操作另带 `evidence_sha256`。
- 每日记录：`reproduction/audit/daily/2026-11-12.md`（含 2026-11-15 Task 7 追加节）、`reproduction/audit/daily/2026-11-16.md`（合并、GitLab 备份、合并后测试失败与恢复、公网实测）
- 交付审阅：`reproduction/audit/reviews/delivery-integration-kimi.md`、`delivery-gitlab-codex.md`

Task 2R: Ruling: 2Wiki 保持上游 `official_2wiki_original_v1`，不为了使用 aliases 改协议 — 上游 spec 生成器和当前 HF 数据均无 answer_id/evidences_id — 若判断错误会偏离作者评测协议。
Task 2R: Ruling: 历史 operations 用独立 SHA256 sidecar，新操作增加 evidence_sha256，不回写追加账本 — 保留审计历史并提高可核验性 — 原始日志仍需安全保留，sidecar 不能恢复被删除内容。
Task 2R: Ruling: 原始日志不批量公开或强制纳入 git — 可能含敏感路径/输出，公开页只展示脱敏快照 — 证据持久性依赖本地文件与 hash 清单。

后续只能在上述 preflight 和独立审阅全部关闭后申请五轮实验；不得以基础设施 smoke 替代模型质量或 benchmark 结果。
