# 独立 Kimi 合并与交付只读复核任务

工作目录：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/bwrap-isolation`

先读取该工作树 `AGENTS.md`，并阅读 `reproduction/PROGRESS.md`、`web/RESULTS.md`、`web/OPERATIONS.md`、`reproduction/audit/reviews/bwrap-sdd-progress.md`。目标是在主 agent 本地合并 `reproduction/4b-bwrap` 到 `reproduction/4b-main8` 并推送内部 GitLab 前，独立复核交付边界。

已知主工作树 `/data/wuxuecheng/Hierarchical_level/RSI-Flow` 有未提交 `reproduction/audit/operations.jsonl` 和 `key_results.jsonl` 追加记录；bwrap 工作树有未提交 `operations.jsonl` 和 `Meta_logs/`。这些都受保护，不能 reset、clean、stage、commit、删除或覆盖。网页服务与 cpolar 在上次会话退出后已停止，最终需要从合并后的主工作树恢复并实测。

请给出：
1. 合并前必须核对的事实；
2. 在不暂存、不提交、不覆盖审计追加记录的前提下，如何处理主工作树 dirty 文件造成的 merge 阻塞；
3. 合并后测试、公网、GitLab 和审计验收清单；
4. 最终汇报不能夸大的结论。

只允许写 `reproduction/audit/reviews/delivery-integration-kimi.md`，以 `PASS` 或 `CHANGES` 开头，列出证据路径与建议。禁止修改其他文件，禁止读取/打印凭据，禁止 commit、push、SCO、启动服务、删除或清理。

完成后在回复末尾输出 `RSIFLOW_KIMI_INTEGRATION_REVIEW_DONE`。
