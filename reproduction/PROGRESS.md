# RSI-Flow 4B 复现进度报告

# SDD ledger — plan: 用户已确认的 4B 主分区八卡顺序执行计划；无独立 plan 文件，以本文件和 audit/schema.md 为准

## 当前执行边界

- 只做 Qwen3-4B；正式实验规划仍是主分区 8×A800 顺序执行、当前仅用 GPU 0–3。Task 6 的隔离 smoke 例外使用用户后来授权的 8×A100，不改变正式实验资源方案。
- 公开数据同协议重构，不是作者原始 3000/744 队列的精确复现。
- Meta 使用自定义 `gpt-6-astra`；ACE 50 跳过并标记 pending，计划评分 694/744。
- 4B 审阅后才决定是否准备和运行 8B；尚未运行五轮演化、SFT、最终 694/744 评估或模型 benchmark 评分。
- 当前分支为 `reproduction/4b-bwrap`。本页只发布脱敏状态，不公开 raw prompts/responses、私有轨迹、凭据或大日志。

## 已完成并验证

- 历史复现分支 `reproduction/4b-main8` 的阶段 commit、追加式操作/关键结果账本、每日审计和 Herdr 三方通道已有记录；当前 bwrap 工作在其后续分支上。
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

- Task 7 的最终独立安全审阅由 controller 在本地 commit 后执行；当前不得把“作者自审”写成独立审阅关闭。
- 五轮 preflight 仍需五类 benchmark 最小 smoke、逻辑 GPU 0–3 上健康的四卡 Qwen 服务、当前 694 配置 `python RSIFlow_4B/launch.py --check`，以及最终独立 experiment preflight review。
- 五轮、SFT、最终 694/744、ACE 50、8B 和 Qwen 服务均未在本轮隔离任务中启动；没有模型 benchmark 分数。

## 过程与证据

- Candidate report：`reproduction/logs/candidate_bwrap_smoke_sco_r3.json`
- EvalPlus report：`reproduction/logs/evalplus-bwrap-smoke/20261113T035651Z/smoke_report.json`
- BFCL receipt：`reproduction/logs/bfcl-bwrap-check-r2/check.json`
- Meta report：`reproduction/logs/meta-bwrap-task6-r4-20261114T173158Z/run/report.json`
- cpolar 过程页：https://4a63b603.r36.cpolar.top/process.html
- 数据 READY：`RSIFlow/runtime/data/rounds_3000_5x600_v4/READY.json`
- 操作账本：`reproduction/audit/operations.jsonl`；关键结果：`reproduction/audit/key_results.jsonl`
- 历史证据 hash：`reproduction/audit/evidence-hashes.json`；新操作另带 `evidence_sha256`。
- 每日记录：`reproduction/audit/daily/2026-11-12.md`（含 2026-11-15 Task 7 追加节）

Task 2R: Ruling: 2Wiki 保持上游 `official_2wiki_original_v1`，不为了使用 aliases 改协议 — 上游 spec 生成器和当前 HF 数据均无 answer_id/evidences_id — 若判断错误会偏离作者评测协议。
Task 2R: Ruling: 历史 operations 用独立 SHA256 sidecar，新操作增加 evidence_sha256，不回写追加账本 — 保留审计历史并提高可核验性 — 原始日志仍需安全保留，sidecar 不能恢复被删除内容。
Task 2R: Ruling: 原始日志不批量公开或强制纳入 git — 可能含敏感路径/输出，公开页只展示脱敏快照 — 证据持久性依赖本地文件与 hash 清单。

后续只能在上述 preflight 和独立审阅全部关闭后申请五轮实验；不得以基础设施 smoke 替代模型质量或 benchmark 结果。
