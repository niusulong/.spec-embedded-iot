# 【卡死】IPV6,建立UDP链路，去激活PPP，直接卡死 原因分析

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **工作项 ID** | NA |
| **平台** | EC626 |
| **模块** | LWIP |
| **问题分类** | 时序竞争 |
| **症状关键词** | 卡死, AT不通, 去激活PPP, IPv6, UDP链路, 死锁 |
| **根因概述** | AT+XIIC=0去激活流程在AT线程上同步持有互斥锁关闭所有UDP socket，_lwip_sock_evt回调以portMAX_DELAY向容量仅20的事件队列投递事件，而唯一能消费该队列的fwk_event_loop线程被互斥锁阻塞，形成三方死锁 |
| **调用链摘要** | AT_CmdFunc_NWY_XIIC → nwy_dsnet_down_clear_all → lwip_close → _lwip_sock_evt → xQueueSend(portMAX_DELAY) |
| **检索关键词** | 死锁, deadlock, XIIC, PPP去激活, IPv6, UDP, sock_event_queue, portMAX_DELAY, 互斥锁 |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 相关文件](#3-相关文件)

---

## 1. 问题描述

IPv6 网络环境下，建立多个 UDP 链路后，执行 `AT+XIIC=0` 去激活 PPP，设备直接卡死，不通 AT，但模块 AP 日志中无死机/崩溃记录，系统仍在运行（WDT 正常喂狗，Doze/Wake 正常）。

**问题类型**：单日志分析

## 2. 根本原因

**AT+XIIC=0 去激活流程在 AT 线程上同步执行，持有 `nwy_dsnet_crit_sect` 互斥锁关闭所有 UDP socket。关闭过程中 `_lwip_sock_evt` 回调以 `portMAX_DELAY` 向容量仅 20 的 `sock_event_queue` 投递事件，而唯一能消费该队列的 `fwk_event_loop` 线程被互斥锁阻塞，导致队列满后形成三方死锁。**

### 2.1 关键日志证据

#### AT命令日志（模块内部日志）

**卡死触发点 — XIIC=0 开始执行：**
```
[4005104] AT CMD , RECV dump: 41 54 2B 58 49 49 43 3D 30 0D 0A    10:36:15.542
[4005104] ATCMD , decode AT: AT+XIIC=0                              10:36:15.542
[4005104] AT CMD , start guard time: 30000 ms                       10:36:15.542
[4005104] NWY_FRM: nwy_dsnet_down_clear_all - close all sockets     10:36:15.542
```

**Socket 0-4 顺序关闭（均在 AT 线程上同步执行）：**
```
lwip_close ( 0 )                                                    10:36:15.542
tcpip_thread: API message 0x1e388                                    10:36:15.542
_lwip_sock_evt socket 0 evt 2                                        10:36:15.542
_lwip_sock_evt socket 0 evt 1                                        10:36:15.542
_lwip_sock_evt socket 0 evt 2                                        10:36:15.542  ← 重复，被去重
trig_sock_event 0 2 same event coming, ignore it                    10:36:15.542
trig_sock_event current queue len 2                                  10:36:15.542
...（socket 0 关闭完成）
lwip_close result 0                                                  10:36:15.552
nwy_dss_close 0 ret 0 errno:11                                       10:36:15.552
close_socket 0 NWY_EWOULDBLOCK !!!!!                                10:36:15.552
nwy_app_at_udp_cb sid = 0 status = 7                                10:36:15.552
fwk_event_loop event: id=0 evt=2                                    10:36:15.552  ← fwk线程被唤醒
nwy_app_dsnet.cpp 1739 check_and_handle_socket_status - Socket 0     10:36:15.552
~nwy_socket_base - Socket id = 0 sockfd = -1 destructor             10:36:15.552

lwip_close ( 1 ) ... (socket 1-4 同样流程) ...                      10:36:15.552-15.573
~nwy_socket_base - Socket id = 4 sockfd = -1 destructor             10:36:15.573  ← 最后一个socket析构

_lwip_sock_evt socket 5 evt 2                                        10:36:15.573  ← socket 5 非框架管理
_lwip_sock_evt socket 5 evt 1                                        10:36:15.573
_lwip_sock_evt socket 5 evt 2                                        10:36:15.573
```

**日志系统异常（unilog 缓冲区饱和，格式字符串未替换）：**
```
memp_free: type %d , address 0x%x                                    10:36:15.563
%s                                                                    10:36:15.573
```

**AT 守护定时器 30 秒超时触发（XIIC=0 从未返回 OK）：**
```
Sig = > SIG_TIMER_EXPIRY(0x100)                                      10:36:45.543  ← 30秒后
```

**后续 AT 命令被 UART DMA 接收但从未处理：**
```
DMA isr , interrupt bitmap: 0x4                                      10:36:51.726
uart dma eor , rx_cnt:11                                             10:36:51.726
Sig = > SIG_AT_CMD_STR_REQ(0x901)                                    10:36:51.726  ← 收到但未处理
```

#### 主机端 AT 日志

```
[2026-04-20_10:36:05:775] OK
[2026-04-20_10:36:15:734] AT+XIIC=0
[2026-04-20_10:36:15:734] +UDPCLOSE: 0,Link Closed
[2026-04-20_10:36:15:734] +UDPCLOSE: 1,Link Closed
[2026-04-20_10:36:15:734] +UDPCLOSE: 2,Link Closed
[2026-04-20_10:36:15:734] +UDPCLOSE: 3,Link Closed
[2026-04-20_10:36:15:734] +UDPCLOSE: 4,Link Closed
// 后面直接不通AT，无 OK 响应
```

### 2.2 代码调用链

| 信息 | 值 |
|------|-----|
| **入口函数** | `AT_CmdFunc_NWY_XIIC()` |
| **调用链** | `AT_CmdFunc_NWY_XIIC` → `nwy_dsnet_status_adpt_rtos(1,0)` → `nwy_app_net_cb` → `nwy_ds_appsrv_put_cmd_ex` → **直接同步调用** → `nwy_app_handle_dsnet_event` → **获取 `nwy_dsnet_crit_sect` 互斥锁** → `nwy_handle_dsnet_event` → `nwy_dsnet_down_clear_all` → 循环 `lwip_close()` |
| **问题位置** | `nwy_platform.c:756` `_lwip_sock_evt` 使用 `portMAX_DELAY` 投递事件 |

**调用链分析**：
- `nwy_ds_appsrv_put_cmd_ex()` 中 `#if 0` 禁用了队列方式，**直接同步调用处理函数**
- 整个去激活流程在 **AT 线程上同步执行**，持有 `nwy_dsnet_crit_sect` 互斥锁
- `lwip_close()` 是同步 API，AT 线程等待 tcpip 线程完成
- tcpip 线程在 `_lwip_sock_evt` 回调中调用 `trig_sock_event(s, event, portMAX_DELAY)`
- `fwk_event_loop` 线程（唯一消费者）被互斥锁阻塞，无法消费队列

### 2.3 问题分析

#### 死锁机制（三方死锁）

```
┌─────────────────────────────────────────────────────────────────┐
│                        三方死锁示意图                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  AT 线程                     Tcpip 线程                         │
│  ┌──────────────┐            ┌──────────────┐                   │
│  │ 持有互斥锁    │──等待──→  │ lwip_close()  │                   │
│  │ nwy_dsnet_   │            │ _lwip_sock_   │                   │
│  │ crit_sect    │            │ evt()         │                   │
│  └──────┬───────┘            └──────┬────────┘                   │
│         │                           │                            │
│         │                           │ xQueueSend(                │
│         │                           │   portMAX_DELAY)           │
│         │                           ↓                            │
│         │                  ┌─────────────────┐                   │
│         │                  │ sock_event_queue│ ← 容量20，已满!   │
│         │                  └────────┬────────┘                   │
│         │                           │ 队列满，                    │
│         │                           │ 无限阻塞                    │
│         │                           ↑                            │
│         │                  ┌─────────────────┐                   │
│         │                  │ fwk_event_loop  │                   │
│         │                  │ 线程            │                    │
│         │                  │                 │                    │
│         │                  │ 等待获取互斥锁 ──┼───────┐           │
│         │                  └─────────────────┘       │           │
│         │                                            │           │
│         └────────────────────────────────────────────┘           │
│              互斥锁被 AT 线程持有                                 │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### 事件计数分析

每个 UDP socket 关闭时 `lwip_close` 触发 `_lwip_sock_evt` 产生 **3 个事件**（evt 2/1/2），其中仅第 3 个可能被去重（去重仅检查队列头部）。`nwy_dss_close` 额外产生 **1 个 CLOSE 事件**（`RTOS_MSG_TIMEOUT=1000` ticks，超时后丢失）。

| Socket | `_lwip_sock_evt` 事件 | 去重 | 入队 | `nwy_dss_close` 事件 | 累计队列 |
|--------|----------------------|------|------|---------------------|---------|
| 0 | 3 (evt2/1/2) | 1 (队首匹配) | 2 | 1 (可能成功) | 3 |
| 1 | 3 | 0 | 3 | 1 | 7 |
| 2 | 3 | 0 | 3 | 1 | 11 |
| 3 | 3 | 0 | 3 | 1 | 15 |
| 4 | 3 | 0 | 3 | 1 | **19** |
| 5(非框架管理) | 3 | 0 | 3 → **第2个起队列满** | - | **≥20** |

**关键**：队列容量仅 **20**。Socket 4 关闭后队列已达 19 项。Socket 5 的事件（来自 lwip netif 变更）将队列填满至 20，后续 `_lwip_sock_evt` 中 `xQueueSend(..., portMAX_DELAY)` **无限阻塞** → tcpip 线程卡死 → AT 线程在 `lwip_close` 中等待 tcpip 线程 → **死锁**。

#### 时序分析

| 时间 | 事件 | 说明 |
|------|------|------|
| 10:36:15.542 | AT+XIIC=0 开始处理 | 启动 30 秒守护定时器 |
| 10:36:15.542 | 获取 `nwy_dsnet_crit_sect` 互斥锁 | AT 线程持有 |
| 10:36:15.542 | `fwk_event_loop` 被唤醒 | 尝试获取互斥锁 → **阻塞** |
| 10:36:15.542-573 | Socket 0-4 关闭完成 | 事件累积至 ~19 项 |
| 10:36:15.573 | Socket 5 事件到达 | 队列满 → tcpip 线程阻塞 → **死锁形成** |
| 10:36:15.593-714 | 系统进入短周期 Doze | `swNearestTimer` 递减至守护定时器 |
| 10:36:45.543 | 30秒守护定时器触发 | AT 线程仍被阻塞，无法响应 |
| 10:36:51.726+ | 后续 AT 命令到达 | UART DMA 收到但 AT 线程卡死，**永不处理** |

#### 与"不死机"现象的吻合

- **系统未崩溃**：WDT 由其他任务喂狗，idle/Doze 正常运行
- **日志无死机记录**：没有 DataAbort/HardFault/Assert
- **AT 不通**：AT 线程被死锁永久阻塞，无法处理新命令
- **unilog 缓冲区饱和**：高密度事件日志导致格式字符串未替换（`%s`, `%d`）

### 2.4 最小复现路径

#### 必要前提

| 条件 | 说明 |
|------|------|
| **网络类型** | IPv6（触发 lwip netif 变更，产生额外 socket 5 事件，将队列从 19 推至 ≥20） |
| **活跃 UDP socket** | ≥ 5 个（4 个 socket 产生 ~19 个事件，恰好接近但未超队列容量 20；第 5 个 socket 的事件将队列填满） |
| **执行命令** | `AT+XIIC=0`（同步持有 `nwy_dsnet_crit_sect` 关闭所有 socket，阻塞 `fwk_event_loop` 消费队列） |

#### 操作步骤

```
步骤1: AT+XIIC=1                                    // 激活 PPP，获取 IPv6 地址
步骤2: AT+UDPSETUP=1,<IPv6远端地址>,<远端端口>       // 建立 UDP socket 1
步骤3: AT+UDPSETUP=2,<IPv6远端地址>,<远端端口>       // 建立 UDP socket 2
步骤4: AT+UDPSETUP=3,<IPv6远端地址>,<远端端口>       // 建立 UDP socket 3
步骤5: AT+UDPSETUP=4,<IPv6远端地址>,<远端端口>       // 建立 UDP socket 4
步骤6: AT+UDPSETUP=5,<IPv6远端地址>,<远端端口>       // 建立 UDP socket 5（关键：第5个）
步骤7: AT+XIIC=0                                    // 去激活 PPP → 触发死锁

预期: 返回 OK
实测: 返回 +UDPCLOSE: 0~4,Link Closed 后无 OK，AT 不通
```

#### 复现概率

| UDP socket 数 | 网络类型 | 事件估算 | 复现概率 | 原因 |
|:---:|:---:|:---:|:---:|:---|
| ≤ 3 | 任意 | ≤ 12 | **不触发** | 事件数远小于队列容量 20 |
| 4 | IPv4 | ~16 | **极低** | 未超容量，依赖队列残留事件 |
| 4 | IPv6 | ~19 | **低** | 接近容量，需要额外事件触发 |
| **5** | **IPv4** | **~20** | **高** | 恰好到容量边界 |
| **5** | **IPv6** | **~22** | **接近 100%** | 超过容量，IPv6 额外事件确保溢出 |
| ≥ 6 | 任意 | ≥ 24 | **100%** | 远超容量，必现 |

> **结论**：当前缺陷条件（5 UDP + IPv6）为 **接近必现的高概率问题**，根因是事件数/队列容量是固定数学关系，不是时序竞争。

### 2.5 修复方案

#### 已采纳方案 A：将 `_lwip_sock_evt` 中的 `portMAX_DELAY` 改为 100ms 超时

**修改文件**：`nwy_app_comm/platform/EC626/nwy_platform.c`

**修改内容**：`_lwip_sock_evt` 函数（L756）中 4 处 `trig_sock_event` 调用的超时参数由 `portMAX_DELAY` 改为 `100`（100ms，`configTICK_RATE_HZ=1000`，1 tick = 1ms）。

```c
/*Begin: Modify by niusulong for/to fix bug 6974423486 lwip sock evt deadlock in 2026.06.04*/
trig_sock_event(s, NWY_WRITE_EVENT, 100); //100ms timeout, avoid deadlock when queue full
trig_sock_event(s, NWY_ACCEPT_EVENT, 100); //100ms timeout, avoid deadlock when queue full
trig_sock_event(s, NWY_READ_EVENT, 100); //100ms timeout, avoid deadlock when queue full
trig_sock_event(s, NWY_CLOSE_EVENT, 100); //100ms timeout, avoid deadlock when queue full
/*End: Modify by niusulong for/to fix bug 6974423486 lwip sock evt deadlock in 2026.06.04*/
```

#### 超时值选择（100ms）

| 超时值 | XIIC=0 额外延迟（3个溢出事件） | 正常运行保护 | 评估 |
|:---:|:---:|:---:|:---|
| 10 ticks (10ms) | 30ms | 偏短，偶发丢事件 | 过于激进 |
| **100 ticks (100ms)** | **300ms** | **充裕** | **✅ 采纳** |
| 1000 ticks (1s, 同`RTOS_MSG_TIMEOUT`) | 3s | 非常充裕 | 没必要 |

#### 事件丢失安全性分析

**XIIC=0 / TCP Server 全关 / Accept 全关场景**：socket 关闭由调用线程同步完成全部资源释放（`lwip_close` → `nwy_dss_close` → 析构 → URC），`_lwip_sock_evt` 事件是冗余通知，丢失无影响。

**残留事件处理**：XIIC=0 完成后 `fwk_event_loop` 恢复消费队列中的积压事件，有三层 NULL 检查保护，无 use-after-free 风险：
1. `get_handleID_by_sockfd()` → 返回 -1（映射已由 `del_sockfd_handleID_map` 清除）→ 跳过
2. `nwy_app_get_dsnet_by_dsnet_handle()` → 返回 NULL（dsnet 对象已释放）→ return
3. `nwy_find_socket_by_sockfd()` → 返回 NULL（socket 对象已析构）→ return

#### 修复覆盖的阻塞场景

该修复同时解决了所有持锁 + `lwip_close` 的三方死锁场景：

| 场景 | 入口 | 关闭 socket 数 | 100ms 是否覆盖 |
|------|------|:---:|:---:|
| XIIC=0 去激活 | `nwy_dsnet_down_clear_all` | 全部（≤22） | ✅ |
| TCP Server 全部关闭 | `nwy_socket_server_close` | 所有 SVR（≤22） | ✅ |
| Accept 连接全部关闭 | `nwy_socket_acpt_close` | 所有 ACPT（≤12） | ✅ |
| 单 socket 关闭 | `nwy_socket_client_close` | 1 | 无需（4 事件 << 20） |

#### 测试结果：✅ 通过

#### 其他备选方案（未采纳）

| 方案 | 描述 | 未采纳原因 |
|------|------|------|
| 方案 B：XIIC=0 异步执行 | `fwk_event_loop` 异步处理，AT 线程立即返回 | 改动大，需调整 AT 响应时机 |
| 方案 C：增大队列容量 | `xQueueCreate(20)` → `xQueueCreate(50)` | 治标不治本，仅降低概率 |
| 方案 D：持锁期间不调用 `lwip_close` | 先标记关闭，释放锁后批量关闭 | 需重构关闭流程 |

## 3. 相关文件

| 文件 | 说明 |
|------|------|
| `PLAT/middleware/eigencomm/at/nwy_at/nwy_net/src/nwy_at_net.c` | AT+XIIC 命令处理入口 |
| `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/platform/EC626/nwy_platform.c` | `_lwip_sock_evt` (L756), `trig_sock_event` (L493), `fwk_event_loop` (L429), `nwy_dss_close` (L865), `sock_event_queue` 容量=20 (L434) |
| `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/src/nwy_app_dsnet.cpp` | `nwy_dsnet_down_clear_all` (L136), `nwy_handle_dsnet_event` (L2160) |
| `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/src/nwy_app_data_mgr.cpp` | `nwy_app_handle_dsnet_event` (L65) - 获取 `nwy_dsnet_crit_sect` 互斥锁 |
| `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/src/nwy_app_event_handler.cpp` | `nwy_app_net_cb` (L51), `nwy_app_sock_cb` (L90) |
| `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/src/nwy_socket_base.cpp` | `close_socket` (L641), `net_down_event` (L248) |
| `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_at_proc/src/nwy_app_at_func_tcp.c` | UDP 回调 `nwy_app_at_udp_cb` (L913) - 发送 UDPCLOSE URC |
| `PLAT/middleware/eigencomm/at/atcust/src/atec_cust_cmd_table.c` | XIIC 守护定时器定义 30 秒 (L1612) |