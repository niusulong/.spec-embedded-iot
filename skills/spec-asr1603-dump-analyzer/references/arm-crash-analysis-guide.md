# ARM 崩溃地址解码指南

## 1. ARM 异常类型与寄存器

| 异常类型 | 说明 | 关键寄存器 |
|----------|------|-----------|
| DataAbort | 数据访问异常（读写非法地址） | PC, LR, DFAR, DFSR |
| PrefetchAbort | 指令预取异常（执行非法地址） | PC, LR, IFAR, IFSR |
| HardFault | 硬件故障（综合异常） | PC, LR, HFSR, CFSR |
| MemManage | 内存管理异常（MPU 违规） | PC, LR, MMFAR, MMFSR |
| BusFault | 总线故障 | PC, LR, BFAR, BFSR |

### 1.1 关键寄存器含义

| 寄存器 | 含义 | 用途 |
|--------|------|------|
| PC | 异常发生时的程序计数器 | 定位崩溃指令地址 |
| LR | 异常发生时的链接寄存器 | 定位调用者函数（Thumb 模式下 bit0=1） |
| SP | 异常发生时的栈指针 | 判断栈溢出，解析栈回溯 |
| DFAR | Data Abort Fault Address | 实际触发异常的数据地址 |
| DFSR | Data Abort Fault Status | 异常状态码（精确类型） |

### 1.2 Thumb 地址约定

ARM Thumb 模式下函数地址 bit0=1，实际代码地址需 `addr & ~1`：

```
MAP 文件中:  nwy_fota_get_reboot_count 0x7e88003d
实际代码地址: 0x7e88003c (bit0 清除)
崩溃 PC:     0x7e880040
偏移:        0x7e880040 - 0x7e88003c = 4 bytes
```

## 2. MAP 文件地址解码流程

### 2.1 解码步骤

1. 从崩溃 dump 提取 PC/LR/SP 值
2. PC 的 bit0 清除得到实际代码地址：`code_addr = PC & ~1`
3. 在 MAP 文件中搜索包含 `code_addr` 的函数
4. 计算偏移：`offset = code_addr - func_start_addr`
5. LR 解码同理，定位调用者函数

### 2.2 MAP 文件格式

ASR SDK MAP 文件典型格式（ARM RVCT/ARMCC 编译器输出）：

```
function_name    0xADDR    Thumb Code    SIZE    obj.o(.text)
```

解析规则：
- 函数名可能有前缀（如 `nwy_`、`fota_` 等）
- `Thumb Code` 表示 Thumb 指令集，`ARM Code` 表示 ARM 指令集
- SIZE 为函数体大小（字节）

### 2.3 快速手动查找方法

```bash
# 搜索 PC 地址附近的函数
grep -E "0x7e88" firmware.map | grep "Code"

# 搜索特定函数
grep "nwy_fota_get_reboot_count" firmware.map
```

## 3. AXF 反汇编流程

### 3.1 什么是 AXF 文件

AXF 是 ARM RVCT/ARMCC 编译器输出的可执行文件，本质是 ELF 格式（带有 ARM 特定的段属性）。包含：
- 完整的代码段（.text）
- 调试信息（如果编译时加了 -g）
- 符号表
- 地址映射

### 3.2 反汇编方法

当没有 `arm-none-eabi-objdump` 等 ARM 工具链时，使用 Python 解析 ELF：

1. **解析 ELF 头**：确定 32/64 位、大小端
2. **解析 Section Headers**：找到代码段的文件偏移和加载地址
3. **定位目标地址**：计算 `file_offset = section_offset + (target_addr - section_addr)`
4. **读取原始字节**：按 Thumb/ARM 指令宽度读取
5. **解码指令**：Thumb-16 (2字节) 或 Thumb-2 (4字节) 或 ARM (4字节)

### 3.3 关键 ELF 结构

```
ELF Header (52 bytes for 32-bit):
  +0x00: e_ident (magic + class + endian)
  +0x10: e_type
  +0x12: e_machine
  +0x20: e_shoff (section header table offset)
  +0x2E: e_shentsize
  +0x30: e_shnum
  +0x32: e_shstrndx

Section Header (40 bytes for 32-bit):
  +0x00: sh_name
  +0x04: sh_type
  +0x0C: sh_addr (load address)
  +0x10: sh_offset (file offset)
  +0x14: sh_size
```

## 4. 崩溃指令模式识别

### 4.1 常见崩溃指令

| 指令 | 含义 | 可能根因 |
|------|------|---------|
| `str Rt, [Rn, #imm]` | 写内存 | 栈溢出/无效指针/MPU违规 |
| `ldr Rt, [Rn, #imm]` | 读内存 | 空指针/野指针/已释放内存 |
| `ldmia Rn!, {regs}` | 批量加载（pop） | 栈破坏/返回地址被覆盖 |
| `stmia Rn!, {regs}` | 批量存储（push） | 栈溢出 |
| `blx Rm` | 函数调用 | 函数指针损坏 |

### 4.2 栈溢出特征

```
push {r2, r3, r4, lr}   ; SP -= 16
movs r0, #0
str r0, [sp, #0]        ; ← 如果 push 成功但 str 失败
```

**矛盾分析**：如果 `push` 成功（写入 [SP-16]~[SP-4]），则 `str r0, [sp, #0]`（写入 [新SP]）理应也成功，因为访问的是同一区域。此时应考虑：
- SP 被其他代码路径破坏
- malloc 分配的栈被相邻堆块溢出破坏
- 中断在 push 和 str 之间修改了内存映射

### 4.3 空指针访问特征

```
ldr r0, [sp, #4]        ; 加载一个指针
ldr r1, [r0, #0]        ; ← 通过空指针访问，触发 DataAbort
```

PC 在 `ldr r1, [r0, #0]`，检查 DFAR 是否接近 0x00000000。

## 5. FAULT_STATUS (DFSR) 寄存器解码

ASR1603 使用 Cortex-R5 PMSAv7 MPU 模式。DFSR 解码公式和完整 FSC 编码表详见 **`arm-pmsav7-dfsr-reference.md`**。

**快速参考**：
- FSC=0x0D = Permission fault (MPU)，PC 指向故障指令，DFAR 有效
- FSC=0x16 = 异步外部中止，PC 不指向故障指令，DFAR 不可靠
- WnR 在 bit[11]，仅同步异常有效
- ExT=1 表示故障与外部总线相关（PSRAM/Flash/DMA）
PMSAv7 中 ExT 在 bit[12]，不是 bit[8]（bit[8] 为 UNK/SBZP）。

### 5.1 典型值速查

| DFSR 值 | 二进制 | PMSAv7 解读 |
|---------|--------|------------|
| **0x80D** | 0b 1000_0000_1101 | **bit[11]=1 WnR(写), FSC=0x0D Permission fault** |
| 0xD | 0b 0000_0000_1101 | WnR=0(读), FSC=0x0D Permission fault |
| 0x16 | 0b 0000_0001_0110 | FSC=0x16 Asynchronous External Abort |
| 0x1016 | 0b 1_0000_0001_0110 | bit[12]=1 ExT, FSC=0x16 Async External Abort |
| 0x108 | 0b 1_0000_0001_0000 | bit[12]=1 ExT, FSC=0x08 Sync External Abort |
| 0x006 | 0b 0000_0000_0110 | FSC=0x06 Translation fault |

> **注意**：0x80D 的 bit[8]=0（不是 1），bit[11]=1 是 WnR。之前的分析错误地将 bit[11] 当作 bit[8]，导致 FSC=0x0D Permission fault 被误判为异步外部中止。

### 5.2 Permission fault（FSC=0x0D）分析要点

Permission fault 是**同步精确异常**，PC 和 DFAR 都可靠：

1. **PC 指向故障指令**：Permission fault 在指令执行时精确报告，PC 就是触发异常的指令地址
2. **DFAR 有效**：保存了触发权限错误的实际内存地址
3. **WnR 有效**：bit[11] 可靠指示读(0)/写(1)方向
4. **故障源**：MPU 区域配置不覆盖目标地址，或目标地址权限不足
5. **常见原因**：
   - MPU region 未配置或被清零/破坏
   - 堆腐败破坏 MPU 配置数据
   - 写入 MPU 未映射的地址（如 NULL 附近）
   - 并发修改 MPU 配置

### 5.3 异步外部中止（FSC=0x16）分析要点

1. **PC 不指向故障指令**：CPU 在写缓冲/流水线中延迟报告，PC 只是上报时刻的指令地址
2. **DFAR 无效**：故障地址寄存器内容不可靠，不可用于推断实际访问地址
3. **寄存器状态偏移**：R0 等寄存器可能反映流水线延迟（如 `movs r0, #0` 后 R0 仍为旧值）
4. **故障源定位**：需回溯更早的内存操作（之前某次总线访问触发错误，延迟到当前指令上报）
5. **常见原因**：
   - 栈溢出 → 堆 metadata 破坏 → malloc/free 解引用非法指针 → 总线错误
   - PSRAM 时序裕量不足（温度/电压）
   - DMA/CPU 总线竞争
   - 堆腐败导致的野指针访问

## 6. CPSR 寄存器位域解读

### 6.1 CPSR 位域定义

```
CPSR[31:0]
  bit[31:28] = N, Z, C, V (条件标志)
  bit[27]    = Q (饱和标志)
  bit[26]    = Reserved
  bit[25:24] = IT[1:0] (If-Then 状态)
  bit[23:20] = Reserved
  bit[19:16] = GE[3:0] (大于等于标志)
  bit[15:10] = IT[7:2] + Reserved
  bit[9]     = E (大小端, 0=小端)
  bit[8]     = A (异步异常屏蔽)
  bit[7]     = I (IRQ 屏蔽, 1=禁用)
  bit[6]     = F (FIQ 屏蔽, 1=禁用)
  bit[5]     = T (Thumb, 1=Thumb 指令集)
  bit[4:0]   = Mode (处理器模式)
```

### 6.2 Mode[4:0] 编码

| Mode | 说明 |
|------|------|
| 0x10 | User (usr) |
| 0x11 | FIQ (fiq) |
| 0x12 | IRQ (irq) |
| 0x13 | **SVC (svc)** — 管理模式 |
| 0x17 | Abort (abt) |
| 0x1B | Undefined (und) |
| 0x1F | System (sys) |

### 6.3 典型值速查

| CPSR 值 | 解读 |
|---------|------|
| 0x20000133 | SVC 模式(0x13), Thumb(T=1), **IRQ 使能**(I=0) |
| 0x200001F3 | System 模式(0x1F), Thumb(T=1), IRQ 使能(I=0) |
| 0x200001D3 | SVC 模式(0x13), Thumb(T=1), **IRQ 禁用**(I=1) |

**常见错误**：I=0 表示 IRQ 使能（未屏蔽），I=1 表示禁用。不要搞反。

## 7. ARM 栈使用分析

### 7.1 栈方向规则

**ARM 栈为 Full Descending（满递减）**：
- 栈从**高地址向低地址**增长
- PUSH 先递减 SP，再写入
- POP 先读取，再递增 SP
- 初始 SP 指向栈的最高地址附近

### 7.2 栈使用量计算（易错点）

```
栈范围: STACK_BOTTOM .. STACK_TOP
SP = 当前栈指针

✗ 错误计算: 已用 = SP - STACK_BOTTOM  ← 把方向搞反了！
✓ 正确计算: 已用 = STACK_TOP - SP
✓ 正确计算: 剩余 = SP - STACK_BOTTOM
✓ 利用率 = 已用 / (STACK_TOP - STACK_BOTTOM + 1)
```

### 7.3 验证方法

```python
stack_top = 0x7ECA870B    # 高地址
stack_bottom = 0x7ECA7F10  # 低地址
sp = 0x7ECA86D8

used = stack_top - sp          # 0x33 = 51 字节（已用）
remaining = sp - stack_bottom  # 0x7C8 = 1992 字节（剩余）
total = stack_top - stack_bottom + 1  # 0x7FC = 2044 字节
usage_pct = used / total * 100       # 2.5%
```

### 7.4 函数栈帧分析（从二进制）

函数栈帧 = PUSH（寄存器保存） + SUB SP（局部变量分配）

**PUSH 指令识别**：
- 16-bit: `0xB4xx` / `0xB5xx` → PUSH {rlist} / PUSH {rlist, lr}
- 32-bit: `0xE92D xxxx` → PUSH.W {寄存器列表}
- 栈消耗 = 寄存器数量 × 4 字节

**SUB SP 指令识别**：
- 16-bit: `(hw & 0xFF80) == 0xB080` → SUB SP, SP, #(imm7 × 4)
  - 最大 508 字节 (127 × 4)
- 32-bit SUB.W SP, SP, #imm12: `(hw & 0xFBEF) == 0xF1AD` 且 `((hw2 >> 8) & 0xF) == 0xD`
  - i_bit = (hw >> 10) & 1, imm3 = (hw2 >> 12) & 7, imm8 = hw2 & 0xFF
  - imm12 = (i_bit << 11) | (imm3 << 8) | imm8
  - 最大 4095 字节（覆盖大函数局部变量）

**BL 指令识别**（函数调用）：
- 32-bit: `hi5 == 0x1E` 且 `(hw2 >> 14) == 3`
- 从 BL 目标地址可追踪调用链

### 7.5 峰值栈深度分析流程

1. 从崩溃函数开始，解析其 PUSH + SUB SP
2. 解析所有 BL 目标地址，在 MAP 中查找函数名和大小
3. 递归分析每个被调用函数的栈帧
4. 累加最深路径上的所有栈帧
5. 注意处理递归调用和循环引用（设 visited 集合）

### 7.6 峰值结果判定框架

计算出反汇编峰值后，**必须与实际栈分配比较**，不能仅看崩溃时刻 SP：

```
反汇编峰值 peak（从 AXF 分析得到）
实际栈分配 alloc（从源码或 MAP 得到）

if peak > alloc:
    → 栈溢出确认，高置信度
    → 峰值路径即为溢出证据链
    → 修复方向：增大栈 / 减少调用链深度 / 减少大缓冲区

if peak < alloc:
    → 栈溢出排除（确定）
    → 转向其他根因排查：
       - PSRAM 总线时序 / 竞争
       - 并发访问冲突
       - 堆腐败（非栈溢出导致）
       - 硬件问题（温度/电压）
```

**异常类型对 SP 判定的影响**：

在 **Permission fault（FSC=0x0D）** 下：
- PC 指向故障指令，SP 反映故障时刻的真实值
- 反汇编峰值 < 栈分配 = **确定性排除**（理论值不会超过实际栈底）

在 **异步外部中止（FSC=0x16）** 下：
- 崩溃时刻的 SP 可能已从峰值回退（调用链已返回浅层）
- SP 看似"充裕"不代表栈溢出没发生过
- 但反汇编峰值 < 栈分配 = **确定性排除**

**实测数据辅助判定**：

| 实测现象 | 判定 |
|----------|------|
| 小栈必现，大栈消失 | 栈溢出（peak 介于两者之间） |
| 小栈必现，大栈仍偶发 | 小栈是栈溢出，大栈有**其他根因** |
| 调栈大小无变化 | 非栈溢出 |

### 7.7 贪心追踪 vs BFS

BFS（广度优先）可遍历所有路径，但在深层调用图中会组合爆炸（printf 函数互相调用）。贪心追踪（每层只跟最大栈帧的 callee）更实用：

1. BFS 适合浅层调用（depth ≤ 5），可找到全局最优
2. 贪心适合深层链路（depth > 5），找到的是**下界**而非上界
3. 如果贪心结果 > 栈分配，结论可靠；如果 < 栈分配，只能排除栈溢出

## 8. Thumb 指令集速查

### 8.1 16-bit Thumb 指令编码

| 位数 [15:11] | 指令类型 | 示例 |
|-------------|---------|------|
| 000xx | 移位/加/减 | `lsl r0, r1, #2` |
| 00100 | MOV immediate | `movs r0, #0` |
| 00101 | CMP immediate | `cmp r0, #0x10` |
| 00110 | ADD immediate | `adds r0, #5` |
| 00111 | SUB immediate | `subs r0, #3` |
| 01000 | 数据处理 | `add r0, r1` |
| 01001 | LDR [PC+imm] | `ldr r0, [pc, #0x10]` |
| 0101x | LDR/STR [reg] | `str r0, [r1, r2]` |
| 011xx | LDRB/STRB/STRH/LDRH | `ldrb r0, [r1, #5]` |
| 10000 | STR [SP+imm] | `str r0, [sp, #0]` |
| 10001 | LDR [SP+imm] | `ldr r0, [sp, #4]` |
| 10100 | ADD Rd, SP+imm | `add r0, sp, #0x10` |
| 10101 | ADD Rd, PC+imm | `add r0, pc, #0x10` |
| 1011 0 10 | PUSH | `push {r2, r3, r4, lr}` |
| 1011 1 10 | POP | `pop {r2, r3, r4, pc}` |
| 1101x | 条件分支 | `beq 0x100` / `ble 0x200` |
| 11100 | 无条件分支 | `b 0x1000` |
| 11110 + 11111 | BL/BLX (32-bit) | `bl func` |

### 8.2 常用指令快速识别

```
b51c    push {r2, r3, r4, lr}   ; 0xB51C = 1011 0 10 0 0001 1100
bd1c    pop  {r2, r3, r4, pc}   ; 0xBD1C = 1011 1 10 0 0001 1100
2000    movs r0, #0              ; 0x2000 = 0010 0 000 00000000
9000    str  r0, [sp, #0]        ; 0x9000 = 1001 0 000 00000000
9801    ldr  r0, [sp, #4]        ; 0x9801 = 1001 1 000 00000001
4668    mov  r0, sp              ; 0x4668 = 0100 0110 0110 1000
2800    cmp  r0, #0              ; 0x2800 = 0010 1 000 00000000
dd01    ble  +2                  ; 0xDD01 = 1101 1101 00000001
f7ff fcb5  bl  func              ; 11110 xxxxxxxxxx + 11111 xxxxxxxxxx
```
