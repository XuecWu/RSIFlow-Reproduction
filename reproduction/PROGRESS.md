# RSI-Flow 4B 复现进度报告

## 已完成并验证

- 本地复现分支 `reproduction/4b-main8`、阶段 commit、追加式操作/关键结果账本、每日审计和 Herdr 三方通道。
- Python 3.11 生产与离线环境、依赖 lock、Qwen3-4B 13 文件/3 分片/hash、Codex 0.153.4 provenance hash。
- 公开数据同协议重构：3000 个训练任务（五轮各 600），744 个验证任务；Code 每轮 120 STDIO + 80 function。
- 检索索引、`RoundStore`、`select_specs()`、ACE 多轮记录与 record offsets/hash。
- skip-ACE 新 694/744 与旧 250/300 兼容修复，自定义 provider 配置覆盖，过程页已发布。

## 当前等待与阻断

- 主分区 Landlock probe `pt-r6np9yvd` 仍在 STARTING/Pending，尚未执行 GPU 或模型 smoke。
- 本机内核 Landlock 返回 `Function not implemented`，上游完整测试为 160 passed、2 skipped、1 failed；目标容器是否支持仍待 probe。
- `assets/private/credentials.sh` 尚不存在，真实 Meta `/models`、Responses/Chat 和 Codex native tools 尚未验证。
- 尚未运行五轮实验、最终 694 评估或 8B；没有模型输出分数。

## 过程与证据

- cpolar 过程页：https://4a63b603.r36.cpolar.top/process.html
- 数据 READY：`RSIFlow/runtime/data/rounds_3000_5x600_v2/READY.json`
- 操作账本：`reproduction/audit/operations.jsonl`
- 关键结果：`reproduction/audit/key_results.jsonl`
- 每日记录：`reproduction/audit/daily/2026-11-11.md`

后续必须先完成 SCO Landlock/GPU probe 和 provider 私有凭据 smoke，不会直接点燃五轮实验。
