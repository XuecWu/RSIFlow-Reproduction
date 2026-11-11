# 上游全量离线测试与 Landlock 环境结论

- 离线环境：`/data/wuxuecheng/miniforge3/envs/rsiflow-offline`，Python 3.11。
- 首次完整 `RSIFlow/tests`：160 passed、2 skipped、1 failed；新增 frozen evaluation scope 后复跑为 161 passed、2 skipped、1 failed；加入 provider/backend 与 BFCL preflight 后最新为 163 passed、2 skipped、1 failed。
- 失败用例：`test_real_isolated_harness_validation_ignores_host_directory_name`。
- 直接复现得到 `RSIFlow validation isolation failed: OSError: [Errno 38] landlock_create_ruleset`。
- 最小 syscall 检查：`landlock_abi=-1`，`Function not implemented`，不是目录名或 Python 代码问题。
- 不修改上游断言、不宣称全量测试通过；主分区 probe `pt-r6np9yvd` 用于验证目标容器 Landlock 可用性。

skip-ACE 的聚焦修复 6/6 与自定义 provider 3/3 仍有效，但完整真实实验也依赖 Landlock，需在目标环境验证后才能继续。
