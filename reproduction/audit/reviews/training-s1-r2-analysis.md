# 训练侧 S1 r2：真实 rollout 证据与 candidate_error 分类修复

- 日期：2026-11-17
- SCO 作业：`pt-x7apfiza`，终态 `FAILED`
- 输出：`reproduction/logs/training-side-v5-s1-20261117T123014Z/`
- 性质：真实五条 mini-B1 rollout 已执行；不是正式 600-task B1，不是 benchmark，不是 SFT/恢复通过。

## 与首轮相比的实际进展

首轮 `pt-ave60pvr` 在 bootstrap/rollout 前因 legacy Landlock 不可用而停止。本轮显式 bwrap 后已进入真实四卡 rollout：

- `coverage.json`：5/5 unique tasks、5/5 training rollouts、`full_coverage=true`。
- `cost.json`：124 次模型调用，823560 input tokens、15878 output tokens，usage complete。
- 每任务调用：EnvScaler 40、DeepCoder STDIO 70、DeepCoder function 6、2Wiki 8、HotpotQA 0。
- 官方评分文件 `performance.json` 完整；当前五条均未成功，宏成功率为 0，但这不是最终可接受的 S1 回执，因为仍有零调用与旧验证器误分类。
- 清理：owned registry 校验、服务停止和四卡显存释放通过，`cleanup.error=null`。

## 两类 candidate_error 必须分开

### 2Wiki：可作为已评分的模型/Harness 失败

- Task：`2wiki:f96fae0a0bae11ebab90acde48001122`。
- 8 次 `transport_calls` 全部 completed，并有逐调用持久 receipt。
- `verification.status=completed`、`success=false`。
- `infrastructure_error=false`、`execution_error_type=candidate_error`。
- `error=FileNotFoundError: [Errno 2] No such file or directory`，同时存在模型最终答案 `Purugupta`。
- 生产 `_classify_error` 只把 `TaskInfrastructureError` 归为 infrastructure；普通异常归为 candidate_error。smoke 旧逻辑却把任何 execution_error_type 都当成基础设施失败，与既定“模型答错但评分完成可记 completed_with_model_failures”合同不一致。

### HotpotQA：仍必须阻断

- Task：`hotpotqa:5abaf8be5542992ccd8e7e85`。
- `model_call_count=0`、`transport_calls=[]`、`steps=0`、最终答案为空。
- `error=BlockingIOError: [Errno 11] Resource temporarily unavailable`。
- 虽然生产字段也标为 candidate_error 且官方空答案评分完成，但没有真实本地模型调用，绝不能凭评分文件把它记成有效模型失败。
- 当前证据只有异常字符串，没有 traceback，尚不能确认 EAGAIN 来自进程创建、线程资源还是其他子系统；不得编造根因。下一次全新目录重跑用于获得新证据；若重复出现，再增加边界诊断。

## TDD 修复

- RED：两个新增测试均失败。
  - 有真实调用的 candidate_error 被错误记为 incomplete。
  - 零调用 candidate_error 虽也 incomplete，但失败原因是旧“Unexplained infrastructure failure”，不是必需的零调用门。
- 最小修复：只有 `infrastructure_error=true` 或 `execution_error_type=infrastructure` 才走基础设施阻断；candidate_error 仍须继续通过完整评分、至少一次真实调用、transport/receipt 对账和五类 coverage。
- GREEN：两个测试 `2 passed`。
- 训练侧完整测试：`91 passed`。
- 主 agent 全量：`463 passed, 2 skipped in 103.86s`。

## 独立审阅与后续门

- 独立 Qwen scoped review `reproduction/audit/reviews/training-s1-candidate-error-review.md`：PASS，0 Critical、0 Important；唯一 Minor 为可选注释，不阻断。
- 下一次 S1 使用全新固定任务输出目录 `reproduction/logs/training-side-v5-s1-dev4-20261117T125342Z`，在已核验的独占 4×A800 dev 机器执行，显式 bwrap，硬上限 7200 秒；旧目录保留。
- 若 HotpotQA 仍零调用或出现真实 infra 错误，S1 继续 incomplete，S2/S3 不启动。
- 若五条全部有效评分但自然正例为 0，则 S1 可为 completed_with_model_failures，S2 必须 `blocked_no_positive_parent_trajectory`；不换题、不造标签、不借 held-out。
- 只有存在自然正例才允许进入 S2 真实训练。
