# AT命令 "too much pending AT input" 问题分析报告

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **平台** | EC626 |
| **模块** | UART/AT解码器 |
| **问题分类** | 缓冲区溢出 |
| **症状关键词** | too much pending AT input, 逐字节接收, pending节点溢出, Doze低功耗, 长数据AT命令 |
| **根因概述** | UART长数据逐字节接收，每个字节触发独立中断和信号后系统立即进入Doze，pending队列节点快速累积超过上限64导致溢出丢弃 |
| **调用链摘要** | UART DMA中断 → SIG_AT_CMD_STR_REQ → pending队列累积 → 队列溢出(>64) → 丢弃所有数据 |
| **检索关键词** | pending节点溢出, UART逐字节接收, too much pending AT input, AT解码器pending, Doze模式, CTM2MSEND, DMA接收缓冲区, pendingNodeNum |

---

## 1. 问题现象

### 1.1 错误日志
```
AT CMD, too much pending AT input, pendingLen: 65 / pendingNodeNum: 65, discard all
```

### 1.2 测试记录

```
[18:18:34.781] AT+CTM2MREG
[18:18:34.842] OK                          ← AT+CTM2MREG 返回成功

[18:18:35.280] AT+CTM2MREG?
               +CTM2MREG: 2
               OK

[18:18:36.268] AT+CTM2MREG?
               +CTM2MREG: 2
               OK

[18:18:37.309] AT+CTM2MREG?
               +CTM2MREG: 1
               OK

[18:18:38.256] AT+CTM2MSEND=5E008E0001... (长数据)
               ERROR                      ← 触发异常
```

### 1.3 问题特征
- AT+CTM2MREG 已返回 OK
- 后续短指令正常执行
- 发送长数据指令时触发异常
- 问题可复现

---

## 2. 根因分析

### 2.1 日志时间线关键发现

通过详细分析日志文件 `.spec/logs/20260320_094407.txt`，发现以下关键时间线：

| 时间 | 事件 | 关键观察 |
|------|------|---------|
| 18:18:37.347 | AT+CTM2MREG? 启动定时器 | 5000ms |
| 18:18:37.348 | 返回 OK | 定时器应该被停止 |
| 18:18:37.350 | 接收到 `\n` | 只是换行符 |
| 18:18:37.364-39.258 | 系统持续 Doze | 没有新 AT 数据 |
| **18:18:39.258** | 接收到 'A' (0x41) | 长数据开始到达 |
| 18:18:39.258-460 | 大量 UART 数据 | **逐字节接收** |
| 18:18:39.460 | "too much pending" | pending 累积到 65 |

### 2.2 **真正的根因：UART 数据逐字节接收 + 系统频繁进入 Doze**

**问题场景：**

```
UART 接收流程：
18:18:39.258: 接收 'A' → SIG_AT_CMD_STR_REQ → Enter Doze
18:18:39.275: 接收 'T' → SIG_AT_CMD_STR_REQ → Enter Doze
18:18:39.275: 接收 '+' → SIG_AT_CMD_STR_REQ → Enter Doze
... (每个字节触发一次中断和信号)
```

**关键问题：**

1. **UART 接收方式问题**：
   - 长数据（如 AT+CTM2MSEND=...）通过 UART 发送
   - 但系统逐字节接收，每个字节触发一次 `SIG_AT_CMD_STR_REQ` 信号
   - 每次信号处理后，系统立即进入 Doze（低功耗）模式

2. **pending 队列快速累积**：
   - 每个字节被单独放入 pending 队列
   - AT 解码器无法及时处理（因为系统频繁 Doze）
   - pending 节点数量快速达到上限 64

3. **没有看到 "one AT is ongoing" 日志的原因**：
   - 在 18:18:39.258 第一个字节到达时，上一个 AT（AT+CTM2MREG?）的定时器已被正确停止
   - 但新数据是逐字节到达的，不足以构成完整的 AT 命令行
   - 因此解码器只是将数据放入 pending，而没有进入 "AT ongoing" 状态

### 2.3 定时器相关代码分析（非根因，但需澄清）

**代码位置：** `middleware/eigencomm/at/atdecoder/src/atc_decoder.c:4978-5000`

```c
void atcStopAsynTimer(AtChanEntityP pAtChanEty, UINT8 tid)
{
    OsaCheck(pAtChanEty != PNULL && tid <= AT_MAX_ASYN_GUARD_TIMER_TID, pAtChanEty, tid, 0);

    OsaDebugBegin(pAtChanEty->asynTimer != OSA_TIMER_NOT_CREATE && pAtChanEty->curTid == tid,
                  tid, pAtChanEty->curTid, pAtChanEty->asynTimer);
    return;
    OsaDebugEnd();

    // 定时器停止和删除代码...
}
```

**宏展开后：**

```c
if (!(pAtChanEty->asynTimer != OSA_TIMER_NOT_CREATE && pAtChanEty->curTid == tid))
{
    OsaCheckDebugFalse(...);
    return;  // 断言失败时提前返回
}
// 断言成功时继续执行定时器停止和删除
```

**结论：`atcStopAsynTimer()` 函数设计正确**，断言成功时定时器会被正确停止和删除。

---

## 3. 问题链条

```
用户发送长数据 AT+CTM2MSEND=...
    ↓
UART 开始逐字节接收数据
    ↓
每个字节触发 DMA 中断
    ↓
发送 SIG_AT_CMD_STR_REQ 信号
    ↓
信号处理函数将数据放入 pending 队列
    ↓
系统进入 Doze（低功耗）模式
    ↓
pending 节点数量持续累积
    ↓
pendingNodeNum > 64
    ↓
报错 "too much pending AT input"
```

---

## 4. 根本原因总结

### 4.1 直接原因

**UART 数据接收方式问题**：
- 长数据被逐字节接收，每个字节创建一个 pending 节点
- 导致 pending 队列快速溢出

### 4.2 潜在原因

1. **UART 接收缓冲区配置问题**：
   - 可能 DMA 接收缓冲区配置过小
   - 或者 UART 超时设置不当

2. **系统调度问题**：
   - 系统频繁进入 Doze 模式
   - AT 解码任务得不到足够的 CPU 时间

3. **pending 队列管理策略问题**：
   - 没有合并连续字节到同一个节点的机制
   - pending 节点上限（64）可能过小

---

## 5. 解决方案

### 5.1 方案一：优化 UART 接收方式（根本解决）

**检查 UART DMA 配置**：
- 增大 DMA 接收缓冲区
- 配置合适的 UART 接收超时
- 减少中断触发频率

### 5.2 方案二：优化 pending 队列管理

**修改位置：** `middleware/eigencomm/at/atdecoder/src/atc_decoder.c`

**优化策略：**
1. 合并连续字节到同一个 pending 节点
2. 在 pending 队列累积到一定数量时，提前处理部分数据

### 5.3 方案三：增大 pending 节点上限（缓解措施）

**修改位置：** `middleware/eigencomm/at/atdecoder/inc/atc_decoder.h`

```c
#define AT_CMD_MAX_PENDING_NODE_NUM  256  // 从 64 增大到 256
```

### 5.4 方案四：优化低功耗策略

**修改位置：** 电源管理模块

**优化策略：**
- 在有 AT 数据待处理时，推迟进入 Doze
- 减少 Doze 模式的进入频率

### 5.5 推荐方案

| 优先级 | 方案 | 效果 | 风险 |
|--------|------|------|------|
| **高** | 方案一 | 根本解决 | 需要硬件测试 |
| **高** | 方案二 | 根本解决 | 需要代码修改 |
| **中** | 方案三 | 缓解 | 低 |
| **中** | 方案四 | 改善响应 | 需要功耗测试 |

---

## 6. 验证方法

### 6.1 调试验证

1. 增加 UART 接收日志：
   ```c
   ECOMM_TRACE(UNILOG_ATCMD_PARSER, uart_rx_debug, P_INFO, 2,
               "UART RX: len=%d, data=%x", rxLen, data);
   ```

2. 检查 DMA 配置：
   - 缓冲区大小
   - 超时设置
   - 中断触发条件

### 6.2 回归测试

1. 发送长数据 AT 命令
2. 检查 pending 队列状态
3. 验证系统响应时间

---

## 7. 相关文件清单

| 文件路径 | 说明 |
|----------|------|
| `middleware/eigencomm/at/atdecoder/src/atc_decoder.c` | AT 解码和 pending 队列管理 |
| `middleware/eigencomm/at/atdecoder/inc/atc_decoder.h` | 配置常量定义 |
| `middleware/eigencomm/at/atentity/src/at_uart.c` | UART 接收处理（如存在） |

---

## 8. 总结

### 8.1 问题本质

**UART 数据逐字节接收导致 pending 队列溢出**，而非定时器停止问题。

### 8.2 关键证据

1. 日志显示每个字节触发独立的 `SIG_AT_CMD_STR_REQ` 信号
2. 接收数据为 'A', 'T', '+', '0', '7', '4', '6', '0'...（AT 命令开头）
3. 没有看到 "one AT is ongoing" 日志，说明定时器已正确停止
4. pending 累积到 65 个节点（超过上限 64）

### 8.3 修复方向

重点关注 **UART 接收优化** 和 **pending 队列管理优化**，而非定时器相关问题。