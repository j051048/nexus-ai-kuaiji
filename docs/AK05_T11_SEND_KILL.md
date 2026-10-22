# AK05 T11：发送后强杀与恢复

## 实现

- `nexus_backend/tests/ak05_child.py`：真实子进程 runner；
- 子进程使用真实 SQLite 队列 + 本地 `AttachmentStore` + 合成 provider；
- provider `extract_invoice` 写入 marker 后阻塞；
- 父进程检测 marker 后 `kill()`；
- 再启动恢复子进程；
- 恢复进程不得再次调用 provider。

## 结果

```text
test_ak05_send_kill_v4.py
1 passed
```

断言：

- 发送前 provider marker 已出现；
- 子进程被强杀；
- 恢复进程 returncode 0；
- counter 文件不存在，即恢复未重复发送；
- `ocr_attempts.phase='sending'`、`status='running'` 保留。

## 限制

- 只覆盖 OCR；
- 未做费用/供应商 HTTP 日志双边核对；
- 未覆盖 PG；
- 使用本地 SQLite 和合成 provider，不代表生产模型/线上。
