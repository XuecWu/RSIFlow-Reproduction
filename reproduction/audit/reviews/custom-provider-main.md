# 自定义 Meta provider 配置记录

- `launch_meta.py` 支持 `codex_host_config` 与 `codex_model_catalog` 覆盖，默认 AutoDL 配置保持不变。
- API key 环境名由 base config 的 `meta.api_key_env` 选择；`OPENAI_API_KEY` 与 AutoDL key 均不转发给 controller 子进程。
- 本机配置使用 `gpt-6-astra`、自定义 OpenAI-compatible endpoint、Codex 0.153.4 和独立 JSON 文件，未修改上游默认 JSON。
- 初始聚焦测试 3/3 通过；补充 TaskAdapter 与真实 Codex backend 后，当前 `test_custom_provider.py` 为 4/4。
- 固定 Codex source checkout、schema 和 catalog hash 已验证；backend 使用离线 fixture 凭据，没有发真实 API 请求。
- 真实 `/models`、Responses/Chat 和 Codex native dynamic tools 仍待私有凭据文件就绪后验证，当前不宣称 provider smoke 通过。
