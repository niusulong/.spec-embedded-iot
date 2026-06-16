# AT+LWM2MCREATE命令返回ERROR原因分析

---

# AT+LWM2MCREATE 返回 ERROR 原因分析

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **工作项 ID** | NA |
| **平台** | EC626 |
| **模块** | LWM2M |
| **问题分类** | 参数错误 |
| **症状关键词** | AT命令ERROR, handler未找到, 宏未启用, LWM2MCREATE |
| **根因概述** | FEATURE_WAKAAMA_ENABLE宏在CFLAGS中被注释掉，导致AT命令表中未注册+LWM2MCREATE处理函数，AT解码器找不到对应handler直接返回ERROR |
| **调用链摘要** | atcProcAtCmd → atcFindPreDefinedAtInfo → 返回PNULL → ATC_DEC_SYNTAX_ERR |
| **检索关键词** | LWM2MCREATE, ERROR, handler未注册, FEATURE_WAKAAMA_ENABLE, 宏配置缺失, AT命令表, 条件编译, wakaama |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)

---

## 1. 问题描述

执行 `AT+LWM2MCREATE="leshan.eclipseprojects.io",5683,56830,"nwy-lient-128",60` 命令，模块返回 ERROR，连续两次均失败。同时观察到 `+PBREADY` URC 在第一次命令响应中穿插出现。

**问题类型**：单日志分析

## 2. 根本原因

**`FEATURE_WAKAAMA_ENABLE` 宏未启用，导致 AT 命令表中未注册 `+LWM2MCREATE` 处理函数，AT 解码器找不到对应 handler 直接返回 ERROR。** 尽管板级配置 `THIRDPARTY_WAKAAMA_ENABLE=y` 已使 wakaama 源码编译进固件，但 CFLAGS 层的 `FEATURE_WAKAAMA_ENABLE` 缺失导致 AT 命令注册和代码条件编译被跳过。

### 2.1 关键日志证据

#### AT命令日志

```
[10:43:10.180] AT CMD, RECV dump: 41 54 2B 4C 57 4D 32 4D 43 52 45 41 54 45 3D ...
[10:43:10.181] ATCMD, decode AT: AT+LWM2MCREATE="leshan.eclipseprojects.io",5683,56830,"nwy-lient-128",60
[10:43:10.181] AT CMD, can't find handler / procFunc for AT: +LWM2MCREATE
[10:43:10.181] Debug Error, file: atc_decoder
[10:43:10.181] Debug Error, line: 3862, val: 0x2, 0x0, 0x3331c
[10:43:10.181] AT CMD, RESP: ERROR
```

第二次执行（10:43:12.229）完全相同的错误序列。

#### 模块AP日志

日志中仅有 Doze/Sleep 低功耗调度和周期性网络状态查询（`nwy_net_led_handle get net_state :2`），无任何 LWM2M 协议栈初始化或处理相关日志，进一步印证 LWM2M 功能未编译进固件。

### 2.2 代码调用链

| 信息 | 值 |
|------|-----|
| **入口函数** | `atcProcAtCmd()` |
| **调用链** | `atcProcAtCmd() → atcFindPreDefinedAtInfo() → 返回 PNULL → ATC_DEC_SYNTAX_ERR` |
| **问题位置** | `atc_decoder.c:2703-2712` |

**调用链分析**：
- AT 解码器收到 `+LWM2MCREATE` 命令后，调用 `atcFindPreDefinedAtInfo()` 在命令注册表中查找
- 由于 `FEATURE_WAKAAMA_ENABLE` 未定义，`atec_cust_cmd_table.c:1679` 处的 `AT_CMD_PRE_DEFINE("+LWM2MCREATE", ...)` 被 `#ifdef` 排除，未编译进固件
- `atcFindPreDefinedAtInfo()` 返回 `PNULL`，触发 `atc_decoder.c:2708` 的错误日志
- 解码器返回 `ATC_DEC_SYNTAX_ERR`，最终输出 `ERROR`

### 2.3 问题分析

**宏配置验证**：

| 文件 | 行号 | 配置项 | 当前值 | 作用 |
|------|------|--------|--------|------|
| `N306_EA/device/.../ec626_0h00.mk` | 46 | `THIRDPARTY_WAKAAMA_ENABLE` | **y** | 控制 wakaama 源码编译（Makefile 级排除） |
| `N306_EA/middleware/.../Makefile.inc` | 256-262 | `THIRDPARTY_WAKAAMA_ENABLE` 判断 | — | y 时不排除 lwm2m 源文件（已生效） |
| `N306_EA/nwy_project.mk` | 50 | `FEATURE_WAKAAMA_ENABLE` (CFLAGS) | **被注释掉** | 控制 AT 命令注册 + C 代码条件编译（未生效） |

**配置不一致**：`THIRDPARTY_WAKAAMA_ENABLE=y` 已使 wakaama 库代码编译进固件，但 `FEATURE_WAKAAMA_ENABLE` CFLAGS 缺失导致：
1. `atec_cust_cmd_table.c:1679` 的 `#ifdef FEATURE_WAKAAMA_ENABLE` 为假，AT 命令表未注册 `+LWM2MCREATE` 等 9 条命令
2. `atec_lwm2m.c` 虽然编译了，但其 handler 未被链接到命令表中
3. `wakaama_core/` 中部分条件代码（如 `registration.c:309` 等）被跳过

**受影响的命令**（均被同一 `#ifdef FEATURE_WAKAAMA_ENABLE` 包裹）：

| AT 命令 | Handler | 状态 |
|---------|---------|------|
| `+LWM2MCREATE` | `lwm2mCREATE` | 未注册 |
| `+LWM2MDELETE` | `lwm2mDELETE` | 未注册 |
| `+LWM2MADDOBJ` | `lwm2mADDOBJ` | 未注册 |
| `+LWM2MDELOBJ` | `lwm2mDELOBJ` | 未注册 |
| `+LWM2MNOTIFY` | `lwm2mNOTIFY` | 未注册 |
| `+LWM2MUPDATE` | `lwm2mUPDATE` | 未注册 |
| `+LWM2MREADCONF` | `lwm2mREADCONF` | 未注册 |
| `+LWM2MWRITECONF` | `lwm2mWRITECONF` | 未注册 |
| `+LWM2MEXECUTECONF` | `lwm2mEXECUTECONF` | 未注册 |

**附加发现**：`+PBREADY` URC 在第一次 AT 命令响应中穿插出现（10:43:10.190），这是 UART 波特率自动检测完成后的通知，与 LWM2M ERROR 无直接因果关系，但表明此时串口刚完成自动波特率协商（`USART AUTO BAUDRATE SUCCESS`，检测波特率 115363）。

### 2.4 修复方案

在 `PLAT/nwy_project/N306_EA/nwy_project.mk` 第 50 行取消注释：

```makefile
# 修改前
#CFLAGS += -DFEATURE_WAKAAMA_ENABLE

# 修改后
CFLAGS += -DFEATURE_WAKAAMA_ENABLE
```

同时建议启用关联宏（如需要 CoAP 支持）：

```makefile
CFLAGS += -DFEATURE_LIBCOAP_ENABLE
```

修改后需重新编译整个固件。

## 3. 相关文件

- `PLAT/nwy_project/N306_EA/nwy_project.mk:50` — CFLAGS 宏配置（根因所在）
- `PLAT/nwy_project/N306_EA/device/eigencomm/board/ec626_0h00/ec626_0h00.mk:46` — 板级编译开关（已启用）
- `PLAT/nwy_project/N306_EA/middleware/eigencomm/Makefile.inc:256-262` — 源文件排除逻辑