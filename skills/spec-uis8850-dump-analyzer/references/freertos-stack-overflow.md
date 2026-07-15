# FreeRTOS 栈溢出判定指南（UIS8850 高频根因）

UIS8850 AP 死机最高频根因之一是**任务栈溢出**。FreeRTOS 在任务切换时用
`0xa5a5a5a5` magic 检测栈越界，触发 `vApplicationStackOverflowHook` → `osiPanic`
→ 蓝屏。本指南给出完整判定流程。

## 1. 检测机制（FreeRTOS Stack Overflow Method 2）

任务创建时，栈缓冲区被填充 `0xa5a5a5a5`（`configCHECK_FOR_STACK_OVERFLOW >= 2`
启用 method 2）。`vTaskSwitchContext` 切换任务时，检查**当前任务栈底**前几个字
是否仍为 magic。若被覆盖 → 调用 `vApplicationStackOverflowHook(xTask, pcTaskName)`。

反汇编特征（`vTaskSwitchContext`，实测）：
```asm
  ldr  r2, [r3, #8]          ; 读栈底某字
  cmp  r2, #0xa5a5a5a5       ; 与 magic 比较
  bne  <hook>                ; 不等 → 栈被破坏
  ...
  ldr  r0, [r4, #0]          ; r0 = *pxCurrentTCB (任务句柄)
  adds r1, #52               ; r1 = TCB+0x34 = 任务名
  bl   vApplicationStackOverflowHook
```

`vApplicationStackOverflowHook` 通常是 `osiPanic` 的 thin wrapper：
```asm
vApplicationStackOverflowHook:
  b.w  __osiPanic_veneer     ; tail-call 直接跳 osiPanic (不保存自身 LR)
```

**tail-call 的关键推论**：因 `vApplicationStackOverflowHook` 用 `b.w`（不是 `bl`），
osiPanic 继承其 LR = `vTaskSwitchContext` 中 `bl vApplicationStackOverflowHook`
的返回地址。故 `osiPanic` 栈帧 `sp+4` 直接 = `vTaskSwitchContext` 的返回地址，
中间没有 StackOverflowHook 的帧。

## 2. 判定树

```
AP PC 在 osiPanic 内? (gBlueScreenRegs.PC 落在 osiPanic 地址范围)
└── 是 → 软件主动 panic
    └── osiPanic 调用者 (sp+4) 是谁?
        ├── vTaskSwitchContext (tasks.c:3xxx) + 反汇编有 cmp #0xa5a5a5a5
        │   → ★ FreeRTOS 栈溢出 ★
        │   └── vApplicationStackOverflowHook 是否 tail-call osiPanic? (b.w __osiPanic_veneer)
        │       → 确认栈溢出 panic
        ├── osiAssert / OS_ASSERT 失败路径
        │   → 软件断言 (看 assert 文件/行)
        ├── FIQ_Handler / 异常向量
        │   → 硬件异常或 CP assert 上报
        └── 业务函数
            → 业务逻辑主动 panic
```

## 3. 锁定溢出任务

读 `pxCurrentTCB`（全局，存当前任务 TCB 指针）→ TCB → 判定：

```
TCB = *pxCurrentTCB
pxTopOfStack = [TCB + 0x0]    ; 当前栈顶
pxStack      = [TCB + 0x30]   ; 栈底
任务名       = [TCB + 0x34]   ; 16B 字符串

pxTopOfStack < pxStack ?
├── 是 → ★ 该任务自己栈溢出 ★ (栈顶越过栈底边界)
│        溢出深度 ≈ pxStack - pxTopOfStack
└── 否 → 栈方向正常, 但栈底 magic 可能被破坏 (见下)
```

**栈底 magic 破坏证据**：读 `pxStack` 处前 0x20 字节，应全为 `0xa5a5a5a5`。
被破坏的字节常含**越界写入的栈帧数据**——若破坏值是代码地址（`0x6xxxxxxx` flash /
`0x802xxxxx` PSRAM 代码 / `0x0010xxxx` IRAM），addr2line 之，即溢出调用链的函数。

本案例实测（nwy_sig_led 栈底被破坏）：
```
[pxStack+0x0] = 0x804e2154  <- 堆指针
[pxStack+0x8] = 0x60189446  <- nwy_ftp_ctrl::get_data_connected (CODE!)
[pxStack+0x14]= 0x6002dfeb  <- __ssprint_r (CODE!)
[pxStack+0x18]= 0x60029c28  <- memmove (CODE!)
```
→ FTP + sprintf 调用链的栈帧数据冲破了栈底 magic。

## 4. 栈上调用链还原

栈溢出任务的栈帧里保留了溢出前的调用链（部分被 magic 区截断）。从
`gBlueScreenRegs.SP` 向上扫 0x400 字节，对每个像代码地址的值 addr2line：

- 用 `Symbols.is_exec_code(addr)`（基于 ELF PT_LOAD 可执行段）过滤代码地址
- **call site 验证**：候选返回地址 V，反汇编 V-4/V-2 处必须是 `bl`/`blx`/`b`
  指令（ARM Thumb：bl 是 32-bit 占 V-4..V-2，b/bx 是 16-bit 占 V-2）
- tail-call 中间层的 LR 可能跳过，故 call site "未确认" 不代表错误

本案例调用链：
```
vTaskSwitchContext (tasks.c:3129)        ← osiPanic 直接调用者
  ← portASM.S:270 (任务切换退出)
  ← nwy_ftp_ctrl::ftp_get_status (nwy_ftp_ctrl.cpp:1510)
  ← nwy_ftp_ctrl::get_data_connected
  ← mbedtls_oid_get_numeric_string       (mbedTLS, 栈消耗大)
  ← sprintf / _svfprintf_r / memmove     (格式化, 栈消耗大)
```

## 5. 任务枚举（threads.py）

扫 PSRAM 堆区（`__heap_start`~`__heap_end`）找所有 TCB。验证特征：
- `pxTopOfStack` 与 `pxStack` 都在堆区
- `pcTaskName`（TCB+0x34）是可打印 ASCII（2-16 字符）
- 优先级 `uxPriority` 合理（< 256）

> ⚠️ **扫描法有误报**：堆区数据偶然符合 TCB 特征（任务名乱码、优先级异常大的）。
> 关注：① `*CURRENT*` 任务；② 栈底 magic 破坏且破坏值是代码地址的任务；③ 栈水位
> 极低（`pxTopOfStack` 接近 `pxStack`）的任务。任务名乱码/优先级 9999 的多为误报。

## 6. 修复方向

1. **加大溢出任务栈**：核算调用链栈消耗。mbedTLS（TLS 握手、`mbedtls_oid_*`）、
   `sprintf`/vfprintf（大格式串）、深层递归都是栈消耗大户。
2. **削减栈峰值**：避免在任务上下文用大局部缓冲区，改静态/堆分配；减少递归。
3. **监控水位**：运行期 `uxTaskGetStackHighWaterMark` 周期打印，定位逼近上限的任务。
4. **核对任务职责**：若简单任务（如 LED）栈溢出，检查是否被误用于深调用（任务名与
   实际功能不符），或被外部越界写破坏（区别：自己溢出 = pxTopOfStack<pxStack；
   被越界 = pxTopOfStack 正常但 magic 被改）。
