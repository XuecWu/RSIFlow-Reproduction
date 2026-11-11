# 外部资产与复现缺口

本次未下载或校验任何实验资产。下表的“来源”表示后续需要确认的上游/发布方，不表示已经取得文件。

## 关键结论

源码发布不包含模型权重、任务数据、source manifests、检索索引、API 凭据或 Codex 二进制。最关键的精确复现缺口是原始 3000/744 source manifests；`prepare_600_744.py` 不会从原始数据自动重建它们。

## 模型与控制器

| 资产 | 后续要求 | 本次状态 |
| --- | --- | --- |
| Qwen3-4B | 本地 Hugging Face checkpoint，记录来源 revision、文件 hash、tokenizer/chat template | 未下载 |
| Qwen3-8B | 与 4B 相同的记录和校验 | 未下载 |
| Codex binary | OpenAI Codex tag `rust-v0.153.4` 官方 release asset | 未下载 |
| Codex source identity | commit `3d2ee51ca2d5db578f328aa75e20aa22c0197c9a` | 仅源码记录已核对 |
| Codex binary SHA256 | `56ef98ab4032d317ab26e9b5e5a175650717351edb16ed9cde0cb6d1734d62da` | 仅源码记录已核对 |
| Meta endpoint | DeepSeek-V4.1-Flash，AutoDL Responses 接口和 model catalog | 未配置、未调用 |
| ACE 用户模拟 | 与配置兼容的 endpoint/model | 未配置、未调用 |

## 训练数据

| 来源 | 协议用途 | 每轮数量 | 后续所需文件/证据 |
| --- | --- | ---: | --- |
| EnvScaler | Tool use | 200 | train/heldout scenarios、environments、官方 utils、固定 commit |
| DeepCoder/TACO | Code | 200 | train.jsonl，120 STDIO + 80 function，任务身份与接口 strata |
| HotpotQA | Multi-hop QA | 100 | train.jsonl、检索语料和索引来源 |
| 2WikiMultiHopQA | Multi-hop QA | 100 | train.jsonl、检索语料和索引来源 |
| 原始 source training manifest | 五轮重打包输入 | 合计 3000 | 完整 task records、record offsets/hash、role/round/group/content identity |
| 原始 source validation manifest | 独立评估重打包输入 | 744 | 与训练 disjoint 的完整任务记录和 hash |

EnvScaler 源码配置记录 commit `96ae8b02dc0187c911b8e2101e7bb6904271597b`。环境定义可跨轮复用，但任务和内容不能重复。

## 独立评估资产

| Benchmark | 数量 | 后续所需资产 |
| --- | ---: | --- |
| BFCL-v3 | 152 | test.jsonl、v3 evaluator repository、兼容 Python 环境 |
| ACEBench | 50 | test.jsonl、AgentSuite/ACEBench、用户模拟 API |
| HumanEval+ | 164 | EvalPlus 数据、隔离执行和官方评分依赖 |
| MBPP+ | 178 | EvalPlus 数据、隔离执行和官方评分依赖 |
| HotpotQA-dev | 100 | dev data、官方评分脚本、预测格式与检索输入 |
| 2Wiki-dev | 100 | dev data、官方评分脚本、预测格式与检索输入 |
| official specs | 全部 | `report-evaluators.json` 及每个 interpreter/repository/data path |
| retrieval index/corpus | 训练与 QA 评估 | 与 frozen records 绑定的 SQLite/index manifest 和 hash |

当前协议不包含 LiveCodeBench。任何 benchmark 缺失都应显示为未完成/阻断，不能用其他任务数量补齐 744。

## 仓库已经包含的内容

- 初始 Harness manifest/bundle：`RSIFlow/seed_harness/`。
- 适配的 Task 组件和 production templates：`RSIFlow/upstream/RSIFlow_task/`。
- 部分官方评测脚本与 notices：`third_party/report_evaluators/`。
- 可选技能 seed：`RSIFlow/meta_skills/reviewed_seed.jsonl`，含历史 case/rule/incident，不包含独立评估分数。
- 离线测试和合成测试逻辑；它们不替代真实模型、数据或 GPU。

## 后续资产验收格式

每项资产至少记录：

1. 来源 URL 或提供方、许可证和使用限制。
2. 上游版本/revision/commit，本地相对路径和文件大小。
3. SHA256 或仓库规定的 checkpoint/files manifest。
4. 与 source/frozen manifest 的绑定关系。
5. 获取时间、获取命令的脱敏摘要和校验结果。
6. 是否为原始发布资产、同协议重构资产或合成 smoke 资产。

凭据只记录“是否已安全注入”，不记录内容。SCO 配置手册中的认证示例不得复制到本目录或网页。
