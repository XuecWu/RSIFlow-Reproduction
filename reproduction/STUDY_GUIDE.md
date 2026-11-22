# RSI-Flow 学习专题：从双层改进机制到单域五轮复现

- 版本：v1.0
- 整理日期：2026-11-22
- 适用对象：第一次阅读 RSI-Flow，或希望理解本次复现过程与证据的同学
- 当前代码与结果基线：`reproduction/4b-main8` 的五轮交付及其后续文档更新
- 核心原则：先分清机制、工程验证和实验结论，再阅读分数

## 1. 这份专题怎么使用

可以按自己的时间选择一条路线。

### 20 分钟：先建立正确整体认识

1. 阅读本文件第 2–4 节，理解 Task/Meta 和一轮闭环。
2. 查看第 3 节的 B1–B5 表格，知道最终 5/16 是怎样得到的。
3. 阅读第 10 节“不能怎样解释结果”，避免把工程 canary 当作作者原始实验复现。

### 60–90 分钟：理解代码怎样实现机制

1. 阅读 `AGENTS.md` 的项目地图和控制流。
2. 按本文件第 6 节顺序阅读代码，并带着每行的问题查找答案。
3. 阅读 `reproduction/LEARNING.md` 的连续问答，理解工具调用、技能记忆和固定方法论。
4. 对照 `reproduction/audit/reviews/deepcoder-five-round-real-results.md`，把抽象机制映射到真实回执。

### 半天：完成一次离线证据学习

1. 阅读第 8 节的复现历程，理解失败和恢复也是实验的一部分。
2. 按第 9 节检查最终汇总、关键结果账本和独立审阅。
3. 运行网站测试；环境已经准备好时，再运行完整离线 pytest。
4. 独立完成第 11 节的四道练习，不调用 GPU 或外部 Meta API。

真正重新运行五轮需要额外模型、数据、四张 GPU、Meta provider 和明确授权，不属于上述离线学习目标。

## 2. 先理解 RSI-Flow 是什么

RSI-Flow 不是简单地“用一个模型训练另一个模型”，而是用持久化 Meta agent 对完整 Task agent 做可测量、可恢复的迭代改进。

```text
Task agent = Qwen checkpoint + Harness + Artifacts
Meta agent = gpt-6-astra + Codex 持久线程 + 工具层 + 技能记忆
```

三个 Task 组件分别承担不同作用：

- **Qwen checkpoint**：负责语言理解、推理和生成。
- **Harness**：组织提示、规划、工具调用、检索、执行和结果处理。
- **Artifacts**：保存任务相关产物；当前 ARTIFACTS 协议可以直接修改已有 submissions 并重评分。

Meta 不是只有一次模型调用。Astra 负责推理和决策，Codex 线程保持上下文，工具层执行文件与实验操作，技能记忆保存历史经验和规则。Meta 的经验增加不等于 Astra 权重被训练，也不保证下一轮一定更好。

## 3. 先看我们实际跑出了什么

本次完成的是**固定同一批 16 条 DeepCoder/TACO function 任务的 A0+B1–B5 单域五轮工程实验**。五轮重复使用同一组任务，因此共有 80 个 rollout positions，但只有 16 个唯一任务。

| 轮次 | Meta 选择 | 父代→候选成功数 | 真实动作 | 最终决定 |
| --- | --- | ---: | --- | --- |
| B1 | ARTIFACTS | 2/16→3/16 | 修改一个已有 submission，零新增 Qwen 调用 | 接受并激活 |
| B2 | MODEL | 4/16→3/16 | 20 条正例、4 ranks、1 epoch、3 optimizer steps 的 LoRA SFT | 保留父代，不激活 |
| B3 | ARTIFACTS | 2/16→3/16 | 修改一个已有 submission，零新增 Qwen 调用 | 接受并激活 |
| B4 | ARTIFACTS | 2/16→3/16 | 修改一个已有 submission，零新增 Qwen 调用 | 接受并激活 |
| B5 | ARTIFACTS | 4/16→5/16 | 修改一个已有 submission，零新增 Qwen 调用 | 接受并激活 |

从最初固定批表现看，结果是 **2/16→5/16，即 12.5%→31.25%**。但最终 active 不是训练后的 B2 模型，而是：

```text
base Qwen3-4B + 四个累计生效的 ARTIFACTS
```

每轮只有 6–8 个有效 submissions 实际进入 full verifier，其余任务没有完整验证结果。ENOENT/EAGAIN 和零调用失败与成功/回归重叠，因此不能把全部净变化都干净地归因于某一种改进机制。

## 4. 一轮演化是怎样闭环的

```text
保存父代 Task 状态
    ↓
父代在当轮固定任务上运行，保存轨迹与逐任务结果
    ↓
Meta 读取统计、代表轨迹和历史技能
    ↓
Meta 只选择 MODEL / HARNESS / ARTIFACTS 之一
    ↓
通过 Codex 原生工具和 experiment 工具构造一个候选
    ↓
候选在完全相同的任务上运行或直接重评分
    ↓
比较成功率、逐任务新增成功与回归、成本和身份绑定
    ↓
Meta 显式选择 accept_candidate 或 retain_parent
    ↓
保存 active/selected state、skills、context 和 snapshot
    ↓
下一轮检索本轮成功与失败经验
```

有两个容易忽略的事实：

1. 候选主要由 **Meta 通过工具构造**，不是 Qwen 自己决定怎样修改自己。
2. `activate_task` 负责执行显式选择，不会替 Meta 自动判断增益正负；严格正增益是实验协议对 Meta 决策的约束。

B2 是理解这个闭环的最好案例：训练确实完成，但候选从 4/16 降到 3/16，所以正确行为是保留父代，而不是因为“训练成功”就激活新模型。

## 5. 三种更新与 Meta 技能记忆

### MODEL

只使用当前父代训练 rollout 中经评估验证的自然成功轨迹，执行 positive-only LoRA SFT、merge 和 reload。训练工程完成和模型质量提升是两个独立结论，必须再看同批配对结果。

### HARNESS

物化完整父代 Harness，定位模块、写改进方向、生成候选并执行验证。单轮 canary 曾选择 HARNESS，取得 3/16→4/16，但伴随 4 个新成功和 3 个回归，且零调用异常限制因果归因。

### ARTIFACTS

本次五轮采用 `direct_submission_same_tasks_v1`。Meta 修复一个已有 submission，复用其他 15 个 baseline results，然后直接重评分。这证明的是 **Meta-assisted output repair**，不是 Task policy 或通用模型能力提升。

### 技能记忆

`meta/skills.jsonl` 是 append-only 经验账本，记录 case、rule、incident、procedure、rule_update 和维护事件。后续 attempt 可以检索成功与失败经验，但“写入技能”不等于“模型已学会”，也不保证检索后决策一定正确。

## 6. 推荐代码阅读顺序

| 顺序 | 文件 | 阅读时重点回答的问题 |
| ---: | --- | --- |
| 1 | `AGENTS.md` | 项目边界、真实控制流和当前实验口径是什么？ |
| 2 | `RSIFlow/launcher.py`、`RSIFlow/launch_meta.py` | 配置怎样进入控制器，run 与恢复怎样开始？ |
| 3 | `RSIFlow/persistent_meta.py` | 同一 Codex thread、动态工具和 durable jobs 怎样维持？ |
| 4 | `RSIFlow/controller_tools.py` | 工具怎样返回事实，为什么控制器不替 Meta 做决策？ |
| 5 | `RSIFlow/task_adapter.py` | launcher/base 配置怎样合并，rollout/SFT/ARTIFACTS 怎样执行？ |
| 6 | `RSIFlow/skill_memory.py`、`RSIFlow/META_SKILLS.md` | 经验怎样追加、检索、维护和修订？ |
| 7 | `RSIFlow/evolution_protocol.py`、`data_protocol.py` | 同批配对、严格增益和训练/评估隔离怎样表达？ |
| 8 | `reproduction/scripts/summarize_deepcoder_five_round.py` | 最终汇总怎样从原始回执重建，而不是复制展示数字？ |
| 9 | `RSIFlow/tests/`、`web/tests/` | 哪些不变量有自动检查，哪些仍必须依赖真实证据？ |

读代码时建议同时打开一个真实 `decision.json`、`selection.json` 和 `snapshot_receipt.json`，沿着“Meta 决策→候选→比较→选择→快照”追踪一次。

## 7. 正式协议与本次 canary 的边界

正式协议是五域五轮，每轮 600 个不同训练任务，共 3000 个；最终独立评估为 744，当前因 ACE 50 pending 计划报告 694/744。独立评估只用于报告，不能进入 SFT、候选接受或 Meta 学习。

本次则是有意缩小范围的单域工程实验：

| 项目 | 正式协议 | 本次五轮 |
| --- | --- | --- |
| 领域 | 五域 | 仅 DeepCoder/TACO function |
| 每轮任务 | 600 个不同任务 | 同一批 16 个任务 |
| 五轮总量 | 3000 个不同任务 | 80 positions / 16 个唯一任务 |
| 独立评估 | 744，当前计划评分 694 | 未运行 |
| 结论 | 面向作者协议复现 | 仅证明单域工程闭环 |

## 8. 我们是怎样一步步跑通的

1. **源码调研**：确认 Task/Meta 控制流、三类更新、数据协议和外部资产缺口。
2. **环境与隔离准备**：准备 Python、模型和数据；由于旧内核 Landlock 不可用，引入显式 `linux_bwrap_seccomp_v1`，禁止静默回退 host。
3. **SCO 排障**：保留多次失败、suspend、provider 502 和超时记录；最终 A100 smoke 区分 combined Meta 失败与后续 retry 成功。
4. **转向 dev4 四卡 A800**：在共享盘上复用代码和证据，加快真实控制流迭代。
5. **单轮 canary**：先验证固定 16 条上的 HARNESS 闭环，暴露零调用、回归和恢复问题。
6. **五轮前置修复**：补齐多轮恢复、汇总器、cleanup、MODEL smoke 和证据绑定，再开始真实五轮。
7. **真实五轮与恢复**：保留 supervision 0001/0003–0008；中间受控停止和 missing-key failure 不改写，最终 0008 正常完成。
8. **独立终审与交付**：重算最终汇总，完成独立 re-review、完整离线测试、公开网页和 GitLab 备份。

更完整的经验、失败根因和决策记录见 `reproduction/RETROSPECTIVE.md`，日常操作见 `web/OPERATIONS.md`。

## 9. 怎样核验结果，而不是只看网页

按以下顺序阅读：

1. `reproduction/audit/reviews/deepcoder-five-round-real-results.md`：真实五轮结果和限制。
2. `reproduction/audit/evidence/five-round-final-summary-20261119.json`：接受的最终机器汇总。
3. `reproduction/audit/reviews/deepcoder-five-round-final-verification.md`：完整测试和终审证据。
4. `reproduction/audit/reviews/deepcoder-five-round-final-re-review.md`：独立 scoped re-review。
5. `reproduction/audit/key_results.jsonl`：逐条关键结果及证据哈希。
6. `reproduction/audit/operations.jsonl`：成功、失败、阻断和恢复操作。
7. 过程页：https://webshow.vip.cpolar.top/rsiflow/process.html

接受汇总的 SHA256 为：

```text
3675d1fa07892afd879838d84bf253787322fa30058f7b12cfea3e4ed134d671
```

如果共享盘上的真实 run 仍存在，可以用生产汇总器重新计算，而不是相信手工复制的结果：

```bash
python reproduction/scripts/summarize_deepcoder_five_round.py "$RUN_DIR" \
  --config "$CONFIG" --cleanup-receipt "$CLEANUP_RECEIPT"
```

`RUN_DIR`、`CONFIG` 和 `CLEANUP_RECEIPT` 必须指向同一次真实 run；不要把另一个 run 的 cleanup receipt 拼进来。

## 10. 不能怎样解释结果

以下说法都不准确：

- “已经复现作者报告的五域最终结果。”
- “Qwen 权重经过五轮训练后从 2/16 提升到 5/16。”
- “五轮共使用了 80 个不同任务。”
- “16 条任务全部进入了完整官方 verifier。”
- “B2 训练完成，所以新模型已经被接受。”
- “ARTIFACTS 的固定批提升证明通用 agent 能力提升。”
- “离线测试或 preflight 通过等于真实实验完成。”

准确结论是：**固定同一批 16 个 DeepCoder/TACO function 任务上的单域五轮工程闭环已经跑通；最终 active 从固定批 2/16 到 5/16，提升来自四个被接受的 ARTIFACTS，B2 MODEL 候选被拒绝。**

## 11. 离线练习

### 练习一：解释 B2

阅读 B2 的 `decision.json`、训练指标和 `selection.json`，回答：为什么训练链路成功，却仍然不能激活 merged candidate？

### 练习二：区分任务数和 rollout positions

查看最终汇总与五轮 manifest，证明为什么 80 不是唯一任务数，并核对五轮 task IDs/source hashes 的一致性。

### 练习三：追踪一次 ARTIFACTS 接受

选择 B1、B3、B4 或 B5，找到父代/候选 performance、selection、activation 和 snapshot，列出严格正增益及新增成功/回归。

### 练习四：运行公开交付检查

```bash
python3 -m unittest discover -s web/tests -v
node --check web/app.js
node --check web/process.js
python3 -m json.tool web/data/reproduction.json > /dev/null
python3 -m json.tool web/data/process.json > /dev/null
```

环境已按 `reproduction/REPRODUCTION.md` 准备时，可再运行：

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" python -m pytest -q tests
```

这些练习不需要重新运行真实五轮，也不应触发 Meta API 或 GPU 服务。

## 12. 继续学习与维护

- 机制连续问答：`reproduction/LEARNING.md`
- 项目级事实与边界：`AGENTS.md`
- 当前进度：`reproduction/PROGRESS.md`
- 真实结果汇总：`web/RESULTS.md`
- 复盘与经验：`reproduction/RETROSPECTIVE.md`
- 复现命令和资源前提：`reproduction/REPRODUCTION.md`

后续新增实验时，应同时更新真实结果报告、审计账本、公开 JSON 和本专题的对应章节；不要把新的工程状态只写在某一个页面里。

## 维护日志

### v1.0 — 2026-11-22

- 建立面向新读者的分层学习路线、代码阅读顺序和离线练习。
- 纳入真实单域五轮 B1–B5、2/16→5/16、B2 SFT 未激活和最终 active 组成。
- 链入最终汇总、独立审阅、操作账本、复盘和公开过程页。
