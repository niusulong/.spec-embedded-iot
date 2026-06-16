## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **工作项 ID** | 6977185133 |
| **平台** | EC626 |
| **模块** | NWY框架/数据通道管理 (TCP) |
| **问题分类** | 状态机异常 |
| **症状关键词** | PSM唤醒后TCP连接失败, HIB Exit后TCPSETUP ERROR, 需重新XIIC拨号, dsnet未重建, NWY_APP_ERR_DSNET_NOT_ACTIVE |
| **根因概述** | HIBERNATE 唤醒后 16KB Retention SRAM 全部掉电，NWY 框架运行时 dsnet 状态（RAM 中）丢失，TCPSETUP 的 socket_check 检测到 dsnet 未 UP 直接返回 ERROR，需 AT+XIIC=1 重新调用 nwy_app_open_channel 重建数据通道 |
| **调用链摘要** | AT+TCPSETUP → nwy_app_tcp_client_setup → nwy_app_socket_check → nwy_dsnet_is_up()==FALSE → NWY_APP_ERR_DSNET_NOT_ACTIVE → ERROR；AT+XIIC=1 → nwy_app_open_channel + nwy_dsnet_status_adpt_rtos(1,1) → dsnet UP → TCPSETUP 成功 |
| **检索关键词** | PSM 唤醒 TCP失败, HIB Exit TCPSETUP ERROR, AT+XIIC=1 重新拨号, dsnet未重建, NWY_APP_ERR_DSNET_NOT_ACTIVE, nwy_app_socket_check, nwy_dsnet_is_up, HIBERNATE 唤醒 dsnet状态丢失, EC626 低功耗恢复 |

---

# TCP连接PSM唤醒后需手动XIIC=1拨号 - 原因分析

## 目录
- [0. 结构化摘要](#0-结构化摘要)
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 相关文件](#3-相关文件)
- [4. 结论与建议](#4-结论与建议)

---

## 1. 问题描述

**缺陷名称**：6977185133+【TCP】模块PSM唤醒后不能直接建立TCP连接，需要手动拨号XIIC=1后才可以连接

**缺陷基本信息**

| 字段 | 内容 |
|------|------|
| 工作项ID | 6977185133 |
| 所属项目 | N306-EA-01-不带蓝牙 |
| 严重程度 | B-严重 |
| 故障概率 | 必现 |
| 功能模块 | 基础通信 |
| 发现版本 | N306-E08-STD-BZ_EA-009 |
| Bug来源 | 测试发现-软测 |

**问题现象**：模块建立 TCP 连接并发送/接收数据成功后，进入 PSM（HIBERNATE）再唤醒，直接执行 `AT+TCPSETUP` 返回 `ERROR`；必须先执行 `AT+XIIC=1` 重新拨号后才能建立 TCP 连接。

**问题类型**：单日志分析

**测试时间**：2026-04-22 14:51:03 - 14:52:54

---

## 2. 根本原因

**结论：这是 EC626 平台的预期设计行为，不是 Bug。** HIBERNATE 唤醒后 16KB Retention SRAM 全部掉电，系统从 main_entry 重新运行；NB-IoT 网络层注册（PSM 保持范围）仍在，但 NWY 框架的运行时数据通道状态（dsnet，存于 RAM）已丢失，必须通过 `AT+XIIC=1` 重建。`AT+TCPSETUP` 在 dsnet 未 UP 时按设计返回 ERROR。

### 2.1 关键日志证据

#### AT命令日志

**阶段一：建立 TCP 并收发数据成功**

```
[14:51:03:802] AT+XIIC?                          ← 数据通道已激活 (1,IPv4+IPv6)
[14:51:03:802] +XIIC:    1,10.105.43.7
[14:51:03:802] +XIIC:    1,2409:8d70:0414:00a4:18a8:9b27:75f1:f181
[14:51:05:342] AT+TCPSETUP=1,120.86.64.161,10032  ← TCP 建立，OK
[14:51:05:584] +TCPSETUP: 1,OK
[14:51:06:629] AT+TCPSEND=1,1024                 ← 发送 1024 字节，OK
[14:51:09:884] +TCPSEND: 1,1024
[14:51:12:082] +TCPRECV: 1,1024,...              ← 接收回环数据，OK
```

**阶段二：进入 HIBERNATE**

```
[14:51:13:282] AT+ECPMUCFG=1,4                   ← 设置最大睡眠深度=HIBERNATE
[14:51:13:282] OK
[14:52:19:450] +HIB Enter                        ← 进入 Hibernate（16KB Ret SRAM 掉电）
```

**阶段三：唤醒后直接 TCPSETUP → ERROR**

```
[14:52:33:412] +HIB Exit                         ← 唤醒，系统从 main_entry 重新运行
[14:52:38:589] OK
[14:52:38:847] ATE1
[14:52:38:847] OK
[14:52:41:079] AT+TCPSETUP=1,120.86.64.161,10032
[14:52:41:079] +TCPSETUP: ERROR                  ← ★ 失败：dsnet 未重建
```

**阶段四：手动 XIIC=1 后 TCPSETUP 成功**

```
[14:52:43:034] AT+XIIC=1                         ← ★ 重建数据通道
[14:52:43:034] OK
[14:52:45:262] AT+TCPSETUP=1,120.86.64.161,10032
[14:52:45:262] OK
[14:52:46:269] +TCPSETUP: 1,OK                   ← 成功
[14:52:46:239] AT+TCPSEND=1,1024                 ← (太早，socket 未就绪)
[14:52:46:239] +TCPSEND: SOCKET ID OPEN FAILED
[14:52:49:764] AT+TCPSEND=1,1024                 ← socket 就绪后发送，OK
[14:52:52:619] +TCPSEND: 1,1024
[14:52:54:229] +TCPRECV: 1,1024,...              ← 回环接收成功
```

### 2.2 代码调用链

| 信息 | 值 |
|------|-----|
| **入口函数** | `nwy_app_at_tcpsetup_func()` |
| **失败调用链** | `nwy_app_at_tcpsetup_func()` → `nwy_app_tcp_client_setup()` → `nwy_app_socket_check()` → `nwy_dsnet_is_up()==FALSE` → `NWY_APP_ERR_DSNET_NOT_ACTIVE` |
| **问题位置** | `nwy_app_data_mgr.cpp:383`（dsnet_is_up 检查） |
| **修复调用链** | `AT_CmdFunc_NWY_XIIC()` → `nwy_app_open_channel()` + `nwy_dsnet_status_adpt_rtos(1,1)` → dsnet UP |

**调用链分析**：

1. **TCPSETUP 失败路径**：

   ```
   AT+TCPSETUP
     ↓
   nwy_app_at_tcpsetup_func()           // nwy_app_at_func_tcp.c:1987
     ↓
   nwy_app_tcp_client_setup(profile_id, channel, ...)  // nwy_app_api.cpp:414
     ↓
   nwy_app_socket_check(profile_id, usr_id)            // nwy_app_api.cpp:414
     ↓                                                    // nwy_app_data_mgr.cpp:377
   nwy_dsnet = nwy_app_get_dsnet_by_profile_id(profile_id)
   if (nwy_dsnet == NULL || !nwy_dsnet->nwy_dsnet_is_up())   // ★ 第383行
       return NWY_APP_ERR_DSNET_NOT_ACTIVE             // HIB唤醒后 dsnet 未 UP
     ↓
   nwy_app_at_tcpsetup_func() 返回 "+TCPSETUP: ERROR"   // nwy_app_at_func_tcp.c:1995
   ```

2. **XIIC=1 修复路径**：

   ```
   AT+XIIC=1
     ↓
   AT_CmdFunc_NWY_XIIC()                // nwy_at_net.c:592
     ↓ (nwy_status==1, 网络层已 NM_NETIF_ACTIVATED)
   atcReply(OK)                         // 先回复 OK
   nwy_app_open_channel(1, "test", nwy_app_dsnet_cb)   // nwy_at_net.c:638 重建数据通道
   nwy_adpt_sleep(100)
   nwy_dsnet_status_adpt_rtos(1, 1)     // nwy_at_net.c:640 标记 dsnet 已 UP
     ↓
   之后再 AT+TCPSETUP → nwy_app_socket_check 通过 → 成功
   ```

### 2.3 问题分析

#### 2.3.1 HIBERNATE 的内存掉电特性（AN0024 依据）

依据《EC NB-IoT 低功耗开发手册》(AN0024 V1.6)：

| 状态 | 256KB SRam | 16KB Ret SRam | 唤醒后程序 |
|------|-----------|---------------|-----------|
| IDLE | ON | ON | 原地继续执行 |
| SLEEP1 | ON | ON | 原地继续执行 |
| SLEEP2 | **OFF** | ON | 从 main_entry 重新运行 |
| **HIBERNATE** | **OFF** | **OFF** | **从 main_entry 重新运行** |

> AN0024 §3.1.4：Hibernate 状态下，16KB Sram 也将掉电。
> AN0024 §3.6.3：SLEEP2 和 HIBERNATE 将重新开始运行 main_entry 函数。
> AN0024 §8.1(1)：由于 Sleep2 及 Hibernate 状态下 sram 会大部分掉电或全部掉电，因此运行在其上的 RTOS 系统也无法继续维持运行。

本日志中 `AT+ECPMUCFG=1,4` 配置最大睡眠深度为 HIBERNATE，`+HIB Enter/+HIB Exit` 确认实际进入了 Hibernate。唤醒后 **全部 SRAM 掉电**，NWY 框架的 `nwy_app_dsnet` 运行时对象（含 `is_up` 状态）必然丢失。

#### 2.3.2 PSM 保持范围 vs 框架状态丢失

| 层级 | PSM 是否保持 | 说明 |
|------|-------------|------|
| **NB-IoT 网络层注册** | ✅ 保持 | PSM 的设计目标，唤醒后无需重新 Attach/认证 |
| **IP 地址分配** | ✅ 保持 | PSM 期间 IP 租约有效 |
| **NWY 框架 dsnet 状态** | ❌ 丢失 | 运行时 RAM 对象，HIB 唤醒后消失 |
| **LWIP 协议栈运行时** | ❌ 丢失 | RTOS 重启，TCP 连接、socket 表全部清空 |
| **TCP 连接** | ❌ 丢失 | 即便上层重建，原 TCP 连接已断（对端 RST） |

**关键点**：`AT+XIIC?` 查询的是网络层 IP 状态（通过 `appGetNetInfoSync`），PSM 唤醒后仍会显示 `1,<IP>`，容易让用户误以为数据通道已就绪；但 `AT+TCPSETUP` 走的是 NWY 框架路径，需要 `dsnet_is_up()==TRUE`，该状态在 HIB 唤醒后未自动恢复，必须 `AT+XIIC=1` 重建。

#### 2.3.3 错误码含义

- `NWY_APP_ERR_DSNET_NOT_ACTIVE`：数据网络（dsnet）未激活。在 `nwy_app_socket_check()` 中，当 `nwy_dsnet == NULL || !nwy_dsnet->nwy_dsnet_is_up()` 时返回。
- TCPSETUP 将该错误码映射为 `+TCPSETUP: ERROR`（见 nwy_app_at_func_tcp.c:1993-1995）。

#### 2.3.4 历史案例佐证（知识库匹配，相似度 0.59）

知识库案例「UDP连接PSM模式」结论一致：**标准 AT TCP/UDP 指令（ATSKT 来源）设计上不支持 PSM/Hibernate 恢复**，需要应用层在唤醒后重建连接。CoAP/LwM2M 才有独立的睡眠管理机制（coapSlpNVMem / lwm2mSaveFile）支持 PSM 恢复。本案例的 XIIC=1 重建是同一设计约束的另一种表现：dsnet 框架状态不在 PSM 保持范围内。

### 2.4 问题复现路径

| 项目 | 内容 |
|------|------|
| **前置条件** | 1. 模块已注网成功，PSM 协商完成（网络侧已分配 PSM 时间）<br>2. AT+XIIC=1 已执行，数据通道已激活（dsnet UP）<br>3. 已通过 AT+ECPMUCFG=1,4 使能 HIBERNATE |
| **必要状态** | 模块处于 ACTIVE 且无数据收发，PSM 定时器到期触发 HIBERNATE |
| **操作步骤** | 1. AT+XIIC? 确认数据通道 UP（显示 1,<IP>）<br>2. AT+TCPSETUP=1,<ip>,<port> 建立 TCP，确认 OK<br>3. AT+TCPSEND=1,<len> 发送数据，确认 OK<br>4. 等待 +HIB Enter（进入 Hibernate）<br>5. 等待 +HIB Exit（PSM 唤醒）<br>6. AT+TCPSETUP=1,<ip>,<port> 直接建立 TCP → **复现 ERROR** |
| **复现概率** | **必现**（HIBERNATE 唤醒后每次必现） |
| **验证方法** | 步骤 6 出现 `+TCPSETUP: ERROR` 即复现；执行 AT+XIIC=1 后再次 TCPSETUP 返回 OK 即确认根因 |

**规避方法**（应用层正确做法）：

```
PSM 唤醒后（监听 +HIB Exit URC）：
  1. AT+XIIC=1           ← 重建数据通道
  2. 等待 OK
  3. AT+TCPSETUP=1,<ip>,<port>   ← 现在可以成功
```

---

## 3. 相关文件

### TCP/数据通道业务
- TCPSETUP AT 处理：`PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_at_proc/src/nwy_app_at_func_tcp.c:1919`（nwy_app_at_tcpsetup_func）
- TCP 客户端 setup：`PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/src/nwy_app_api.cpp:386`（nwy_app_tcp_client_setup）
- socket/dsnet 检查：`PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/src/nwy_app_data_mgr.cpp:377`（nwy_app_socket_check，★ 第383行 dsnet_is_up 检查）
- XIIC AT 处理：`PLAT/middleware/eigencomm/at/nwy_at/nwy_net/src/nwy_at_net.c:592`（AT_CmdFunc_NWY_XIIC，★ 第638-640行重建 dsnet）
- dsnet 状态设置：`PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/platform/EC626/nwy_platform.c:631`（nwy_dsnet_status_adpt_rtos）

### 低功耗机制
- PMU 配置 AT：`AT+ECPMUCFG=1,4`（设置最大睡眠深度=HIBERNATE）
- 参考文档：AN0024《EC NB-IoT 低功耗开发手册》V1.6 §3.1.4 / §3.6.3 / §8.1

### 历史参考案例
- 知识库：EC626/UDP连接PSM模式.md（相似度 0.59，结论一致：标准AT TCP/UDP不支持PSM恢复）

---

## 4. 结论与建议

### 4.1 结论

| 项目 | 结论 |
|------|------|
| **是否 Bug** | **否**，是 EC626 平台的预期设计行为 |
| **严重程度建议** | 建议从 B-严重 降级为「设计限制/已知行为」 |
| **根因** | HIBERNATE 唤醒后全部 SRAM 掉电，NWY 框架 dsnet 运行时状态丢失，需 XIIC=1 重建；这是 PSM 保持范围（仅网络层注册）与框架运行时状态（RAM）边界差异的正常表现 |
| **文档依据** | AN0024 §3.1.4/§3.6.3/§8.1：HIBERNATE 全部 SRAM 掉电，系统从 main_entry 重新运行 |

### 4.2 处理建议

1. **测试/用例层面**：用例应将「PSM 唤醒后需 XIIC=1 重建数据通道」作为预期步骤，而非异常。
2. **应用层文档**：在客户应用笔记中明确说明——PSM/HIB 唤醒后必须先执行 `AT+XIIC=1`，再发起 TCP/UDP/HTTP 等数据业务。
3. **如需"零感知"恢复**：仅 CoAP/LwM2M 协议支持 PSM 后直接收发（独立睡眠管理机制），标准 AT TCP/UDP 不支持。

---

*报告生成时间：2026-06-15*
*分析工具：spec-bug-analyzer*
*参考文档：AN0024 V1.6、知识库案例「UDP连接PSM模式」(相似度 0.59)*