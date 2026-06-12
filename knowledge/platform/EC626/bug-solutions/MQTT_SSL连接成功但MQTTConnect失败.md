# MQTT SSL连接成功但MQTT Connect失败 原因分析

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **平台** | EC626 |
| **模块** | MQTT |
| **问题分类** | 状态机异常 |
| **症状关键词** | SSL连接成功, MQTTConnect失败, CONNACK未等待, 连接立即关闭 |
| **根因概述** | mqttConnectWithResults()函数中等待CONNACK的代码被#if 0注释掉，导致发送CONNECT后立即返回且返回值被误判为失败，触发连接关闭逻辑 |
| **调用链摘要** | mqttCONN() → mqttSendTask() → mqttConnectClient() → mqttConnect() → mqttConnectWithResults() → 跳过CONNACK → lwip_close |
| **检索关键词** | MQTT Connect失败, CONNACK, #if 0, SSL连接成功, mqttConnectWithResults, 返回值错误, MQTT_CREATE_CLIENT_ERROR, 连接关闭 |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 解决方案](#3-解决方案)
- [4. 验证步骤](#4-验证步骤)
- [5. 相关文件](#5-相关文件)

---

## 1. 问题描述

用户报告在测试MQTT SSL连接时，SSL连接成功但MQTT Connect失败。AT命令日志显示：
- `AT+ECMTOPEN` 返回成功 `+ECMTOPEN: 0,0`
- `AT+ECMTCONN` 返回错误 `+ECMTCONN: 0,2,2`

**问题类型**：单日志分析

## 2. 根本原因

**MQTT Connect发送后立即关闭TCP连接，导致连接失败**。核心问题是 `mqttConnectWithResults()` 函数中等待CONNACK的代码被注释掉（#if 0），导致发送CONNECT后立即返回，且后续有逻辑错误导致连接被关闭。

### 2.1 关键日志证据

#### AT日志关键片段

```
[2026-03-12_14:15:07:767]AT+ECMTOPEN=0,"219.144.245.178",13013
[2026-03-12_14:15:07:767]OK
[2026-03-12_14:15:14:772]+ECMTOPEN: 0,0  // TCP+SSL连接成功
[2026-03-12_14:15:19:794]AT+ECMTCONN=0,"ci_mqtt_001","neoway","neoway"
[2026-03-12_14:15:19:794]OK
[2026-03-12_14:15:26:349]+ECMTCONN: 0,2,2  // MQTT连接失败
[2026-03-12_14:15:26:479]+ECMTCONN: 0,0,0  // 第二次尝试返回
```

#### 模块日志关键片段

```
// SSL连接成功
mbedtls_ssl_handshake_client_step return -0x0
caCert varification ok
...mqtt ssl connect ok!!!...
.....open mqtt client ok.......

// MQTT CONNECT发送
ATCMD , decode AT: AT+ECMTCONN=0,"ci_mqtt_001","neoway","neoway"
.....start connect mqtt client.......
...mqttSendPacket..0.                    // 开始发送
70 bytes data has sent to server         // 发送70字节成功
ssl->f_send return:0x46                  // SSL发送返回70字节
...mqttSendPacket.. = 200.               // 发送成功(MQTT_OK=200)

// 关键问题：发送后立即关闭连接
lwip_close ( 0 )
tcp_close: closing in
.....connect mqtt client fail.......
...mqtt connect fail!!!...
```

### 2.2 代码调用链

| 信息 | 值 |
|------|-----|
| **入口函数** | `mqttCONN()` - [atec_mqtt.c:762](middleware/eigencomm/at/atcust/src/atec_mqtt.c#L762) |
| **调用链** | `mqttCONN() → mqttSendTask() → mqttConnectClient() → mqttConnect() → mqttConnectWithResults()` |
| **问题位置** | `mqttConnectWithResults()` [at_mqtt_task.c:377](middleware/eigencomm/at/atentity/src/at_mqtt_task.c#L377) |

**调用链分析**：

1. `mqttConnectWithResults()` 发送MQTT CONNECT包
2. 等待CONNACK的代码被注释掉（#if 0 ... #endif），导致直接返回
3. 但返回值被误判为失败，触发连接关闭逻辑

### 2.3 问题分析

#### 时序分析

| 时间 | 事件 | 说明 |
|------|------|------|
| 14:15:07 | ECMTOPEN命令 | 开始TCP连接 |
| 14:15:14 | SSL握手完成 | SSL连接成功 |
| 14:15:19 | ECMTCONN命令 | 开始MQTT连接 |
| 14:15:26.319 | 发送CONNECT包 | 70字节发送成功 |
| 14:15:26.319 | **立即关闭连接** | lwip_close(0) |
| 14:15:26.329 | 报告连接失败 | +ECMTCONN: 0,2,2 |

#### 错误码含义

| 错误码 | 含义 |
|--------|------|
| `+ECMTCONN: 0,2,2` | tcpId=0, ret=2(MQTT_CREATE_CLIENT_ERROR), conn_ret_code=2 |
| `ret = -0x6800` | MQTT读取超时错误（socket已关闭） |

#### 代码问题

关键代码在 [at_mqtt_task.c:377-429](middleware/eigencomm/at/atentity/src/at_mqtt_task.c#L377-L429)：

```c
int mqttConnectWithResults(mqtt_context *mqttContext, MQTTPacket_connectData* options, MQTTConnackData* data)
{
    // ... 省略 ...

    if ((len = MQTTSerialize_connect(...)) <= 0)
        goto exit;
    if ((rc = mqttSendPacket(...)) != SUCCESS)  // 发送CONNECT包
        goto exit;

    #if 0  // ⚠️ 关键问题：等待CONNACK的代码被注释掉！
    // this will be a blocking call, wait for the connack
    if (mqttWaitForAck(mqttContext, CONNACK, &connect_timer) == CONNACK)
    {
        data->rc = 0;
        data->sessionPresent = 0;
        if (MQTTDeserialize_connack(...) == 1)
            rc = data->rc;
        else
            rc = FAILURE;
    }
    else
        rc = FAILURE;
    #endif

exit:
    if (rc == SUCCESS)
    {
        mqttContext->mqtt_client->isconnected = 1;
        mqttContext->mqtt_client->ping_outstanding = 0;
    }
    // ...
    return rc;  // 返回rc，但此时rc的值不确定
}
```

**问题点**：
1. 等待CONNACK的代码被 `#if 0` 注释掉
2. `mqttSendPacket()` 返回 200 (MQTT_OK)，但 `rc` 变量可能未被正确赋值
3. 函数返回非0值，被 `mqttConnectClient()` 判断为失败

## 3. 解决方案

### 3.1 方案1: 恢复CONNACK等待逻辑（推荐）

**修改内容**：将 `#if 0` 改为 `#if 1`，恢复等待CONNACK的逻辑

**修改位置**：[at_mqtt_task.c:403-416](middleware/eigencomm/at/atentity/src/at_mqtt_task.c#L403-L416)

```c
#if 1  // 恢复等待CONNACK逻辑
// this will be a blocking call, wait for the connack
if (mqttWaitForAck(mqttContext, CONNACK, &connect_timer) == CONNACK)
{
    data->rc = 0;
    data->sessionPresent = 0;
    if (MQTTDeserialize_connack(&data->sessionPresent, &data->rc, mqttContext->mqtt_client->readbuf, mqttContext->mqtt_client->readbuf_size) == 1)
        rc = data->rc;
    else
        rc = FAILURE;
}
else
    rc = FAILURE;
#endif
```

**影响范围**：MQTT连接流程，会增加等待CONNACK的超时时间

**实施难度**：低

### 3.2 方案2: 修复返回值处理

**修改内容**：确保 `mqttSendPacket()` 成功后正确设置 `rc` 值

```c
if ((rc = mqttSendPacket(mqttContext, len, &connect_timer, 0, false)) != SUCCESS)
    goto exit;

// 如果不等待CONNACK，发送成功即认为连接成功
rc = SUCCESS;  // 显式设置成功

#if 0
// ... 等待CONNACK逻辑 ...
#endif
```

**影响范围**：MQTT连接流程

**实施难度**：低

### 3.3 方案3: 检查异步接收机制

**分析**：根据代码注释和架构，MQTT模块可能设计为异步接收模式：
- 发送CONNECT后不阻塞等待CONNACK
- 由 `mqtt_recv_task` 异步接收CONNACK

**需要检查**：
1. `mqtt_recv_task` 是否正确启动
2. CONNACK接收后是否正确更新连接状态
3. 异步模式下如何判断连接成功

**影响范围**：需要深入分析异步接收机制

**实施难度**：中

## 4. 验证步骤

1. **确认问题**：在 `mqttConnectWithResults()` 中添加调试日志，打印 `rc` 返回值
2. **验证方案1**：启用 `#if 1` 后测试MQTT SSL连接
3. **验证方案2**：显式设置 `rc = SUCCESS` 后测试
4. **监控日志**：观察是否正确等待并接收CONNACK
5. **功能测试**：测试订阅、发布等后续MQTT功能

## 5. 相关文件

- [at_mqtt_task.c](middleware/eigencomm/at/atentity/src/at_mqtt_task.c) - MQTT任务核心实现
- [at_mqtt_task.h](middleware/eigencomm/at/atentity/inc/at_mqtt_task.h) - MQTT任务头文件
- [atec_mqtt.c](middleware/eigencomm/at/atcust/src/atec_mqtt.c) - AT命令处理
- [MQTTClient.c](middleware/thirdparty/mqtt/MQTTClient-C/src/MQTTClient.c) - Paho客户端实现
- [MQTTTls.c](middleware/thirdparty/mqtt/MQTTClient-C/src/eigencomm/MQTTTls.c) - TLS传输适配