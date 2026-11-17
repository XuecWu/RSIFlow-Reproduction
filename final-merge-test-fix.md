# Final merge test fix

- 基线：`bc47d54d34290630fe176a37c215287f193dfa26`
- 范围：仅修正 DeepCoder canary 机器配置测试的 `frozen_data_dir` 预期；未修改生产代码、机器配置或真实完成所用的 workspace config。

## 主 checkout 复现

在主 checkout `/data/wuxuecheng/Hierarchical_level/RSI-Flow/RSIFlow` 只读运行（禁用 pytest cache 与字节码写入）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/runtime:$PWD" \
  /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_deepcoder_canary.py::test_machine_config_changes_only_canary_fields_and_is_accepted
```

结果：`1 failed in 2.12s`。实际值固定为 `/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight/reproduction/logs/deepcoder-canary-16-v1/release`，旧预期则由主 checkout 的 `PROJECT` 动态生成 `/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/logs/deepcoder-canary-16-v1/release`。这复现了合并后暴露的预期错误；preflight 中两者原先恰好相同，因此旧测试为 GREEN。

## 最小修正

`RSIFlow/tests/test_deepcoder_canary.py` 中仅将 `expected["frozen_data_dir"]` 改为上述原机器配置的 preflight 绝对路径字面量。未从 `value` 读取预期，未改其他任何断言。

## 验证

在 preflight 的 `RSIFlow` 目录使用同一解释器、`PYTHONPATH` 和禁用 cache/字节码设置运行：

- focused 单测：`1 passed in 1.86s`。
- 完整 `tests` 套件：`522 passed, 2 skipped in 104.18s`。

主 checkout 复现前后 `git status --short` 一致，既有修改与未跟踪文件均未触碰。preflight 既有未跟踪审计 diff 文件未纳入本次提交。
