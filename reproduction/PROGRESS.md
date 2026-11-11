# RSI-Flow 4B 复现进度报告

# SDD ledger — plan: 用户已确认的 4B 主分区八卡顺序执行计划；无独立 plan 文件，以本文件和 audit/schema.md 为准

## 当前执行边界

- 只做 Qwen3-4B；主分区 8×A800 顺序执行，当前仅用 GPU 0–3。
- 公开数据同协议重构，不是作者原始 3000/744 队列的精确复现。
- Meta 使用自定义 `gpt-6-astra`；ACE 50 跳过并标记 pending，计划评分 694/744。
- 4B 审阅后才决定是否准备和运行 8B；尚未运行五轮演化，尚无模型 benchmark 分数。

## 已完成并验证

- 本地复现分支 `reproduction/4b-main8`、阶段 commit、追加式操作/关键结果账本、每日审计和 Herdr 三方通道。
- Python 3.11 生产与离线环境、依赖 lock、Qwen3-4B 13 文件/3 分片/hash、Codex 0.153.4 provenance hash。
- 冻结 release `rounds_3000_5x600_v4`：3000 个训练任务（五轮各 600），744 个验证任务；Code 每轮 120 STDIO + 80 function。真实 release 与机器配置测试 4/4 通过，没有依赖上游占位配置中的 skip。
- v4 READY 自带 `evaluation_scope`：744 frozen、694 scored、50 pending；config hash `22305bf10342e11f76aab19df033ce6fedacee033ef4acbd043249b5c3caa231`。
- `reproduction/audit/cohort-release-v4.json` 连接 source build、READY 和逐源对账；保留 `clean_independence_audited=false`、`exposure_unknown` 和两次隔离重建历史。
- 只读泄漏审计：Code train/dev overlap=0、final exact matches=0；EnvScaler train/heldout 共享 191 个环境定义，上游总状态 `failed_overlap`。该复用是本次重构的明确保留差异，不能宣称完整独立性审计通过。
- 检索索引、`RoundStore`、`select_specs()`、ACE 多轮记录与 record offsets/hash 已验证。
- 四类 official spec 已从 v4 重新生成：164/178/100/100；HotpotQA 与 2Wiki 官方 v1 单条金答案 smoke 均按 100 固定分母得到 EM=F1=0.01，只证明评测链路，不是模型分数。2Wiki 保持上游 v1，不擅自改用需要 aliases 的 v1.1。
- skip-ACE 新 694/744 与旧 250/300 兼容修复，自定义 provider 配置覆盖，过程页已发布。
- Qwen 独立审阅 `preflight-qwen-r1.md` 的 CHANGES 已处理：v4 证据链、694/744 公开口径、真实 release 测试、操作证据 SHA256、历史 sidecar、快照 sequence 与数据保留意见。
- `pt-ox3o5he2` 四卡真实模型 smoke 已完成：四个 BF16 A800 服务、checkpoint/weights 绑定、`RSIFLOW_SMOKE_OK` 短生成（22 prompt + 8 completion tokens）、服务停止与显存释放。它是诊断，不是 benchmark 分数。

## 当前等待与阻断

- **完整实验当前硬阻断于 Landlock**：`pt-texqw4a8` 在目标 `Linux-4.18.0-425.3.1.el8` 上明确返回 errno 38、`landlock_available=false`，在模型加载前失败。不能用无隔离回退继续五轮。
- 本机与目标 SCO 均不支持 Landlock；上游最新完整测试为 167 passed、2 skipped、1 failed。目标镜像还没有 bwrap：`pt-if57mo0b` 明确为 `bwrap: command not found`、exit 127，未验证任何替代隔离。
- 四卡模型诊断已完成，但不能替代 Landlock、Meta 和五类 benchmark 门槛。
- `assets/private/credentials.sh` 尚不存在，真实 Meta `/models`、Responses/Chat 和 Codex native tools 尚未验证。
- BFCL、HumanEval+、MBPP+ 最小真实 benchmark smoke 尚未全部完成。
- 尚未运行五轮实验、最终 694 评估或 8B；没有模型输出分数。

## 过程与证据

- cpolar 过程页：https://4a63b603.r36.cpolar.top/process.html
- 数据 READY：`RSIFlow/runtime/data/rounds_3000_5x600_v4/READY.json`
- 操作账本：`reproduction/audit/operations.jsonl`
- 历史证据 hash：`reproduction/audit/evidence-hashes.json`；新操作另带 `evidence_sha256`。
- 关键结果：`reproduction/audit/key_results.jsonl`
- 每日记录：`reproduction/audit/daily/2026-11-11.md`

Task 2R: Ruling: 2Wiki 保持上游 `official_2wiki_original_v1`，不为了使用 aliases 改协议 — 上游 spec 生成器和当前 HF 数据均无 answer_id/evidences_id — 若判断错误会偏离作者评测协议。
Task 2R: Ruling: 历史 operations 用独立 SHA256 sidecar，新操作增加 evidence_sha256，不回写追加账本 — 保留审计历史并提高可核验性 — 原始日志仍需安全保留，sidecar 不能恢复被删除内容。
Task 2R: Ruling: 原始日志不批量公开或强制纳入 git — 可能含敏感路径/输出，公开页只展示脱敏快照 — 证据持久性依赖本地文件与 hash 清单。

后续必须先完成 SCO Landlock/GPU probe、provider 私有凭据 smoke 和五类 benchmark smoke，不会直接启动五轮实验。
