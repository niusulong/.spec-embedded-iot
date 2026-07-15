# UIS8850 ARM 蓝屏机制指南

UIS8850 / N706-STD (ARM Cortex-R + FreeRTOS) 的 panic 蓝屏机制。本指南解释
`osiPanic` → `udf` → 异常 → `gBlueScreenRegs` 的完整链路，以及如何解读
ARM 寄存器现场与 CPSR。

## 1. 蓝屏入口：osiPanic

8850 的软件 panic 统一走 `osiPanic`（`osi_blue_screen.c:39`）。它**不是**直接死循环，
而是用一条 `udf #255`（未定义指令）故意触发 ARM 未定义指令异常，由异常向量保存
完整现场后再死循环。这样能捕获到精确的寄存器快照。

反汇编（实测）：
```asm
osiPanic @0x001006b0:
  push {r3, lr}              ; 保存调用者 LR 到栈 (sp+4)
  mov  r3, lr                ; r3 = 调用者地址 (用于 trace)
  ldr  r2, [pc, #16]         ; 文件名/trace 参数
  ldr  r0, [pc, #16]
  blx  __osiTraceIdBasic_veneer   ; 记录 trace
  bl   osiProfileCode
  udf  #255                  ; 0xdeff —— 触发未定义指令异常
```

**关键推论**：
- `gBlueScreenRegs.PC` = osiPanic 内 `udf` 指令地址（如 0x1006c6）。看到 PC 落在
  osiPanic 范围内 → **软件主动 panic，根因在调用者**（不是硬件异常）。
- `gBlueScreenRegs.LR` 不可信（被 osiPanic/异常处理覆盖）。调用者 LR 在 osiPanic
  `push {r3,lr}` 的栈帧里 = `gBlueScreenRegs.SP + 4`。
- 同族：`osiPanicAt`(带位置)、`osiPanicPosix`(带 errno)，机制相同。

## 2. 蓝屏现场：gBlueScreenRegs

`gBlueScreenRegs`（@ PSRAM BSS，size≈444B）保存异常发生时的完整 ARM 寄存器现场。
结构从 DWARF 读（`struct_offsets("gBlueScreenRegs")`），**勿硬编码偏移**——不同编译
选项字段数会变。实测结构：

| 偏移 | 字段 | 说明 |
|---|---|---|
| +0x0 | `r[16]` | r0-r15（r13=sp, r14=lr, r15=pc） |
| +0x40 | `d[]` | VFP/NEON double 寄存器 |
| +0x140 | `cpsr` | 当前程序状态寄存器 |
| +0x144 | `fpscr` | 浮点状态 |
| +0x14c 起 | `sp_usr/lr_usr/sp_svc/lr_svc/sp_abt/lr_abt/sp_und/lr_und/sp_irq/lr_irq/sp_fiq/lr_fiq/spsr_*` | 各模式栈与保存的 SPSR |

读取要点：
- `r` 数组偏移从 DWARF 读（默认 0）。`sp = r[13] = base + r_off + 13*4`，
  `pc = r[15] = base + r_off + 15*4`。**注意 13*4=0x34，不是 0x20**（曾踩坑）。
- `cpsr` 偏移从 DWARF 读（默认 0x140）。
- 紧邻 `gBlueScreenAbortType`（@ gBlueScreenRegs - 4，u8）。

## 3. CPSR 解码

`cpsr` 32 位，关键字段：

| 位 | 字段 | 含义 |
|---|---|---|
| 31-28 | N Z C V | 条件标志 |
| 9 | E | 大小端（1=big） |
| 8 | A | 异步 abort 屏蔽 |
| 7 | I | IRQ 屏蔽（1=关） |
| 6 | F | FIQ 屏蔽（1=关） |
| 5 | T | Thumb 状态（1=Thumb） |
| 4-0 | M | CPU 模式 |

ARM 模式码：
| M | 模式 | 说明 |
|---|---|---|
| 0x10 | USR | 用户态 |
| 0x11 | FIQ | 快中断 |
| 0x12 | IRQ | 普通中断 |
| 0x13 | SVC | 管理模式（内核常用） |
| 0x17 | ABT | 数据/预取 abort |
| 0x1b | UND | 未定义指令（osiPanic 的 udf 进入此） |
| 0x1f | SYS | 系统模式 |

**蓝屏现场典型值**：`cpsr = 0x000b01bf` = SYS 模式 + Thumb + I=1(IRQ关) + A=1 + F=0(FIQ未关)。
- SYS/SVC 模式 = 任务级上下文（非中断）
- I=1 = osiPanic 关了 IRQ
- T=1 = Thumb 指令（8850 AP 全 Thumb）
- F=0 = FIQ 未关（8850 用 FIQ 做调试/异常通道，故保留）

若 cpsr 模式 = ABT/UND，说明是硬件异常（data/prefetch/undefined abort），需反汇编
`pc` 处崩溃指令。

## 4. gBlueScreenAbortType

u8，标在 `gBlueScreenRegs` 前 4 字节。常见值：
- `0xFE` = 软件断言（OS_ASSERT 失败主动 abort）
- `0xCA` = osiPanic 经 udf 触发的蓝屏 abort 码（本案例）
- 其他 = 异常码（结合 cpsr 模式判断）

abort type 在异常向量 / `osiBlueScreen` 里设置。`gBlueScreenAbortType` 的写入点
可通过搜 ELF `.text` 里引用其地址（`gBlueScreenAbortType` 地址作为 literal pool）
定位，常落在 `vectors.S` 的 `FIQ_Handler`。

## 5. 关键全局符号速查

| 符号 | 含义 |
|---|---|
| `gIsPanic` | =1 表示设备进入 panic（蓝屏抓 dump 状态） |
| `gBlueScreenAbortType` | abort 类型码（u8） |
| `gBlueScreenRegs` | ARM 蓝屏寄存器现场（444B） |
| `osiPanic` / `osiPanicAt` / `osiPanicPosix` | 软件 panic 入口（udf 触发） |
| `osiBlueScreen` | 蓝屏死循环主函数 |
| `pxCurrentTCB` | 当前任务 TCB 指针（FreeRTOS） |
| `gBuildRevision` | 当前固件版本串 |
| `gfupdateStat` | FOTA 更新状态（非0=进行中） |
| `g_CpMdVersion` | CP/Modem 固件 svn 版本号（如 0xed91=60817） |

## 6. FreeRTOS TCB 结构（DWARF 实测）

`tskTCB`（任务控制块），关键字段：

| 偏移 | 字段 | 说明 |
|---|---|---|
| +0x0 | `pxTopOfStack` | 当前栈顶（向下增长） |
| +0x4 | `xStateListItem` | 就绪/阻塞链表项 |
| +0x18 | `xEventListItem` | 事件链表项 |
| +0x2c | `uxPriority` | 优先级 |
| +0x30 | `pxStack` | **栈底指针**（栈缓冲区起始，magic 填充处） |
| +0x34 | `pcTaskName` | 任务名（16B） |
| +0x44 | `uxTCBNumber` | TCB 编号 |

栈布局：`pxStack`（栈底，低地址，填 `0xa5a5a5a5` magic）→ 向上到 `pxStack+size`
（初始栈顶）→ `pxTopOfStack` 向下增长。**正常 `pxTopOfStack >= pxStack`**；
若 `pxTopOfStack < pxStack` → 栈顶越过栈底边界 = **该任务自己栈溢出**。

详见 [[freertos-stack-overflow]]。
