# skip_ace 修复审查报告（R1）

结论：**CHANGES**

## 范围与方法

静态审查 `RSIFlow/validate.py`、`RSIFlow/validation_skip.py`、`RSIFlow/tests/test_skip_ace_protocol.py`，对照 `git show HEAD:RSIFlow/validate.py` 中的旧 skip_ace 分支，并只读追踪指标发布及回执消费代码。旧版指本次审查时的 Git HEAD 版本，不混同于 AGENTS.md 中的历史调研基线。

未运行测试、验证入口、GPU/API smoke 或实验，未读取凭据文件或密钥。此次交付仅写入本报告，不修改代码。下述结论来自源码检查，不代表执行验证通过。

## 已确认的正确行为

- `RSIFlow/validation_skip.py:6`：从配置的 benchmark 列表排除 `acebench`，不再强制包含旧队列的 LiveCodeBench。
- `RSIFlow/validation_skip.py:7`、`RSIFlow/validation_skip.py:8`：总数取自 manifest，已评分目标取非 ACE 任务数量。744 队列跳过 50 个 ACE 后应为 **694/744**；旧 300 队列跳过 50 个 ACE 后应为 **250/300**。
- `RSIFlow/validation_skip.py:10`：保留结果列表、评分数量、整体不得完整及不得存在 ACE 结果文件的检查。
- `RSIFlow/validation_skip.py:18`：保留 `code_search_completed_ace_pending` 状态和原回执字段名称，`feedback_to_meta` 仍为 `False`。
- `RSIFlow/validate.py:11`：仍限制 skip_ace 只能用于非 full-benchmark 的 independent_validation。
- `RSIFlow/validate.py:108`：正常 skip 分支仍写入独立的部分完成回执，并在写完整 `complete.json` 前返回。
- `RSIFlow/tests/test_skip_ace_protocol.py:26`、`RSIFlow/tests/test_skip_ace_protocol.py:38`：分别覆盖 694/744 与 250/300 的计算；`RSIFlow/tests/test_skip_ace_protocol.py:47` 覆盖评分数量不足和已有 ACE 结果文件的拒绝。

## 问题一：指标文件缺失时仍生成部分完成回执

- 严重度：P2。
- 位置：`RSIFlow/validation_skip.py:16`、`RSIFlow/validation_skip.py:18`。
- 类型：本次提取辅助函数引入的校验退化。
- 事实：新实现对不存在或非普通文件的 `task_metrics.json` 返回 `metrics_sha256=None`，只要传入的内存 metrics 满足计数条件，仍返回部分完成状态。旧版直接调用 `sha(out/'task_metrics.json')`，缺失文件会报错。
- 影响：辅助函数可以宣告部分完成，却没有对应的持久化指标及有效摘要，削弱回执的证据完整性，也不符合要求真实摘要的消费者约束。正常 `validate()` 路径此前会调用 `publish()` 写文件，因此这不是每次运行都会触发的问题；触发条件是指标文件缺失或辅助函数被单独调用。
- 修复建议：恢复严格的文件读取和 SHA256 计算，文件缺失或不可读时拒绝生成完成回执。不要以 `None` 代替必需证据。
- 测试遗漏：`RSIFlow/tests/test_skip_ace_protocol.py:31`、`RSIFlow/tests/test_skip_ace_protocol.py:43` 的成功用例未创建指标文件，默认接受了此退化。应为成功用例提供指标文件并断言摘要，另增加缺失文件拒绝用例。

## 问题二：旧轮次验证调用链仍硬编码 250/300

- 严重度：P2。
- 位置：`RSIFlow/runtime/rsiflow/task_meta/round_validation.py:48`、`RSIFlow/runtime/rsiflow/task_meta/round_validation.py:55`、`RSIFlow/runtime/rsiflow/task_meta/round_validation.py:64`、`RSIFlow/runtime/rsiflow/task_meta/round_validation.py:66`。
- 类型：744 队列兼容性遗漏；旧消费者的既有约束尚未同步。
- 事实：`verify_partial()` 仍要求包含 LiveCodeBench 的固定列表，以及回执和指标均为 250/300。存在恢复授权文件时，授权校验也仍固定要求 300 和 250。`RSIFlow/runtime/rsiflow/task_meta/round_validation.py:87` 在子进程完成后再次验证该回执。
- 影响：若通过该旧入口运行 744 队列，即使新 `validate.py` 正确完成 694 个非 ACE 任务，仍会被拒绝；携带新计数的恢复授权也无法通过。这不是旧 300 队列的回归，而是新队列在该入口尚不兼容。当前 `TaskAdapter.evaluate()` 路径直接调用 `validate.py`，不经过此验证器，不能将此问题扩大为所有入口均失败。
- 修复建议：如果该入口需要支持 744，则依据已验证的冻结 manifest 和配置推导非 ACE benchmark 列表及计数，并同步授权与回执校验；保留旧 300 队列兼容性。若该入口明确仅支持历史 300 协议，应在启动评估前明确拒绝不支持的队列，并说明适用范围。
- 测试遗漏：`RSIFlow/tests/test_skip_ace_protocol.py:38` 仅证明辅助函数支持旧计数，没有覆盖消费者对新旧回执的接受情况。应增加旧调用链的 694/744 与 250/300 回执验证用例。

## 问题三：已有完整回执绕过 skip_ace 状态约束

- 严重度：P2。
- 位置：`RSIFlow/validate.py:49`、`RSIFlow/validate.py:54`。
- 类型：旧版已有问题，本次未修复，并非本次新引入。
- 事实：发现 `complete.json` 后，代码检查 state hash 和结果文件摘要，然后直接返回；该分支没有检查 `skip_ace`。末尾 skip 分支中“不得存在 ACE 结果”的检查因提前返回而不会执行。
- 影响：对已经完整评估过的同一输出目录调用 `skip_ace=True`，在原绑定和摘要一致时，可能返回完整 `completed` 回执，而不是部分完成回执或明确的模式冲突错误。输出语义与 skip 分支的约束不一致。
- 修复建议：在完整回执缓存返回前显式处理 `skip_ace`。为维持现有部分评估语义，应拒绝把已有完整评估目录当作 ACE-skipped 结果复用，并提示使用独立输出目录；不要将完整回执改写为部分回执。
- 测试遗漏：`RSIFlow/tests/test_skip_ace_protocol.py:6` 只导入辅助函数，现有测试未覆盖 `validate()` 的完整缓存提前返回。应增加“已有完整回执且 skip_ace=True”的入口级测试，并断言明确拒绝。

## 补充说明

`RSIFlow/validate.py:149` 的 CLI 帮助仍只提到“300-task report”，没有体现新增 744 队列支持。建议改为不绑定固定任务数的说明。这是文案遗漏，不单独构成阻塞问题。

本次核心计数修复方向正确，旧回执字段和正常部分完成状态保留；CHANGES 的依据是证据文件校验退化、旧消费者兼容性遗漏，以及已有完整缓存的状态边界问题。测试需要覆盖上述边界，不能仅以两种计数成功用例作为端到端兼容性证明。
