# ARM Thumb 栈回溯指南

UIS8850 AP 是 ARM Cortex-R，全 Thumb 指令，不强制 frame pointer。栈回溯靠
prologue 解析 + call site 验证。本指南给出实用方法。

## 1. 起点：gBlueScreenRegs.SP

`osiPanic` 的 `push {r3, lr}` 把调用者 LR 存在 `sp+4`。故：

```
gBlueScreenRegs.SP = osiPanic 内 udf 时的 SP（push 后）
调用者 LR = [SP + 4]
```

`SP+4` 即 osiPanic 的直接调用者返回地址。这是回溯的起点。

> ⚠️ `gBlueScreenRegs.LR` 不可信——被 osiPanic/异常处理覆盖。**只用 sp+4 的 LR**。

## 2. call site 验证（必做）

ARM 不用 frame pointer，栈上数据偶然落在代码段会被误判为返回地址。**每个候选
返回地址 V，必须反汇编 V-4/V-2 处确认是调用指令**：

```python
def is_call_site(objdump, elf, addr):
    real = addr & ~1   # strip Thumb bit
    dis = objdump_range(objdump, elf, real - 4, real + 2)
    for line in dis:
        insn = line after ':'
        if re.match(r'(bl|blx|b\.w|b|bx)\b', insn):
            return True
    return False
```

ARM Thumb 调用指令：
- `bl` / `blx`：32-bit（占 V-4..V-2），函数调用（带返回）
- `b` / `b.w` / `bx`：16/32-bit，跳转（tail-call，不带返回）

**tail-call 链**：若中间函数用 `b.w`（不是 `bl`）调下一层，则不压栈，LR 透传。
此时 osiPanic 的 sp+4 直接是**更上层**的返回地址，跳过了 tail-call 的中间函数。
这是正常的，call site 验证"未确认"不代表错误。

例：`vApplicationStackOverflowHook` 用 `b.w __osiPanic_veneer`，故 osiPanic 的
LR = vTaskSwitchContext 中 `bl vApplicationStackOverflowHook` 的返回地址。

## 3. 逐帧上溯（prologue 解析）

对链上每个函数，objdump 其 prologue，提取：
- frame size（`sub sp, #N` / `push {...}` 的字节数）
- LR 保存偏移（`push {..., lr}` 后 LR 在 sp+某偏移）

```
sp_next = sp_current + frame_size
caller_lr = [sp_next + lr_offset]
```

ARM Thumb prologue 常见模式：
```asm
push {r4-r11, lr}    ; 保存寄存器 + LR, sp -= 4*(count)
sub  sp, #N          ; 分配局部变量
...
```

`push {r4-r11, lr}` = 9 个寄存器 × 4 = 36 字节，LR 在最高 = `sp + 32`。

## 4. 实用捷径：栈扫描 + 过滤

完整 prologue 解析繁琐。实用方法：从 SP 扫 0x400 字节，对每个值用
`Symbols.is_exec_code()`（ELF PT_LOAD 可执行段）过滤代码地址，addr2line，再 call
site 验证。这能快速还原调用链主体（虽可能有少量噪声）。

```python
for i in range(0, 0x400, 4):
    v = mem.try_u32(sp + i)
    if v and syms.is_exec_code(v):
        info = addr2line(v & ~1)   # Thumb 地址 & ~1
        # call site 验证 V-4 处
```

**双向核对语义**：栈地址 A 的值 V → V 的函数调用了 A 处帧的所有者。若 V 的函数
与当前帧所有者无调用关系，可能是噪声。

## 5. addr2line 的 Thumb 处理

ARM Thumb 函数地址低位 = 1（Thumb 标志）。addr2line 前**必须 `& ~1`** 去掉 Thumb
位，否则解析到错误地址：

```python
addr2line -f -e <elf> 0x$(printf '%x' $((addr & ~1)))
```

`Symbols.resolve(addr)` 内部已处理。

## 6. 常见陷阱

| 陷阱 | 说明 |
|---|---|
| `gBlueScreenRegs.LR` 不可信 | 用 sp+4 的 push LR |
| 栈上数据误判为返回地址 | 必须 call site 验证 |
| Thumb 位未 strip | addr2line 前先 `& ~1` |
| tail-call 跳过中间帧 | 正常，call site"未确认"非错误 |
| 中断上下文栈 | 若 cpsr 模式=IRQ/FIQ，被中断任务的栈在别处，需先切到任务栈 |
| SP 指向堆区 | FreeRTOS 任务栈从堆分配，SP 在 `__heap_start` 附近正常（非栈溢出） |
