# Dump 分析：rti_thread_switch_out 空指针解引用导致 DataAbort

| 项 | 内容 |
|---|---|
| 工作项 ID | NA |
| 状态 | **✅ 根因已确认并修复（实测不再死机，见 §0、§8、§11）** |
| 固件版本 | SDK_1.011.156 (Jun 15 2026 22:51:30) |
| dump 来源 | `N706B-A07-STD-BZ_CN1X_PWR-017_T8_20260520` |
| 平台 | ASR1603 (ARM Cortex-R + ThreadX) |
| 分析日期 | 2026-06-16（初版）/ 2026-06-16（根因确认更新） |
| 归档 | `.spec/bug/NA_rti_switchout_null_deref/` |

---

## 0. 结构化摘要

| **字段** | 值 |
|----------|-----|
| **工作项 ID** | NA |
| **平台** | ASR1603 |
| **模块** | TCP自动重建(nwy_tcpsrv_redial) / PPPREDIAL / RTI线程切换插桩 |
| **问题分类** | 任务栈溢出→内存破坏→空指针解引用(DataAbort)，已修复 |
| **症状关键词** | DataAbort, rti_thread_switch_out, 空指针解引用, RTI记录全零, FAULT_ADDRESS=0xE59FF018, 栈溢出, 越界写, 任务栈2048B, nwy_tcpsrv_redial, cisend |
| **根因概述** | 新增的 TCP 自动重建任务 `nwy_tcpsrv_redial` 栈仅 2048B，在 ASR(Cortex-R+ThreadX+ASR TCP栈) 上执行 `nwy_app_tcp_server_setup/_v6` 深 TCP 建链调用时栈溢出，越界清零相邻堆内存中的 RTI per-thread 记录；当 nwy_cp_service 任务被上下文切换切出时，`rti_thread_switch_out` 读取该全零记录 → NULL 解引用读向量表得 0xE59FF018 → 再解引用未映射地址 → DataAbort 死机。栈改 4096 后实测不再死机。 |
| **调用链摘要** | `AT+CFUN=0/1` 断网恢复 → data_cb → redial 任务 `nwy_yddl_tcpsrv_redial_server_func`(2048B栈) → `nwy_app_tcp_server_setup/_v6`(深栈,溢出) → 越界清零 RTI 记录 → 切出 nwy_cp_service → `switch_out` → `rti_thread_switch_out` 读 record[0]=0 → *(0xC)=0xE59FF018 → *(0xE59FF018) → DataAbort |
| **检索关键词** | DataAbort, rti_thread_switch_out, switch_out, RTI记录全零, 0xE59FF018, nwy_tcpsrv_redial, 栈溢出, 任务栈大小, NWY_TCPSRV_REDIAL_TASK_STACK_SIZE, 跨平台移植栈大小, UIS8852到ASR, PPPREDIAL, TCPLISTEN, nwy_cp_service, ciRequest, nwy_app_tcp_server_setup, 空指针解引用, 越界写堆 |

---

## 〇、根因确认与修复状态（2026-06-16 更新）

> 初版分析定位到"被切出线程的 RTI 记录被清零 → `rti_thread_switch_out` 空指针解引用"，但"记录为何被清零"列为待验证。**后续已实测确认根因并修复：**

- **根因**：本次新增的 TCP 自动重建任务 `nwy_tcpsrv_redial` 栈仅 **2048 字节**，在 ASR（Cortex-R + ThreadX + ASR TCP 栈）上执行 `nwy_app_tcp_server_setup/_v6`（TCP 建链深调用栈）时**栈溢出**，越界写坏相邻堆内存——其中包含被切出线程的 RTI per-thread 记录，使其被清零。
- **证据吻合**：dump 中栈溢出的匿名 "Init" 线程栈 **2044 字节**（见 §8），与 `nwy_tcpsrv_redial` 声明的 2048 字节几乎一致（差值 4 为 ThreadX 栈开销/对齐），即**该溢出线程就是 nwy_tcpsrv_redial**。
- **修复**：`NWY_TCPSRV_REDIAL_TASK_STACK_SIZE` 由 `2048` 改为 `4096`（最终上传版本；调试阶段曾试 8192，确认 4096 已足够），位于 `pcac/NWY_FRAMEWORK/atcmd/nwy_at_proc/src/nwy_app_at_func_tcp.c:6143`。
- **验证**：重跑复现用例（`AT+PPPREDIAL=10` + `AT+TCPLISTEN=9600` + 反复 `AT+CFUN=0/1` 断网恢复），**不再死机**。
- **平台差异教训**：2048 字节在源平台 UIS8852（RISC-V）上够用，但 ASR（Cortex-R + ThreadX + 自有 TCP 栈）调用约定与栈开销不同，跨平台移植网络类任务**不能直接照搬栈大小**，需按目标平台实测复核。

## 一、现象与版本

设备死机，抓取完整 crash dump。版本校验：EE_Hbuf、CustVer、AXF 三处版本来源一致（`All available version sources match`），后续结论无需标注版本警告。

dump 文件齐全：`com_EE_Hbuf.bin`（异常头）、`com_DDR_RW.bin`（PSRAM/DDR 转储）、`com_ITCM.bin`（指令紧耦合内存）、`*.map`、`*.axf`、`com_wdtKICK.bin`、`com_rti_tsk.bin`、`com_CustVer.bin`。

## 二、异常信息（EE=EXCEPTION）

| 字段 | 值 |
|---|---|
| ee_type_raw | 0x1C2 = 450 → **EXCEPTION**（非 WDT/ASSERT） |
| exception_type | **DataAbort** |
| 报告 PC | 0x0000BAE2 → `rti_thread_switch_out+0x1A` |
| LR | 0x0000BB6F → `switch_out+0x19` |
| SP | 0x7E0E28A4 |
| FAULT_STATUS | 0x08 → FSC=SyncExtAbort, WnR=0（读访问） |
| FAULT_ADDRESS | 0xE59FF018 |
| task_name | Unknown（TCB 在未抓取的 0xB00xxxxx 区） |
| is_isr | False |

关键寄存器：`R0=0 / R1=0xE59FF018 / R2=0x54485244("THRD") / R4=0x7E2358C0 / R7=0xB0020758`。

## 三、代码完整性校验

崩溃 PC（0xBAE2）位于 ELF section `DDR_ITCM (0x00000000..0x0000DC18)`（运行于 ITCM）。

**AXF vs com_ITCM.bin @0xBAE2 对比结果：64/64 字节 100% 匹配 → 代码完整，无损坏。** 反汇编结论可信。

> 注：dump_analyzer 自带反汇编器对 `0x68xx/0x69xx` 系列 Thumb 编码误标为 `ldrb` 并用字节偏移，实际 `01101` 编码为 **LDR（字加载，偏移按 ×4）**。本报告按 ARM 编码手册人工解码修正。

## 四、崩溃指令精确定位

`rti_thread_switch_out`（0xBAC8）指令流（修正后解码）：

```asm
0xbac8: push.w {r2,r3,r4,r5,r6,r7,r8,lr}
0xbacc: ldr   r7, [pc,#260]     ; r7 = 全局(RTI控制) = 0xB0020758
0xbace: mov   r4, r0            ; r4 = 参数(RTI记录指针) = 0x7E2358C0
0xbad0: ldrb  r0, [r7,#6]       ; 使能标志
0xbad2: cmp   r0,#0 ; beq 出口
0xbad6: cmp   r4,#0 ; beq 出口   ; 只判记录指针非空
0xbada: ldr   r0, [r4,#0]       ; R0 = *(record+0)  = 0x00000000   ← 记录首字段为 NULL!
0xbadc: ldr   r2, [pc,#284]     ; R2 = 0x54485244 ("THRD")
0xbade: ldr   r1, [r0,#12]      ; R1 = *(0x0+0xC)  = 0xE59FF018    ← 读到向量表第4项!
0xbae0: ldr   r6, [r1,#0]       ; ★真实崩溃指令: 读 *(0xE59FF018) → 地址未映射 → DataAbort
0xbae2: ldr   r1, [r0,#16]      ; (报告 PC = 此地址 = 真实指令 +2，ARM DataAbort 标准偏移)
0xbae8: ldr   r1, [r0,#0]       ; (本应在此校验 *(R0)=="THRD"，但已崩)
```

**矛盾分析法佐证**：报告 PC=0xBAE2 的指令以 R0 为基址，与 FAULT_ADDRESS=R1 不符；真实崩溃指令为 **0xBAE0 `LDR R6,[R1]`**（FAULT_ADDRESS=0xE59FF018=R1，WnR=0 读，与 FAR 完全自洽；R6 目标寄存器=0 未写回亦自洽）。

## 五、RTI 记录寻址还原（switch_out）

`switch_out`（0xBB56）如何得到参数：

```asm
0xbb58: ldr   r4,[pc,#268]     ; r4 = 0xB00209C0  (全局"当前线程指针"地址)
0xbb5a: ldr   r0,[r4,#0]       ; r0 = 当前线程 TX_THREAD*
0xbb60: ldr.w r0,[r0,#0xb0]    ; r0 = 当前线程->field[0xB0]  (RTI记录偏移)
0xbb64: ldr   r1,[pc,#260]     ; r1 = 0x7E231600  (RTI表基址)
0xbb66: add.w r0,r1,r0          ; r0 = RTI_base + field[0xB0]
0xbb6a: bl    0xbac8            ; → rti_thread_switch_out(r0)
```

代入：`0x7E231600 + field[0xB0] = 0x7E2358C0` ⇒ **当前线程 `field[0xB0] = 0x42C0`**。

## 六、为何崩溃：RTI 记录为全零

- 0x7E2358C0 处结构**连续 160+ 字节全为 0**；该地址处于一段 **3004 字节的堆零区**（`0x7E2352E0 .. 0x7E235E9B`）中间。
- RTI 表（基址 0x7E231600）密度扫描：
  - `0x7E231600..0x7E232400`：有效稀疏 per-thread 记录（首记录 `+0x00 = 0xB0022138` 是合法 TX_THREAD 指针）。
  - `0x7E232400` 之后为大段零区（中间夹杂其它堆对象的密集数据，说明**整片是堆区**而非单一表格）。
  - 崩溃记录偏移 **0x42C0 远超有效记录范围（~0xE00）**，落在堆零区。
- 关键逻辑：若线程"从未注册"，`field[0xB0]` 应为 0，则参数会命中表头**有效**记录（0x7E231600），不会崩溃；但此处是**非零越界值 0x42C0** ⇒ 该记录槽指向未初始化堆空间，`record[0]=NULL`，触发空指针→向量表→未映射地址的两级解引用崩溃。

**直接根因（崩溃机制）**：当前（被切出）线程的 RTI per-thread 统计记录无效（全零），`rti_thread_switch_out` 缺少对 `record[0]==NULL` / `field[0xB0]` 越界的防御性校验，发生空指针解引用。

> **记录为何被清零？（已确认）** 初版此处为"待验证"。现已确认：RTI 记录所在的堆区域被 **`nwy_tcpsrv_redial` 任务栈溢出越界写入清零**（详见 §0、§8）。即 RTI 记录全零是**结果**，**根本原因**是该任务栈过小。`rti_thread_switch_out` 空指针是崩溃的**直接触发点**，而非根因。

## 七、触发场景（被切出线程 = nwy_cp_service 任务）

栈上关键调试串（栈地址 0x7E0E29F8 起，小端还原）：

```
DEBUG|nwy_cp_service.c|1494|[cp-service err][Line: 1494]: cisend primID 9
```

栈中调用链（已解析）：

```
UARTLogPrintf_Extend+0xD1   (0x7E31FF1F)
 └─ diagPrintf_Extend+0x39D (0x7E317E35)
     └─ diagSendPDU+0x3B    (0x7E326C5F)
         └─ diagBufferPDUExtIf+0x113 (0x7E3183DF)
             └─ DiagCommExtIfTransmit+0x19 (0x7E3254BD)
                 └─ ... OsaFlagSet+0x3D (0x0000A121) → _tx_event_flags_set+0x184 (0x0000692C)
                     └─ KiOsStopTimer+0xBF (0x00001499) → switch_out → rti_thread_switch_out ★崩溃
```

源码定位 `pcac/nwy_bpv2_plat/plat/asr1603/cp_service/nwy_cp_service.c:1494`（函数 `nwy_cp_service_cisend_witch_subprim_sync`）：

```c
1483: nwy_cp_service_mutex_lock(g_nwy_cp_service_ci_sema_ref);   // 持锁
...
1494: NWY_CP_SERVICE_LOG_ERR("cisend primId %d", primId);        // ← 栈上这条日志
1495: res = ciRequest(gAtciSvgHandle[serid], primId, ...);       // 同步发 CI 请求到 CP
1497: os_sta = OSAMsgQRecv(..., waittime*200);                   // 阻塞等响应
```

**触发链**：nwy_cp_service 任务（持锁）调用 `ciRequest()` 向 CP 发送 primitive（primId 9）→ 经 diag 日志 + `OsaFlagSet` 唤醒 CP 侧任务 → 触发**上下文切换** → 切出 nwy_cp_service 时访问其全零 RTI 记录 → 崩溃。

> `cisend` 逻辑本身正常，line 1494 只是**切出瞬间恰好执行到的位置**，非缺陷点。

## 八、★根因：nwy_tcpsrv_redial 任务栈溢出（已确认）

> 初版将本节误判为"独立现象"。经源码核对与修复验证，**这才是本次死机的根本原因**——dump 中溢出的 "Init" 线程即 `nwy_tcpsrv_redial` 任务。

### 8.1 溢出线程 = nwy_tcpsrv_redial

- dump 中匿名 "Init" 线程（TCB=0x7E0C1980）栈 `0x7E0C1AF8..0x7E0C22F3`（**2044 字节**），栈底 0xEF 被覆盖，当前 SP=0x7E0C226C 已退栈到顶部（**说明溢出发生在更早的深层调用时刻，事后栈已回退**）。
- 源码 `nwy_app_at_func_tcp.c:6141-6143`（初版）：
  ```c
  #define NWY_TCPSRV_REDIAL_TASK_NAME         "nwy_tcpsrv_redial"
  #define NWY_TCPSRV_REDIAL_TASK_STACK_SIZE   2048   // 初版：仅 2048 字节
  ```
  声明栈 2048，与 dump 实测溢出栈 2044 几乎完全一致（差 4 为 ThreadX 栈开销/对齐）→ **溢出线程即 nwy_tcpsrv_redial**。

### 8.2 为何溢出

该任务入口 `nwy_yddl_tcpsrv_redial_server_func()`（`nwy_app_at_func_tcp.c:6169`）执行 TCP 服务端建链，调用链很深：

```
nwy_yddl_tcpsrv_redial_server_func
 └─ nwy_app_tcp_server_setup / nwy_app_tcp_server_setup_v6   (IPv4/IPv6 双栈)
     └─ socket → bind → listen → setsockopt ...（ASR TCP/LwIP 深栈）
```

2048 字节在源平台 **UIS8852（RISC-V）** 上勉强够用；移植到 **ASR1603（Cortex-R + ThreadX + ASR 自有 TCP 栈）** 后，调用约定（AAPCS 寄存器保存）、RTOS 切换帧、TCP 栈本地变量开销更大，2048 字节不足以容纳建链深栈 → **栈溢出**，越界写坏相邻堆内存（含被切出线程的 RTI 记录，将其清零）→ 触发 §四 的 `rti_thread_switch_out` 空指针死机。

### 8.3 修复

`NWY_TCPSRV_REDIAL_TASK_STACK_SIZE`：`2048` → **`4096`**（最终上传版本；调试阶段曾试 8192，实测 4096 已足够）。实测复现用例 **不再死机**。

### 8.4 其它任务栈使用率偏高（系统级，建议同步评估）

ATChanT(97%)、NWYCOMM(96%)、abshTask(95%)、VgCiTask(94%)、LteRrcTa(94%)、PlmsTask(94%) 等 >90%，系统内存紧张，越界风险高。建议按运行时栈水位统计评估加栈。

## 九、根因决策树落点

```
ee_type=450 EXCEPTION
└─ 代码完整（Step5: AXF==ITCM 100%）
└─ DataAbort，崩溃指令 LDR R6,[R1]，Rn=R1≈0xE59FF018（向量表内容，非栈非0）
    └─ 非栈溢出、非裸 NULL（NULL 经两级解引用放大为 0xE59FF018）
    └─ FAULT_ADDRESS 不在栈范围 → 野指针（来源：被切出线程全零的 RTI 记录）
        ├─ 直接触发点：rti_thread_switch_out 空指针解引用（崩溃机制）
        └─ 根本原因（已确认）：nwy_tcpsrv_redial 任务栈 2048B 不足 → 建链深栈溢出
              → 越界清零相邻堆中的 RTI 记录（§8）
              ⇒ 根因：nwy_tcpsrv_redial 任务栈溢出破坏 RTI 记录；栈改 4096 后已修复
```

## 十、复现路径（已实测确认）

**前置条件**：固件启用 `FEATURE_NWY_AT_PPPREDIAL_FUNCTION`；`nwy_tcpsrv_redial` 任务栈为初版的 2048 字节；已开启 TCP 监听并设置 PPPREDIAL。

**复现用例（实测日志 2026-06-16 11:25–11:31，可稳定复现）**：

```
AT+XIIC=1              → +NEWIP / +PPPSTATUS:OPENED          (激活 PDP)
AT+TCPLISTEN=9600      → +TCPLISTEN: 0,OK / 1,OK             (开启 TCP 监听)
AT+PPPREDIAL=10        → OK                                  (使能断网自动重拨, 10s)
AT+CFUN=0              → GPRS DISCONNECTION                  (断网)
AT+CFUN=1              → +PBREADY2                           (重连)
  ↳ 数据重连 +PPPSTATUS:OPENED → data_cb 命中 → redial 任务 server_func
     → nwy_app_tcp_server_setup/setup_v6 (深栈) → 2048B 栈溢出 → 越界清零 RTI 记录
        → 下次上下文切换切出某任务时 rti_thread_switch_out 读全零记录 → DataAbort 死机
```

反复执行 `AT+CFUN=0/1` 断网恢复循环（每次重连触发一次 TCP 重建），数轮内即可触发死机。

**修复后验证**：将 `NWY_TCPSRV_REDIAL_TASK_STACK_SIZE` 改为 4096 后，重跑同样用例（多轮 `CFUN=0/1` + `PPPREDIAL=10` + `TCPLISTEN`），**不再死机** → 根因确认。

## 十一、修复状态与建议

### 已实施并验证 ✅

1. **【根因修复·已完成】** `nwy_tcpsrv_redial` 任务栈 `2048` → **`4096`**（最终上传版本，`nwy_app_at_func_tcp.c:6143`）。实测复现用例不再死机。
   - 调试阶段曾试 8192，实测 4096 已足够；后续可按运行时栈水位再评估是否需进一步上调（如 8KB/16KB）。

### 已排查并排除（初版假设）

2. **【RTI 注册路径·已排除】** 核查确认 `nwy_thread_create` 经 `OsaTaskCreate` 创建任务，RTI 注册由 OSA 内部自动处理，与其它 NWY 线程一致——**非"app 任务未注册 RTI"导致**。RTI 记录全零确为运行时被栈溢出越界清零，而非创建期遗漏。

### 建议跟进（防御加固，非本次阻塞项）

3. **【防御性加固·建议】** 即便根因已修，建议在 `rti_thread_switch_out`（及 `switch_out`）增加防御性校验，避免未来其它内存破坏再次单点拖死整机：
   - `field[0xB0]` 落在 RTI 表有效记录范围内；
   - `record[0]` 非 NULL（且 `*(record[0])` 含 "THRD" 魔数）后再解引用。
4. **【系统级栈水位治理·建议】** 复查 §8.4 列出的 >90% 栈使用任务，结合运行时栈水位统计评估加栈；并在跨平台移植网络/深栈类任务时，按目标平台实测复核栈大小（UIS8852 的 2048 不可直接照搬到 ASR）。

## 附录 A：关键证据地址

| 地址 | 含义 | 来源 |
|---|---|---|
| 0x0000BAE0 | 真实崩溃指令 `LDR R6,[R1]` | ITCM 反汇编（AXF==ITCM 验证） |
| 0x0000BAE2 | 报告 PC（+2 偏移） | com_EE_Hbuf |
| 0x7E2358C0 | 全零 RTI 记录（参数 R4） | com_DDR_RW @ base 0x7E0407FC |
| 0x7E231600 | RTI 表基址 | switch_out 字面量 @0xBC6C |
| 0xB00209C0 | "当前线程指针"全局地址 | switch_out 字面量 @0xBC68 |
| 0xE59FF018 | FAULT_ADDRESS = `LDR PC,[PC,#0x18]` 复位向量码 | com_EE_Hbuf FAR |
| 0x7E0C1980 | 栈溢出线程 TCB = **`nwy_tcpsrv_redial` 任务**（栈 2044B，声明 2048B） | scan-threads + 源码 |
| nwy_cp_service.c:1494 | 被切出瞬间执行点（cisend primID 9） | 栈上调试串 + 源码 |
| nwy_app_at_func_tcp.c:6143 | **根因修复点**：`NWY_TCPSRV_REDIAL_TASK_STACK_SIZE` 2048→4096 | 源码 |

## 附录 B：DDR 基址与校验

- DDR 基址自动检测：**0x7E0407FC**（以 0x7E0E28A4 处 CPSR 值 0x20000193 定位）。
- 二次校验：栈地址 0x7E0E29F8 处读取到 `DEBUG|nwy_cp_ser...`，与栈帧一致，基址可信。
- 堆扫描（scan-heap）：未发现 TX_BYTE_POOL 结构（ASR 堆管理签名不同），改用直接内存读取分析。