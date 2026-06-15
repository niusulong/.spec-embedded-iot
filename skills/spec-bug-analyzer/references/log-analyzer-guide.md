# 日志分析脚本使用指南

脚本路径：`scripts/log_analyzer.py`（相对于本技能目录，执行时按技能加载给出的目录拼接）。

## 子命令速查

| 子命令 | 用途 | 关键参数 |
|--------|------|----------|
| `search` | 关键字搜索 + 上下文 | `-k` `--start-time` `--end-time` `-c` `--report` |
| `extract` | 提取日志片段到文件 | `--start-time` `--end-time` `--start-line` `--end-line` `-k` `-o` |
| `stats` | 关键字出现次数和时间分布 | `-k` `--start-time` `--end-time` |
| `compare` | 正常 vs 异常流程对比 | `normal` `abnormal` `-k` `--normal-start-time` `--abnormal-start-time` |

## compare 两种模式

### 不同文件对比

```bash
python scripts/log_analyzer.py compare \
  success.log fail.log -k "ssl" "handshake" "error"
```

### 同文件时间段对比

```bash
python scripts/log_analyzer.py compare \
  app.log app.log -k "ssl" "handshake" "error" \
  --normal-start-time "10:00:00.000" --normal-end-time "10:05:00.000" \
  --abnormal-start-time "10:10:00.000" --abnormal-end-time "10:15:00.000"
```

## 常用示例

```bash
# 搜索错误关键字，显示3行上下文
python scripts/log_analyzer.py search app.log \
  -k "ERROR" "fail" "timeout" -c 3

# 提取指定时间段的日志
python scripts/log_analyzer.py extract app.log \
  -o extracted.log --start-time "10:00:00.000" --end-time "10:05:00.000"

# 统计关键字分布
python scripts/log_analyzer.py stats app.log \
  -k "ssl" "tls" "mbedtls"

# 搜索并生成报告
python scripts/log_analyzer.py search app.log \
  -k "ERROR" --report .spec/issue/search-report.md
```
