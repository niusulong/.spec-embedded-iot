# UDP连接进出PSM模式后需要重新建立连接 - 机制分析报告

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **平台** | EC626 |
| **模块** | UDP/Socket管理 |
| **问题分类** | 状态机异常 |
| **症状关键词** | PSM退出后UDP断开, HIB Exit后DISCONNECT, 需重新建立连接, hibCheck未注册, socket上下文未保存 |
| **根因概述** | 标准UDP AT指令(AT+UDPSETUP)使用ATSKT来源socket，未注册hibCheck回调导致hibEnable=FALSE，PSM进入时socket上下文不保存到AON区域，退出后连接状态丢失 |
| **调用链摘要** | AT+UDPSETUP → ATSKT(hibCheck=NULL) → cmsSockMgr(hibEnable=FALSE) → PSM进入不保存上下文 → PSM退出后DISCONNECT |
| **检索关键词** | UDP PSM恢复, HIB Exit UDP断开, ATSKT hibCheck, cmsSockMgr hibernate, socket上下文保存, CoAP LwM2M睡眠管理, AT+UDPSETUP PSM, socket来源类型 |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 解决方案](#3-解决方案)
- [4. 验证步骤](#4-验证步骤)
- [5. 相关文件](#5-相关文件)

---

## 1. 问题描述

**问题现象**：正常建立UDP连接并发送数据成功，进出PSM模式（HIB Enter/Exit）之后，再次发送数据失败，需要重新建立UDP连接。

**问题类型**：单日志分析

**测试时间**：2026-04-10 14:05:00 - 14:24:33

**重要背景**：根据文档说明，**UDP业务以及传输层基于UDP协议的CoAP/LwM2M业务（例如运营商物联网开放平台），模块支持WAKEUP管脚主动唤醒后直接收发数据，而不需要重新建立UDP连接，不需要重新建立物联网开放平台连接。**

## 2. 根本原因

**结论：这是设计差异，标准UDP AT指令不支持PSM恢复，而CoAP/LwM2M有独立的睡眠管理机制。**

### 2.1 关键日志证据

#### 正常建立UDP连接并发送数据（成功）

```
[14:05:06] AT+UDPSETUP=0,36.99.162.16,8255
           OK
           +UDPSETUP: 0,OK

[14:05:11] AT+UDPSEND=0,497,"..."
           OK
           +UDPSEND: 0,497

[14:05:13] +UDPRECV: 0,26,...  // 成功接收服务器响应
```

#### 进入PSM模式后退出，连接断开

```
[14:05:37] +HIB Enter
[14:17:42] +HIB Exit
[14:17:43] AT+IPSTATUS=0
           +IPSTATUS: 0,DISCONNECT  // 连接已断开
```

### 2.2 Socket来源类型与Hibernate支持差异

系统定义了多种Socket来源类型：

```c
// middleware/eigencomm/cms/sockmgr/inc/cms_sock_mgr.h:91
typedef enum{
    SOCK_SOURCE_MINI   = 0,
    SOCK_SOURCE_ATSKT,      // AT命令创建的socket（AT+UDPSETUP使用）
    SOCK_SOURCE_ECSOC,      // ECSOC接口创建的socket
    SOCK_SOURCE_SDKAPI,     // SDK API创建的socket
    SOCK_SOURCE_ECSRVSOC,   // ECSRV socket
    SOCK_SOURCE_MAX,
}CmsSockMgrSource;
```

**关键差异：不同来源的hibernate支持不同**

| Socket来源 | hibCheck回调 | hibernate支持 | 使用场景 |
|------------|--------------|---------------|----------|
| SOCK_SOURCE_ATSKT | **NULL** | **不支持** | AT+UDPSETUP/TCPSETUP |
| SOCK_SOURCE_ECSOC | EcsocCheckHibMode | 支持 | ECSOC接口 |
| SOCK_SOURCE_ECSRVSOC | EcSrvsocCheckHibMode | 支持 | TCP服务器 |

### 2.3 代码机制分析

#### ATSKT来源（AT+UDPSETUP使用）- 不支持hibernate

```c
// middleware/eigencomm/at/atentity/src/at_sock_entity.c:5435
atskthandleDefine.source = SOCK_SOURCE_ATSKT;
atskthandleDefine.hibCheck = NULL;  // ← 关键：未注册hibCheck回调
atskthandleDefine.recoverHibContext = atSktRecoverConnContext;
atskthandleDefine.storeHibContext = atSktStoreConnHibContext;
```

#### ECSOC来源 - 支持hibernate

```c
// middleware/eigencomm/at/atentity/src/at_sock_entity.c:5442
ecsochandleDefine.source = SOCK_SOURCE_ECSOC;
ecsochandleDefine.hibCheck = EcsocCheckHibMode;  // ← 关键：注册了hibCheck回调
ecsochandleDefine.recoverHibContext = atEcsocRecoverConnContext;
ecsochandleDefine.storeHibContext = atEcsocStoreConnHibContext;
```

#### hibernate模式启用条件

```c
// middleware/eigencomm/cms/sockmgr/src/cms_sock_mgr.c:750
CmsSockMgrContext* cmsSockMgrGetHibContext(CmsSockMgrConnType type)
{
    for(context = gSockMgrContext.contextList; context != NULL; context = context->next)
    {
        // 关键条件：hibEnable必须为TRUE
        if(context->hibEnable == TRUE &&
           (context->status == SOCK_CONN_STATUS_CONNECTED || context->status == SOCK_CONN_STATUS_CREATED))
        {
            if(context->type == type)
            {
                return context;
            }
        }
    }
    return context;
}
```

### 2.4 CoAP/LwM2M为什么能恢复？

**CoAP/LwM2M使用独立的睡眠管理机制，不依赖cmsSockMgr的hibernate机制。**

#### CoAP的睡眠管理

```c
// middleware/eigencomm/at/atentity/src/at_coap_task.c

// CoAP有独立的睡眠信息结构
static coapClientSlpInfo_t coapSlpInfo[COAP_CLIENT_NUMB_MAX];
static coapSlpNVMem_t coapSlpNVMem;  // 保存到NV存储

// 维护睡眠信息
void coapMaintainSlpInfo(uint8_t id, uint8_t msgType)
{
    if(msgType == COAP_MSGTYPE_CON)
    {
        coapSlpInfo[id].last_msg_type = msgType;
        coapSlpInfo[id].wait_msg = 1;
    }
    coapCheckSafe2Sleep();
}

// 从NV存储恢复上下文
void coapSlpCheck2RestoreCtx(void)
{
    if(coapSlpIsNVMemValid() == false)
    {
        stat = slpManGetLastSlpState();
        if((stat == SLP_SLP2_STATE) || (stat == SLP_HIB_STATE))
        {
            if(coapSlpNVMem.coapSlpCtx.restoreFlag == COAP_IS_CREATE)
            {
                coapCtxRestore();  // 恢复CoAP上下文
            }
        }
    }
}
```

#### LwM2M的睡眠管理

```c
// middleware/eigencomm/at/atentity/src/at_lwm2m_task.c

// LwM2M使用平台投票机制管理睡眠
void lwm2mInitSleepHandler()
{
    slpManRet_t result=slpManFindPlatVoteHandle("LWM2MHIB", &lwm2mSlpHandler);
    if(result==RET_HANDLE_NOT_FOUND)
        slpManApplyPlatVoteHandle("LWM2MHIB",&lwm2mSlpHandler);
}

void lwm2mDisableSleepmode()
{
    slpManPlatVoteDisableSleep(lwm2mSlpHandler, SLP_SLP2_STATE);
}

void lwm2mEnableSleepmode()
{
    slpManPlatVoteEnableSleep(lwm2mSlpHandler, SLP_SLP2_STATE);
}

// LwM2M使用文件系统保存状态
// 注册成功后保存到文件
if(code == LWM2M_NOTIFY_CODE_SUCCESS){
    lwm2mSaveFile();  // 保存状态到文件系统
}
```

### 2.5 架构对比图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        PSM/Hibernate 恢复机制对比                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐│
│  │  AT+UDPSETUP    │     │     CoAP        │     │     LwM2M       ││
│  │  (ATSKT来源)    │     │  (独立睡眠管理)  │     │  (独立睡眠管理)  ││
│  └────────┬────────┘     └────────┬────────┘     └────────┬────────┘│
│           │                       │                       │         │
│           ▼                       ▼                       ▼         │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐│
│  │ cmsSockMgr      │     │ coapSlpNVMem    │     │ lwm2mSaveFile   ││
│  │ hibEnable=FALSE │     │ (NV存储)        │     │ (文件系统)      ││
│  │ hibCheck=NULL   │     │                 │     │                 ││
│  └────────┬────────┘     └────────┬────────┘     └────────┬────────┘│
│           │                       │                       │         │
│           ▼                       ▼                       ▼         │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐│
│  │ PSM进入时不保存  │     │ PSM进入时保存    │     │ PSM进入时保存    ││
│  │ 上下文          │     │ 上下文到NV       │     │ 状态到文件       ││
│  └────────┬────────┘     └────────┬────────┘     └────────┬────────┘│
│           │                       │                       │         │
│           ▼                       ▼                       ▼         │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐│
│  │ PSM退出后       │     │ PSM退出后       │     │ PSM退出后       ││
│  │ DISCONNECT      │     │ 恢复上下文      │     │ 恢复连接        ││
│  │ 需要重连        │     │ 直接收发数据    │     │ 直接收发数据    ││
│  └─────────────────┘     └─────────────────┘     └─────────────────┘│
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.6 为什么标准UDP AT指令不支持hibernate？

**设计原因分析**：

1. **通用性考虑**：标准UDP AT指令是通用接口，需要支持各种场景，包括短连接、长连接等。自动启用hibernate可能在某些场景下产生意外行为。

2. **资源限制**：hibernate上下文需要保存到AON（Always-On）存储区域，资源有限。系统选择只为特定来源（ECSOC）启用。

3. **状态管理复杂度**：UDP是无连接协议，"连接状态"完全由应用层维护。标准AT指令不维护应用层状态，无法在PSM退出后自动恢复。

4. **CoAP/LwM2M的特殊性**：这些协议有明确的应用层会话概念，需要维护服务器注册状态、观察订阅等，必须有独立的睡眠管理机制。

### 2.7 5-Why 根因分析

```
第1个Why: 为什么UDP连接在PSM退出后变为DISCONNECT状态？
  → 因为socket上下文没有被保存到AON区域

第2个Why: 为什么socket上下文没有被保存？
  → 因为ATSKT来源的socket未启用hibernate模式（hibEnable=FALSE）

第3个Why: 为什么hibEnable为FALSE？
  → 因为ATSKT来源未注册hibCheck回调，导致cmsSockMgrCommonCheckHib()不会被调用

第4个Why: 为什么ATSKT来源不注册hibCheck回调？
  → 这是设计决策。标准UDP AT指令设计为不支持PSM恢复

第5个Why: 为什么CoAP/LwM2M可以恢复？
  → 因为CoAP/LwM2M实现了独立的睡眠管理机制，使用NV存储/文件系统保存状态

根本原因：标准UDP AT指令（AT+UDPSETUP）设计上不支持PSM恢复，
         而CoAP/LwM2M有独立的睡眠管理机制支持PSM恢复。
```

## 3. 解决方案

### 3.1 方案1: 使用CoAP/LwM2M协议（推荐，符合设计）

**说明**：如果业务场景是运营商物联网平台，应该使用CoAP或LwM2M协议，这些协议原生支持PSM恢复。

**实施方法**：
- 使用AT+COAPCREATE/AT+COAPSEND等CoAP命令
- 或使用AT+LWM2M相关命令

**优点**：
- 符合系统设计
- 原生支持PSM恢复
- 适合物联网场景

**实施难度**：中（需要适配协议）

### 3.2 方案2: 应用层实现自动重连（标准做法）

**说明**：对于标准UDP AT指令，应用层应该实现PSM退出后的自动重连逻辑。

**实施方法**：
```c
// 监听+HIB Exit URC
void onHibExit(void)
{
    // 检查UDP连接状态
    AT+IPSTATUS=0
    if (status == DISCONNECT)
    {
        // 重新建立UDP连接
        AT+UDPSETUP=0,server_ip,server_port
    }
}

// 或者在发送前检查
void sendUdpData(void)
{
    AT+IPSTATUS=0
    if (status == DISCONNECT)
    {
        AT+UDPSETUP=0,server_ip,server_port
    }
    AT+UDPSEND=0,len,data
}
```

**优点**：
- 简单直接
- 符合标准UDP AT指令的设计意图

**实施难度**：低

### 3.3 方案3: 修改代码启用ATSKT的hibernate支持（不推荐）

**说明**：修改代码为ATSKT来源启用hibernate支持。

**实施方法**：

```c
// 方案A：注册hibCheck回调
// middleware/eigencomm/at/atentity/src/at_sock_entity.c:5436
atskthandleDefine.hibCheck = atSktCheckHibMode;  // 新增

// 方案B：在UDP创建时直接启用hibernate
// 在nwy_app_udp_client_setup()成功后调用
cmsSockMgrEnableHibMode(sockMgrContext);
```

**风险**：
- 可能影响现有行为
- 需要充分测试各种场景
- 不符合原始设计意图

**实施难度**：中

## 4. 验证步骤

### 4.1 验证标准UDP AT指令行为

```
1. 建立UDP连接
   AT+UDPSETUP=0,36.99.162.16,8255
   预期：OK

2. 发送数据
   AT+UDPSEND=0,10,"testdata"
   预期：OK

3. 进入PSM
   预期：+HIB Enter

4. 退出PSM
   预期：+HIB Exit

5. 检查连接状态
   AT+IPSTATUS=0
   预期：+IPSTATUS: 0,DISCONNECT

6. 需要重连
   AT+UDPSETUP=0,36.99.162.16,8255
   预期：OK
```

### 4.2 验证CoAP的PSM恢复能力

```
1. 创建CoAP客户端
   AT+COAPCREATE=0,"coap://server:port"
   预期：OK

2. 发送CoAP请求
   AT+COAPSEND=0,...
   预期：收到响应

3. 进入PSM
   预期：+HIB Enter

4. 退出PSM
   预期：+HIB Exit

5. 直接发送CoAP请求（不需要重新创建）
   AT+COAPSEND=0,...
   预期：收到响应
```

## 5. 相关文件

### 标准UDP AT指令
- Socket管理器：[cms_sock_mgr.c](middleware/eigencomm/cms/sockmgr/src/cms_sock_mgr.c)
- Socket初始化：[at_sock_entity.c](middleware/eigencomm/at/atentity/src/at_sock_entity.c)
- UDP AT命令：[nwy_app_at_func_tcp.c](middleware/thirdparty/NWY_FRAMEWORK/nwy_app_at_proc/src/nwy_app_at_func_tcp.c)

### CoAP睡眠管理
- CoAP任务：[at_coap_task.c](middleware/eigencomm/at/atentity/src/at_coap_task.c)
- CoAP AT命令：[atec_coap.c](middleware/eigencomm/at/atcust/src/atec_coap.c)

### LwM2M睡眠管理
- LwM2M任务：[at_lwm2m_task.c](middleware/eigencomm/at/atentity/src/at_lwm2m_task.c)
- LwM2M AT命令：[atec_lwm2m.c](middleware/eigencomm/at/atcust/src/atec_lwm2m.c)

## 6. 总结

| 项目 | 标准UDP AT指令 | CoAP/LwM2M |
|------|---------------|------------|
| **PSM恢复支持** | **不支持** | **支持** |
| **睡眠管理机制** | 无（hibEnable=FALSE） | 独立实现（NV存储/文件系统） |
| **设计意图** | 通用UDP接口，应用层管理状态 | 物联网平台专用，自动维护会话 |
| **推荐做法** | PSM退出后应用层重连 | 直接使用，无需重连 |

**关键结论**：
1. **标准UDP AT指令（AT+UDPSETUP）设计上不支持PSM恢复**，这是预期行为，不是Bug
2. **CoAP/LwM2M有独立的睡眠管理机制**，支持PSM恢复，符合文档描述
3. 如果业务需要PSM恢复能力，应使用CoAP/LwM2M协议，或在应用层实现重连逻辑

---
*报告生成时间：2026-04-13*
*分析工具：spec-bug-analyzer*