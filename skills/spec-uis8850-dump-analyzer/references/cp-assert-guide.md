# CP (Modem) Assert 上报指南

UIS8850 是双核：AP（ARM Cortex-R，跑应用/FreeRTOS，AP ELF）+ CP/Modem（ARM，
跑 ivykit_modem 协议栈）。CP 核 assert 后，通过 IPC 把寄存器现场上报给 AP，AP
记录 "AP PANIC" 文本。本技能分析 AP dump，CP assert 信息可解析，但 **CP 代码不在
AP ELF**，CP 根因需 CP/Modem 符号文件。

## 1. 上报链路

```
CP 核发生异常/断言
  → CP 异常处理保存寄存器, 通过 IPC 发给 AP
    → AP drv_md_ipc.c 收到 (ipc_notify_cp_assert @2293 / ipc_show_cp_assert @2299)
      → 格式化 "CP Assert reg:r0..r17" + "Version:ivykit_modem..." + "AP PANIC"
        → 写入 PSRAM 日志 buffer
          → (可能)触发 AP panic
```

## 2. CP Assert 文本格式（PSRAM 全文搜）

```
CP Assert reg:r0  0x9
CP Assert reg:r1  0x801219cc
...
CP Assert reg:r13 0x801219e4    (SP)
CP Assert reg:r14 0x50806989    (LR)
CP Assert reg:r15 0x50806989    (PC ← CP 崩溃点)
CP Assert reg:r16 0x60000192    (CPSR?)
CP Assert reg:r17 0x20000033    (SPSR?)
Version:ivykit_modem_rls-shell_8850BM_cat1bis_plus-svn60817-date2025-08-07 17:11:25
Loaction:md_ipc.c->LINE:1374        (注意原代码 typo: Loaction)
Indication:AP PANIC
```

提取要点（`cp_assert.py`）：
- 正则 `CP Assert reg:r(\d+)\s+0x([0-9a-fA-F]+)`，取**最后一组完整** r0..r17（最新一次）
- r13=SP, r14=LR, r15=PC, r16=CPSR, r17=SPSR（CP 也是 ARM）
- `g_CpMdVersion`（AP 全局）= CP svn 版本号数值（如 0xed91=60817），与 Version 串
  的 `svn60817` 对应，可交叉验证

## 3. CP 崩溃点反汇编

CP PC 常落在 `0x50800000`（aon_iram，CP/AON 代码区）或 `0x10100000`（cp_iram）。
这些区**不在 AP ELF 的 PT_LOAD 段内**，无法 addr2line。只能从 dump 的 `.bin`
裸字节反汇编（ARM Thumb，对齐 `& ~1`）：

```python
# CP PC=0x50806989 (奇数=Thumb), 实际指令在 0x50806988
aon = open("50800000.bin","rb").read()
off = (cp_pc & ~1) - 0x50800000
blob = aon[off-0x30 : off+0x40]
objdump -D -b binary -marm -Mforce-thumb --adjust-vma=<vma> blob
```

## 4. CP15 读取识别（异常处理特征）

CP 崩溃点常是**异常处理代码在读 CP15 系统控制寄存器**（保存故障现场）：

```asm
50806988: mov r0, r4                ; <<< CP PC
5080698a: mrc 15, 0, r2, cr1, cr0   ; 读 SCTLR (系统控制)
5080698e: mrc 15, 0, r1, cr2, cr0   ; 读 TTBR0 (页表基址)
50806992: mrc 15, 0, sl, cr5, cr0   ; 读 DFSR (数据 fault 状态)
50806996: mrc 15, 0, r6, cr6, cr0   ; 读 FAR (故障地址)
5080699a: mrc 15, 0, ip, cr5, cr0,{1}; 读 IFSR (指令 fault 状态)
5080699e: mrc 15, 0, r3, cr6, cr0,{2}; 读 IFAR (指令故障地址)
```

| CP15 寄存器 | 含义 | 排查价值 |
|---|---|---|
| SCTLR (cr1) | 系统控制（MMU/cache 使能） | 初始化状态 |
| TTBR0 (cr2) | 页表基址 | 地址翻译 |
| **DFSR (cr5)** | 数据 fault 状态码 | **data abort 类型** |
| **FAR (cr6)** | 故障虚拟地址 | **非法访问地址** |
| IFSR (cr5,{1}) | 指令 fault 状态 | prefetch abort 类型 |
| IFAR (cr6,{2}) | 指令故障地址 | 取指非法地址 |

看到 `mrc 15` 连续读 cr5/cr6 → CP 发生了 **data/prefetch abort**。CP 的 DFSR/FAR
值若能在 CP 寄存器快照里找到（r2/r6 等，取决于异常处理是否已读出），可判断 CP 是
访问了哪个非法地址。但通常 CP assert 文本只存通用 r0-r17，CP15 值未单独上报。

## 5. CP assert 与 AP 栈溢出的关系

AP dump 里同时出现 CP assert 文本和 AP 栈溢出现象时，判断因果：

- **CP 独立异常**：CP 自己的协议栈逻辑/指针错误触发 abort，与 AP 无关。需 CP ELF。
- **AP 栈溢出殃及 CP**：AP 任务栈溢出破坏了 AP-CP 共享内存（IPC buffer/共享数据），
  CP 处理 IPC 时读到损坏数据 → CP abort。此时 AP 栈溢出是**根因**，CP assert 是
  **后果**。
- **并发**：两者独立同时发生（压测场景）。

区分线索：
- AP 栈溢出任务的栈底破坏值是否含共享内存地址（指向 IPC buffer）
- CP assert 的 FAR 是否落在 AP 任务栈范围
- 时序：CP assert 文本是否在 AP panic 之前已存在（历史日志 vs 本次）

## 6. 局限

- **无 CP ELF**：CP PC 只能裸反汇编，无法定位到 CP 函数名/源码行
- **CP 版本独立**：CP 固件（ivykit_modem，svn60817/2025-08-07）可能比 AP 旧很多，
  FOTA 升级时 AP 更新而 CP 未同步是常见情况，需确认 CP 固件是否需随版本更新
- 若要定位 CP 根因，需向 modem 团队索取 CP/Modem 符号文件（含 aon_iram/cp_iram
  代码段的 ELF 或 map）
