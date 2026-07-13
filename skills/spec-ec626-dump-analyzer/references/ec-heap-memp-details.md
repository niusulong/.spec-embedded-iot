# EC 平台 Heap/Memp 分析实现细节

> 本文档从 SKILL.md 分离，包含 LWIP memp 池扫描、trace_node 分配追踪、TLSF 堆利用率的内部实现原理和结构体布局。Claude 在需要深入理解或调试脚本时读取。

## 目录

1. [LWIP Memp Pool 扫描原理](#1-lwip-memp-pool-扫描原理)
2. [trace_node 分配追踪原理](#2-trace_node-分配追踪原理)
3. [TLSF Heap 利用率扫描原理](#3-tlsf-heap-利用率扫描原理)

---

## 1. LWIP Memp Pool 扫描原理

### 扫描流程

1. 从 MAP 提取 `memp_tab_*`、`memp_memory_*_base`、`memp_<POOL_NAME>` 符号地址
2. 从 ELF 读取 `memp_desc` 结构体获取元素大小(size)和数量(num)（推荐）
3. 无 ELF 时，从空闲链表地址间距推导元素步长（降级模式）
4. 遍历空闲链表标记已分配元素，计算利用率
5. 对耗尽/高利用率池，扫描已分配元素中的 Flash 指针，识别持有者（泄漏检测）
6. 单一持有者占多数 → 判定为 **Likely LEAK**

### 泄漏检测判定

| 条件 | 判定 |
|------|------|
| 单一持有者占 ≥50% 元素 | **Likely LEAK** |
| ≤3 个持有者占 ≥80% 元素 | **Likely LEAK** |
| 持有者分散 | 可能是正常峰值使用 |

---

## 2. trace_node 分配追踪原理

### 扫描流程

1. 从 MAP 提取 `trace_node`、`node_hash`、`free_node` 符号地址和数组大小
2. **自动检测结构体布局版本**（V1 vs V2），取有效条目更多的版本
3. 读取 `free_node` 指针，步行空闲链表标记空闲节点
4. 遍历 `trace_node[]` 数组（24 字节/节点），识别有效分配记录
5. 每个有效节点包含：memptr（分配地址）、funptr（调用者 LR）、length（分配大小）、task_name（分配任务）
6. `funptr` 通过 MAP 符号解析为调用函数名
7. 按 size 降序排列 → Top 20 大块未释放
8. 按任务/调用者分组统计
9. 泄漏指标：同一 caller+task 有 ≥3 个 ≥64B 的大块分配

### `mm_trace_node` 结构体双布局（24 字节）

脚本自动检测布局版本：采样前 20 条记录，分别用 V1/V2 偏移计算有效条目数，取更优版本。

**Layout V1**（DWARF 标准，来源于 ELF 调试信息）：

| 偏移 | 字段 | 类型 | 说明 |
|------|------|------|------|
| 0 | memptr | void* (4B) | malloc 返回地址 |
| 4 | funptr | uint32 (4B) | 调用者 LR（谁调了 malloc） |
| 8 | length | uint32 (4B) | 分配大小 |
| 12 | task_name[8] | char[8] | 分配任务名（前 7 字符 + NUL） |
| 20 | next | ptr (4B) | hash 链表指针 |

**Layout V2**（部分 EC 固件实测，funptr@+12 且 task_name 仅 4 字节）：

| 偏移 | 字段 | 类型 | 说明 |
|------|------|------|------|
| 0 | (tag) | char[4] | 短标签（如 "Ctr", "Sen", "Tas"） |
| 4 | next | ptr (4B) | hash 链表指针 |
| 8 | memptr | void* (4B) | malloc 返回地址 |
| 12 | funptr | uint32 (4B) | 调用者 LR |
| 16 | length | uint32 (4B) | 分配大小 |
| 20 | task_name[4] | char[4] | 分配任务名（前 4 字符） |

检测到 V2 时输出 `[Layout V2 detected: memptr@+8, funptr@+12, task_name@+20(4B)]`。

### 局限性

- `MM_TRACE_MAX=128`（ON=1）或 `1024`（ON=2），超出上限的分配不被追踪
- `funptr` 大部分为 `pvPortMallocEC+0x9`（通用 malloc wrapper），无法追溯实际业务调用者
- dump 是静态快照，无法还原分配/释放时序
- 无 MAP 文件时 `funptr` 显示为原始地址

---

## 3. TLSF Heap 利用率扫描原理

### 扫描流程

1. 从 MAP 提取 `gTotalHeapSize` 和 `ucHeap` 符号地址
2. 从 dump 读取 `gTotalHeapSize` 变量获取总堆大小
3. 从 dump 解引用 `ucHeap` 指针获取堆内存起始地址
4. 在堆内存中扫描 `0xbeafdead`（块头边界标记）定位第一个 TLSF 块
5. 步行物理块链表：根据 `size` 字段计算下一个块地址
6. 统计已用块/空闲块数量和大小，计算利用率
7. 对大块已用块读取 `alloc_owner`（低 24 位=调用者 LR，高 8 位=任务编号）

### TLSF 块头布局（heap_6，MM_DEBUG_EN 启用）

| 偏移 | 字段 | 类型 | 说明 |
|------|------|------|------|
| 0 | prev_phys_block | ptr (4B) | 上一个物理块（仅当上一块空闲时有效） |
| 4 | head_bound | u32 (4B) | 头部边界标记 (0xbeafdead) |
| 8 | alloc_owner | u32 (4B) | 低 24 位=funcPtr, 高 8 位=taskNum |
| 12 | size | u32 (4B) | bit0=空闲, bit1=上一块空闲 |
| 12+ | next_free/prev_free | ptr (4B×2) | 仅空闲块有效 |

### `size` 字段编码（无 MEM_BLK_SIZE_32BIT）

- **空闲块**：全部 32 位 = 块大小（除去 bit0/bit1 标志）
- **已用块**：低 16 位 = 块大小，高 16 位 = 用户请求大小
