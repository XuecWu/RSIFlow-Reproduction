# Qwen4-Max 复盘文档任务

工作目录：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/bwrap-isolation`

你是本任务的复盘文档 implementer。先读取该工作树的 `AGENTS.md`，再阅读：
- `reproduction/PROGRESS.md`
- `reproduction/audit/reviews/bwrap-sdd-progress.md`
- `reproduction/audit/reviews/bwrap-task6-report.md`
- `reproduction/audit/reviews/bwrap-task7-report.md`
- `web/RESULTS.md`
- `web/OPERATIONS.md`

目标：新建并只修改 `reproduction/RETROSPECTIVE.md`，用中文沉淀本次 RSI-Flow 调研、修复、SCO 排障和交付经验。内容必须包括：
1. 阶段历程与关键决策；
2. 已交付内容和真实验证证据（含最终 HEAD 的 297 passed/2 skipped、网站 11/11）；
3. 失败与根因，区分已确认根因和外部 provider 故障，保留 failed/timeout/重试历史；
4. 可复用经验：Landlock/bwrap 边界、shared `/data`、A100/A800 证据不可混用、fresh run 状态、manifest/receipt、key hygiene、combined job 与 Meta-only retry 的结论口径；
5. 尚未完成事项和正式五轮前的 preflight；
6. 新内容如何靠 Git 分支、commit、JSONL 审计、报告哈希和网页追踪。

严格事实边界：`pt-v6m10c4t` 整体 FAILED，只有 Candidate/Harness/BFCL 阶段通过；`pt-x6b4ista` 是 8×A100 Meta-only SUCCEEDED。没有五轮、SFT、最终模型分数、8B 或 A800 Meta pass。Codex native-shell descendant hygiene 未验证，只允许当前 `shell_tool=false` gate。EvalPlus 是历史单任务 smoke，未在最终 HEAD 重跑。

禁止修改其他文件，禁止读取/打印密钥或凭据文件，禁止 commit、push、SCO、启动实验、删除或清理任何文件。不要修改 `operations.jsonl`、`key_results.jsonl` 或 `Meta_logs/`。

完成后运行 `git diff --check -- reproduction/RETROSPECTIVE.md`，自查文档不含秘密。返回文件路径、检查结果和一句总结，并在末尾将 `RSIFLOW_QWEN4_RETRO` 与 `DONE` 用下划线拼接作为完成标记。
