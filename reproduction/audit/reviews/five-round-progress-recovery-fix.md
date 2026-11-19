# 五轮真实恢复：轮次路径与原生 selection 修复

日期：2026-11-19（UTC）  
基线：`0c25bcbfc9f28915a34ef7899defb6210db45b3a`  
范围：`RSIFlow/experiment_progress.py`、`RSIFlow/tests/test_experiment_progress.py`

## 真实故障

supervision 0003 的真实产品已经走到 B1 snapshot，但 advisory workflow 仍显示 A0/bootstrap。原始 `workflow_events.jsonl` 显示 A0 bootstrap/snapshot 被分配到不存在的第 16 轮，B1 全部里程碑随后被标记为 `round_locked`。

根因是 `_explicit_round()` 的路径正则把 run 根名 `deepcoder_five_round_16_v1` 中的 `round_16` 当成实验轮次。另一个恢复兼容问题是实际 B1 `selection.json` 使用 `selection=accept`，而进度识别只读取 `accept_or_retain`。

这不是重复执行实验的理由。不得新建 run、重放 A0/B1 或修改历史回执。

## 最小修复

- 轮次识别按路径组件进行，不再扫描 run 名子串。保留 `round_00`、`round_00_base`、`round_00_seed200`、`round_1`、`round_1_attempts`、`round_1-attempt-1`、`B1_attempt_1`、`B1-attempt-1`、`round_1.json`，不再把 `deepcoder_five_round_16{,_v1,_attempt_1}` 误认为 B16。
- 选择读取兼容生产的 `selection` 字段，同时保留 `accept_or_retain` 优先语义，合法字符串集合不变；数组/对象等非法类型按 invalid selection 处理，不让 rebuild 崩溃。

## RED→GREEN 证据

新增：

- `test_five_round_run_name_batch_size_is_not_round_number`：初版修复前 `A0 != B1`。
- `test_five_round_native_selection_field_advances_after_accept`：修复前 `B1 != B2`。
- 12 组路径组件测试：初版正则下 7 failed / 5 passed，覆盖历史路径兼容及三种 run-root 反例。
- 原生非法 selection 数组/对象测试：修复前均因 `TypeError: unhashable type` 失败。

最终 `tests/test_experiment_progress.py`：**39 passed**；独立复审者也复跑为 39 passed。初次完整离线套件为 945 passed, 2 skipped；纳入复审修复后的最终完整离线套件为 **960 passed, 2 skipped in 506.06s**。

## 独立复审

第一轮指出初版正则丢失历史路径形式且仍有 run-root 残余误判，结论 With fixes。改为路径组件识别并补齐非法类型后，第二轮独立复审：Critical=0、Important=0、Minor=0，指定 diff 范围 **Ready: Yes**。该范围不包含 summarizer。

## 真实历史重建

对同一 run 的现有命令/回执执行生产 `ExperimentProgress.rebuild()`，结果：

- A0：bootstrap、task_meta_snapshot complete。
- B1：parent/evidence/decision/candidate/compare/selection/skills/context/snapshot complete，`last_selection=accept`。
- 当前：B2，`next_milestone=parent_rollout`，`finish_allowed=false`。

没有在此重建过程中调用 Astra、启动 GPU job 或重跑 B1。

## B1 原始回执交叉核验

真实 ARTIFACTS direct-submission 不调用 Qwen 生成新答案。逐条验证 16 份 submission receipt 的 `state_hash`、baseline row hash、编辑标记、持久 row 与 candidate 一致，并验证唯一编辑 artifact 的 baseline/payload：

- 父代：2/16。
- 候选：3/16。
- new success=1，new regression=0。
- 新增 Qwen calls=0，编辑 submissions=1。

该结果只表示 Meta-assisted output repair，不表示独立 Task policy、模型权重或 Harness 质量提升。

## 资源与外部服务

0003 遇到持续 Astra 502 后按 owned identity 停止：exit=143、reconcile complete；external cleanup `obs-05c28b734ae8` completed，四张 A800 实测释放。随后 provider probe 恢复 HTTP200/completed/gpt-6-astra。

## 当前限制

- 当前汇总器尚未兼容真实 ARTIFACTS 的 `candidate_rollout/`、`B1_attempt_1`、`selection=accept` 和 direct-submission receipt 结构；其 candidate=0/products blockers 是报告器缺陷，不得用于否定或重放真实 B1。
- 0004 必须在最终完整套件通过、当前 resume preflight 和 launch seal 全部通过后启动。
