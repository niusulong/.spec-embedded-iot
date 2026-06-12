# FILEFTPGET 下载进度 URC 端口切换原因分析

## 0. 结构化摘要

| **字段** | 值 |
|----------|-----|
| **平台** | ASR1603 |
| **模块** | FTP / AT框架 |
| **问题分类** | 异步回调端口错乱 |
| **症状关键词** | URC端口切换, FILEFTPGET进度上报, USB端口抢占 |
| **根因概述** | 全局变量 `nwy_at_engine` 在每次AT命令处理时被覆写，FTP异步回调通过该全局变量获取端口，导致后续AT命令所在端口覆盖原始发起端口 |
| **调用链摘要** | `AT_NWY_CmdFunc_Adpt()` → `nwy_at_engine = *xid_p` → FTP回调 → `nwy_app_at_get_plat_at_channel()` → `&nwy_at_engine` → `nwy_buf_echo_port()` |
| **检索关键词** | nwy_at_engine, FILEFTPGET, URC, 端口切换, at_channel, 异步回调 |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 解决方案](#3-解决方案)
- [4. 验证步骤](#4-验证步骤)
- [5. 相关文件](#5-相关文件)

---

## 1. 问题描述

UART 发送 `AT+FILEFTPGET=05-4c.pack` 命令开始 FTP 下载后，下载进度 `+FILEFTPGET:10%` 仍正常在 UART 端口上报。之后通过 USB 端口发送任意 AT 命令（如 `AT+FSLS?`），所有后续 URC 主动上报（包括 `+FILEFTPGET:20%~100%` 和 `+FILEFTPSTAT`）全部切换到 USB 端口输出，UART 端口不再收到任何主动上报。

**问题类型**：单日志分析 + 代码交叉验证

**问题时间范围**：2026-06-01 16:37:34 ~ 16:38:33

---

## 2. 根本原因

**全局变量 `nwy_at_engine` 被任意端口的 AT 命令覆写，导致 FTP 异步回调获取到错误的 URC 上报端口。**

FTP 下载进度上报通过全局变量 `nwy_at_engine` 确定输出端口，而该变量在每次处理任何端口的 AT 命令时都会被覆写为当前命令的端口号。当 USB 端口发送 AT 命令后，`nwy_at_engine` 被更新为 USB 端口号，后续 FTP 进度 URC 就错误地路由到 USB 端口。

### 2.1 关键日志证据

#### AT 日志关键片段

**UART 端口日志**（uart-at-log.log）：
```
[2026-06-01 16:37:34.430] SEND >>>>>>>>>> AT+FILEFTPGET=05-4c.pack
[2026-06-01 16:37:34.502] OK
[2026-06-01 16:37:36.400] +FILEFTPGET:10%        ← 最后一次在 UART 上报
（之后 UART 再无任何上报）
```

**USB 端口日志**（usb-at-log.log）：
```
[2026-06-01 16:37:39.488] SEND >>>>>>>>>> AT+FSLS?   ← USB 首次发送 AT 命令
[2026-06-01 16:37:39.560] +FSLS: 589824 OK
[2026-06-01 16:37:55.873] +FILEFTPGET:20%           ← URC 已切换到 USB！
[2026-06-01 16:38:02.536] +FILEFTPGET:30%
...（后续进度全部在 USB）
[2026-06-01 16:38:33.161] +FILEFTPGET:100%
[2026-06-01 16:38:33.161] +FILEFTPSTAT: 1,106468
```

#### AP 日志关键片段（sAtpIndex 端口追踪）

| 行号 | 时间 | sAtpIndex | 事件 |
|------|------|-----------|------|
| 34338 | 16:37:34.441 | **13 (UART)** | `[ AT RECV ] [ 13 ] : AT+FILEFTPGET=05-4c.pack` |
| 34484 | 16:37:34.501 | **13 (UART)** | `nwy_get_at_rspcb_func: sAtpIndex=13 is valid` |
| 40566 | 16:37:36.401 | **13 (UART)** | `nwy_get_at_rspcb_func: sAtpIndex=13` ← 10% 进度上报 |
| 47418 | 16:37:39.500 | **30 (USB)** | `[ AT RECV ] [ 30 ] : AT+FSLS?` ← USB 命令到达 |
| 47520 | 16:37:39.560 | **30 (USB)** | `nwy_get_at_rspcb_func: sAtpIndex=30` ← **端口切换！** |
| 73542 | 16:37:55.879 | **30 (USB)** | `nwy_get_at_rspcb_func: sAtpIndex=30` ← 20% 进度上报 |
| 74910 | 16:38:02.554 | **30 (USB)** | 30% 进度 |
| ... | ... | **30 (USB)** | 40%~100% 全部在 USB |

### 2.2 代码调用链

| 信息 | 值 |
|------|-----|
| **入口函数** | `AT_NWY_CmdFunc_Adpt()` |
| **调用链** | `AT_NWY_CmdFunc_Adpt()` → `nwy_at_engine = *xid_p` → FTP 回调 → `nwy_app_at_get_plat_at_channel()` → `nwy_app_at_unsol_str()` |
| **问题位置** | `nwy_app_at_platform.c:611` — `return (void*)&nwy_at_engine;` |
| **覆写位置** | `nwy_app_at_parser_adpt.c:26` — `nwy_at_engine = *xid_p;` |

**调用链分析**：

1. **FTP 命令发起**：UART 端口（sAtpIndex=13）发送 `AT+FILEFTPGET`
   - `AT_NWY_CmdFunc_Adpt()` 执行 `nwy_at_engine = *xid_p`，将全局变量 `nwy_at_engine` 设为 `MAKE_AT_HANDLE(13)`
   - FTP 下载异步启动，回调函数 `nwy_ftp_rsp_cb` 注册等待

2. **USB 命令到达**（关键转折点）：USB 端口（sAtpIndex=30）发送 `AT+FSLS?`
   - `AT_NWY_CmdFunc_Adpt()` 执行 `nwy_at_engine = *xid_p`，将全局变量 `nwy_at_engine` **覆写**为 `MAKE_AT_HANDLE(30)`

3. **FTP 进度回调触发**：
   - `nwy_ftp_rsp_cb()` 被异步调用
   - 调用 `nwy_app_at_get_plat_at_channel(cid, sid)` 获取 `&nwy_at_engine`
   - 解引用 `*(unsigned int*)&nwy_at_engine` 读到 `MAKE_AT_HANDLE(30)`（已被 USB 命令覆写）
   - `nwy_buf_echo_port(MAKE_AT_HANDLE(30), "+FILEFTPGET:20%")` → `sAtpIndex=30` → 将 URC 发送到 USB 端口

### 2.3 问题分析（5-Why）

```
Why 1: 为什么 UART 端口不再收到 FTP 下载进度上报？
  → 因为 URC 被路由到了 USB 端口（sAtpIndex=30）而非 UART 端口（sAtpIndex=13）

Why 2: 为什么 URC 路由到了错误的端口？
  → 因为 nwy_ftp_rsp_cb() 通过 nwy_app_at_get_plat_at_channel() 获取 &nwy_at_engine，
     解引用读到的是被 USB 命令覆写后的值（sAtpIndex=30）

Why 3: 为什么 nwy_at_engine 会被覆写？
  → 因为 nwy_at_engine 是全局变量，每次处理 AT 命令时在 AT_NWY_CmdFunc_Adpt() 中
     被赋值为当前命令的句柄（nwy_at_engine = *xid_p）

Why 4: 为什么回调使用全局变量而非保存发起端口？
  → 因为 arg->at_channel 是指向框架临时内存 xid_p 的指针，命令返回 OK 后失效，
     回调中无法直接使用 arg->at_channel，只能通过 nwy_app_at_get_plat_at_channel()
     获取全局变量地址 &nwy_at_engine

Why 5: 为什么不在 FTP 发起时保存句柄值？
  → 设计缺陷：原始代码未在 FTP 命令入口解引用保存句柄值，
     导致异步回调只能依赖全局变量 nwy_at_engine

根本原因：FTP 异步回调依赖全局变量 nwy_at_engine 获取端口句柄，
         而该变量会被任意端口的 AT 命令覆写，未在命令入口保存绑定端口的句柄值。
```

**核心缺陷代码**：

```c
// nwy_app_at_parser_adpt.c:25-26 — 每次处理 AT 命令都会覆写
*xid_p = MAKE_AT_HANDLE(*(TelAtParserID *)arg_p);
nwy_at_engine = *xid_p;

// nwy_app_at_parser_adpt.c:57 — at_channel 是指向框架临时内存的指针
func_args.at_channel = (void*)xid_p;

// nwy_app_at_platform.c:609-612 — 获取 at_channel，忽略 cid/sid
void* nwy_app_at_get_plat_at_channel(int cid, int sid)
{
  return (void*)&nwy_at_engine;  // 返回全局变量地址，值会被最新命令覆写
}

// nwy_app_at_platform.c:552-563 — unsol_str 解引用取句柄值
int nwy_app_at_unsol_str(void* at_channel, char* fmt, ...)
{
    ...
    nwy_buf_echo_port(*(unsigned int*)at_channel, buf, len);  // 解引用取句柄值
    ...
}
```

### 2.4 对比分析

#### 对比环境

| 对比维度 | 正常阶段（USB 命令前） | 异常阶段（USB 命令后） |
|----------|----------------------|----------------------|
| 时间范围 | 16:37:34 ~ 16:37:36 | 16:37:39 ~ 16:38:33 |
| nwy_at_engine | MAKE_AT_HANDLE(13) | MAKE_AT_HANDLE(30) |
| URC 输出端口 | UART ✓ | USB ✗ |

#### 关键差异点

| 时间点 | 正常值 | 异常值 | 差异说明 |
|--------|--------|--------|----------|
| 16:37:36.401 | sAtpIndex=13 | — | 10% URC 正确路由到 UART |
| 16:37:39.500 | — | sAtpIndex=30 | **USB 命令覆写 nwy_at_engine** |
| 16:37:55.879 | — | sAtpIndex=30 | **20% URC 错误路由到 USB** |

---

## 3. 解决方案

**核心思路**：在 FTP 命令入口解引用获取句柄值并保存到全局变量，回调中传递全局变量地址给 `nwy_app_at_unsol_str`。

**原理**：
- `nwy_app_at_unsol_str` 内部做 `*(unsigned int*)at_channel` 解引用取句柄值
- 传入 `&g_ftp_at_handle`（全局变量地址），始终有效
- `g_ftp_at_handle` 保存的是命令入口时刻的句柄值，不会被其他命令覆写
- `nwy_buf_echo_port(handle_value, ...)` 通过句柄值中的 sAtpIndex 定位端口，端口回调始终有效

**跨平台说明**：本方案仅适用于 asr1603(bpv2) 平台。`*(unsigned int*)arg->at_channel` 解引用方式是 asr1603 平台特有的，bl5612/rda8850 平台的 `at_channel` 语义不同（值传递/引擎指针），不适用此方案。

### 3.1 修改文件

**`pcac/NWY_FRAMEWORK/atcmd/nwy_at_proc/src/nwy_app_at_func_ftp.c`**（仅此 1 个文件）

### 3.2 修改内容

#### 改动 0：替换全局变量类型（line 34）

将 `void* g_fileftp_channel` 替换为 `unsigned int g_ftp_at_handle`，保存解引用后的句柄值。

```c
// 修改前
void* g_fileftp_channel;

// 修改后
static unsigned int g_ftp_at_handle = 0;
```

#### 改动 1：回调使用保存的句柄地址（line 322）

`nwy_ftp_rsp_cb` 中用 `&g_ftp_at_handle` 替代 `nwy_app_at_get_plat_at_channel()`。

```c
// 修改前
void* at_channel = nwy_app_at_get_plat_at_channel(cid, sid);

// 修改后
void* at_channel = (void*)&g_ftp_at_handle;
```

#### 改动 2：FTP PUT timer 回调使用保存的句柄地址（line 105, 123）

```c
// 修改前
nwy_app_at_unsol_str(g_fileftp_channel, "\r\n+FILEFTPSTAT: 1,%d\r\n", g_fileput_info.sent_size);
nwy_app_at_unsol_str(g_fileftp_channel, "\r\n+FILEFTPSTAT: 0,%d\r\n", g_fileput_info.sent_size);

// 修改后
nwy_app_at_unsol_str((void*)&g_ftp_at_handle, "\r\n+FILEFTPSTAT: 1,%d\r\n", g_fileput_info.sent_size);
nwy_app_at_unsol_str((void*)&g_ftp_at_handle, "\r\n+FILEFTPSTAT: 0,%d\r\n", g_fileput_info.sent_size);
```

#### 改动 3~9：各 FTP 命令入口保存句柄值

在所有触发 `nwy_ftp_rsp_cb` 回调的 FTP 命令处理函数中，发起异步操作前保存当前句柄值。

| # | 命令 | 函数 | 插入位置 |
|---|------|------|---------|
| 3 | AT+FTPLOGIN | `nwy_app_at_ftplogin_func` | `nwy_app_ftp_login()` 调用前 |
| 4 | AT+FTPLOGOUT | `nwy_app_at_ftplogout_func` | `nwy_app_ftp_logout()` 调用前 |
| 5 | AT+FTPGET | `nwy_app_at_ftpget_func` | `nwy_app_ftp_get_file_ext()` 调用前 |
| 6 | AT+FTPSIZE | `nwy_app_at_ftpsize_func` | `nwy_app_ftp_get_size()` 调用前 |
| 7 | AT+FTPRENAME | `nwy_app_at_ftprename_func` | `nwy_app_ftp_rename()` 调用前 |
| 8 | AT+FTPPUT | `nwy_app_at_ftpput_func` | FTP 操作调用前 |
| 9 | AT+FILEFTPGET | `nwy_app_at_fileftpget_func` | `nwy_app_ftp_get_file()` 调用前 |
| — | AT+FILEFTPPUT | `nwy_app_at_fileftpput_func` | **原有赋值需替换** |

每处插入相同代码：
```c
g_ftp_at_handle = *(unsigned int*)arg->at_channel;
```

原有 FILEFTPPUT 入口（line 1645）替换：
```c
// 修改前
g_fileftp_channel = arg->at_channel;

// 修改后
g_ftp_at_handle = *(unsigned int*)arg->at_channel;
```

### 3.3 修改汇总

| 类型 | 数量 | 说明 |
|------|------|------|
| 变量替换 | 1 处 | `void* g_fileftp_channel` → `static unsigned int g_ftp_at_handle = 0` |
| 回调改引用 | 1 处 | `nwy_app_at_get_plat_at_channel()` → `(void*)&g_ftp_at_handle` |
| timer 回调改引用 | 2 处 | `g_fileftp_channel` → `(void*)&g_ftp_at_handle` |
| 各入口加赋值 | 7 处 | FTPLOGIN / FTPLOGOUT / FTPGET / FTPSIZE / FTPRENAME / FTPPUT / FILEFTPGET |
| 原有赋值替换 | 1 处 | FILEFTPPUT 入口 `g_fileftp_channel = arg->at_channel` → `g_ftp_at_handle = *(unsigned int*)arg->at_channel` |
| **总净改动** | **+9 行，-3 行，改 3 行** | |
| **修改文件** | **1 个文件** | `nwy_app_at_func_ftp.c` |

### 3.4 数据流验证

```
命令入口（UART sAtpIndex=13）:
  arg->at_channel = (void*)xid_p          // 指向框架临时内存
  *xid_p = MAKE_AT_HANDLE(13)             // 值 = MAKE_AT_HANDLE(13)
  g_ftp_at_handle = *(unsigned int*)xid_p // 保存值 = MAKE_AT_HANDLE(13) ✓

USB 命令到达（sAtpIndex=30）:
  nwy_at_engine = MAKE_AT_HANDLE(30)      // 全局变量被覆写
  g_ftp_at_handle 不变                    // 仍为 MAKE_AT_HANDLE(13) ✓

FTP 回调触发:
  at_channel = (void*)&g_ftp_at_handle    // 指向全局变量地址，始终有效 ✓
  nwy_app_at_unsol_str(at_channel, ...)
    → *(unsigned int*)at_channel          // 解引用 = MAKE_AT_HANDLE(13) ✓
    → nwy_buf_echo_port(MAKE_AT_HANDLE(13), ...)
      → GET_ATP_INDEX → sAtpIndex=13     // 路由到 UART ✓
```

---

## 4. 验证步骤

1. **复现测试**：UART 发送 `AT+FILEFTPGET=<filename>` 开始下载 → USB 发送任意 AT 命令 → 确认 UART 不再收到进度上报
2. **修复验证**：应用修改后，重复上述步骤，确认进度 URC 始终在 UART 端口上报
3. **交叉验证**：USB 发起 FTP 下载 → UART 发送 AT 命令 → 确认 URC 仍在 USB 端口上报
4. **LOGIN 验证**：UART 发送 `AT+FTPLOGIN` → USB 发送 AT 命令 → 确认 `+FTPLOGIN:` URC 在 UART 上报
5. **其他 FTP 命令验证**：测试 `AT+FTPLOGOUT`、`AT+FTPSIZE`、`AT+FTPRENAME` 等命令的 URC 端口正确性
6. **长时间稳定性**：多轮 FTP 下载/上传测试，确认 URC 路由始终正确

---

## 5. 相关文件

| 文件 | 说明 |
|------|------|
| `pcac/NWY_FRAMEWORK/atcmd/nwy_at_proc/src/nwy_app_at_func_ftp.c` | FTP AT 命令处理及 URC 回调（**本次修改文件**） |
| `pcac/nwy_bpv2_plat/plat/asr1603/nwy_app_at_parser_adpt.c` | `AT_NWY_CmdFunc_Adpt()` 中 `nwy_at_engine = *xid_p` 覆写全局句柄（第 25-26 行），`func_args.at_channel = (void*)xid_p` 设置临时指针（第 57 行） |
| `pcac/nwy_bpv2_plat/plat/asr1603/nwy_app_at_platform.c` | `nwy_at_engine` 全局变量声明（第 61 行），`nwy_app_at_get_plat_at_channel()` 返回 `&nwy_at_engine`（第 609-612 行），`nwy_app_at_unsol_str()` 解引用 `*(unsigned int*)at_channel`（第 562 行） |
| `hop/telephony/nwy/src/nwy_interface_util.c` | `nwy_buf_echo_port()` 通过 `GET_ATP_INDEX(GET_AT_HANDLE(reqHandle))` 提取端口索引，调用 `nwy_get_at_rspcb_func(sAtpIndex)` 路由到端口输出回调（第 959-969 行） |
| `hop/telephony/atcmdsrv/inc/teldef.h` | `MAKE_AT_HANDLE`/`GET_ATP_INDEX`/`GET_AT_HANDLE` 句柄宏定义（第 475/492/494 行） |