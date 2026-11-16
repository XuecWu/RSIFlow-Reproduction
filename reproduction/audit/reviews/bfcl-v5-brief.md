# BFCL v5 数据协议迁移实施任务

工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight`
先读 `AGENTS.md` 和 `reproduction/audit/reviews/bfcl-v5-design.md`，按推荐方案 B 实施。用户已授权自主修复并继续 preflight。

实际资产在主工作树 `/data/wuxuecheng/Hierarchical_level/RSI-Flow` 下可读：v4 source manifests、allocated/frozen packs、pinned BFCL checkout 均存在；设计报告中“worktree 相对路径不存在”不是资产缺失。只读 v4，所有新输出必须版本化且此前不存在。

允许修改/新增：
- 新建单用途 `reproduction/scripts/migrate_bfcl_v5.py`
- 新建/修改现有 `reproduction/tests/` 中相关测试
- 新建 `reproduction/configs/train_4b_v5.json` 和 `reproduction/configs/validation_744_v5.json`
- 修正 `reproduction/tests/test_frozen_release.py` 中既有 BFCL repository-name 断言：不断言目录名含 `bfcl-ea13468`，改用 `native_layout`/`git rev-parse` 核对实际 pinned commit、isolated Python 与数据布局。先保留旧失败证据，再让测试验证真实契约而非路径字符串。
- 更新 `reproduction/REPRODUCTION.md`
- 新建 `reproduction/audit/reviews/bfcl-v5-report.md`
不得改 v4 文件、official evaluator/data、正式结果、JSONL 账本、生产 `validate.py` 或五轮运行配置 `train_4b.json`（v5 先独立验证，切换由 controller 决定）。

要求：
1. 严格 TDD：临时 fixture 先 RED，覆盖 official ID universe、19 替换、确定性、152 分母、disjoint、record offset/hash、旧 release 不变、候选不足/重复/篡改 fail closed、其他五源不变。
2. 从 pinned `ea13468` 官方 BFCL 数据选择真实完整行；保留 133 个有效项，确定性替换 19 个无效项。尽可能按被移除项 category 匹配；两条无 ID 项的未知 category 要显式记录选择规则，不能伪造 crosswalk。候选不得与现有 train/validation IDs/group/content 冲突。
3. 生成机器可读 migration receipt：old/new IDs、原因、category、official source file/offset/record hash、选择规则、disjoint、父 v4 hashes。不得看模型结果选样。
4. 构造新 source validation manifest 和版本化 allocated pack，复用现有 `prepare_600_744.py` 在新 v5 输出目录生成完整 3000/744 release；训练 3000 和其他五源记录 bytes/IDs 与 v4 保持一致，BFCL 仍 152、ACE 50、744 frozen/694 scored。保持现有标准 READY 不变，release=v5、migration receipt hash、父 v4 hashes 与 exposure 限制放在独立 migration receipt/报告，不扩展 prepare schema。
5. v5 全部 152 BFCL native IDs 必须属于 pinned official 集合；验证 runner queue 可接受，新的 `validation_sources.freeze/verify` 成功。真实生成仅写主工作树 Git 忽略的新版本目录，所有路径先 `test ! -e`，不得覆盖。设计预审明确指出 `evaluate_tool` 对 independent_validation 仍硬编码 50，本任务只关闭 v5 数据/ID/queue 预检，**不宣称最终 152 评分或完整 694 已解锁**；该 runner 协议冲突另行修复。
6. 运行新测试、reproduction 全套（含修正后的 pinned checkout 契约测试）、必要 RSIFlow 数据协议测试和实际 v5 验证；记录全部失败，不隐瞒。
7. 不 commit/push，不启动模型/API/SCO/五轮/SFT，不联网，不读取凭据。

完成后报告 RED/GREEN、migration receipt、v5 release/READY、测试和自审。若实际迁移过大或发现设计不成立，停止在明确 blocker，不伪造 v5 成功。
