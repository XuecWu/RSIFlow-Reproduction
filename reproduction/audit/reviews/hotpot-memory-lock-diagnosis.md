# HotpotQA 零调用与 2Wiki candidate_error：memory flock 诊断

- 日期：2026-11-17
- 诊断输出：`reproduction/logs/training-side-v5-s1-dev4-trace-20261117T131306Z/`
- 方法：在不改生产源码的一次性诊断入口中包装 `_classify_error`，仅增加 `traceback.print_exception`；完整五条任务和四卡环境与原 S1 相同。该运行不作为正式 S1 PASS。

## HotpotQA 根因

完整异常栈：

```text
run_rsiflow()
  → _memory_scope(memory_storage_root).__enter__
  → fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
  → BlockingIOError: [Errno 11] Resource temporarily unavailable
```

位置：`RSIFlow/runtime/rsiflow/task_meta/rsiflow_runtime.py:640-657`，实际失败点为 `:653`。

因此 HotpotQA 的零调用不是 GPU、模型服务、进程数上限或一般性资源耗尽：异常发生在任何 agent 构建和模型调用之前，具体边界是共享盘上的 `_memory_scope/.rollout.lock` 排他 flock 获取。共享盘为 quarkfs；本诊断没有继续修改文件系统或锁实现，因此不对更底层网络文件系统锁协议作未验证断言。

## 2Wiki FileNotFoundError 根因

另一份完整异常栈显示：

```text
run_rsiflow()
  → _memory_scope(memory_storage_root).__exit__
  → fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
  → FileNotFoundError: [Errno 2] No such file or directory
```

失败点为 `rsiflow_runtime.py:657`。2Wiki 的 8 次真实模型调用、检索和最终答案已经发生，异常在 memory scope 退出解锁阶段出现。这解释了为什么它同时具备 candidate_error、完整调用和完整评分；不能把它当作模型答案质量本身，也不能在 smoke 中误称未知基础设施故障。

## 处置边界

- 当前用户已批准将主线缩为 DeepCoder/TACO function 单域 canary；该路径不依赖 HotpotQA/2Wiki 的 searchqa memory scope。
- 本轮不为了让单域 canary 通过而扩大修改共享 memory flock 生产逻辑。
- 多域恢复时应单独设计并 TDD 验证共享文件系统上的锁获取/释放、并发归属、异常分类与本地锁替代方案；不能简单删除锁或忽略解锁异常。
- 两次诊断均完成 owned services 清理并释放四卡；第一次诊断启动仅有 import-path 错误，未进入 rollout，第二次才产生上述真实异常栈。
