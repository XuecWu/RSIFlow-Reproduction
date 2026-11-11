# 主 agent 会审复核与修复 R1

## 处理结果

- 非 UTF-8：subprocess 使用 errors=replace，新增真实 `0xff` 输出测试。
- 超时：audit_exec 支持 timeout-seconds，记录 TimeoutExpired、status=timeout、exit=124。
- 脱敏：补充 `--api-key VALUE`、key 赋值、Bearer、cpolar 等形式，额外字段递归脱敏。
- 结果状态：默认 unreviewed，显式枚举校验，只有调用者明确指定才记 main_verified。
- 证据：关键结果保存绝对路径和 SHA256；命令输出写入 audit/evidence/ 而非被忽略的 logs/。
- 并发：共享文件系统上的锁使用有界非阻塞重试，8 路并发 sequence 唯一。

## 验证

`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s reproduction/tests -v`：7/7 通过。

新增测试先观察到 5 项失败，修复后全部通过。未启动 GPU/API 实验，未读取真实凭据。
