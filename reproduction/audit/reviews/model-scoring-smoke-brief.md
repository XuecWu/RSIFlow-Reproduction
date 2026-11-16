# 真实 Qwen 最小评分 smoke 实现任务

工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight`
分支：`reproduction/4b-preflight`

先读 `AGENTS.md` 和 `reproduction/audit/reviews/model-scoring-smoke-design.md`，后者是已完成的只读设计；严格保持其协议边界。用户已授权实现和后续 SCO preflight，但本任务不提交 SCO、不启动真实 GPU，真实执行由 controller 在审阅后进行。

允许修改/新增：
- 新建 `reproduction/scripts/model_scoring_smoke.py`
- 新建一个现有 pytest 体系下的 `RSIFlow/tests/test_model_scoring_smoke.py`
- 必要时修改 `reproduction/REPRODUCTION.md` 添加待执行命令和范围
- 新建 `reproduction/audit/reviews/model-scoring-smoke-report.md`
不得修改 `validate.py`、`tool_validation.py`、正式 manifest/config、JSONL 账本或其他生产代码。

目标：独立 smoke runner 组合现有接口，不调用完整 `validate()`：
1. 默认 `--plan` 只做资产、配置、确定性子集和 binding 校验；真实模型必须显式 `--execute` 且 output root 不存在。
2. 从原 `validation_744_v4/manifest.json` 确定性选择 HumanEval+/MBPP+/HotpotQA/2Wiki 各 1 个完整 task；BFCL 选择原顺序中前 50 个其 native IDs 全部存在于 pinned official `native_layout` 的完整 task。若不足 50 或任何 ID/family 不明确，fail closed。记录未被官方识别的 19 个 BFCL ID 为父 manifest 协议缺口，不能静默修复或宣称完整 694。
3. 保留原 manifest hash、子集 hash、选择规则、task/native IDs、record offsets/hash、spec/config/snapshot hash。写明确 smoke-only 初始 Task snapshot/state，不虚构已完成轮次。
4. 真实执行时经 `TaskAdapter._config()` 使用机器配置、四 endpoint，启动/验证四服务；Code/QA 复用 `generate_dynamic` + `evaluate_official`，BFCL 复用 `evaluate_tool`，ACE 不执行。显式 bwrap，真实 OPENAI/ACE API 均不需要。
5. 每任务必须有真实非空预测和已完成本地模型调用；模型质量错误可以成为官方 FAIL，但空预测、缺 ID、异常、分母不符、未完成 RPC、服务或隔离失败必须 incomplete/blocked，不能记模型零分。
6. BFCL 严格 50 个不同完整 task，使用 selected_tasks/selected_task_ids，检查真实非空 native submission 和本地模型调用证据；不触发 ACE user client。不得把 54 项 smoke 汇总成 694/744 或全 benchmark 准确率。
7. `try/finally` 只停止本 runner 拥有的服务，记录四卡释放；异常和 cleanup 状态都保留。输出不写 `complete.json`、不进入训练/Meta/候选接受，标记 held-out exposure/report-only。
8. 所有输出必须在 Git 忽略的新目录，原始正式数据只读。

TDD：
- 先写失败测试并实际观察 RED，覆盖确定性选择、50 完整 BFCL、无效 ID/family fail closed、spec/hash/原 manifest binding、空预测/错误 state hash/重复 ID、模型错误答案可 scored、ACE 不触发、阶段顺序和 cleanup。
- 用 temp dirs 和 monkeypatch 隔离 GPU/官方进程；测试合成结果不能作为真实证据。
- 最小实现，不重写官方 comparator，不引入依赖。
- 运行新测试、相关测试、完整 RSIFlow pytest 和实际资产 `--plan`；记录所有命令/输出。若完整套件失败必须列名。

不 commit/push，不读取凭据，不启动模型/API/SCO/五轮/SFT。完成后把 RED/GREEN、plan 回执、测试和自审写入报告并回复状态。
