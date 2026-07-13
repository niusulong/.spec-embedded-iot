---
name: spec-memory-leak-analyzer
description: >-
  嵌入式系统内存泄漏定位技能（call-stack 追踪法）：在内存分配/释放点埋点记录调用者返回地址，
  按地址配对分配与释放，精确定位"只分配不释放"的代码位置，再用 MAP 文件/addr2line 把地址映射回源码。
  当用户说"分析内存泄漏"、"内存泄漏定位"、"内存只增不减"、"可用堆持续下降"、"free heap 一直掉"、
  "malloc 返回 NULL"、"长期运行内存涨"、"leak"、"memory leak"、"定位哪里在漏内存"时使用——
  只要用户意图是"找出内存在哪里泄漏 / 为什么堆一直减少"，即使没有明说"内存泄漏"也应触发。
  适用周期性/偶发/大块泄漏，覆盖 GCC(__builtin_return_address)、ARMCC(__return_address)、
  MSVC(_ReturnAddress)、IAR 等工具链及 Unisoc/RDA/EC/ASR/Quectel 等平台。
  边界：若是 crash dump 解析（PC/LR/堆栈寄存器），用 spec-ec626-dump-analyzer 或 spec-asr1603-dump-analyzer；
  若是通用 bug 根因分析（多类日志综合），用 spec-bug-analyzer（它会在怀疑泄漏时转交本技能）。
  本技能聚焦"内存泄漏的埋点追踪与定位"，需要能重新编译烧录带追踪的固件。
version: 1.0
author: niusulong
---

## 核心原理

内存泄漏的本质：**分配次数 − 释放次数 > 0**。本技能的方法是在 `malloc/calloc/free` 处埋点，记录每次操作的**调用者返回地址（caller）**，按内存地址配对分配与释放——剩余未配对的就是泄漏块，其 caller 地址直接指向泄漏的代码位置。再用 MAP 文件 / addr2line 把地址映射回函数名和源码行号。

为什么用 caller 地址而不只数次数：次数只告诉你"漏了"，caller 地址告诉你"在哪漏"。

## 适用判断

| 信号 | 说明 |
|------|------|
| 可用堆随业务周期持续下降、不回升 | 周期性泄漏（本方法最强项） |
| `malloc`/`calloc` 返回 NULL，或 ASSERT 在分配处 | 堆已耗尽，需回溯泄漏源 |
| 长期运行后内存占用单调增长 | 慢泄漏 |

> 本方法要求**能重新编译烧录"带追踪的固件"**。若只有 crash dump、没有改固件加埋点的条件，先转 `spec-ec626-dump-analyzer`/`spec-asr1603-dump-analyzer` 看崩溃点。

## 前置条件：平台能否取调用者地址

埋点依赖获取调用者返回地址，不同工具链接口不同（完整兼容性表与验证方法见 `references/call-stack-tracking-guide.md`）：

| 工具链 | 接口 |
|--------|------|
| GCC (ARM/x86) | `__builtin_return_address(0)` |
| ARMCC | `__return_address()` |
| MSVC | `_ReturnAddress()`（需 `<intrin.h>`） |
| IAR | 内联汇编，需特殊处理 |

无法获取 caller 地址的平台，本方法不适用，退化为间接手法（堆趋势对比 / 类型标记配对，见参考文档附录）。

## 标准日志格式

埋点输出必须用固定格式，脚本才能解析（字段顺序无关，按 `key=value`）：

```
MEM_ALLOC: addr=0x3a4b0, size=128, caller=0x60102169
MEM_FREE:  addr=0x3a4b0, caller=0x60102169
MEM_HEAP:  total=67168, free=23008, used=44160
```

> addr/caller 小写十六进制，size 十进制字节。完整的 `mem_trace.h` 模板与各平台适配见参考文档。

## 执行流程

### Step 1：平台验证
确认目标平台支持取 caller 地址：写个小函数打印 `__builtin_return_address(0)`，用 MAP 文件验证地址落在调用函数内。见参考文档 §平台兼容性。

### Step 2：业务分析
摸清内存管理层级（业务层 → 统一接口层 → 平台抽象层 → 系统 malloc），用 grep 列出所有 alloc/free 调用点，识别**绕过统一接口的直接调用**和第三方库（mbedTLS 等）的内存接口。漏掉任何一条路径，配对就会出现"假泄漏"。

### Step 3：埋点实现
套用参考文档的 `mem_trace.h` 模板：提供 `mem_trace_malloc/calloc/free` + `mem_trace_heap`，内部调原始接口并按标准格式打日志；用 `MEM_TRACE_ENABLE` 宏开关控制启停；日志接口和 caller 接口用宏留出平台适配位（参考文档给了 Unisoc/RDA/Quectel 等的取值）。

### Step 4：接口替换
把业务与第三方库的 `malloc/calloc/free` 替换为追踪版本；第三方库用宏重定向（如 `#define mbedtls_calloc mem_trace_calloc`）；`realloc` 拆成 `MEM_FREE`+`MEM_ALLOC` 两条。务必覆盖全部路径。

### Step 5：数据采集
烧录带追踪的固件 → 跑业务流程（如 SSL 连接→收发→断开）→ 重复 **10+ 周期**（周期性泄漏需要多周期才看得出趋势）→ 关键节点查堆状态 → 导出完整 AP 日志 + 备份 MAP 文件（**版本必须与固件一致**）。

### Step 6：日志分析
运行脚本（路径相对本技能目录）：
```bash
python scripts/mem_leak_analyzer.py <日志文件> --map <MAP文件> --report leak-report.md
```
脚本会：按地址配对 alloc/free 找泄漏块 → 按 caller 聚合排名（泄漏次数 + 字节）→ 用 MAP 把 caller 地址映射到函数 → 输出堆趋势。完整用法见 `python scripts/mem_leak_analyzer.py --help`。

### Step 7：地址定位到源码
脚本给出 caller 地址→函数名后，进一步定位到行号：
- **MAP 文件**：找包含地址范围的符号
- **addr2line**：`arm-none-eabi-addr2line -e fw.elf -f <addr>` 直接出函数名+行号（最权威）
- **objdump**：`objdump -d fw.elf` 看反汇编上下文
- 命中 `.part.`/`.isra.` 后缀 = 被内联，查附近符号找真实调用者

## 诊断要点

- **配对结果大量"假泄漏"**：多半是 Step 4 没替换干净（仍有裸 malloc/free，或第三方库走了别的内存接口）。用 grep 复查；释放用了 `free()` 而分配用了追踪接口（或反之）也会错配。
- **地址映射不上**：MAP/ELF 与固件版本不一致；或编译优化（-O2）导致地址偏移——用 `-O0 -g` 调试版重测。
- **异步释放延迟被误判泄漏**：多跑几个周期看趋势是否持续，别只看单周期。
- **realloc 盲区**：realloc 既像 free 又像 malloc，埋点必须拆成两条记录，否则配对错乱。

## 参考文档

- `references/call-stack-tracking-guide.md` — 完整方法论：平台兼容性表、`mem_trace.h` 模板、各平台适配、业务分析、接口替换、地址定位、内联函数处理、局限性与替代手法
- `scripts/mem_leak_analyzer.py` — 日志分析脚本（配对 / caller 聚合 / MAP 映射 / 堆趋势 / 报告）
