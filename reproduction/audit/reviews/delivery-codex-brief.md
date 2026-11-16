# Codex GitLab 备份只读审查任务

工作目录：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/bwrap-isolation`

先读取该工作树 `AGENTS.md`。审查对象是当前分支 `reproduction/4b-bwrap` 的 HEAD `6c2049b002ad6c4214af34e8ecfb0331cb03adeb`，目标是备份到内部 GitLab `git@gitlab.acerobotics.com:wuxuecheng/rsiflow.git`。用户已明确授权主 agent 后续配置 remote 和 push；你只读审查，不执行远程写操作。

只读检查：
- 当前分支、base、remote、status 和待推送提交范围；
- HEAD 已跟踪文件中是否有超过 50 MiB 的文件、明显凭据/私钥/密钥材料、机器特定 symlink 或不应入库的大资产；
- `.gitignore` 是否覆盖 assets、模型、日志、缓存和本地运行产物；
- 不要把文档中关于 API key 的抽象说明误报为真实密钥，但任何疑似真实 secret 都必须报告准确路径和行号，不能打印 secret 内容；
- 检查 `Meta_logs/` 和未提交 `operations.jsonl` 是否会被普通显式推送 HEAD 带入远端；
- 给出推送前后核验清单。

允许写入的唯一文件是 `reproduction/audit/reviews/delivery-gitlab-codex.md`。报告必须以 `PASS` 或 `CHANGES` 开头，列出实际命令、范围、发现和未覆盖边界。禁止修改任何其他文件，禁止读取/打印认证文件，禁止 commit、push、PR、SCO、删除或清理。

完成后在回复末尾输出 `RSIFLOW_CODEX_GITLAB_REVIEW_DONE`。
