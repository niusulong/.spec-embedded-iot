# LWM2M 加密(DTLS PSK)连接 REGISTER TIMEOUT 原因分析

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **工作项 ID** | NA |
| **平台** | EC626 |
| **模块** | CoAP/LWM2M |
| **问题分类** | 超时 |
| **症状关键词** | REGISTER TIMEOUT, DTLS, PSK, 加密连接超时, 握手失败 |
| **根因概述** | DTLS PSK加密连接时，connection_create()中DTLS握手在后续connection_send()时才隐式触发，PSK凭据不匹配或DTLS版本不兼容导致握手失败，Register请求无法发出，事务超时触发REGISTER TIMEOUT |
| **调用链摘要** | lwm2mCREATE → lwm2mConnectServer → connection_create → connection_send → dtls_write → 握手失败 → REGISTER TIMEOUT |
| **检索关键词** | LWM2M, DTLS, PSK, REGISTER TIMEOUT, 加密连接, coaps, TinyDTLS, 握手失败 |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 相关文件](#3-相关文件)

---

## 1. 问题描述

使用 AT+LWM2MCREATE 指令连接 LwM2M 服务器时，非加密连接（coap:// 端口5683）正常，但 DTLS PSK 加密连接（coaps:// 端口5684）超时，返回 `+LWM2M ERROR: REGISTER TIMEOUT`。

**问题类型**：对比分析

## 2. 根本原因

**DTLS 握手阶段 `connection_create()` 返回 NULL，导致 `lwm2mConnectServer()` 重试30次后仍返回 NULL，`prv_register()` 获取 sessionH 失败，注册请求无法发出，最终触发 REGISTER TIMEOUT。**

核心问题在于 `dtlsconnection.c:connection_create()` 中 DTLS 连接创建流程：该函数先通过 `getaddrinfo` + `connect` 验证服务器可达性，再调用 `get_dtls_context()` 初始化 DTLS 上下文，但 **DTLS 握手（`dtls_connect`）并未在此处执行**。DTLS 握手依赖后续 `lwm2m_step()` 中发送 CoAP Register 消息时由 `connection_send()` → `dtls_write()` 首次触发。若 DTLS 握手失败（如 PSK 凭据不匹配、DTLS 版本不兼容、服务器无响应等），Register 请求无法发出，事务超时后触发 `LWM2M_NOTIFY_CODE_TIMEOUT`。

### 2.1 关键日志证据

#### AT命令日志

**非加密连接（正常）**：
```
[14:02:51.986] AT+LWM2MCREATE="leshan.eclipseprojects.io",5683,56830,"nwy-lient-128",60
[14:02:54.011] +LWM2MCREATE: 0    ← 2秒内成功
[14:02:54.011] OK
```

**加密连接（异常）**：
```
[14:03:21.619] AT+LWM2MCREATE="leshan.eclipseprojects.io",5684,56830,"nwy-lient",60,123,313332
[14:04:25.697] +LWM2M ERROR: REGISTER TIMEOUT    ← 约64秒后超时
```

#### 关键差异

| 对比维度 | 非加密 (coap://:5683) | 加密 (coaps://:5684) |
|----------|----------------------|---------------------|
| 协议 | UDP 直连 | DTLS over UDP |
| 端口 | 5683 | 5684 |
| 响应时间 | ~2秒 | ~64秒超时 |
| 结果 | +LWM2MCREATE: 0 | REGISTER TIMEOUT |
| pskId/psk | 无 | 123 / 313332 |

### 2.2 代码调用链

| 信息 | 值 |
|------|-----|
| **入口函数** | `lwm2mCREATE()` |
| **调用链** | `lwm2mCREATE()` → `lwm2mCreate()` → `lwm2m_configure()` → `lwm2m_step()` → `prv_register()` → `lwm2mConnectServer()` → `connection_create()` |
| **问题位置** | `dtlsconnection.c:connection_create()` 第435行 |

**调用链分析**：
1. `lwm2mCREATE()` 解析 AT 命令参数，设置 pskId/psk，调用 `lwm2mCreate()`
2. `lwm2mCreate()` 构建 `coaps://` URI，调用 `get_security_object()` 设置 `securityMode = LWM2M_SECURITY_MODE_PRE_SHARED_KEY`
3. 主循环 `lwm2mMainLoop()` 中 `lwm2m_step()` 触发注册流程 `prv_register()`
4. `prv_register()` 调用 `connectServerCallback` 即 `lwm2mConnectServer()`
5. `lwm2mConnectServer()` 调用 `connection_create()`，最多重试30次（每次间隔1秒）
6. `connection_create()` 中若 `securityMode != LWM2M_SECURITY_MODE_NONE`，调用 `get_dtls_context()` 初始化 DTLS 上下文
7. DTLS 握手在后续 `connection_send()` → `dtls_write()` 时触发
8. 握手失败 → Register 请求无法发出 → 事务超时 → `prv_handleRegistrationReply()` 中 packet==NULL → `LWM2M_NOTIFY_CODE_TIMEOUT` → `+LWM2M ERROR: REGISTER TIMEOUT`

#### 对比环境

| 对比维度 | 正常日志 | 异常日志 |
|----------|----------|----------|
| 场景描述 | 非加密连接 coap://:5683 | 加密连接 coaps://:5684 |
| AT命令 | LWM2MCREATE=...,5683,... | LWM2MCREATE=...,5684,...,123,313332 |
| securityMode | LWM2M_SECURITY_MODE_NONE | LWM2M_SECURITY_MODE_PRE_SHARED_KEY |
| DTLS握手 | 无 | 需要完成 |

#### 关键差异点

| 时间点 | 正常日志值 | 异常日志值 | 差异说明 |
|--------|-----------|-----------|----------|
| T1 | URI: coap://...:5683 | URI: coaps://...:5684 | **协议/端口不同** |
| T2 | securityMode=0 (NONE) | securityMode=1 (PSK) | **安全模式不同** |
| T3 | 无DTLS上下文 | get_dtls_context() 被调用 | **DTLS 初始化** |
| T4 | 直接发送 Register | 需先完成 DTLS 握手 | **额外握手阶段** |
| T5 | 收到 2.01 Created | packet==NULL (无响应) | **关键差异：握手失败** |
| T6 | +LWM2MCREATE: 0 | REGISTER TIMEOUT | **最终结果** |

### 2.3 问题分析

**分析1：PSK 凭据问题**

AT 命令传入 `pskId=123, psk=313332`。代码处理流程：
- `atec_lwm2m.c:150` — `pskLen = pskLen/2 = 6/2 = 3`（psk 为 hex 字符串，转为 3 字节二进制）
- `atec_lwm2m.c:154` — `cmsHexStrToHex()` 将 "313332" 转为字节 `{0x31, 0x33, 0x32}`
- `object_security.c` — `get_security_object()` 设置 `securityMode = LWM2M_SECURITY_MODE_PRE_SHARED_KEY`，存储 pskId 和 psk

DTLS 握手时 `get_psk_info()` 回调（`dtlsconnection.c:185`）从 Security Object 读取 pskId 和 psk 提供给 TinyDTLS。**若服务器端配置的 PSK 与客户端不匹配，握手将失败。**

**分析2：DTLS 握手时序**

`connection_create()` 中（`dtlsconnection.c:533-537`）：
```c
if (security_get_mode(connP->securityObj, connP->securityInstId)
         != LWM2M_SECURITY_MODE_NONE)
{
    connP->dtlsContext = get_dtls_context(connP);
}
```
此处仅初始化 DTLS 上下文，**未执行 `dtls_connect()` 进行握手**。DTLS 握手在 `connection_send()` 中首次 `dtls_write()` 时隐式触发。若握手未完成或失败，Register 请求无法发出。

**分析3：`lwm2mConnectServer()` 重试机制**

`at_lwm2m_task.c:1043-1057` 中，`connection_create()` 失败时循环重试最多30次，每次 `osDelay(1000)`（1秒）。但 `connection_create()` 的失败条件是 `getaddrinfo` 或 `connect` 失败（网络层），**不包含 DTLS 握手结果**。因此即使 DTLS 握手有问题，`connection_create()` 仍可能返回非 NULL，重试机制无法解决 DTLS 层面的问题。

**分析4：超时路径**

Register 事务超时后，`prv_handleRegistrationReply()` 中 `packet==NULL`（未收到服务器响应），走 else 分支（`registration.c:232`）：
```c
else mContextP->notifyCallback(LWM2M_NOTIFY_TYPE_REG, LWM2M_NOTIFY_CODE_TIMEOUT, mContextP->clientId);
```
回调 `lwm2mNotifyCallback()` 设置 `isQuit=2`，主循环中映射为 `LWM2M_REG_TIMEOUT`，最终输出 `+LWM2M ERROR: REGISTER TIMEOUT`。

### 2.4 可能的根因方向（需进一步验证）

| 优先级 | 可能原因 | 验证方法 |
|--------|----------|----------|
| **P0** | **服务器端 PSK 凭据与客户端不匹配**：pskId="123"、psk="313332" 与 Leshan 服务器配置不一致 | 检查 Leshan 服务器安全配置，确认 PSK Identity 和 Key 是否匹配 |
| **P1** | **DTLS 版本/密码套件不兼容**：TinyDTLS 与服务器支持的 DTLS 版本或密码套件不匹配 | 抓包查看 DTLS ClientHello，对比服务器支持的套件 |
| **P2** | **网络/防火墙阻断 DTLS**：端口5684被防火墙阻断或网络路径不支持 | 使用其他 DTLS 客户端测试同服务器同端口 |
| **P3** | **TinyDTLS 库编译问题**：`WITH_TINYDTLS` 宏未正确定义，导致使用了非加密的 `connection.c` 而非 `dtlsconnection.c` | 确认编译配置中 `WITH_TINYDTLS` 是否启用 |

**最可能根因（P0）**：PSK 凭据不匹配。Leshan 服务器需要预先配置客户端的 PSK Identity 和 Key，若客户端传入的 `pskId=123, psk=313332` 未在服务器端注册，DTLS 握手将在 ServerHello 阶段被拒绝，客户端收不到响应，最终超时。

### 2.5 问题复现路径

| 项目 | 内容 |
|------|------|
| **触发条件** | 使用 AT+LWM2MCREATE 带 pskId/psk 参数连接 Leshan 服务器 5684 端口 |
| **必要状态** | 网络已附着，非加密连接正常 |
| **操作步骤** | 1. AT+LWM2MCREATE="leshan.eclipseprojects.io",5684,56830,"nwy-lient",60,123,313332 2. 等待约64秒 3. 收到 +LWM2M ERROR: REGISTER TIMEOUT |
| **复现概率** | 100%（每次加密连接均超时） |

## 3. 相关文件

- `PLAT/middleware/eigencomm/at/atcust/src/atec_lwm2m.c` — AT 命令解析，PSK 参数处理（第99-154行）
- `PLAT/middleware/eigencomm/at/atentity/src/at_lwm2m_task.c` — lwm2mCreate()、lwm2mConnectServer()、lwm2mMainLoop()（第1030-1670行）
- `PLAT/middleware/thirdparty/wakaama/examples/shared/dtlsconnection.c` — DTLS 连接创建、PSK 回调、DTLS 握手（第185-554行）
- `PLAT/middleware/thirdparty/wakaama/examples/client/object_security.c` — Security Object 创建，securityMode 设置（第738-800行）
- `PLAT/middleware/thirdparty/wakaama_core/registration.c` — 注册流程、超时回调（第191-236行、第302-326行）
- `PLAT/middleware/eigencomm/at/atreply/src/atc_reply.c` — 错误码映射 LWM2M_REG_TIMEOUT（第1659-1660行）