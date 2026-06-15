# 内存泄漏定位：call-stack 追踪法（通用指南）

> 本文是 `spec-memory-leak-analyzer` 技能的方法论参考。核心思路：在内存分配/释放点埋点，记录**调用者返回地址**，按地址配对，定位"只分配不释放"的代码位置，再用 MAP/addr2line 映射回源码。方法与平台无关，覆盖 GCC/ARMCC/IAR/MSVC 工具链。

## 1. 为什么这样做

内存泄漏的本质是 `分配次数 − 释放次数 > 0`。但光知道"漏了"没用，要回答**在哪漏**。

调用者地址（caller / return address）是埋点时由 `__builtin_return_address(0)` 等接口拿到的"是谁调用了我"的代码地址。每块内存记录下分配它的 caller，配对时未匹配到释放的块，其 caller 就直接指向泄漏的代码位置——再把这个地址映射回函数名/行号即可。

| 场景 | 适用性 | 说明 |
|------|--------|------|
| 周期性泄漏 | ★★★★★ | 每个业务周期后内存持续减少，最强项 |
| 偶发性泄漏 | ★★★★☆ | 需多次复现收集数据 |
| 大块泄漏 | ★★★★★ | 泄漏量明显，易追踪 |
| 碎片化泄漏 | ★★★☆☆ | 需结合堆分析工具（本法的字节级配对仍有帮助） |

**优势**：侵入性小（只改内存管理模块，业务无感知）、开销低（只增日志）、信息丰富（caller 地址 + 分配大小 + 内存地址）、可自动化（日志格式固定）。

## 2. 平台兼容性：能否取调用者地址

整个方法的前提是平台能拿到调用者返回地址。各工具链接口：

| 工具链 | 接口 | 状态 |
|--------|------|------|
| GCC (ARM/x86) | `__builtin_return_address(0)` | ✅ 完全支持 |
| ARMCC (Keil armclang/armcc) | `__return_address()` | ✅ 需调整语法 |
| MSVC | `_ReturnAddress()`（需 `#include <intrin.h>`） | ✅ |
| IAR ICCARM | 内联汇编读取 LR | ⚠️ 需特殊处理 |

> EC626 平台用的是 IAR ICCARM，**没有 `__builtin_return_address`**——这正是 `spec-bug-analyzer` 处理的那个 CoAP 泄漏案改用"类型标记配对(CMA/CMF)+堆趋势对比"的原因。本 call-stack 追踪法在 GCC/ARMCC 平台（如 Unisoc 8910、RDA、Quectel 下的 GCC 构建）上最顺。

**验证方法**：写个最小测试，打印 `__builtin_return_address(0)`，再用 MAP 文件确认地址落在调用函数内。

```c
#include <stdio.h>
extern void* __builtin_return_address(unsigned int level);  /* GCC */

void test_callee(void) {
    printf("Caller address: %p\n", __builtin_return_address(0));
}
void test_caller(void) { test_callee(); }
int main(void) { test_caller(); return 0; }
```

编译运行，输出应是一个有效的代码地址；多级调用 `level=1,2...` 在嵌入式上可能不支持，依赖 `level=0`（直接调用者）即可。

## 3. 标准日志格式

埋点输出必须固定格式，`scripts/mem_leak_analyzer.py` 才能解析。字段顺序无关（按 `key=value` 解析），但字段名固定：

```
MEM_ALLOC: addr=0x3a4b0, size=128, caller=0x60102169
MEM_FREE:  addr=0x3a4b0, caller=0x60102169
MEM_HEAP:  total=67168, free=23008, used=44160
```

| 字段 | 格式 | 说明 |
|------|------|------|
| 标签 | `MEM_ALLOC` / `MEM_FREE` / `MEM_HEAP` | 脚本识别用，兼容旧式 `MALLOC`/`MFREE`/`HEAP` |
| addr | `0x` + 十六进制 | 内存块地址 |
| size | 十进制 | 字节数（仅 ALLOC） |
| caller | `0x` + 十六进制 | 调用者返回地址 |
| total/free/used | 十进制 | 堆状态（仅 HEAP，可选） |

## 4. `mem_trace.h` 模板

直接套用，按平台改两个宏（日志接口、caller 接口）即可。

```c
#ifndef MEM_TRACE_H
#define MEM_TRACE_H

#include <stdlib.h>

/*=== 配置开关 =============================================================*/
#ifndef MEM_TRACE_ENABLE
#define MEM_TRACE_ENABLE   1     /* 1=启用追踪, 0=禁用（发布版置 0） */
#endif

/*=== 日志输出接口（按平台适配）=============================================*/
#ifndef MEM_TRACE_LOG
#define MEM_TRACE_LOG(fmt, ...)  OSI_LOGI(0x0, fmt, ##__VA_ARGS__)  /* 示例: Unisoc */
#endif

/*=== 调用者地址接口（按平台适配）==========================================*/
#ifndef MEM_TRACE_CALLER
#define MEM_TRACE_CALLER()  __builtin_return_address(0)             /* 示例: GCC */
#endif

/*========================================================================*/
#if MEM_TRACE_ENABLE

static inline void* mem_trace_calloc(size_t count, size_t size) {
    void *ptr = calloc(count, size);
    MEM_TRACE_LOG("MEM_ALLOC: addr=%p, size=%u, caller=%p",
                  ptr, (unsigned int)(count * size), MEM_TRACE_CALLER());
    return ptr;
}

static inline void* mem_trace_malloc(size_t size) {
    void *ptr = malloc(size);
    MEM_TRACE_LOG("MEM_ALLOC: addr=%p, size=%u, caller=%p",
                  ptr, (unsigned int)size, MEM_TRACE_CALLER());
    return ptr;
}

static inline void mem_trace_free(void *ptr) {
    MEM_TRACE_LOG("MEM_FREE: addr=%p, caller=%p", ptr, MEM_TRACE_CALLER());
    free(ptr);
}

/* realloc 必须拆成 free + alloc 两条记录，否则配对错乱 */
static inline void* mem_trace_realloc(void *ptr, size_t size) {
    if (ptr) MEM_TRACE_LOG("MEM_FREE: addr=%p, caller=%p", ptr, MEM_TRACE_CALLER());
    void *np = realloc(ptr, size);
    MEM_TRACE_LOG("MEM_ALLOC: addr=%p, size=%u, caller=%p",
                  np, (unsigned int)size, MEM_TRACE_CALLER());
    return np;
}

static inline void mem_trace_heap(size_t total, size_t free_size, size_t used) {
    MEM_TRACE_LOG("MEM_HEAP: total=%u, free=%u, used=%u",
                  (unsigned int)total, (unsigned int)free_size, (unsigned int)used);
}

#else /* 发布版：零开销 */

#define mem_trace_calloc(c, s)   calloc(c, s)
#define mem_trace_malloc(s)      malloc(s)
#define mem_trace_free(p)        free(p)
#define mem_trace_realloc(p, s)  realloc(p, s)
#define mem_trace_heap(t, f, u)  ((void)0)

#endif
#endif /* MEM_TRACE_H */
```

### 各平台适配取值

| 平台 / 工具链 | `MEM_TRACE_LOG` | `MEM_TRACE_CALLER` |
|------|------|------|
| Unisoc 8910 (GCC) | `OSI_LOGI(0, fmt, ...)` | `__builtin_return_address(0)` |
| RDA (GCC) | 平台 trace 宏 | `__builtin_return_address(0)` |
| Quectel (GCC) | `Ql_Debug_Trace(fmt, ...)` | `__builtin_return_address(0)` |
| Keil ARMCC | 平台 log 宏 | `__return_address()` |
| 任意自定义 | 改 `MEM_TRACE_LOG` 宏 | 改 `MEM_TRACE_CALLER` 宏 |

## 5. 业务分析（埋点前先摸清内存层）

典型嵌入式项目的内存管理层级：

```
应用层业务代码      nwy_ssl_tcp.cpp / nwy_http.cpp / coap_task.c ...
        ↓
统一内存接口层      ds_appsrv_alloc() / nwy_malloc() ...
        ↓
平台抽象层          nwy_modem_mem_alloc() / mbedtls_calloc() ...
        ↓
系统层             malloc / calloc / free
```

埋点要尽量落在**接近系统层**的位置（覆盖面最广），但要识别**绕过统一接口的直接调用**：

```bash
# 列出所有直接 malloc/calloc/free 调用
grep -rn "malloc\s*("  --include="*.c" --include="*.cpp" components/ PLAT/
grep -rn "calloc\s*("  --include="*.c" --include="*.cpp" components/ PLAT/
grep -rn "free\s*("    --include="*.c" --include="*.cpp" components/ PLAT/
# 第三方库的内存接口
grep -rn "mbedtls_calloc\|mbedtls_free" thirdparty/ third_party/
```

产出一张《内存接口使用清单》：文件 / 行号 / 原始调用 / 所属模块 / 是否需要替换。

## 6. 接口替换

| 原调用 | 替换为 |
|--------|--------|
| `malloc(size)` | `mem_trace_malloc(size)` |
| `calloc(n, size)` | `mem_trace_calloc(n, size)` |
| `free(ptr)` | `mem_trace_free(ptr)` |
| `realloc(ptr, size)` | `mem_trace_realloc(ptr, size)`（内部已拆 free+alloc） |

**第三方库**通过宏重定向，不改库源码：

```c
#include "mem_trace.h"
#define mbedtls_calloc(n, size)   mem_trace_calloc(n, size)
#define mbedtls_free(ptr)         mem_trace_free(ptr)
```

> **这是配对正确性的关键**：只要有一条路径用裸 `free()` 释放了用追踪接口分配的内存（或反过来），就会出现"无对应分配的释放"或"假泄漏"。替换后务必 grep 复查，并保证编译无警告、功能测试通过。

## 7. 数据采集

1. 烧录**带追踪功能**的固件
2. 执行业务测试流程（如 SSL 连接 → 数据收发 → 断开，或 COAP CLOSE→OPEN→SEND 循环）
3. 关键节点查堆状态（如 `AT^HEAPINFO` / `AT+ECSHOWMEM`，对应 `MEM_HEAP` 日志）
4. **重复 10+ 周期**（周期性泄漏需多周期才看得出趋势）
5. 导出完整 AP 日志（避免缓冲溢出丢日志，建议串口/USB 全量导出）
6. 备份 MAP 文件与 ELF——**版本必须与烧录固件一致**

交付清单：AP 日志 + MAP 文件（+ 可选 ELF / 源码）+ 测试说明（场景、周期数、泄漏现象）。

## 8. 地址定位到源码

脚本给出 caller 地址 → 函数名后，进一步定位到行号：

**方法 1：MAP 文件搜索**——找包含地址范围的符号：
```
地址 0x60102169 → 在 MAP 中找：
 .text.entropy_gather_internal.part.0   0x60102114   0x82  lib/libmbedtls.a(entropy.c.obj)
```

**方法 2：addr2line（最权威）**：
```bash
arm-none-eabi-addr2line -e firmware.elf -f 0x60102169
# entropy_gather_internal
# library/entropy.c:128
```

**方法 3：objdump 反汇编**：
```bash
arm-none-eabi-objdump -d firmware.elf | grep -A 20 "60102114"
```

### 内联函数处理

定位到带 `.part.` / `.isra.` 后缀 → 被编译器内联，真实调用者在附近符号：

```
定位: entropy_gather_internal.part.0
  ↓ 查附近符号 → mbedtls_hardware_poll.str1.1
  ↓ 推断真实调用位置: entropy_poll.c 中的 mbedtls_hardware_poll()
```

必要时给可疑函数加 `__attribute__((noinline))` 重新编译，再测一次。

## 9. 局限性与排查

| 现象 | 原因 | 应对 |
|------|------|------|
| 日志里没有追踪记录 | 仍有裸 malloc/free，或走了别的内存接口 | grep 复查替换完整性 |
| 地址映射不上源码 | MAP/ELF 与固件版本不一致；编译优化地址偏移 | 用 `-O0 -g` 调试版；核对版本 |
| "无对应分配的释放"很多 | 配对错位（分配用追踪、释放用裸 free，或反之） | 复查 Step 6 替换；realloc 是否拆条 |
| 泄漏结果不稳定 | 日志丢失；异步释放延迟 | 增大日志缓冲；多跑周期看趋势 |
| 多线程日志交错 | 线程并发 | 日志加序号/时间戳；配对按地址仍正确 |

**性能**：日志输出是主要开销，仅在调试版启用（`MEM_TRACE_ENABLE=1`），发布版置 0 即零开销。

## 10. 附录：无法取 caller 地址时的替代手法

当平台不支持 `__builtin_return_address`（如 IAR ICCARM、部分 RTOS），call-stack 追踪法不可用，退化为以下间接手法（定位精度下降，但仍有效）：

**A. 类型标记配对法（CMA/CMF）**：在分配接口里带一个"类型 tag"（如 CoAP 的 `coap_memory_tag_t` t=0..12），日志记 `CMA type=N addr=...` / `CMF type=N addr=...`，按类型统计 alloc/free 差值，定位是哪类对象在漏。EC626 的 CoAP 泄漏案即用此法（`COAP_MEM_STATS=1`）确认 OPTLIST(t=12) 100% 泄漏。

**B. 堆趋势对比法**：在两种操作模式下定期采样 `free heap`（如 `AT+ECSHOWMEM`），对比哪个模式下堆单调下降。能定位到"哪类操作在漏"，再结合代码审查找释放缺失点。CoAP 案的关键观察就是"单会话连续发送堆稳定、CLOSE→OPEN 循环堆持续下降"，直接把嫌疑锁定在 close/open 路径。

**C. 分配者记录法（MM_TRACE_ON）**：若平台堆管理器自带调用者记录（如 FreeRTOS heap 的 `trace_node`），开启后 dump 里会带每个未释放块的分配者信息，效果接近 call-stack 追踪。

这三种手法精度递减，但都不依赖 `__builtin_return_address`，可在 IAR/受限平台上使用。
