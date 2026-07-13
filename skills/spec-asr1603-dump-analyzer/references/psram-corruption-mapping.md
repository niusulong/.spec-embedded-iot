# PSRAM 代码损坏范围深度映射

> 当 Step 5（AXF vs DDR 初步对比）检测到代码损坏时，执行此深度映射流程。
> Step 5 只用了 PC 附近 64~256 字节做初步判定，无法回答"损坏有多大、影响哪些段、边界在哪里"。

## 1. 为什么需要深入映射

损坏范围的大小和边界直接指向根因方向：

| 损坏特征 | 根因方向 |
|----------|---------|
| 恰好 4KB 且 4KB 对齐 | Flash 扇区操作溢出到相邻 PSRAM |
| 跨越多个段、MB 级 | PSRAM 总线级别故障 |
| 有清晰的起始边界 | DMA 传输目标地址错误 |
| 仅影响特定段 | 该段的加载/刷新机制有问题 |

## 2. 多点采样（快速定位损坏边界）

对 Step 5 发现的损坏段，在三个位置各采样 256 字节，确认损坏是否覆盖整段。同时对未损坏段（如 LR 所在段）采样，确认损坏的选择性。

```bash
# 采样 PC 所在段的段首/段中/段尾
python "scripts/ddr_code_compare.py" <axf_file> <ddr_file> \
  --pc <段首地址> --base <DDR基地址> --size 256
python "scripts/ddr_code_compare.py" <axf_file> <ddr_file> \
  --pc <段尾地址-256> --base <DDR基地址> --size 256

# 采样 LR 所在段（未损坏对比组）
python "scripts/ddr_code_compare.py" <axf_file> <ddr_file> \
  --pc <LR地址> --base <DDR基地址> --size 256
```

**快速判定**：三点全部损坏 → 整段损坏（进入 §3 确认边界）；部分损坏 → 需要二分查找边界（§4）。

## 3. 全段扫描（精确损坏率）

对确认损坏的段执行更大范围的扫描（如 4KB~1MB），获取精确的损坏率、主导字节模式和损坏分布：

```bash
# 扫描 PC 所在段的大范围
python "scripts/ddr_code_compare.py" <axf_file> <ddr_file> \
  --pc <段首地址> --base <DDR基地址> --scan <段大小或固定如4096>

# 如需更大范围，用 --scan 逐步扩大
python "scripts/ddr_code_compare.py" <axf_file> <ddr_file> \
  --pc <段首地址> --base <DDR基地址> --scan 0x100000
```

## 4. 损坏边界定位（二分搜索）

如果损坏有清晰的起始/结束边界（非整段损坏），用二分法定位边界地址：

```
从段首开始，每次将采样点向未损坏方向移动一半距离，直到找到损坏→完好的分界点。
记录分界地址，精确到 256 字节粒度。
```

## 5. 输出汇总格式

将损坏范围映射结果整理为以下表格，这些数据直接输入根因定位决策树：

| 项目 | 内容 |
|------|------|
| 损坏段 | 段名、虚拟地址范围、段大小 |
| 损坏范围 | 起始地址 ~ 结束地址（精确到 KB） |
| 损坏率 | N% 字节不一致 |
| 损坏类型 | DMA_OVERWRITE / BIT_FLIP / PSRAM_BUS_FAILURE / PARTIAL_OVERWRITE |
| 主导字节模式 | 0xAA 占 X%、0x55 占 Y%（PSRAM 总线故障特征） |
| 未损坏段 | 段名、采样点、完好率（对比组） |

## 6. 损坏类型判定速查

| 特征 | 类型 | 典型根因 |
|------|------|---------|
| 大区域（>4KB）+ 完美 4KB 对齐 | DMA_OVERWRITE | DMA 传输目标地址错误 |
| 恰好 4KB | FLASH_SECTOR | Flash 扇区擦写溢出到相邻 PSRAM |
| ≤8 字节 | BIT_FLIP | PSRAM 总线 glitch / 时序裕量不足 |
| 中等大小，非对齐 | PARTIAL_OVERWRITE | 堆溢出 / 缓冲区越界写入代码区域 |
| 整段代码（MB级），0xAA 占主导 | PSRAM_BUS_FAILURE | QSPI 总线错误 / DMA 覆写 |
| 整段代码，0xAA+0x55 > 60%，段边界对齐 | **BIST_CHECKERBOARD** | **PSRAM BIST 残留 — 段从未被加载** |

## 7. 代码段未加载 vs 代码被损坏 — 区分方法

当 AXF vs DDR 对比发现不一致时，需要区分两种根本不同的场景：

| 特征 | 代码段未加载 | 代码被损坏 |
|------|------------|-----------|
| DDR 主导字节 | 0xAA/0x55 checkerboard | 随机/特定模式 |
| 不一致范围 | 精确对齐 ELF section 边界 | 可能不对齐 |
| 段首和段尾 | 全部不一致 | 可能部分不一致 |
| 相邻段 | 完好（正常加载） | 可能也受影响 |
| LR 所在段 | 通常正常（已加载段） | 无关 |
| `full-analyze` 输出 | `CODE SECTION NOT LOADED` | `CODE CORRUPTED` |

### 确认步骤

1. **full-analyze 自动检测**：自动执行段归属 + 边界采样 + checkerboard 识别
2. **DDR 属性搜索**：搜索 DDR dump 中控制段加载的配置属性
   ```bash
   python "scripts/dump_analyzer.py" ddr-search \
     --ddr <DDR> --base <BASE> --string "<与段加载相关的关键词>"
   ```
   常见关键词因平台而异（如启动模式开关、段加载条件标志），参考各平台的启动文档。
3. **调用链分析**：确认调用链是否跨越已加载段和未加载段
4. **scatter/linker script 检查**：确认该段的放置规则和加载条件
5. **常见原因**：条件加载的段中包含了无条件调用路径上的函数
