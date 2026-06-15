# Crash Dump 分析模式参考

## 模式1：栈溢出判定

### 判定方法

1. 从 .cmm PRINT 行提取 `stack range (bottom..top)`
2. 从 DDR dump 提取栈区域完整内容
3. 检查栈底部是否仍为 ThreadX 0xEFEFEFEF 填充

### 判定标准

```
栈底 0xEF 被覆盖       → 栈溢出确认
栈底 0xEF 完整, >95%   → 栈溢出高风险
栈底 0xEF 完整, <80%   → 排除栈溢出
```

### 分析脚本

```bash
# 注意: 以下地址为 ASR1603 平台示例，其他平台需根据实际 dump 调整
python "scripts/dump_analyzer.py" stack-analysis \
  --ddr com_DDR_RW.bin \
  --base 0x7E20FFFC \    # ASR1603 DDR 基地址
  --stack-bottom 0x7ECA7F10 \
  --stack-top 0x7ECA870B \
  --sp 0x7ECA86D8 \
  --map NEZHAC_CP_SKL_MIFI_TX.map
```

### 注意事项

- SP 位置 ≠ 峰值栈使用。SP 是当前值，而栈的最深使用可能发生在之前的函数调用中
- 必须检查栈底的 0xEF 填充是否完整，而不能仅看 SP 到栈底的距离
- 栈底附近的 DEADBEEF 标记是栈保护（stack guard），如果被覆盖也说明溢出

---

## 模式2：NULL 指针解引用

### 识别特征

```
FAULT_ADDRESS = 0x00000000  → 直接解引用 NULL 指针
FAULT_ADDRESS = 0x00000004  → 访问结构体成员 (offset 4)
FAULT_ADDRESS = 0x00000008  → 访问结构体成员 (offset 8)
```

### 分析步骤

1. 确认 FAULT_ADDRESS 是否在 0x0~0x10 范围
2. 如果偏移 > 0，查找调用链中哪个结构体在该偏移有成员
3. 检查该指针的来源（malloc 返回值？函数参数？全局变量？）
4. 验证是否因堆碎片化、资源耗尽导致分配失败

### 关键检查

- 调用链中是否包含 `malloc`/`calloc`/`realloc`
- 140+ 次循环后复现 → 堆碎片化的典型特征
- R0 寄存器值是否异常（如 0xAA 通常表示损坏）

---

## 模式3：MPU Permission Fault (FSC=0x0D)

### 识别特征

```
DFSR = 0x80D (bit 11 = 1 → WnR=1 写操作, FSC = 0x0D → Permission fault)
或 DFSR = 0x00D (WnR=0 读操作, FSC = 0x0D → Permission fault)
```

**重要**：FSC=0x0D 是 Permission fault（MPU 权限违规），**不是**异步外部中止。DFAR 有效，PC 指向故障指令。

### 分析要点

Permission fault 表示 CPU 尝试访问 MPU 规则禁止的内存区域：
- MPU 区域配置错误（权限位设置不当）
- 尝试向只读区域写入
- PSRAM 代码区域被损坏导致 MPU 异常

### 分析策略

1. 确认 DFAR 指向的地址属于哪个 MPU 区域
2. 检查该 MPU 区域的权限配置（读/写/执行权限）
3. 检查是否有 DMA/FOTA 操作修改了 MPU 配置
4. **执行 AXF vs DDR 代码完整性检查**，确认代码是否被损坏

---

## 模式4：异步外部中止 (FSC=0x16)

### 识别特征

```
FSC = 0x16 → Asynchronous external abort (DFSR 解码后 FSC 值为 0x16)
```

**注意**：FSC=0x16 才是异步外部中止，此时 DFAR 不可靠，PC 不指向故障指令。

### 分析要点

异步中止意味着故障指令和 PC 不一致：
- PC 指向的指令可能不是故障原因
- 需要分析整个调用链追溯先前的操作
- 可能由 PSRAM 总线错误、QSPI Flash 错误引起

### 分析策略

1. 提取栈中所有代码段地址，还原完整调用链
2. 检查调用链中的外部存储器访问操作（QSPI、PSRAM）
3. 关注 `spi_nor_do_read`、`qspi_*` 等底层驱动函数
4. 检查是否有并发访问冲突

---

## 模式5：调用链还原

### 方法

从栈中提取所有代码段地址，使用 map 文件解析为函数名。

### 地址筛选规则

```
代码段地址范围 (ASR1603 平台，其他平台需根据实际 map 文件调整):
- 0x7E000000 ~ 0x7FFFFFFF: 代码/只读数据

排除:
- 栈地址 (在 stack_bottom..stack_top 范围内的)
- 已知的数据地址 (0x7EC23000 等)
- 0xEFEFEFEF (栈填充)
- 0xDEADBEEF (栈保护)
```

### 交叉验证

1. 栈中的返回地址应形成连续的调用链
2. LR 寄存器的值应与栈中某个地址匹配
3. 调用链中的函数在源码中应有调用关系

---

## 模式6：DDR 基地址推导

### 原理

DDR dump 是原始内存转储，文件偏移 = 虚拟地址 - 基地址。
基地址通常与 PSRAM 的物理映射有关。

### 步骤

1. 从 .cmm 栈帧数据中选取 3-5 个非零、非特殊标记的已知值
2. 在 DDR dump 中搜索第一个值的字节序列
3. 对每个匹配位置计算候选基地址：`base = known_addr - file_offset`
4. 用其余已知值交叉验证：`ddr[known_addr - base] == known_value`
5. 通过验证的即为正确基地址

### 脚本

```bash
python "scripts/dump_analyzer.py" ddr-base \
  --ddr com_DDR_RW.bin \
  --cmm com_EE_Hbuf.cmm
```

---

## 模式7：累进性问题分析

### 特征

- 测试 N 次后复现（N > 100）
- 每次复现的 N 值不同
- 正常运行期间无异常

### 常见根因

| 根因 | 证据 | 验证方法 |
|------|------|----------|
| 堆碎片化 | 调用链含 malloc，FAULT_ADDRESS≈0 | 添加堆监控日志 |
| 资源泄漏 | 文件描述符/内存持续增长 | 记录每次迭代的资源使用 |
| 计数器溢出 | 某个计数器接近上限 | 检查相关计数器值 |
| Flash 磨损 | 写入操作频繁 | 检查 Flash 健康状态 |
| NV 文件损坏 | 读写同一文件的两个模块 | 检查文件内容一致性 |

---

## 模式8：ThreadX 任务分析

### com_rti_tsk.bin 格式

任务列表，每条目约 16 字节：
```
NNx:Name\0\xAA\xBB\xCC\xDD

NN   = 序号 (ASCII "00"~"99")
x    = 类型 ('T'=任务, 'I'=空闲/ISR)
:    = 分隔符
Name = 任务名 (null terminated)
4字节 = 任务控制块地址或其他元数据
```

### 关键字段

```
SLP:TaskName  = 休眠/挂起的任务
NN T:TaskName = 活跃任务
```

### TX_THREAD 结构关键字段

在栈 dump 中，可通过 "THRD" (0x54485244) 或 "CIST" (0x54495343) 标记定位：

```
0x54485244 = "DRHT" (little-endian "THRD") = ThreadX 线程 ID
0x54495343 = "CIST" (little-endian "CISC"→"CSIT") = 线程控制块标记

TX_THREAD 关键成员:
+ 偏移 0x00: thread_id (0x54485244 "THRD")
+ 偏移 0x04: thread_state
+ 偏移 0x08: thread_stack_ptr (当前 SP)
+ 偏移 0x0C: thread_stack_start (栈底)
+ 偏移 0x10: thread_stack_end (栈顶)
+ 偏移 0x14: thread_stack_size (栈大小)
+ 偏移 0x28: thread_name_ptr (字符串指针)
```

### 全线程栈溢出扫描方法

**原理**：ThreadX 任务栈通过 malloc 从堆分配。任何任务栈越界会破坏相邻堆块结构，可能导致 malloc 失败。仅检查崩溃任务的栈不够，必须排查所有任务。

**自动化扫描**：

```bash
# 注意: 以下地址为 ASR1603 平台示例
python "scripts/dump_analyzer.py" scan-threads \
  --ddr com_DDR_RW.bin \
  --base 0x7E20FFFC
```

**扫描逻辑**：
1. 在 DDR dump 中搜索所有 `0x54485244` (TX_THREAD 标记)
2. 读取每个 TX_THREAD 的 stack_start, stack_end, stack_size
3. 检查栈底部前 128 字节的 `0xEFEFEFEF` 填充是否完好
4. 检查栈前的 `DEADBEEF` 守卫是否完好
5. 读取线程名称用于输出报告

**判定标准**：

```
栈底 0xEF 被覆盖         → 栈溢出确认（该线程溢出了）
栈底 0xEF 完好           → 该线程未溢出
所有线程 0xEF 完好        → 排除任何线程栈越界的可能性
```

**栈分配结构（malloc 分配的堆内存）**：

在 DDR dump 中可以看到栈分配的堆块头部：

```
[DEADBEEF DEADBEEF]  ← 栈守卫（stack guard）
[0x54495343]          ← CSIT 标记（ThreadX 控制块标记）
[ptr to prev]         ← 前一个分配块的指针
[0x00000001]          ← 标记
[分配大小+开销]        ← 如 0x1008 = 4092栈 + 12开销
[入口函数地址]         ← 任务入口函数
[0xEFEFEFEF ...]      ← 栈数据开始
```

通过读取堆块大小字段，可以验证：
- 分配是否合理（大小与 stack_size 字段匹配）
- 堆块头部是否被踩（如果大小字段异常，说明堆被破坏）

**注意事项**：

- 0xEFEFEFEF 是"用水印"：栈帧一旦触及就永久覆盖，不可恢复
- ARM 栈向下增长（从高地址到低地址），溢出方向也是向下
- 两个相邻任务的栈溢出方向相同，不会互相覆盖（但会破坏堆结构）
- 栈溢出破坏的是 malloc 的堆块元数据（块头、空闲链表），导致后续 malloc 失败
```

---

## 模式9：PSRAM 代码损坏检测

### 背景

嵌入式系统中代码通常在 PSRAM 中执行（XIP 或拷贝到 PSRAM）。如果 PSRAM 中的代码被损坏，CPU 会执行错误指令，导致各种异常（DataAbort、Undefined Instruction 等）。从 DDR dump 反汇编损坏的代码会得到错误的结论。

### 识别特征

- DDR dump 反汇编结果与 AXF 编译代码不一致
- 反汇编结果看起来不像正常的编译器输出
- AXF vs DDR 对比发现代码区域字节不匹配

### AXF vs DDR 对比方法

```
1. 从 AXF (ELF文件) 解析 section header，提取崩溃地址处的编译字节
2. 从 DDR dump 提取相同虚拟地址处的运行时字节
3. 逐字节对比

ELF 字节提取公式:
  对每个 section:
    if sh_addr <= target_addr < sh_addr + sh_size:
      file_offset = sh_offset + (target_addr - sh_addr)

DDR 字节提取公式:
  ddr_offset = virtual_addr - DDR_base_address
```

### 损坏特征分析

| 损坏特征 | 类型 | 典型根因 |
|----------|------|---------|
| 大区域（>4KB）+ 4KB 对齐 | DMA 覆写 | DMA 传输目标地址错误 |
| 恰好 4KB | Flash 扇区溢出 | Flash 擦写溢出到相邻 PSRAM |
| ≤8 字节 | 比特翻转 | PSRAM 总线 glitch |
| 整段代码（MB级），特定字节模式主导 | PSRAM 总线大面积损坏 | QSPI 总线错误 |
| 中等大小，非对齐 | 部分覆写 | 堆溢出/缓冲区越界 |

### 关键结论模板

当检测到 PSRAM 代码损坏时：

```
结论：崩溃根因是 PSRAM 代码损坏，不是软件逻辑 bug
证据：
- AXF 原始编译字节: [hex dump]
- DDR 运行时字节:   [hex dump]
- 差异: N/M 字节不一致 (XX%)
- 损坏区域: 0xXXXXXXXX ~ 0xXXXXXXXX (XXX KB)
- 损坏特征: [DMA_OVERWRITE / BIT_FLIP / ...]

正确的崩溃指令来自 AXF: [AXF 反汇编]
DDR dump 反汇编的是损坏后的指令，不可信。

下一步: 分析损坏区域是否与 [DMA通道/FOTA写入/QSPI操作] 的地址范围重叠
```

---

## 模式10：崩溃指令矛盾分析

### 场景

当栈溢出看似是原因，但崩溃指令与简单溢出不一致时。

### 分析方法

1. 使用 `axf_disasm.py` 反汇编崩溃 PC 附近的指令
2. 确认崩溃指令类型（push/ldr/str 等）
3. 检查指令序列中的矛盾

### 矛盾判定表

| 指令序列 | 矛盾？ | 可能根因 |
|----------|--------|---------|
| push 成功 → str [sp] 失败 | 是 | SP 被破坏 / 堆溢出踩栈 / MPU 配置变化 |
| push 失败（函数入口） | 否 | 栈溢出（函数入口 SP 越界） |
| str [sp, #大偏移] 失败 | 否 | 栈溢出或 SP 超出栈区域 |
| ldr/str [Rn≠sp] 失败 | 否 | 空指针 / 野指针（检查 Rn 值） |
| pop 失败 | 否 | 返回地址被覆盖，LR 值非法 |
| blx Rm 失败 | 否 | 函数指针损坏 |

### 矛盾分析示例

```
函数入口处:
  push {r4, lr}         ← 成功（SP 足够）
  sub sp, sp, #0x20     ← 成功
  str r0, [sp, #0x1c]   ← 崩溃！str 访问 sp+0x1c 失败

分析:
  push 和 str 访问相邻的栈区域（相差几十字节）。
  如果 push 成功但 str 失败，纯栈溢出无法解释（两者访问连续内存）。
  
可能根因:
1. SP 被其他任务/中断在中途修改
2. malloc 分配的栈被相邻堆块溢出破坏
3. MPU 配置在 push 和 str 之间发生变化
```

---

## 模式11：反汇编峰值 vs 运行时栈分析交叉验证

### 场景

运行时栈分析（0xEF 检查）和反汇编峰值分析（调用图分析）结果需要交叉验证。

### 方法

1. **运行时分析**：通过 DDR dump 检查栈底 0xEF 填充（模式1）
2. **静态分析**：通过 `stack_analysis.py` 反汇编计算调用链峰值栈深度

```bash
# 静态峰值计算
python "scripts/stack_analysis.py" <axf_file> <map_file> --func <崩溃函数>
```

### 交叉验证矩阵

| 运行时 0xEF | 反汇编峰值 | 结论 |
|-------------|-----------|------|
| 被覆盖 | peak > alloc | 栈溢出确认（双重证据） |
| 被覆盖 | peak < alloc | 需进一步分析（可能是其他线程溢出覆盖，或异步异常） |
| 完整 | peak > alloc | 可能未执行到峰值路径 / 异步异常下溢出已恢复 |
| 完整 | peak < alloc | 栈溢出排除（双重确认） |

### 关键区分

- 崩溃时刻 SP 显示"充裕" ≠ 栈溢出不可能
  （异步异常下溢出可能在更早时刻发生，SP 已恢复）
- 反汇编峰值 < 栈分配 = **确定排除栈溢出**（无论 SP 看起来怎样）
- 反汇编峰值 > 栈分配 = **确定确认栈溢出**（无论 SP 看起来怎样）

---

## 模式12：代码段未加载分析

### 场景

CPU 执行了 PSRAM 中某个 ELF 段的代码，但该段在启动时因配置条件未被加载到 PSRAM。PSRAM 中该区域保留的是硬件初始化（BIST）的测试数据。

### 识别特征

- AXF vs DDR 对比：整段不一致，不是局部损坏
- DDR 字节模式：0xAA/0x55 checkerboard（BIST 残留）
- 不一致范围：精确对齐 ELF section 边界（段首到段尾）
- 跨段调用：LR 在已加载段，PC 在未加载段
- FAULT_ADDRESS 通常接近 0（执行填充数据作为指令的偶然副作用）
- `full-analyze` Step 7 输出 `CODE SECTION NOT LOADED`

### 分析步骤

1. 运行 full-analyze，检查 Step 7 输出是否为 `CODE SECTION NOT LOADED`
2. 用 `ddr-search` 搜索 DDR 中的启动配置属性：
   ```bash
   python "scripts/dump_analyzer.py" ddr-search \
     --ddr <DDR> --base <BASE> --string "<段加载相关关键词>"
   ```
3. 检查 scatter/linker script 中该段的加载条件
4. 检查调用链中为何调用了未加载段的函数
5. 常见原因：条件加载的段中包含了无条件调用路径上的函数

### 与"PSRAM 代码损坏"的关键区别

| | 代码损坏 | 代码段未加载 |
|---|---------|------------|
| 代码状态 | 曾正确加载后被运行时操作覆写 | 从未写入 PSRAM |
| DDR 特征 | 随机/非对齐损坏 | BIST 残留 + 段边界对齐 |
| 根因方向 | PSRAM 总线/DMA/Flash 操作问题 | 启动配置/段加载条件/代码放置问题 |
| 修复方向 | 排查总线/DMA/Flash 操作 | 调整 scatter 文件或代码放置策略 |
