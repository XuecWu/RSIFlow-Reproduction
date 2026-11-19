# DeepCoder 五轮最终交付验证

- 日期：2026-11-19（UTC）
- 代码基线：`5071b705273975c5ee2657ed8c21595fd84e8465` 加 Task 7 最终修复工作树 diff。
- 范围：离线测试、真实汇总重算、独立 scoped re-review、网站/静态/密钥形态和合并前 HTTP 检查。没有重跑真实五轮实验。

## 1. 完整 Python 套件

从 `.worktrees/five-round/RSIFlow` 执行：

```bash
PYTHONPATH="$PWD/runtime:$PWD" \
  /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests
```

最终结果：**1031 passed, 2 skipped in 723.43s (0:12:03)，exit 0**。

证据：`reproduction/audit/evidence/five-round-final-full-suite-20261119.log`，SHA256 `f8000a809872d55188f741ee255a60915b0d8d5ff54e910a11dbf684434eda61`。

第一次主 agent 尝试被 300 秒执行上限在约 41% 处终止，不是测试失败；上述结果来自随后以 1800 秒上限执行的完整重跑。

## 2. 专项与真实证据重算

- MODEL execute root/receipt 反例和合法恢复控制：**6 passed, 151 deselected**。
- selection 优先级、完整 activation inventory、retain/false-accept 独立反例及合法控制：**15 passed, 190 deselected**。
- 使用绝对 run/config/0008 cleanup 路径重新生成最终汇总：`completed_rounds=5`、`protocol_complete=true`、`launcher_exited=true`、`external_cleanup.ok=true`、`run_complete=true`、blockers 为空，B1–B5 结果逐项匹配。
- 新汇总与接受汇总 SHA256 均为 `3675d1fa07892afd879838d84bf253787322fa30058f7b12cfea3e4ed134d671`，没有改写真实实验证据。

## 3. 独立 scoped re-review

`deepcoder-five-round-final-re-review.md`：**Ready=Yes**；前次三个 Important 区域全部 ADDRESSED，本 fix diff 新引入 Critical=0、Important=0。复审同时确认后续用户授权的 tmux 占卡与 cleanup 时自动 restore pending/四卡空闲历史被明确区分。

## 4. 网站与静态检查

- `python3 -m unittest discover -s web/tests -v`：**11/11 PASS**。
- `node --check web/app.js`、JSON 解析和 `git diff --check`：PASS。
- 高置信密钥/私钥形态扫描：零命中；交付文件均小于 50 MiB。
- 合并前临时 HTTP 服务：`/`、CSS、JS、两份 JSON、REPRODUCTION、RESULTS、README、OPERATIONS、process 共 **10 个资源全部 HTTP 200**；五轮完成和 tmux 状态语义断言通过。

HTTP 证据：`reproduction/audit/evidence/five-round-premerge-http-20261119.json`。合并后的主工作树和公网复验另记主审计及 `web/OPERATIONS.md`。

## 5. 交付边界

固定同一批 16 个唯一 DeepCoder/TACO function 任务上的单域五轮工程跑通已经验证；五轮共 80 个 rollout positions，不是 80 个唯一任务。ARTIFACTS 是零新增 Qwen 调用的 Meta-assisted direct-submission 输出修复；B2 SFT 未激活，最终模型仍为基础 Qwen3-4B。每侧只有 6–8 个有效 submissions 进入 full verifier。没有正式五域 3000、held-out、694/744 或作者结果复现结论。
