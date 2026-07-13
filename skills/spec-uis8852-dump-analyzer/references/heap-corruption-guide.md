# dlmalloc 堆损坏与耗尽分析（UIS8852）

UIS8852 AP 系统堆用 Doug Lea malloc（`components/kernel/rt-thread/dlmalloc.c`，DEBUG 模式）。dump 分析中 dlmalloc assert 是**高频死机点**。本文档讲如何区分"堆耗尽"vs"堆元数据损坏"vs"堆内存被越界写粉碎"。

## chunk 结构

```c
struct malloc_chunk {
    size_t prev_size;   // +0: 前一 chunk 大小（仅前一 chunk 空闲时有意义）
    size_t size;        // +4: 本 chunk 大小（含 overhead），低 2 位是标志
    struct malloc_chunk *fd;  // +8: 前向指针（仅 free chunk 用）
    struct malloc_chunk *bk;  // +12: 后向指针（仅 free chunk 用）
};
```

### 标志位（size 字段低 2 位）

| 位 | 名 | 值 | 含义 |
|----|----|----|------|
| 0 | `PREV_INUSE` | `0x1` | 前一相邻 chunk 在用 |
| 1 | `IS_MMAPPED` | `0x2` | 本 chunk 由 mmap 分配 |

- `SIZE_BITS = PREV_INUSE | IS_MMAPPED = 0x3`
- `chunksize(p) = p->size & ~0x3`（真实大小）
- `next_chunk(p) = p + chunksize(p)`
- `MINSIZE = 16`（最小 chunk），chunk 大小 8 字节对齐

## free chunk 与 bin

free chunk 组织成双向链表（bin）。bin header 是 `av_` 数组里的**合成 chunk**（无真实 size，只有 fd/bk）：

```c
#define NAV 128                              // bin 数量
#define bin_at(n, i)  (&av_[n][2*i+2] - 2*SIZE_SZ)   // bin i 的 header 地址
#define top(n)         (bin_at(n,0)->fd)     // top chunk = bin[0].fd
```

- **bin header 间距 8 字节**：`bin_at(i) = av_ + 8*i`
- **`av_` 数组在 DTCM**（`0x10000~0x10400` 区）—— free chunk 的 fd/bk 循环指回这里的 bin header，**这是正常的，不是损坏**
- smallbin：bin i 存 `size = 8*i` 的 chunk（如 size 0x30=48 的 chunk 在 bin 6，header @ `av_+0x30`）
- `top` chunk：堆顶剩余块，`bin_at(0)->fd` 指向它

> **关键**：`bin_at(0)->fd`（DTCM 内）= top chunk 地址。遍历堆时最后 chunk 应 = top。

## do_check_chunk assert（dlmalloc DEBUG 检查）

```c
static void do_check_chunk(osDlmalloc_t *heap, mchunkptr p) {
    size_t sz = p->size & ~PREV_INUSE;
    OS_ASSERT(!chunk_is_mmapped(p));              // :536
    OS_ASSERT((char*)p >= heap->base);            // :539  ← p 落在堆基址前
    if (p != top(heap->num))
        OS_ASSERT((char*)p + sz <= (char*)top(heap->num));  // :542  ← chunk 越过 top
    else
        OS_ASSERT((char*)p + sz <= heap->base + heap->total); // :545
}
```

| 行 | 触发条件 | 典型根因 |
|----|---------|---------|
| 536 | chunk 被 mmap | 罕见 |
| **539** | **`p < heap->base`** | free-list 遍历落到非堆地址（某 free chunk 的 bk 被改）/ victim 野指针 |
| 542 | `p + sz > top` | top 附近 chunk 的 size 被改大 |
| 545 | top chunk `p+sz > base+total` | top size 损坏 |

### 进入 do_check_chunk 的路径

1. **`dlMalloc` line 1138**：`top chunk 剩余 < MINSIZE`（堆耗尽）→ 调 `dlmallocPrint(heap,1)` + `OS_ASSERT(0)`
2. **`osMallocTrace` line 1848**：采样 dump 检查 → `dlmallocPrint`。**默认禁用**（`g_osApSystemMemDumpRate=100`，条件 `!= 100` 才触发）；仅当固件显式改了该 NV 才走此路径
3. `dlmallocPrint`(line 798-801) 遍历每个 bin 的 free chunk，调 `check_free_chunk` → `do_check_free_chunk` → `do_check_chunk`
4. `dlMalloc` 取 victim 后调 `check_malloced_chunk` → `do_check_malloced_chunk` → `do_check_chunk`

> **关键**：路径 1/2（`dlmallocPrint`）在**堆耗尽**时触发，遍历中若撞见链表异常也会报 539。**不要一见 539 就断定独立 corruption**——先看堆使用率（耗尽也会触发 539）。

## 耗尽 vs 损坏 判定树

```
读 g_osApSystemMem.used / total
│
├── used/total > 95%  →  堆耗尽（高危）
│   │
│   └── 堆物理遍历（heap_walker.py）：
│       ├── 所有 chunk size 合法 + gap=0 + top<64B  →  纯耗尽（堆物理完整）
│       ├── 某个 chunk size 异常/越界               →  堆内存被越界写粉碎
│       └── 物理完整 + 某 free chunk fd/bk 指向非法  →  free-list 链接损坏
│
└── used/total 正常  →  非耗尽，查损坏来源
    └── 物理遍历定位破坏点 → 越界写 / use-after-free
```

### 堆物理遍历算法（`heap_walker.py`）

```
p = heap->base
while p < heap->base + heap->total:
    size_raw = mem[p + 4]
    sz = size_raw & ~0x3                      # chunksize
    if sz < MINSIZE or sz > total or (sz&7)!=0 or p+sz > end:
        → CORRUPTION at p（记录 prev_size/size_raw/邻 chunk）
        break
    inuse = mem[p + sz + 4] & PREV_INUSE      # 下一 chunk 说本 chunk 在用
    if not inuse:
        fd = mem[p+8]; bk = mem[p+12]
        检查 fd/bk 是否合法（见下）
    p += sz
```

### free chunk fd/bk 合法性判定

| fd/bk 指向 | 判定 |
|-----------|------|
| DTCM `0x10000~0x10400`（bin header 区） | **正常**（指回 `av_` bin header） |
| 本堆区 `[base, base+total)` | **正常**（指向另一 free chunk） |
| 其他地址（`<base` 或越界） | **损坏**（free-list 链接被破坏） |

> 例：本次 dump 物理遍历 204 个 free chunk，大量 fd/bk 指向 `0x10010/0x10018/0x10020...`（间距 8，bin header），全部正常。只有栈帧寄存器里的 `0x10038`（bin-6 header）被 do_check_chunk 当 chunk 检查才触发 539——是 free-list 某个 bk 把遍历引到了 bin header。

## 堆耗尽户定位

### malloc trace ring（`gOsiMemRecords`）

每条 `{caller, ptr}`：`caller` 字段 **bit31 = alloc(1)/free(0) 标志**，`[30:0]` = 调用者地址 `>>1`。解码：`is_alloc = caller & 0x80000000`；`addr = ((caller & 0x7FFFFFFF) << 1)`。`heap_state.py` 只统计 **alloc** 调用者（free 会污染排名）。

- 统计占比最大的 caller → 主要堆消耗户
- 配对分析：alloc caller vs free caller（如 `osMsgSend`/`osMsgRecv` 成对，`Ps_LpmCallback` 两处偏移可能 alloc+free）

> **陷阱**：ring 末尾**不是**崩溃时刻的 alloc（见 `stack-unwind-guide.md` 陷阱 2）。崩溃时刻调用者看栈。

### SLOG ISR 日志堆积

`g_slogIsrLogTotalLen`（限流阈值 `SLOG_ISR_LOG_MAX_SIZE=1024`）顶满 → ISR 日志生产 > 消费。`SLOG_GetCommBuffer` 的 ISR 路径每次 `osMalloc(sizeof(SLOG_CachedIntPrint)+len)`（slog_helper.c:1577），由任务上下文 `SLOG_CheckCachedIntPrints` 回收，回收滞后则堆积。

`g_slogBufPool.cachedIntPrints`（偏移 `+0xa8`）是 ISR 日志队列，遍历可看具体日志内容（解码见 dlmallocPrint 输出等格式）。

## 常见根因模式

| 现象 | 根因 |
|------|------|
| 堆 99%+ 满 + top<64B + ISR 内 osMalloc | **堆耗尽**（ISR 高频分配 + 回收滞后） |
| 堆物理完整 + free-list bk 损坏 | use-after-free（ISR alloc 与任务 free 并发） |
| 某个 chunk size 异常 | 越界写（相邻 chunk header 被覆盖） |
| dlmalloc.c:1138 + top<64B | 纯耗尽（top 不足分支） |
| dlmalloc.c:539 + 堆正常 | 独立 free-list 损坏（查 UAF） |
