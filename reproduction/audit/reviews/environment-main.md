# 上游全量离线测试与 Landlock 环境结论

- 离线环境：`/data/wuxuecheng/miniforge3/envs/rsiflow-offline`，Python 3.11。
- 首次完整 `RSIFlow/tests`：160 passed、2 skipped、1 failed；新增 frozen evaluation scope 后复跑为 161 passed、2 skipped、1 failed；加入 provider/backend 与 BFCL preflight 后最新为 163 passed、2 skipped、1 failed。
- 失败用例：`test_real_isolated_harness_validation_ignores_host_directory_name`。
- 直接复现得到 `RSIFlow validation isolation failed: OSError: [Errno 38] landlock_create_ruleset`。
- 最小 syscall 检查：`landlock_abi=-1`，`Function not implemented`，不是目录名或 Python 代码问题。
- 不修改上游断言、不宣称全量测试通过；主分区 probe `pt-r6np9yvd` 只确认调度成功，结束容器 stdout 不可得。
- `pt-texqw4a8` 的共享盘 `landlock-model-smoke.json` 明确显示目标内核 `Linux-4.18.0-425.3.1.el8` 同样返回 errno 38、`landlock_available=false`，作业在模型加载前 FAILED。
- 当前完整实验因 Landlock 硬阻断。`pt-b1r8t0uk` 只做 bwrap 与四卡 GPU 诊断，不是绕过隔离的完整实验。

skip-ACE 的聚焦修复与自定义 provider 离线检查仍有效，但不能替代目标环境的隔离能力。替代隔离方案必须单独审阅和验证，不能把 bwrap 可用直接等同于 Landlock 语义通过。
