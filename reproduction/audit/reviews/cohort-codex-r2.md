# Codex 数据脚本复核 R2

Codex 对最新 `build_cohort.py` 与 `test_build_cohort.py` 的结论：未发现确定性的 record path、offset、hash 或 retrieval-source 缺陷会直接导致有效 3000/744 cohort 构建失败。

边界：该结论是静态只读复核，不证明真实公开资产有足够的 eligible/disjoint 记录，也不替代真实 cohort 构建和配额验证。测试与真实构建当时尚未运行。

主 agent 后续又补齐 QA 官方 `_id` 字段和脚本直接执行的仓库根路径测试，当前聚焦测试 10/10。
