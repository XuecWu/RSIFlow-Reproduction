# 数据重构主 agent 复核 R1

独立 Kimi 在审查中指出 source manifest 的原始 scan 字段与 `prepare.repack` 不一致。主 agent 复核还发现检索任务不能从已打包记录构造。

处理：

- 新增 `pack_allocated_manifests()`，先经仓库 `materialize()` 生成 B1–B5 与 validation role packs，再合并 source training manifest，保留 ACE 多轮记录及 record_file/record_offset/record_length/record_sha256。
- 检索任务改从 allocate 前的原始 training manifests 构造，保留 JSONL/JSON array offsets；`build_search_index()` 与 `native_index()` 的 `search.sqlite.manifest.json` 契约由真实测试验证。
- 目录型数据集 inspection 改为逐文件 hash，支持 parquet 目录。
- 新增端到端测试：allocated packs 进入 repack、native index 链接 retrieval manifest、唯一 task ID。

验证：`python3 -m unittest tests.test_build_cohort -v`，8/8 通过。完整 3000/744 的真实数据构建尚未执行，不宣称 READY。
