# MQTT SSL双向认证连接失败原因分析

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **工作项 ID** | NA |
| **平台** | EC626 |
| **模块** | MQTT/SSL |
| **问题分类** | 状态机异常 |
| **症状关键词** | ECMTCONN失败, SSL上下文丢失, 重复URC, 订阅ERROR |
| **根因概述** | ECMTOPEN阶段建立的SSL连接状态未正确传递到ECMTCONN阶段，导致ECMTCONN重新建立TCP连接时丢失SSL上下文，MQTT报文可能以明文发送到SSL端口被服务器拒绝 |
| **调用链摘要** | mqttCONN() → mqtt_client_connect() → mqtt_send_task() → mqttConnectSocket() → 重建TCP(SSL丢失) |
| **检索关键词** | MQTT SSL, 双向认证, ECMTCONN失败, SSL上下文丢失, ECMTOPEN, mbedTLS, 状态传递缺陷, MQTT连接失败 |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 解决方案](#3-解决方案)
- [4. 验证步骤](#4-验证步骤)
- [5. 相关文件](#5-相关文件)

---

## 1. 问题描述

用户配置MQTT SSL双向认证后，执行 `AT+ECMTCONN` 命令时连接失败，表现为：
- `AT+ECMTOPEN` 成功返回 `+ECMTOPEN: 0,0`（TCP+SSL连接建立成功）
- `AT+ECMTCONN` 先返回 `+ECMTCONN: 0,2,2`（失败），紧接着又返回 `+ECMTCONN: 0,0,0`（成功）
- 后续 `AT+ECMTSUB` 返回 `ERROR`

**问题类型**：单日志分析

**AT命令执行序列**：
```
AT+ECMTCFG="ssl",2,"ca_cert.pem","client.crt2.pem","ck.pem"
OK
AT+ECMTOPEN=0,"219.144.245.178",13013
OK
+ECMTOPEN: 0,0                                    ← ECMTOPEN成功
ATE1
OK
AT+ECMTCONN=0,"ci_mqtt_001","neoway","neoway"
OK
+ECMTCONN: 0,2,2                                  ← 先返回失败
+ECMTCONN: 0,0,0                                  ← 又返回成功（异常）
AT+ECMTSUB=0,1,test,2
ERROR                                              ← 订阅失败
```

---

## 2. 根本原因

**SSL连接状态在ECMTOPEN和ECMTCONN阶段之间未正确传递**，导致ECMTCONN阶段重新建立TCP连接时丢失了SSL上下文，MQTT报文可能以明文形式发送到SSL端口，服务器拒绝连接。

### 2.1 关键日志证据

#### AT日志关键片段

```
[2026-03-11_11:33:59.299] AT+ECMTCFG="ssl",2,"ca_cert.pem","client.crt2.pem","ck.pem"
[2026-03-11_11:33:59.302] OK

[2026-03-11_11:34:03.503] AT+ECMTOPEN=0,"219.144.245.178",13013
[2026-03-11_11:34:09.765] +ECMTOPEN: 0,0   ← TCP+SSL连接成功

[2026-03-11_11:34:18.003] AT+ECMTCONN=0,"ci_mqtt_001","neoway","neoway"
[2026-03-11_11:34:18.004] OK
[2026-03-11_11:34:18.318] +ECMTCONN: 0,2,2   ← 先返回失败
[2026-03-11_11:34:18.320] +ECMTCONN: 0,0,0   ← 又返回成功（异常）
```

#### 模块AP日志关键片段

**ECMTOPEN阶段 - SSL握手成功**：
```
STEP 0 . Loading the CA root certificate ...
ok ( 0 skipped )
STEP 0 . start prepare client cert ...
STEP 0 . start mbedtls_pk_parse_key
STEP 1 . Connecting to / / ...
ok
STEP 2 . Setting up the SSL / TLS structure...
ok
STEP 3 . Performing the SSL / TLS handshake...
=> handshake
client hello , max version: [ 3:3 ]
<= write client hello
..... => ssl_parse_certificate_request
..... => ssl_parse_server_hello_done
<= handshake
ok
STEP 4 . Verifying peer X.509 certificate..
caCert varification ok
AT CMD , URC: +ECMTOPEN: 0,0
```

**ECMTCONN阶段 - 重新建立TCP连接（关键问题）**：
```
ATCMD , decode AT: AT+ECMTCONN=0,"ci_mqtt_001","neoway","neoway"
...
mqttConnectSocket connect is ongoing        ← ⚠️ 重新建立TCP连接！
errno = 115 ( EINPROGRESS ) connect success in time ( 10 s )
...
AT CMD , URC: +ECMTCONN: 0,2,2              ← 失败
AT CMD , URC: +ECMTCONN: 0,0,0              ← 成功（异常）
...
get_socket ( 0 ) : not active               ← Socket已不活跃
tcp FIN WAIT2 timer has deactive            ← TCP连接关闭
netconn_recv_data: tcp rcv buff null        ← 接收缓冲区为空
```

### 2.2 代码调用链

| 信息 | 值 |
|------|-----|
| **入口函数** | `mqttCONN()` @ [atec_mqtt.c](middleware/eigencomm/at/atcust/src/atec_mqtt.c) |
| **调用链** | `mqttCONN()` → `mqtt_client_connect()` → `mqtt_send_task` → `MQTT_MSG_CONNECT` |
| **问题位置** | `MQTTFreeRTOS.c:261` - `mqttConnectSocket connect is ongoing` |

**调用链分析**：

```
AT+ECMTCONN=0,"ci_mqtt_001","neoway","neoway"
    ↓
mqttCONN() [atec_mqtt.c]
    ↓ 解析参数
mqtt_client_connect() [at_mqtt_task.c]
    ↓ 发送 MQTT_MSG_CONNECT 消息
mqtt_send_task()
    ↓ 处理 MQTT_MSG_CONNECT
    ↓
NetworkConnect() 或相关函数
    ↓ ⚠️ 问题：重新建立TCP连接
    ↓
mqttConnectSocket() [MQTTFreeRTOS.c:261]
    ↓ 输出 "connect is ongoing"
    ↓ SSL上下文未传递
    ↓
发送MQTT CONNECT报文（可能未加密）
    ↓
服务器拒绝连接
```

### 2.3 问题分析

#### 时序分析

| 时间点 | 事件 | 说明 |
|--------|------|------|
| 11:33:59 | SSL配置 | `AT+ECMTCFG="ssl",2,...` 成功 |
| 11:34:03 | ECMTOPEN | 开始建立TCP+SSL连接 |
| 11:34:04 | SSL握手 | Client Hello → Server Hello → Certificate → CertificateRequest |
| 11:34:09 | SSL完成 | 握手成功，证书验证通过，`+ECMTOPEN: 0,0` |
| 11:34:18 | ECMTCONN | **重新建立TCP连接**（问题发生） |
| 11:34:18.318 | 响应1 | `+ECMTCONN: 0,2,2` 失败 |
| 11:34:18.320 | 响应2 | `+ECMTCONN: 0,0,0` 成功（异常） |
| 11:34:18.431 | 连接关闭 | `get_socket ( 0 ) : not active` |

#### 5-Why分析

```
Why 1: 为什么ECMTCONN返回失败后又返回成功？
  → 存在竞态条件，导致重复发送URC

Why 2: 为什么会出现竞态条件？
  → ECMTCONN阶段重新建立了TCP连接

Why 3: 为什么在ECMTCONN阶段会重新建立TCP连接？
  → ECMTOPEN建立的SSL连接状态未正确传递到ECMTCONN阶段

Why 4: 为什么SSL连接状态未传递？
  → SSL上下文存储在mqtts_client中，但ECMTCONN阶段的连接流程未复用该上下文

Why 5: 为什么未复用SSL上下文？
  → 根本原因：ECMTOPEN和ECMTCONN之间的状态传递存在缺陷
```

#### 错误码含义

| 错误码 | 含义 |
|--------|------|
| `+ECMTCONN: 0,2,2` | result=2（连接失败），connCode=2（MQTT拒绝：标识符不正确） |
| `+ECMTCONN: 0,0,0` | result=0（成功），connCode=0（连接接受） |

### 2.4 对比分析

| 对比维度 | ECMTOPEN阶段 | ECMTCONN阶段 |
|----------|--------------|--------------|
| TCP连接 | 新建成功 | **重新建立** |
| SSL状态 | 完整握手 | **丢失/未复用** |
| 数据传输 | SSL加密 | 可能明文 |
| 服务器响应 | 正常 | 拒绝后接受 |

---

## 3. 解决方案

### 3.1 方案1: 复用SSL连接（推荐）

**修改内容**：
1. 在 `mqtt_client_connect()` 函数中检查是否已存在SSL连接
2. 如果ECMTOPEN阶段已建立SSL连接，ECMTCONN阶段应直接复用，不应重新建立TCP连接
3. 修改 `MQTTFreeRTOS.c` 中的连接函数，增加SSL状态检查

**关键修改位置**：
- `middleware/thirdparty/mqtt/MQTTClient-C/src/FreeRTOS/MQTTFreeRTOS.c:256`
- `middleware/eigencomm/at/atentity/src/at_mqtt_task.c` - MQTT_MSG_CONNECT处理

**代码修改示例**：
```c
// 在 MQTTFreeRTOS.c 的相关连接函数中
#ifdef FEATURE_MQTT_TLS_ENABLE
    // 检查SSL连接是否已建立
    if (mqttContext->mqtts_client != NULL &&
        mqttContext->mqtts_client->ssl != NULL)
    {
        // SSL连接已建立，直接返回成功，不重新建立TCP连接
        ECOMM_TRACE(UNILOG_MQTT, mqtt_ssl_already_connected, P_INFO, 0,
                    "SSL connection already established, skip reconnect");
        return 0;
    }
#endif
```

**影响范围**：MQTT TLS连接逻辑

**实施难度**：中

### 3.2 方案2: 检查连接状态判断

**修改内容**：
在ECMTCONN阶段开始时，检查 `is_connected` 状态是否为 `MQTT_CONN_OPENED`（表示TCP+SSL已建立），如果是则跳过TCP连接步骤。

**修改位置**：
- `middleware/eigencomm/at/atentity/src/at_mqtt_task.c` - `MQTT_MSG_CONNECT` case

**代码修改示例**：
```c
case MQTT_MSG_CONNECT:
    // 检查是否已有SSL连接（ECMTOPEN已建立）
    if (mqttNewContext->is_connected == MQTT_CONN_OPENED &&
        mqttNewContext->mqtts_client != NULL &&
        mqttNewContext->mqtts_client->ssl != NULL)
    {
        ECOMM_TRACE(UNILOG_MQTT, mqtt_reuse_ssl_conn, P_INFO, 0,
                    "Reusing existing SSL connection");
        // 跳过TCP连接步骤，直接进行MQTT连接
    }
    else
    {
        // 正常流程...
    }
```

**影响范围**：MQTT连接状态机

**实施难度**：低

### 3.3 方案3: 增加SSL连接有效性检查

**修改内容**：
在ECMTCONN阶段发送MQTT CONNECT报文前，验证SSL连接是否有效：
1. 检查 `mqtts_client->ssl` 是否为NULL
2. 检查 `mqtts_client->ssl->sslContext.state` 是否为 `MBEDTLS_SSL_HANDSHAKE_OVER`

**影响范围**：SSL连接验证逻辑

**实施难度**：低

---

## 4. 验证步骤

1. **验证SSL配置正确性**
   ```
   AT+ECMTCFG="ssl",2,"ca_cert.pem","client_cert.pem","client_key.pem"
   ```
   确认返回OK

2. **验证ECMTOPEN成功**
   ```
   AT+ECMTOPEN=0,"server_ip",8883
   ```
   确认返回 `+ECMTOPEN: 0,0`

3. **验证ECMTCONN不再重复建立连接**
   - 添加日志观察ECMTCONN阶段是否还出现 `mqttConnectSocket connect is ongoing`
   - 确认只返回一次 `+ECMTCONN: 0,0,0`

4. **验证MQTT订阅功能**
   ```
   AT+ECMTSUB=0,1,"test",2
   ```
   确认返回 `+ECMTSUB: 0,1,0,2` 而不是 ERROR

---

## 5. 相关文件

| 文件路径 | 说明 |
|----------|------|
| [middleware/eigencomm/at/atentity/src/at_mqtt_task.c](middleware/eigencomm/at/atentity/src/at_mqtt_task.c) | MQTT任务核心实现，MQTT_MSG_CONNECT处理 |
| [middleware/thirdparty/mqtt/MQTTClient-C/src/FreeRTOS/MQTTFreeRTOS.c](middleware/thirdparty/mqtt/MQTTClient-C/src/FreeRTOS/MQTTFreeRTOS.c) | TCP连接实现，`mqttConnectSocket` |
| [middleware/thirdparty/mqtt/MQTTClient-C/src/eigencomm/MQTTTls.c](middleware/thirdparty/mqtt/MQTTClient-C/src/eigencomm/MQTTTls.c) | SSL连接实现，`mqttSslConn()` |
| [middleware/eigencomm/at/atcust/src/atec_mqtt.c](middleware/eigencomm/at/atcust/src/atec_mqtt.c) | AT命令处理入口 |
| [middleware/eigencomm/at/atentity/inc/at_mqtt_task.h](middleware/eigencomm/at/atentity/inc/at_mqtt_task.h) | MQTT上下文和状态定义 |

---

## 附录：问题诊断补充

### A. 正常流程 vs 异常流程

**正常流程**（预期行为）：
```
ECMTOPEN: 建立TCP + SSL握手 → 存储SSL上下文
ECMTCONN: 复用SSL连接 → 发送MQTT CONNECT → 接收CONNACK
```

**异常流程**（当前行为）：
```
ECMTOPEN: 建立TCP + SSL握手 → 存储SSL上下文
ECMTCONN: 重新建立TCP连接（SSL状态丢失）→ 发送数据（可能明文）→ 服务器拒绝
```

### B. 关键日志标记

| 日志关键词 | 出现阶段 | 含义 |
|------------|----------|------|
| `STEP 3 . Performing the SSL/TLS handshake` | ECMTOPEN | SSL握手开始 |
| `caCert varification ok` | ECMTOPEN | 服务器证书验证通过 |
| `mqttConnectSocket connect is ongoing` | ECMTCONN | **异常：重新建立TCP连接** |
| `get_socket ( 0 ) : not active` | ECMTCONN后 | Socket已关闭 |

---

*报告生成时间: 2026-03-11*
*基于日志: mqtttls-ssl连接失败.txt, mqtt-tls失败atlog.txt*
*分析工具: spec-bug-analyzer*