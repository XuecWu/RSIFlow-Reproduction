# skip-ACE 主 agent 复核与修复 R1

Codex 报告 `reproduction/audit/reviews/skip-ace-codex-r1.md` 的三项实质问题均已处理：

1. `validation_skip.py` 对缺失 `task_metrics.json` 直接抛错，不再生成 metrics_sha256=None 的回执；成功用例验证 64 位摘要。
2. `round_validation.py` 改为按 settings/manifest 计算 expected、scored/total，并同步授权校验；旧 300 与当前 744 都适用。
3. `validate.py` 在读取完整缓存前调用 `reject_complete_cache_for_skip`，拒绝把 completed 结果复用为 skip-ACE。
4. CLI 帮助改为不绑定固定任务数。

验证：`python3 -m unittest tests.test_skip_ace_protocol -v`，6/6 通过，覆盖 694/744、250/300、缺失指标、异常 ACE 结果、完整缓存冲突和旧消费者 744 回执。

完整上游 pytest 仍待 Conda 依赖安装结束后运行，当前不宣称全量测试通过。
