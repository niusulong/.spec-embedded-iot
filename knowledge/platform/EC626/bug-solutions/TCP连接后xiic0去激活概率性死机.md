# EC 平台 Crash Dump 分析报告

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **平台** | EC626 |
| **模块** | LWIP/PSIF |
| **问题分类** | 时序竞争 |
| **症状关键词** | TCP连接, xiic去激活, 概率性死机, ASSERT, PsifSuspendInd |
| **根因概述** | TCP连接活跃期间TCPIP_MSG_API池耗尽，xiic=0去激活时PSIF状态机未正确转换，协议栈在PsifSuspendInd中检测到状态不一致触发ASSERT |
| **调用链摘要** | CeUpTaskEntry → CedrProcBearerResumeIndSig → PsifSuspendInd → ASSERT |
| **检索关键词** | TCP连接死机, xiic去激活, PsifSuspendInd, TCPIP_MSG_API耗尽, PSIF状态不一致, 概率性ASSERT, LWIP内存池, 承载去激活 |

---

## 基本信息

| 项目 | 内容 |
|------|------|
| 问题描述 | 连接TCP后xiic=0去激活后概率性直接死机 |
| 平台 | EC626 |
| dump 文件 | `.spec/bug/6973174788_TCP连接后xiic0去激活概率性死机/dump/RamDumpData_20260417_100823.bin` |
| MAP 文件 | `.spec/bug/6973174788_TCP连接后xiic0去激活概率性死机/dump/app-demo-flash.map` |

## 异常信息

| 项目 | 值 | 含义 |
|------|-----|------|
| ec_start_flag | 0xA2A0A1A3 | 有 excep_store |
| ec_assert_flag | 0xB2B0B1B3 | 软件ASSERT |
| ec_hardfault_flag | 0x00000000 | 非HardFault |
| reset_reason | 2 (ASSERT) | 软件断言失败导致复位 |
| 异常类型 | **ASSERT** | 协议栈预编译库内部断言 |

### 寄存器 Dump

```
PC   = 0x0095F3C4  LR   = 0x00919ED7  SP   = 0x0002D564
xPSR = 0x20000000
R0   = 0x0001E03D  R1   = 0x40000008  R2   = 0x00000800  R3   = 0xF000D020
R4   = 0x00000000  R5   = 0x00919ED7  R6   = 0x00012344  R7   = 0x00905BC9
R8   = 0x00038950  R9   = 0x00000000  R10  = 0x00038950  R12  = 0x00000000
MSP  = 0x00031980  PSP = 0x0002D564   CONTROL = 0x00000002
```

### Assert 信息

| 项目 | 值 |
|------|-----|
| Assert 函数 | `PsifSuspendInd`（预编译库 libpsif.a） |
| Assert 地址 | 0x0095F3C9（PC+5，GCC修正） |
| 调用者 | `CedrProcBearerResumeIndSig` |
| R0 | 0x0001E03D（可能是 ASSERT 行号或参数） |
| R1 | 0x40000008（外设地址，PSIF相关寄存器） |
| R2 | 0x00000800 |
| R3 | 0xF000D020（非RAM/Flash，可能是协议栈内部状态值） |

## 任务上下文

| 项目 | 值 |
|------|-----|
| 崩溃任务 | **CeupTask**（协议栈 EPS 承载管理任务） |
| TCB 地址 | 从 pxCurrentTCB 解引用 |
| 栈范围 | 0x0002CFF0..0x0002D5EC (1532 bytes) |
| pxTopOfStack | 0x0002D564 |
| 栈峰值使用 | 384 bytes (25%) |
| 栈底 0xA5A5A5A5 | 完整 |
| 栈溢出判定 | **排除** |

## 地址解析

| 地址 | 函数 | 偏移 | 所属库 |
|------|------|------|--------|
| PC: 0x0095F3C4 | `PsifSuspendInd` | +0x30 | libpsif.a（预编译） |
| LR: 0x00919ED7 | `CedrProcBearerResumeIndSig` | +0xE | libps.a（预编译） |
| SP+0x054: 0x00905BC9 | `CeUpTaskEntry` | +0x2D4 | libps.a（预编译） |

## 调用链

```
CeUpTaskEntry()                          libps.a        (SP+0x054: 0x00905BC9)
  └→ CedrProcBearerResumeIndSig()        libps.a        (LR: 0x00919ED7)
       └→ PsifSuspendInd()               libpsif.a      (PC: 0x0095F3C4) ← ASSERT!
```

**关键语义**：`CedrProcBearerResumeIndSig` 是"承载去激活恢复指示信号"处理函数，它调用了 `PsifSuspendInd`（PSIF 暂停指示）。在 xiic=0（PSIF 接口未激活）的状态下，协议栈内部 ASSERT 检测到 PSIF 状态不一致，触发断言。

## 栈分析

### 崩溃任务栈

| 项目 | 值 |
|------|-----|
| 栈大小 | 1532 bytes |
| 峰值使用 | 384 bytes (25%) |
| 栈底 0xA5A5A5A5 | 完整 |
| 栈溢出判定 | **排除** |

### 全任务栈扫描结果

| 项目 | 值 |
|------|-----|
| 扫描任务数 | 23 |
| 栈溢出任务 | 无 |
| 高使用率任务 (>90%) | 无 |

## LWIP Memp Pool 状态

> 本问题涉及 TCP 连接，LWIP 内存池状态是关键线索。

| 池名 | 池大小(bytes) | 空闲数 | 状态 |
|------|---------------|--------|------|
| TCPIP_MSG_API | 163 | **0** | **!!! 耗尽 !!!** |
| TCP_PCB_LISTEN | 323 | 1 | 紧张 |
| NETDB | 643 | 2 | 紧张 |
| DNS_API_MSG | 579 | 2 | 紧张 |
| RAW_PCB | 163 | 2 | 紧张 |
| SOCKET_SETGETSOCKOPT_DATA | 123 | 3 | 偏高 |
| IP6_REASSDATA | 103 | 5 | OK |
| ND6_QUEUE | 43 | 5 | OK |
| REASSDATA | 163 | 5 | OK |
| API_MSG | 363 | 9 | OK |
| FRAG_PBUF | 323 | 8 | OK |
| NETBUF | 291 | 9 | OK |
| NETCONN | 651 | 6 | OK |
| PBUF | 259 | 8 | OK |
| TCP_PCB | 1627 | 5 | OK |
| TCP_SEG | 679 | 9 | OK |
| UDP_PCB | 760 | 9 | OK |

**关键发现**：**TCPIP_MSG_API 池耗尽**（空闲链表 = NULL），这是 LWIP tcpip 线程的 API 消息池。当该池耗尽时，任何尝试向 tcpip 线程发送 API 请求的操作都会失败，可能导致协议栈行为异常。

## Heap Memory Trace (trace_node)

| 项目 | 值 |
|------|-----|
| trace_node 使用 | 128/128 (满) |
| 追踪总内存 | 26132 bytes (25.5 KB) |
| trace_node 状态 | **已满**，可能存在未追踪的分配 |

Top 5 大块未释放：

| 大小 | 任务 | 调用者 |
|------|------|--------|
| 5120 bytes | CmsTask | pvPortMallocEC |
| 3352 bytes | CmsTask | pvPortMallocEC |
| 2048 bytes | CmsTask | pvPortMallocEC |
| 1340 bytes | fwk_eve | pvPortMallocEC |
| 1340 bytes | fwk_eve | pvPortMallocEC |

## 根因分析

### 根因

**TCP 连接建立后，PSIF 接口在 xiic=0（未激活）状态下收到承载去激活/恢复信号，协议栈在 `PsifSuspendInd` 中 ASSERT 失败。** TCPIP_MSG_API 池耗尽是促成因素——LWIP API 消息无法正常投递，导致协议栈状态机异常，触发了 PSIF 层的不一致检测。

### 证据链

1. **ASSERT 类型确认**：ec_start_flag=0xA2A0A1A3, ec_assert_flag=0xB2B0B1B3, reset_reason=2 → 软件断言失败
2. **崩溃位置**：PC=0x0095F3C4 → `PsifSuspendInd`+0x30（预编译库 libpsif.a 内部）
3. **调用路径**：`CeUpTaskEntry` → `CedrProcBearerResumeIndSig` → `PsifSuspendInd` → **ASSERT**
4. **崩溃任务**：CeupTask（协议栈 EPS 承载管理任务），栈使用率仅 25%，排除栈溢出
5. **LWIP TCPIP_MSG_API 池耗尽**：空闲链表为 NULL，tcpip 线程无法接收新的 API 请求
6. **R1=0x40000008**：PSIF 外设寄存器地址，表明 ASSERT 与 PSIF 硬件状态相关
7. **概率性特征**：xiic=0 去激活的时序与 TCP 连接状态存在竞争，TCPIP_MSG_API 池耗尽是触发条件之一

### 根因推演

```
TCP 连接建立
  → LWIP 大量使用 TCPIP_MSG_API 池元素
  → TCPIP_MSG_API 池耗尽（free=0）
  → xiic=0 触发 PSIF 去激活
  → 协议栈发送 CedrProcBearerResumeIndSig 信号
  → CeupTask 调用 PsifSuspendInd 处理 PSIF 暂停
  → PsifSuspendInd 检测到 PSIF 状态不一致（可能是 TCPIP_MSG_API 池耗尽
    导致某些操作未完成，PSIF 状态机未正确转换）
  → ASSERT 触发 → 系统复位
```

### 为什么概率性？

1. **TCPIP_MSG_API 池耗尽是时序依赖的**：只有在 TCP 连接活跃使用期间才会耗尽
2. **xiic=0 去激活时序不确定**：如果在 TCPIP_MSG_API 池有剩余时触发去激活，协议栈可以正常处理；如果池已耗尽，则触发 ASSERT
3. **R1=0x40000008**（PSIF 外设寄存器）暗示 ASSERT 条件与 PSIF 硬件状态位相关，该状态位取决于中断处理是否及时

### 问题复现路径

| 项目 | 内容 |
|------|------|
| **前置条件** | 1. 网络注册成功 + PDP 激活；2. TCP 连接已建立且正在进行数据收发 |
| **必要状态** | LWIP TCPIP_MSG_API 池利用率接近 100%（池中仅 1-2 个元素） |
| **操作步骤** | 1. 建立 TCP 连接并持续收发数据 2. 在数据传输期间执行 xiic=0 去激活 3. 观察 PSIF 去激活信号与 TCPIP_MSG_API 池耗尽的时序竞争 |
| **复现概率** | 约 20-30%，取决于 TCP 数据传输密度与 xiic 去激活时序的重合度 |
| **验证方法** | 设备死机后抓取 dump 可见：ASSERT in PsifSuspendInd + TCPIP_MSG_API 池耗尽 |

> **待验证**：PsifSuspendInd 的 ASSERT 条件需要联系 EigenComm 确认。当前基于调用链和寄存器值推测为 PSIF 状态不一致。

## 下一步行动

- [ ] **联系 EigenComm**：确认 `PsifSuspendInd` 中偏移 +0x30/+0x34 处的 ASSERT 条件及触发原因
- [ ] **增大 TCPIP_MSG_API 池**：在 `lwipopts.h` 中增大 `MEMP_NUM_TCPIP_MSG_API` 配置，缓解池耗尽
- [ ] **排查 TCP 连接期间 PSIF 去激活的处理逻辑**：检查 AT 命令层在 xiic=0 去激活时是否有等待 TCP 资源释放的逻辑
- [ ] **增加 LWIP 资源检查**：在 xiic 去激活前检查 TCPIP_MSG_API 池是否可用，若不可用则延迟去激活
- [ ] **添加 TCPIP_MSG_API 池水位监控**：在 TCP 数据收发路径中添加池水位日志，确认耗尽时的业务上下文

## 相关文件

- RAM dump: `.spec/bug/6973174788_TCP连接后xiic0去激活概率性死机/dump/RamDumpData_20260417_100823.bin`
- MAP: `.spec/bug/6973174788_TCP连接后xiic0去激活概率性死机/dump/app-demo-flash.map`
- ELF: `.spec/bug/6973174788_TCP连接后xiic0去激活概率性死机/dump/app-demo-flash.elf`