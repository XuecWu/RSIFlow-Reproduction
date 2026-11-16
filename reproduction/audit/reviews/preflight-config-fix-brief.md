# Preflight 有效配置修复任务

工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight`
分支：`reproduction/4b-preflight`
基线：`9f4c2624e1bc30f72f60272286a314fb297819f7`，完整 pytest 已实际取得 `297 passed, 2 skipped in 89.42s`。

先读该工作树 `AGENTS.md`。独立发现来源：主工作树 `reproduction/audit/reviews/experiment-preflight-kimi-r1.md` Critical 3/4、Important 4；两项配置映射缺陷已由主 agent 回源确认。用户已明确批准 bounded 设计并授权实施。

允许修改：
- `RSIFlow/task_adapter.py`
- 一个最合适的现有 `RSIFlow/tests/test_*.py` 文件
- `AGENTS.md`
- `reproduction/REPRODUCTION.md`

要求：
1. 严格 TDD。先新增 effective-config 回归测试并实际运行，观察因映射缺失而 RED；保存命令和失败摘要。
2. launcher 提供 `max_wall_seconds` 时，有效 PipelineConfig 必须采用该值；未提供时保留 base 值。
3. launcher 提供 `meta_worker_socket` / `meta_relay_socket` 时，有效 `config.meta.remote_worker_socket` / `remote_relay_socket` 必须采用它们；未提供时保留 base 值。
4. 实现最小化，仅在现有 `TaskAdapter._config()` 合并流程传递这三个字段，不改配置 schema、默认上游 JSON、GPU 映射或其他行为。
5. 文档把当前 694 检查改为显式 `--config` 的准确命令，使用 rsiflow 解释器；明确 ACP 正式控制器应前台、显式机器配置，不能直接依赖固定 `--prepare-data --detach` 的脚本。不宣称五轮已运行。
6. 运行新测试、相关测试和完整 `cd RSIFlow && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests`。完整测试若失败必须报告名称，不得隐瞒。
7. 不 commit、不 push、不启动实验/SCO/API，不读取凭据，不修改主工作树或其他 worktree，不修改 JSONL 审计账本。

把 RED/GREEN 命令、输出、修改文件和自审写入 `reproduction/audit/reviews/preflight-config-fix-report.md`（允许新建该报告）。完成后回复状态、测试摘要和报告路径。
