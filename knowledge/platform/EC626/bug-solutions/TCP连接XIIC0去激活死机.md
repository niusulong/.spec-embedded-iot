# 【TCP】连接TCP后xiic=0去激活后必现卡死/概率性死机 原因分析

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **平台** | EC626 |
| **模块** | LWIP/TCP |
| **问题分类** | 时序竞争 |
| **症状关键词** | XIIC=0去激活, 卡死死机, 三方死锁, type 9内存池耗尽, EC_ASSERT |
| **根因概述** | AT+XIIC=0去激活时≥3个TCP socket关闭产生大量_lwip_sock_evt事件以portMAX_DELAY投递到容量仅20的sock_event_queue，而唯一能消费该队列的fwk_event_loop正同步执行nwy_dsnet_down_clear_all持锁阻塞在lwip_close等待，形成三方死锁导致tcpip_thread停止处理消息，type 9内存池耗尽后PSIF状态异常触发PsifSuspendInd断言死机 |
| **调用链摘要** | nwy_dsnet_deactive() → nwy_dsnet_down_clear_all() → close_socket() → lwip_close() → _lwip_sock_evt() → xQueueSend(portMAX_DELAY阻塞) → 三方死锁 → type 9耗尽 → PsifSuspendInd ASSERT |
| **检索关键词** | XIIC去激活死机, 三方死锁, sock_event_queue满, portMAX_DELAY阻塞, type 9内存池耗尽, PsifSuspendInd ASSERT, tcpip_thread停滞, EC626死机 |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 相关文件](#3-相关文件)

---

## 1. 问题描述

TCP连接建立后，执行 `AT+XIIC=0` 去激活PDP上下文，≥3个活跃TCP socket时必现卡死（AT阻塞），概率性死机。

**问题类型**：AP日志分析 + Dump分析

**故障时序**：
```
10:05:54.506  +TCPSEND: 5,1024  (TCP发送成功)
10:05:55.959  +TCPRECV: 5,1024,...  (TCP接收成功)
10:07:29.204  AT+XIIC=0  (去激活 → 死机)
```

## 2. 根本原因

**直接死机点**（Dump分析确认）：`AT+XIIC=0` 去激活触发 PDP 断开流程，协议栈 Bearer Resume 信号处理中调用 `PsifSuspendInd` 时，PSIF suspend 状态不一致（非 IDLE 状态），触发 **EC_ASSERT 断言失败导致死机**。

**平台分析结论**：内存耗尽导致死机。此结论**正确但非根因**——内存耗尽是三方死锁的级联后果，而非独立原因。

**根因**（AP日志分析确认）：`AT+XIIC=0` 去激活后，4个 TCP socket 关闭产生大量 `_lwip_sock_evt` 事件，以 `portMAX_DELAY` 投递到容量仅 20 的 `sock_event_queue`，而唯一能消费该队列的 `fwk_event_loop` 线程正在同步执行 `nwy_dsnet_down_clear_all`（持锁期间不回循环消费队列），**形成三方死锁**（tcpip_thread 阻塞在 xQueueSend + fwk_event_loop 阻塞在 lwip_close 等待 + sock_event_queue 容量不足）。tcpip_thread 死锁后不再处理邮箱消息，lwIP type 9 内存池（MEMP_TCPIP_MSG_API）消息积压不释放，**type 9 池在 10:07:30.451 耗尽**（20次连续分配失败）。内存池耗尽后，PSIF suspend/resume 流程无法正常完成，协议栈状态机异常，最终触发断言。

**因果链**：
```
三方死锁（根因）
  → tcpip_thread 停止处理邮箱消息
  → type 9 消息积压不释放（消息已投递但无法被消费）
  → type 9 池耗尽（平台看到的"内存耗尽"）
  → PSIF suspend/resume LWIP FAIL
  → PsifSuspendInd ASSERT → 死机
```

**关键区分**：平台分析看到"内存耗尽"是正确的现象观察，但**内存耗尽不是独立原因**——如果三方死锁不发生，tcpip_thread 正常消费消息，type 9 池不会耗尽（正常运行时分配/释放完全平衡，见 §2.5）。内存耗尽是死锁导致消息无法释放的后果，消除死锁即可消除内存耗尽。

### 2.1 关键日志证据

#### AT命令日志

```
[2026-04-17_10:05:54:506]+TCPSEND: 5,1024
[2026-04-17_10:05:55:959]+TCPRECV: 5,1024,START6789012345...
[2026-04-17_10:07:29:204]AT+XIIC=0  //直接死机
```

#### Dump日志（死机/崩溃问题时）

Dump 文件：`RamDumpData_20260417_100823.bin`

```
崩溃类型: EC_ASSERT (软件断言)，非 HardFault
崩溃函数: PsifSuspendInd (0x0095F394, size 0x64)
调用者:   CedrProcBearerResumeIndSig (0x00919EC8, size 0x130)
调用链:   CeUpTaskEntry → CedrProcBearerResumeIndSig → PsifSuspendInd → EC_ASSERT

关键寄存器:
  PC = 0x0095F3C4 (PsifSuspendInd+0x30)
  LR = 0x00919ED7 (CedrProcBearerResumeIndSig+0x0F)
  R0 = 0x0001E03D (RAM数据指针)
  R1 = 0x40000008 (外设地址: APB)
  R2 = 0x00000800 (值: 2048)

栈溢出: 否 (CeuTask 栈使用率仅25%，所有23个任务栈底标记完好)
```

详细 Dump 分析参见：[CeuTask_ASSERT_PsifSuspendInd/Dump分析.md](../CeuTask_ASSERT_PsifSuspendInd/Dump分析.md)

#### 模块AP日志

**10:07:29.160 — XIIC=0 触发去激活：**
```
ATCMD , decode AT: AT+XIIC=0
NWY_FRM: nwy_app_dsnet.cpp 70 nwy_dsnet_down_clear_all - close all sockets
lwip_close ( 0 )
```

**10:07:29.170 — socket 0 关闭，进入 CLOSING 状态：**
```
NWY_FRM: nwy_platform.c 878 nwy_dss_close 0 ret 0 errno:11  ← lwip_close成功，但返回EWOULDBLOCK
NWY_FRM: nwy_socket_base.cpp 647 close_socket - Socket:fd:0 result:-1 err:102
NWY_FRM: nwy_socket_base.cpp 653 close_socket 0 NWY_EWOULDBLOCK !!!!!  ← socket进入CLOSING
```

**10:07:29.170 — 事件循环处理 socket 0 CLOSE 事件（最后一次 fwk_event_loop 日志）：**
```
NWY_FRM: nwy_platform.c 447 fwk_event_loop event: id=0 evt=4  ← 最后一条事件循环日志！
NWY_FRM: nwy_app_event_handler.cpp 90 nwy_app_sock_cb nethandle = 1 sockfd = 0 event_mask = 4
```

**10:07:29.170-183 — socket 1, 2, 3 依次关闭（全部 CLOSING）：**
```
NWY_FRM: nwy_platform.c 878 nwy_dss_close 1 ret 0 errno:11
NWY_FRM: nwy_socket_base.cpp 653 close_socket 1 NWY_EWOULDBLOCK !!!!!

NWY_FRM: nwy_platform.c 878 nwy_dss_close 2 ret 0 errno:11
NWY_FRM: nwy_socket_base.cpp 647 close_socket - Socket:fd:2 result:-1 err:102

NWY_FRM: nwy_platform.c 758 _lwip_sock_evt socket 3 evt 5  ← socket 3 lwIP回调
NWY_FRM: nwy_platform.c 758 _lwip_sock_evt socket 3 evt 2
```

**关键异常：从 socket 1 关闭开始，日志格式字符串不再替换（显示 `%d`、`%x` 而非实际值），说明系统已异常。**

**10:07:29.170 之后 — `fwk_event_loop event` 日志完全消失，事件循环线程崩溃。**

**10:07:29.495-30.251 — lwIP type 9 内存池被连续耗尽：**
```
10:07:29.160  memp_malloc: type 9, address 0x1e388 success  ← lwip_close socket 0
10:07:29.170  memp_free:   type 9, address 0x1e388 success  ← 释放
10:07:29.495  memp_malloc: type 9, address 0x1e378 success  ← PSIF suspend
10:07:29.828  memp_malloc: type 9, address 0x1e368 success  ← PSIF resume
10:07:29.999  memp_malloc: type 9, address 0x1e358 success  ← 下行IP包
10:07:30.060  memp_malloc: type 9, address 0x1e348 success  ← 下行IP包
10:07:30.110  memp_malloc: type 9, address 0x1e338 success  ← 下行IP包
10:07:30.110  memp_malloc: type 9, address 0x1e328 success  ← 下行IP包
10:07:30.150  memp_malloc: type 9, address 0x1e318 success  ← 下行IP包
10:07:30.201  memp_malloc: type 9, address 0x1e308 success  ← 下行IP包
10:07:30.251  memp_malloc: type 9, address 0x1e2f8 success  ← 下行IP包（最后一个成功）
10:07:30.451  memp_malloc: type 9 fail                      ← 池耗尽！
              memp_malloc: out of memory in pool
              CID: 0 ; TCPIP RECV DL IP PKG ; FAIL
              ... 后续连续 20 次失败（10:07:30 ~ 10:08:05）
```

地址递减模式：0x1e388→0x1e378→...→0x1e2f8（每个16字节），分配后无释放，池被耗尽。
10:07:29.495 之后分配的 type 9 全部**未被释放**，说明 tcpip 线程未正常处理这些消息。

### 2.2 代码调用链

| 信息 | 值 |
|------|-----|
| **入口函数** | `nwy_dsnet_deactive()` (AT+XIIC=0) |
| **调用链** | `nwy_dsnet_deactive() → nwy_dsnet_close() → nwy_plat_sock_cb_g=NULL` |
| **调用链2** | `nwy_dsnet_deactive() → nwy_dsnet_down_clear_all() → close_socket() → nwy_dss_close() → trig_sock_event(CLOSE)` |
| **崩溃调用链** | `CeUpTaskEntry → CedrProcBearerResumeIndSig → PsifSuspendInd → EC_ASSERT` |
| **崩溃位置** | `PsifSuspendInd` (libpsif.a, 预编译库) 偏移+0x35 |

**调用链分析**：

1. `AT+XIIC=0` 触发 `nwy_dsnet_deactive()`
2. `nwy_dsnet_deactive()` 调用 `nwy_dsnet_close()`，将 `nwy_plat_sock_cb_g = NULL`（nwy_platform.c:603）
3. 同时触发 PDP 去激活，产生 `NWY_DS_ENETNONET` 网络事件
4. 网络事件通过 `trig_dsnet_status_adpt_rtos` 发送 `CMD_LOOP_NET` 到 `sock_event_queue`
5. `fwk_event_loop` 从队列取出 `CMD_LOOP_NET` 事件，调用 `nwy_plat_net_cb_g`（即 `nwy_app_net_cb`）
6. `nwy_app_net_cb` 调用 `nwy_ds_appsrv_put_cmd_ex(&nwy_app_dsnet_msg_handler, ...)`，由于 `#if 0` 禁用了异步队列，**同步直接调用** `nwy_app_dsnet_msg_handler`
7. `nwy_app_dsnet_msg_handler` → `nwy_app_handle_dsnet_event` → **`ASLockGuard dsnet_lock(nwy_dsnet_crit_sect)`** 获取互斥锁
8. 在持锁状态下调用 `nwy_dsnet_down_clear_all()`，遍历所有 socket，对每个调用 `close_socket()`
9. `close_socket()` → `nwy_dss_close()` → `lwip_close()` — **同步等待 tcpip_thread 处理完成**
10. tcpip_thread 处理 `lwip_close` 时触发 `_lwip_sock_evt` 回调，以 `portMAX_DELAY` 向 `sock_event_queue` 投递事件
11. 队列满后 tcpip_thread 阻塞 → `lwip_close` 无法返回 → fwk_event_loop 无法释放锁 → **三方死锁**

**Dump 分析与 AP 日志分析的关系**：
- AP 日志分析定位了 **三方死锁机制**：`_lwip_sock_evt` 以 `portMAX_DELAY` 投递事件到容量仅 20 的 `sock_event_queue`，fwk_event_loop 持锁同步关闭 socket 不消费队列，tcpip_thread 阻塞 → 死锁
- Dump 分析定位了 **协议栈层的直接崩溃点**（`PsifSuspendInd` 断言），这是**死锁的级联后果**
- 因果链：三方死锁 → tcpip_thread 停止 → type 9 池耗尽 → PSIF suspend/resume FAIL → PsifSuspendInd ASSERT → 死机
- 缺陷2（回调置空）和缺陷3（无同步保护）是独立问题，不是三方死锁的成因

### 2.3 问题分析

**三方死锁是核心根因**，以下缺陷1是死锁形成的必要条件（使事件数超过队列容量），缺陷2/缺陷3是独立问题（与死锁无直接因果关系，但在死锁后可能加剧异常）。

#### 缺陷1（三方死锁根因）：`nwy_dss_close()` 成功关闭也返回 EWOULDBLOCK 并触发 CLOSE 事件

文件：`nwy_platform.c:895-901`

```c
else {
  if (ret == 0) {  // lwip_close 成功！
      *dss_errno = NWY_EWOULDBLOCK;
      //TCP is OK, but add this, because SSL has no close event
      trig_sock_event(sockfd, NWY_CLOSE_EVENT, RTOS_MSG_TIMEOUT);
  }
  ret = NWY_DSS_ERROR;  // 成功也返回ERROR
}
```

这个逻辑的设计初衷是为 SSL socket 补偿关闭事件（"SSL has no close event"），但对 **TCP socket 也会触发**。这导致：
- 每次 TCP socket 关闭，无论 `lwip_close` 是否成功，都会往事件队列推送一个 `NWY_CLOSE_EVENT`
- `close_socket()` 总是走 EWOULDBLOCK 分支，socket 总是进入 CLOSING 状态
- 这些 CLOSE 事件将在后续被 `fwk_event_loop` 处理

#### 缺陷2（独立问题）：`fwk_event_loop` 未检查 `nwy_plat_sock_cb_g` 是否为 NULL

> **注意**：此缺陷与三方死锁无直接因果关系。三方死锁的形成只依赖缺陷1（事件数超过队列容量）+ `_lwip_sock_evt` 使用 `portMAX_DELAY`。但此缺陷在死锁后可能加剧异常——如果 `fwk_event_loop` 恢复后处理残留事件时回调已被置空，会导致空指针崩溃。

文件：`nwy_platform.c:471-473`

```c
handle_id = get_handleID_by_sockfd(id);
if (handle_id != -1) {
    nwy_plat_sock_cb_g(handle_id, id, evt, NULL);  // 可能NULL！
}
```

当 `nwy_dsnet_close()` 已将 `nwy_plat_sock_cb_g` 设为 NULL 后，`fwk_event_loop` 处理队列中的 CLOSE 事件时直接调用空指针。

#### 缺陷3（独立问题）：`nwy_dsnet_close()` 无同步保护地置空全局回调

> **注意**：此缺陷与三方死锁无直接因果关系，但属于多线程安全问题。

文件：`nwy_platform.c:602-603`

```c
nwy_plat_net_cb_g = NULL;
nwy_plat_sock_cb_g = NULL;
```

在 AT 命令处理线程中直接置空全局回调指针，而 `fwk_event_loop` 在另一个线程中可能正在使用这些指针，无任何互斥保护。

#### 概率性原因

为什么是**概率性**死机：
- 三方死锁的直接结果是 **AT 阻塞（卡死）**，不是死机
- 死机需要级联条件：三方死锁 → tcpip_thread 停止 → type 9 池耗尽 → PSIF 状态异常 → ASSERT
- type 9 池耗尽取决于三方死锁后**下行 IP 包是否持续到达**（TCP FIN-ACK 重传、网络侧数据推送等）
- 如果三方死锁后没有下行 IP 包到达，type 9 不会被耗尽，系统只会卡死（AT 阻塞），不会死机
- 日志显示本 bug 中下行包确实到达了（10:07:29.999-30.251 连续 8 个 IP 包分配成功），导致池耗尽 → 死机

### 2.4 必现路径分析

**结论：三方死锁是必现的（数学确定性），但死机是三方死锁的级联效应，依赖 type 9 池耗尽后 PSIF 状态异常触发 ASSERT，这一步是概率性的。**

#### 必现性论证：三方死锁

三方死锁形成条件是**数学确定性**的，而非时序依赖的：

| 条件 | 值 | 性质 |
|------|-----|------|
| `sock_event_queue` 容量 | **20**（固定） | 确定值 |
| 每个 TCP socket `_lwip_sock_evt` 事件数 | **6**（evt 5/2/5/1/2/5） | 确定值 |
| 每个 TCP socket `nwy_dss_close` CLOSE 事件 | **1**（EWOULDBLOCK） | 确定值（代码逻辑：ret==0 时也触发） |
| `fwk_event_loop` 消费能力 | **0**（同步执行 `nwy_dsnet_down_clear_all`，阻塞在 `lwip_close` 等待中不回循环） | 确定性 |
| `_lwip_sock_evt` 阻塞方式 | `portMAX_DELAY`（无限阻塞，**bug 发生时的原始代码**；现已改为 100ms 超时） | 确定性 |

**事件数/队列容量是固定数学关系**，不是时序竞争：

| TCP socket 数 | `_lwip_sock_evt` 事件 | CLOSE 事件 | fwk 消费事件 | 净入队 | 是否超过20 |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 6 | 1 | 0（不回循环消费） | 7 | ❌ |
| 2 | 12 | 2 | 0 | 14 | ❌ |
| **3** | **18** | **3** | **0** | **21** | **✅ 必现死锁** |
| 4 | 24 | 4 | 0 | 28 | ✅ 必现死锁 |
| ≥5 | ≥30 | ≥5 | 0 | ≥35 | ✅ 必现死锁 |

**3 个及以上活跃 TCP socket 时，事件数 ≥ 21 > 队列容量 20，三方死锁必然形成。**

#### 概率性：死机（三方死锁的级联效应）

三方死锁的直接结果是 **AT 阻塞（卡死）**，不是死机。死机需要额外的级联条件：

```
三方死锁（必现）
  → tcpip_thread 停止处理邮箱消息
  → type 9 消息积压不释放
  → 下行 IP 包持续到达（生产不停止）
  → type 9 池耗尽（概率性，取决于下行包到达速率和数量）
  → PSIF suspend/resume LWIP FAIL
  → PsifSuspendInd ASSERT → 死机
```

**type 9 池耗尽是概率性的**，因为：
- 下行 IP 包的到达速率和数量取决于网络侧行为（TCP FIN-ACK 重传、网络侧数据推送等）
- 如果三方死锁后没有下行 IP 包到达，type 9 不会被耗尽，系统只会卡死（AT 阻塞），不会死机
- 日志显示本 bug 中下行包确实到达了（10:07:29.999-30.251 连续 8 个 IP 包分配成功），导致池耗尽

#### 最小必现路径

| 项目 | 内容 |
|------|------|
| **必现条件** | 有 ≥ 3 个活跃 TCP 连接 + `_lwip_sock_evt` 使用 `portMAX_DELAY` |
| **必要状态** | 模块已建立 PDP 上下文，≥ 3 个 TCP socket 处于 ESTABLISHED 状态 |
| **操作步骤** | 1. 建立 PDP 上下文（AT+XIIC=1） 2. 建立 ≥ 3 个 TCP 连接（AT+TCPSETUP） 3. 执行 AT+XIIC=0 去激活 |
| **必现结果** | ≥ 3 个 TCP socket 时**三方死锁必现** → AT 阻塞（卡死） |
| **概率性结果** | 如果三方死锁后下行 IP 包持续到达 → type 9 池耗尽 → PSIF ASSERT → **死机** |
| **当前修复状态** | `_lwip_sock_evt` 已改为 100ms 超时（2026.06.04），三方死锁已消除，但队列满时事件可能丢失。正式修复方案参见 [解决方案.md](解决方案.md) |

## 3. 相关文件

- `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/platform/EC626/nwy_platform.c` — 缺陷1(行897-903)、缺陷2(行444-460)、缺陷3(行604-606)
- `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/src/nwy_socket_base.cpp` — close_socket() EWOULDBLOCK处理(行641-681)
- `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/src/nwy_app_dsnet.cpp` — nwy_dsnet_down_clear_all()(行136-156)
- `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/src/nwy_app_data_mgr.cpp` — nwy_app_handle_socket_event()(行85-105)
- `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/src/nwy_app_event_handler.cpp` — nwy_app_sock_cb()(行73-94)
- `libpsif.a (psifadpt.o)` — PsifSuspendInd() 崩溃函数（预编译库，无源码）
- `libps.a (cedrbearer.o)` — CedrProcBearerResumeIndSig() 调用者（预编译库，无源码）

### 2.5 内存分析：内存耗尽是死锁的后果，而非根因

**结论：不是传统意义的内存泄漏，而是三方死锁导致 lwIP type 9 固定内存池被消息积压耗尽。**

**回应平台结论"内存耗尽导致死机"**：内存耗尽是**可观察的现象**，但不是**根因**。如果三方死锁不发生，tcpip_thread 正常消费邮箱消息，type 9 池不会耗尽（正常运行时分配/释放完全平衡，见下方证据）。内存耗尽的本质是：死锁 → 消息已投递但无法被消费 → 无法释放 → 池耗尽。消除三方死锁即可消除内存耗尽。

#### 两种内存机制对比

| 类型 | 机制 | 大小 | 是否耗尽 |
|------|------|------|----------|
| **系统堆** (FreeRTOS heap) | `pvPortMalloc`/`vPortFree` 动态分配 | ~58KB | ❌ 未耗尽（10:05:55 剩余 37856 字节，约 65%） |
| **type 9 固定池** (MEMP_TCPIP_MSG_API) | `memp_malloc`/`memp_free` 固定大小池 | **仅10个slot**（`MEMP_NUM_TCPIP_MSG_API=10`，每个16字节） | ✅ **完全耗尽** |

**平台分析的"内存耗尽"指的是 type 9 固定内存池耗尽，而非系统堆耗尽**。系统堆在死机时仍有约 65% 剩余，不是问题所在。type 9 池仅 10 个 slot（共 160 字节），是极小的固定资源，正常运行时分配/释放完全平衡，只有在 tcpip_thread 停止消费后才会积压耗尽。

#### 正常运行时：分配/释放完全平衡

10:05:00 ~ 10:07:28 期间，type 9 分配后立即释放，地址始终为 0x1e388：
```
memp_malloc: type 9, address 0x1e388 success  10:05:00.093
memp_free:   type 9, address 0x1e388 success  10:05:00.093  ← 立即释放
memp_malloc: type 9, address 0x1e388 success  10:05:00.294
memp_free:   type 9, address 0x1e388 success  10:05:00.294  ← 立即释放
...每2秒一次循环，malloc和free成对出现
```

#### XIIC=0 之后：只分配不释放

10:07:29.170 之后，**再也没有 `memp_free: type 9` 出现**，type 9 消息被持续投递到 tcpip 线程邮箱但未被取出处理：
```
10:07:29.495  memp_malloc: type 9, address 0x1e378  ← PSIF suspend，未释放
10:07:29.828  memp_malloc: type 9, address 0x1e368  ← PSIF resume，未释放
10:07:29.999~30.251  8次分配 (0x1e358→0x1e2f8)      ← 下行IP包，未释放
10:07:30.451  memp_malloc: type 9 fail               ← 10个slot全部被占！
```

#### 不释放的原因：tcpip 线程停止处理消息

lwIP `tcpip_thread` 代码（`tcpip.c:124-181`）：
```c
while (1) {
    TCPIP_MBOX_FETCH(&mbox, (void **)&msg);  // 从邮箱取消息
    switch (msg->type) {
    case TCPIP_MSG_PS_INPKT:
        msg->msg.ps_inp.ps_input_fn(...);     // 处理下行包
        memp_free(MEMP_TCPIP_MSG_API, msg);   // 处理完才释放！
        break;
    }
}
```

type 9 的释放依赖 tcpip 线程从邮箱取出消息并处理完毕。如果 tcpip 线程阻塞（可能被 PSIF suspend/resume 流程阻塞，或 `TCPIP_MBOX_FETCH` 死等在空邮箱上），已投递的消息就永远不会被释放。

#### "生产-消费"失衡模型（三方死锁的后果）

```
正常运行：下行IP包到达 → TcpipPsInpkt() 分配 type 9 → 投递邮箱
     → tcpip线程取出处理 → memp_free释放 → 速率平衡，池不耗尽

三方死锁后（消费停滞）：
     XIIC=0 → ≥3个socket关闭 → 三方死锁形成（根因）
     → tcpip_thread 被阻塞，不再从邮箱取消息（消费=0）
     → PSIF suspend/resume 占用 type 9 不释放
     + TCP FIN重传 + 网络重attach → 大量下行IP包持续到达（生产加速）
     → 10个slot全部积压 → type 9 池耗尽 → 协议栈状态异常
```

**这是三方死锁导致的"生产-消费"速率失衡，而非独立的内存泄漏问题**。正常运行时生产速率低、消费正常，池不会耗尽；三方死锁导致消费归零，同时 XIIC=0 触发突发大量下行包加速生产，固定池瞬间耗尽。**消除三方死锁即可恢复正常的生产-消费平衡，type 9 池不会耗尽。**

### 2.6 tcpip 线程停滞深度分析

#### 日志证据链

**证据①：tcpip_thread 最后活动时间**

```
10:07:28.261  tcpip_thread: API CALL message 0x1e388  ← 正常周期性定时器
10:07:28.461  tcpip_thread: API CALL message 0x1e388  ← 正常周期性定时器
10:07:29.160  tcpip_thread: API message 0x1e388       ← 处理 lwip_close(0) 的 TCPIP_MSG_API
10:07:29.170  tcpip_thread: API message 0x%x          ← 处理 lwip_close(1) 的 TCPIP_MSG_API（最后一条！）
```

10:07:29.170 之后，tcpip_thread **再无任何日志输出**。tcpip_thread 在 `_lwip_sock_evt` 中以 `portMAX_DELAY` 向 `sock_event_queue` 投递时被阻塞，后续无法继续处理。

**证据②：fwk_event_loop 最后活动时间**

```
10:07:29.170  fwk_event_loop event: id=0 evt=4         ← 处理 socket 0 CLOSE 事件
10:07:29.170  nwy_app_sock_cb nethandle=1 sockfd=0 event_mask=4
10:07:29.170  trig_sock_event 0 4 same event coming, ignore it  ← 去重
10:07:29.170  trig_sock_event current queue len 1
```

10:07:29.170 之后，fwk_event_loop **再无任何日志输出**。fwk_event_loop 阻塞在 `lwip_close` 的同步等待中，无法回到事件循环。

**证据③：`_lwip_sock_evt` 事件序列与截断**

```
socket 0:  evt 5 → evt 2 → evt 5 → [fwk消费evt=4] → evt 1 → evt 2(去重) → evt 5   (6个evt)
socket 1:  evt 5 → evt 2 → evt 5 → evt 1 → evt 2 → evt 5                          (6个evt)
socket 2:  evt 5 → evt 2 → evt 5 → evt 1 → evt 2 → evt 5                          (6个evt)
socket 3:  evt 5 → evt 2 → evt 5                                                    (仅3个evt！截断)
```

socket 3 只记录了 3 条 `_lwip_sock_evt`（evt 5/2/5），缺少正常的 evt 1 和 evt 2/5。tcpip_thread 在 `_lwip_sock_evt` 中以 `portMAX_DELAY` 向 `sock_event_queue` 投递时被阻塞，后续 evt 无法产生。

**证据④：socket 3 没有 `nwy_dss_close` / `close_socket` 日志**

日志中只有 socket 0/1/2 的关闭记录：
```
10:07:29.170  nwy_dss_close 0 ret 0 errno:11
10:07:29.170  close_socket 0 NWY_EWOULDBLOCK !!!!!
10:07:29.180  nwy_dss_close 1 ret 0 errno:11
10:07:29.180  close_socket 1 NWY_EWOULDBLOCK !!!!!
10:07:29.183  nwy_dss_close 2 ret 0 errno:11
10:07:29.183  close_socket - Socket:fd:2 result:-1 err:102
```

**socket 3 完全没有 `nwy_dss_close` 或 `close_socket` 日志**。这说明 `nwy_dsnet_down_clear_all` 在关闭 socket 2 后，对 socket 3 调用 `lwip_close(3)` 时阻塞——因为 `lwip_close` 是同步 API，需要 tcpip_thread 处理，而 tcpip_thread 已在 `_lwip_sock_evt` 中被 `sock_event_queue` 阻塞。

**证据⑤：unilog 缓冲区饱和（格式字符串未替换）**

从 socket 1 的 `lwip_close` 开始，日志出现格式字符串未替换现象：
```
10:07:29.170  memp_free: type %d , address 0x%x success    ← 应为 type 9, 0x1e388
10:07:29.170  lwip_close result %d                         ← 应为 0
10:07:29.170  tcpip_thread: API message 0x%x              ← 应为 0x1e388
10:07:29.183  %s                                          ← 完全无法替换
```

高密度事件日志导致 unilog 缓冲区饱和，格式字符串来不及替换。

**证据⑥：type 9 池只分配不释放**

```
10:07:29.160  memp_malloc: type 9, address 0x1e388 success  ← lwip_close(0) 分配
10:07:29.170  memp_free:   type 9, address 0x1e388 success  ← lwip_close(0) 释放（正常）
10:07:29.495  memp_malloc: type 9, address 0x1e378 success  ← PSIF suspend，未释放
10:07:29.828  memp_malloc: type 9, address 0x1e368 success  ← PSIF resume，未释放
10:07:29.999  memp_malloc: type 9, address 0x1e358 success  ← 下行IP包，未释放
10:07:30.060  memp_malloc: type 9, address 0x1e348 success  ← 下行IP包
10:07:30.110  memp_malloc: type 9, address 0x1e328 success  ← 下行IP包
10:07:30.150  memp_malloc: type 9, address 0x1e318 success  ← 下行IP包
10:07:30.201  memp_malloc: type 9, address 0x1e308 success  ← 下行IP包
10:07:30.251  memp_malloc: type 9, address 0x1e2f8 success  ← 下行IP包（最后一个成功）
10:07:30.451  memp_malloc: type 9 fail                      ← 池耗尽！
```

10:07:29.170 之后，**再也没有 `memp_free: type 9` 出现**。地址递减模式 0x1e378→0x1e2f8（每个16字节），分配后无释放。原因：tcpip_thread 死锁 → 无法从邮箱取出消息处理 → type 9 消息积压不释放 → 池耗尽。

**证据⑦：PSIF LWIP FAIL → PsifSuspendInd ASSERT**

```
10:08:05.761  PSIF , suspend ( 1 ) / resume ( 0 ) : 1 , LWIP FAIL   ← PSIF suspend 失败
10:08:05.831  PSIF , suspend ( 1 ) / resume ( 0 ) : 0 , LWIP FAIL   ← PSIF resume 失败
10:08:05.831  ASSERT , FUNC: PsifSuspendInd                          ← 断言触发 → 死机
```

PSIF suspend/resume 因 type 9 池耗尽无法通知 lwIP，返回 LWIP FAIL，PSIF 状态机异常触发 ASSERT。

#### 与同类 UDP 去激活卡死问题的日志模式对比

| 佐证维度 | 同类 UDP 去激活卡死问题 | 当前 TCP bug | 是否一致 |
|----------|-------------------|-------------|---------|
| AT+XIIC=0 触发 | ✅ 日志直接记录 | ✅ 日志直接记录 | ✅ |
| tcpip_thread 停止 | ✅ socket 5 后无日志 | ✅ socket 1 后无日志 | ✅ |
| fwk_event_loop 停止 | ✅ socket 0 CLOSE 后无日志 | ✅ socket 0 CLOSE 后无日志 | ✅ |
| `_lwip_sock_evt` 截断 | ✅ socket 5 只有3条evt | ✅ socket 3 只有3条evt | ✅ |
| socket 未被 close | ✅ socket 5 无 nwy_dss_close | ✅ socket 3 无 nwy_dss_close | ✅ |
| unilog 缓冲区饱和 | ✅ `%d` `%x` `%s` 未替换 | ✅ `%d` `%x` `%s` 未替换 | ✅ |
| type 9 池耗尽 | ❌（卡死不死机，未到耗尽） | ✅ 10:07:30.451 fail | 当前更严重 |
| PSIF LWIP FAIL | ❌（卡死不死机） | ✅ 10:08:05.761 FAIL | 当前更严重 |
| 最终结果 | 卡死（AT阻塞，不死机） | **卡死**（AT阻塞）+ 概率性死机（PSIF ASSERT） | 死锁形成机制相同，当前级联更深 |

**结论：7 项佐证维度中 6 项完全一致，第 7 项（最终结果）三方死锁形成机制相同，但级联后果不同——当前 bug 三方死锁后下行 IP 包导致 type 9 池耗尽，触发 PSIF ASSERT 死机，而同类 UDP 去激活问题仅卡死不死机。**

> **注意**：fwk_event_loop 停止的原因不是"等待获取互斥锁"，而是**阻塞在 `lwip_close()` 的同步等待中**。`lwip_close()` 需要等待 tcpip_thread 处理完成才能返回，而 tcpip_thread 已在 `xQueueSend(portMAX_DELAY)` 中阻塞，形成互相等待的死锁。

#### lwIP 配置（lwip_config_ec6160h00.h）

| 配置项 | 值 | 影响 |
|--------|-----|------|
| `LWIP_TCPIP_CORE_LOCKING` | **0** | `LOCK_TCPIP_CORE()`/`UNLOCK_TCPIP_CORE()` 为空宏，无互斥保护 |
| `LWIP_TCPIP_CORE_LOCKING_INPUT` | **0** | 输入包通过邮箱投递，非直接加锁处理 |
| `TCPIP_MBOX_SIZE` | **10** | tcpip_thread 邮箱容量仅10条消息 |
| `MEMP_NUM_TCPIP_MSG_API` | **10** | type 9 池仅10个slot |
| `LWIP_TCPIP_THREAD_ALIVE` | **()** (空宏) | 看门狗喂狗未启用，无法检测 tcpip_thread 停滞 |
| `PS_ENABLE_TCPIP_HIB_SLEEP2_MODE` | **1** | HIB/SLEEP2 低功耗模式启用 |

#### lwIP 关键代码路径分析

**tcpip_thread 主循环**（`tcpip.c:124-128`）：
```c
while (1) {
    UNLOCK_TCPIP_CORE();                    // 空宏（LOCKING=0）
    LWIP_TCPIP_THREAD_ALIVE();              // 空宏！无看门狗
    TCPIP_MBOX_FETCH(&mbox, (void **)&msg); // 阻塞等待邮箱消息
    LOCK_TCPIP_CORE();                      // 空宏（LOCKING=0）
    // ... 处理消息 ...
}
```

**TCPIP_MBOX_FETCH 展开**（`timeouts.c:568-615`）：
```c
sys_timeouts_mbox_fetch() {
    // HIB/SLEEP2 投票
    if (PsifIsTcpipAllowEnterHIB()) {
        PsifTcpipAllowEnterHib();   // ← 可能允许系统进入低功耗！
    }
    sys_arch_mbox_fetch(mbox, msg, sleeptime);  // 阻塞等待
    PsifTcpipNotAllowEnterHib();    // 收到消息后禁止休眠
}
```

**sys_arch_mbox_fetch 实现**（`sys_arch.c:179-224`）：
```c
if (ulTimeOut != 0UL) {
    osMessageQueueGet(*pxMailBox, &(*ppvBuffer), NULL, ulTimeOut/portTICK_PERIOD_MS);
} else {
    // timeout=0: 无限等待！
    while (osOK != osMessageQueueGet(*pxMailBox, &(*ppvBuffer), NULL, portMAX_DELAY));
}
```

**sys_mbox_post 实现**（`sys_arch.c:116-119`）：
```c
void sys_mbox_post(sys_mbox_t *pxMailBox, void *pxMessageToPost) {
    // 邮箱满时无限阻塞！
    while (osMessageQueuePut(*pxMailBox, &pxMessageToPost, 0, portMAX_DELAY) != osOK);
}
```

#### lwip_close 调用链中的阻塞点

`lwip_close()` → `netconn_delete()` → `netconn_apimsg()` → `tcpip_send_msg_wait_sem()`：

```c
tcpip_send_msg_wait_sem(fn, apimsg, sem) {
    // 1. 分配 type 9 消息
    TCPIP_MSG_VAR_ALLOC(msg);
    // 2. 投递到 tcpip_thread 邮箱（阻塞式！邮箱满则无限等待）
    sys_mbox_post(&mbox, &TCPIP_MSG_VAR_REF(msg));
    // 3. 无限等待 tcpip_thread 处理完毕后 signal 信号量
    sys_arch_sem_wait(sem, 0);    // timeout=0 → portMAX_DELAY
    // 4. 释放 type 9 消息
    TCPIP_MSG_VAR_FREE(msg);
}
```

**关键**：`TCPIP_MSG_API` 类型消息的 `memp_free` 不在 `tcpip_thread` 中，而在**调用者线程**的 `tcpip_send_msg_wait_sem` 中（行584）。tcpip_thread 处理完 API 消息后 signal 信号量，调用者被唤醒后执行 `TCPIP_MSG_VAR_FREE(msg)` 释放 type 9。

#### tcpip_thread 停滞原因：三方死锁（fwk_event_loop 阻塞在 lwip_close 同步等待，而非等待获取锁）

**确认：tcpip_thread 停滞的根本原因是三方死锁（tcpip_thread 阻塞在 xQueueSend + fwk_event_loop 阻塞在 lwip_close 等待 + sock_event_queue 容量不足）。**

当前 bug 发生时（2026-04-17），`_lwip_sock_evt` 中的 `trig_sock_event` 仍使用 `portMAX_DELAY`（无限阻塞），100ms 超时修复尚未应用。

**三方死锁形成机制**：

- **fwk_event_loop** 持有 `nwy_dsnet_crit_sect` 锁，在 `nwy_dsnet_down_clear_all` 中逐个调用 `lwip_close()`，同步等待 tcpip_thread 处理完成才能返回
- **tcpip_thread** 在 `_lwip_sock_evt` 中以 `portMAX_DELAY` 向 `sock_event_queue` 投递事件，队列满时阻塞
- **sock_event_queue** 容量 20，≥3 个 TCP socket 产生 ≥21 个事件就溢出
- fwk_event_loop 无法消费队列（阻塞在 `lwip_close` 等待中，不是"等待获取锁"），tcpip_thread 无法完成 `lwip_close` 处理（阻塞在 `xQueueSend` 中），互相等待 → 死锁

```
┌─────────────────────────────────────────────────────────────────┐
│                    三方死锁示意图                                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  fwk_event_loop 线程           Tcpip 线程                       │
│  (AT+XIIC=0 处理)              (lwip_close 处理)                │
│  ┌──────────────┐              ┌──────────────┐                 │
│  │ 持有互斥锁    │──等待──→    │ lwip_close()  │                 │
│  │ nwy_dsnet_   │  lwip_close  │ _lwip_sock_   │                 │
│  │ crit_sect    │  返回        │ evt()         │                 │
│  └──────┬───────┘              └──────┬────────┘                 │
│         │                             │                          │
│         │ 同步等待 lwip_close 完成      │ xQueueSend(               │
│         │ （lwip_close 需要            │   portMAX_DELAY)          │
│         │  tcpip_thread 处理）         ↓                          │
│         │                    ┌─────────────────┐                 │
│         │                    │ sock_event_queue│ ← 容量20，已满! │
│         │                    └────────┬────────┘                 │
│         │                             │ 队列满，                  │
│         │                             │ 无限阻塞                  │
│         │                             │                          │
│         │                    tcpip_thread 阻塞在 xQueueSend，    │
│         │                    无法处理 lwip_close，                │
│         │                    fwk_event_loop 的 lwip_close 无法返回│
│         │                                                    │
│         └─────── 互相等待 → 死锁 ────────┘                     │
│                                                                 │
│  注：fwk_event_loop 并非"等待获取锁"，而是"等待 lwip_close 返回"  │
│  lwip_close 返回需要 tcpip_thread 处理，而 tcpip_thread 被阻塞   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**时序验证**：

| 时间 | 事件 | 死锁形成过程 |
|------|------|-------------|
| 10:07:29.160 | AT+XIIC=0 开始，fwk_event_loop 通过 `nwy_app_handle_dsnet_event` 获取 `nwy_dsnet_crit_sect` | fwk_event_loop 持锁进入 `nwy_dsnet_down_clear_all` |
| 10:07:29.160 | `nwy_dsnet_down_clear_all` 逐个调用 `socket[i]->close_socket()` | 每个 `close_socket()` → `nwy_dss_close()` → `lwip_close()` 同步等待 tcpip_thread |
| 10:07:29.160 | tcpip_thread 处理 `lwip_close(0)` | `_lwip_sock_evt` 向 sock_event_queue 投递事件，fwk_event_loop 不消费队列 |
| 10:07:29.170 | `lwip_close(0)` 完成，`nwy_dss_close` 返回 EWOULDBLOCK | socket 0 进入 CLOSING，继续关闭 socket 1 |
| 10:07:29.170 | tcpip_thread 处理 `lwip_close(1)` → `_lwip_sock_evt` | **最后一次 tcpip_thread 日志！** |
| 10:07:29.170 | `_lwip_sock_evt socket 0 evt 5/2/5/1/2/5` | socket 0 产生 6 个事件入队 |
| 10:07:29.170-183 | `_lwip_sock_evt socket 1 evt 5/2/5/1/2/5` | socket 1 产生 6 个事件入队 |
| 10:07:29.180-183 | `_lwip_sock_evt socket 2 evt 5/2/5/1/2/5` | socket 2 产生 6 个事件入队 |
| 10:07:29.183 | `_lwip_sock_evt socket 3 evt 5/2/5`（仅3条！） | **socket 3 的后续 evt 被 portMAX_DELAY 阻塞！** |
| → | tcpip_thread 阻塞在 `xQueueSend(sock_event_queue, portMAX_DELAY)` | fwk_event_loop 阻塞在 `lwip_close` 等待 tcpip_thread → **三方死锁** |
| 10:07:29.495 | PSIF suspend 分配 type 9 成功 | 但 tcpip_thread 已死锁，消息无法被处理 |
| 10:07:30.451 | type 9 池耗尽 | 死锁线程的邮箱消息积压，type 9 不释放 |
| 10:08:05.831 | PsifSuspendInd ASSERT | PSIF 状态异常 → 死机 |

**关键证据**：socket 3 只记录了 3 条 `_lwip_sock_evt`（evt 5/2/5），缺少正常的 evt 1 和 evt 2/5。tcpip_thread 在 `_lwip_sock_evt` 中以 `portMAX_DELAY` 向 `sock_event_queue` 投递时被阻塞，后续 evt 无法产生。

**事件计数估算**：

| Socket | `_lwip_sock_evt` 事件数 | `nwy_dss_close` CLOSE事件 | 累计 |
|--------|:---:|:---:|:---:|
| 0 | 6 (evt5/2/5/1/2/5) | 1 (EWOULDBLOCK) | 7 |
| 1 | 6 | 1 | 14 |
| 2 | 6 | 1 | 21 **→ 已超队列容量20！** |
| 3 | ≥3（被阻塞截断） | 1 | ≥25 |

**socket 2 关闭完成后，队列已达 21 条（超过容量 20），tcpip_thread 在 `_lwip_sock_evt socket 3` 的第 4 个事件投递时 `xQueueSend(sock_event_queue, portMAX_DELAY)` 无限阻塞 → 三方死锁形成。**

#### 死锁后的连锁效应（内存耗尽的产生机制）

三方死锁形成后，产生级联故障：

1. **tcpip_thread 死锁** → 不再从邮箱取消息 → type 9 消息积压不释放
2. **tcpip_thread 邮箱积压** → `TCPIP_MBOX_SIZE=10` → 下行 IP 包通过 `sys_mbox_trypost` 投递（非阻塞），成功但不被消费 → type 9 不释放
3. **type 9 池耗尽**（10:07:30.451） → **此即平台分析的"内存耗尽"** → PSIF suspend/resume 无法分配 type 9 → `LWIP FAIL`
4. **PSIF 状态机异常** → `PsifSuspendInd` ASSERT → 死机

所以 **type 9 池耗尽是三方死锁的后果，不是根因**。平台分析的"内存耗尽导致死机"是对级联中间环节的正确观察，但根因是 `_lwip_sock_evt` 中 `portMAX_DELAY` 阻塞式投递导致的三方死锁。消除三方死锁即可打破这条级联链，type 9 池不会耗尽，死机不会发生。

#### 额外发现：TcpipPsInpkt 使用了错误的内存池

`TcpipPsInpkt()`（tcpip.c:334）分配 `MEMP_TCPIP_MSG_API`（type 9 池，10个slot），而非专用的 `MEMP_TCPIP_MSG_PS_INPKT`（8个slot）。

lwIP 定义了三个独立池（`memp_std.h`）：
- `MEMP_TCPIP_MSG_API` — 10个slot（API 消息、callback、timeout）
- `MEMP_TCPIP_MSG_INPKT` — 3个slot（LAN 输入包）
- `MEMP_TCPIP_MSG_PS_INPKT` — 8个slot（PS 输入包）

但 `TcpipPsInpkt()` 实际使用 `MEMP_TCPIP_MSG_API` 而非 `MEMP_TCPIP_MSG_PS_INPKT`，导致：
- **下行 IP 包与 API 消息、PSIF suspend/resume callback 争抢同一个仅10个slot的池**
- 专用 PS_INPKT 池（8个slot）完全空闲浪费
- 这是 lwIP 移植层的**设计缺陷**，极大降低了 type 9 池的抗突发能力

## 4. 修复建议

### 4.2 NWY 框架层修复（消除三方死锁根因）

1. **`_lwip_sock_evt` 中的事件投递方式（最关键）**：
   - 原始代码（bug 发生时）：`xQueueSend(sock_event_queue, &evt, portMAX_DELAY)` → 队列满时无限阻塞 tcpip_thread → 三方死锁
   - 当前临时修复（2026.06.04）：改为 100ms 超时 → 消除死锁，但**队列满时丢弃事件**，可能导致 socket 状态不一致
   - **三种正式修复方案对比**：详见 [解决方案.md](解决方案.md)
     - **方案A**：增大队列容量到 160 + 恢复 portMAX_DELAY → 不死锁、不丢事件、改动最小
     - **方案B**：100ms 超时（当前临时方案）→ 不死锁、但丢事件
     - **方案C**：环形缓冲区 + 直接处理 → 不死锁、不丢事件、理论最完备但改动较大

2. **缺陷1**：`nwy_dss_close()` 中，对 TCP socket 成功关闭时不应触发 CLOSE 事件和返回 EWOULDBLOCK（仅对 SSL socket 保留此逻辑），可减少队列事件数量

3. **缺陷2**：`fwk_event_loop` 处理 socket 事件前应检查 `nwy_plat_sock_cb_g` 是否为 NULL，避免空指针调用

4. **缺陷3**：`nwy_dsnet_close()` 置空前应先排空事件队列或加锁保护

### 4.3 lwIP 层修复（消除 type 9 池耗尽——三方死锁的连锁后果）

1. **TcpipPsInpkt 使用错误的内存池（最关键的 lwIP 缺陷）**：
   - 当前代码（`tcpip.c:334`）：`memp_malloc(MEMP_TCPIP_MSG_API)` — 使用仅10个slot的共用池
   - 应改为：`memp_malloc(MEMP_TCPIP_MSG_PS_INPKT)` — 使用专用8个slot池
   - 同时 `memp_free` 和 `sys_mbox_trypost` 失败时的释放也要相应改为 `MEMP_TCPIP_MSG_PS_INPKT`
   - **效果**：下行 IP 包不再与 API 消息/callback 争抢 type 9 池，极大提高抗突发能力

2. **增大 `MEMP_NUM_TCPIP_MSG_API`**：从 10 增加到 20~30（治标）
   - 当前配置：`lwip_config_ec6160h00.h:544`，`MEMP_NUM_TCPIP_MSG_API = 10`
   - 4个socket同时关闭 + PSIF suspend/resume callback 即可能消耗 10+ 个 slot

3. **增大 `TCPIP_MBOX_SIZE`**：从 10 增加到 20~30，避免 `sys_mbox_post` 在邮箱满时阻塞调用线程
   - 当前配置：`lwip_config_ec6160h00.h:1749`，`TCPIP_MBOX_SIZE = 10`
   - `sys_mbox_post` 使用 `portMAX_DELAY` 无限等待（`sys_arch.c:118`），邮箱满时调用线程死等

4. **启用 `LWIP_TCPIP_THREAD_ALIVE` 看门狗**：当前为空宏（`lwip_config_ec6160h00.h:1757`），无法检测 tcpip 线程停滞
   - 建议实现喂狗机制，超时后触发恢复或报告异常

5. **排查 HIB/SLEEP2 模式与 tcpip_thread 的交互**：
   - `PS_ENABLE_TCPIP_HIB_SLEEP2_MODE=1`，tcpip_thread 在 `sys_timeouts_mbox_fetch` 中投票允许进入 HIB
   - 如果 PSIF resume 因 type 9 耗尽无法通知 lwIP 恢复，tcpip_thread 可能永远停留在 HIB 状态
   - 需要确认：HIB 恢复机制是否有独立于 type 9 池的路径

6. **`TcpipPsInpkt()` 容错增强**（`tcpip.c:334-345`）：`memp_malloc` 失败时返回 `ERR_MEM`，需确认协议栈是否正确处理此错误

### 4.4 协议栈层修复（消除直接崩溃点）

- `PsifSuspendInd` 中的 ASSERT 条件需要 EigenComm PS 团队确认，可能需要放宽断言条件或增加状态保护
- 需要源码确认：Resume 信号处理中为何调用了 SuspendInd，以及 suspend 状态为何非 IDLE

---

# TCP连接XIIC0去激活死机 — 解决方案

## 死锁根因回顾

```
三方死锁形成条件：
1. fwk_event_loop 持有 nwy_dsnet_crit_sect 锁，同步执行 nwy_dsnet_down_clear_all()
2. nwy_dsnet_down_clear_all() 串行关闭所有 socket，每个 lwip_close() 同步等待 tcpip_thread
3. tcpip_thread 处理 lwip_close 时触发 _lwip_sock_evt 回调
4. _lwip_sock_evt 调用 trig_sock_event() 向 sock_event_queue 投递事件
5. sock_event_queue 容量 20，≥3 个 TCP socket 产生 ≥21 个事件
6. 队列满后 _lwip_sock_evt 阻塞在 xQueueSend(portMAX_DELAY)
7. tcpip_thread 被阻塞 → lwip_close 无法返回 → fwk_event_loop 无法释放锁
8. 死锁
```

**关键约束**：
- `_lwip_sock_evt` 在 **tcpip_thread** 上执行（lwIP 回调机制决定）
- `lwip_close()` 是**同步调用**，等待 tcpip_thread 处理完成
- fwk_event_loop 在 `lwip_close()` 等待期间**无法消费 sock_event_queue**
- tcpip_thread 阻塞 = lwip_close 无法完成 = 死锁

**事件数量**：每个 TCP socket 关闭时，`_lwip_sock_evt` 产生 6 个事件（evt 5/2/5/1/2/5），`nwy_dss_close` 产生 1 个 CLOSE 事件，合计 7 个

| TCP socket 数 | 总事件数 | sock_event_queue (容量20) |
|:---:|:---:|:---:|
| 1 | 7 | ❌ 不满 |
| 2 | 14 | ❌ 不满 |
| 3 | 21 | ✅ 溢出 → 死锁 |
| 5 | 35 | ✅ 溢出 → 死锁 |
| 12 | 84 | ✅ 溢出 → 死锁 |

---

## 解决方案：增大队列容量 + NULL 检查 + 恢复 portMAX_DELAY

### 原理

死锁的唯一场景是 net-down 时 fwk_event_loop 持锁期间 tcpip_thread 事件积压超过队列容量。只要队列容量 > 最大可能积压事件数，tcpip_thread 就不会阻塞，死锁不会发生。

**优化思路**：原方案仅增大队列容量到 160，余量仅 6（160 - 154）。优化后：
1. **增大余量**：队列容量设为 200，余量 46（200 - 154），应对 NET/DNS/EXEC 事件共享队列及未来代码变化
2. **NULL 检查**：`fwk_event_loop` 处理 socket 事件前检查 `nwy_plat_sock_cb_g != NULL`，避免 net-down 后空指针崩溃

> **关于 `nwy_dss_close` EWOULDBLOCK 设计**（对 TCP 成功关闭仍返回 EWOULDBLOCK + CLOSE 事件）：
>
> 这是框架的正常设计，目的是让 socket 进入 CLOSING 状态，从而触发 `release_send_buff()` 和 recv_buff 残留数据通知（`NWY_SOCKET_BUF_DATA_REMAIN`）。若移除此设计，每个 socket 积压事件可从 7 降为 6，但会导致 CLOSING 状态下的清理逻辑被跳过，上层丢失未读数据通知。因此**不修改此设计**。

### 事件数精确计算

net-down 期间，`fwk_event_loop` 持锁执行 `nwy_dsnet_down_clear_all()`，无法消费 `sock_event_queue`。此期间队列积压来源于两个路径：

| 事件来源 | 调用线程 | 每个 socket 事件数 | 投递方式 |
|----------|----------|:---:|----------|
| `_lwip_sock_evt`（lwIP 回调） | tcpip_thread | 6（evt 5/2/5/1/2/5） | `trig_sock_event` → `xQueueSend` |
| `nwy_dss_close`（CLOSE 补偿） | fwk_event_loop | 1（`ret==0` 时 EWOULDBLOCK） | `trig_sock_event` → `xQueueSend` |

```
最大积压 = NWY_APP_MAX_SOCKET_NUM × 7 = 22 × 7 = 154
```

> 注：`nwy_dss_close` 中 `errno == EAGAIN` 分支的 CLOSE 事件也计入（0-1/socket），`ret==0` 分支的 CLOSE 事件计入（1/socket），合计 1/socket。

### 具体修改

**文件**：`nwy_platform.c`

**修改1**：增大 sock_event_queue 容量（第 434 行）

```c
// 修改前
sock_event_queue = xQueueCreate(20, sizeof(sock_event*));

// 修改后
/*Begin: Modify by niusulong for/to fix bug 6974423486 enlarge sock event queue in 2026.06.08*/
sock_event_queue = xQueueCreate(200, sizeof(sock_event*));
/*End: Modify by niusulong for/to fix bug 6974423486*/
```

**修改2**：`_lwip_sock_evt` 恢复 portMAX_DELAY（第 760-772 行）

```c
// 修改前（当前 100ms 超时方案）
/*Begin: Modify by niusulong for/to fix bug 6974423486 lwip sock evt deadlock in 2026.06.04*/
if (evt == 1) { //LWIP_EVENT_SENT
    trig_sock_event(s, NWY_WRITE_EVENT, 100);
} else if (evt == 2) { //LWIP_EVENT_RECV
    if (conn->state == NETCONN_LISTEN) {
        trig_sock_event(s, NWY_ACCEPT_EVENT, 100);
    } else {
        trig_sock_event(s, NWY_READ_EVENT, 100);
    }
} else { //5: error!
    trig_sock_event(s, NWY_CLOSE_EVENT, 100);
}
/*End: Modify by niusulong for/to fix bug 6974423486*/

// 修改后（恢复原始逻辑，队列容量已增大确保不会满）
/*Begin: Modify by niusulong for/to fix bug 6974423486 restore portMAX_DELAY in 2026.06.08*/
if (evt == 1) { //LWIP_EVENT_SENT
    trig_sock_event(s, NWY_WRITE_EVENT, portMAX_DELAY);
} else if (evt == 2) { //LWIP_EVENT_RECV
    if (conn->state == NETCONN_LISTEN) {
        trig_sock_event(s, NWY_ACCEPT_EVENT, portMAX_DELAY);
    } else {
        trig_sock_event(s, NWY_READ_EVENT, portMAX_DELAY);
    }
} else { //5: error!
    trig_sock_event(s, NWY_CLOSE_EVENT, portMAX_DELAY);
}
/*End: Modify by niusulong for/to fix bug 6974423486*/
```

**修改3**：`fwk_event_loop` 处理 socket 事件前检查 `nwy_plat_sock_cb_g != NULL`（第 472 行）

```c
// 修改前
handle_id = get_handleID_by_sockfd(id);
if (handle_id != -1) {
    nwy_plat_sock_cb_g(handle_id, id, evt, NULL);
}

// 修改后
/*Begin: Modify by niusulong for/to fix bug 6974423486 check NULL callback in 2026.06.08*/
handle_id = get_handleID_by_sockfd(id);
if (handle_id != -1 && nwy_plat_sock_cb_g != NULL) {
    nwy_plat_sock_cb_g(handle_id, id, evt, NULL);
}
/*End: Modify by niusulong for/to fix bug 6974423486*/
```

### 内存开销

队列存储 `sock_event*`（4 字节指针），不存储 `sock_event` 结构体本身。

| 容量 | 内存占用 | vs 原始(20) |
|:---:|:---:|:---:|
| 20 | 80B | 基准 |
| 200 | 800B | +720B |

### 有效性验证

| TCP socket 数 | `_lwip_sock_evt` 事件 | `nwy_dss_close` CLOSE 事件 | 最大积压 | 队列容量 200 | 死锁？ |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 3 | 18 | 3 | 21 | 21 < 200 | ❌ |
| 5 | 30 | 5 | 35 | 35 < 200 | ❌ |
| 12 | 72 | 12 | 84 | 84 < 200 | ❌ |
| 22 (最大) | 132 | 22 | 154 | 154 < 200 | ❌ |

**余量分析**：

| 项目 | 值 | 说明 |
|------|:---:|------|
| 队列容量 | 200 | — |
| 最大 socket 事件积压 | 154 | 22 × 7 |
| **余量** | **46** | 可容纳 NET/DNS/EXEC 事件 + 未来代码变化 |

**NET/DNS/EXEC 事件占用余量分析**：

net-down 期间，`sock_event_queue` 中还可能存在非 socket 事件：

| 事件类型 | 来源 | 最大数量 | 占用余量 |
|----------|------|:---:|:---:|
| `CMD_LOOP_NET` | PDP 状态变化 | 1-2 | ✅ 远小于 46 |
| `CMD_LOOP_DNS` | DNS 解析完成 | 0-1 | ✅ |
| `CMD_LOOP_EXEC` | 异步调用 | 0 | ✅ |

即使在最极端场景（22 个 socket 全部关闭 + NET/DNS 事件同时到达），总积压 ≤ 154 + 5 = 159 < 200。

### NULL 检查的安全性

**Q：`nwy_plat_sock_cb_g` 何时为 NULL？**

A：`nwy_dsnet_close()` 中将 `nwy_plat_sock_cb_g = NULL`（第 603 行）。net-down 场景下，`nwy_dsnet_close()` 在 `CMD_LOOP_NET` 事件被 fwk_event_loop 取出之前就被调用（调用链：`nwy_dsnet_deactive() → nwy_dsnet_close()`），所以当 fwk_event_loop 处理积压的 socket 事件时，回调已为 NULL。

**Q：检查 NULL 后跳过事件处理是否安全？**

A：安全。net-down 场景下，这些积压的 socket 事件对应的 socket 已被 `nwy_dsnet_down_clear_all` 关闭，上层已通过 `close_socket()` 的 `notify_status(NWY_SOCKET_CLOSED_PASV)` 收到断开通知。跳过这些冗余事件的处理是正确行为。

### 风险

| 风险 | 说明 | 严重程度 | 应对 |
|------|------|:---:|------|
| **`NWY_APP_MAX_SOCKET_NUM` 增大** | 如果未来最大 socket 数从 22 增加到 >28（28×7=196≈200），队列可能不够 | ⚠️ 低 | 队列容量可同步调整 |
| **fwk_event_loop 因其他原因长时间不消费队列** | 当前代码中只有 net-down 持锁会导致长时间不消费 | ⚠️ 低 | 46 个余量可缓冲短时间积压 |
| **NET/DNS/EXEC 事件与 socket 事件共享队列** | 正常运行时 NET/DNS/EXEC 事件数量远少于 socket 事件 | ✅ 无风险 | 46 个余量足够 |
| **`portMAX_DELAY` 队列满时永久阻塞 tcpip_thread** | 队列容量 200 远大于最大积压 154，理论上不会满 | ✅ 无风险 | 余量 46 提供安全边界 |

### 结论

**优点**：
- 改动小（3 处，1 个数字 + 恢复原始超时值 + 1 个 NULL 检查）
- 正常场景零影响（`portMAX_DELAY` 恢复，事件不丢失，时序不变）
- 同时修复 2 个问题：死锁（根因）+ 空指针崩溃
- 余量充足（46），应对 NET/DNS/EXEC 事件及未来代码变化
- 无数据竞争风险（使用 FreeRTOS 队列原语，内部处理线程安全）
- 无额外数据结构，无需内存屏障

**缺点**：
- 额外内存 +720B（从 80B 增至 800B）
- 理论上依赖"积压 ≤ 154"的假设，但该假设在当前代码下有确定性保证

---

## 附录：关键代码位置

| 文件 | 行号 | 说明 |
|------|:---:|------|
| `nwy_platform.c` | 434 | `sock_event_queue = xQueueCreate(20, ...)` — 修改1 |
| `nwy_platform.c` | 760-773 | `_lwip_sock_evt()` — 修改2 |
| `nwy_platform.c` | 472 | `nwy_plat_sock_cb_g(handle_id, id, evt, NULL)` — 修改3（NULL检查） |
| `nwy_platform.c` | 867-904 | `nwy_dss_close()` — 不修改（EWOULDBLOCK 是框架正常设计） |
| `nwy_platform_def.h` | 17 | `NWY_APP_MAX_TCP_UDP_SOCKET_NUM = 12` — 最大 socket 数参考 |
| `nwy_app_api_types.h` | 38 | `NWY_APP_MAX_SOCKET_NUM = 22` — 含 HTTP socket |

---

## 附录：`nwy_dss_close` EWOULDBLOCK 设计分析

### 设计描述

`nwy_dss_close()` 中 `ret == 0`（TCP `lwip_close` 成功）时，仍设置 `*dss_errno = NWY_EWOULDBLOCK` 并触发 `trig_sock_event(CLOSE)`。这是框架的正常设计，目的是让 socket 进入 CLOSING 状态，确保：
- `release_send_buff()` 被及时调用，释放发送缓冲区
- recv_buff 残留数据通过 `NWY_SOCKET_BUF_DATA_REMAIN` 通知上层
- socket 生命周期经过 CLOSING → CLOSED 的完整转换

### 若移除此设计的业务影响

移除后，`nwy_dss_close` 对 TCP `ret==0` 不再设置 EWOULDBLOCK，`close_socket()` 走直接 CLOSED 路径而非 CLOSING 路径。这导致 `nwy_tcp_client::socket_event(CLOSING, CLOSE)` 中的以下逻辑**不被执行**：

1. **`release_send_buff()`**：send_buff 缓冲区延迟到 socket 对象析构时释放（`~nwy_client_base()`），**不影响功能，仅时序变化**
2. **recv_buff 残留数据通知**：如果 recv_buff 中有未消费数据，修改前会通知 `NWY_SOCKET_BUF_DATA_REMAIN` 并进入 `CLOSED_BUFF_DATA` 状态；修改后在析构中静默释放 recv_buff，**上层收不到残留数据通知** — **这是业务影响**

| 场景 | 当前设计（EWOULDBLOCK） | 若移除 | 业务影响 |
|------|------------------------|--------|----------|
| TCP 关闭，无残留数据 | CLOSED（经过 CLOSING） | CLOSED（直接） | ✅ 无影响 |
| TCP 关闭，recv_buff 有残留数据 | `NWY_SOCKET_BUF_DATA_REMAIN` → `CLOSED_BUFF_DATA` | CLOSED，残留数据静默丢弃 | ⚠️ 上层丢失数据通知 |
| SSL 关闭 | 走 EAGAIN 分支，不受影响 | 不受影响 | ✅ 无影响 |

**结论**：EWOULDBLOCK 设计是框架正常机制，不应移除。