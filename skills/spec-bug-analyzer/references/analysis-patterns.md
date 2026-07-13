# 常见问题模式

> 用于 Step 4 根因定位时快速对号入座。模式只是方向提示，定位后仍需 Step 5 代码交叉验证，不要把模式当结论。

## 通用模式

| 问题模式 | 关键日志特征 | 根因方向 |
|----------|--------------|----------|
| **缓冲区溢出/不足** | `BUFFER_TOO_SMALL`、`overflow`、`EXCEED`、`truncated` | 缓冲区配置过小 / 越界写 |
| **超时问题** | `TIMEOUT`、`timeout`、长时间无响应后断开 | 网络状态差 / 任务阻塞 / 超时配置过短 |
| **参数错误** | `PARAM_ERROR`、`INVALID_PARAM`、`illegal`、`bad value` | 输入校验缺失 / 协议理解偏差 / 单位换算 |
| **状态机异常** | `state` 跳转异常、状态不一致、重复进入某态 | 逻辑错误 / 并发竞争 / 事件丢失 |
| **资源耗尽** | `EXHAUST`、`NO_MEMORY`、`NO_HANDLE`、`alloc fail` | 资源泄漏 / 未释放 / 配置上限过低 |
| **TLS/SSL 失败** | `handshake`、`alert`、`certificate`、`-0x7xxx` 错误码 | 证书 / 加密套件 / 版本兼容 / 系统时间未同步 |
| **AT 命令错误** | `ERROR`、`CME ERROR`、`CMS ERROR` | 命令格式 / 参数越界 / 模块状态不满足前置 |
| **断连/重连** | `disconn`、`closed`、`reconnect`、链路状态回落 | 心跳超时 / 对端主动关闭 / 底层链路掉线 |

## EC 平台（Cortex-M + FreeRTOS）专属

| 现象 | 日志特征 | 根因方向 |
|------|----------|----------|
| **memp 池耗尽** | `memp_malloc fail`、`LWIP OOM` | 某类 pbuf/socket 未释放，累积耗尽；查 `trace_node` 分配追踪 |
| **FreeRTOS 堆耗尽/泄漏** | `freeHeap` 持续下降、`pvPort_malloc fail` | 大块未释放；查 `trace_node`、任务栈占用 |
| **栈溢出** | HardFault、`stack overflow`、栈水位检查告警 | 任务栈配置过小 / 递归或大局部变量 |
| **ASSERT 触发** | 含 `文件名:行号` 的 `ASSERT` | 直接定位断言点，分析为何前置条件不满足 |
| **看门狗复位** | `WDT`、`IWDT`、长时间无喂狗 | 某任务死循环 / 被高优先级任务饿死 / 中断中阻塞 |
| **excep_store 异常** | `EXC_` 前导、HardFault 寄存器组 | 转交 `spec-ec-dump-analyzer` 解析调用链 |

## ASR 平台（Cortex-R + ThreadX）专属

| 现象 | 日志特征 | 根因方向 |
|------|----------|----------|
| **DataAbort** | `DataAbort`、访问非法地址 | 野指针 / 释放后使用 / PSRAM 数据损坏 |
| **PSRAM 代码完整性失败** | PSRAM 区执行校验异常 | DDR/PSRAM 数据被踩 / 时序问题 |
| **ThreadX 阻塞** | `tx_thread` 长时间挂起 | 信号量/队列等待未唤醒、优先级反转 |
| **看门狗超时** | `WDT`、`WdTimeout` | ISR 或线程死循环、调度被阻塞 |
| **crash 寄存器组** | PC/LR/SP 地址、`0x7e...` | 转交 `spec-asr1603-dump-analyzer` 反汇编解码 |

## 搜索技巧

- **先定位报错时刻**：用 `stats` 看错误关键字的时间分布，锁定首次出现的时间点，再用 `search` 带上下文查看。
- **沿时序向上追溯**：报错点向上找前一条相关日志（请求/事件），逐条回溯触发源，不要只盯报错那一行。
- **对比基准**：正常/异常日志都有时，先用 `compare` 自动 diff，人眼聚焦差异点（见 `contrast-analysis-guide.md`）。
- **错误码先查定义**：报错码（如 `-0x7200`）先查对应头文件/枚举定义再推理含义，不要猜。
- **关注资源趋势**：内存/handle/句柄类问题，对比问题发生前后占用趋势，判断是泄漏还是突发耗尽。
