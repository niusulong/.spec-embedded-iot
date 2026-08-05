# QCX216 堆损坏判定指南（TLSF + MM_DEBUG）

QCX216 主应用堆是 **TLSF** 分配器 + **MM_DEBUG**（`MM_HEAD_BOUNDARY` + `HEAP_MEM_DEBUG`）。
本文档覆盖 HardFault/ASSERT 由堆损坏引发时的判定流程。

> ⚠️ **能力边界（重要）**：QCX216 **无 malloc/free trace ring**（对比 UIS8852 有 `gOsiMemRecords`
> 记录每次分配/释放的 {caller,ptr}）。TLSF+MM_DEBUG 只有：① 块头 `alloc_owner`（分配者，仅 used
> 块）；② `block_record[FL][SL]` 大小类分配/释放计数矩阵；③ `hist_min_free` 历史最小空闲水位。
> 因此静态 dump **无法通用检测"同一地址释放两次"（double-free）**。本指南只做**静态快照能可靠
> 做到**的：堆完整性校验（head_bound）+ 任意地址块状态查询 + 链表悬空节点取证（特定模式，临时脚本）。

## MM_DEBUG 块头布局（已从 tlsf.c + dump 验证）

```
+0x00  prev_phys_block   (u32)  前一物理块地址
+0x04  head_bound        (u32)  = 0xBEAFDEAD  ← 块头边界魔数（金标准）
+0x08  alloc_owner       (u32)  used: (funcPtr & 0xFFFFFF) | (taskNum << 24)
+0x0C  size              (u32)  bit0=free; used 时低16=allocSize 高16=wantedSize
       [payload ...]            block+0x10 .. block+0x0C+size
```
- **下一块 = block + 0x0C + size**。`size==0` 或越界为 last/异常。
- `head_bound=0xBEAFDEAD` 是合法块的**必要标志**；不符 = 块头被踩（越界写 / double-free 破坏）。
- `alloc_owner`：used 块记录分配者返回地址（低24位）+ 任务号（高8位），可定位"谁分配的"。
- ⚠️ `psSlp2FreeBytesRemaining` 是 sleep/retention **另一堆**，非主 TLSF 堆，勿当主堆 free。

## 损坏判定树（按现象路由）

```
HardFault（vListInsert/uxListRemove 等链表操作解引用 NULL/野指针）
或 ASSERT 在堆相关函数
│
├─ 1) full-analyze 的「Heap integrity」段（主堆物理遍历 + head_bound 校验）
│   ├─ head_bound 非 0xBEAFDEAD / 遍历中断 @某块
│   │     → 越界写踩坏块头（定位首个坏块地址，查相邻 used 块的 owner）
│   ├─ 物理遍历连续无中断 → 堆元数据完好，继续查"被引用块是否已释放"
│   └─ used% 高：注意 Heap integrity 中断时统计不完整，勿误读，看完整堆统计
│
├─ 2) full-analyze 的「Crash registers -> block status」段（自动查崩溃帧 R0-R3）
│   ├─ 落在 FREE 块内 → use-after-free / 悬空指针（该地址被引用但其内存已释放）
│   ├─ 落在 USED 块内 + owner → 正常在用，看 owner 语义
│   └─ 无所属块（非堆 / 无头）→ 可能野指针指向非堆地址
│   （手动查任意地址：python 调 qcx216_heap.find_enclosing_block(data, addr)）
│
└─ 3) 链表节点取证（临时脚本，见下节）：遍历目标 List 节点查块状态
    ├─ 某 node 的 pxNext 越界/NULL（链表断裂）→ 链表节点损坏
    ├─ 某 node 落在已释放块内 → 悬空节点（链表残留已 free 的内存）
    └─ node 内容全 0 且无所属块 → 可疑悬空/被清零节点
    → 链表损坏 = 堆损坏/use-after-free 的强签名
```

## 链表损坏取证（临时脚本，QCX216 无通用 double-free 检测）

FreeRTOS 大量结构（软件定时器表、延时任务表、事件组、队列）用 List_t。某节点内存被异常
释放/清零后仍被链表引用，下一次 `vListInsert`/`uxListRemove` 遍历到它就解引用 NULL/野指针 →
HardFault。**定时器服务任务（Tmr Svc）在 vListInsert 崩 = 活动定时器表有悬空节点**（典型签名）。

因无 trace、无通用子命令，取证需临时脚本（复用技能模块）：

```python
import sys; sys.path.insert(0, r"<技能>/scripts")
from qcx216_common import DumpReader, LIST_OFF_NUMITEMS, LIST_OFF_END, LISTEND_OFF_PXNEXT
from qcx216_elf import ElfReader
from qcx216_heap import find_enclosing_block
dr = DumpReader("<dump>"); elf = ElfReader("<elf>"); data = dr.data
sym = elf.find_symbol("pxCurrentTimerList")      # 或 pxOverflowTimerList / pxDelayedTaskList1/2 ...
list_addr = __import__("qcx216_common").u32(data, sym.addr)   # 指针符号解引用
end = list_addr + LIST_OFF_END
n = __import__("qcx216_common").u32(data, list_addr + LISTEND_OFF_PXNEXT)
while n and n != end:
    blk = find_enclosing_block(data, n)
    free = blk and blk["free"]
    print("node@0x%08X %s" % (n, "⚠️ 落在已释放块(hdr 0x%08X)" % blk["hdr"] if free else "OK"))
    n = __import__("qcx216_common").u32(data, n + 4)   # ITEM_OFF_PXNEXT
```

List_t / ListItem_t 偏移常量在 `qcx216_common`（`LIST_OFF_*` / `ITEM_OFF_*`，与 FreeRTOS 标准布局一致）。

**关键 List 符号**：`pxCurrentTimerList` / `pxOverflowTimerList`（软件定时器）、
`pxDelayedTaskList1` / `pxDelayedTaskList2` / `xPendingReadyList` / `xSuspendedTaskList` 等。

## 与"正常 FreeRTOS 删除路径"的区分（重要，避免过度推断）

⚠️ FreeRTOS **正常** `xTimerDelete` 是 **先 `uxListRemove` 摘链再 `vPortFree`**（timers.c:724
先于 :796）。故**单次正常删除不会**留下"仍被链表引用的已释放节点"。若发现悬空节点，根因不是
"单次删除"，而是：
- **并发 double-free**：无锁的 enter/leave 被多任务同时调用 → 同一资源 double `free` → 堆元数据
  损坏（`Heap integrity` 会发现 head_bound 坏块）。
- **use-after-free**：删除命令异步处理窗口内，回调/其它路径仍访问已释放上下文 → 野指针写。
- **越界写**：相邻 used 块越界踩坏链表节点内存。

→ 判定堆损坏**性质**（double-free vs UAF vs 越界）需结合代码（释放路径是否有锁/是否检查返回值/
是否先摘链）+ 运行时埋点（见下）。静态 dump 能确认"链表/堆已损坏"，但"哪条路径产生"常需复测。

## 典型证据形态（链表损坏类 HardFault）

当 HardFault 崩在 `vListInsert`/`uxListRemove`/`xTaskRemoveFromEventList` 等 List 操作，
且崩溃指令是 `STR/LDR R?,[R?,#?]`、某寄存器为 0 或野值时，按下列三重签名取证（互相印证）：

1. **异常帧**（`frame` 子命令 / full-analyze）：PC 在 List 操作函数；R0/R1/R2 之一为 0 或指向
   非法地址（即崩溃访存基址）。
2. **链表悬空节点**（临时脚本遍历 List + `find_enclosing_block`）：某 node `pxNext=NULL/越界` 且
   `落在已释放块内`（块头 free_bit=1）—— 节点内存已释放但仍被链表引用。
3. **堆元数据损坏**（full-analyze 的 `Heap integrity`）：TLSF 物理遍历在某块 `head_bound≠0xBEAFDEAD`
   中断 —— 堆被越界写 / double-free 破坏。

三者一致即确认"堆损坏破坏了某 List 节点内存，List 操作遍历到它崩溃"。根因域再结合代码查
释放路径（无锁并发 / 异步删除窗口 UAF / 相邻块越界写）。具体案例归档报告与平台记忆另存，
本文只给方法论。

## 工具

| 工具 | 用途 |
|------|------|
| `full-analyze` 的「Heap integrity」段 | 主堆物理遍历 + head_bound 校验（越界写 / 堆元数据损坏） |
| `qcx216_heap.find_enclosing_block` | 查任意地址所在 MM_DEBUG 块状态（used/free + 分配者，use-after-free） |
| `full-analyze` 的「Crash registers」段 | 自动查崩溃帧 R0-R3 指向地址的块状态 |
| `qcx216_heap.walk_tlsf` | 完整堆统计（used/free/碎片化/owner TOP，判耗尽） |
| `qcx216_heap.check_heap_integrity` | 堆完整性校验（head_bound）的程序接口 |

## 锁死根因的运行时埋点（静态 dump 不足时）

- 堆释放染色：`vPortFree` 把块填 `0xDEADBEEF`；复测后看坏节点是 `0x00`（显式 memset）还是
  `0xDEADBEEF`（free 后未覆写），区分"释放路径清零"vs"free 后被复用"。
- 给可疑 free/delete 加调用者日志 + 返回地址埋点（spec-memory-leak-analyzer 的返回地址法）。
- 开 `configUSE_LIST_DATA_INTEGRITY_CHECK_BYTES`，让链表损坏在发生当下 assert（离破坏时刻更近）。
- 若要精确追溯"谁 free 了某地址"：给 `vPortFree` 加调用者返回地址日志（本技能静态 dump 无此信息）。
