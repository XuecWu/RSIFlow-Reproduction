# v5 真实评分 r2：四项 scored、2Wiki blocked

- 日期：2026-11-17
- 作业：`pt-bvyk7gi1`，SCO 终态 `FAILED`（smoke 有 blocked 项，不是平台排队失败）
- 输出：`reproduction/logs/model-scoring-v5-a100-20261117T122959Z/`
- 范围：54 条预声明 smoke 子集，不是完整 694/744，不是作者结果复现。

## 真实进展

- 四卡 Qwen 服务 `verified`。
- 总计 479 次 completed 本地模型调用。
- HumanEval+、MBPP+、HotpotQA-dev 和 BFCL-v3 均为 `scored`，每项有真实预测、官方结果与哈希。
- BFCL 50/50 native tasks、50/50 verified nonempty submissions、437 completed calls、52 attributed receipts，归属与请求哈希门全部通过。
- 2Wiki-dev 有非空预测和 6 次 completed calls，但官方评测进程 blocked，因此整体 smoke `incomplete`，不能声称五类评分全部通过。
- 清理成功：四服务停止、四卡显存释放、`cleanup.error=null`。

## 已得到的 smoke-only 分数

| Benchmark | 本分母 | 官方结果 |
|---|---:|---|
| HumanEval+ | 1 | `system_pass@1=0.0`，base/plus 均 fail |
| MBPP+ | 1 | `system_pass@1=0.0`，base/plus 均 fail |
| HotpotQA-dev | 1 | `EM=0.0`、`F1=0.0` |
| BFCL-v3 固定子集 | 50 | category macro `0.33184523809523814`；sample-weighted `0.74` |
| 2Wiki-dev | 1 | blocked，无有效分数，不能记模型零分 |

BFCL 数字只描述本次固定 50 条子集，不能外推为完整 BFCL 152 或跨 benchmark 聚合。`aggregate_score=null`、`no_complete_marker=true`。

## 2Wiki 根因证据

`scores/2wiki_dev/stderr.txt`：

```text
TypeError: 'str' object does not support item assignment
```

调用栈位于官方 `2wikimultihop_evaluate.py:120 -> normalize_evi():114`。当前 `subset/2wiki_dev.json` 中 `supporting_facts`、`evidences`、`context` 是 JSON 字符串；官方评测器直接读取 `dp['evidences']` 并按二维列表逐项规范化、写回。字符串被当作序列遍历后触发 item assignment 异常。

这是一项数据物化/官方输入类型适配问题，不是模型质量零分，也不是已证实的 GPU、进程限制或网络错误。修复必须保留 frozen 原始记录、54 条分母和官方脚本语义，不能根据模型答案换题或删除 evidence 指标。
