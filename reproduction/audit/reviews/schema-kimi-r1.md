# CHANGES — 空白 Kimi 审计 R1

基线：当时 3 个单元测试通过，普通成功/失败可记录。

1. `audit_exec.py`：非 UTF-8 输出会触发 UnicodeDecodeError，后续 operation_record 不执行；缺少超时记录。
2. `auditlib.py`：`--api-key VALUE` 与额外 note 字段可绕过脱敏。
3. `record_result.py`：默认 main_verified 不能证明验证语义，且 review_status 没有枚举校验。
4. 证据相对路径未记录 cwd，换目录后无法稳定复算。
5. 复现确认：非 UTF-8、两种脱敏绕过、无关证据仍可 main_verified、非法 review status 均可出现。

以上由独立 Kimi 使用临时目录复现，未读取真实密钥或启动实验。主 agent 已逐项核对并进入修复。
