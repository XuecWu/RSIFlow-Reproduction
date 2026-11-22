# DeepCoder 五轮 Task 7 最终修复波次 scoped re-review

- 日期：2026-11-19（UTC）
- 基线：`5071b705273975c5ee2657ed8c21595fd84e8465`
- 输入：`task-7-final-review-fix-brief.md`、`task-7-final-review-fix-report.md`、884 行 `task-7-final-review-fix-review.diff`
- 范围：仅复审前次三个 Important 区域，以及 cleanup 后用户授权 tmux 直接占卡的表述；不重开全分支或其他 deferred 范围。

## 总结 verdict

**Ready: Yes。** 三个原 Important 区域均为 **ADDRESSED**。此 fix diff 新引入 **Critical=0、Important=0**。

该 Ready 仅表示本次 scoped 修复波次可以进入主 agent 的最终交付检查，不冒充全量套件、网站/静态/secret/HTTP 检查或 merge/public 已完成；这些仍按 brief 由主 agent 执行。

## 1. MODEL completed receipt 与 canonical root — ADDRESSED

`reproduction/scripts/deepcoder_model_smoke.py:580-639` 新增完整的执行根和完成阶段绑定：

- execute root 在解析前拒绝最终 symlink，并要求 strict resolve 后等于原 canonical absolute path、位于项目内且为真实目录。
- `_validate_completed_stage()` 要求 completed 状态、精确 stage、兼容的 operation、当前 `plan_identity`；SFT 的 candidate state、metrics、GPU phase 路径与 hash 均重新绑定，recovery child 必须等于已绑定 SFT child。
- `_bound_stage_output()` 同时拒绝绝对路径外、非 canonical、含 symlink alias、越出 stage/root 或不符合预期目的地的输出。

`reproduction/scripts/deepcoder_model_smoke.py:813-856` 在读取 plan 前和 stage lock 内各重验一次 root，然后才允许 `already_completed` 或 recovery。覆盖的正是前次 stale/foreign final receipt 与 post-plan root symlink swap，而不是仅比较已解析路径。

新增测试覆盖有效 resume/recovery、两个阶段的 stale plan、foreign root 和 post-plan symlink swap；实现者报告 MODEL 定向 6 passed、model-smoke 回归 66 passed，以及真实 v2 receipt rebinding control PASS。未重跑这些相同测试。实现保留真实 v2 receipt 无 `operation` 字段时的兼容路径，没有使已接受证据失效。

## 2. Selection 优先级与完整 activation inventory — ADDRESSED

`reproduction/scripts/summarize_deepcoder_five_round.py:488-499` 在存在生产字段时直接调用 `experiment_progress._selection_choice`，与 `RSIFlow/experiment_progress.py:192-205` 共用 `accept_or_retain` 优先级和字符串白名单。只有完全不存在 `accept_or_retain`/`selection` 时才使用 legacy decision-only fallback，避免新旧证据兼容分支重新引入优先级分叉。

`reproduction/scripts/summarize_deepcoder_five_round.py:1307-1336` 不再取第一份 round-matched activation：

- 收集该轮完整 activation inventory，并单独计算全部成功 activation。
- accept 要求成功数恰好为 1，且该回执的 candidate source、active destination、source/active hash 全部正确；错误 destination 和重复成功均拒绝。
- retain 遇到任何成功 activation 都拒绝；早期失败后的唯一后续成功仍可用于合法 accept。

新增 conflicting dual-field、earlier failure/later success、duplicate success 测试，并将 false-accept 与 retain-with-activation 隔离。实现者报告相关 6 passed、隔离 guard 9 passed，以及最终两个受影响测试文件 362 passed；未重跑。实现者另报告真实最终汇总断言仍为五轮完成、B2 retain 且无 activation，与代码语义一致。

## 3. 最终真实结果文档、网页和 Retry 5 措辞 — ADDRESSED

`reproduction/audit/reviews/deepcoder-five-round-real-results.md:1-80` 已提供计划要求的独立结果报告。逐轮 calls、full verifier、成功数、transitions、selection/activation 与接受 summary 一致；B2 的 20 samples、4 ranks、1 epoch、3 optimizer steps、loss `0.06101469943920771` 与真实 `training_metrics.json` 一致。报告明确最终 active 是 base Qwen3-4B + 四个累计 artifacts，B2 merged candidate 未激活，并保留 16 unique/80 positions、6–8 full-verifier submissions、ENOENT/EAGAIN/零调用归因限制及非作者/非五域/非 694/744 边界。

`AGENTS.md`、`reproduction/PROGRESS.md`、`reproduction/REPRODUCTION.md`、`web/RESULTS.md`、`web/OPERATIONS.md` 和 `web/data/reproduction.json:84-143` 已同步有界完成状态；`run_4b_five_rounds` 不再是 `not_run_pending_approval`，正式五域与独立评估仍保持未运行。`web/tests/test_web.py:145-158` 对状态、逐轮分数、B2 retain 和 SFT 样本数提供契约断言。基线 1020 passed/2 skipped 被明确标为 `5071b705` 的接受基线，不冒充本波次全量复验。

`reproduction/audit/reviews/deepcoder-model-smoke-report.md:219` 已改为 Retry 5 **not executed again**，同时保留 Retry 4 已 execute 并失败的历史，原措辞问题关闭。

## Cleanup 后 tmux 占卡表述核对 — 未篡改实验证据

- 最终 accepted summary SHA256 仍为 `3675d1fa07892afd879838d84bf253787322fa30058f7b12cfea3e4ed134d671`。
- 0008 external cleanup receipt SHA256 仍为 `2edb6da598746c0c2905af7bb901565669656b597c6d347ad41ce8f09c79c621`，与 `deepcoder-five-round-real-results.md:65` 一致；receipt 中自动 `restore_pending=true` 历史保持原样。
- `deepcoder-five-round-real-results.md:63-68` 与 `deepcoder-five-round-runbook.md:262-268` 明确区分：cleanup 时四卡空闲、自动恢复因 NVML PID ownership 未解决而 pending；随后用户明确授权，才以独立 tmux 会话直接启动。文本没有把直接启动写成自动 restore receipt，也没有反改 cleanup 的空卡观测。
- 只读查看 `/data/wuxuecheng/z-occupier-ready/runtime/dev4/tmux-occupier-20261119.log` 末尾，rank 0/1/2/3 均持续推进训练 step，脚本各报告约 2519 MB，与文档一致。
- scoped diff 不包含 accepted summary、cleanup receipt 或其他真实实验证据的修改。

## 审阅操作与剩余边界

本次回源读取了完整 scoped diff、当前生产实现、相关测试、最终文档、真实 B2 metrics、accepted summary/cleanup hash 和 tmux 日志。未重复实现者已报告的相同测试，未启动实验，未读取或修改 GPU lease，未修改工作树。唯一写入是本主审计报告。

前次列出的 numeric policy typing、v4/ToyModel 覆盖、额外 alias/artifact 测试、cross-mount `st_dev`、legacy cosmetics、zero-call label 和旧 review trailing whitespace 均为 nonblocking follow-up，本次未重开。主 agent 仍需完成最终全量套件、网站/JSON/静态/secret/HTTP 检查及主审计账本记录。
