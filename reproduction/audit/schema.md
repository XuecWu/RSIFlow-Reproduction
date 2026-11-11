# RSI-Flow 复现审计规范

## 操作记录

`operations.jsonl` 是追加式全量操作账本。每次工具调用、命令、文件变更、网络获取、Herdr、SCO、cpolar、恢复和清理都必须记录，包括失败、拒绝、超时和无结论。

字段：

- `sequence`：从 1 开始单调递增，不重用。
- `start_timestamp` / `end_timestamp`：UTC ISO 8601。
- `actor`：主 agent 或独立审计 agent。
- `tool_kind`：bash/read/write/edit/grep/glob/web/herdr/sco/git/plan 等。
- `phase` / `action`：所属计划阶段和具体操作。
- `sanitized_input`：脱敏输入；凭据只写 `REDACTED`。
- `cwd` / `status` / `exit_code` / `duration_seconds` / `error_type`。
- `evidence_paths`：原始输出或报告路径；原始日志不进入公开 web 根目录。
- `git_commit` / `sco_job_id`：对应版本和集群作业，没有则为 null。

shell 命令由 `audit_exec.py` 包装，原始 stdout/stderr 脱敏后写入 `audit/evidence/`。无法经 wrapper 的 Read/Write/Edit/Grep/Glob、Herdr 等操作由主 agent 立即用 `record_operation.py` 追加。不得事后只凭记忆补写。证据路径使用绝对路径；命令支持超时记录和非 UTF-8 输出替换。

## 关键结果

`key_results.jsonl` 只记录有真实证据的结果：

- `result_id`、`timestamp`、`phase`、`metric`、`value`。
- `verification_command`、`evidence_paths`、`evidence_sha256`。
- `review_status`：`unreviewed`、`agent_reported`、`main_verified`、`changes_required`、`not_verified`；默认 `unreviewed`，只有主 agent 明确复核后才写 `main_verified`，非法状态拒绝写入。

模型或审计 agent 的返回值与主 agent 复算分行记录。没有实际输出、证据文件或可复现命令时，不得写成功结果。

## 资产与运行

`assets.json` 记录来源、revision、license、路径、大小、SHA256、获取时间、转换脚本和配置 hash。原始模型、数据、索引与私有文件不纳入 git。

`runs.json` 记录 backbone、provider/model、数据/模型/配置 hash、run dir、ACP job、状态和起止时间。

`daily/YYYY-MM-DD.md` 汇总当天阶段、所有关键结果、失败与根因、会审、资源、下一步和未解决项。每日核对 sequence 与证据链接。
