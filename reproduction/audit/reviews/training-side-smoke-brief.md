# 训练侧最小真实 smoke 实施任务（后续派发）

工作树：`.worktrees/preflight`。先读 AGENTS 和 `reproduction/audit/reviews/training-side-smoke-design.md`，按推荐五条 mini-B1 方案实现。用户已授权自主 preflight，但本任务实现阶段不运行真实 GPU/SCO。

允许新增/修改：
- 新建 `reproduction/scripts/training_side_smoke.py`
- 新建现有测试体系下 `RSIFlow/tests/test_training_side_smoke.py`
- `reproduction/REPRODUCTION.md`
- 新建 `reproduction/audit/reviews/training-side-smoke-report.md`
不得改生产 TaskAdapter/pipeline/trainer/gpu phases、正式配置或 frozen assets。

要求：
1. 子命令 plan / execute-rollout / execute-sft / execute-recovery / status；plan 创建全新 ignored output root，无模型调用。每阶段不可变 receipt、stage lock、代码/config/hash 绑定；部分 started 无 final 时 requires_audit，不静默重跑。
2. 从真实 B1 原顺序固定选 EnvScaler、DeepCoder/TACO STDIO、DeepCoder/TACO function、HotpotQA、2Wiki 各1，共5个唯一 task；创建 mini release 并验证原 record bytes/offset/hash、tasks/search sqlite、原资产不变。
3. 派生 smoke-only launcher：1轮、5 tasks、source quotas 1/2/1/1、独立 data/output/run roots；保留真实 checkpoint、SFT preset、one epoch、四 rank、seed42。复用 TaskAdapter.bootstrap/rollout/sft，不调用 pipeline.run/Meta。
4. rollout 要5条真实评分/trajectory/local calls，模型答错可 completed_with_model_failures；无自然正例时 SFT blocked_no_positive_parent_trajectory，禁止换题/造标签/借held-out。
5. SFT 只取本次 parent 正例，四 rank、merge 新 child checkpoint 于 output root，base manifest不变，weights改变，child四服务reload+短生成；不写正式active Task/候选接受。
6. recovery 不重复训练，验证训练receipt hashes不变，重启同一child服务、短生成、清理。所有阶段finally只停owned registry，GPU0-3释放>=40000MiB；cleanup失败总结果失败。
7. 明确 smoke_only/formal_round_completed=false/eligible_for_meta=false/eligible_for_candidate_acceptance=false，不写正式round complete或694标记。
8. 严格TDD，按设计第7节覆盖选择、mini release、有效配置、阶段门、零正例、错误答案vs infra、SFT证据、防篡改、清理、恢复不重复训练、plan无副作用。运行focused/相关测试和实际资产plan，主 agent负责全量。

不 commit/push、不读取凭据、不启动真实模型/API/SCO/五轮。报告RED/GREEN、plan回执、测试和自审。
