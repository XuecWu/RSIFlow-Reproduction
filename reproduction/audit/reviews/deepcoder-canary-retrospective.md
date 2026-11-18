# DeepCoder function 16 单轮 canary 专项复盘

- 日期：2026-11-18（UTC）
- 对象：2026-11-17 真实单域单轮单候选 canary 及其前置失败
- 权威结果：`deepcoder-canary-real-results.md`
- 独立审阅：Spec PASS；Quality DONE_WITH_CONCERNS

## 1. 最终真正完成了什么

固定 16 条 DeepCoder/TACO function 任务完成 A0+B1、一个 HARNESS 候选、父子同批比较、显式接受和激活、技能记忆、context、snapshot、durable jobs 调和与四卡清理。

| 指标 | 父代 | 候选 |
| --- | ---: | ---: |
| 固定任务 | 16 | 16 |
| 真实 Qwen 调用 | 51 | 93 |
| 有效 submission 进入 full verifier | 7 | 8 |
| 官方成功 | 3 | 4 |
| macro success | 0.1875 | 0.25 |

完整配对增量为 +0.0625，但有 4 个新成功和 3 个回归，父代原 3 个成功全部回归。MODEL 未选择，SFT 未运行，Qwen checkpoint 未变化。

这次证明的是**真实单轮工程闭环能够完成并恢复**，不是普遍 Harness 改进、模型质量提升、五轮稳定性或作者结果复现。

## 2. 历程与关键转折

### 2.1 第一次启动：旧内核没有 pidfd_open

dev4 Linux 4.18 的 `pidfd_open` 返回 ENOSYS，A0 前即失败。后续自动重连又因早期 state 未记录 backend 而被正确拒绝。

处理：只对 ENOSYS 增加 `/proc`+PGID+start_ticks 后备，其他 errno 和身份歧义继续 fail closed；启动时提前持久化 backend。真实 app-server 和完整 canary 后续验证了该路径。

保留风险：`/proc` 后备仍有理论 PID 复用窗口，部分信号后可能需要外部调和，不能宣称与 pidfd 等价。

### 2.2 第二次启动：bwrap 可写不等于 host 写门允许

真实 Astra thread 已建立，但 bootstrap 的 active state 位于 worktree 外，被 `NativeExperimentTools` host-side 写门拒绝。没有 Qwen rollout 或组件候选。

处理：不放宽写门，改用 workspace 内自然派生的 output/run 配置。bwrap mount 授权和 host 工具写授权是两层独立边界，必须同时满足。

### 2.3 第三次启动：父代完成，Astra 持续 502

A0、父代 16/16 和 HARNESS 前期产物完成后，Meta provider 连续 502，候选尚未通过验证。监督 attempt 0001 被有界停止并清理。

准确计数是 24 个 distinct failed turns、24 条 error events，另有 24 条对应 turn/completed events；不是 48 个失败 turns。

### 2.4 同 thread 恢复：避免重跑已完成工作

provider 健康探针恢复后，attempt 0002 使用同一 run、config、backend 和 Astra thread，从 `candidate_built` 继续。没有第二次 bootstrap、父代 rollout、技能种子重放或新实验 attempt。

最终同一 HARNESS 候选经过两次可执行检查后通过，完成评估、接受、激活和清理。历史 attempt 0001 保留，没有被改写成成功。

## 3. 结果质量的核心教训

### 3.1 净正增益不能替代逐任务转移分析

4 个新成功全部来自父代零模型调用失败，3 个回归全部是候选零模型调用失败。父代有 9 个零调用过程失败，候选有 8 个，主要涉及 EAGAIN/BlockingIOError 和 ENOENT/FileNotFoundError。

这些记录按当前协议保留在 16 条分母中，没有事后改分或撤销接受；但现有证据无法分离 Harness 编辑与执行过程波动。因此后续每轮必须同时报告：

- 净分数变化；
- new success/new regression；
- 零模型调用过程异常；
- 这些异常与结果转移的重合；
- 因果归因是否仍受限。

### 3.2 verification completed 不等于 full verifier 覆盖

父代 16 条、候选 16 条均有 verification completed，但只有 7/8 个有效 submission 进入 full verifier。其余无有效答案，不应写成“16 条均执行完整官方测试”。

后续报告必须分别列出 rollout 覆盖、verification 状态、有效 submission、full tests 和成功数。

### 3.3 过程失败分类要谨慎

这些零调用轨迹的 `infrastructure_error/failure=false`，所以不能擅自改记为 infrastructure error；但它们也不证明模型实际作答失败。最稳妥的口径是“协议内 process outcome，零模型调用，根因与质量归因未解决”。

## 4. 工程与运维经验

1. **完成回执优先于进程状态。** launcher exit 0、进程存活、transport success 或 state 最后帧单独都不是实验完成。必须同时检查 workflow、逐阶段产物、jobs 和资源清理。
2. **监督边界要拆分。** launcher PGID、durable jobs、Meta app-server 和四卡服务是不同 owner；4 小时 wrapper 不能自动证明后三者结束。
3. **恢复是同一实验的延续。** 同 thread、新 supervision attempt、原 run/config/backend；不得通过新 run 重放父代来“恢复”。
4. **失败历史不可覆盖。** 每次启动和 attempt 都有独立身份、exit 和 reconcile；后续成功不删除先前失败。
5. **停止要有身份核验。** 不使用宽泛 `pkill`、不按端口杀进程；active durable job 未调和时不 cleanup、不 resume。
6. **主机与沙箱授权要一致。** output/run、工具写门和 bwrap writable roots 必须在启动前共同核对。
7. **provider 健康是时间敏感的。** 一次 HTTP 200 只证明当次探针；持续 502 时保留部分产物，停止浪费资源，再从同一状态恢复。
8. **真实组件决定报告内容。** HARNESS 接受不能写成 MODEL/SFT 成功；未发生的训练明确写“未运行”。
9. **离线 GREEN 不是真实 PASS。** 测试、`--check`、smoke、单轮 canary 和五轮实验是不同证据层。
10. **审计单根不等于多 worktree 自动一致。** 既有 dirty 主账本不覆盖、不拼接；sequence 相同也必须逐条语义对账。

## 5. 对五轮设计的直接影响

- 新增独立五轮 scope，不能把单轮 `retain_parent` 终止语义直接扩展到五轮。
- 每轮最多一个 attempt；retain 完成当前轮经验和快照后进入下一轮。
- A0 和五轮使用新的 run；上一轮 accepted Task 和 skills 不偷渡进新 A0。
- 每轮结束立即核对 active identity、逐任务转移和 snapshot，不能等五轮结束才发现中间缺轮。
- 预先验证 B1→B2、retain→下一轮、B5 finish gate 和同 thread 恢复。
- MODEL 路径尚未真实验证，必须在五轮前独立通过 SFT/merge/reload 最小 smoke，或保持明确阻断。
- 固定 16 条重复五轮只用于工程演化，不增加泛化或作者复现口径。
- 每轮公开零调用异常和质量限制，不因后续轮次出现正增益而省略。

## 6. 可复用检查清单

### 每轮结束后

- [ ] 父子 ID、顺序、source hashes 和 manifest 完全一致。
- [ ] 实际组件、一个 attempt、一个候选和检查次数有回执。
- [ ] Qwen 调用数由 declared、trajectory 和持久 call 文件交叉核验。
- [ ] verification、有效 submission、full verifier 和成功数分开报告。
- [ ] delta、new success、new regression 和零调用异常共同分析。
- [ ] accept/retain 符合规则，active state 与选择一致。
- [ ] skills/context/snapshot 完整并可复算 hash。
- [ ] 下一轮从正确 selected Task 开始，没有重跑当前轮父代。

### 每次恢复前

- [ ] 当前 attempt 有最终 exit 和 complete reconcile。
- [ ] launcher/Codex PID 不存活，durable jobs 全部有最终回执。
- [ ] 使用同一 run/config/backend/thread，新 supervision attempt。
- [ ] 明确下一 milestone，不重复 bootstrap、rollout 或 SFT。

### 最终交付前

- [ ] state COMPLETE 与 finish_allowed=true 由回执支持。
- [ ] 所有 jobs、app-server、owned services 和 GPU 清理分别核验。
- [ ] 独立 Spec/质量审阅完成，限制没有被宣传口径覆盖。
- [ ] 文档、审计和网页只公开脱敏结果，不含凭据或私有轨迹。
