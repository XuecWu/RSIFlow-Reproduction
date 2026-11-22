# 学习专题与五轮归档独立复核

- 日期：2026-11-22
- 范围：`9dd6b1d` 之后的学习专题、文档导航、审计证据、公开快照和 GitLab 交付准备
- 复核者：独立 agent-65（未参与本轮实现）
- 初始结论：Ready to merge **With fixes**

## 已确认优点

- 学习专题结构完整，代码/报告/脚本/JSON 路径存在，汇总器命令与实际 argparse 一致。
- 五轮任务数、逐轮决策、B2 未激活、最终 active 和 2/16→5/16 与接受的机器汇总一致。
- 归档 manifest 恰好覆盖 84 个纳入项和 7 个排除项，与 91 个原始未跟踪文件完全对账；逐项大小和 SHA256 匹配。
- 7 个排除项均含完整 provider response；脱敏摘要不含 instructions、output、response ID、prompt cache key、endpoint 或凭据。
- 过程页与主审计根重建结果一致，包含 1283 operations、145 results 和 operation 1445。
- 审计 JSONL 与既有 daily 均为追加；未发现有效私钥、`sk-` 凭据、symlink 或超过 50 MiB 文件。
- 独立复跑 website 12/12、JS/JSON/diff-check 通过。

## Important 与修复

### 1. 复盘仍把五轮和 SFT 写成当前未运行

- 原位置：`reproduction/RETROSPECTIVE.md:245-249`。
- 修复：明确后续固定 16 条单域五轮和 B2 SFT 已完成，B2 未激活；仅正式五域、独立评估、ACE、8B 和持久正式服务仍未运行。
- 验证：文档一致性断言 RED→GREEN。

### 2. 当前结果页仍写旧 GitLab main

- 原位置：`web/RESULTS.md:7`。
- 修复：把 `0c47d98` 标为合并前基线，记录本次更新前实测 `main=16e75f55`，最新值仍以 `git ls-remote` 为准；保留未强推、未向 GitHub 推送、未建 MR 和 CI 未验证。
- 验证：文档一致性断言 RED→GREEN。

### 3. 当前 694 配置预检仍写未通过

- 原位置：`AGENTS.md:154`，同类旧表述还见于 `RETROSPECTIVE.md` 和公开 `reproduction.json`。
- 修复：依据 `daily/2026-11-16.md` 与关键结果 `00001277-current_694_launch_check`，记录 `--check` exit 0；公开状态改为 `current_694_launch_check_passed_other_gates_remain`，保留不代表完整协议/GPU/API/评测链路通过的限制。
- 验证：`test_experiment_status_tracks_real_evidence` 先因旧状态 RED，更新后 GREEN；完整 website 12/12 再次通过。

## Minor（ deferred ）

1. 五轮 process 契约可进一步钉住 operation 1445、80 positions、B2 未激活和 final_active 字段；这些事实已由其他公开契约和本次主验证覆盖，本次不扩大测试范围。
2. 复盘中的旧快照计数和“当前无公开入口”位于明确历史语境；最新状态由 RESULTS/PROGRESS 维护，本次不改写该历史段落。

## 最终主复核

三项 Important 均已修复，完整 website **12/12 OK**，`node --check web/app.js`/`web/process.js`、公开 JSON 解析和 `git diff --check` 通过。按执行约定不重跑真实 GPU 五轮、不重新计算私有 run、不新增第二次 reviewer；本报告不替代此前五轮独立终审。
