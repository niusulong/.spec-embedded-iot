---
name: spec-project-overview
description: >-
  项目概览生成器。扫描项目根目录，生成粗粒度（目录级）的结构化项目概览文档：目录结构映射、模块清单、构建系统识别、技术栈识别、入口点分析。
  它是一份"项目地图"——用最少代价（看目录名、构建文件、顶层结构）勾勒嵌入式项目整体布局，是后续深入分析的前置步骤。
  当用户说"spec 项目概览"、"spec 了解项目"、"这个项目结构是什么样"、"帮我梳理下项目全貌"、"项目 overview"、"项目地图"时使用——
  只要用户意图是"了解项目整体布局 / 有哪些模块 / 怎么编译 / 从哪启动"，即使没明说"概览"也应触发。
  边界：要深入某个模块的代码实现 → 用 spec-code-summary；要查 C 编码规范 → 用 spec-neoway-coding-standards；
  要生成需求/方案/计划 → 用 spec-requirement-generator 等；本技能只做目录级全景，不解析具体函数实现。
version: 1.3
author: niusulong
---

## 核心定位

这是项目的"地图"：用最少的代价（看目录名、构建文件、顶层结构）勾勒出一个嵌入式项目的整体布局，让用户和后续的 spec 技能快速知道"这个项目分几层、有哪些模块、用什么编译、从哪启动"。

三个原则：
1. **宏观优先，目录级粒度**：基于目录名、文件名、构建配置推断功能，不读函数体。细节留给 spec-code-summary。
2. **推断要给依据**：每个结论附"凭什么这么判断"（哪个目录/文件），避免凭空臆测或编造。
3. **识别而非罗列**：不是把目录树抄一遍，而是把上百个目录归类成"应用层/中间件/系统层/工具链/构建产物"，并标出哪些值得深入。

## 执行流程

### 1. 确定项目路径与平台

- **project_path**：从用户输入提取；未给则用当前工作目录。向用户复述确认后再扫。
- **平台名**：取 project_path 末级目录名（大写）。例：
  - `D:\EC626` → 平台 `EC626`（EigenComm，Cortex-M）
  - `U:\UIS8850` → 平台 `UIS8850`（Unisoc/展锐，Cortex-R）
  - 用户显式指定的平台名优先于路径推断。
  > 注意：平台名只代表芯片系列，**不代表 OS**。同一平台不同项目可能跑不同 RTOS（实测同一份 UIS8850 SDK 用的是 FreeRTOS，而非刻板印象里的 ThreadX）。OS 一律按下表看 kernel 目录里的实际源码，不要凭平台名猜。

### 2. 环境检查与输出路径

- 检查项目下 `.spec/` 是否存在；不存在则提示用户先跑 `spec-init`，但概览本身不依赖它，可继续。
- 知识库输出路径（Windows 下 `~` 需展开为 `C:\Users\<用户>\`）：
  `C:\Users\<用户>\.agents\knowledge\platform\<平台名>\项目概览.md`
  （在 bash 里等价于 `~/.agents/knowledge/platform/<平台名>/项目概览.md`）

### 3. 扫描项目结构

用 **Glob** 和 **Bash** 扫描。注意：本环境**没有 `list_directory` 工具**——列目录用 `Bash` 跑 `ls`/`find`，模式匹配用 `Glob(pattern, path)`（两个参数，不是 shell 风格的 `glob "x" path`）。

```bash
# 顶层与关键子目录（用 Bash 的 ls）
ls <project_path>
ls <project_path>/PLAT        # EC626 的真正内容常在 PLAT/ 下，顶层很简洁
ls <project_path>/components  # UIS8850 等组件式项目，模块都在这里
```
```
# 构建文件（用 Glob）
Glob(pattern="**/Makefile",        path=<project_path>)
Glob(pattern="**/CMakeLists.txt",  path=<project_path>)
Glob(pattern="**/Kconfig",         path=<project_path>)
Glob(pattern="**/*.mk",            path=<project_path>)
```

**必须排除的目录**（扫描时跳过——它们要么是构建产物，要么是第三方工具链/源码，会拖慢扫描并严重污染识别结果）：
`build/` `out/` `gccout/` `.git/` `node_modules/` `prebuilts/`（工具链，常数百 MB）`tools/`（常含工具链示例）`samples/` `cmake/share/` `newlib/`，以及任何第三方源码树（如 `micropython/`、`lwip/` 原始包）。

### 4. 识别项目信息

**平台 / OS 识别**（目录特征 → 结论，附依据）：

| 目录特征 | 推断 |
|---|---|
| `PLAT/` 下分 `driver/middleware/os/device` | Neoway 平台 SDK（EC626 风格） |
| `components/` + `Kconfig` + `CMakeLists.txt` | 组件式 SDK（ASR/ESP 风格） |
| `os/` 或 `kernel/` 下有 freertos 源（tasks.c/queue.c/timers.c） | FreeRTOS |
| ThreadX 源码或 `tx_`/`TX_` 前缀文件 | ThreadX |

**构建系统识别**：

| 配置文件 | 构建系统 |
|---|---|
| `Makefile` + `*.mk`（如 `nwy_common.mk`） | Make + 厂商定制 |
| `CMakeLists.txt` + `cmake/` | CMake |
| `Kconfig` + `.config` | Kconfig 配置 |
| `GccBuild_*.bat` / `build_nwy.sh` | GCC 工具链 + 厂商构建脚本 |

**入口点识别**（重要——不要只找 `main.c`）：

嵌入式真正的入口很少叫 `main.c`。按下面优先级找，且**必须先排除第三方目录**（prebuilts/tools/samples/micropython 等）：

1. 厂商应用目录：`nwy_project/`、`project/`、`applications/`、`app/` 下的 app 入口
2. 启动组件：`components/appstart`、`components/bootloader`、`apploader`
3. 启动汇编：`startup*.S` / `vectors.*` / 复位向量表
4. 任务创建点：在应用目录里搜 `xTaskCreate` / `tx_thread_create` / `osTaskNew`
5. 最后才是 `main.c` / `app_main`——但要确认它不在第三方目录内。

> 为什么这条特别强调：在 EC626 里 `**/main.c` 只会命中 `tools/gcc/share/.../samples/retarget/main.c`（工具链示例）；在 UIS8850 里会命中几十个 micropython 示例。直接 `glob **/main.c` 会把用户完全带偏。

**技术栈识别**（看目录名/库名）：lwIP（网络）、mbedTLS/tls（加密）、FatFS/littlefs（文件系统）、FreeRTOS/ThreadX（RTOS）、micropython（脚本引擎）等。

### 5. 生成文档

- 读 `references/project-overview-template.md`，填占位符后写入第 2 步的知识库路径。
- 目录树限 2-3 层，排除第 3 步列出的目录。
- "模块清单"和"建议深入分析的功能域"必须落到真实存在的目录，并在"后续分析建议"里把 spec-code-summary 作为推荐工具。

### 6. 完整性检查 + 自动纳入向量索引

- 模板 9 个章节齐全；识别不到的章节要明确写出"未识别到，原因…"，**不要留空更不要编造**。
- **自动纳入向量索引**：文档落盘后自动跑一次增量索引，让 spec-bug-analyzer / spec-code-summary 等后续技能能按语义检索到本概览（文档虽已写到 knowledge 目录，但向量索引需单独构建，否则语义检索搜不到）：
  ```bash
  python ~/.agents/skills/spec-knowledge-archiver/scripts/embed_indexer.py update --collection project-overview
  ```
  （Windows bash 下 `~` 展开为 `C:\Users\<用户>\`；若未展开则改用该绝对路径。）
  - **成功**：向用户报告脚本输出的 `新增 X, 更新 Y, 清理孤儿 Z` 结果。
  - **失败**（脚本异常 / Python 缺失 / 首次需下载约 450MB 嵌入模型）：**不中止**——文档已落盘、按路径引用不受影响，索引只是检索增强。给一句警告，并提示可稍后手动跑 `spec-knowledge-archiver` 补救。
  - **跳过**：若用户本次明确说"先不索引 / 跳过索引"，则跳过本步。

## 加密文件提示

若 `Read` 配置文件时返回乱码或读不到内容，项目源码可能被 EsafeNet（绿盾）加密——转用 `esafenet-file-io` 技能透明读写（仅 Windows）。

## 输出格式要求

1. 文档必须有目录（`## 目录` + 锚点链接）。
2. 表格化：目录映射、模块清单、技术栈用表格。
3. 每个推断附依据（来自哪个目录/文件）。

---

现在开始：等待用户确认 project_path，或直接用其给出的路径。
