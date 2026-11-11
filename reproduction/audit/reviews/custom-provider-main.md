# 自定义 Meta provider 配置记录

- `launch_meta.py` 支持 `codex_host_config` 与 `codex_model_catalog` 覆盖，默认 AutoDL 配置保持不变。
- API key 环境名由 base config 的 `meta.api_key_env` 选择；`OPENAI_API_KEY` 与 AutoDL key 均不转发给 controller 子进程。
- 本机配置使用 `gpt-6-astra`、自定义 OpenAI-compatible endpoint、Codex 0.153.4 和独立 JSON 文件，未修改上游默认 JSON。
- 聚焦测试 `python3 -m unittest tests.test_custom_provider -v`：3/3 通过。
- 真实 `/models`、Responses/Chat 和 Codex native dynamic tools 仍待私有凭据文件就绪后验证，当前不宣称 provider smoke 通过。
