# ARM 工具链使用指南（arm-none-eabi objdump / nm / readelf）

QCX216 仓库自带 GCC 10 工具链，路径 `PLAT/tools/gcc/arm-none-eabi/bin/`（`objdump.exe` /
`nm.exe` / `readelf.exe`，文件名**不带** `arm-none-eabi-` 前缀）。它是 ARM Thumb-2 反汇编的
**权威后端**（正确解 ITE 条件块 / MSR / 宽指令，自带 DWARF 源码行），优先于自写的纯 Python
反汇编器（`qcx216_disasm.py`）与 capstone（需 `pip install capstone`，未必可用）。

## 为何用工具链

| 能力 | 纯 Python 反汇编器 | capstone | **objdump（工具链）** |
|------|---------------------|----------|----------------------|
| Thumb-2 完整性 | 覆盖高频指令，罕见指令降级 `.short` | 完整 | 完整 |
| ITE 条件块 | ⚠️ 漏解（曾误判 poolId，见下） | 正确 | 正确 |
| 源码行 file:line | ❌ | ❌ | ✅（`-l`，替代 addr2line） |
| 依赖 | 无 | `pip install capstone` | 仓库自带，零依赖 |
| 函数边界/大小 | ❌ | ❌ | ✅（`<func>:` + 符号表 size） |

> **前车之鉴**：纯 Python 反汇编器漏解 `ITE LS` 条件块（把 `MOVLS/MOVHI` 当普通 MOV），
> 曾导致 `OsaCreateFastSignal` 的 poolId 误判（误为 2，实为 1）。**有工具链时务必用 objdump**
> 解条件指令。

## 工具链查找（`qcx216_toolchain.find_toolchain`）

顺序：① 环境变量 `QCX216_ARM_TOOLCHAIN`（bin 目录）；② `<repo>/PLAT/tools/gcc/arm-none-eabi/bin`
（从 dump/ELF/cwd/技能目录向上找含 `PLAT/` 的仓库根）；③ `PATH`。返回 `{objdump, nm, readelf}`
路径 dict，找不到返回 `{}`（调用方降级 capstone/纯 Python）。

## 常用命令

### objdump 反汇编（带源码行）— 替代 addr2line + 反汇编
```bash
OBJDUMP=PLAT/tools/gcc/arm-none-eabi/bin/objdump.exe
# 反汇编地址范围 + 源码行
$OBJDUMP -d -C --no-show-raw-insn -l --start-address=0x50E4 --stop-address=0x5112 ap_at_command.elf
```
输出（每条指令带 `file.c:line`）：
```
000050e4 <vListInsert>:
vListInsert():
E:/.../PLAT/os/freertos/src/list.c:104
    50e4:   push    {r4, r5, lr}
...
E:/.../list.c:158
    5102:   str     r1, [r2, #8]      ← 崩溃点，R2=0 → 写 0x8 → HardFault
```
- `-d` 反汇编；`-C` 解 C++ 符号；`--no-show-raw-insn` 去原始字节；`-l` 附源码行。
- 无 `addr2line` 时，`-l` 的 `file:line` 就是地址→源码行的来源。

### nm 符号表（交叉验证 ElfReader）
```bash
NM=PLAT/tools/gcc/arm-none-eabi/bin/nm.exe
$NM -S -C ap_at_command.elf | grep vListInsert
# 000050e4 0000002e T vListInsert
```
`-S` 带 size，`-C` 解符号。可核对 `ElfReader.sym_at` 的符号/大小是否一致。

### readelf（section 布局，备用）
```bash
READELF=PLAT/tools/gcc/arm-none-eabi/bin/readelf.exe
$READELF -S ap_at_command.elf          # section 头（代码/RAM 段范围）
```

## Python API（`qcx216_toolchain.py`）

```python
import qcx216_toolchain as tc

tc.find_toolchain()                      # {objdump, nm, readelf} 或 {}
tc.has_objdump()                         # bool
tc.toolchain_status()                    # 人类可读状态（用于 full-analyze 头部）

# 反汇编范围 → [(addr, mnem, ops, "file:line"|None)]，失败 None（降级）
tc.objdump_disasm(elf_path, 0x50E4, 0x5112, with_line=True)

# 反汇编整个函数（用 ElfReader 定范围）
tc.objdump_func(elf_path, elf_reader, 0x5102, pad_before=0, pad_after=0)

# nm 符号 → [(addr, size, type, name)]，失败 None
tc.nm_symbols(elf_path)
```

## 分工：工具链 vs pyelftools

| 场景 | 用工具链 | 用 pyelftools（`ElfReader`） |
|------|---------|-----------------------------|
| 反汇编崩溃指令 | ✅ objdump（首选，带源码行） | 降级：capstone / 纯 Python |
| 地址→函数名+偏移 | 都可（objdump 符号 / nm） | ✅ `sym_at`（已缓存，快） |
| 地址→源码行 | objdump `-l`（回退） | ✅ `locate`/`line_at`（.debug_line） |
| 符号精确查找 | nm（交叉验证） | ✅ `find_symbol`（O(1)） |
| section 是代码还是数据 | readelf -S | ✅ `is_code`/`is_ram`（section flags） |
| 读 ELF section 字节（超 dump 代码反汇编） | — | ✅ `read_u8`/`read_code` |

**原则**：符号查询 / section 判定用 pyelftools（快、已缓存）；**反汇编一律优先 objdump**
（权威、带源码行、解条件指令）。两者互补，不互斥。`disasm`/`full-analyze` 的
`render_disasm_around()` 已实现"objdump 优先 → capstone → 纯 Python"自动降级。

## 常见坑

1. **文件名无前缀**：仓库工具是 `objdump.exe` 不是 `arm-none-eabi-objdump.exe`。objdump 反汇编
   不依赖文件名（读 ELF 头识别架构），故通用 `objdump.exe` 能反汇编 ARM ELF。
2. **超 dump 代码段**：协议栈代码常在 `0x8Cxxxx~0x9Axxxx`（超 dump 0x540000）。objdump 从 ELF
   反汇编这些段（不依赖 dump），而纯 Python/capstone 需 `make_mem` 回退 ELF 读字节。
3. **objdump 慢启动**：首次对大 ELF 反汇编需数秒（加载 DWARF）；范围反汇编（`--start/stop`）快。
4. **`-l` 源码行解析**：objdump 源码行格式 `<path>:<line>` 或 `<path>:<line> (discriminator N)`，
   `_parse_objdump` 已处理；函数标签 `<func>:` 不含行号，已排除。
