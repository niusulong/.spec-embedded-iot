# QCX216 Dump Analyzer 回归测试

锁住 `full-analyze` 关键输出在脚本重构/优化后不漂移。两个 case 覆盖互补路径：

| Case | 场景 | 覆盖能力 |
|------|------|---------|
| `7056787126` | HardFault 崩在 vListInsert（链表悬空→堆损坏） | 异常帧还原 + objdump 反汇编 + 堆完整性 + 崩溃寄存器块状态 |
| `7031160371` | ASSERT OsaCreateFastSignal（OSA 信号池满） | excepInfoStore ASSERT 解析 + 触发点 + 调用链/中断源 |

## 运行

```bash
# 项目本地（dump 在 <repo>/.spec/logs/<id>/）
python evals/run_regression.py                    # 全部
python evals/run_regression.py --case 7056787126  # 单个
```

## 设计

- `run_regression.py` 对每个 case 跑 `full-analyze`，逐条正则断言校验输出。
- dump/elf 定位 `<repo>/.spec/logs/<id>/`；`.spec` 被 gitignore（Glob 找不到，脚本用文件系统直查）。
- 找不到 dump/elf 的 case 自动 **SKIP**（不报错）——源副本（无测试 dump）跑时全 SKIP，项目副本跑时 PASS。
- 退出码：有 FAIL/ERROR → 非 0；全 PASS/SKIP → 0。

## 加新 case

1. 把 dump+elf 放到 `<repo>/.spec/logs/<new_id>/`。
2. `regression.json` 加一条，断言用 `full-analyze` 输出的**稳定关键行**（地址/符号/判定），避免用易变的统计数字。
3. 跑 `run_regression.py --case <new_id>` 确认 PASS。
