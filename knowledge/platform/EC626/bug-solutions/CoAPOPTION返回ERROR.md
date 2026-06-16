# AT+COAPOPTION 部分选项值返回 ERROR 原因分析

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **工作项 ID** | NA |
| **平台** | EC626 |
| **模块** | CoAP |
| **问题分类** | 参数错误 |
| **症状关键词** | COAPOPTION ERROR, Uri-Port, strlen校验, 端口号被拒绝, 选项值校验 |
| **根因概述** | coap_config_client函数中COAP_OPTION_URI_PORT分支使用strlen校验十进制字符串长度而非数值范围，导致三位数及以上合法端口号被错误拒绝 |
| **调用链摘要** | coapOPTION → coap_client_option → coap_task_send_process → coap_config_client → strlen校验(BUG) |
| **检索关键词** | COAPOPTION, Uri-Port, strlen校验, 参数错误, option value, CoAP选项, 端口号ERROR, at_coap_task |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 相关文件](#3-相关文件)

---

## 1. 问题描述

测试 CoAP 功能指令时，`AT+COAPOPTION` 命令在设置 Uri-Port 选项（option name=7）时，部分端口号值返回 ERROR。具体表现为：值为 "99" 时返回 OK，但值为 "100" 或 "221" 时返回 ERROR。

**问题类型**：单日志分析

**AT命令执行结果汇总**：

| 序号 | AT命令 | 结果 | 时间 |
|------|--------|------|------|
| 1 | `AT+COAPOPTION=3,3,"180.101.147.115",7,"99",11,"ea"` | ✅ OK | 15:40:06.763 |
| 2 | `AT+COAPOPTION=3,3,"180.101.147.115",7,"100",11,"ea"` | ❌ ERROR | 15:40:09.267 |
| 3 | `AT+COAPOPTION=3,3,"180.101.147.115",7,"221",11,"ea"` | ❌ ERROR | 15:40:11.539 |
| 4 | `AT+COAPOPTION=2,3,"180.101.147.115",11,"ea"` | ✅ OK | 15:40:14.054 |

---

## 2. 根本原因

**`coap_config_client()` 函数中 `COAP_OPTION_URI_PORT` 分支的输入校验逻辑错误**：代码使用 `strlen(optValue) > 2` 校验用户输入的十进制端口号字符串长度，但该条件的本意是限制 CoAP 线缆编码后的字节长度为 0-2 字节（对应端口号范围 0-65535）。代码**混淆了字符串长度与数值范围**，导致十进制位数 ≥ 3 的端口号（如 100、221、5683）被错误拒绝。

### 2.1 关键日志证据

#### AT命令日志

**成功（value="99"）**：
```
[15:40:06.763] SEND >>>>>>>>>> AT+COAPOPTION=3,3,"180.101.147.115",7,"99",11,"ea"
[15:40:06.914] OK
```

**失败（value="100"）**：
```
[15:40:09.267] SEND >>>>>>>>>> AT+COAPOPTION=3,3,"180.101.147.115",7,"100",11,"ea"
[15:40:09.379] ERROR
```

**失败（value="221"）**：
```
[15:40:11.539] SEND >>>>>>>>>> AT+COAPOPTION=3,3,"180.101.147.115",7,"221",11,"ea"
[15:40:11.810] ERROR
```

#### 模块AP日志

**成功时（option name=7, value="99"）** — CNF 返回成功（error=0）：
```
.....coap_config_client..0.2...7.                   → COAP_CFG_OPTION, name=7
.....coapCnf->str_para1 name :99
.....opt_value name :99
LFS file open , path: coap_nvm                      → 存储 NV 成功
coapSlp store Context to FileSys
SIG_CMS_APPL_CNF: body data:05 0E B6 01 00 00 00 00 → error_code=0（成功）
AT CMD , RESP: OK
```

**失败时（option name=7, value="100"）** — CNF 返回错误（error=1），且无 NV 存储：
```
.....coap_config_client..0.2...7.                   → COAP_CFG_OPTION, name=7
.....coapCnf->str_para1 name :100
.....opt_value name :100
                                                    → ❌ 缺少 LFS file write（校验失败，跳过了 NV 存储）
SIG_CMS_APPL_CNF: body data:05 0E B7 01 01 00 00 00 → error_code=1（COAP_PARAM_ERROR）
AT CMD , RESP: ERROR
```

> **关键差异**：失败时在 `opt_value` 设置后直接返回错误，**没有执行 LFS 写入和 coapSlp store**，说明错误发生在 `coap_insert_optlist()` 之前的校验阶段。

### 2.2 代码调用链

| 信息 | 值 |
|------|-----|
| **入口函数** | `coapOPTION()` — AT 命令解析 |
| **调用链** | `coapOPTION()` → `coap_client_option()` → `coap_task_send_process()` → `coap_config_client()` |
| **问题位置** | `coap_config_client()` 中 `COAP_OPTION_URI_PORT` 分支，`at_coap_task.c` 约第 1331 行 |

**调用链分析**：
1. AT 框架解析 `AT+COAPOPTION` 命令，提取参数对 (name, value)
2. `coapOPTION()` 将每个选项封装为 `coap_send_msg`，通过 `xQueueSend()` 发送到 CoAP 任务队列
3. `coap_task_send_process()` 从队列取出消息，调用 `coap_config_client()`
4. `coap_config_client()` 进入 `COAP_CFG_OPTION` 分支，按 option name 进行 switch 分发
5. **在 `COAP_OPTION_URI_PORT` case 中**，`strlen(optValue) > 2` 校验将十进制字符串长度 ≥ 3 的合法端口号误判为参数错误

### 2.3 问题分析

**问题代码**（`at_coap_task.c`，`COAP_OPTION_URI_PORT` 分支）：

```c
case COAP_OPTION_URI_PORT: /*Uri-Port  uint, 0-2 B*/
    if(strlen(optValue) > 2)        // ❌ BUG：校验的是十进制字符串长度
    {
        ret = COAP_ERR;              // 返回错误码 101
        break;
    }
    optLen = strlen(optValue);
    cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
    optNode = coap_new_optlist(COAP_OPTION_URI_PORT, 
                               coap_encode_var_safe(optBuf2, sizeof(optBuf2), optNodeValue), 
                               optBuf2);
    coap_insert_optlist(&coapCurrClient->coap_optlist, optNode);
    break;
```

**注释 `/*Uri-Port  uint, 0-2 B*/` 的含义**：CoAP 协议规定 Uri-Port 选项的线缆编码长度为 0-2 字节，可表示 0~65535 的无符号整数。代码注释的本意是限制编码长度，但 `strlen()` 校验的是用户输入的**十进制字符串长度**，而非编码后的字节长度。

**数值分析**：

| 十进制字符串 | strlen | 数值 | 线缆编码字节 | 能否表示 | 实际校验结果 |
|-------------|--------|------|-------------|---------|------------|
| "99" | 2 | 99 | 1 字节 | ✅ | ✅ 通过 |
| "100" | 3 | 100 | 1 字节 | ✅ | ❌ 被拒绝 |
| "221" | 3 | 221 | 1 字节 | ✅ | ❌ 被拒绝 |
| "5683" | 4 | 5683 | 2 字节 | ✅ | ❌ 被拒绝 |
| "65535" | 5 | 65535 | 2 字节 | ✅ | ❌ 被拒绝 |

**正确做法**：应先调用 `cmsDecStrToUInt()` 将字符串转为整数，再校验数值范围是否在 0~65535 内：

```c
case COAP_OPTION_URI_PORT: /*Uri-Port  uint, 0-2 B*/
    optLen = strlen(optValue);
    cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
    if(optNodeValue > 65535)         // ✅ 校验数值范围
    {
        ret = COAP_ERR;
        break;
    }
    optNode = coap_new_optlist(COAP_OPTION_URI_PORT, 
                               coap_encode_var_safe(optBuf2, sizeof(optBuf2), optNodeValue), 
                               optBuf2);
    coap_insert_optlist(&coapCurrClient->coap_optlist, optNode);
    break;
```

### 2.4 同类问题影响范围

该 `strlen > N` 模式在同一 switch 块内被多个 uint 类型选项复用，均存在同类 Bug：

| 选项 | Option Name | 当前校验 | 线缆字节 | 受影响的合法值示例 |
|------|------------|---------|---------|-----------------|
| Uri-Port | 7 | `strlen > 2` | 0-2 B | ≥100 的端口号（100, 5683, 8080…） |
| Observe | 6 | `strlen > 3` | 0-3 B | ≥1000 的值 |
| Content-Format | 12 | `strlen > 2` | 0-2 B | ≥100 的格式号 |
| Accept | 17 | `strlen > 2` | 0-2 B | ≥100 的格式号 |
| Max-Age | 14 | `strlen > 4` | 0-4 B | ≥10000 的值 |
| Block1 | 19 | `strlen > 3` | 0-3 B | ≥1000 的值 |
| Block2 | 23 | `strlen > 3` | 0-3 B | ≥1000 的值 |
| Size1 | 60 | `strlen > 4` | 0-4 B | ≥10000 的值 |
| Size2 | 28 | `strlen > 4` | 0-4 B | ≥10000 的值 |

**修复建议**：对上述所有 uint 类型选项统一修改为数值范围校验，替换 `strlen(optValue) > N` 为对 `optNodeValue` 的范围检查。

---

## 3. 相关文件

- `PLAT/middleware/eigencomm/at/atentity/src/at_coap_task.c` — **Bug 所在文件**，`coap_config_client()` 函数 `COAP_OPTION_URI_PORT` 分支（约第 1331 行）
- `PLAT/middleware/eigencomm/at/atcust/src/atec_coap.c` — AT 命令解析层 `coapOPTION()` 函数
- `PLAT/middleware/eigencomm/at/atcust/inc/atec_coap.h` — AT 命令参数宏定义
- `.spec/logs/20260603_154022-coap.txt` — 问题日志文件

---

# AT+COAPOPTION uint 选项校验 Bug 修改方案

## 1. 修改文件

`PLAT/middleware/eigencomm/at/atentity/src/at_coap_task.c`

## 2. 宏控

使用已有的 **`FEATURE_NWY_AT_COAP_COMPATIBLE_N21`** 宏。

> 该宏已在 `PLAT/nwy_project.mk` 中通过 `CFLAGS += -DFEATURE_NWY_AT_COAP_COMPATIBLE_N21` 默认开启。
> 去掉该宏后，代码回退到原有 strlen 校验逻辑，编译正常。

## 3. 修改范围

`coap_config_client()` 函数内 `COAP_CFG_OPTION` → `switch(coapCnf->dec_para1)` 分支中，**所有 uint 类型选项**的 `strlen(optValue) > N` 校验，共 9 处。

### 3.1 修改前后对比（通用模式）

**修改前：**
```c
case COAP_OPTION_XXX: /*XXX  uint, 0-N B*/
    if(strlen(optValue) > N)           // ← 校验十进制字符串长度
    {
        ret = COAP_ERR;
        break;
    }
    optLen = strlen(optValue);
    cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
```

**修改后：**
```c
case COAP_OPTION_XXX: /*XXX  uint, 0-N B*/
/*Begin: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
#ifdef FEATURE_NWY_AT_COAP_COMPATIBLE_N21
    optLen = strlen(optValue);
    cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
    if(optNodeValue > MAX_VALUE)        // ← 校验数值范围
    {
        ret = COAP_ERR;
        break;
    }
#else
    if(strlen(optValue) > N)
    {
        ret = COAP_ERR;
        break;
    }
    optLen = strlen(optValue);
    cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
#endif
/*End: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
```

### 3.2 各选项具体修改内容

---

#### 修改点 1：COAP_OPTION_OBSERVE（第 1316~1328 行）

线缆字节 0-3 B，最大值 16777215

```c
                    case COAP_OPTION_OBSERVE: /*Observe  empty/uint, 0 B/0-3 B*/
/*Begin: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
#ifdef FEATURE_NWY_AT_COAP_COMPATIBLE_N21
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
                        if(optNodeValue > 16777215)
                        {
                            ret = COAP_ERR;
                            break;
                        }
#else
                        if(strlen(optValue) > 3)
                        {
                            ret = COAP_ERR;
                            break;
                        }
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
#endif
/*End: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
                        {
                            optNode = coap_new_optlist(COAP_OPTION_OBSERVE, coap_encode_var_safe(optBuf4, sizeof(optBuf4), optNodeValue), optBuf4);
                            coap_insert_optlist(&coapCurrClient->coap_optlist, optNode);
                        }
                        break;
```

---

#### 修改点 2：COAP_OPTION_URI_PORT（第 1330~1340 行）⬅️ 报告的 Bug

线缆字节 0-2 B，最大值 65535

```c
                    case COAP_OPTION_URI_PORT: /*Uri-Port  uint, 0-2 B*/
/*Begin: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
#ifdef FEATURE_NWY_AT_COAP_COMPATIBLE_N21
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
                        if(optNodeValue > 65535)
                        {
                            ret = COAP_ERR;
                            break;
                        }
#else
                        if(strlen(optValue) > 2)
                        {
                            ret = COAP_ERR;
                            break;
                        }
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
#endif
/*End: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
                        optNode = coap_new_optlist(COAP_OPTION_URI_PORT, coap_encode_var_safe(optBuf2, sizeof(optBuf2), optNodeValue), optBuf2);
                        coap_insert_optlist(&coapCurrClient->coap_optlist, optNode);
                        break;
```

---

#### 修改点 3：COAP_OPTION_CONTENT_FORMAT（第 1408~1436 行）

线缆字节 0-2 B，最大值 65535（含值白名单校验）

```c
                    case COAP_OPTION_CONTENT_FORMAT: /*Content-Format  uint, 0-2 B*/
/*Begin: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
#ifdef FEATURE_NWY_AT_COAP_COMPATIBLE_N21
                        optLen = strlen(optValue);
                        for(i=0; i<optLen; i++)
                        {
                            if((optValue[i] < '0')||(optValue[i] > '9'))
                            {
                                ret = COAP_ERR;
                                break;
                            }
                        }
                        if(ret == COAP_ERR)
                        {
                            break;
                        }
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
                        if(optNodeValue > 65535)
                        {
                            ret = COAP_ERR;
                            break;
                        }
#else
                        if(strlen(optValue) > 2)
                        {
                            ret = COAP_ERR;
                            break;
                        }

                        optLen = strlen(optValue);
                        for(i=0; i<optLen; i++)
                        {
                            if((optValue[i] < '0')||(optValue[i] > '9'))
                            {
                                ret = COAP_ERR;
                                break;
                            }
                        }
                        if(ret == COAP_ERR)
                        {
                            break;
                        }
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
#endif
/*End: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
                        if((optNodeValue!=0)&&(optNodeValue!=40)&&(optNodeValue!=41)&&(optNodeValue!=42)&&(optNodeValue!=47)&&(optNodeValue!=50))
                        {
                            ret = COAP_ERR;
                            break;
                        }
                        optNode = coap_new_optlist(COAP_OPTION_CONTENT_FORMAT, coap_encode_var_safe(optBuf2, sizeof(optBuf2), optNodeValue), optBuf2);
                        coap_insert_optlist(&coapCurrClient->coap_optlist, optNode);
                        break;
```

---

#### 修改点 4：COAP_OPTION_MAXAGE（第 1438~1448 行）

线缆字节 0-4 B，最大值 4294967295

```c
                    case COAP_OPTION_MAXAGE: /*Max-Age  uint, 0--4 B*/
/*Begin: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
#ifdef FEATURE_NWY_AT_COAP_COMPATIBLE_N21
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
                        if(optNodeValue > 4294967295)
                        {
                            ret = COAP_ERR;
                            break;
                        }
#else
                        if(strlen(optValue) > 4)
                        {
                            ret = COAP_ERR;
                            break;
                        }
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
#endif
/*End: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
                        optNode = coap_new_optlist(COAP_OPTION_MAXAGE, coap_encode_var_safe(optBuf4, sizeof(optBuf4), optNodeValue), optBuf4);
                        coap_insert_optlist(&coapCurrClient->coap_optlist, optNode);
                        break;
```

---

#### 修改点 5：COAP_OPTION_ACCEPT（第 1459~1487 行）

线缆字节 0-2 B，最大值 65535（含值白名单校验）

```c
                    case COAP_OPTION_ACCEPT: /*Accept  uint, 0-2 B*/
/*Begin: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
#ifdef FEATURE_NWY_AT_COAP_COMPATIBLE_N21
                        optLen = strlen(optValue);
                        for(i=0; i<optLen; i++)
                        {
                            if((optValue[i] < '0')||(optValue[i] > '9'))
                            {
                                ret = COAP_ERR;
                                break;
                            }
                        }
                        if(ret == COAP_ERR)
                        {
                            break;
                        }
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
                        if(optNodeValue > 65535)
                        {
                            ret = COAP_ERR;
                            break;
                        }
#else
                        if(strlen(optValue) > 2)
                        {
                            ret = COAP_ERR;
                            break;
                        }

                        optLen = strlen(optValue);
                        for(i=0; i<optLen; i++)
                        {
                            if((optValue[i] < '0')||(optValue[i] > '9'))
                            {
                                ret = COAP_ERR;
                                break;
                            }
                        }
                        if(ret == COAP_ERR)
                        {
                            break;
                        }
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
#endif
/*End: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
                        if((optNodeValue!=0)&&(optNodeValue!=40)&&(optNodeValue!=41)&&(optNodeValue!=42)&&(optNodeValue!=47)&&(optNodeValue!=50))
                        {
                            ret = COAP_ERR;
                            break;
                        }
                        optNode = coap_new_optlist(COAP_OPTION_ACCEPT, coap_encode_var_safe(optBuf2, sizeof(optBuf2), optNodeValue), optBuf2);
                        coap_insert_optlist(&coapCurrClient->coap_optlist, optNode);
                        break;
```

---

#### 修改点 6：COAP_OPTION_BLOCK2（第 1498~1508 行）

线缆字节 0-3 B，最大值 16777215

```c
                    case COAP_OPTION_BLOCK2: /*Block2  uint, 0-3 B*/
/*Begin: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
#ifdef FEATURE_NWY_AT_COAP_COMPATIBLE_N21
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
                        if(optNodeValue > 16777215)
                        {
                            ret = COAP_ERR;
                            break;
                        }
#else
                        if(strlen(optValue) > 3)
                        {
                            ret = COAP_ERR;
                            break;
                        }
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
#endif
/*End: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
                        //optNodeValue format is: (block.num << 4 | block.m << 3 | block.szx)
                        optNode = coap_new_optlist(COAP_OPTION_BLOCK2, coap_encode_var_safe(optBuf3, sizeof(optBuf3), optNodeValue), optBuf3);
                        coap_insert_optlist(&coapCurrClient->coap_optlist, optNode);
                        break;
```

---

#### 修改点 7：COAP_OPTION_BLOCK1（第 1510~1520 行）

线缆字节 0-3 B，最大值 16777215

```c
                    case COAP_OPTION_BLOCK1: /*Block1  uint, 0-3 B*/
/*Begin: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
#ifdef FEATURE_NWY_AT_COAP_COMPATIBLE_N21
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
                        if(optNodeValue > 16777215)
                        {
                            ret = COAP_ERR;
                            break;
                        }
#else
                        if(strlen(optValue) > 3)
                        {
                            ret = COAP_ERR;
                            break;
                        }
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
#endif
/*End: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
                        //optNodeValue format is: (block.num << 4 | block.m << 3 | block.szx)
                        optNode = coap_new_optlist(COAP_OPTION_BLOCK1, coap_encode_var_safe(optBuf3, sizeof(optBuf3), optNodeValue), optBuf3);
                        coap_insert_optlist(&coapCurrClient->coap_optlist, optNode);
                        break;
```

---

#### 修改点 8：COAP_OPTION_SIZE2（第 1522~1532 行）

线缆字节 0-4 B，最大值 4294967295

```c
                    case COAP_OPTION_SIZE2: /*SIZE  uint, 0-4 B*/
/*Begin: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
#ifdef FEATURE_NWY_AT_COAP_COMPATIBLE_N21
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
                        if(optNodeValue > 4294967295)
                        {
                            ret = COAP_ERR;
                            break;
                        }
#else
                        if(strlen(optValue) > 4)
                        {
                            ret = COAP_ERR;
                            break;
                        }
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
#endif
/*End: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
                        optNode = coap_new_optlist(COAP_OPTION_SIZE2, coap_encode_var_safe(optBuf4, sizeof(optBuf4), optNodeValue), optBuf4);
                        coap_insert_optlist(&coapCurrClient->coap_optlist, optNode);
                        break;
```

---

#### 修改点 9：COAP_OPTION_SIZE1（第 1552~1562 行）

线缆字节 0-4 B，最大值 4294967295

```c
                    case COAP_OPTION_SIZE1: /*Size1  uint, 0-4 B*/
/*Begin: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
#ifdef FEATURE_NWY_AT_COAP_COMPATIBLE_N21
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
                        if(optNodeValue > 4294967295)
                        {
                            ret = COAP_ERR;
                            break;
                        }
#else
                        if(strlen(optValue) > 4)
                        {
                            ret = COAP_ERR;
                            break;
                        }
                        optLen = strlen(optValue);
                        cmsDecStrToUInt(&optNodeValue, (const UINT8 *)optValue, optLen);
#endif
/*End: Modify by niusulong for/to fix strlen check bug of uint option in 2026.06.03*/
                        optNode = coap_new_optlist(COAP_OPTION_SIZE1, coap_encode_var_safe(optBuf4, sizeof(optBuf4), optNodeValue), optBuf4);
                        coap_insert_optlist(&coapCurrClient->coap_optlist, optNode);
                        break;
```

---

## 4. 无需修改的选项（String/opaque 类型）

以下选项的值为字符串，`strlen` 校验字符串长度是正确的，无需修改：

| 选项 | 行号 | 校验 |
|------|------|------|
| If-Match (opaque) | 1286 | `strlen > 8` ✅ |
| Uri-Host (string) | 1295 | `strlen > 255` ✅ |
| ETag (opaque) | 1304 | `strlen > 8` ✅ |
| If-None-Match (empty) | 1312 | 无校验 ✅ |
| Location-Path (string) | 1343 | `strlen > 255` ✅ |
| Uri-Path (string) | 1352 | `strlen > 255` ✅ |
| Uri-Query (string) | 1451 | `strlen > 255` ✅ |
| Location-Query (string) | 1490 | `strlen > 255` ✅ |
| Proxy-Uri (string) | 1535 | `strlen > 1034` ✅ |
| Proxy-Scheme (string) | 1544 | `strlen > 255` ✅ |

## 5. 数值范围与编码字节数对应表

| 线缆字节 | 可表示最大值 | 用于 |
|---------|------------|------|
| 0-2 B | 65535 | Uri-Port, Content-Format, Accept |
| 0-3 B | 16777215 | Observe, Block1, Block2 |
| 0-4 B | 4294967295 | Max-Age, Size1, Size2 |

## 6. 验证方法

修改完成后，使用以下 AT 命令序列验证：

```
// 1. 创建客户端
AT+COAPCREATE=5683

// 2. 验证 Uri-Port 原来失败的值（100、221、5683）
AT+COAPOPTION=2,3,"180.101.147.115",7,"100"           → 期望 OK
AT+COAPOPTION=2,3,"180.101.147.115",7,"221"           → 期望 OK
AT+COAPOPTION=2,3,"180.101.147.115",7,"5683"          → 期望 OK
AT+COAPOPTION=2,3,"180.101.147.115",7,"65535"         → 期望 OK
AT+COAPOPTION=2,3,"180.101.147.115",7,"65536"         → 期望 ERROR（超出范围）

// 3. 回归验证原来成功的值
AT+COAPOPTION=2,3,"180.101.147.115",7,"99"            → 期望 OK

// 4. 删除客户端
AT+COAPDELETE
```