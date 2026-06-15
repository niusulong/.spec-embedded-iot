---
name: spec-ec-dump-analyzer
description: >
  EC 平台 (EigenComm ARM Cortex-M + FreeRTOS) crash dump 分析。
  适用于 EC626/EC626E/EC616，从构建输出文件自动推导芯片内存布局。
  支持 excep_store 解析、HardFault/ASSERT/WDT/FS Assert/XIC 区分、
  Fault Status 解码、栈溢出检测、FreeRTOS TCB 解析、调用链重建、
  LWIP memp 内存池耗尽+泄漏检测、FreeRTOS heap 分配追踪(trace_node)。
  支持 DWARF 源码行号映射（.debug_line）、objdump 反汇编上下文查找、
  ELF 符号表解析（.symtab）。
  触发词：分析dump、死机分析、EC dump、EC626崩溃、HardFault、
  栈溢出、crash dump、看门狗超时、WDT、设备重启、excep_store、
  内存池耗尽、memp_malloc fail、内存不足、LWIP OOM、内存泄漏、
  trace_node、大块未释放、堆分配追踪。
  仅适用于 EC 平台 (Cortex-M)，不适用于 ASR Cortex-R。
---

通过 RAM dump 文件分析 EC626/EC626E/EC616 死机原因。
脚本从 MAP 文件自动推导芯片配置，无需手动指定芯片型号。

## 输入要求

用户提供 dump 文件所在目录路径。

## 执行流程

> **分支概览**：
> - **ASSERT（reset_reason=2）**：Step 1→2→3(按需)→9→10
> - **HardFault（reset_reason=1）**：Step 1→2→3(按需)→4→5→6→7→8→9→10
> - **WDT（reset_reason=3）**：Step 1→2→5→9→10（排查 Default_Handler → 死锁 → 栈溢出）
> - **XIC（reset_reason=6）**：Step 1→2→10
> - **无 excep_store**：Step 1→2→10（可能是 Default_Handler 陷阱）

---

### Step 1：文件扫描

使用 Glob 扫描 dump 目录，按扩展名识别文件：

| 文件 | 用途 | 必需 |
|------|------|------|
| `RamDumpData_*.bin` | RAM 完整转储（272KB，部分带 PMUD 头部自动剥离） | 是 |
| `*.map` | GCC MAP 链接器符号映射表 | 推荐 |
| `*.symbols` | arm-none-eabi-nm -S 符号表（可替代 MAP） | 可选 |
| `*.elf` | ELF 可执行文件（符号表 + DWARF 调试信息） | 可选 |
| `*.txt` | objdump -d 反汇编输出（崩溃指令上下文） | 可选 |

脚本从 MAP `Memory Configuration` 段或 `.symbols` 符号文件推导 RAM_END、Flash 范围、`excep_store` 和 `pxCurrentTCB` 符号地址。`--map` 参数自动识别文件格式（`.map` / `.symbols`）。无构建输出文件时回退 EC626 默认值。

**PMUD dump 头部自动处理**：部分 EC 设备的 dump 文件以 "PMUD" 魔数开头（通常 0x48=72 字节头部），包含时间戳、芯片标识、RAM 大小等元数据。脚本自动检测并剥离 PMUD 头部，确保文件偏移 == RAM 地址，无需手动处理。输出 `[PMUD] Dump header detected (72 bytes), stripped` 表示已检测并剥离。裸 dump 文件（无 PMUD 头部）自动跳过。

### Step 2：运行 full-analyze（核心步骤）

```bash
SKILL_DIR="<Base directory for this skill>"
python "scripts/ec_dump_analyzer.py" full-analyze <dump_bin> --map <map_file>
```

`full-analyze` 自动执行：芯片配置推导 → excep_store 定位 → 异常类型识别 → PC/LR 解析 → 寄存器输出 → 任务上下文 → 栈溢出扫描 → LWIP memp 池扫描 → heap 分配追踪 → 调用链重建 → 根因结论。`--map` 参数同时支持 `.map` 和 `.symbols` 文件。无构建输出文件时回退 EC626 默认值。

**增强输出**（需 `--elf` 和/或 `--disasm`）：
- `--elf`：崩溃地址自动映射到源码文件名+行号（如 `PC = 0x0087AF3C -> pvPortMallocEC [heap_6.c:101]`）
- `--disasm`：崩溃点和调用链地址输出前后各 5 条 ARM Thumb-2 反汇编指令
- `--map` 也支持 `.elf` 文件（通过 ELF `.symtab` 提取符号，需 `pyelftools`）

**内部原理**（详见 `references/ec626-platform-reference.md`）：
- **PMUD header 自动剥离**：检测 dump 开头 "PMUD" 魔数 → 从 offset 0x28 读取 RAM 大小 → 计算 header = filesize - ramsize → 剥离头部
- 从 `RAM_END - 固定偏移` 读取 reset_reason、magic、store_ptr
- 三级定位 excep_store：MAP 符号 → store_ptr 间接 → 全量扫描
- 从 pxCurrentTCB 解引用获取 TCB，读取任务名和栈范围
- trace_node 布局自动检测：采样前 20 条，对比 V1/V2 有效条目数，取更优版本

### Step 3：按需补充解析

`full-analyze` 已覆盖全部流程，以下命令仅在特殊场景下使用：

```bash
# 单独解析 excep_store（如需确认结构体原始字段）
python "scripts/ec_dump_analyzer.py" parse-excep <dump_bin> --map <map_file>

# 解析额外地址（如调用链中有未解析的地址）
python "scripts/ec_dump_analyzer.py" resolve <addr1> <addr2> ... --map <map_file> --elf <elf_file> --disasm <txt_file>
```

### Step 4：异常类型路由 + HardFault 解码

根据 `full-analyze` 输出的异常类型选择分析方向：

| ec_start_flag | reset_reason | 类型 | 分析重点 |
|---|---|---|---|
| 0xA2A0A1A3 | 2 | **ASSERT** | PC+5 定位；task_name 为空需从 TCB 读；检查 R1/R2/R3 匹配 FS_ASSERT_MAGIC |
| 0xA2A0A1A3 | 3 | **WDT** | excep_store 有数据但根因不是 ASSERT。排查 Default_Handler → 死锁 → 关中断 → 栈溢出 |
| 0xA2A0A1A3 | 6 | **XIC** | 中断风暴，检查 ISR 卡死/重入 |
| 0xF2F0F1F3 | 1 | **HardFault** | 解码 HFSR/MFSR/BFSR/UFSR → `references/cortex-m-exception-guide.md`；task_name 有效 |
| (无) | 3 | **Default_Handler** | MemManage/BusFault/UsageFault → 无限循环 → WDT |
| (无) | 0 | **无崩溃数据** | 检查是否静默复位模式 |

完整 flag 组合见 `references/ec626-platform-reference.md` §3。

19 种完整分析模式见 `references/crash-analysis-patterns.md`。

### Step 5：栈分析

```bash
python "scripts/ec_dump_analyzer.py" scan-stacks <dump_bin>
```

栈溢出判定：

| 条件 | 判定 |
|------|------|
| 栈底 0xA5A5A5A5 被覆盖 | **栈溢出确认** |
| 栈底 0xA5 完整，使用率 > 95% | **栈溢出高风险** |
| 栈底 0xA5 完整，使用率 < 80% | **排除栈溢出** |

当前任务栈从 TCB 读取（pxTopOfStack=TCB+0x00, pxStack=TCB+0x30, 任务名=TCB+0x34）。`pxCurrentTCB` 是指针变量，需先解引用获取实际 TCB 地址。

### Step 6：LWIP Memp Pool 扫描 + 泄漏检测

当死机涉及网络/TCP/LWIP 代码时，检查 LWIP 内存池耗尽和泄漏：

```bash
# 基本扫描
python "scripts/ec_dump_analyzer.py" scan-memp <dump_bin> --map <map_file>

# 使用 ELF 精确读取元素大小/数量（推荐）
python "scripts/ec_dump_analyzer.py" scan-memp <dump_bin> --map <map_file> --elf <elf_file>

# 详细模式 / 自定义阈值
python "scripts/ec_dump_analyzer.py" scan-memp <dump_bin> --map <map_file> --elf <elf_file> --verbose --util-threshold 70
```

内存池状态判定：

| 条件 | 判定 |
|------|------|
| memp_tab_X = NULL (0x00000000) | **池耗尽** — 0 个空闲元素 |
| 利用率 ≥ 阈值（默认 80%） | **HIGH** — 资源紧张 |
| 利用率 < 阈值 | OK |

泄漏检测判定：

| 条件 | 判定 |
|------|------|
| 单一已识别持有者占 ≥50% 元素 | **Likely LEAK** |
| ≤3 个已识别持有者占 ≥80% 元素 | **Likely LEAK** |
| 仅 `<no flash ptr>` 持有者 | **无法判定** — 元素首字为 RAM 指针（链表等），非泄漏证据 |
| 全部池同时 100% 耗尽 | **系统性问题** — 不太可能是单一模块泄漏，检查共同根因 |

**重要注意事项**（分析 memp 池时必须考虑）：

1. **memp 池与 heap 独立**：`MEMP_MEM_MALLOC=0` 时，LWIP memp 池使用独立 BSS 静态内存，与 FreeRTOS heap（heap_6.c）完全分离。memp 池耗尽**不会**导致 `pvPortMallocEC` 返回 NULL。如果死机在 `pvPortMallocEC`（heap 分配失败），根因是 **heap 泄漏**而非 memp 池耗尽。

2. **memp 池是系统公用资源**：UDP_PCB、NETCONN、PBUF 等被所有网络模块共享。一个 UDP 应用（如 COAP 用 1 个 socket）只会消耗 UDP_PCB×1 + NETCONN×1，不可能耗尽全部 17 个池。TCP_PCB、DNS_API_MSG 等是非 UDP 模块使用的。

3. **全部池同时耗尽 ≠ 单一泄漏**：当 ALL 池同时 100% 时，更可能是系统性问题（LWIP 线程阻塞、全局资源管理异常等），而非每个池都有独立泄漏。此时应关注 heap 泄漏分析（trace_node、TLSF 利用率）而非 memp 池。

4. **`<no flash ptr>` 不等于泄漏**：持有者分析查看已分配元素的第一个 4 字节是否为 Flash 代码指针。当结果显示 `<no flash ptr>` 时，只说明首字是 RAM 地址（如链表 next 指针），并不代表该元素泄漏。不应仅凭此判定 "Likely LEAK"。

池按协议分组（辅助归因）：

| 分组 | 池名 | 典型消耗者 |
|------|------|-----------|
| **udp** | UDP_PCB, NETCONN, NETBUF, PBUF | UDP socket（COAP、MQTT 等） |
| **tcp** | TCP_PCB, TCP_PCB_LISTEN, TCP_SEG | TCP 连接（HTTP、MQTT 等） |
| **dns** | DNS_API_MSG, NETDB | DNS 查询（lwip_getaddrinfo） |
| **api** | API_MSG, TCPIP_MSG_API, TCPIP_MSG_INPKT, SOCKET_SETGETSOCKOPT_DATA | socket API 调用（所有协议共享） |
| **ip** | REASSDATA, FRAG_PBUF, IP6_REASSDATA, ND6_QUEUE | IP 层分片/重组 |

实现原理见 `references/ec-heap-memp-details.md` §1。

### Step 7：Heap Memory Trace（trace_node 分配追踪）

检查 FreeRTOS 堆内存分配追踪记录，识别未释放的大块内存和可疑泄漏：

```bash
# 基本扫描（Top 20 大块未释放 + 按任务/调用者分组 + 泄漏指标）
python "scripts/ec_dump_analyzer.py" scan-heap <dump_bin> --map <map_file>

# 详细模式（显示所有 active trace_node 条目）
python "scripts/ec_dump_analyzer.py" scan-heap <dump_bin> --map <map_file> --verbose
```

关键判定：

| 条件 | 判定 |
|------|------|
| free_node = NULL + 所有节点 active | **trace_node 满** — 可能存在未追踪的分配 |
| 同 caller+task ≥3 块 ≥64B | **可疑泄漏指标** |
| 单块 > 1KB | **大块未释放** — 值得关注 |

`mm_trace_node` 双布局（V1/V2）及自动检测原理见 `references/ec-heap-memp-details.md` §2。

### Step 8：TLSF Heap Utilization（堆整体利用率）

堆利用率判定：

| 条件 | 判定 |
|------|------|
| 利用率 ≥ 95% | **CRITICAL** — 堆严重不足 |
| 利用率 ≥ 80% | **HIGH** — 堆资源紧张 |
| 利用率 < 80% | **OK** — 堆使用正常 |

`full-analyze` 自动输出 `## TLSF Heap Utilization`，根因总结中区分"堆耗尽"vs"memp 池耗尽"。TLSF 块头布局和 size 编码见 `references/ec-heap-memp-details.md` §3。

> **关键区分**：当 crash 在 `pvPortMallocEC`（heap malloc 失败）时，根因是 **heap 内存耗尽**。LWIP memp 池耗尽是独立的共发现象（memp 使用独立 BSS 内存，不影响 heap）。根因分析应聚焦 heap 泄漏源（trace_node、malloc 调用方追踪），而非 memp 池状态。

### Step 9：根因定位

按决策树定位根因 → `references/crash-analysis-patterns.md` 末尾 "Complete Crash-to-Dump Decision Tree"。

### Step 10：报告归档

**报告路径**：`.spec/bug/{工作项ID}_{问题描述}/Dump分析.md`

**使用模板**：`references/ec-dump-report-template.md`

**Dump 文件归档**：将 dump 文件复制到 `.spec/bug/{工作项ID}_{问题描述}/dump/` 目录。

**确认工作项 ID**：如果用户未提供工作项 ID，必须询问用户获取。

**合并归档**：生成报告前检查 `.spec/bug/` 下是否已有同工作项 ID 的目录（`{工作项ID}_*`）。如果已存在，复用该目录归档。

**问题复现路径**：找到根因后必须输出复现路径，包含前置条件、必要状态和操作步骤。概率性问题额外标注复现概率和触发频率。证据不足以推导完整复现路径时，输出已知条件和推测路径，并标注"待验证"。

```bash
mkdir -p ".spec/bug/{工作项ID}_{问题描述}/dump"
cp <dump目录>/{RamDumpData_*.bin,*.map,*.elf} ".spec/bug/{工作项ID}_{问题描述}/dump/"
```

## 脚本速查

```bash
SKILL_DIR="<Base directory for this skill>"
python "scripts/ec_dump_analyzer.py" <subcommand> [options]
```

| 子命令 | 用途 |
|--------|------|
| `full-analyze <dump> --map <map> [--elf <elf>]` | 一键全流程分析（含 memp 泄漏检测 + heap 追踪） |
| `parse-excep <dump> --map <map>` | 解析 excep_store |
| `resolve <addr>... --map <map>` | 符号解析（支持 .map / .symbols） |
| `resolve <addr>... --map <map> --elf <elf> --disasm <txt>` | 符号+源码行号+反汇编上下文 |
| `scan-stacks <dump>` | 全任务栈溢出扫描 |
| `scan-memp <dump> --map <map> [--elf <elf>]` | LWIP memp 池耗尽+泄漏检测 |
| `scan-heap <dump> --map <map> [-v]` | FreeRTOS heap 分析：TLSF 利用率 + trace_node 追踪 |

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--map <file>` | MAP 或 `.symbols` 文件（**推荐**，自动识别格式） | 无 |
| `--elf <file>` | ELF 文件（源码行号映射 + memp 精确分析） | 无 |
| `--disasm <file>` | objdump -d 反汇编输出文件（崩溃指令上下文） | 无 |
| `--store-addr <addr>` | 手动 excep_store 地址 | 三级自动检测 |
| `--tcb-addr <addr>` | 手动 pxCurrentTCB 地址 | 从 MAP 自动定位 |
| `--flash-start/end` | 手动 Flash 范围 | 从 MAP 自动设置 |
| `--util-threshold <N>` | HIGH 利用率阈值（%） | 80 |

Windows Python 路径：`/c/Users/20220715012/AppData/Local/Programs/Python/Python312/python`

## 依赖

| 依赖 | 安装 | 用途 |
|------|------|------|
| Python 3.8+ | 系统 Python | 必需 |
| `pyelftools` | `pip install pyelftools` | ELF 符号表（`--map .elf`）+ 源码行号映射（`--elf`）。无此库时 ELF 相关功能自动降级 |

## 参考文档

- `references/ec-dump-report-template.md` — EC 平台结构化崩溃分析报告模板
- `references/ec626-platform-reference.md` — 内存布局、复位原因枚举、excep_store 结构、异常处理架构、WDT 流程
- `references/cortex-m-exception-guide.md` — Cortex-M 异常类型、Fault Status 解码、EXC_RETURN、xPSR
- `references/crash-analysis-patterns.md` — 19 种分析模式 + 完整根因决策树
- `references/ec-heap-memp-details.md` — LWIP memp 扫描原理、trace_node 双布局、TLSF 块头结构
