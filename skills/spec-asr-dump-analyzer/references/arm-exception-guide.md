# ARM Cortex-R 异常分析参考

## 1. 异常类型与入口

| 异常类型 | 入口地址 | 触发条件 |
|----------|----------|----------|
| Reset | 0x00000000 | 上电/硬复位 |
| Undefined Instruction | 0x00000004 | 执行未定义指令 |
| SVC (Software Interrupt) | 0x00000008 | 执行 SVC 指令 |
| PrefetchAbort | 0x0000000C | 指令预取失败 |
| DataAbort | 0x00000010 | 数据访问失败 |
| IRQ | 0x00000018 | 外部中断请求 |
| FIQ | 0x0000001C | 快速中断请求 |

## 2. CPSR 寄存器解析

```
CPSR 格式: 0x20000133

Bit 31-28: NZCV 标志 (条件码)
Bit 27:    Q (饱和)
Bit 26:    J (Jazelle)
Bit 25:    保留
Bit 24:    J (Jazelle)
Bit 9-6:   GE (大于等于)
Bit 7:     I (IRQ 禁止, 1=禁止)
Bit 6:     F (FIQ 禁止, 1=禁止)
Bit 5:     T (Thumb, 1=Thumb模式)
Bit 4-0:   Mode (处理器模式)
```

### 处理器模式

| Mode | 值 | 说明 |
|------|-----|------|
| User | 0x10 | 用户模式 |
| FIQ | 0x11 | 快速中断 |
| IRQ | 0x12 | 中断 |
| SVC | 0x13 | 管理模式 |
| Abort | 0x17 | 数据/指令中止 |
| Undefined | 0x1B | 未定义指令 |
| System | 0x1F | 系统模式 |

### 示例

```
CPSR = 0x20000133
  NZCV = 0010 (N=0, Z=0, C=1, V=0)
  I = 1 (IRQ 禁止)
  F = 0 (FIQ 允许)
  T = 1 (Thumb 模式)
  Mode = 0x13 = SVC

→ 设备在 SVC 模式、Thumb 指令集下发生了异常
```

## 3. DataAbort 分析

### DFSR (Data Fault Status Register) 解码

ASR1603 使用 Cortex-R5 MPU（PMSAv7），**必须使用 PMSAv7 格式解码**（不要混淆 VMSAv7 MMU 格式）。

```
FSC 解码公式: FSC = ((DFSR >> 6) & 0x10) + (DFSR & 0x0F)
WnR 提取: WnR = (DFSR >> 11) & 1
```

**关键区分**：
- FSC=0x0D = **Permission fault (MPU权限错误)**，DFAR 有效，PC 指向故障指令
- FSC=0x16 = **异步外部中止**，DFAR 不可靠，PC 不指向故障指令
- WnR 在 **bit[11]**，不是 bit[9]

完整的 FSC 编码表、DFSR 位域布局、SEGGER 官方解码代码、PMSAv7 vs VMSAv7 对比，详见 **`arm-pmsav7-dfsr-reference.md`**。

### 常见 FAULT_ADDRESS 模式

| FAULT_ADDRESS | 含义 | 根因方向 |
|---------------|------|----------|
| 0x00000000 | NULL 指针解引用 | 未检查 malloc 返回值 / 未初始化指针 |
| 0x00000004 | NULL+4 结构体成员访问 | 偏移4处的成员，如 `int` 类型字段 |
| 0x00000008 | NULL+8 结构体成员访问 | 偏移8处的成员，如第二个 `int` 字段 |
| 0x00000001 | 非对齐访问 | 将非指针对齐的地址作为指针使用 |
| 0xDEADxxxx | 看门狗/未初始化标记 | 使用了标记为无效的指针 |
| 高地址 (>0x7F...) | PSRAM 越界 | 数组越界或栈溢出 |
| 在栈范围内 | 栈溢出 | 超出栈底访问 |

> **注意**: 上表中"高地址"的 0x7F... 前缀是 ASR1603 平台特征（代码段映射在 0x7E000000~0x7FFFFFFF），其他平台需根据实际内存映射调整。

## 4. 同步中止 vs 异步中止

**注意**：判断同步/异步要看 FSC 编码值，不是 DFSR 的单个 bit。

| 特征 | 同步中止 (Permission fault等) | 异步中止 (Async external abort等) |
|------|----------|----------|
| FSC 编码 | 0x00/0x01/0x06/0x07/0x08/0x0C/**0x0D**/0x0E/0x19 | **0x16**/0x18 |
| PC 指向 | 故障指令 | 故障指令之后的某条指令 |
| FAULT_ADDRESS | 精确 | 可能不准确 |
| DFAR 有效 | 是 | 否 |
| 原因 | 当前指令直接触发 | 之前写入操作的延迟响应 |
| 分析难度 | 低 | 高（需追溯调用链） |

### 异步中止分析策略

1. PC 不一定指向故障指令，需要分析调用链
2. 关注栈中的所有代码地址，追溯先前的函数调用
3. 检查 FAULT_ADDRESS 是否与调用链中的某个结构体偏移匹配
4. 结合 R0 等寄存器的异常值判断

## 5. Thumb 指令地址

ARM 符号表中，Thumb 函数的地址 bit 0 = 1：
```
nwy_fota_get_reboot_count  0x7E88003D  (Thumb Code, 26 bytes)  ← ASR1603 平台示例
                                    ^ bit 0 = 1 表示 Thumb
实际代码起始地址: 0x7E88003C (清除 bit 0)
函数范围: 0x7E88003C ~ 0x7E880055 (26 bytes)
```

在解析 PC/LR 值时需要清除 bit 0：
```python
actual_addr = saved_addr & ~1
```

## 6. ARM 栈帧布局

ARM 使用满递减栈 (Full Descending)：
- SP 指向最后压入的数据
- PUSH: 先减 SP，再存储
- POP: 先读取，再增 SP

```
高地址
┌──────────────┐
│ 栈顶 (初始SP) │ ← ThreadX 任务入口时的 SP
├──────────────┤
│ 函数A局部变量 │
│ saved LR (A)  │ ← PUSH {LR}
├──────────────┤
│ 函数B局部变量 │
│ saved R4      │
│ saved LR (B)  │ ← PUSH {R4, LR}
├──────────────┤
│ 函数C局部变量 │ ← 当前 SP
└──────────────┘
低地址 (栈底/限制)
```
