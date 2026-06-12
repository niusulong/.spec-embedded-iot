# AT+CTM2MREG 执行命令定时器未停止场景深度分析

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **平台** | EC626 |
| **模块** | CTM2M/LWM2M |
| **问题分类** | 时序竞争 |
| **症状关键词** | 定时器未停止, xQueueSend失败, pending队列溢出, 守护定时器超时 |
| **根因概述** | AT+CTM2MREG异步命令中xQueueSend返回值未检查，消息入队失败时定时器回调永不触发导致定时器直到超时才停止 |
| **调用链摘要** | atcProcAtCmd → atec_CTM2MREG_proc → ctlwm2m_client_reg → xQueueSend(返回值未检查) |
| **检索关键词** | CTM2MREG, 定时器未停止, xQueueSend, pending队列溢出, guard timer, 异步命令, AT命令超时 |

---

## 1. 问题背景

AT+CTM2MREG 执行命令采用异步处理机制，定时器启动后依赖异步回调来停止。本报告深入分析哪些场景会导致定时器未被正确停止。

---

## 2. AT+CTM2MREG 执行命令完整调用链

### 2.1 正常流程

```
atcProcAtCmd()
    │
    ├─→ atcStartAsynTimer()     // 启动 5000ms 守护定时器
    │
    └─→ atec_CTM2MREG_proc()    // AT 命令处理函数
            │
            ├─→ ctlwm2m_client_create()  // 创建任务
            │       │
            │       └─→ 返回 CTLWM2M_OK 或 CTLWM2M_TASK_ERR
            │
            └─→ ctlwm2m_client_reg()     // 发送注册消息
                    │
                    ├─→ xQueueSend()     // 发送消息到队列
                    │
                    └─→ return CMS_RET_SUCC  // ⚠️ 无论队列发送是否成功都返回成功
```

### 2.2 异步处理流程

```
ctlwm2m_atcmd_processing 任务
    │
    ├─→ xQueueReceive()         // 阻塞等待消息
    │
    └─→ MSG_CTLWM2M_REG 处理
            │
            ├─→ ctiot_at_reg()  // 执行注册
            │
            └─→ applSendCmsCnf() // 发送确认
                    │
                    └─→ ctiotRegCnf()
                            │
                            └─→ atcReply()  // 停止定时器
```

---

## 3. 定时器未停止的场景分析

### 3.1 🔴 场景一：xQueueSend 失败（关键 BUG）

**代码位置：** [at_ctlwm2m_task.c:76-88](middleware/eigencomm/at/atentity/src/at_ctlwm2m_task.c#L76-L88)

```c
CmsRetId ctlwm2m_client_reg(UINT32 reqHandle)
{
    CTLWM2M_ATCMD_Q_MSG ctMsg;
    ECOMM_TRACE(UNILOG_CTLWM2M, ctlwm2m_client_reg_1, P_INFO, 0, "TO SEND AT+CTM2MREG");

    memset(&ctMsg, 0, sizeof(ctMsg));
    ctMsg.cmd_type = MSG_CTLWM2M_REG;
    ctMsg.reqhandle = reqHandle;

    xQueueSend(ctlwm2m_atcmd_handle, &ctMsg, CTLWM2M_MSG_TIMEOUT);  // 返回值未检查！

    return CMS_RET_SUCC;  // ⚠️ 无论发送成功与否都返回成功
}
```

**问题分析：**

| 情况 | xQueueSend 返回值 | ctlwm2m_client_reg 返回值 | 定时器状态 |
|------|-------------------|---------------------------|------------|
| 发送成功 | pdTRUE | CMS_RET_SUCC | 依赖后续回调停止 |
| 队列满超时 | errQUEUE_EMPTY | CMS_RET_SUCC | **永远不会停止** |
| 其他失败 | 其他错误码 | CMS_RET_SUCC | **永远不会停止** |

**根因：** `xQueueSend()` 的返回值完全被忽略，函数总是返回 `CMS_RET_SUCC`。

**影响：**
- `atcProcAtCmd()` 收到 `CMS_RET_SUCC` 后认为命令处理成功
- 定时器不会被立即停止（等待异步回调）
- 但消息未入队，异步回调永远不会发生
- 定时器直到超时（5000ms）才会触发 `atcAsynTimerExpiry()`

---

### 3.2 🔴 场景二：队列超时配置不合理

**代码位置：** [at_ctlwm2m_task.h:32](middleware/eigencomm/at/atentity/inc/at_ctlwm2m_task.h#L32)

```c
#define CTLWM2M_MSG_TIMEOUT 500  // 500 ticks
```

**问题分析：**

- 队列发送超时设置为 500 ticks
- 如果 `ctlwm2m_atcmd_processing` 任务处理缓慢或阻塞
- 队列可能被填满，导致新消息无法入队

**队列满的场景：**
1. `ctlwm2m_atcmd_processing` 任务优先级过低，长时间得不到调度
2. `ctiot_at_reg()` 执行时间过长，阻塞任务处理
3. 系统负载过高，任务切换延迟

---

### 3.3 🟡 场景三：ctlwm2m_atcmd_processing 任务未运行

**代码位置：** [at_ctlwm2m_task.c:280-302](middleware/eigencomm/at/atentity/src/at_ctlwm2m_task.c#L280-L302)

```c
int ctlwm2m_client_create(void)
{
    if(ctlwm2m_atcmd_task_flag == CTLWM2M_TASK_STAT_NONE)
    {
        ctlwm2m_atcmd_task_flag = CTLWM2M_TASK_STAT_CREATE;
        if(ctlwm2m_handleAT_task_Init() != CTLWM2M_OK)
        {
            return CTLWM2M_TASK_ERR;  // 任务创建失败
        }
    }
    return CTLWM2M_OK;
}
```

**问题分析：**

如果 `ctlwm2m_client_create()` 成功（任务已创建），但任务后续因异常退出或被删除：
- 队列中的消息无人处理
- `applSendCmsCnf()` 永远不会被调用
- 定时器永远不会停止

---

### 3.4 🟡 场景四：applSendCmsCnf 发送失败

**调用链：**
```
applSendCmsCnf()
    → 将确认消息发送回 AT 解码器
    → 触发 ctiotRegCnf()
    → atcReply()
```

**问题分析：**

如果 `applSendCmsCnf()` 内部发送失败（内存不足、消息队列满等）：
- `ctiotRegCnf()` 不会被调用
- `atcReply()` 不会被调用
- 定时器不会被停止

---

### 3.5 🟡 场景五：hasThread 检查失败导致确认未发送

**代码位置：** [at_ctlwm2m_task.c:163-171](middleware/eigencomm/at/atentity/src/at_ctlwm2m_task.c#L163-L171)

```c
while(!pContext->send_thread_run && count < 39 && msg_type != MSG_CTLWM2M_REG){
    ECOMM_TRACE(UNILOG_CTLWM2M, ctlwm2m_atcmd_process_0, P_INFO, 1, "wait send thread start count:%d", count);
    osDelay(1000);  // wait 1000ms
    count += 1;
}
if(count < 39){
    hasThread = TRUE;
}
```

**问题分析：**

对于 `MSG_CTLWM2M_REG` 消息类型，会跳过 `send_thread_run` 检查（`msg_type != MSG_CTLWM2M_REG` 条件）。

但是，如果 `ctiot_at_reg()` 内部失败但返回了非 `CTLWM2M_OK` 的错误码：
```c
ret = ctiot_at_reg(pContext);
if(ret != CTLWM2M_OK){
    ctCnfMsg.ret = ret;
    applSendCmsCnf(atHandle, APPL_RET_FAIL, APPL_CTLWM2M, APPL_CT_REG_CNF, primSize, &ctCnfMsg);
}
```

此时会发送失败确认，`ctiotRegCnf()` 会被调用，定时器会被停止。

**结论：** 此场景不会导致定时器未停止。

---

## 4. 问题场景汇总表

| 场景 | 严重程度 | 发生条件 | 定时器状态 | 根因 |
|------|----------|----------|------------|------|
| xQueueSend 失败 | 🔴 高 | 队列满或超时 | 永不停止 | 返回值未检查 |
| 队列超时配置 | 🔴 高 | 任务处理慢 | 永不停止 | 500 ticks 可能不足 |
| 任务未运行 | 🟡 中 | 任务异常退出 | 永不停止 | 缺乏任务健康检查 |
| applSendCmsCnf 失败 | 🟡 中 | 内存/队列问题 | 永不停止 | 缺乏错误处理 |
| 系统负载高 | 🟡 中 | CPU 占用高 | 延迟停止 | 任务调度延迟 |

---

## 5. 定时器未停止的最终表现

### 5.1 定时器超时处理

**代码位置：** [atc_decoder.c:5066-5091](middleware/eigencomm/at/atdecoder/src/atc_decoder.c#L5066-L5091)

```c
void atcAsynTimerExpiry(UINT16 timeId)
{
    ECOMM_TRACE(UNILOG_ATCMD_PARSER, atcAsynTimerExpiry_warning_1, P_WARNING, 2,
                "AT CMD. channId: %d, tid: %d, guard timer expiry", chanId, tid);

    // ...
    atcSendResultCode(pAtChanEty, AT_RC_ERROR, PNULL);  // 发送 ERROR
    // ...
}
```

**表现：**
1. 定时器 5000ms 后超时
2. 发送 ERROR 给用户
3. 日志显示 "guard timer expiry"

### 5.2 Pending 队列累积

**关键代码：** [atc_decoder.c](middleware/eigencomm/at/atdecoder/src/atc_decoder.c)

```c
BOOL atcAnyPendingAt(AtChanEntityP pAtChanEty)
{
    AtInputInfo *pAtInfo = &(pAtChanEty->atInputInfo);

    return (pAtInfo->input.cmdInput.pLine != NULL ||
            OsaTimerIsRunning(pAtChanEty->asynTimer));  // 定时器运行中返回 TRUE
}
```

**问题链条：**

```
定时器运行中 (asynTimer != OSA_TIMER_NOT_CREATE)
    ↓
atcAnyPendingAt() 返回 TRUE
    ↓
新数据被放入 pending 队列
    ↓
如果定时器超时后未正确清理
    ↓
pending 队列持续累积
    ↓
"too much pending AT input"
```

---

## 6. 与日志现象的对应分析

### 6.1 日志关键时间点

| 时间 | 事件 | 分析 |
|------|------|------|
| 18:18:34.842 | AT+CTM2MREG 返回 OK | 执行命令完成 |
| 18:18:35.280 | AT+CTM2MREG? 返回 OK | 查询命令完成 |
| 18:18:36.268 | AT+CTM2MREG? 返回 OK | 查询命令完成 |
| 18:18:37.309 | AT+CTM2MREG? 返回 OK | 查询命令完成 |
| 18:18:38.256 | AT+CTM2MSEND ERROR | 触发异常 |

### 6.2 关键问题

**为什么 AT+CTM2MREG? 查询命令的 OK 后没有触发 SIG_AT_CMD_CONTINUE_REQ？**

根据代码逻辑，`atcReply()` 会检查 `atcAnyPendingAt()`：
- 如果定时器仍在运行，返回 TRUE，发送 SIG_AT_CMD_CONTINUE_REQ
- 如果定时器已停止，且 pLine == NULL，返回 FALSE

**推测：**
在 18:18:37.309 的 AT+CTM2MREG? 查询命令返回 OK 时：
1. 定时器已经停止（之前的执行命令 OK 时已停止）
2. 没有新的 pending 数据
3. `atcAnyPendingAt()` 返回 FALSE
4. 不发送 SIG_AT_CMD_CONTINUE_REQ

**真正的问题发生在：**
18:18:39.258 开始接收长数据时，系统逐字节处理导致 pending 队列溢出。

---

## 7. 根本原因总结

### 7.1 直接原因

**pending 队列溢出**：UART 数据逐字节接收，每个字节创建一个 pending 节点，快速达到上限 64。

### 7.2 潜在的定时器问题

虽然本次问题不是定时器未停止导致，但代码中存在以下隐患：

1. **xQueueSend 返回值未检查**：这是代码缺陷，虽然在本案例中不是根因，但可能导致其他场景下的问题
2. **异步回调链过长**：任何一环失败都会导致定时器无法停止
3. **缺乏超时后的清理机制**：定时器超时后，pending 队列状态可能不正确

---

## 8. 修复建议

### 8.1 修复 xQueueSend 返回值检查（推荐）

```c
CmsRetId ctlwm2m_client_reg(UINT32 reqHandle)
{
    CTLWM2M_ATCMD_Q_MSG ctMsg;
    BaseType_t queueRet;

    memset(&ctMsg, 0, sizeof(ctMsg));
    ctMsg.cmd_type = MSG_CTLWM2M_REG;
    ctMsg.reqhandle = reqHandle;

    queueRet = xQueueSend(ctlwm2m_atcmd_handle, &ctMsg, CTLWM2M_MSG_TIMEOUT);

    if (queueRet != pdTRUE)
    {
        ECOMM_TRACE(UNILOG_CTLWM2M, ctlwm2m_client_reg_err, P_ERROR, 1,
                    "Queue send failed: %d", queueRet);
        return CMS_FAIL;  // 让调用者知道失败了，定时器会被立即停止
    }

    return CMS_RET_SUCC;
}
```

### 8.2 增加队列大小

检查队列深度配置，适当增加队列大小以应对高负载场景。

### 8.3 增加任务健康检查

定期检查 `ctlwm2m_atcmd_processing` 任务状态，异常时自动恢复。

---

## 9. 相关文件清单

| 文件路径 | 说明 |
|----------|------|
| [at_ctlwm2m_task.c](middleware/eigencomm/at/atentity/src/at_ctlwm2m_task.c) | CTM2M 任务处理 |
| [at_ctlwm2m_task.h](middleware/eigencomm/at/atentity/inc/at_ctlwm2m_task.h) | 任务头文件 |
| [atec_ctlwm2m1_5.c](middleware/eigencomm/at/atcust/src/atec_ctlwm2m1_5.c) | CTM2MREG 命令处理 |
| [atec_ctlwm2m_cnf_ind.c](middleware/eigencomm/at/atcust/src/cnfind/atec_ctlwm2m_cnf_ind.c) | 异步回调处理 |
| [atc_decoder.c](middleware/eigencomm/at/atdecoder/src/atc_decoder.c) | AT 解码器和定时器管理 |
| [atc_reply.c](middleware/eigencomm/at/atreply/src/atc_reply.c) | AT 响应处理 |