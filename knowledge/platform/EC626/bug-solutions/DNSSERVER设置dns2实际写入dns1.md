# AT+DNSSERVER 设置 dns2 时实际写入 dns1 位置 原因分析

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **工作项 ID** | NA |
| **平台** | EC626 |
| **模块** | LWIP/DNS |
| **问题分类** | 参数错误 |
| **症状关键词** | DNSSERVER, dns2写入dns1, index参数未使用, NVM覆盖, 垃圾数据 |
| **根因概述** | nwy_app_set_dns_server函数接收index参数但从未使用，始终将IP写入ipv4Dns[0]，且dnsCfg局部变量未从NVM读取当前配置导致另一DNS槽位被栈上垃圾数据覆盖 |
| **调用链摘要** | nwy_app_at_dnsserver_func → nwy_app_set_dns_server(index未使用) → mwSetAndSaveDefaultDnsConfig(整体覆盖) |
| **检索关键词** | DNSSERVER, dns2写入dns1, index参数, NVM覆盖, DNS配置, nwy_platform, MidWareDefaultDnsCfg, 垃圾数据 |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 修复记录](#3-修复记录)
- [4. 相关文件](#4-相关文件)

---

## 1. 问题描述

通过 AT+DNSSERVER 命令分别设置 dns1 和 dns2 后，查询结果显示 dns2 的值被写入了 dns1 的位置，dns2 显示为垃圾值。不论设置 index 为 1 还是 2，IP 地址始终写入 dns1 槽位。

| 操作 | 输入 | 返回 |
|------|------|------|
| 查询初始值 | `AT+DNSSERVER?` | `+DNSSERVER: dns1: 255.255.255.255,dns2: 140.173.1.0` |
| 设置 dns1 | `AT+DNSSERVER=1,123.122.121.122` | OK |
| 设置 dns2 | `AT+DNSSERVER=2,111.111.111.111` | OK |
| 查询结果 | `AT+DNSSERVER?` | `+DNSSERVER: dns1: 111.111.111.111,dns2: 111.0.0.0` |

**期望结果**：`dns1: 123.122.121.122, dns2: 111.111.111.111`

**实际结果**：
- dns1 被覆盖为 111.111.111.111（dns2 的值）
- dns2 显示 111.0.0.0（栈上垃圾数据）
- dns1 原先设置的 123.122.121.122 丢失

**问题类型**：单日志分析

---

## 2. 根本原因

**`nwy_app_set_dns_server()` 函数接收 `index` 参数（1 或 2）但从未使用它，始终将 IP 地址写入 `dnsCfg.ipv4Dns[0]`（dns1 槽位）。同时 `dnsCfg` 局部变量未从 NVM 读取当前配置，导致未修改的 DNS 槽位被栈上垃圾数据覆盖后整体写入 NVM。**

### 2.1 关键日志证据

#### AT命令日志

```
[2026-06-04 10:07:46.725] SEND >>>>>>>>>> AT+DNSSERVER?
[2026-06-04 10:07:46.747] +DNSSERVER: dns1: 255.255.255.255,dns2: 140.173.1.0
[2026-06-04 10:07:46.747] OK

[2026-06-04 10:07:57.690] SEND >>>>>>>>>> AT+DNSSERVER=1,123.122.121.122
[2026-06-04 10:07:57.754] OK

[2026-06-04 10:08:10.172] SEND >>>>>>>>>> AT+DNSSERVER=2,111.111.111.111
[2026-06-04 10:08:10.376] OK

[2026-06-04 10:08:20.387] SEND >>>>>>>>>> AT+DNSSERVER?
[2026-06-04 10:08:20.418] +DNSSERVER: dns1: 111.111.111.111,dns2: 111.0.0.0
[2026-06-04 10:08:20.418] OK
```

#### 模块AP日志

```
# 初始查询
[10:07:46.728] NWY_FRM: nwy_app_at_table.c 332 +DNSSERVER found!
[10:07:46.729] NWY_FRM: nwy_app_at_func_nwdns.c 199 at resp:+DNSSERVER: dns1: 255.255.255.255,dns2: 140.173.1.0

# AT+DNSSERVER=1,123.122.121.122
[10:07:57.694] NWY_FRM: nwy_app_at_func_nwdns.c 160 nwy_judge_ip4_or_ip6:123.122.121.122 4
[10:07:57.694] NWY_FRM: nwy_platform.c 1732 nwy_app_set_dns_server =
[10:07:57.695] NWY_FRM: nwy_platform.c 1830 nwy_app_set_dns_server NWY_APP_IP_V4
[10:07:57.695] NWY_FRM: nwy_platform.c 1938 nwy_app_set_dns_server validDns ture          ← 写入成功

# AT+DNSSERVER=2,111.111.111.111
[10:08:10.177] NWY_FRM: nwy_app_at_func_nwdns.c 160 nwy_judge_ip4_or_ip6:111.111.111.111 4
[10:08:10.177] NWY_FRM: nwy_platform.c 1732 nwy_app_set_dns_server =
[10:08:10.177] NWY_FRM: nwy_platform.c 1830 nwy_app_set_dns_server NWY_APP_IP_V4
[10:08:10.177] NWY_FRM: nwy_platform.c 1938 nwy_app_set_dns_server validDns ture          ← 写入成功（但写到了 dns1 位置）

# 最终查询
[10:08:20.391] NWY_FRM: nwy_app_at_table.c 332 +DNSSERVER found!
[10:08:20.392] NWY_FRM: nwy_app_at_func_nwdns.c 199 at resp:+DNSSERVER: dns1: 111.111.111.111,dns2: 111.0.0.0
```

### 2.2 代码调用链

| 信息 | 值 |
|------|-----|
| **入口函数** | `nwy_app_at_dnsserver_func()` |
| **调用链** | `nwy_app_at_dnsserver_func()` → `nwy_app_set_dns_server()` → `mwSetAndSaveDefaultDnsConfig()` |
| **问题位置** | `nwy_app_set_dns_server()` — `nwy_platform.c:1726-1954` |

**调用链分析**：
- AT handler 正确解析了 `dns_index`（1 或 2）并传入 `nwy_app_set_dns_server()`
- `nwy_app_set_dns_server()` 接收 `index` 参数但从未引用，使用硬编码初始值 0 的 `validIpv4DnsNum` 作为数组索引
- `mwSetAndSaveDefaultDnsConfig()` 执行整体结构体覆盖，将未初始化的 dns2 数据也写入 NVM

### 2.3 问题分析

#### 缺陷1（根因）：`index` 参数从未使用，始终写入 dns1 槽位

**文件**：`nwy_platform.c:1726-1954`

```c
int nwy_app_set_dns_server(int sim_id, int cid, int index, nwy_ip_addr_type* ip_addr)
{
    MidWareDefaultDnsCfg    dnsCfg;                         // L1728: 未初始化的局部变量
    UINT8   paraIdx = 0, validIpv4DnsNum = 0, ...;          // L1731: validIpv4DnsNum 硬编码为 0
    // ... index 参数从未被引用 ...

    // L1852-1860: 始终写入 ipv4Dns[0]（dns1 槽位）
    if (validIpv4DnsNum >= MID_WARE_DEFAULT_DNS_NUM)
    {
        validIpv4DnsNum = MID_WARE_DEFAULT_DNS_NUM - 1;
    }
    dnsCfg.ipv4Dns[validIpv4DnsNum][0] = ip4_addr1(&(ipAddr.u_addr.ip4));  // → ipv4Dns[0]
    dnsCfg.ipv4Dns[validIpv4DnsNum][1] = ip4_addr2(&(ipAddr.u_addr.ip4));  // → ipv4Dns[0]
    dnsCfg.ipv4Dns[validIpv4DnsNum][2] = ip4_addr3(&(ipAddr.u_addr.ip4));  // → ipv4Dns[0]
    dnsCfg.ipv4Dns[validIpv4DnsNum][3] = ip4_addr4(&(ipAddr.u_addr.ip4));  // → ipv4Dns[0]
```

AT handler 中 `dns_index` 解析正确（1-2 范围校验通过），也正确传入了 `index` 参数：

```c
// nwy_app_at_func_nwdns.c:153
if (!nwy_app_at_get_int(arg->params_list, 0, &dns_index, 1, 2, 1))  // 范围 [1,2]
    return nwy_app_at_func_resp_err(arg->at_channel);
// ...
// L171
nwy_app_set_dns_server(sim_id, profile_id, dns_index, &ip_addr)  // dns_index 正确传入
```

但 `nwy_app_set_dns_server()` 内部从未使用 `index`，`validIpv4DnsNum` 始终为 0。

**对照**：同平台 EC7XX 的 `nwy_platform.c` 中相同函数也存在同样问题，均未使用 `index` 参数。

#### 缺陷2（次要）：`dnsCfg` 未从 NVM 读取当前配置，导致另一个 DNS 槽位被垃圾数据覆盖

**文件**：`nwy_platform.c:1728`

```c
MidWareDefaultDnsCfg    dnsCfg;     // 未初始化，栈上内容随机
// ... 中间没有任何 mwGetDefaultDnsConfig(&dnsCfg) 调用 ...
// L1945
mwSetAndSaveDefaultDnsConfig(&dnsCfg);   // 整体覆盖 NVM
```

查看 `mw_config.c:2095-2112`，`mwSetAndSaveDefaultDnsConfig()` 实现为整体结构体覆盖：

```c
void mwSetAndSaveDefaultDnsConfig(MidWareDefaultDnsCfg *pDnsCfg)
{
    // ...
    memcpy(&(mwNvmConfig.defaultDnsCfg), pDnsCfg, sizeof(MidWareDefaultDnsCfg));
    mwSaveNvmConfig();
}
```

而查询函数 `nwy_app_get_dns_server()`（L1955-2029）正确使用了 `mwGetDefaultDnsConfig(&dnsCfg)` 先读取 NVM：

```c
int nwy_app_get_dns_server(int sim_id, int cid, int pdp_type, char *dns1, char *dns2)
{
    MidWareDefaultDnsCfg    dnsCfg;
    mwGetDefaultDnsConfig(&dnsCfg);     // ← 正确：先读取 NVM 当前配置
    // ...
}
```

**对比**：设置函数缺少 `mwGetDefaultDnsConfig()` 调用，导致只修改了目标槽位，另一槽位为栈上垃圾数据。

### 2.4 完整触发链路（以 `AT+DNSSERVER=2,111.111.111.111` 为例）

```
1. AT+DNSSERVER=2,111.111.111.111
2. nwy_app_at_dnsserver_func()
   → dns_index = 2, dns_str = "111.111.111.111"
   → nwy_judge_ip4_or_ip6("111.111.111.111") = NWY_APP_IP_V4      ✓ 分类正确
   → nwy_dss_inet_aton() 解析成功                                    ✓ 解析正确
   → nwy_app_set_dns_server(0, profile_id, 2, &ip_addr)             ✓ index=2 正确传入

3. nwy_app_set_dns_server(sim_id, cid, index=2, ip_addr)
   → dnsCfg 局部变量（未初始化，栈上随机数据）
   → validIpv4DnsNum = 0                                             ✗ 未使用 index=2
   → dnsCfg.ipv4Dns[0] = {111, 111, 111, 111}                        ✗ 写入 [0] 而非 [1]
   → dnsCfg.ipv4Dns[1] = 栈上垃圾（可能为 {111, 0, 0, 0}）           ✗ 未从 NVM 读取

4. mwSetAndSaveDefaultDnsConfig(&dnsCfg)
   → 将整个 dnsCfg（含垃圾 dns2）覆盖写入 NVM                        ✗ 整体覆盖

5. 查询 AT+DNSSERVER?
   → mwGetDefaultDnsConfig(&dnsCfg) 从 NVM 读取
   → dnsCfg.ipv4Dns[0] = {111, 111, 111, 111} → dns1 = 111.111.111.111  ✗ dns1 被覆盖
   → dnsCfg.ipv4Dns[1] = {111, 0, 0, 0}       → dns2 = 111.0.0.0        ✗ 垃圾数据
```

### 2.5 NVM 数据流图

```
                    设置前 NVM                      设置 dns1=1 后                      设置 dns2=2 后
                ┌──────────────────┐            ┌──────────────────┐             ┌──────────────────┐
ipv4Dns[0]     │ 255.255.255.255  │  ───────→  │ 123.122.121.122  │  ─────────→ │ 111.111.111.111  │ ← dns2 的值写到了 dns1
(dns1)         │                  │            │                  │             │                  │
                ├──────────────────┤            ├──────────────────┤             ├──────────────────┤
ipv4Dns[1]     │ 140.173.1.0      │  ───────→  │ 栈垃圾（覆盖）    │  ─────────→ │ 栈垃圾（覆盖）    │ ← 未从 NVM 读取
(dns2)         │                  │            │                  │             │ = 111.0.0.0      │
                └──────────────────┘            └──────────────────┘             └──────────────────┘
```

---

## 3. 修复记录

### 3.1 修改文件

`PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/platform/EC626/nwy_platform.c`

### 3.2 修复内容

#### 修复1：使用 `index` 参数作为 DNS 数组偏移，并增加边界校验（L1733 + L1740-1748）

**缺陷**：`validIpv4DnsNum` 硬编码为 0，`index` 参数从未使用，导致无论设置 dns1 还是 dns2，IP 地址始终写入 `ipv4Dns[0]`。

**AI 审核发现（2026.06.05）**：直接 `UINT8 validIpv4DnsNum = index - 1` 存在整数下溢风险：
- `index=0` 时，`index - 1 = -1`（int），赋给 `UINT8` 后回绕为 `255`
- 后续 bounds check `255 >= MID_WARE_DEFAULT_DNS_NUM` 会钳位到 `1`，写入 dns2 槽位但函数仍返回 OK
- `nwy_app_set_dns_server()` 作为独立 API 函数应自行做防御性校验

**修复**：将初始化与赋值分离，先声明安全默认值 0，再校验 `index` 合法性后显式赋值。

```c
// 初次修复（2026.06.04）— 存在整数下溢风险
UINT8   paraIdx = 0, validIpv4DnsNum = index - 1, validIpv6DnsNum = index - 1;

// 二次修复（2026.06.05）— 分离声明与赋值，增加边界校验
UINT8   paraIdx = 0, validIpv4DnsNum = 0, validIpv6DnsNum = 0;           // 安全默认值
// ... 其他声明 ...
NWY_APP_LOG_HIGH("nwy_app_set_dns_server =", 0, 0, 0);
/*Begin: Add by niusulong for validate index param and compute array offset in 2026.06.05*/
if (index < 1 || index > MID_WARE_DEFAULT_DNS_NUM)                      // 防御性校验
{
    NWY_APP_LOG_HIGH("nwy_app_set_dns_server invalid index", 0, 0, 0);
    return -1;
}
validIpv4DnsNum = (UINT8)(index - 1);                                   // 校验后安全赋值
validIpv6DnsNum = (UINT8)(index - 1);
/*End: Add by niusulong for validate index param and compute array offset in 2026.06.05*/
```

#### 修复2：修改前先从 NVM 读取当前 DNS 配置（L1749-1751）

**缺陷**：`dnsCfg` 为未初始化局部变量，`mwSetAndSaveDefaultDnsConfig()` 执行整体覆盖写，导致未修改的 DNS 槽位被栈上垃圾数据覆盖。

**修复**：在修改 `dnsCfg` 前调用 `mwGetDefaultDnsConfig()` 读取当前 NVM 配置，确保未修改的槽位保留原始值。

```c
// 修复前
NWY_APP_LOG_HIGH("nwy_app_set_dns_server =", 0, 0, 0);
#if 0

// 修复后
NWY_APP_LOG_HIGH("nwy_app_set_dns_server =", 0, 0, 0);
/*Begin: Add by niusulong for read current dns config from NVM before modify in 2026.06.04*/
mwGetDefaultDnsConfig(&dnsCfg);
/*End: Add by niusulong for read current dns config from NVM before modify in 2026.06.04*/
#if 0
```

### 3.3 修复后数据流

```
                    设置前 NVM                      设置 dns1=1 后                      设置 dns2=2 后
                ┌──────────────────┐            ┌──────────────────┐             ┌──────────────────┐
ipv4Dns[0]     │ 255.255.255.255  │  ───────→  │ 123.122.121.122  │  ─────────→ │ 123.122.121.122  │ ← 保留 dns1 值
(dns1)         │                  │            │                  │             │                  │
                ├──────────────────┤            ├──────────────────┤             ├──────────────────┤
ipv4Dns[1]     │ 140.173.1.0      │  ───────→  │ 140.173.1.0      │  ─────────→ │ 111.111.111.111  │ ← 正确写入 dns2
(dns2)         │                  │            │                  │             │                  │
                └──────────────────┘            └──────────────────┘             └──────────────────┘
```

### 3.4 修复验证

| 操作 | 输入 | 期望返回 |
|------|------|----------|
| 查询初始值 | `AT+DNSSERVER?` | `+DNSSERVER: dns1: 255.255.255.255,dns2: 140.173.1.0` |
| 设置 dns1 | `AT+DNSSERVER=1,123.122.121.122` | OK |
| 设置 dns2 | `AT+DNSSERVER=2,111.111.111.111` | OK |
| 查询结果 | `AT+DNSSERVER?` | `+DNSSERVER: dns1: 123.122.121.122,dns2: 111.111.111.111` |

---

## 4. 相关文件

| 文件 | 角色 |
|------|------|
| `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/platform/EC626/nwy_platform.c` | **根因文件** — `nwy_app_set_dns_server()` (L1726-1954) 未使用 `index` 参数，未从 NVM 读取当前配置；`nwy_app_get_dns_server()` (L1955-2029) 对比参考（正确读取 NVM） |
| `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_at_proc/src/nwy_app_at_func_nwdns.c` | AT 命令处理入口 — `nwy_app_at_dnsserver_func()` (L135-208) 正确解析 dns_index 并传入 |
| `PLAT/middleware/eigencomm/common/src/mw_config.c` | NVM 配置管理 — `mwSetAndSaveDefaultDnsConfig()` (L2095-2112) 整体覆盖写；`mwGetDefaultDnsConfig()` (L2119-2131) 读取当前配置 |
| `PLAT/middleware/eigencomm/common/inc/mw_config.h` | 数据结构定义 — `MidWareDefaultDnsCfg` 含 `ipv4Dns[2][4]`，`MID_WARE_DEFAULT_DNS_NUM = 2` |