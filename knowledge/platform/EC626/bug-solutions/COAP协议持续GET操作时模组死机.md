# COAP协议持续GET操作时模组死机 原因分析

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **平台** | EC626 |
| **模块** | CoAP/libcoap/堆内存 |
| **问题分类** | 资源耗尽 |
| **症状关键词** | COAP死机, 堆内存泄漏, OPTLIST泄漏, LL_SORT bug, pvPortMallocEC返回NULL, ASSERT死机, COAPCLOSE循环, 内存持续下降 |
| **根因概述** | COAP客户端在COAPCLOSE→COAPOPEN循环中存在heap内存泄漏，OPTLIST（t=12）100%泄漏率（120 allocs / 0 frees）。根因是coap_add_optlist_pdu()中的LL_SORT宏在ICCARM编译器NO_DECLTYPE模式下，将*options置为NULL后无法正确恢复，导致6个optlist节点（~143B/周期）全部泄漏，最终堆内存耗尽触发ASSERT死机 |
| **调用链摘要** | COAPSEND → coap_new_request → coap_add_optlist_pdu → LL_SORT((*options), order_opts) → *options=NULL → OPTLIST泄漏 → 堆耗尽 → coapSlpDeInit → coap_recv_task_Init → xTaskCreate → pvPortMallocEC(1575) → NULL → ASSERT死机 |
| **检索关键词** | COAP死机, OPTLIST泄漏, LL_SORT, NO_DECLTYPE, ICCARM, utlist, coap_add_optlist_pdu, 堆内存泄漏, pvPortMallocEC NULL, EC626 COAP, CoAP close循环, 内存耗尽死机 |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
  - [2.1 确切的死机位置（Dump 分析确认）](#21-确切的死机位置dump-分析确认)
  - [2.2 关键代码分析：为什么是 heap 泄漏而非 memp 池泄漏](#22-关键代码分析为什么是-heap-泄漏而非-memp-池泄漏)
  - [2.3 关键日志证据 — 内存泄漏趋势](#23-关键日志证据--内存泄漏趋势)
  - [2.4 代码调用链与泄漏分析](#24-代码调用链与泄漏分析)
  - [2.5 泄漏源分析（按可能性排序）](#25-泄漏源分析按可能性排序)
  - [2.6 OPTLIST 泄漏根因确认（已验证）](#26-optlist-泄漏根因确认已验证)
  - [2.7 问题复现路径](#27-问题复现路径)
  - [2.8 下一步验证方案](#28-下一步验证方案)
- [3. 相关文件](#3-相关文件)
  - [3.1 核心代码文件](#31-核心代码文件)
  - [3.2 libcoap 库文件](#32-libcoap-库文件)
  - [3.3 LWIP 配置文件](#33-lwip-配置文件)
  - [3.4 分析报告文件](#34-分析报告文件)
  - [3.5 工具和日志文件](#35-工具和日志文件)

---

## 1. 问题描述

模组在执行 COAP 协议持续 GET 操作时，经过多次 COAPCLOSE→COAPOPEN→COAPSEND 循环后，出现内存持续下降，最终因堆内存耗尽导致模组死机重启。

**问题类型**：日志分析 + Crash Dump 分析

**复现条件**：执行 COAPCLOSE→COAPOPEN→COAPADDRES→COAPOPTION→COAPSEND 循环操作，约 80-90 个周期后模组死机。

---

## 2. 根本原因

### 一句话结论

**COAP 客户端在 COAPCLOSE→COAPOPEN 循环中存在 heap 内存泄漏（`coap_malloc_type` = `malloc()`），每次循环泄漏约 170-250 字节的 COAP 对象，最终堆内存耗尽。系统休眠唤醒流程中 `coapSlpDeInit → coap_recv_task_Init → xTaskCreate` 需要分配 TCB 时 `pvPortMallocEC` 返回 NULL 触发 ASSERT 死机。**

> **已确认根因（2026-06-11）**：CMA/CMF 追踪分析确认 **OPTLIST（t=12）是主要泄漏源**，100% 泄漏率（120 allocs / 0 frees）。根因是 `coap_add_optlist_pdu()` 中的 `LL_SORT` 宏在 ICCARM 编译器 `NO_DECLTYPE` 模式下，将 `*options` 置为 NULL 后无法正确恢复，导致 6 个 optlist 节点（~143B/周期）全部泄漏。**方案A（局部变量隔离 LL_SORT）已验证修复成功。**

> **重要修正**：Dump 分析显示 LWIP memp 池全部耗尽，但经代码验证：① `MEMP_MEM_MALLOC=0`，memp 池使用独立 BSS 内存，与 FreeRTOS heap 完全分离，不会导致 `pvPortMallocEC` 失败；② memp 池是系统公用资源，COAP 仅使用 UDP_PCB + NETCONN 各 1 个，不可能耗尽全部 17 个池；③ 真正导致死机的是 COAP 自身 `malloc()` 分配的对象泄漏，而非 LWIP memp 池耗尽。memp 池耗尽是共发现象。

### 2.1 确切的死机位置（Dump 分析确认）

| 项目 | 值 | 含义 |
|------|-----|------|
| **异常类型** | ASSERT (reset_reason=2) | 软件断言失败，非硬件异常 |
| **死机 PC** | `0x0087AF3C` | `pvPortMallocEC()` heap_6.c:101 |
| **死机 LR** | `0x00879F87` | `xTaskCreate()` tasks.c:680 |
| **R0** | `0x00000000` | malloc 返回 NULL |
| **R6** | `0x00000627` | 申请的 TCB 大小 = 1575 字节 |

**精确调用链**：
```
pmuEnterPagingSlp()           ecpm_ec626.c:5140    ← 系统进入休眠
  └→ coapSlpDeInit()          at_coap_task.c:3124   ← CoAP 休眠去初始化
       └→ coap_recv_task_Init()  at_coap_task.c:2351 ← 重建 CoAP 接收任务
            └→ osThreadNew() → xTaskCreate()         tasks.c:680
                 └→ pvPortMallocEC(1575)             heap_6.c:101
                      └→ 返回 NULL → EC_ASSERT → 死机！
```

**Assert 详情**：
```
Assert 文件: heap_6.c
Assert 行号: 101
Assert 描述: EC_ASSERT(0, v1=237948, v2=99588, v3=0) — malloc 返回 NULL
```

### 2.2 关键代码分析：为什么是 heap 泄漏而非 memp 池泄漏

#### 证据 1：`coap_malloc_type()` 在 WITH_POSIX 构建下就是 `malloc()`

项目构建使用 `WITH_POSIX`（MakefileNoDtls.inc:37 `CFLAGS += -DWITH_POSIX`），而非 `WITH_LWIP`。

`mem.h` 中的条件编译：
```c
// WITH_POSIX 路径（实际编译）
// line 48-86: #ifndef WITH_LWIP
void *coap_malloc_type(coap_memory_tag_t type, size_t size);  // 函数声明

// WITH_LWIP 路径（未编译）
// line 88-114: #ifdef WITH_LWIP
#define coap_malloc_type(type, size) memp_malloc(MEMP_ ## type)  // 宏，用 memp 池
```

`mem.c:35-37` 中的实际实现：
```c
void *coap_malloc_type(coap_memory_tag_t type, size_t size) {
  (void)type;
  return malloc(size);  // ← 直接 malloc，分配到 FreeRTOS heap
}
```

**结论**：COAP 所有分配（context、session、resource、PDU、optlist、string 等）全部走 **FreeRTOS heap (`pvPortMallocEC`)**，不走 LWIP memp 池。

#### 证据 2：`MEMP_MEM_MALLOC = 0` → memp 池与 heap 独立

`lwip_config_ec6160h00.h:320`：`#define MEMP_MEM_MALLOC 0`

LWIP memp 池使用预分配的 BSS 静态内存（编译时确定），与 FreeRTOS heap_6.c 管理的堆内存**完全独立**。memp 池耗尽**不会**导致 `pvPortMallocEC` 返回 NULL。

#### 证据 3：`LWIP_STATS = 0` → lwip_stats 计数器不可用

`lwip_config_ec6160h00.h:2072`：`#define LWIP_STATS 0`

LWIP 统计功能编译时已裁掉，`lwip_stats.memp[].used/avail` 计数器不存在。通过 `lwip_stats` 接口监控 memp 池利用率的方案**不可行**。

#### 证据 4：memp 池是系统公用资源

COAP 仅通过 `socket()` 创建 1 个 UDP socket，只消耗 **UDP_PCB ×1 + NETCONN ×1**。Dump 显示全部 17 个池（含 TCP_PCB、DNS_API_MSG、NETDB 等非 UDP 相关池）均为 100%。这些池被其他系统模块（TCP 连接管理、DNS 解析等）共同使用，**不能全部归因于 COAP**。

### 2.3 关键日志证据 — 内存泄漏趋势

#### AT 命令日志（ECSHOWMEM 数据）

**阶段 1：单会话内连续 COAPSEND（无 COAPCLOSE），内存稳定**
```
[09:10:27] curr_free_heap:34588...min_free_heap:23008    ← 初始值
[09:11:00] curr_free_heap:33420...min_free_heap:23008    ← 稳定
[09:13:52] curr_free_heap:33384...min_free_heap:23008    ← 48次发送后仍稳定
```

**阶段 2：开启 COAPCLOSE→COAPOPEN 循环，min_free_heap 持续下降**
```
[09:14:54] curr_free_heap:34804...min_free_heap:23008    ← COAPCLOSE后回收
[09:15:00] curr_free_heap:33200...min_free_heap:22788    ← 第1次循环: ↓220
[09:16:55] curr_free_heap:32304...min_free_heap:22064    ← ↓944 (多次循环累计)
[09:21:17] curr_free_heap:31296...min_free_heap:20852    ← ↓2156
[09:29:05] curr_free_heap:26816...min_free_heap:20504
[09:35:05] curr_free_heap:23352...min_free_heap:16964
[09:40:11] curr_free_heap:20468...min_free_heap:14060
[09:44:23] curr_free_heap:18128...min_free_heap:11836    ← 接近耗尽
```

**阶段 3：设备重启后继续操作，内存再次持续下降**
```
[09:50:17] curr_free_heap:58568...min_free_heap:41168    ← 重启后恢复
[10:23:11] curr_free_heap:24512...min_free_heap:18224    ← 再次下降
[10:25:17] curr_free_heap:23276...min_free_heap:16908    ← 日志截止（死机）
```

> **关键对比**：单会话连续发送时内存不泄漏（min_free_heap 稳定在 23008），仅在 COAPCLOSE→COAPOPEN 循环中泄漏。**这明确排除了发送/接收路径的嫌疑，定位到 close/open 循环中 COAP 对象未正确释放。**

#### 内存泄漏量化

| 操作周期 | 时间 | min_free_heap | 累计泄漏 |
|----------|------|---------------|----------|
| 初始 | 09:10:27 | 23008 | 0 |
| ~1次循环后 | 09:15:00 | 22788 | 220 |
| ~9次循环后 | 09:16:55 | 22064 | 944 |
| ~20次循环后 | 09:21:17 | 20852 | 2156 |
| ~70次循环后 | 09:35:05 | 16964 | 6044 |
| ~85次循环后 | 09:44:23 | 11836 | **11172** |
| **设备死机** | ~09:44:35 | **耗尽** | **ASSERT** |

> 总堆约 67KB（`EC heap size is 67168`），min_free_heap 从 23008 降至 11836 后死机。平均每循环泄漏约 **130-250 字节**。

### 2.4 代码调用链与泄漏分析

| 信息 | 值 |
|------|-----|
| **崩溃入口** | `coapSlpDeInit()` at_coap_task.c:3124 |
| **崩溃调用链** | `coapSlpDeInit → coap_recv_task_Init → xTaskCreate → pvPortMallocEC → ASSERT` |
| **泄漏触发路径** | COAPCLOSE → COAPOPEN → COAPADDRES → COAPOPTION → COAPSEND（循环） |

**COAP 生命周期中的资源分配/释放对照**：

所有 COAP 对象通过 `coap_malloc_type()` = `malloc()` 分配到 **FreeRTOS heap**。LWIP memp 池仅在 socket 创建时消耗 UDP_PCB + NETCONN。

| 步骤 | 操作 | 分配的 heap 资源 (malloc) | 分配的 LWIP memp 资源 | 释放情况 |
|------|------|---------------------------|----------------------|----------|
| 1. COAPOPEN | `coap_create_client()` | `coap_context_t` (~200B) | 无 | ✅ `coap_free_context()` 释放 |
| 2. COAPADDRES | `coap_config_client()` | `coap_resource_t` + `coap_attr_t` (~100-200B) | 无 | ⚠️ 见分析 |
| 3. COAPOPTION | `coap_config_client()` | `coap_optlist_t` × 6 (~300-600B) | 无 | ✅ `coap_delete_optlist()` 释放 |
| 4. COAPSEND | `coap_open_client()` | `coap_session_t` (~200B) + `coap_pdu_t` + buf | **UDP_PCB×1 + NETCONN×1** | ⚠️ 见分析 |
| 5. 响应接收 | `coap_message_handler()` | `coap_token` + `coap_payload` + `recv_optlist_buf`(512B) | 无 | ✅ `coapRecvInd()` 释放 |
| 6. COAPCLOSE | `coap_delete_client()` | — | — | ⚠️ 见下方 |

**`coap_delete_client()` 中的释放操作**（at_coap_task.c line 1042-1091）：
```c
// 1. 释放 optlist
coap_delete_optlist(coapClient->coap_optlist);     // line 1060

// 2. 释放 ip 字符串
free(coapClient->coap_ip);                         // line 1063

// 3. 清理 resource — ⚠️ 代码被注释掉！
if(coapClient->coap_resource != NULL) {
    //coap_delete_all_resources(coapClient->coap_ctx);   // ← 被注释
    //free(coapClient->coap_resource);                    // ← 被注释
    //coapClient->coap_resource = NULL;                   // ← 被注释
    ret = COAP_OK;
}

// 4. 释放 session
coap_session_release(coapClient->coap_session);    // line 1076
coapClient->coap_session = NULL;

// 5. 释放 context
coap_free_context(coapClient->coap_ctx);           // line 1083
coapClient->coap_ctx = NULL;

// 6. 全局清理
coap_cleanup();                                     // line 1088
```

**注意**：`coap_free_context()` (net.c:553) 内部会调用 `coap_delete_all_resources()`，所以步骤 3 的注释代码虽然冗余但 `coap_free_context` 理论上会处理。但如果 context 结构的 resource 链表状态异常（如 resource 被手动 free 但链表未摘除），可能导致 `coap_free_context` 内部释放不完整。

### 2.5 泄漏源分析（按可能性排序）

#### 泄漏源 1：`coap_session_t` 未释放（heap 泄漏 ~200B/次 + LWIP memp 资源）

**证据**：
- 每次泄漏 170-250B，与 `coap_session_t` 大小匹配
- `coap_session_release()` 仅在 `ref == 0 && type == CLIENT` 时才调用 `coap_session_free()`
- 如果 `session->ref > 1`（被 `coap_read()` 中的 `coap_session_reference()` 递增但未配对 release），session 结构体 + socket 都泄漏

**libcoap session 释放机制**：
```c
void coap_session_release(coap_session_t *session) {
  if (session) {
    assert(session->ref > 0);
    if (session->ref > 0)
      --session->ref;
    if (session->ref == 0 && session->type == COAP_SESSION_TYPE_CLIENT)  // ← 两个条件
      coap_session_free(session);    // → coap_session_mfree → coap_socket_close → close()
  }
}
```

`coap_session_free` → `coap_session_mfree` 会调用 `coap_socket_close()` 释放 socket。如果 session 未被 free，则：
- **heap 泄漏**：`coap_session_t` 结构体 (~200B)
- **memp 泄漏**：socket 关联的 UDP_PCB + NETCONN 未释放

**可能的机制**：
1. `coap_read()` (net.c:1410) 在处理过程中 `coap_session_reference(session)` 递增 ref，如果处理过程中发生错误导致 `coap_session_release()` 未被调用
2. `coap_new_client_session()` 创建后 ref=1，如果 `coap_session_connect()` 内部又递增了 ref

**验证方法**：在 `coap_session_release` 中打印 `session->ref` 值（见方案 B）。

#### 泄漏源 2：全局 `aliAuthToken`/`aliRandom` 在 COAPCLOSE 时未释放

`at_coap_task.c` line 144-145 定义了两个全局指针：
```c
static coap_string_t *aliAuthToken = NULL;    // line 144
static coap_string_t *aliRandom = NULL;       // line 145
```

这两个指针在 `coap_message_handler()` 中被分配（line 529/544），每次重新分配前会先释放旧值。但在 `coap_delete_client()` 中**完全未释放**。

- `aliAuthToken`：通过 `coap_new_string(size)` = `coap_malloc_type(COAP_STRING, ...)` = `malloc()` 分配
- `aliRandom`：同上

**关闭客户端后**，这两个指针仍然指向已分配的 heap 内存，形成泄漏。每次分配大小取决于 token/random 内容，可能数十到数百字节。

#### 泄漏源 3：`recv_optlist_buf`（512B）在 `applSendCmsInd` 失败时泄漏

`coap_message_handler()` (line 420) 每次接收响应分配 `recv_optlist_buf`（512 字节），通过 `applSendCmsInd()` 传递给 `coapRecvInd()` 释放。

如果 `applSendCmsInd()` 返回失败，512 字节泄漏。但此情况发生概率较低。

#### 泄漏源 4：LWIP memp 池资源未释放（共发现象）

Dump 显示全部 17 个 LWIP memp 池均 100% 耗尽。这些池是系统公用资源：
- COAP 直接使用：UDP_PCB（每次 socket 创建 1 个）、NETCONN（每次 socket 创建 1 个）
- 其他系统使用：TCP_PCB、TCP_SEG、DNS_API_MSG、NETDB 等

**可能的关联机制**：如果泄漏源 1 成立（`coap_session_t` 未释放 → socket 未关闭），则每次循环会泄漏 UDP_PCB + NETCONN 各 1 个。9 次循环后 UDP_PCB 池（9个）和 NETCONN 池（9个）就会耗尽。其他池的耗尽可能来自系统其他网络操作。

### 2.6 OPTLIST 泄漏根因确认（已验证）

> **详细分析报告**：见 `内存泄漏分析.md` 和 `OPTLIST泄漏根因分析（最终版）.md`

#### 2.6.1 CMA/CMF 追踪结果

通过 `COAP_MEM_STATS=1` 启用 ECOMM_TRACE 日志，追踪 `coap_malloc_type`/`coap_free_type` 的分配/释放：

| 类型 | 名称 | CMA(分配) | CMF(释放) | 差值 | 说明 |
|------|------|-----------|-----------|------|------|
| t=0 | STRING | 270-282 | 237-240 | 30-45 | 含追踪盲区（plain free） |
| t=3 | PACKET | 17-21 | 0-3 | 17-19 | 真实泄漏 |
| t=4 | NODE | 19-20 | 1-4 | 16-19 | 真实泄漏 |
| t=5 | CONTEXT | 20 | 19-20 | 0-1 | 正常 |
| t=7 | PDU | 37-41 | 2-6 | 34-39 | 含 realloc 盲区 |
| t=8 | PDU_BUF | 37-41 | 2-4 | 33-39 | 含 realloc 盲区 |
| t=9 | RESOURCE | 19-20 | 19-20 | 0 | 正常 |
| t=10 | RESATTR | 19-20 | 19-20 | 0 | 正常 |
| t=11 | SESSION | 20 | 19-20 | 0-1 | 正常 |
| **t=12** | **OPTLIST** | **120** | **0** | **120** | **⚠️ 100% 泄漏** |

**关键发现**：OPTLIST（t=12）100% 泄漏率，120 次分配 / 0 次释放，每周期泄漏 6 个 optlist 节点（~143B）。

#### 2.6.2 根因分析

**根因**：`coap_add_optlist_pdu()` 中的 `LL_SORT` 宏在 EC626 平台（ICCARM 编译器，`NO_DECLTYPE` 模式）下，将 `*options`（即 `coapNewClient->coap_optlist`）置为 NULL 后无法正确恢复。

**调用链**：
```
MSG_COAP_SEND (at_coap_task.c:2161)
  → coap_new_request() (at_coap_task.c:276)
    → coap_add_optlist_pdu(pdu, &coapNewClient->coap_optlist) (option.c:584)
      → LL_SORT((*options), order_opts)  ← 问题发生处
```

**问题机制**：
1. `LL_SORT((*options), order_opts)` 在 `NO_DECLTYPE` 路径下，宏展开后通过 `char** _alias = (char**)&(*options)` 操作双重解引用表达式
2. ICCARM 编译器因严格别名规则（Strict Aliasing），将排序过程中对 `(*options)` 的写回优化掉
3. 排序完成后 `*options` 保持为 NULL，6 个 optlist 节点全部泄漏

**日志铁证**：
```
cfg_opt (HEAD阶段): optlist = 0x339b0  ✓ (有值)
send_entry (SEND入口): optlist = 0x339b0  ✓ (有值)
req_pre_addopt (LL_SORT前): *options = 0x339b0  ✓ (有值)
req_post_addopt (LL_SORT后): *options = 0x0     ✗ (已丢失！)
```

#### 2.6.3 修复方案（方案 A：局部变量隔离）

**修改文件**：`PLAT/middleware/thirdparty/libcoap/libcoap-4.2.0/src/option.c`

**修改前**：
```c
int coap_add_optlist_pdu(coap_pdu_t *pdu, coap_optlist_t** options) {
  coap_optlist_t *opt;
  if (options && *options) {
    LL_SORT((*options), order_opts);      // ← 问题发生处
    LL_FOREACH((*options), opt) {
      coap_add_option(pdu, opt->number, opt->length, opt->data);
    }
    return 1;
  }
  return 0;
}
```

**修改后**：
```c
int coap_add_optlist_pdu(coap_pdu_t *pdu, coap_optlist_t** options) {
  coap_optlist_t *opt;
#ifdef FEATURE_NWY_AT_COAP_COMPATIBLE_N21
  coap_optlist_t *head;
  if (options && *options) {
    head = *options;
    LL_SORT(head, order_opts);            // ← 局部变量，安全
    *options = head;
    LL_FOREACH(head, opt) {
      coap_add_option(pdu, opt->number, opt->length, opt->data);
    }
    return 1;
  }
#else
  if (options && *options) {
    LL_SORT((*options), order_opts);
    LL_FOREACH((*options), opt) {
      coap_add_option(pdu, opt->number, opt->length, opt->data);
    }
    return 1;
  }
#endif
  return 0;
}
```

**原理**：`head` 是简单局部变量，`&head` 取地址是直接栈地址，编译器无法优化掉写操作。避免了 `NO_DECLTYPE` 路径下 `char**` alias 的严格别名规则问题。

#### 2.6.4 验证结果

| 验证项 | 修复前 | 修复后 | 结论 |
|--------|--------|--------|------|
| `req_post_addopt` trace | `*options = 0x0` | `*options = 0x32848` | ✅ optlist 指针保持一致 |
| CMF t=12 (OPTLIST释放) | 0 次 | 正常出现 | ✅ OPTLIST 节点被正确释放 |
| 堆内存趋势 | 每周期下降 ~170B | 稳定在 ~53868-53888 | ✅ 不再持续下降 |
| COAP 功能 | 正常（选项丢失的副作用） | 正常（删除 Uri-Path 选项后） | ✅ 功能正常 |

**结论**：方案 A 已验证修复成功，OPTLIST 泄漏问题已解决。

### 2.7 问题复现路径

| 项目 | 内容 |
|------|------|
| **前置条件** | EC626 模组已开机，网络已注册（CREG: 1,1），PS 已附着 |
| **必要状态** | COAP 客户端可正常创建和发送（服务器可达：180.101.147.115:5683） |
| **操作步骤** | 1. `AT+COAPOPEN=500` <br> 2. `AT+COAPADDRES=5,"7/0/0"` <br> 3. `AT+COAPOPTION=6,11,"rd",12,"42",15,"lwm2m=1.0",15,"ep=867009060000182",15,"b=U",15,"lt=84600"` <br> 4. `AT+COAPSEND=0,1,180.101.147.115,5683` <br> 5. 等待 `+COAPRECV` 响应 <br> 6. `AT+COAPCLOSE` <br> 7. 重复步骤 1-6 约 80-90 次 |
| **复现概率** | **必现**。每循环泄漏约 170-250 字节，67KB 堆约 80-90 个循环后耗尽 |
| **验证方法** | 1. 每次循环后执行 `AT+ECSHOWMEM`，观察 `min_free_heap` 持续下降 <br> 2. 约 80-90 次后模组重启 <br> 3. 抓取 dump 确认 ASSERT + pvPortMallocEC NULL |

### 2.8 下一步验证方案

> **主要泄漏源（OPTLIST）已修复**：方案 A（局部变量隔离 LL_SORT）已验证成功，OPTLIST 100% 泄漏问题已解决。堆内存不再持续下降。

**剩余目标**：验证其他次要泄漏点，确保长期运行稳定性。

#### 已完成的验证

| 方案 | 状态 | 结果 |
|------|------|------|
| OPTLIST 泄漏修复（方案 A） | ✅ 已完成 | 堆内存稳定，CMF t=12 正常出现 |
| CMA/CMF 追踪机制 | ✅ 已启用 | 可追踪所有 COAP 内存分配/释放 |
| 诊断 trace（6 点定位） | ✅ 已完成 | 确认 LL_SORT 是罪魁祸首 |

#### 待验证的次要泄漏点

| 类型 | 泄漏数/周期 | 泄漏字节/周期 | 原因 | 优先级 |
|------|------------|--------------|------|--------|
| PACKET (t=3) | ~1 | ~1544 | coap_read_session 部分路径未释放 | P1 |
| NODE (t=4) | ~1 | ~28 | 重传队列节点未清理 | P2 |
| PDU/PDU_BUF (t=7/8) | ~2 | ~300 | realloc 追踪盲区 | P2（部分为误报） |
| STRING (t=0) | ~2 | ~1020 | coapRecvInd 用 plain free() 释放（追踪盲区，非真实泄漏） | P3（误报） |

#### 方案 A：在 `coap_delete_client()` 各步骤前后打印 heap 可用量

在每个释放操作前后打印 heap 信息，定位哪一步释放不完整：
```c
// 在 coap_delete_client() 中添加
void coap_delete_client(CoapClient *coapClient) {
    ECOMM_TRACE(UNILOG_COAP, heap_dbg_0, P_INFO, 1,
        "delete_start: free_heap=%d", xPortGetFreeHeapSize());

    coap_delete_optlist(coapClient->coap_optlist);
    ECOMM_TRACE(UNILOG_COAP, heap_dbg_1, P_INFO, 1,
        "after_delete_optlist: free_heap=%d", xPortGetFreeHeapSize());

    free(coapClient->coap_ip);
    ECOMM_TRACE(UNILOG_COAP, heap_dbg_2, P_INFO, 1,
        "after_free_ip: free_heap=%d", xPortGetFreeHeapSize());

    coap_session_release(coapClient->coap_session);
    coapClient->coap_session = NULL;
    ECOMM_TRACE(UNILOG_COAP, heap_dbg_3, P_INFO, 1,
        "after_session_release: free_heap=%d", xPortGetFreeHeapSize());

    coap_free_context(coapClient->coap_ctx);
    coapClient->coap_ctx = NULL;
    ECOMM_TRACE(UNILOG_COAP, heap_dbg_4, P_INFO, 1,
        "after_free_context: free_heap=%d", xPortGetFreeHeapSize());

    coap_cleanup();
    ECOMM_TRACE(UNILOG_COAP, heap_dbg_5, P_INFO, 1,
        "after_cleanup: free_heap=%d", xPortGetFreeHeapSize());
}
```

> **这个方法能直接看到每步释放后 heap 增长了多少。如果 `coap_session_release` 后 heap 没有增长，说明 session 未被释放（ref > 0）。**

#### 方案 B：在 `coap_delete_client` 中主动释放 `aliAuthToken`/`aliRandom`

无论是否是主因，这两个全局指针在关闭时未释放本身就是 bug：
```c
// 在 coap_delete_client() 中 coap_cleanup() 之前添加
if (aliAuthToken != NULL) {
    coap_delete_string(aliAuthToken);
    aliAuthToken = NULL;
}
if (aliRandom != NULL) {
    coap_delete_string(aliRandom);
    aliRandom = NULL;
}
```

> 可以同时作为验证：如果加了此代码后泄漏率从 ~200B/次 降低到接近 0，说明 aliAuthToken/aliRandom 就是主因。

#### 方案 C：修复 `coapRecvInd` 追踪盲区

`coapRecvInd` (atec_coap_cnf_ind.c) 用 `free()` 释放 `coap_message_handler` 用 `COAP_AT_MALLOC` 分配的 STRING 缓冲，导致 CMA 有记录但 CMF 无记录：
```c
// atec_coap_cnf_ind.c:444
free(parasCoap->coap_payload);     // 用 free() 而非 coap_free_type()
free(parasCoap->coap_token);        // 同上
free(parasCoap->recv_optlist_buf);  // 同上
```

**修复**：将 `free()` 改为 `COAP_AT_FREE()` 或 `coap_free_type(COAP_STRING, ...)` 使 CMF 可追踪。

#### 方案 D：开启 MM_TRACE_ON 追踪堆分配历史

Dump 显示 `trace_node` 为空（MM_TRACE_ON=0）。开启后可追溯每个 malloc 的调用者：
```c
// 在 coap_config.h 或 Makefile 中
#define MM_TRACE_ON 2
#define MM_TRACE_NODE_MAX 1024
```

> 开启后下次复现时 dump 会包含完整的堆分配历史，可精确定位每个未释放 malloc 的调用栈。

#### 调试代码清理（修复验证后）

1. 删除所有诊断 trace（send_entry_dbg / send_pre_open_dbg / send_post_open_dbg / send_pre_req_dbg / req_pre_addopt_dbg / req_post_addopt_dbg / cfg_client_dbg / cfg_opt_dbg / coap_optlist_dbg）
2. `COAP_MEM_STATS` 设为 0 或删除 CMA/CMF 日志
3. 恢复 `at_coap_task.c` / `nwy_at_cmd_coap.c` 中 `COAP_AT_MALLOC/FREE` 为原始 `malloc/free`（或将宏保留但关闭 trace）

---

## 3. 相关文件

### 3.1 核心代码文件

- `PLAT/middleware/eigencomm/at/atentity/src/at_coap_task.c` — COAP 客户端核心逻辑
  - `coap_delete_client()` line 1042 — 资源释放（清理代码被注释、aliAuthToken/aliRandom 未释放）
  - `coap_create_client()` line 1785 — 创建 context
  - `coap_config_client()` line 1142 — COAPADDRES 创建 resource
  - `coap_message_handler()` line 357 — 接收响应分配缓冲区
  - `coapSlpDeInit()` line 3115 — 休眠去初始化（崩溃入口）
  - `coap_recv_task_Init()` line 2341 — 重建接收任务（触发 malloc 失败）
  - `aliAuthToken` line 144, `aliRandom` line 145 — 全局指针，COAPCLOSE 时未释放
- `PLAT/middleware/eigencomm/at/atcust/src/cnfind/atec_coap_cnf_ind.c` — COAP 接收指示处理
  - `coapRecvInd()` line 442-456 — 用 `free()` 释放 COAP_AT_MALLOC 分配的缓冲（追踪盲区）

### 3.2 libcoap 库文件

- `PLAT/middleware/thirdparty/libcoap/libcoap-4.2.0/src/mem.c` — `coap_malloc_type()` = `malloc()` (line 35-37)
- `PLAT/middleware/thirdparty/libcoap/libcoap-4.2.0/src/option.c` — OPTLIST 分配/释放
  - `coap_new_optlist()` — OPTLIST 分配
  - `coap_delete_optlist()` — OPTLIST 释放
  - `coap_add_optlist_pdu()` line 584 — **OPTLIST 泄漏根因（LL_SORT bug）**
  - `order_opts()` line 573 — 排序比较函数
- `PLAT/middleware/thirdparty/libcoap/libcoap-4.2.0/src/coap_session.c` — libcoap session 生命周期
  - `coap_session_release()` line 77 — 引用计数释放（ref==0 && type==CLIENT 才释放）
  - `coap_session_free()` line 171 — 实际释放（含 socket close）
  - `coap_session_mfree()` line 148 — 释放 socket/PBUF/delayqueue
- `PLAT/middleware/thirdparty/libcoap/libcoap-4.2.0/src/coap_io.c` — socket 管理
  - `coap_socket_close()` line 639 — POSIX/LWIP socket 关闭
- `PLAT/middleware/thirdparty/libcoap/libcoap-4.2.0/src/net.c` — `coap_free_context()` line 553
- `PLAT/middleware/thirdparty/libcoap/libcoap-4.2.0/include/coap2/utlist.h` — LL_SORT 宏（NO_DECLTYPE bug）

### 3.3 LWIP 配置文件

- `PLAT/middleware/thirdparty/lwip/src/include/lwip_config_ec6160h00.h` — LWIP 配置
  - `MEMP_MEM_MALLOC = 0` line 320 — memp 池独立于 heap
  - `LWIP_STATS = 0` line 2072 — 统计功能关闭

### 3.4 分析报告文件

- `Bug分析.md` — 本文档，综合 Bug 分析报告
- `内存泄漏分析.md` — CMA/CMF 追踪分析报告
- `OPTLIST泄漏根因分析（最终版）.md` — OPTLIST 泄漏根因分析及修复方案
- `Dump分析.md` — Crash Dump 分析报告

### 3.5 工具和日志文件

- `logs/coap_leak_analyzer.py` — CMA/CMF 配对分析脚本
- `logs/disasm.py` — ELF 反汇编工具（pyelftools）
- `logs/20260610_104541.txt` — CMA/CMF 追踪日志
- `dump/RamDumpData_20260610_102532.bin` — RAM dump 文件
- `dump/app-demo-flash.map` — MAP 文件
- `dump/app-demo-flash.elf` — ELF 文件
- `dump/app-demo-flash.txt` — 反汇编文件