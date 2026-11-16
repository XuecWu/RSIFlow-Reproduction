# Preflight validation sources 修复报告（Task B）

状态：配置路径修复与来源快照 freeze/verify 完成；新增测试 RED→GREEN。reproduction 测试套件 `32 passed, 1 failed`，失败是既有 BFCL checkout 路径断言与当前配置不一致，未修改断言掩盖。未运行模型、API、SCO、五轮或最终评估。

## 修改范围

- `reproduction/configs/validation_744.json`：仅将 `tool_benchmarks.bfcl_v3.data_path` 和 `acebench.data_path` 指向 `assets/cohort/allocated/validation/{bfcl_v3,acebench}.jsonl`。
- `reproduction/tests/test_frozen_release.py`：添加一个回归测试，检查配置路径存在、精确指向 allocated validation、记录数分别为 152/50，并与 validation v4 中冻结 pack 字节一致。
- `reproduction/REPRODUCTION.md`：注明两个路径是冻结 allocated validation 输入、来源快照校验范围仍覆盖 official specs/native checkout/所有 benchmark data path，以及 BFCL 的 19 个 ID blocker 未被本修复改变。
- 本报告：按任务要求记录 TDD、测试、快照和自审。

未修改 `validate.py`、`tool_validation.py`、配置 schema、JSONL 账本、normalized 全量池、官方 evaluator 或 BFCL IDs；未 commit/push。

## TDD 证据

修复前先添加测试，运行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
  /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q \
  reproduction/tests/test_frozen_release.py -k tool_validation_inputs -p no:cacheprovider
```

RED：`1 failed, 4 deselected`。失败断言为配置中的 `/data/wuxuecheng/Hierarchical_level/RSI-Flow/assets/cohort/bfcl_v3/test.jsonl` 不存在。之后将 BFCL 与 ACE 路径改为 allocated validation 下指定文件。

同命令 GREEN：`1 passed, 4 deselected in 0.21s`。测试对每个文件验证配置路径与 allocated 位置一致、非空 JSONL 行数匹配 152/50、且文件 bytes 与 `RSIFlow/runtime/data/validation_744_v4/{bfcl_v3,acebench}.jsonl` 完全一致。该测试固定了源关系，不会因仅把路径换成任意存在文件而通过。

## reproduction 测试套件

首次以不完整导入路径运行时，4 个模块在 collection 阶段因找不到 `build_cohort`、`build_status`、`model_service_smoke`、`prepare_report_evaluators` 而报错；这不是测试结果，随后补上 `reproduction/scripts` 重跑：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/reproduction/scripts:$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
  /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q reproduction/tests -p no:cacheprovider
```

结果：`1 failed, 32 passed in 56.90s`。唯一失败为 `reproduction/tests/test_frozen_release.py::test_bfcl_evaluator_uses_pinned_source_and_isolated_python`，其断言要求 repository_root 字符串含 `bfcl-ea13468`，但当前值为 `/data/wuxuecheng/Hierarchical_level/RSI-Flow/assets/evaluators/bfcl-official/berkeley-function-call-leaderboard`。这是既有 pinned checkout 名称断言与当前机器配置目录的差异，不由本次 `data_path` 改动导致。按 brief 不改官方 evaluator/无关断言；报告该失败，不宣称全套通过。

## validation_sources 快照证据

在 Git 忽略目录 `reproduction/logs/validation-sources-task-b-20261116/` 对当前 `reproduction/configs/validation_744.json` 实际调用 `validation_sources.freeze(settings, path)`，随后调用 `verify(settings, path)` 成功。输出：

- Receipt：`reproduction/logs/validation-sources-task-b-20261116/validation_sources.json`
- SHA256：`3eb3ba4c111140b034bd78c6096ab089792d78da5041719d66add30214e52936`
- `git check-ignore -v` 命中 `.gitignore:28:**/logs/`。

冻结输入通过 `snapshot()` 覆盖 official specs、配置中每个非 tool benchmark 的 evaluator/data/ID（及如有 alias）文件、BFCL/ACE native checkout 下相关 Python/JSON/JSONL 文件，以及每个 tool benchmark 配置的 `data_path`。本次未删除或缩窄来源校验。快照只读公开评测资产并写入 ignored 回执，不运行模型/API/SCO。

## 自审与边界

- 配置只改两个指定 `data_path`，指向配额与冻结 pack 精确一致的 allocated 152/50 输入；normalized 全量池未动。
- 新测试确实会在旧不存在路径 RED，并在修复后 GREEN；测试同时锁定 allocated 路径与 frozen pack 字节关系。
- reproduction suite 的一项旧 BFCL repository-name 断言仍失败，已准确报告；没有通过改断言或 evaluator 来掩盖。
- BFCL 另有 19 个 manifest ID 不在官方 runner 的独立 blocker 仍存在；本任务没有更改 ID、官方数据或断言来绕过它。
- `git diff --check` 对本次三个允许修改文件通过。
- freeze/verify 只证明当前配置引用的外部文件快照可生成且可复核，不等价于 GPU/API 可用、BFCL 全队列兼容或完整 694 评估通过。
