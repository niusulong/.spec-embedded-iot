# QCX216 Dump 分析报告模板

复制本模板到 `.spec/bug/{工作项ID}_{问题描述}/Dump分析.md` 填写。
脚本 `full-analyze` 的输出可直接粘贴到「异常信息」「任务/栈」「地址解码」各节。

---

# {工作项ID} QCX216 死机分析：{一句话问题描述}

## 1. 概述
- **现象**：设备死机/重启的表象（如：运行约 N 分钟后死机、某操作后必死、偶发死机等）
- **死机类型**：ASSERT / HardFault / WDT / 静默复位
- **根因结论**：{一句话根因}

## 2. 环境信息
| 项 | 值 |
|----|----|
| 平台 | QCX216 / N706D（Cortex-M3 + FreeRTOS） |
| 固件版本 | {从 comdb.txt BuildInfo 或 nwy_build_info.h} |
| Dump 文件 | `RamDumpData_{时间戳}.bin`（{大小}） |
| 崩溃 ELF | `ap_at_command.elf`（编译时间 {date}） |
| 运行时长 | xTickCount = {值} ≈ {秒} |

## 3. 异常信息
（粘贴 `full-analyze` 的 `## Exception Store` 与 `Root-Cause Summary` 段）

```
Exception Type: ASSERT
Func    : ...
Line    : ...
Val     : ...
Context : interrupt / task
Trigger : 0x... -> 函数+偏移 [文件:行]
```

## 4. 任务与栈分析
（粘贴 `## FreeRTOS Tasks & Stack Analysis` 段）

- 当前任务：{name}（pxCurrentTCB = 0x...）
- 栈溢出：{有/无}（{哪个栈 OVERFLOW/HIGH RISK}）

## 5. 调用链 / 地址解码
| 地址 | 符号 | 源码位置 | 说明 |
|------|------|---------|------|
| 0x... | 函数+偏移 | 文件:行 | 中断入口 / 触发点 / ... |

## 6. 根因分析
基于上述证据推导根因：
1. 异常发生在 {中断/任务} 上下文，{函数} 在 {操作} 时触发 {assert/fault}；
2. Val={值} 表明 {...}；
3. （若源码在仓内）对照 `{文件}:{行}` 代码，assert 条件是 {...}；
4. （若源码是二进制库）对照头文件函数签名 + Val + 调用上下文推断 {...}。

## 7. 复现路径
- **前置条件**：{设备状态、网络、配置}
- **必要状态**：{如已注网、建立连接、运行某业务}
- **操作步骤**：
  1. ...
  2. ...
- **复现概率**：{必现 / 偶发 X% / 触发频率}
- （证据不足时标注「待验证」，列出已知条件与推测路径）

## 8. 修复建议
- {针对根因的修复方向}
- {若为协议栈二进制库内部 assert，需反馈 Unisoc / 升级库版本 / 规避触发条件}

## 9. 归档
- Dump + ELF：`.spec/bug/{工作项ID}_*/dump/`
- 知识库归档：（如适用）`spec-knowledge-archiver` 归档到平台 QCX216 目录
