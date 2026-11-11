# Herdr 审计通道启动记录

- 主 agent 已在当前 RSIFlow tab 新建三个 sibling panes，未改变其他项目窗格。
- `rsiflow-kimi`（`w1:pS`）：原生 `agent prompt` 返回 stalled，改用 pane 输入并完成最终回复 `RSIFLOW_KIMI_A17F`。
- `rsiflow-qwen`（`w1:pV`）：界面确认 Qwen Code 0.27.1、`qwen3.8-max (Alibaba ModelStudio)`，最终回复 `RSIFLOW_QWEN_B29Q`。
- `rsiflow-codex`（`w1:pT`）：启动参数为 gpt-6-astra/high/full access，但当前停在仓库 Trust 页面。按安全规范必须由用户本人确认，主 agent 未代点，尚未完成探针，不能计入审计。

后续 Kimi/Qwen 可执行只读审计；Codex 在用户确认 Trust 并完成探针后才能加入。所有正式审计仍需主 agent 回源复核。
