# QCX216 / N706D 平台参考

本文档沉淀 QCX216（Neoway N706D，Unisoc ARM Cortex-M3 + FreeRTOS）死机 dump 分析所需的
全部平台知识。所有布局均从真实 dump（`RamDumpData_20260629_103016.bin`）+ 崩溃固件 ELF
（`ap_at_command.elf`）逆向验证。

## 目录
1. [平台概述](#1-平台概述)
2. [内存布局](#2-内存布局)
3. [Dump 文件格式](#3-dump-文件格式)
4. [excepInfoStore 异常存储结构](#4-excepinfoStore-异常存储结构)
5. [FreeRTOS TCB 布局](#5-freertos-tcb-布局)
6. [关键符号表](#6-关键符号表)
7. [ASSERT 文本格式](#7-assert-文本格式)
8. [根因决策树](#8-根因决策树)
9. [已知约束与坑](#9-已知约束与坑)

---

## 1. 平台概述

| 项 | 值 |
|----|----|
| SoC | QCX216（`NWY_SOC_MODEL "QCX216"`） |
| 模块 | Neoway N706D（`NWY_MODEL "N706D"`） |
| 内核 | ARM **Cortex-M3**（ARMv7-M，EABI5，Thumb-2，`e_machine=EM_ARM(0x28)`） |
| RTOS | **FreeRTOS**（`OS=freertos`、`PLAT/os/freertos`、`libfreertos.a`） |
| 架构 | AP/CP 双核（dump 分析 AP 核 `ap_at_command`） |
| 构建工具链 | GCC（`gccout/` 产物，ELF 带 `.symtab` + `.debug_line`） |
| 采集工具 | Unisoc DTools（`EPAT`/`SigLogger`/`UnilogViewer`/`comdb.txt`） |
| Neoway 框架 | 源码在 `PLAT/middleware/thirdparty/nwy/nwy_fwk_v2/NWY_FRAMEWORK`（见 [[framework-v2-source-location]]） |

**与 N706C 的区别**：N706C = UIS8852 (RISC-V + RT-Thread)；N706D = QCX216 (ARM Cortex-M3 + FreeRTOS)。
**同为 Cortex-M3 + FreeRTOS 但二进制库/异常存储机制与 EC616 不同，不能复用 EC 脚本。**

---

## 2. 内存布局

地址空间从 ELF section header 推导（`SHF_ALLOC | SHF_EXECINSTR` = 代码，`SHF_ALLOC & ~EXEC` = 数据）。
代码分散在多个区域（典型）：

| 区域 | 范围（示例） | 说明 |
|------|------------|------|
| 底层驱动代码 | `0x00000F4C-0x00007310` | flash.c / dma.c / clock.c / bsp_*.c（向量表在 0x0） |
| OSA/应用代码 | `0x004002C0-0x00421990` | osasig.c / lwip / 应用（与 RAM 同 0x004xxxxx 段，靠 section 属性区分代码/数据） |
| 大块 Flash | `0x00853838-0x00AA6248` | 可能是 bootloader / 文件系统镜像区（entry 常落在此） |
| RAM | `0x00421990-0x00463E68` 等 | FreeRTOS heap、TCB、任务栈、全局变量 |

> **不能凭地址前缀判断代码/数据**：`0x0041baa5`(OsaCreateFastSignal, 代码) 与
> `0x00423908`(ucHeap, RAM) 都在 `0x004xxxxx`。脚本 `ElfReader.is_code()` 用 section header 判断。

---

## 3. Dump 文件格式

`RamDumpData_*.bin` 是 **从物理地址 0x0 开始的统一地址空间转储**（Flash 代码 + RAM 拼接），
**偏移 == 物理地址**（base=0x0）。验证证据（dump `0x540000` 字节）：

| 物理地址(=偏移) | 符号 | dump 中的值 | 解读 |
|----------------|------|-----------|------|
| `0x00000000` | （向量表[0] 初始 SP） | `0x00457E98` | 栈顶指针，落在 RAM 高地址 |
| `0x00000004` | Reset_Handler | `0x000001BF` | Thumb 地址 |
| `0x0000000C` | HardFault_Handler | `0x0000026F` | 桩函数地址 |
| `0x004232F4` | excepInfoStore[0] | `0xEC112013` | 异常 magic（有效） |
| `0x00427EAC` | pxCurrentTCB | `0x00429874` | 指向 IDLE 任务 TCB |
| `0x004283A0` | xTickCount | `0x00032165` | 205413 tick ≈ 运行 205 秒 |

`DumpReader` 默认 base=0x0，直接按地址读写。

---

## 4. excepInfoStore 异常存储结构

异常发生时，HardFault/Assert handler 把现场写入 `excepInfoStore`（符号地址随版本变，
从 ELF 符号表取）。本次逆向布局（`@0x004232F4`）：

```
+0x00  magic1 = 0xEC112013        有效异常转储标志
+0x04  magic2 = 0xAA010129        辅助判别（0xAA 前缀）
+0x08 .. +0xBF  header / 寄存器快照 / 内存布局描述
+0xC0 ..         ASCII assert 字符串区
```

**寄存器快照区**（store 头部）散落异常时刻的调用地址，例如本次：
- `+0x34 = 0x00003306` → `MPDMA_interruptHandler+0x27`（中断入口，对应 "interrupt"）
- `+0x48 = 0x0041BB05` → `OsaCreateFastSignal+0x60`（assert 触发点）

精确的字段偏移会随固件版本变，因此解析采用「结构无关」策略：
扫描 store 头部 `+0x00~+0x140` 内落在 ELF 可执行 section 的 4 字节值作为调用链候选，
过滤 mapping 符号（`$d`/`$t`/`Image$`/`__sf`）与函数指针表（后段 `+0x300+`）噪音。

**类型判定**：
- store 内有 assert 文本（`Func:`/`Line:`/`Val:`）→ **ASSERT**
- magic 有效但无 assert 文本 → **HardFault**
- 无 magic → 静默复位 / WDT / 无异常数据

---

## 5. FreeRTOS TCB 布局

Cortex-M3 移植的 FreeRTOS TCB（用 IDLE 任务 TCB 验证）：

| 偏移 | 字段 | 说明 |
|------|------|------|
| `+0x00` | `pxTopOfStack` | 当前栈指针（任务被切出时的 SP，栈向下生长） |
| `+0x30` | `pxStack` | 栈底（低地址端） |
| `+0x34` | `pcTaskName[16]` | 任务名（ASCII） |

`pxCurrentTCB` 是**指针变量**，需先解引用拿到真实 TCB 地址：
`pxCurrentTCB` 符号地址 → 读 u32 → TCB 地址 → 再读 TCB 字段。

任务栈溢出判定（`vPortInitialiseStack` 用 `0xA5` 填充）：
- 栈底哨兵 `0xA5A5A5A5` 被覆盖 → **OVERFLOW 确认**
- 哨兵完整，使用率 >95% → HIGH RISK；>80% → WARNING
- 栈底前 256 字节无任何 `0xA5A5A5A5` → **NO SENTINEL**（MSP 主栈等不填哨兵，无法判定，非溢出）

---

## 6. 关键符号表

从崩溃 ELF `.symtab` 提取（地址随版本变，符号名稳定）：

| 符号 | 用途 |
|------|------|
| `excepInfoStore` | 异常存储主体（348 字节，解析入口） |
| `excepStep`/`excepStepDump`/`excepDumpEndFlag`/`excepCfgOption` | 异常转储步骤/配置（辅助） |
| **pxCurrentTCB** | FreeRTOS 当前任务 TCB 指针（需解引用） |
| **__StackTop/__StackLimit** | MSP 主栈范围（中断上下文 SP 在此，栈回溯终点） |
| `Mcu_HardFault_Handler`/`Mcu_Default_Handler` | 异常向量处理 |
| `IsHardFault` | HardFault 判定函数 |
| `apExceptCheckPoint`/`cpExceptCheckPoint` | AP/CP 异常检查点 |
| **pool_group** | TLSF 池表（`node[0].start/size`），**主堆遍历入口** |
| **pxTlsf** | TLSF `control_t` 控制块指针 |
| **gTotalHeapSize** | 主 TLSF 堆总量（≈ used+free+overhead） |
| ⚠️ `psSlp2FreeBytesRemaining` | **sleep/retention 另一堆**的空闲，**非主堆**，勿当主堆 free 误读 |
| ⚠️ `psSlp2MinimumEverFreeBytesRemaining` | 同上另一堆的历史最低，**非主堆金标准** |

> **主堆统计靠 TLSF 物理块遍历**（`qcx216_heap.walk_tlsf`），不靠 psSlp2 计数。
> MM_DEBUG 块头：`prev_phys_block(+0) / head_bound(+4,0xBEAFDEAD) / alloc_owner(+8) /
> size(+C,bit0=free,used低16=alloc高16=wanted)`；`alloc_owner = (funcPtr&0xFFFFFF)|(taskNum<<24)`
> 可按分配者归类内存。判断「不足 vs 碎片化」：used%≥95 不足；总 free 够但最大连续块小
> （碎片化>50%）则碎片化。Fast Signal 疑用独立池，其耗尽不走主堆统计。
| `xTickCount`/`xSchedulerRunning`/`uxCriticalNesting` | 调度状态 |
| **ramRstReason** | 复位原因（异常后可能被覆盖成 magic） |
| `gWdtDataBase`/`hibresetcnt`/`gPendingReset`/`gCmiAppWatchdogTimer` | WDT/复位辅助 |

**全任务枚举**：扫描 RAM，判据 = `+0x34` ASCII 名 + `+0x30/+0x00` 栈地址落在 RAM +
栈底 `0xA5A5A5A5` 哨兵。典型任务：`Ccm/Cemm/Cerrc(RRC)/Ceup`（协议栈）、`Uicc*`(SIM)、
`tcpip_thread`、`lfs`、`Ccio*`(串口收发)、`nwy_app_init`/`nwy_pm_task`、`IDLE`/`Tmr Svc`。

**调用链回溯**：从 excepInfoStore 提取 SP 候选（落在 MSP 范围的值），向 `__StackTop`
扫描栈里的代码地址（每 4 字节，`elf.is_code` 判定），按偏序输出（内层→外层）。
常见 QCX216 中断触发源：`XIC_IntHandler`(中断分发) → `ACIpcAlone0/1Isr`(AP↔CP IPC) →
`IpcC2AMsg2Errc`(CP→AP 消息到协议栈) → `MPDMA_interruptHandler`(DMA)。

**OSA Signal 内存池（`OsaCreate*Signal` assert 必查）**：`OsaCreateFastSignal` 等不走红 TLSF 堆，
而调 `OsaMemPoolIdAlloc(poolId, ...)` 从 **OSA 专用池**分配。池数据：
- `osaMemPoolDescList[3]` @0x425550，每池 24B 描述符：
  `u16(+0)=blksize, u16(+2)=total, u16(+4)=used计数, u32(+8)=base, u32(+C)=end, u32(+10)=freeHead(空闲链头), u32(+14)=tail`
- `gUpMemPoolBuf` @0x44EE08（实际池内存）
- 典型配置：pool[0] blksize=20/256槽, **pool[1] blksize=36/32槽**, pool[2] blksize=132/8槽

**poolId 动态选择**（`OsaCreateFastSignal` 内 `ITE LS` 条件块，capstone 还原）：
```
r8 = sigBodySize + 4
cmp r8, #36 ; r8<=36 (sigBodySize<=32) → poolId=1 (pool[1] 36B 小信号池)
            ; else                     → poolId=2 (pool[2] 132B Fast Signal 池)
```
**故 `OsaCreateFastSignal(sigBodySize=12)` → poolId=1**（不是 2）。⚠️ 曾因纯 Python 反汇编器
漏解 `ITE LS` 条件块（把 `MOVLS/MOVHI` 当普通 MOV）误判 poolId=2，看错池。**有 capstone 时
务必用它解条件指令。**

**满判定**（`OsaMemPoolIdAlloc`）：`freeHead = desc[poolId]+0x10`；`CBZ freeHead` → 若 NULL 返回 NULL。
即 **`desc[poolId]+0x10 == 0` 即满**（等价 `used(+4) >= total(+2)`）。
- 7031160371 案例：`pool[1]` freeHead=NULL、used=32=total → **满** → `OsaMemPoolIdAlloc(1)` 返回 NULL → assert@146。

**block 布局（实测，务必用此偏移读 slot；曾因布局搞反误判"链表损坏"）**：每块 stride = blksize + 4（pool[1]=36+4=**40**，不是 36）：
```
block+0  magic    u16 = 0xD5E9   (所有 block 固定标志，校验合法 block；★非 free 标记★)
block+2  poolId   u8
block+3  flag     u8  (1=free, 2=alloc)   ← free/alloc 真正判据
block+4  sigId    u16 (alloc 时) / next u32 (free 时)
block+6  sigBodyLen u16
block+8.. sigBody
```
- ⚠️ **0xD5E9 是 block magic（恒定），不是 free 标记**；free/alloc 看 `block+3` flag（1=free / 2=alloc）。
  曾误把 `block+0` magic 当 free 标记、把 `block+4`(sigId u16) 当 next 指针(u32)，得出"链表损坏"的错误结论——
  实际 desc 簿记(used/freeHead) 与物理 block flag 完全一致，无损坏。（7031160371 案例：32 块 flag 全=2 alloc。）
- **sigId 可由 IPC msgId 动态计算**（如 `IpcC2AMsg2Cms`: `sigId=(msgId-0x27BD)&0xFFFF`），故全代码段
  搜不到 sigId 立即数时，需反汇编创建函数看计算逻辑（7031160371：sigId=0x0949←msgId=0x3106）。

⚠️ **满/空判据看 desc 簿记（freeHead/used），不靠遍历 slot**。`OsaMemPoolIdAlloc`@0x41BCE8 完整反汇编
（capstone 即可，无需 ARM 工具链）：临界区 `CPSID I`@0x41BD46 后 `LDR R4,[desc+0x10]`(freeHead) →
`CBZ R4`(NULL 即满→返回 NULL)；`OsaMemPoolFree`@0x41BDC6 释放时 `STRB 1,[block+3]`(flag=free)、
double-free 检测 `block+3==1`→assert。MSR/CPS/DSB/ISB 屏障段可分段反汇编或用 CS_MODE_MCLASS。

**OSA signal 全链路分析（`OsaCreate*Signal` 池满时定位根因）**：assert 调用链（如 `IpcC2AMsg2Errc`）常是池满后的"受害者"，真正根因是堆积的 sigId。定位流程：
1. **池 + 堆积 sigId**：`scan-osa-pool` 自动输出满池 + 堆积 sigId 归类（`analyze_pool_slots` 按 `block+4` 读 sigId）。
2. **sigId 创建点定位**（sigId 不一定是代码常量！）：
   - 先搜全代码段 `MOVW #<sigId>` 立即数（立即数情况，如 `OsaCreateSignal(sigId=常量)`）；
   - **搜不到** → sigId 由 IPC msgId/变量**动态计算** → 反汇编 `OsaCreate*Signal` 的调用者（如 4 个 `IpcC2AMsg2*`），
     看计算公式（如 `IpcC2AMsg2Cms`: `sigId=(msgId-0x27BD)&0xFFFF`），反推 sigId←msgId；
   - 数据段扫 `sigId`(u16 LE)：仅出现在堆积块=运行时生成，出现在表/rodata=静态定义。
3. **销毁路径定位**：从创建处 `OsaSendSignal(taskId,...)` 得目标 taskId → 找任务 Entry 符号（如 `cmsTaskEntry`）
   → 反汇编其信号循环（**地址常超 dump 0x540000，用 `disasm` 的 ELF 回退**），找按 sigId 的销毁路由
   （`SUBW/CMP` 区间判定 → `OsaDestroyFastSignal`/`OsaDestroySignal`）。**taskId↔Entry 直接验证**：
   任务 Entry 的 `OsaReceiveSignal(taskId)` 应与发送方 taskId 一致（7031160371：两端均 taskId=7）。
   再反汇编 `OsaDestroyFastSignal` 确认它 `BL OsaMemPoolFree` 真回收 pool（销毁链完整才算"销毁正确"，
   排除销毁 bug 后才能定"速率失配"）。
4. **根因性质判定**：
   - 销毁路径**存在且正确** → **生产/消费速率失配**（生产=CP 投递/创建速率，消费=任务处理+销毁速率），非泄漏；
   - 销毁**漏调/路径缺失** → 泄漏。
- **案例 7031160371**：sigId=0x0949（←CP msgId=0x3106 CMS 消息，`IpcC2AMsg2Cms` 计算）堆积 31 个耗尽 pool[1]；
  `cmsTaskEntry` 销毁路由 sigId∈[0x946,0x952]→`OsaDestroyFastSignal` **正确** → 生产/消费速率失配，仅印度网络高频。

---

## 7. ASSERT 文本格式

excepInfoStore 内 assert 文本形如（null 作字段对齐填充，`\r\n` 分隔）：
```
interrupt  Func:OsaCreateFastSignal\r\n  Line:146\r\n  Val:0xc,0x0,0x0\r\n
```
- `Func`：触发 assert 的函数（多为 Unisoc OSA/协议栈 API，如 `OsaCreateFastSignal`）
- `Line`：assert 源码行（DWARF 可映射到 `osasig.c:146`）
- `Val`：assert 检查的表达式/参数值（如 `0xc,0x0,0x0`）
- 前缀 `interrupt`：发生在中断上下文（`OsaCreateIsrSignal`=`OsaCreateFastSignal` 别名）

**Val 字段含义对照**（OSA assert 的 Val 通常是触发函数的关键形参，需结合头文件签名推断）：
| Func | Val 字段 | 含义 |
|------|---------|------|
| `OsaCreateFastSignal(sigId, sigBodySize, signal)` | `0xc,0x0,0x0` → 第1个 `0xc`=**sigBodySize=12 字节** | 申请的信号体大小；该值过大或堆不足会导致分配失败 assert |
| `OsaCreateSignal` | 同上 | 信号 body 上限 `OSA_BIG_FAST_SIG_BODY_MAX_SIZE=128`，超限即 assert |
| 通用 | `<expr>,<a>,<b>` | 多为 assert 表达式当前值 + 边界值 |

推断方法：① DWARF 行号映射到 `.c:行`（即使源码不在仓，文件名:行号有效）；
② 查 `PLAT/prebuild/PLAT/inc/*.h` 头文件取函数签名；③ 反汇编触发点附近指令
（如 `CMP r1,#128` 暗示 128 上限检查）；④ 结合 heap 利用率（内存分配类 assert 看堆）。

`!!! AP assert !!!` 类日志也出现在 EPAT 的 `Communicatios.log`（UTF-16LE 编码），
含 `trigger assert, index <N>`（设备主动上报标记，可佐证非人为强制触发）。

---

## 8. 根因决策树

```
dump full-analyze
├─ Exception Type = ASSERT
│   ├─ Func 是 OSA API(OsaCreate*/OsaDestroy*) ?
│   │   → 协议栈在中断/任务里操作信号/资源失败；看 Val + 调用链上下文
│   ├─ context = interrupt ?
│   │   → 某中断处理函数(XXX_interruptHandler)里触发；查该 ISR 近期事件
│   └─ DWARF 映射到的 .c 文件在仓内 ?
│       → 读源码确认 assert 条件；不在仓内(二进制库) → 对照 osasig.h 等 header + Val 推断
├─ Exception Type = HardFault
│   ├─ 解读寄存器快照区代码地址(PC/LR) → 定位崩溃指令
│   ├─ fault status(见 cortex-m-exception-guide.md)：MemManage/BusFault/UsageFault
│   └─ 当前任务栈是否 OVERFLOW → 栈溢出导致的 HardFault
└─ Exception Type = Unknown
    └─ 查 EPAT Communicatios.log(UTF-16LE) + reset 原因；可能是 WDT/静默复位
```

---

## 9. 已知约束与坑

1. **OSA/协议栈源码不在仓内**：`PLAT/os/osa/src/osasig.c` 等只存在于构建环境，
   成品仓内只有 `PLAT/prebuild/PLAT/inc/*.h` 头文件 + 二进制库。DWARF 行号仍能给出
   `文件名:行号`，但无法看源码——需对照头文件函数签名 + assert `Val` 推断。
2. **EPAT 日志是 UTF-16LE**：`Communicatios.log` grep 前必须 `iconv -f UTF-16LE -t UTF-8`。
3. **ELF 版本必须匹配**：用 dump 同目录的崩溃固件 ELF，勿用 `gccout/` 当前编译产物。
4. **反汇编已内置（无需 capstone/objdump）**：`qcx216_disasm.py` 纯 Python 实现
   Thumb/Thumb-2 反汇编，覆盖调试高频指令（PUSH/POP/MOV/LDR/STR/B/BL/CBZ/LDR.W/…），
   罕见指令降级 `.short/.word`。`full-analyze` 自动反汇编触发点附近；`disasm` 子命令可反汇编任意地址。
5. **`__mcu_stack`(MSP)/部分任务栈不填 0xA5**：栈扫描显示 `NO SENTINEL` 是正常现象，
   不代表溢出。
6. **entry 指针落在 `0x008xxxxx`**：可能是 bootloader/镜像区入口，不影响 AP 固件分析。
