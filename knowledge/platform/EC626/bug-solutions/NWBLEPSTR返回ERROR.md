# Bug分析：AT+NWBLEPSTR=0,0 返回 ERROR

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **工作项 ID** | NA |
| **平台** | EC626 |
| **模块** | BLE |
| **问题分类** | 状态机异常 |
| **症状关键词** | NWBLEPSTR返回ERROR, BLE芯片无响应, HCI超时, UART通信200ms超时 |
| **根因概述** | 执行NWBLEPSTR前未执行AT+NWBTBLEPWR=1初始化BLE芯片（或初始化不完整），YC1323芯片未就绪导致HCI命令无响应，read()在200ms超时后返回0触发ERROR |
| **调用链摘要** | AT_CmdFunc_NWY_NWBLEPSTR() → BT_AddBleService() → ble_yc_cmd() → UART send → UART read(200ms超时) → return 0 → ERROR |
| **检索关键词** | NWBLEPSTR, BLE ERROR, YC1323, HCI无响应, NWBTBLEPWR, BLE初始化, UART超时, BLE芯片未就绪 |

---

## 问题描述

| 项目 | 内容 |
|------|------|
| **故障现象** | 按流程执行 `NWBLEPSRV` → `NWBLEPCRT` → `NWBLEPSTR` 后，`AT+NWBLEPSTR=0,0` 返回 `ERROR` |
| **发生时间** | 2026-06-03 15:26:13 / 15:26:58（多次尝试均失败） |
| **日志来源** | `.spec/logs/20260603_104341.txt` + 用户 AT 命令日志 |

---

## 用户操作时序（完整 AT 日志）

```
15:25:15.832  AT+IVSN                        → +IVSN: N306-E08-STD-BZ_EA-009 → OK
15:25:17.851  AT+NWBLEPSRV=0,"0x2A98",2,1    → +NWBLEPSRV: 0 → OK ✓
15:25:55.396  AT+NWBLEPCRT=0,0,"0x9999",0,2,4 → +NWBLEPCRT: 0 → OK ✓
15:26:13.077  AT+NWBLEPSTR=0,0               → ERROR ✗ (~220ms)
15:26:24.029  AT+NWBLEPSTR=0,1               → ERROR ✗ (~29ms)
15:26:58.146  AT+NWBLEPSTR=0,0               → ERROR ✗ (~203ms)
```

**关键发现**：日志中**没有 `AT+NWBTBLEPWR=1` 命令**（BLE 电源初始化）。

---

## AP 日志关键证据

AP 日志在 `AT+NWBLEPSTR=0,0` 处理期间记录到：

```
15:26:58.133  ATCMD , decode AT: AT+NWBLEPSTR=0,0
15:26:58.134  send:0x4-3          ← HCI 命令头发送成功 (3 bytes, event=0x4=TX_DONE)
15:26:58.135  send:0x4-3          ← HCI 数据发送成功 (3 bytes: 0x02,0x98,0x2A)
              ...                  ← 等待 YC1323 响应 (200ms 超时)
15:26:58.335  AT CMD , RESP: ERROR  ← 超时无响应 → ERROR
```

**证据结论**：HCI 命令通过 UART 成功发送到 YC1323 芯片，但芯片在 200ms 内**无任何响应**。

---

## 根因定位

### NWBLEPSTR 错误路径分析

`AT_CmdFunc_NWY_NWBLEPSTR()` ([nwy_at_ble.c:607](../../PLAT/middleware/eigencomm/at/nwy_at/nwy_ble/src/nwy_at_ble.c#L607)) 的 ERROR 检查点：

| # | 检查点 | 代码行 | 条件 | srv_id=0 | srv_id=1 |
|---|--------|--------|------|----------|----------|
| 1 | app_id 参数 | L619-622 | 超出 0-1 | ✗ | ✗ |
| 2 | srv_id 参数 | L624-627 | 超出 0-1 | ✗ | ✗ |
| 3 | **服务 UUID 未创建** | L629-632 | `srv[].uuid == 0x00` | ✗ (已创建) | **✓ (未创建)** |
| 4 | 服务已启动 | L634-637 | `g_ble_add_srv[] == 1` | ✗ | — |
| 5 | **BT_AddBleService 返回 0** | **L644-647** | **start_handle == 0** | **✓ 根因** | — |

- `AT+NWBLEPSTR=0,1` → 29ms 返回 ERROR → **命中检查 #3**（srv_id=1 没有创建服务）
- `AT+NWBLEPSTR=0,0` → 200ms 返回 ERROR → **命中检查 #5**（BLE 芯片无响应）

### BT_AddBleService 调用链

```
AT_CmdFunc_NWY_NWBLEPSTR()                     [nwy_at_ble.c:642]
  └→ BT_AddBleService(payload, 3)              [ble_yc_cmd.c:63]
       ├→ ble_yc_cmd(s_ble_bus, 0x77, ...)     [ble_yc_cmd.c:36]
       │    ├→ bus->wake(TRUE)                  ← GPIO 唤醒 YC1323
       │    ├→ bus->send(msg, 3, 30)            ← 发送 HCI 头: {0x01, 0x77, 0x03}  ✓
       │    └→ bus->send(data, 3, 15)           ← 发送 UUID: {0x02, 0x98, 0x2A}     ✓
       └→ s_ble_bus->read(buff, 18, 200)        ← 等待 YC1323 响应...
              └→ xEventGroupWaitBits(..., 200ms) ← 超时，cnt=0
       → return 0                                ← start_handle = 0 → ERROR
```

### 根因：YC1323 BLE 芯片未响应 HCI 命令

**直接原因**：`BT_AddBleService()` 通过 UART 向 YC1323 发送 `HCI_CMD_ADD_SERVICE_UUID` (0x77) 命令，芯片在 200ms 超时内无响应，`read` 返回 0，触发 ERROR。

**根本原因**：BLE 芯片未正确初始化。日志中无 `AT+NWBTBLEPWR=1` 记录，BLE 子系统可能：
- 从未执行初始化（漏掉了 `NWBTBLEPWR=1` 步骤）
- 或之前初始化失败（固件补丁下载失败、芯片无响应等）

---

## 代码佐证：BLE 初始化流程

`AT+NWBTBLEPWR=1` 触发的初始化链路 ([nwy_at_ble.c:129](../../PLAT/middleware/eigencomm/at/nwy_at/nwy_ble/src/nwy_at_ble.c#L129))：

```
AT_CmdFunc_NWY_NWBTBLEPWR(mode=1)              [nwy_at_ble.c:112-132]
  └→ bleTaskInit()                              [bleapp.c:72]
       ├→ ec_uart_default(115200)               ← 初始化 BLE UART
       ├→ xEventGroupCreate()                   ← 创建事件组 gBleAppEvents
       ├→ osThreadNew(uartTask, ...)             ← 创建 UART 接收任务
       └→ osThreadNew(ycmdTask, ...)             ← 创建 YC1323 初始化任务
            └→ ble_yc_init(&yc_bus, &g_ble_cfg) [ble_yc_cmd.c:351]
                 ├→ s_ble_bus = bus              ← 设置全局 bus 指针
                 ├→ ble_yc_ask(data)             ← 询问芯片状态
                 ├→ ble_yc_patch(bus, ...)       ← 下载固件补丁到 YC1323
                 ├→ ble_yc_setName(...)          ← 设置 BLE 名称
                 ├→ ble_yc_setAddr(...)          ← 设置 BLE MAC
                 ├→ ble_yc_setFunc(...)          ← 设置功能模式
                 └→ bus->wake(FALSE)             ← 芯片进入低功耗
```

**没有 `NWBTBLEPWR=1` → `s_ble_bus` 可能未设置 → 芯片固件未加载 → 芯片无法响应 HCI 命令。**

AP 日志中 `send:0x4-3` 证明 `s_ble_bus` 已设置（否则会 HardFault），说明之前有过初始化。但芯片不响应说明初始化可能不完整或芯片状态异常。

---

## 两次 ERROR 的差异对比

| 命令 | 延迟 | 失败原因 | 错误路径 |
|------|------|----------|----------|
| `NWBLEPSTR=0,0` | ~200ms | BLE 芯片无响应 | L644: `BT_AddBleService() == 0` |
| `NWBLEPSTR=0,1` | ~29ms | 服务不存在 | L629: `srv[1].uuid == 0x00` |

200ms 延迟精确匹配 `s_ble_bus->read(buff, 18, 200)` 的超时值，确认是 UART 通信超时。

---

## 结论与建议

| 项目 | 内容 |
|------|------|
| **问题类型** | BLE 芯片未正确初始化导致 HCI 命令无响应 |
| **根因** | `AT+NWBLEPSTR=0,0` 前**未执行 `AT+NWBTBLEPWR=1`** 或 BLE 初始化不完整，YC1323 芯片未就绪 |
| **代码层面** | `NWBLEPSTR` 函数缺少 `g_nwy_ble_work_status` 检查，未能在发送 HCI 命令前判断 BLE 是否已初始化 |

### 修复建议

**用户操作层面**（立即可行）：
在 BLE 操作流程最前面增加电源初始化步骤：
```
AT+NWBTBLEPWR=1                  ← 1. 开启 BLE 电源（必须！）
AT+NWBLEPSRV=0,"0x2A98",2,1      ← 2. 创建服务
AT+NWBLEPCRT=0,0,"0x9999",0,2,4  ← 3. 创建特征值
AT+NWBLEPSTR=0,0                 ← 4. 启动服务
```

**代码优化建议**（可选）：
在 `AT_CmdFunc_NWY_NWBLEPSTR()` 入口处增加 BLE 工作状态检查，提供更明确的错误提示：
```c
// 建议在 nwy_at_ble.c:617 (AT_SET_REQ 分支) 处增加：
if(nwy_ble_status_get() == 0)
{
    ret = atcReply(atHandle, AT_RC_CME_ERROR, CME_OPERATION_NOT_SUPPORT, NULL);
    break;
}
```

---

*分析时间: 2026-06-03*
*分析工具: spec-bug-analyzer*