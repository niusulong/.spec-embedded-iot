# UIS8850 内存布局获取指南

**核心原则：内存布局从版本文件获取，不硬编码。** 同一平台不同项目裁剪后，PSRAM
大小、IRAM 边界、堆范围都会变，硬编码 region 列表必然踩坑（8850 的 AP ram 区从
`0x802ec000` 起，不是 PSRAM 起点 `0x80000000`）。

## 1. 三个来源（交叉验证）

### 来源 A：MAP `MEMORY` 配置（最权威）

MAP 文件开头的 `Memory Configuration` 段，链接脚本定义的内存区：

```
Memory Configuration

Name             Origin             Length             Attributes
flash            0x0000000060020000 0x000000000024c000 xr
ram              0x00000000802ec000 0x0000000000514000 xrw
sram             0x0000000000100000 0x0000000000004000 xrw
```

- `flash` = XIP 代码区（`.text` 在此执行）
- `ram` = PSRAM 里 AP 可用的 RAM 区（代码+数据+堆+BSS）。**注意 Origin 不是
  PSRAM 物理起点**——`0x80000000~0x802ec000` 是 CP/共享区
- `sram` = IRAM（AP 代码 + osiPanic 等）

### 来源 B：ELF `PT_LOAD` 段（运行时加载地址）

```python
from elftools.elf.elffile import ELFFile
for seg in ELFFile(open(elf,'rb')).iter_segments():
    if seg['p_type'] == 'PT_LOAD':
        print(seg['p_vaddr'], seg['p_memsz'], seg['p_flags'])
```

PT_LOAD 告诉运行时哪些虚拟地址有内容。`p_flags & 1`（X 位）= 可执行段 = 代码。
关键 sections：`.text`（flash XIP）、`.data`（PSRAM 已初始化）、`.bss`（PSRAM，
全局量在此）。

### 来源 C：dump 目录 `.bin` 文件名（实际抓取的区段）

DTools 约定：`.bin` 文件名 = hex 基址，文件大小 = 区段长度。例：
- `80000000.bin` = PSRAM（基址 0x80000000，8MB）
- `00100000.bin` = AP IRAM（0x00100000，16KB）
- `50800000.bin` = aon_iram（CP/AON 代码区）
- `10100000.bin` = cp_iram
- `50039000.bin` = watchdog 寄存器（外设）

dtools.log 的 cfg 段也列出每个区：`[cfg:N] name=psram addr=0x80000000 size=0x800000`。

## 2. 读取全局量的方法（平台无关）

**自动注册 dump 目录所有 hex 命名 `.bin`**，按 `[base, base+len)` 建索引：

```python
from common import Mem
mem = Mem(dump_dir)   # 默认 scan_all=True, 注册所有 .bin
# 符号地址从 ELF 读
addr = syms.lookup("gIsPanic")[0]   # 0x8030cce4
val = mem.u8(addr)                   # 自动找到 80000000.bin 的对应 offset
```

符号地址从 ELF `.symtab` 读，落在哪个 `.bin` 就从哪个读。**完全平台/项目无关**，
任何裁剪都能工作。

## 3. 别名处理

某些平台 PSRAM 有别名（如 8852 的 `0x80000000` ↔ `0x40000000`）。8850 实测 AP
符号地址都在 `0x8xxxxxxx`（无 `0x4xxxxxxx` 别名）。若符号地址落在别名区，显式
传入 `regions=[("80000000.bin", 0x40000000)]` 补充。

## 4. 判断地址类型

| 地址范围 | 含义 | 来源 |
|---|---|---|
| `0x00100000~0x00104000` | AP IRAM（代码+部分全局） | sram |
| `0x60020000~0x6024c000` | flash XIP 代码（`.text`） | flash |
| `0x802ec000~0x80800000` | PSRAM AP RAM（数据/堆/BSS/任务栈） | ram |
| `0x80000000~0x802ec000` | PSRAM CP/共享区（CP assert 文本常在此） | psram 低段 |
| `0x50800000~0x50814000` | aon_iram（CP/AON 代码） | aon_iram |
| `0x10100000~0x10134000` | cp_iram（CP 代码） | cp_iram |
| `0x50039000` | watchdog 寄存器 | 外设 |

`Symbols.is_exec_code(addr)` 基于 ELF PT_LOAD 可执行段判断（动态，非硬编码），
用于栈回溯时过滤代码地址。

## 5. FOTA 场景的版本判定

dtools.log 若有 `gBuildRevision in _elf_ vs board` 比对，直接用。否则（操作员未
点版本校验）：

1. **PSRAM 全文搜版本串**（不依赖 ELF）：搜产品名前缀（如 `8850BM_cat1bis_plus`），
   列出所有版本串及地址。
2. **每个候选 ELF 读 `gBuildRevision`**：地址从 ELF 读，读出的串即板上实际版本。
   两套 ELF 地址可能相同（布局接近），读出值一致 = 板上实际版本。

`gBuildRevision` 是板上**当前运行固件**的版本标识，读出什么就是什么版本（FOTA
方向 003→002 但死机时仍跑 003 的情况，读出 003 串即证）。
