# 当前交付与真实结果

## 状态与访问入口

- 源码调研：已完成，上游基线 `496d3cfb9ff39997d107d0ff29e746b07e638180`。
- 复现分支：`reproduction/4b-main8`，新增代码、配置、测试和审计文件通过本地阶段 commit 追踪，未 push。
- 当前范围：Qwen3-4B、主分区 8×A800 但仅用 GPU 0–3、公开数据同协议重构、Meta `gpt-6-astra`。
- **尚未运行五轮演化或最终评估，没有任何模型 benchmark 分数。**
- **公网首页：** https://4a63b603.r36.cpolar.top
- **过程审计：** https://4a63b603.r36.cpolar.top/process.html
- **本地地址：** http://127.0.0.1:18000/

## 已验证的准备结果

1. 环境：生产/离线 Python 3.11、依赖 lock、Qwen3-4B 13 文件和 3 个权重分片、Codex 0.153.4 provenance 均已核验。
2. 数据：v4 冻结 3000=5×600，独立验证 744；每轮代码为 120 STDIO + 80 function。它是同协议重构，不是作者原始队列。
3. 评分口径：744 条冻结，当前只计划评分 694；ACE 50 为 pending，不记零分，不反馈 Meta。
4. 回执：`RSIFlow/runtime/data/rounds_3000_5x600_v4/READY.json` 自带 744/694/50 scope 和 config hash。
5. 对账：`reproduction/audit/cohort-release-v4.json` 连接 source build、READY 和逐源数量；保留语义独立性未知、预训练暴露未知和隔离重建历史。
6. Official spec：HumanEval+ 164、MBPP+ 178、HotpotQA 100、2Wiki 100 已从 v4 生成并验证。两类 QA 官方 v1 的单条金答案 smoke 均通过，EM=F1=0.01 是 1/100 固定分母结果，不是模型成绩。
7. 审计：新操作带证据 SHA256；历史操作有独立 `evidence-hashes.json`；过程 JSON 标记生成时间和截至 sequence。

## 当前阻断

- **完整实验硬阻断于 Landlock**：`pt-texqw4a8` 在目标 SCO `Linux-4.18.0-425.3.1.el8` 上明确返回 errno 38 / `Function not implemented`，在模型加载前失败。
- 本机也不支持 Landlock；上游最新全量测试为 163 passed、2 skipped、1 failed，不能宣称全绿。
- `pt-b1r8t0uk` 仅做 bwrap 和四卡 GPU 诊断；替代隔离方案必须单独审阅，不能把 bwrap 可用当成 Landlock 通过。
- `assets/private/credentials.sh` 尚未提供，真实 Meta `/models`、Responses/Chat 和 Codex dynamic tools 未验证。
- BFCL、HumanEval+、MBPP+ 最小真实 benchmark smoke 尚未全部完成。
- 8B 未准备、未运行；4B 审阅后才决定。

## 独立审阅与修正

Qwen Code 的 `reproduction/audit/reviews/preflight-qwen-r1.md` 给出 CHANGES。主 agent 已回源处理 v2/v3 错位、744/694 混淆、真实队列断言 skip、证据 hash、过程快照过期和公开状态保留意见。新真实 release 测试不再依赖上游 `/path/to` 占位配置。

2Wiki 官方 `data_ids.zip` 和 aliases 已下载校验，但上游 spec 生成器及当前数据使用 v1；本次不为了使用 aliases 而改成 v1.1。

## 运行服务

| 服务 | PID | 地址 | 原始日志（不公开） |
| --- | ---: | --- | --- |
| 静态 HTTP | 2000430 | `127.0.0.1:18000` | `web-runtime/logs/http-server.log` |
| cpolar | 2000586 | `https://4a63b603.r36.cpolar.top` | `web-runtime/logs/cpolar.log` |

cpolar 临时地址可能变化。公开目录仅包含脱敏静态页面；原始操作证据和私有轨迹不放入 web 根目录。
