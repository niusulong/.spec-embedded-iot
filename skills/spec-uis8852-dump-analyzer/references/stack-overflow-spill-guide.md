# 系统/中断栈溢出溅射相邻全局 — 取证方法论

UIS8852 AP 在 DTCM 只保留**一块**栈 `__stack_start..__stack_end`（`__stack_size`，常 2KB），身兼系统栈与中断栈两职（`__start` 预填 `0x24242424`；`switch_irq_sp` 每次中断把 SP 重置到 `__stack_end`）。它向下生长，越过 `__stack_start` 会踩踏**紧贴其下的最后一个 BSS 全局**（如 `g_tRlcUeEntity`）。这是本平台的复现性失败模式（见记忆 `uis8852-system-stack-overflow-mode`）。

本文固化从真实案例提炼的取证方法，配套脚本：`system_stack.py`（自动检测）+ `disasm.py`/`peek.py`（逐字核实）。

## 目录
1. [为什么 fill-scan 水位会误导](#1-为什么-fill-scan-水位会误导)
2. [三条独立物证（决定性）](#2-三条独立物证决定性)
3. [崩溃值的精确身份：dlmalloc chunk-size](#3-崩溃值的精确身份dlmalloc-chunk-size)
4. [cm.push 帧布局速查](#4-cmpush-帧布局速查)
5. [核实命令清单](#5-核实命令清单)
6. [反例：何时不是栈溢出](#6-反例何时不是栈溢出)

---

## 1. 为什么 fill-scan 水位会误导

高水位（high-water-mark）法：启动把栈填 `0x24242424`，用过后找"最低非填充字"作为最深用量。**陷阱**：RISC-V Zcmp 的 `cm.push {ra,s0-sN},-N` 只把寄存器写在帧顶（ra 在 `sp+0x0c` 一带），**帧底部留空仍为填充**。于是：

- "最深非填充字"实际是某个帧的 ra 位置，其下方到栈底还有"帧内未写底部"被判成填充；
- fill-scan 报的"余量"是**上界**，会把"帧内未写底部"误当未用栈 → 余量偏大、真实最深 SP 可能已到栈底；
- 典型误读：报告"溢出余量 12B、高危"，实则栈早已越过栈底踩了相邻全局。

**结论**：是否真溢出**不要看 fill-scan 余量**，看下方的溅射物证。

## 2. 三条独立物证（决定性）

`system_stack.py` 的 **[溢出溅射检测]** 跨栈底扫描 `[受害全局 .. 栈底+0x80]`，给出三条相互独立、不依赖帧布局假设的物证：

**① 返回地址 ra 越过栈底（最强）**：受害全局(BSS)里出现指向代码段的返回地址。BSS 不可能自发产生代码地址 → 只能是栈帧写进来的 → 栈越过 `__stack_start`。
- 核对：受害全局里的 ra 是否与栈残留中的同一函数（如 `do_check_free_chunk+8`、`do_check_inuse_chunk+0xbe`）以相近间距反复出现。

**② 背景填充指纹**：`__start` 只把**栈区**填 `0x24242424`，受害全局是 BSS（零初值）。栈帧越过栈底后，同一套帧结构的"未写位"由 `0x24242424`（栈区）切换为 `0x00000000`（BSS）。
- 核对：`填充 0x24242424 ×N` 只出现在栈底以上，栈底以下为 `zero/其它`。这个切换点就是栈底。

**③ 崩溃值 = dlmalloc chunk-size**（见下节）：受害槽里的崩溃值是 allocator 内部数据，非合法业务指针。

三者其一成立即确证；②③ 是 ① 的交叉验证。

## 3. 崩溃值的精确身份：dlmalloc chunk-size

案例：`Pdcp_SendPdu2Rlc` 解引用 `Rlc_CheckRbNode(1)` 返回的 `0xc9` 死机。`0xc9` 不是随机垃圾——

- `0xc9 = 0xc8(200B) | PREV_INUSE`，正是 **dlmalloc chunk 头的 size 字段**；
- `do_check_*` 各函数以 `lw ?,4(chunk)` 读这个字段做 DEBUG 一致性校验（`do_check_free_chunk`/`do_check_chunk` 等）；
- 即崩溃值是 **DEBUG 版 `do_check_*` 校验链压栈时，把某个 chunk 的 size 推进了紧贴栈底的 RLC 表**。

这条把"受害值"从"巧合的小整数"升级为"allocator 内部数据"，与"栈溢出溅射"唯一吻合。用 `peek.py` 看受害槽即可识别（小整数 + 相邻是 `g_osApSystemMem`/do_check 代码指针）。

## 4. cm.push 帧布局速查

核实帧大小时用 `disasm.py` 看函数 prologue 的 `cm.push`：

| 指令 | 帧大小 | 说明 |
|---|---|---|
| `cm.push {ra},-16` | 16B | 仅 ra（罕见） |
| `cm.push {ra,s0-s1},-16` | 16B | ra+s0+s1（如 `Pdcp_SendPdu2Rlc`） |
| `cm.push {ra,s0-s2},-16` | 16B | ra+s0..s2（如 `do_check_chunk`）——**注意 s1 持的是 chunk 指针(a1)，非 chunk-size** |
| `cm.push {ra,s0-s3},-32` | 32B | ra+s0..s3（如 `do_check_free_chunk`/`do_check_malloced_chunk`） |

要点：
- ra 保存在帧顶（约 `sp+0x0c`），帧底部留空 → 解释 fill-scan 偏大；
- **不要假设所有 do_check 帧同大小**（`do_check_chunk` 是 16B，`do_check_free_chunk` 是 32B），逐帧重建溢出链时按各自 prologue 算；
- `cm.mvsa01 s0,s1` = 把 a0/a1 存进 s0/s1（读参数），解读保存的 s-regs 时注意。

## 5. 核实命令清单

```bash
SKILL_DIR="<base>"
DUMP=<dump_dir>; ELF=<ap.elf>

# 自动检测（首选）：水位 + 受害全局 + [溢出溅射检测] 三物证
python "$SKILL_DIR/scripts/system_stack.py" "$DUMP" "$ELF"

# 逐字核实 —— 跨栈底看背景指纹与 ra
python "$SKILL_DIR/scripts/peek.py" "$DUMP" <受害全局基址> 0x40 "$ELF"
python "$SKILL_DIR/scripts/peek.py" "$DUMP" <栈底-0x20> 0x40 "$ELF"     # 含栈底上下

# 反汇编核实崩溃指令 / 读表算术 / cm.push 帧大小
python "$SKILL_DIR/scripts/disasm.py" "$DUMP" "$ELF" <崩溃函数> +0x60
python "$SKILL_DIR/scripts/disasm.py" "$DUMP" "$ELF" <do_check_*>        # 看 cm.push -N
```

## 6. 反例：何时不是栈溢出

见到"全局被踩"先排除其它写入路径，再下栈溢出结论：
- **堆损坏**：受害地址在堆区（`g_osApSystemMem.base..end`）→ 用 `heap_walker.py`（越界写会留 size 异常 chunk）；
- **double-free / use-after-free**：受害在堆且 `g_osErrorLog` 含 `dlmalloc.c:2066` → 用 `double_free_detect.py`；
- **野指针写**：受害全局地址在全内存只出现 1 次（无代码持有指向它的指针）→ 支持栈溢出；若有多处引用则查写入者；
- **受害全局有合法业务写入者**：若某函数会写该全局非零值，则"非零=污染"不成立——需确认"仅释放路径写 0、其余全读"。

栈溢出的**独有指纹**仍是 ②背景填充切换（受害区未写位是 BSS 的 0，而栈区是 `0x24242424`）+ ①ra 越栈底——这两条别的机制给不出。
