# 【udp】AT+COPS=2或CFUN=4后UDP链路依旧存在，需手动关闭后才可重新建立连接 原因分析

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **平台** | EC626 |
| **模块** | UDP/LWIP/NWY框架 |
| **问题分类** | 状态机异常 |
| **症状关键词** | COPS=2后UDP未关闭, CFUN=4后socket残留, UDPSETUP ERROR1, UDP链路未清理, bearer释放路径缺陷 |
| **根因概述** | COPS=2/CFUN=4的bearer释放路径仅设置OOS标志位，未调用nwy_dsnet_status_adpt_rtos触发NWY框架的nwy_plat_net_cb_g回调，导致UDP socket未被清理而保持OPENED状态 |
| **调用链摘要** | AT+COPS=2/CFUN=4 → bearer释放 → netif_enable_oos_state → udp_netif_enter_oos_state(仅设标志) → 未触发nwy_plat_net_cb_g → socket未关闭 |
| **检索关键词** | UDP socket未关闭, COPS=2 UDP残留, CFUN=4 UDP链路, nwy_plat_net_cb_g, nwy_dsnet_status_adpt_rtos, OOS标志位, UDPSETUP ERROR1, bearer释放socket清理 |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 相关文件](#3-相关文件)

---

## 1. 问题描述

AT+COPS=2（注销网络）或 AT+CFUN=4（飞行模式）后，已建立的 UDP socket 不会被主动关闭，socket 状态保持为 CONNECT。导致重新注册网络后，使用相同 channel 执行 AT+UDPSETUP 时返回 ERROR1（socket 已存在），必须先手动 AT+UDPCLOSE 关闭旧链路才能重新建立。

**问题类型**：单日志分析

## 2. 根本原因

**COPS=2/CFUN=4 的 bearer 释放路径不经过 NWY 框架的网络状态回调（`nwy_plat_net_cb_g`），导致 UDP socket 不会被清理。对比 AT+XIIC=0（PDP 去激活），它显式调用 `nwy_dsnet_status_adpt_rtos(1, 0)` → `nwy_plat_net_cb_g()` 触发完整的 socket 清理流程，能正确上报 `+UDPCLOSE: 0,Link Closed`。**

### 2.1 关键日志证据

#### AT命令日志 — COPS=2 场景

```
[947]  ATCMD , decode AT: AT+UDPSETUP=0,8.135.10.183 ,56319                   → 15:00:18.506  建立UDP
[1053] NWY_FRM: nwy_app_at_udp_cb sid = 0 status = 5                          → 15:00:18.523  socket创建成功(NWY_SOCKET_OPENED=5)
[1276] AT CMD , RESP: +IPSTATUS: 0,CONNECT,UDP,0                              → 15:00:25.850  确认socket状态=CONNECT

[1363] ATCMD , decode AT: AT+COPS=2                                            → 15:00:28.786  ⚠️ 注销网络
[1708] CEDR , EPSID: 5 , free bearer context                                  → 15:00:29.446  Bearer释放
[1867] udp_netif_enter_oos_state pcb 0x1f738                                   → 15:00:29.459  UDP进入OOS
[1879] udp_netif_exit_oos_state pcb 0x1f738                                    → 15:00:29.460  UDP立即退出OOS ⚠️
[1881] Netif status changed to: 2 (OOS)                                        → 15:00:29.459  网络接口→OOS
[1891] Netif status changed to: 1 (DEACTIVATED)                                → 15:00:29.460  网络接口→去激活

[2484] AT CMD , RESP: +IPSTATUS: 0,CONNECT,UDP,0                              → 15:00:32.994  ⚠️ socket仍为CONNECT！

[2690] ATCMD , decode AT: AT+UDPSETUP=0,8.135.10.183 ,56319                   → 15:00:35.680  尝试重建UDP
[2699] AT CMD , RESP: +UDPSETUP: 0,ERROR1                                     → 15:00:35.683  ⚠️ 失败：socket 0已存在

[2967] ATCMD , decode AT: AT+COPS=0                                            → 15:00:38.950  重新注册网络
[4146] Netif status changed to: 3 (ACT)                                        → 15:00:42.539  网络恢复

[5219] ATCMD , decode AT: AT+UDPSETUP=0,8.135.10.183 ,56319                   → 15:01:03.219  再次尝试重建UDP
[5228] AT CMD , RESP: +UDPSETUP: 0,ERROR1                                     → 15:01:03.223  ⚠️ 仍然失败！

[5445] lwip_netconn_do_delconn: remove check udp hib pcb                      → 15:01:07.196  hib机制延迟清理
[5461] NWY_FRM: nwy_app_at_udp_cb sid = 0 status = 7                          → 15:01:07.198  socket终于被关闭(status=7)
```

#### AT命令日志 — CFUN=4 场景

```
[5978] ATCMD , decode AT: AT+UDPSETUP=0,8.135.10.183 ,56319                   → 15:01:17.063  重建UDP成功
[6073] NWY_FRM: nwy_app_at_udp_cb sid = 0 status = 5                          → 15:01:17.079  socket创建成功

[6197] ATCMD , decode AT: AT+CFUN=4                                            → 15:01:20.290  ⚠️ 飞行模式
[6611] CEDR , EPSID: 5 , free bearer context                                  → 15:01:21.318  Bearer释放
[6653] udp_netif_exit_oos_state pcb 0x1f738                                    → 15:01:21.324  ⚠️ 仅退出OOS，未关闭socket

[7709] ATCMD , decode AT: AT+CFUN=1                                            → 15:01:31.445  恢复
[9356] ATCMD , decode AT: AT+CFUN=4                                            → 15:01:42.693  再次飞行模式
[10066] ATCMD , decode AT: AT+CFUN=1                                           → 15:01:47.332  恢复

[11550] ATCMD , decode AT: AT+UDPSETUP=0,8.135.10.183 ,56319                  → 15:01:54.300  尝试重建UDP
[11569] AT CMD , RESP: +UDPSETUP: 0,ERROR1                                    → 15:01:54.301  ⚠️ 失败！

[11900] lwip_netconn_do_delconn: remove check udp hib pcb                     → 15:02:02.247  hib机制延迟清理
[11917] NWY_FRM: nwy_app_at_udp_cb sid = 0 status = 7                         → 15:02:02.250  socket被关闭
```

### 2.2 代码调用链对比

#### AT+XIIC=0 — 正确关闭路径（参考基线）

```
AT+XIIC=0
  └→ nwy_at_net.c:631  nwy_dsnet_status_adpt_rtos(1, 0)
      └→ nwy_platform.c:630  nwy_plat_net_cb_g(profile_id, 0, 0, NULL)
          └→ NWY框架网络状态回调(status=0, 网络断开)
              └→ 遍历关闭所有 socket（含 UDP）
                  └→ nwy_app_at_udp_cb() status=7 (NWY_SOCKET_CLOSED)
                      └→ +UDPCLOSE: 0,Link Closed  ✅
```

#### AT+COPS=2 / CFUN=4 — 缺陷路径

```
AT+COPS=2 / CFUN=4
  └→ Modem协议栈 bearer释放 (CEDR free bearer context)
      └→ lwIP层: netif_enable_oos_state()
          └→ udp_netif_enter_oos_state()
              └→ upcb->related_netif_oos = 1  (仅设标志)
              └→ ⚠️ 未调用 nwy_dsnet_status_adpt_rtos()
              └→ ⚠️ 未调用 nwy_plat_net_cb_g()
              └→ ⚠️ UDP socket 状态不变，仍为 OPENED
```

| 信息 | XIIC=0 | COPS=2 / CFUN=4 |
|------|--------|------------------|
| **入口** | `nwy_dsnet_status_adpt_rtos(1, 0)` | `netif_enable_oos_state()` |
| **NWY框架回调** | ✅ 触发 `nwy_plat_net_cb_g()` | ❌ **未触发** |
| **socket清理** | ✅ 所有 socket 被关闭 | ❌ 仅设 PCB 标志位 |
| **AT层通知** | ✅ `+UDPCLOSE: 0,Link Closed` | ❌ 无通知 |

**关键差异**：`AT+XIIC=0` 在 `nwy_at_net.c:631` 显式调用 `nwy_dsnet_status_adpt_rtos(1, 0)`，该函数通过 `nwy_plat_net_cb_g()` 回调通知 NWY 框架网络已断开，框架随即遍历并关闭所有 socket（包括 UDP），触发 `NWY_SOCKET_CLOSED` 事件并上报 `+UDPCLOSE`。而 `COPS=2/CFUN=4` 的 bearer 释放仅在 lwIP 层设置 OOS 标志，**完全绕过了 NWY 框架的 socket 管理流程**。

### 2.3 问题分析

**UDP 与 TCP 的关键差异**：

| 维度 | TCP | UDP |
|------|-----|-----|
| 协议类型 | 面向连接 | 无连接 |
| 网络断开后被动关闭 | 有（超时/RST/FIN） | **无**（无连接状态） |
| OOS 时的实际效果 | 最终会被协议栈超时关闭 | **永远不会被被动关闭** |
| 最终清理机制 | 协议超时 | 仅依赖 hib/sleep2 后台清理 |

1. **OOS 机制设计缺陷**：`udp_netif_enter_oos_state()`（`udp.c:1835`）仅将 `related_netif_oos` 标志置 1，不执行 `udp_remove()` 或通知应用层。对比 TCP，虽然 `tcp_netif_enter_oos_state()`（`tcp.c:3826`）同样只设标志位，但 TCP 连接会在后续超时或对端关闭时被清理。

2. **UDPSETUP 检查导致失败**：`nwy_app_at_udpsetup_func()`（`nwy_app_at_func_tcp.c:3448`）在 line 3478 检查 socket 状态：
   ```c
   if (NWY_SOCKET_STATUS_OPENED == nwy_app_get_ip_status(nwy_get_pdp_cid(), channel)
       || NWY_SOCKET_STATUS_OPENING == nwy_app_get_ip_status(nwy_get_pdp_cid(), channel))
   {
       sprintf(_err, "+UDPSETUP: %d,ERROR1", channel);
       return nwy_app_at_func_resp_str(arg->at_channel, _err);
   }
   ```
   由于旧 socket 仍为 `NWY_SOCKET_STATUS_OPENED`，新创建请求被拒绝。

3. **延迟清理不可靠**：日志显示 socket 最终通过 `lwip_netconn_do_delconn: remove check udp hib pcb` 被清理（COPS=2 后约 38 秒，CFUN=4 后约 20 秒），这是休眠/低功耗后台机制的副作用，时序不确定，不能作为可靠修复手段。

4. **日志中 enter/exit OOS 几乎同时发生**：`udp_netif_enter_oos_state` (15:00:29.459) 和 `udp_netif_exit_oos_state` (15:00:29.460) 仅间隔 1ms，说明网络接口状态快速切换时，OOS 标志位被迅速清除，但 socket 本身的状态完全不受影响。

### 2.4 问题复现路径

| 项目 | 内容 |
|------|------|
| **触发条件** | 已建立 UDP 连接（AT+UDPSETUP 成功） |
| **必要状态** | socket 状态为 NWY_SOCKET_STATUS_OPENED |
| **操作步骤** | 1. AT+UDPSETUP=0,\<IP\>,\<PORT\> 建立UDP连接<br>2. AT+COPS=2 或 AT+CFUN=4 进入去注册/飞行模式<br>3. AT+COPS=0 或 AT+CFUN=1 恢复网络<br>4. AT+UDPSETUP=0,\<IP\>,\<PORT\> 尝试重建连接 → 返回 ERROR1 |
| **复现概率** | 100%，必现 |

## 3. 相关文件

- `PLAT/middleware/thirdparty/lwip/src/core/udp.c` — `udp_netif_enter_oos_state()` line 1835，OOS 处理逻辑（仅设标志，不关 socket）
- `PLAT/middleware/thirdparty/lwip/src/core/netif.c` — `netif_enable_oos_state()` line 931，网络 OOS 入口
- `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_at_proc/src/nwy_app_at_func_tcp.c` — `nwy_app_at_udpsetup_func()` line 3448，UDPSETUP 命令处理及状态检查；`nwy_app_at_udp_cb()` line 913，UDP 回调
- `PLAT/middleware/eigencomm/at/nwy_at/nwy_net/src/nwy_at_net.c` — `AT_CmdFunc_NWY_XIIC()` line 592，XIIC=0 的 PDP 去激活路径，调用 `nwy_dsnet_status_adpt_rtos(1, 0)` line 631
- `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/platform/EC626/nwy_platform.c` — `nwy_dsnet_status_adpt_rtos()` line 627，网络状态适配函数；`nwy_plat_net_cb_g()` line 458，NWY 框架网络回调入口

## 4. 修复建议

**核心思路**：COPS=2/CFUN=4 释放 bearer 时，应复用 XIIC=0 的 PDP 去激活路径，确保 NWY 框架的 `nwy_plat_net_cb_g()` 被调用以清理所有 socket。

- **方案 A**（推荐，NWY 框架层修复）：在 COPS=2/CFUN=4 的 bearer 释放处理流程中，增加对 `nwy_dsnet_status_adpt_rtos(1, 0)` 的调用，与 XIIC=0 走相同的 socket 清理路径。修改位置建议在 bearer 释放确认回调或网络状态变化通知处。
- **方案 B**（lwIP 层修复）：在 `udp_netif_enter_oos_state()`（`udp.c:1835`）中，对匹配的 UDP PCB 执行 `udp_remove()` 并通知上层关闭连接。
- **方案 C**（AT 层修复）：在 COPS=2/CFUN=4 的 AT 命令处理（`atec_mm.c` / `atec_dev.c`）中，bearer 释放后显式调用 `nwy_dsnet_status_adpt_rtos(1, 0)`，与 XIIC=0 行为对齐。

---

# 修复方案：COPS=2/CFUN=4 后关闭 UDP socket（最小化 v2）

## 关键发现

`mmSetDeregister()` 和 `devSetFunc()` 是**异步**的，仅发送请求到 Modem，真正结果在 cnf 回调中返回。因此 `nwy_dsnet_status_adpt_rtos` 必须放在 **cnf 回调成功时**执行，避免 deregister 失败但 socket 已被关闭。

## 修改文件清单（2 个文件，4 处修改）

| # | 文件 | 修改点 |
|---|------|--------|
| 1 | `PLAT/middleware/eigencomm/at/atps/src/cnfind/atec_mm_cnf_ind.c` | COPS=2 cnf 成功时关闭；COPS=0 cnf 成功时恢复 |
| 2 | `PLAT/middleware/eigencomm/at/atps/src/cnfind/atec_dev_cnf_ind.c` | CFUN=4/0 cnf 成功时关闭；CFUN=1 cnf 成功时恢复 |

---

### 文件 1: `atec_mm_cnf_ind.c`

#### 修改点 A — `mmCOPSDereCnf` (line 261)，COPS=2 确认成功时关闭

**修改后**:
```c
if(rc == CME_SUCC)
{
    /*Begin: Add by niusulong for/to close socket when COPS=2 dereg success in 2026.06.04*/
#ifdef FEATURE_NWY_AT
    extern void nwy_dsnet_status_adpt_rtos(sint15 profile_id, uint8 st);
    nwy_dsnet_status_adpt_rtos(1, 0);
#endif
    /*End: Add by niusulong for/to close socket when COPS=2 dereg success in 2026.06.04*/
    ret = atcReply(reqHandle, AT_RC_OK, 0, NULL);
}
```

#### 修改点 B — `mmCOPSSetAutoCnf` (line 211)，COPS=0 确认成功时恢复

**修改后**:
```c
if(rc == CME_SUCC)
{
    /*Begin: Add by niusulong for/to restore socket when COPS=0 auto reg success in 2026.06.04*/
#ifdef FEATURE_NWY_AT
    extern void nwy_dsnet_status_adpt_rtos(sint15 profile_id, uint8 st);
    nwy_dsnet_status_adpt_rtos(1, 1);
#endif
    /*End: Add by niusulong for/to restore socket when COPS=0 auto reg success in 2026.06.04*/
    ret = atcReply(reqHandle, AT_RC_OK, 0, NULL);
}
```

---

### 文件 2: `atec_dev_cnf_ind.c`

#### 修改点 C — `devCFUNSetCnf` (line 58)，CFUN 设置确认时判断

**修改后**:
```c
if (rc == CME_SUCC)
{
    /*Begin: Add by niusulong for/to close/restore socket when CFUN set success in 2026.06.04*/
#ifdef FEATURE_NWY_AT
    CmiDevSetCfunCnf *pCmiCnf = (CmiDevSetCfunCnf *)paras;
    extern void nwy_dsnet_status_adpt_rtos(sint15 profile_id, uint8 st);
    if (pCmiCnf != NULL && (pCmiCnf->func == 4 || pCmiCnf->func == 0))
        nwy_dsnet_status_adpt_rtos(1, 0);
    else if (pCmiCnf != NULL && pCmiCnf->func == 1)
        nwy_dsnet_status_adpt_rtos(1, 1);
#endif
    /*End: Add by niusulong for/to close/restore socket when CFUN set success in 2026.06.04*/
    ret = atcReply(reqHandle, AT_RC_OK, 0, NULL);
}
```

---

## 参考先例

`atec_ps.c:203-207` 已有相同跨层调用：
```c
nwy_dsnet_status_adpt_rtos(test_cid, 1);
nwy_dsnet_status_adpt_rtos(test_cid, 0);
```

## 验证步骤

1. AT+UDPSETUP=0,8.135.10.183,56319 → 成功
2. AT+COPS=2 → deregister 成功后收到 +UDPCLOSE:0,Link Closed
3. AT+COPS=0 → 注册成功后 AT+UDPSETUP → 成功
4. 如果 COPS=2 失败 → socket 保持不变，不影响后续操作
5. 同样测试 AT+CFUN=4 → AT+CFUN=1 → AT+UDPSETUP