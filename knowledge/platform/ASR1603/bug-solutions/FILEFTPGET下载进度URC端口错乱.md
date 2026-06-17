# FILEFTPGET下载进度URC上报端口错误 原因分析

## 0. 结构化摘要

| **字段** | 值 |
|----------|-----|
| **工作项 ID** | NA |
| **平台** | ASR1603 |
| **模块** | FTP / AT框架 |
| **问题分类** | 异步回调端口错乱 |
| **症状关键词** | URC端口错乱, FILEFTPGET进度上报, 全局变量覆盖 |
| **根因概述** | `nwy_at_engine` 是全局单一变量，每次AT命令到来时被覆盖写入，异步回调中通过该变量获取端口信息时取到最新命令端口而非原始命令端口 |
| **调用链摘要** | `AT_NWY_CmdFunc_Adpt()` → `nwy_at_engine = *xid_p` → `nwy_ftp_rsp_cb()` → `nwy_app_at_get_plat_at_channel()` → `nwy_app_at_unsol_str()` → `nwy_buf_echo_port()` |
| **检索关键词** | nwy_at_engine, FILEFTPGET, URC, at_channel, nwy_app_at_get_plat_at_channel, 全局变量 |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 解决方案](#3-解决方案)
- [4. 验证步骤](#4-验证步骤)
- [5. 相关文件](#5-相关文件)

---

## 1. 问题描述

UART口发送 `AT+FILEFTPGET=05-4c.pack` 命令后，开始正常通过UART上报 `+FILEFTPGET:10%` 下载进度。随后通过USB口发送任意AT命令（如 `AT+FSLS?`），此后所有主动上报（包括 `+FILEFTPGET:20%` ~ `+FILEFTPGET:100%`）都切换到了USB端口上报，UART口不再收到任何上报。

**问题类型**：对比分析

**时间线**：
| 时间 | 事件 | 端口 |
|------|------|------|
| 16:37:34.430 | 发送 `AT+FILEFTPGET=05-4c.pack` | UART |
| 16:37:34.502 | 收到 `OK` | UART |
| 16:37:36.400 | 收到 `+FILEFTPGET:10%` | UART (正常) |
| 16:37:39.488 | 发送 `AT+FSLS?` | USB (触发端口切换) |
| 16:37:55.873 | 收到 `+FILEFTPGET:20%` | USB (异常，应在UART) |
| 16:38:02~33 | 收到 `+FILEFTPGET:30%~100%` | USB (异常，应在UART) |

## 2. 根本原因

**`nwy_at_engine` 是全局单一变量，每次AT命令到来时被覆盖写入，导致异步回调中通过该全局变量获取端口信息时，取到的是最新命令的端口而非原始命令的端口。**

### 2.1 关键日志证据

#### UART口AT日志

```
[2026-06-01 16:37:34.430] SEND >>>>>>>>>> AT+FILEFTPGET=05-4c.pack
[2026-06-01 16:37:34.502] OK
[2026-06-01 16:37:36.400] +FILEFTPGET:10%
--- 之后UART再无任何上报 ---
```

#### USB口AT日志

```
[2026-06-01 16:37:39.488] SEND >>>>>>>>>> AT+FSLS?     <-- USB口发送命令，覆盖nwy_at_engine
[2026-06-01 16:37:39.560] +FSLS: 589824 OK
[2026-06-01 16:37:55.873] +FILEFTPGET:20%              <-- FTP进度URC错误地从USB口输出
[2026-06-01 16:38:02.536] +FILEFTPGET:30%
[2026-06-01 16:38:09.641] +FILEFTPGET:40%
... (后续所有进度都从USB输出)
[2026-06-01 16:38:33.161] +FILEFTPGET:100%
[2026-06-01 16:38:33.161] +FILEFTPSTAT: 1,106468
```

### 2.2 代码调用链

| 信息 | 值 |
|------|-----|
| **入口函数** | `AT_NWY_CmdFunc_Adpt()` |
| **调用链** | `AT_NWY_CmdFunc_Adpt()` → `nwy_at_engine = *xid_p` → `nwy_ftp_rsp_cb()` → `nwy_app_at_get_plat_at_channel()` → `nwy_app_at_unsol_str()` → `nwy_buf_echo_port()` |
| **问题位置** | `nwy_app_at_get_plat_at_channel()` 返回全局变量 `&nwy_at_engine`，而非保存原始命令端口的句柄 |

**调用链分析**：

1. **AT命令入口** - `AT_NWY_CmdFunc_Adpt()` (`nwy_app_at_parser_adpt.c:26`)：
   ```c
   nwy_at_engine = *xid_p;  // 全局变量被覆盖为当前命令的端口句柄
   ```
   当UART口发送 `AT+FILEFTPGET` 时，`nwy_at_engine` 被设置为UART端口的ATP index。

2. **异步回调获取端口** - `nwy_ftp_rsp_cb()` (`nwy_app_at_func_ftp.c:322`)：
   ```c
   void* at_channel = nwy_app_at_get_plat_at_channel(cid, sid);
   ```
   每次FTP数据回调时，都通过 `nwy_app_at_get_plat_at_channel()` 获取 `at_channel`。

3. **全局变量返回** - `nwy_app_at_get_plat_at_channel()` (`nwy_app_at_platform.c:607-610`)：
   ```c
   void* nwy_app_at_get_plat_at_channel(int cid, int sid)
   {
     return (void*)&nwy_at_engine;  // 返回全局变量的地址！
   }
   ```
   返回的是 `nwy_at_engine` 的**地址**，而非命令发起时保存的值。

4. **USB命令覆盖** - 当USB口发送 `AT+FSLS?` 时，`AT_NWY_CmdFunc_Adpt()` 再次执行，`nwy_at_engine` 被覆盖为USB端口的ATP index。

5. **进度URC发到错误端口** - 后续FTP回调调用 `nwy_app_ftp_pack_dowlod_progress(at_channel, ...)` → `nwy_app_at_unsol_str(at_channel, ...)` → `nwy_buf_echo_port(*(unsigned int*)at_channel, ...)` 时，`at_channel` 指向的 `nwy_at_engine` 已经是USB端口的值，所以URC被发送到USB口。

#### 对比环境

| 对比维度 | 正常日志(10%进度) | 异常日志(20%~100%进度) |
|----------|-------------------|----------------------|
| 日志文件 | uart-at-log.log | usb-at-log.log |
| 时间范围 | 16:37:34 ~ 16:37:36 | 16:37:39 ~ 16:38:33 |
| 场景描述 | USB口未发送命令前，URC正确上报到UART | USB口发送AT命令后，URC错误上报到USB |

#### 关键差异点

| 时间点 | 正常行为(10%) | 异常行为(20%~100%) | 差异说明 |
|--------|--------------|-------------------|----------|
| 16:37:36 | UART收到+FILEFTPGET:10% | - | nwy_at_engine=UART端口 |
| 16:37:39 | - | USB口发送AT+FSLS? | **nwy_at_engine被覆盖为USB端口** |
| 16:37:55 | (期望UART收到20%) | USB收到+FILEFTPGET:20% | **关键差异：URC端口从UART变为USB** |

### 2.3 问题分析

**核心问题**：`nwy_at_engine` 是一个全局单一变量，用于存储当前AT命令的端口句柄。在异步操作场景下，这个全局变量会被后续来自不同端口的AT命令覆盖。

**数据流分析**：

```
时间线：UART发送FILEFTPGET → USB发送FSLS → FTP回调触发

nwy_at_engine 值变化：
  16:37:34  nwy_at_engine = UART_PORT_HANDLE    (UART发送FILEFTPGET)
  16:37:36  FTP回调: at_channel = &nwy_at_engine → 读取UART_PORT_HANDLE → 正确输出到UART ✓
  16:37:39  nwy_at_engine = USB_PORT_HANDLE     (USB发送FSLS，覆盖！)
  16:37:55  FTP回调: at_channel = &nwy_at_engine → 读取USB_PORT_HANDLE → 错误输出到USB ✗
```

**为什么10%能正确上报**：因为10%进度发生在USB口发送AT命令之前（16:37:36 vs 16:37:39），此时 `nwy_at_engine` 尚未被覆盖。

**为什么func_args.at_channel保存了正确值但未被使用**：注意 `AT_NWY_CmdFunc_Adpt()` 第57行 `func_args.at_channel = (void*)xid_p` 保存了命令发起时的端口句柄，但这个值只传递给了同步命令处理函数。异步回调 `nwy_ftp_rsp_cb()` 并未使用 `func_args.at_channel`，而是重新通过 `nwy_app_at_get_plat_at_channel()` 获取全局变量。

**影响范围**：此问题不仅影响FILEFTPGET，所有使用 `nwy_app_at_get_plat_at_channel()` 获取端口信息的异步操作都存在同样的端口错乱风险，包括：
- FOTA下载进度上报
- HTTP下载进度上报
- 其他异步AT命令的URC上报

## 3. 解决方案

### 3.1 方案1: 为异步操作保存命令发起时的端口句柄（推荐）

**修改内容**：在FTP（及其他异步操作）的命令处理函数中，将 `at_channel`（命令发起时的端口句柄）保存到模块级数据结构中，异步回调时使用保存的句柄而非全局变量。

具体修改：

1. 在 `ftp_file_info` 结构体中添加 `at_channel` 字段：
```c
// nwy_app_at_func_def.h
typedef struct
{
  int is_vaild;
  int recv_mode;
  char filename[256];
  char locname[256];
  int pos;
  int length;
  int file_size;
#if defined(FEATURE_NWY_AT_YDDL) || defined(FEATURE_NWY_AT_PROC_FTPRATE)
  int dowload_size;
  int curr_size;
#endif
  unsigned int saved_at_channel;  // 新增：保存命令发起时的端口句柄
} nwy_ftp_fileinfo;
```

2. 在 `nwy_app_at_fileftpget_func()` 命令处理函数中保存端口句柄：
```c
// nwy_app_at_func_ftp.c - nwy_app_at_fileftpget_func()
ftp_file_info.saved_at_channel = *(unsigned int*)arg->at_channel;
```

3. 在 `nwy_ftp_rsp_cb()` 异步回调中使用保存的句柄：
```c
// nwy_app_at_func_ftp.c - nwy_ftp_rsp_cb()
// 修改前：
// void* at_channel = nwy_app_at_get_plat_at_channel(cid, sid);
// 修改后：
void* at_channel = (void*)&ftp_file_info.saved_at_channel;
```

**影响范围**：仅修改FTP模块相关文件，不影响其他模块

**实施难度**：低

### 3.2 方案2: 修改 nwy_app_at_get_plat_at_channel() 支持按cid/sid存储端口句柄

**修改内容**：为每个异步操作（cid, sid）维护独立的端口句柄映射表，替代单一全局变量。

```c
// 新增映射表
typedef struct {
    uint16 cid;
    uint16 sid;
    unsigned int at_channel;
} nwy_async_channel_map_t;

#define NWY_MAX_ASYNC_OPS 16
static nwy_async_channel_map_t async_channel_map[NWY_MAX_ASYNC_OPS];

// 注册：AT命令发起时调用
void nwy_app_at_register_channel(int cid, int sid, unsigned int at_channel);

// 查询：异步回调时调用
void* nwy_app_at_get_plat_at_channel(int cid, int sid);

// 注销：异步操作完成时调用
void nwy_app_at_unregister_channel(int cid, int sid);
```

**影响范围**：影响所有使用 `nwy_app_at_get_plat_at_channel()` 的模块，需全面测试

**实施难度**：中

## 4. 验证步骤

1. UART口发送 `AT+FILEFTPGET=05-4c.pack`，等待 `+FILEFTPGET:10%` 上报
2. USB口发送 `AT+FSLS?`（或任意AT命令）
3. 确认后续 `+FILEFTPGET:20%~100%` 进度URC仍然从UART口上报
4. 确认USB口只收到 `AT+FSLS?` 的响应，不收到FTP进度URC
5. 反向验证：USB口发送 `AT+FILEFTPGET`，UART口发送AT命令，确认进度URC从USB口上报
6. 验证下载完成后 `+FILEFTPSTAT` 上报端口是否正确

## 5. 相关文件

- `pcac/nwy_bpv2_plat/plat/asr1603/nwy_app_at_parser_adpt.c` - AT命令适配层，`nwy_at_engine` 全局变量赋值处
- `pcac/nwy_bpv2_plat/plat/asr1603/nwy_app_at_platform.c` - `nwy_app_at_get_plat_at_channel()` 实现，返回全局变量地址
- `pcac/NWY_FRAMEWORK/atcmd/nwy_at_proc/src/nwy_app_at_func_ftp.c` - FTP命令处理和进度上报
- `pcac/NWY_FRAMEWORK/atcmd/nwy_at_proc/inc/nwy_app_at_func_def.h` - `nwy_ftp_fileinfo` 数据结构定义
- `hop/telephony/nwy/src/nwy_interface_util.c` - `nwy_buf_echo_port()` 端口输出路由