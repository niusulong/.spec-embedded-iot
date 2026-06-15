---
name: spec-project-overview
description: >-
  项目概览生成器。分析项目根目录，生成结构化的项目概览文档，包含目录结构映射、模块清单、技术栈识别、构建系统分析和入口点分析。
  输出粗粒度（目录级）的项目整体布局文档，帮助了解项目全貌。
  当用户说"使用spec 技能生成项目概览"、"spec 项目概览"、"spec 了解项目"时使用。
version: 1.0
author: niusulong
---

## 核心原则
1. **宏观视角**：分析项目整体布局，不深入单个模块实现细节
2. **目录级粒度**：基于目录名、文件名、配置文件推断功能，不解析具体代码
3. **标准化输出**：严格按照标准模板生成 .md 文档
4. **快速索引**：为用户提供"可以深入分析的功能域"清单

## 执行流程

### 1. 确定项目路径
- 从用户输入提取 **project_path**（可选），默认为当前工作目录

### 2. 环境检查 + 确定平台
检查 `.spec` 目录是否存在，不存在则提示用户先运行 `spec-init`。

确定**平台名**（如 EC626、ASR1603）：
- 优先从用户输入获取
- 或从项目路径推断（如 `D:\EC626\` → 平台名 `EC626`）

确认中央知识库路径：`~/.agents/knowledge/platform/{平台名}/`

### 3. 扫描项目结构

使用以下工具进行跨平台扫描：

| 工具 | 用途 | 示例 |
|------|------|------|
| `list_directory` | 列出目录结构 | `list_directory {project_path}` |
| `glob` | 查找文件 | `glob "Makefile" {project_path}` |

**扫描命令**：
```
# 目录树（递归扫描）
list_directory {project_path}

# 构建文件查找
glob "Makefile" {project_path}
glob "CMakeLists.txt" {project_path}
glob "Kconfig" {project_path}

# 入口文件查找
glob "**/main.c" {project_path}
```

### 4. 识别项目信息

**项目类型识别**：
| 目录特征 | 项目类型 |
|---------|---------|
| `arch/`, `kernel/`, `drivers/` | 操作系统/内核 |
| `components/`, `modules/` | IoT固件/中间件 |
| `src/`, `include/`, `lib/` | 库/组件 |
| `app/`, `application/` | 应用程序 |

**构建系统识别**：
| 配置文件 | 构建系统 |
|---------|---------|
| `Makefile`, `*.mk` | Make |
| `CMakeLists.txt` | CMake |
| `Kconfig`, `.config` | Kbuild |
| `SConscript` | SCons |

**技术栈识别**：
| 目录/特征 | 技术栈 |
|---------|--------|
| `lwip/`, `lwIP/` | lwIP |
| `mbedtls/`, `tls/` | mbedTLS |
| `freertos/`, `rtt/` | RTOS |
| `fatfs/`, `littlefs/` | 文件系统 |

### 5. 生成文档

**输出路径**：`~/.agents/knowledge/platform/{平台名}/项目概览.md`

**使用模板**：读取 `references/project-overview-template.md`，填充占位符后输出。

### 6. 完整性检查
- 章节完整，缺失章节需告知用户
- 目录树排除：build/, out/, .git/, node_modules/
- 所有推断基于目录名/文件名/配置文件

## 输出格式要求
1. **必须有目录**：使用 `## 目录` + `- [章节名](#锚点)` 格式
2. **表格化数据**：目录映射、模块清单、技术栈等用表格
3. **明确推断依据**：说明如何从目录名/文件名推断功能

---

现在开始，等待用户输入项目路径或使用默认当前目录。
