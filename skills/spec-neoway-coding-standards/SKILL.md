---
name: spec-neoway-coding-standards
description: >-
  Neoway 嵌入式软件 C 语言编码规范查询助手。提供编码风格、命名规范、注释规范、宏控规范等查询功能。
  当用户说"spec 编码规范"、"spec 代码规范"时使用。
version: 3.0
author: niusulong
---



## 核心原则

1. **统一风格**: 统一公司软件编程风格
2. **提高可读性**: 提高软件代码的易读性、可靠性和稳定性
3. **降低成本**: 减少软件维护成本，最终提高软件产品生产力
4. **基本准则**: 维持代码的易读易维护、保持代码简明清晰、尽可能复用代码

## 执行流程

### Step 1：识别查询类型

根据用户输入识别查询类型：

| 查询类型 | 关键词示例 | 返回内容 |
|----------|-----------|----------|
| 完整规范 | "编码规范"、"所有规范"、"完整规范" | 完整规范文档 |
| 命名规范 | "函数命名"、"变量命名"、"文件命名" | 命名规范章节 |
| 注释规范 | "注释格式"、"如何注释"、"文件头注释" | 注释规范章节 |
| 宏控规范 | "宏命名"、"功能宏"、"宏控规则" | 宏控规范章节 |
| 代码风格 | "缩进"、"花括号"、"空格" | 编码风格章节 |
| 变量规范 | "全局变量"、"变量命名" | 变量规范章节 |
| 代码检查 | "检查这段代码"、"是否符合规范" | 执行检查并返回结果 |
| 模板生成 | "生成模板"、"函数模板"、"文件模板" | 对应模板代码 |

### Step 2：返回规范内容或执行检查

根据识别的查询类型，从下方规范内容中提取对应章节返回。

### Step 3：提供示例

返回规范内容时，同时提供代码示例。

---

# Neoway 编码规范（完整版）

<!-- ================================================================== -->
<!--                    第一部分：编码风格规范                            -->
<!-- ================================================================== -->

## 一、编码风格规范

### 1.1 缩进规范

| 场景 | 规范 |
|------|------|
| 新增文件 | 统一使用 **4个空格** 缩进 |
| 平台文件新增函数/结构体/枚举 | 统一使用 **4个空格** 缩进 |
| 平台文件原始代码 | 与原始代码保持一致 |

**示例**:
```c
// 新增代码 - 4空格缩进
void nwy_test_function(void)
{
    if (condition) {
        do_something();
    }
}
```

### 1.2 花括号规范

**原则**: 同一文件只能有一种风格

| 风格 | 格式 | 使用场景 |
|------|------|----------|
| 类Window风格 | 左括号单独一行 | MODEM侧代码 |
| 类Linux风格 | 左括号紧跟语句 | APP侧代码 |

**类Linux风格示例**:
```c
// if语句
if (*f_pos) {
    addr = end;
    return 0;
} else {
    addr = start;
    return 0;
}

// while循环
while (addr != end) {
    func(addr);
    addr++;
}

// do-while
do {
    func(addr);
    addr++;
} while (addr == end);

// for循环
for (addr = start; addr != end; addr++) {
    func1(addr);
    func2(addr);
}

// 结构体
struct hello_android_dev {
    int val;
    struct semaphore sem;
    struct cdev dev;
};

// 函数体（特殊：左括号换行）
int func(void)
{
    /*...*/
}

// switch语句（两种方式均可）
switch (animal) {
case ANIMAL_CAT:
    handle_cat();
    break;
default:
    printk(...);
    break;
}
```

### 1.3 空格规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 关键字周围 | 加空格 | `if (foo)`, `while (foo)`, `for (i = 0; i < NR_CPUS; i++)` |
| 函数名与括号 | 无空格 | `wake_up_process(task);` |
| 参数之间 | 加空格 | `test(int a, int b)` |
| 二元/三元操作符 | 前后加空格 | `sum = a + b;`, `nr = nr ? 1 : 0;` |
| 一元操作符 | 不加空格 | `foo++`, `--foo`, `~mask` |

### 1.4 每行代码长度

- **限制**: 120个字符以内
- **换行规则**: 后续行与第一行保持TAB倍数缩进，尽量与括号对齐

**函数定义换行示例**:
```c
// 方式1
void nwy_client_voice_ind_cb
(
    client_handle_type hndl,
    uint32 msg_id,
    void *ind_struct,
    uint32 ind_len
)

// 方式2
void nwy_client_voice_ind_cb(client_handle_type hndl, uint32 msg_id,
                            void *ind_struct, uint32 ind_len)

// 方式3
void nwy_client_voice_ind_cb(client_handle_type hndl,
                            uint32 msg_id,
                            void *ind_struct,
                            uint32 ind_len)
```

**函数调用换行示例**:
```c
nwy_test_func1(hndl,
            MCM_VOICE_COMMAND_REQ_V01,
            &req_msg,
            resp_msg,
            NULL,
            &token_id);
```

### 1.5 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 文件命名 | 以 `nwy` 开头 | `nwy_test.c` |
| 函数命名 | 小写+下划线，以 `nwy_` 开头 | `void nwy_voice_call_request()` |
| 变量命名 | 小写+下划线，**不以** `nwy_` 开头 | `int pin_mode_flag;` |
| 结构体命名 | 小写+下划线，以 `nwy_` 开头，加 `_t` 后缀 | `nwy_voice_ind_type_t` |

**命名示例**:
```c
// 文件名: nwy_audio_api.c, nwy_audio_core.c

// 函数命名
void nwy_voice_call_request(void);
int nwy_get_adc(int fd, int port);

// 变量命名
int g_pin_mode_flag = 0;          // 全局变量以 g_ 开头
static char g_msisdn_value[128];  // 全局变量必须加 static

// 结构体命名
typedef enum nwy_voice_ind_type {
    NWY_VOICE_CALL_IND = 0,
    NWY_VOICE_MUTE_IND = 1,
    NWY_VOICE_DTMF_IND = 2,
} nwy_voice_ind_type_t;

typedef struct nwy_audio_config {
    int sample_rate;
    int channels;
} nwy_audio_config_t;
```

### 1.6 变量使用规范

| 规范项 | 要求 |
|--------|------|
| 全局变量 | 必须以 `g_` 开头 |
| 全局变量定义 | 必须加 `static` |
| 跨文件引用 | 需封装成接口调用 |
| 变量初始化 | 使用前应该初始化 |
| 数组使用 | 每次使用前检查是否清空 |

**示例**:
```c
// 正确示例
static int g_pin_mode_flag = 0;
static char g_msisdn_value[128] = {0};

// 跨文件访问 - 封装接口
// file1.c
static int g_global_value = 0;

int get_global_value(void) {
    return g_global_value;
}

// file2.c
extern int get_global_value(void);
int value = get_global_value();
```

<!-- ================================================================== -->
<!--                    第二部分：注释规范                                -->
<!-- ================================================================== -->

## 二、注释规范

### 2.1 语言原则

代码注释原则上要求全部使用英文，如默认源文件使用中文，则也可以使用中文。

### 2.2 默认源文件修改注释

#### 增加代码

```c
/*Begin: Add + Author + Reason + Date*/
code
/*End: Add + Author + Reason + Date*/
```

**示例**:
```c
/*Begin: Add by niusulong for/to client init in 2018.05.16*/
nwy_client_init(&client_hndl);
/*End: Add by niusulong for/to client init in 2018.05.16*/
```

#### 修改代码

```c
/*Begin: Modify + Author + Reason + Date*/
code
/*End: Modify + Author + Reason + Date*/
```

**示例**:
```c
/*Begin: Modify by niusulong for/to fix the bug 62349 in 2018.05.16*/
if (x < y)
    mask = POLLIN | POLLRDNORM;
/*End: Modify by niusulong for/to fix the bug 62349 in 2018.05.16*/
```

#### 删除代码

```c
/*Begin: Delete + Author + Reason + Date*/
/*code*/
/*End: Delete + Author + Reason + Date*/
```

**示例**:
```c
/*Begin: Delete by niusulong for/to fix the bug 62349 in 2018.05.16*/
/*if (x <= y)
    mask = POLLIN;*/
/*End: Delete by niusulong for/to fix the bug 62349 in 2018.05.16*/
```

#### 注意事项

- 不要叠加注释，只保留最新的注释
- 如果宏控添加了注释，宏控包含的代码可以不加注释

### 2.3 新增源文件注释

#### 文件头部注释

```c
/*====*====*====*====*====*====*====*====*====*====*====*====*====*====*====*====
   Copyright (c) 2017 Neoway Technologies, Inc.
   All rights reserved.
   Confidential and Proprietary - Neoway Technologies, Inc.
   Author: niusulong
   Date: 2018.05
*====*====*====*====*====*====*====*====*====*====*====*====*====*====*====*====*/
```

#### 函数注释格式1

```c
/*****************************************************************************
    FUNCTION nwy_get_adc
/*****************************************************************************
@Desc： Get adc value of the channel
@Para： fd – file handle
       port – the Channel of Adc
@Return: 0 - True, -1 - Error
*****************************************************************************/
```

#### 函数注释格式2 (Linux内核风格)

```c
/**
 * kobject_set_name - Set the name of a kobject
 * @kobj: struct kobject to set the name of
 * @fmt: format string used to build the name
 *
 * This sets the name of the kobject. If you have already added the
 * kobject to the system, you must call kobject_rename() in order to
 * change the name of the kobject.
 *
 * Return: 0 - True, -1 - Error
*/
```

**注意**: 同一源文件中只能使用一种注释风格。

<!-- ================================================================== -->
<!--                    第三部分：宏控规范                                -->
<!-- ================================================================== -->

## 三、宏控规范

### 3.1 基本原则

| 规范项 | 要求 |
|--------|------|
| 宏名 | 必须为大写加下划线 |
| 项目宏 | 控制编译源文件 |
| 功能宏 | 负责打开对应项目的功能模块 |
| 新功能 | 必须添加功能宏控，宏名中不要出现项目名称 |
| 客户定制 | 使用客户定制宏 |
| 回滚性 | 去掉宏之后，编译正常 |
| 调试/参数/函数宏 | 统一以 `NWY` 开头 |

### 3.2 #ifdef、#if defined 与 #if

| 场景 | 使用方式 | 示例 |
|------|----------|------|
| 单个宏判断 | `#ifdef` 或 `#if defined` | `#ifdef XXX` |
| 复杂宏判断 | `#if defined` | `#if defined(XXX) \|\| defined(YYY)` |
| 宏值判断 | `#if` | `#if (XXX) \|\| (YYY > 3)` |

```c
// 例1：单个宏
#ifdef XXX
#else
#endif

#if defined XXX
#else
#endif

// 例2：复杂判断
#if defined (XXX) || defined (YYY)
#elif defined (KKK)
#endif

// 例3：宏值判断
#define XXX 0
#define YYY 4

#if (XXX) || (YYY > 3) || defined (YYY)
#elif defined (KKK)
#endif
```

### 3.3 宏控缩进

**写法1: 顶格开始**
```c
#if defined (FEATURE_JSR_BMA250_AUTO_CLB)
    this_client = client;
    acc_cali_bma2x2 = data;
    gbma250_data_offset.x = 0;
    gbma250_data_offset.y = 0;
    gbma250_data_offset.z = 0;
#endif
```

**写法2: 在原代码基础上前缩进**
```c
#ifdef XXX
void test4(int a)
{
#ifdef YYY
    if (a) {
    #ifdef ZZZ
        printf("%d\n", a);
    #endif
    }
#endif
}
#endif
```

### 3.4 宏控命名规则

#### 3.4.1 项目宏

```
NWY_PROJECT_XXX
```
由软件经理管控，研发人员不涉及修改和引用。

#### 3.4.2 框架类宏

```
FEATURE_NWY_AT
FEATURE_NWY_OPEN
```

**示例**:
```c
static void atEngineTaskEntry(void *argument)
{
#ifdef FEATURE_NWY_AT
    nwy_at_init();
#endif
}
```

#### 3.4.3 功能宏

```
FEATURE_NWY_[AT(OPEN)]_FUNC_[FUNC1]_[BZ(DL)]
```

| 字段 | 说明 | 示例 |
|------|------|------|
| AT(OPEN) | 区分AT和open版本，不区分可省略 | `FEATURE_NWY_AT_FTP` |
| FUNC | 大功能描述 | `FEATURE_NWY_AT_BASE`, `FEATURE_NWY_AT_AUDIO` |
| FUNC1 | 子功能（可选） | `FEATURE_NWY_AT_AUDIO_DTMF` |
| BZ(DL) | 行业特性（可选） | `FEATURE_NWY_AT_FTP_DL` |

**示例**:
```c
// 标准版本FTP功能
#ifdef FEATURE_NWY_AT_FTP
#endif

// 驱动配置（不需要加AT和OPEN字段）
#ifdef FEATURE_NWY_SENSOR_LTR559
#endif

// 基本指令集
#ifdef FEATURE_NWY_AT_BASE
#endif

// 音频DTMF子功能
#ifdef FEATURE_NWY_AT_AUDIO_DTMF
void atCmdHandleCDTMF(atCommand_t *cmd) {
}
#endif

// 电力行业FTP功能
#ifdef FEATURE_NWY_AT_FTP_DL
#endif
```

#### 3.4.4 客户定制宏

```
FEATURE_NWY_CUS_[INDEX]
```

| 字段 | 说明 |
|------|------|
| CUS | 客户标识（由软件经理制定） |
| INDEX | 可选，同一客户多个项目的区分 |

**示例**:
```c
// 美团客户定制总宏
#ifdef FEATURE_NWY_CUS_MT
    // 美团FTP定制
    #ifdef FEATURE_NWY_AT_FTP
    #endif

    // 美团audio定制
    #ifdef FEATURE_NWY_AT_AUDIO
    #endif
#endif

// 美团项目A分宏
#define FEATURE_NWY_CUS_MT_A

// 美团项目B分宏
#define FEATURE_NWY_CUS_MT_B
```

#### 3.4.5 数值和字符串宏

```c
#define FEATURE_NWY_POWERON_GPIO GPIO_4
#define FEATURE_NWY_UART_FILE "/dev/ttyHSL0"
#define FEATURE_NWY_FTP_MAX_LINK_NUM 100

#if (GPIO_4 == FEATURE_NWY_POWERON_GPIO)
#endif

fd = open(FEATURE_NWY_UART_FILE, ...);
```

#### 3.4.6 兼容友商宏

```
FEATURE_NWY_AT(OPEN)_COMPATIBLE_XXX
```

| 友商缩写 | 说明 |
|----------|------|
| QL | 移远 |
| ... | ... |

```c
#ifdef FEATURE_NWY_AT_COMPATIBLE_QL
#endif
```

#### 3.4.7 功能块内部宏

```c
#define NWY_STATE_CNT 4
#define NWY_LOW_CNT 3
#define NWY_HIGH_CNT 3
#define NWY_GPIO_CIR_SIZE 10
#define NWY_GPIO_DEBOUNCE_MSEC 15
```

#### 3.4.8 Kernel宏

```c
// 板级配置宏
CONFIG_BOARD_NWY_<板级配置>
// 示例: CONFIG_BOARD_NWY_E45T=y

// 驱动宏
CONFIG_NWY_<驱动类型>_<IC名>
// 示例: CONFIG_NWY_SENSORS_LTR559=y

// 功能宏
CONFIG_NWY_<驱动类型>_<功能/差异>
// 示例: CONFIG_NWY_DOUBLE_BATTERY=y
```

<!-- ================================================================== -->
<!--                    第四部分：代码检查清单                            -->
<!-- ================================================================== -->

## 四、代码检查清单

当用户请求检查代码时，按以下清单逐项检查：

### 4.1 编码风格检查

- [ ] 缩进是否使用4个空格
- [ ] 花括号风格是否与文件保持一致
- [ ] 关键字周围是否有空格
- [ ] 函数名与括号间是否无空格
- [ ] 二元操作符前后是否有空格
- [ ] 一元操作符与操作数间是否无空格
- [ ] 每行代码长度是否在120字符以内

### 4.2 命名规范检查

- [ ] 文件名是否以 nwy 开头
- [ ] 函数名是否小写+下划线，以 nwy_ 开头
- [ ] 变量名是否小写+下划线，不以 nwy_ 开头
- [ ] 全局变量是否以 g_ 开头并加 static
- [ ] 结构体名是否以 nwy_ 开头，加 _t 后缀

### 4.3 注释规范检查

- [ ] 新增文件是否有头部版权注释
- [ ] 重要函数是否有函数注释
- [ ] 修改基线代码是否添加 Begin/End 注释
- [ ] 注释风格是否与文件保持一致

### 4.4 宏控规范检查

- [ ] 宏名是否全大写加下划线
- [ ] 功能宏是否符合命名规则
- [ ] 宏控缩进是否正确
- [ ] 是否正确使用 #ifdef/#if defined/#if

<!-- ================================================================== -->
<!--                    第五部分：代码模板                                -->
<!-- ================================================================== -->

## 五、代码模板

### 5.1 新增源文件模板

```c
/*====*====*====*====*====*====*====*====*====*====*====*====*====*====*====*====
   Copyright (c) 2024 Neoway Technologies, Inc.
   All rights reserved.
   Confidential and Proprietary - Neoway Technologies, Inc.
   Author: niusulong
   Date: [YYYY.MM]
*====*====*====*====*====*====*====*====*====*====*====*====*====*====*====*====*/

#include "nwy_osi_api.h"
#include "nwy_common.h"

/*****************************************************************************
    FUNCTION nwy_example_function
/*****************************************************************************
@Desc： Brief description of function
@Para： param1 – description of parameter 1
       param2 – description of parameter 2
@Return: 0 - Success, -1 - Error
*****************************************************************************/
int nwy_example_function(int param1, char *param2)
{
    int ret = 0;

    if (param2 == NULL) {
        return -1;
    }

    // Function implementation
    ret = do_something(param1);

    return ret;
}
```

### 5.2 平台文件修改模板

```c
/*Begin: Add by niusulong for/to [修改原因] in [YYYY.MM.DD]*/
#ifdef FEATURE_NWY_XXX_FUNC
int nwy_new_function(void)
{
    // Implementation
}
#endif
/*End: Add by niusulong for/to [修改原因] in [YYYY.MM.DD]*/

/*Begin: Modify by niusulong for/to [修改原因] in [YYYY.MM.DD]*/
#ifdef FEATURE_NWY_XXX_FUNC
    // Modified code
#endif
/*End: Add by niusulong for/to [修改原因] in [YYYY.MM.DD]*/

/*Begin: Delete by niusulong for/to [修改原因] in [YYYY.MM.DD]*/
/*#ifdef FEATURE_OLD*/
/*    // Deleted code*/
/*#endif*/
/*End: Delete by niusulong for/to [修改原因] in [YYYY.MM.DD]*/
```

### 5.3 函数注释模板

#### 格式1：标准格式

```c
/*****************************************************************************
    FUNCTION nwy_get_adc
/*****************************************************************************
@Desc： Get adc value of the channel
@Para： fd – file handle
       port – the Channel of Adc
@Return: 0 - True, -1 - Error
*****************************************************************************/
```

#### 格式2：Linux内核风格

```c
/**
 * kobject_set_name - Set the name of a kobject
 * @kobj: struct kobject to set the name of
 * @fmt: format string used to build the name
 *
 * This sets the name of the kobject. If you have already added the
 * kobject to the system, you must call kobject_rename() in order to
 * change the name of the kobject.
 *
 * Return: 0 - True, -1 - Error
*/
```

---

## 交互规则

- 用户询问具体规范 → 从上方规范内容中提取对应章节返回
- 用户请求检查代码 → 使用检查清单逐项检查，给出具体修改建议
- 用户请求生成模板 → 返回标准代码模板
- 用户询问示例 → 返回规范示例代码
- 用户请求完整规范 → 返回完整规范文档

## 注意事项

1. 本规范对公司主流平台有效，部分小众平台因风格差异较大，与基线保持一致即可
2. 本规范自生效日期起，对以后新编写和修改的代码有约束力
3. 开发工具自动生成的代码可以不约束
4. 本文档是公司软件开发的标准化文档，其它规范标准不再有效
