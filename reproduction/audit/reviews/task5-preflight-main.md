# Task 5 离线 preflight 主 agent 记录

## 已验证

- `TaskAdapter._config()` 曾拒绝 `OpenAI/OPENAI_API_KEY`；`MetaBackendConfig` 增加显式 OpenAI 支持后，真实有效配置测试通过。
- `CodexOpenRouterBackend.validate()` 原 endpoint 注册表缺少 OpenAI，已加入固定 `http://token.acerobotics.com/v1`、固定模型 `gpt-6-astra` 和 `OPENAI_API_KEY`；不是任意 endpoint 或模型 fallback。
- 固定 Codex source `3d2ee51ca2d5db578f328aa75e20aa22c0197c9a` 已 checkout，worktree clean，`config.schema.json` 存在。修正了机器 catalog 的陈旧 SHA256。
- 使用离线 fixture 凭据和真实 binary/source/schema/catalog 的 backend 测试 4/4；local execution probe 用 fixture 隔离，未发 API 请求，不能替代真实 provider smoke。
- 4B launcher `--check` 通过，run name 改为 `eval694` 后 `prepare_600_744.py` 返回 `already_prepared`，v4 未改写。
- BFCL 使用固定 `ea13468e4423454d0c213704fb87cf7cb3990433` 和独立 `rsiflow-bfcl` 环境；基础包导入成功、NumPy=1.26.4、VERSION_PREFIX=BFCL_v3。生产环境依赖未降级。
- BFCL CLI 冷启动加载大量第三方模型类型，90 秒 preflight 不足；`check_native` 上限改为 900 秒，测试先失败后通过。真实评测 budget 未缩短。
- 第一次完整 CLI 导入在 649 秒暴露 `soundfile` 缺失；独立环境补装 `soundfile==0.14.0` 后，同一导入以 260.38 秒完成，`cli=True`。这证明 CLI 可导入，不等于 native isolation 或模型评分已通过。
- 四卡 smoke 脚本测试通过：真实 start/verify/短生成/finally stop 的调用契约 2/2；GPU 实跑仍待 SCO。
- 第一次真实服务在 600 秒内均未 ready，且日志为空；清理后为 `services_stopped`。新增 tokenizer/weights 阶段日志和 1800 秒冷启动上限，对应 `test_service_startup.py` 2/2 通过。第二次 `pt-jnjtat52` 记录到 torch=8.84s、torch+fastapi=9.90s，随后 transformers 仍在导入，不能误判为 GPU 权重加载慢。

## 数据审计保留意见

`data-audit-v4.json`：Code train/dev overlap=0，BFCL/HumanEval+/MBPP+ 对训练集精确匹配=0；EnvScaler train/heldout 共享 191 个环境定义，上游总状态为 `failed_overlap`。这是已批准同协议重构的环境复用差异，不能宣称完整独立性或作者原始队列。

## 仍阻断

- 原 SCO `pt-r6np9yvd` 已 SUCCEEDED，但容器 stdout 不可得；Landlock 未验证。新 `pt-texqw4a8` 将复查 Landlock 并执行四卡真实服务，目前排队中。
- 本机 `check_native` 明确失败为 `Native benchmark isolation unavailable`，不做无隔离回退。
- `assets/private/credentials.sh` 不存在；真实 `/models`、Responses/Chat 和 Codex native dynamic tools 未执行。
- BFCL CLI 完整冷启动已通过；五类最小真实 benchmark smoke 尚待完成，没有模型 benchmark 分数。
