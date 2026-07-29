# UIS8850 Dump 分析报告模板

复制本模板到 `.spec/bug/{工作项ID}_{问题描述}/Dump分析.md`，填入分析结果。
所有结论附脚本输出/反汇编作证据，区分"已验证"与"推断"。

---

# UIS8850 (N706-STD) {问题描述} Dump 分析

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。字段定义见
> `spec-knowledge-archiver/references/section0-spec.md`（统一规范，禁止自由发挥）。

| 字段 | 内容 |
|------|------|
| **工作项 ID** | [缺陷单号；必填，用于归档追溯。无则填 NA] |
| **平台** | [纯平台名：UIS8850；**禁止带架构后缀/括号/版本号**] |
| **模块** | [如 MQTT / LWIP / UART / FOTA] |
| **bug_type** | [**只能从 10 个规范值选一个**：时序竞争 / 内存越界 / 状态机异常 / 参数校验缺失 / 资源泄漏 / 编码错误 / 配置错误 / 硬件相关 / 协议实现错误 / AT回码格式。映射规则见 section0-spec.md] |
| **症状关键词** | [3-5 个关键词，逗号分隔，如: 蓝屏, 栈溢出, osiPanic] |
| **根因概述** | [一句话描述死机根因] |
| **调用链摘要** | [箭头串，如: taskA → foo() → bar() → 越界写 → HardFault] |
| **检索关键词** | [5-8 个检索词，中英文均可，逗号分隔] |
| **兄弟文件** | [选填，同 bug 目录下其它文件，如 Bug分析.md / 修改方案.md] |

## 0.1 Dump 现场信息

| 项 | 值 |
|---|---|
| Dump 目录 | `{绝对路径}` |
| 抓取时间 | {DTools 时间} |
| 平台 | UIS8850 |
| AP 当前固件 | {版本串} |
| CP/Modem 固件 | {svn 版本, 编译日期} |
| 升级方向 | {FOTA 方向, 死机时实际版本} |
| 死机类型 | gIsPanic={0/1}, gBlueScreenAbortType={0x??} |
| **直接根因** | **{一句话根因}** |

## 0.2 结论摘要

1. **版本**：死机时板子运行 {版本}（{如何判定}）。
2. **直接根因**：{根因性质, 如 FreeRTOS 任务栈溢出}。{触发路径}。
3. {其他关键发现}
4. {CP 并发 assert 等关联现象}
5. {FOTA/场景关联}

## 1. 平台与版本判定

### 1.1 平台架构
- ELF `e_machine = EM_ARM`，arm-none-eabi-gcc {版本}。
- 内存布局从 MAP `MEMORY` + ELF `PT_LOAD` 读（非硬编码），自动注册 dump `.bin`。

### 1.2 死机版本 = {版本}（确凿）
- dtools.log {是否做了版本校验}
- 双路交叉验证：PSRAM 全文搜版本串 + 两套 ELF 各读 gBuildRevision
- {判定依据}

## 2. AP 蓝屏现场 (gBlueScreenRegs)

### 2.1 关键全局量
| 符号 | 地址 | 值 | 说明 |
|---|---|---|---|
| gIsPanic | | | |
| gBlueScreenAbortType | | | |
| gBlueScreenRegs | | | |
| gBuildRevision | | | |
| gfupdateStat | | | |
| pxCurrentTCB | | | |

### 2.2 gBlueScreenRegs 寄存器现场
```
r0-r12 = ...
sp  = 0x...
lr  = 0x... -> {函数}
pc  = 0x... -> {函数}
cpsr= 0x... -> {模式解码}
```

### 2.3 osiPanic 机制确认
```asm
osiPanic: push{r3,lr}; ...; udf #255
```
{AP PC 是否在 osiPanic 内 → 软件/硬件 panic}

## 3. 根因：{根因类型}

### 3.1 栈回溯
{osiPanic 调用者 = sp+4, 调用链表}

### 3.2 {根因证据}
{反汇编铁证, 如 vTaskSwitchContext cmp 0xa5a5a5a5 → bl StackOverflowHook → b.w osiPanic}

### 3.3 {锁定任务/位置}
{pxCurrentTCB → 任务名, pxTopOfStack vs pxStack, 栈底 magic 破坏值}

### 3.4 完整根因链
```
{触发条件}
  → {中间过程}
    → {检测点}
      → {panic 路径}
        → 蓝屏
```

## 4. CP 侧并发 Assert（若有）
{CP 寄存器, CP PC 反汇编, CP15 读取, 上报路径, 与 AP 根因的关系}

## 5. 修复建议
1. {直接对策}
2. {深层排查}
3. {监控/预防}
4. {CP 侧, 版本管理等}

## 6. 分析方法与可追溯性
### 6.1 方法论要点
- 平台无关内存访问（自动注册 .bin）
- 内存布局从版本文件获取
- 版本判定双路交叉验证
- 架构适配（ARM + gBlueScreenRegs + arm 工具链）

### 6.2 证据产物
| 文件 | 用途 |
|---|---|
| analysis/01_uis8850_analyze.txt | 现场解析 |
| analysis/02_unwind.txt | 栈回溯 |
| analysis/03_threads.txt | 任务列表 |
| analysis/04_cp_assert.txt | CP assert |
| dtools.log | 抓取日志 |

### 6.3 关键地址速查
| 地址 | 含义 |
|---|---|
| ... | ... |
