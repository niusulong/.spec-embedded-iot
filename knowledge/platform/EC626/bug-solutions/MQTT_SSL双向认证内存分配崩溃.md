# MQTT SSL双向认证连接崩溃分析报告

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **平台** | EC626 |
| **模块** | MQTT |
| **问题分类** | 资源耗尽 |
| **症状关键词** | Assert崩溃, SSL双向认证, 内存不足, ECC运算, mqttSend任务 |
| **根因概述** | SSL双向认证握手过程中ECC椭圆曲线密码运算需要大量动态内存，系统剩余内存仅约1KB，pvPortMallocEC分配失败时使用configASSERT导致系统崩溃而非返回NULL让上层处理 |
| **调用链摘要** | mbedtls_ssl_handshake → ecp_normalize_jac_many → mbedtls_mpi_mul_mpi → mbedtls_mpi_grow → calloc → pvPortMallocEC → configASSERT |
| **检索关键词** | MQTT, SSL, 双向认证, 内存不足, OOM, Assert, ECC, mbedtls, pvPortMallocEC, 崩溃 |

---

## 一、问题描述

| 项目 | 内容 |
|------|------|
| **问题类型** | 系统崩溃（Assert） |
| **崩溃任务** | mqttSend |
| **发生时间** | SSL/TLS 双向认证握手阶段 |
| **故障现象** | 发送客户端证书后，执行 ECC 大数运算时触发内存分配断言崩溃 |
| **日志文件** | mqtttls-ssl连接失败.txt |

## 二、关键事件时间线

| 时间 | 事件 | 状态 |
|------|------|------|
| 16:06:59.590 | 网络附着成功，获得IP: 10.108.188.179 | ✓ |
| 16:07:08.557 | `AT+ECMTOPEN=0,"219.144.245.178",13013` | MQTT连接命令 |
| 16:07:08.713 | 加载CA根证书成功 | ✓ |
| 16:07:08.884 | TCP连接开始 | ✓ |
| 16:07:09.355 | TCP连接建立成功 | ✓ |
| 16:07:09.362 | SSL/TLS握手开始 | ✓ |
| 16:07:09.369 | 发送Client Hello | ✓ |
| 16:07:10.224 | 收到Server Hello + Server Certificate | ✓ |
| 16:07:10.837 | 解析CertificateRequest + ServerHelloDone | ✓ |
| 16:07:11.352 | 发送客户端证书 | ✓ |
| **16:07:11.638** | **Assert崩溃！** | ✗ |

## 三、Dump日志分析

### 3.1 崩溃现场寄存器

```
Assert occur in task context, task name: mqttSend
exception pc: 0x87ae54
lr: 0x87ce2d
psr: 0x40000000
```

### 3.2 堆栈帧

```
dump stack frame: 0x0  0x0      0x1    0x0
dump stack frame: 0x88 0x87ce2d 0x2710 0x0
dump stack frame: 0x413f0 0x8dacd1 0x0 0x11
dump stack frame: 0x3e7ac 0x413e4 0x413f0 0x8db5f7
dump stack frame: 0x1  0x0      0x0    0x1
dump stack frame: 0x0  0x0      0x3b258 0x0
dump stack frame: 0x35460 0x3b2dc 0x3b254 0x412d0
dump stack frame: 0x1f 0x8d60e9 0x1    0x1
```

## 四、调用栈解析

通过 map 文件解析地址到函数映射：

| 层级 | 地址 | 函数 | 说明 |
|------|------|------|------|
| 1 | **0x87ae54** | `pvPortMallocEC` | **崩溃点** - 内存分配断言失败 |
| 2 | 0x87ce2d | `calloc` | 标准库内存分配函数 |
| 3 | 0x8dacd1 | `mbedtls_mpi_grow` | 大数运算内存扩展 |
| 4 | 0x8db5f7 | `mbedtls_mpi_mul_mpi` / `mbedtls_mpi_add_int` | 大数运算 |
| 5 | 0x8d60e9 | `ecp_normalize_jac_many` | ECC 雅可比坐标归一化 |
| 6 | ... | `mbedtls_ssl_handshake` | SSL 握手流程 |

### 调用链（从底到顶）

```
mbedtls_ssl_handshake (SSL握手主函数)
└── mbedtls_ssl_handshake_client_step (客户端握手步骤)
    └── ecp_normalize_jac_many (ECC 雅可比坐标归一化)
        └── mbedtls_mpi_mul_mpi (大数乘法)
            └── mbedtls_mpi_grow (大数内存扩展)
                └── mbedtls_calloc → calloc
                    └── pvPortMallocEC
                        └── configASSERT(pvReturn != 0) ← 断言失败崩溃！
```

## 五、根本原因分析（5-Why）

```
Why 1: 为什么会在mqttSend任务中发生Assert崩溃？
  → 因为 pvPortMallocEC 函数中的 configASSERT(pvReturn != 0) 断言失败

Why 2: 为什么内存分配会返回 NULL？
  → 因为 mbedtls_mpi_grow 请求分配大数运算内存时，系统内存不足

Why 3: 为什么系统内存不足？
  → SSL 双向认证握手过程中，ECC 椭圆曲线密码运算需要大量动态内存
     崩溃前日志显示 "CEUP BM, left size is: 1000" 仅剩约 1KB

Why 4: 为什么 ECC 运算需要这么多内存？
  → SSL 双向认证发送客户端证书后，服务器验证客户端证书需要执行
     ECC 签名验证运算（ecp_normalize_jac_many），涉及大数乘法

Why 5: 为什么内存分配失败会导致系统崩溃而非优雅降级？
  → 因为 pvPortMallocEC 使用 configASSERT 处理分配失败，
     而非返回 NULL 让上层代码处理错误
```

**根本原因**：`pvPortMallocEC` 函数在内存分配失败时使用 `configASSERT` 导致系统崩溃，而非返回错误码让上层处理。

## 六、关键代码分析

### 6.1 崩溃代码位置

**文件**: [os/freertos/src/heap_6.c](os/freertos/src/heap_6.c#L97-L104)

```c
void *pvPortMallocEC( size_t xWantedSize, unsigned int funcPtr )
{
    void *pvReturn = pvPortMallocNoAssertEC(xWantedSize, funcPtr);

    configASSERT( pvReturn != 0 );  // ← 问题代码：分配失败直接崩溃！

    return pvReturn;
}
```

### 6.2 内存分配限制

**文件**: [os/freertos/src/heap_6.c](os/freertos/src/heap_6.c#L63)

```c
configASSERT((xWantedSize > 0 && xWantedSize < 0x10000) && "zero or 64K+ alloc is prohibited!");
```

限制单次分配不能超过 64KB。

### 6.3 大数内存分配

**文件**: [middleware/thirdparty/mbedtls/library/bignum.c](middleware/thirdparty/mbedtls/library/bignum.c#L114-L129)

```c
int mbedtls_mpi_grow( mbedtls_mpi *X, size_t nblimbs )
{
    mbedtls_mpi_uint *p;

    if( nblimbs > MBEDTLS_MPI_MAX_LIMBS )
        return( MBEDTLS_ERR_MPI_ALLOC_FAILED );

    if( X->n < nblimbs )
    {
        if( ( p = (mbedtls_mpi_uint*)mbedtls_calloc( nblimbs, ciL ) ) == NULL )
            return( MBEDTLS_ERR_MPI_ALLOC_FAILED );  // ← 上层可处理此错误
        ...
    }
}
```

mbedTLS 层面正确处理了分配失败，但 `calloc` 内部调用 `pvPortMallocEC` 时崩溃。

## 七、解决方案

### 方案1：修改内存分配错误处理（推荐）

**修改文件**: `os/freertos/src/heap_6.c`

```c
// 修改前（崩溃原因）
void *pvPortMallocEC( size_t xWantedSize, unsigned int funcPtr )
{
    void *pvReturn = pvPortMallocNoAssertEC(xWantedSize, funcPtr);
    configASSERT( pvReturn != 0 );  // 分配失败直接崩溃
    return pvReturn;
}

// 修改后（允许返回 NULL）
void *pvPortMallocEC( size_t xWantedSize, unsigned int funcPtr )
{
    void *pvReturn = pvPortMallocNoAssertEC(xWantedSize, funcPtr);
    // 移除断言，改为日志记录
    if (pvReturn == NULL) {
        configPRINT_STRING("Warning: pvPortMallocEC failed!\r\n");
    }
    return pvReturn;
}
```

**影响评估**：
- ✅ SSL 握手失败时返回错误码，不会导致系统崩溃
- ✅ mbedTLS 会返回 `MBEDTLS_ERR_MPI_ALLOC_FAILED` 错误
- ⚠️ 需要确保所有调用 `pvPortMallocEC` 的代码都检查返回值

### 方案2：增加堆内存大小

检查 FreeRTOS 堆内存配置，增加可用堆空间：

- 检查 `configTOTAL_HEAP_SIZE` 配置
- 评估 SSL 双向认证场景下的内存峰值需求
- 预留足够的内存余量

### 方案3：SSL 内存优化

- 使用 `MBEDTLS_SSL_MAX_FRAGMENT_LENGTH` 限制 SSL 记录大小
- 考虑使用 ECC 固定大小内存池
- 优化证书大小

## 八、验证方法

1. **修改验证**：修改 `pvPortMallocEC` 后，SSL 握手应返回错误码而非崩溃
2. **内存监控**：在 SSL 握手过程中打印内存使用情况
3. **压力测试**：测试不同证书大小、不同密钥长度下的内存消耗
4. **回归测试**：确保修改不影响其他内存分配场景

## 九、相关文件

| 文件 | 说明 |
|------|------|
| `os/freertos/src/heap_6.c` | FreeRTOS 堆管理，崩溃点 |
| `middleware/thirdparty/mbedtls/library/bignum.c` | mbedTLS 大数运算 |
| `middleware/thirdparty/mbedtls/library/ecp.c` | mbedTLS ECC 运算 |
| `middleware/thirdparty/mqtt/MQTTClient-C/src/eigencomm/MQTTTls.c` | MQTT SSL 连接 |
| `middleware/eigencomm/at/atentity/src/at_mqtt_task.c` | MQTT 任务 |

---

**分析日期**: 2026-03-10
**分析工具**: spec-bug-analyzer