# AT+DNSSERVER 设置无效 DNS 地址返回 OK 原因分析

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **工作项 ID** | NA |
| **平台** | EC626 |
| **模块** | DNS |
| **问题分类** | 参数错误 |
| **症状关键词** | 无效IP地址, 返回OK, NVM写入, inet_addr未校验 |
| **根因概述** | nwy_dss_inet_aton()函数调用inet_addr()解析IP地址后未检查返回值是否为INADDR_NONE，始终返回成功，导致无效IP地址通过校验链被写入NVM |
| **调用链摘要** | ATC_ProcessDNSSERVER → nwy_app_at_dnsserver_func → nwy_dss_inet_aton → nwy_app_set_dns_server |
| **检索关键词** | DNS, DNSSERVER, 无效地址, IP校验, inet_addr, INADDR_NONE, 参数校验缺失, AT命令 |

---

## 目录
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 相关文件](#3-相关文件)

---

## 1. 问题描述

通过 AT+DNSSERVER 命令设置无效的 DNS 服务器地址时，模块返回 OK 而非 ERROR。测试了三种无效输入，均未报错：

| 测试用例 | 输入 | 无效原因 | 实际返回 | 期望返回 |
|----------|------|----------|----------|----------|
| 用例1 | `AT+DNSSERVER=1,256.5.5.5` | IP 八位组越界（256>255） | OK | ERROR |
| 用例2 | `AT+DNSSERVER=2,www.baidu.com` | 域名不是 IP 地址 | OK | ERROR |
| 用例3 | `AT+DNSSERVER=2,-223.5.5.5` | 负数非法 IP | OK | ERROR |

查询结果 `AT+DNSSERVER?` 返回 `+DNSSERVER: dns1: 255.255.255.255,dns2: 140.173.1.0`，确认无效地址被存入 NVM。

**问题类型**：单日志分析

---

## 2. 根本原因

**`nwy_dss_inet_aton()` 函数调用 `inet_addr()` 解析 IP 地址后，未检查其返回值是否为 `INADDR_NONE`（解析失败标志），始终返回 `NWY_DSS_SUCCESS`，导致无效 IP 地址通过校验链。**

### 2.1 关键日志证据

#### AT命令日志

```
[2026-06-04 09:45:17.322] SEND >>>>>>>>>> AT+DNSSERVER=1,256.5.5.5
[2026-06-04 09:45:17.393] OK

[2026-06-04 09:45:28.982] SEND >>>>>>>>>> AT+DNSSERVER=2,www.baidu.com
[2026-06-04 09:45:29.055] OK

[2026-06-04 09:45:43.448] SEND >>>>>>>>>> AT+DNSSERVER=2,-223.5.5.5
[2026-06-04 09:45:43.475] OK

[2026-06-04 09:45:52.115] SEND >>>>>>>>>> AT+DNSSERVER?
[2026-06-04 09:45:52.129] +DNSSERVER: dns1: 255.255.255.255,dns2: 140.173.1.0
[2026-06-04 09:45:52.129] OK
```

#### 模块AP日志

```
# 用例1：AT+DNSSERVER=1,256.5.5.5
[09:45:17.326] AT CMD , RECV dump: 41 54 2B 44 4E 53 53 45 52 56 45 52 3D 31 2C 32 35 36 2E 35 2E 35 2E 35 0D
[09:45:17.326] ATCMD , decode AT: AT+DNSSERVER=1,256.5.5.5
[09:45:17.327] NWY_FRM: nwy_app_at_table.c 332 +DNSSERVER found!
[09:45:17.328] NWY_FRM: nwy_app_at_func_nwdns.c 160 nwy_judge_ip4_or_ip6:256.5.5.5 4    ← 判定为IPv4
[09:45:17.328] NWY_FRM: nwy_platform.c 1732 nwy_app_set_dns_server =
[09:45:17.328] NWY_FRM: nwy_platform.c 1830 nwy_app_set_dns_server NWY_APP_IP_V4
[09:45:17.328] NWY_FRM: nwy_platform.c 1938 nwy_app_set_dns_server validDns ture          ← 被误判为有效
[09:45:17.328] LFS file open , path: midwareconfig.nvm                                     ← 写入NVM
[09:45:17.384] AT CMD , RESP: OK                                                           ← 返回OK
```

### 2.2 代码调用链

| 信息 | 值 |
|------|-----|
| **入口函数** | `ATC_ProcessDNSSERVER()` |
| **调用链** | `ATC_ProcessDNSSERVER()` → `AT_NWY_CmdFunc_Adpt()` → `nwy_app_at_dnsserver_func()` → `nwy_judge_ip4_or_ip6()` → `nwy_dss_inet_aton()` → `nwy_app_set_dns_server()` |
| **问题位置** | `nwy_dss_inet_aton()` — `nwy_platform.c:1443-1452` |

**调用链分析**：
- `nwy_judge_ip4_or_ip6()` 将字符串分类为 IPv4 或 IPv6（仅检查字符集）
- `nwy_dss_inet_aton()` 负责解析 IPv4 地址字符串并校验合法性——**此函数存在根本缺陷**
- `nwy_app_set_dns_server()` 仅检查 IP 是否为 0.0.0.0（空地址），不做进一步校验
- 调用链中唯一能拦截无效 IP 的环节是 `nwy_dss_inet_aton()`，但它永远返回成功

### 2.3 问题分析

#### 缺陷1（根因）：`nwy_dss_inet_aton()` 不检查 `inet_addr()` 返回值

**文件**：`nwy_platform.c:1443-1452`

```c
int32 nwy_dss_inet_aton(const char *cp, void *addr)
{
    uint32 ip = inet_addr(cp);          // 解析失败时返回 INADDR_NONE (0xFFFFFFFF)
    memcpy(addr, &ip, sizeof(uint32));  // 将 0xFFFFFFFF 复制到 addr → 变成 255.255.255.255
    return NWY_DSS_SUCCESS;             // 始终返回成功！从不检查 inet_addr 是否失败
}
```

`inet_addr()` 的标准行为：
- 解析成功：返回网络字节序的 32 位 IP 地址
- 解析失败：返回 `INADDR_NONE`（即 `0xFFFFFFFF` = `255.255.255.255`）

**对于三种无效输入的效果**：

| 输入 | inet_addr 返回值 | 存入 addr 的 IP | 校验结果 |
|------|------------------|-----------------|----------|
| `256.5.5.5` | `INADDR_NONE` (0xFFFFFFFF) | 255.255.255.255 | 通过（非 0.0.0.0） |
| `www.baidu.com` | `INADDR_NONE` (0xFFFFFFFF) | 255.255.255.255 | 通过（非 0.0.0.0） |
| `-223.5.5.5` | 行为未定义/溢出 | 未知乱码 | 通过（非 0.0.0.0） |

**对照**：同文件中的 IPv6 版本 `nwy_dss_inet6_aton()` 实现正确：
```c
int32 nwy_dss_inet6_aton(const char *cp, void *addr)
{
    int ret = ip6addr_aton(cp, addr);
    if (ret == 1)
        return NWY_DSS_SUCCESS;   // 只在成功时返回 SUCCESS
    return NWY_DSS_ERROR;         // 失败时返回 ERROR
}
```

#### 缺陷2（次要）：`nwy_judge_ip4_or_ip6()` 对非 IP 字符串分类不正确

**文件**：`nwy_app_at_func_nwdns.c:10-58`

- `www.baidu.com`：字符 'w' 既非数字/点也非十六进制/冒号 → 跳出循环 → `len != strLen` → 走到末尾 `return retValue`（默认值 `NWY_APP_IP_V4`）
- `-223.5.5.5`：字符 '-' 既非数字/点 → 同上 → 默认返回 `NWY_APP_IP_V4`

这意味着域名和负数地址都被当作 IPv4 处理，然后进入 `nwy_dss_inet_aton()` 解析。由于缺陷1，解析失败不报错。

#### 缺陷3（次要）：`nwy_app_set_dns_server()` 校验不充分

**文件**：`nwy_platform.c:1838-1935`

```c
validDns = TRUE;   // 第1838行：无条件置为 TRUE
if (ipAddr.type == IPADDR_TYPE_V4)
{
    if (ip4_addr_isany(&(ipAddr.u_addr.ip4)))  // 仅检查 0.0.0.0
    {
        validDns = FALSE;
    }
    // ... 存入 NVM
}
```

- 无条件先将 `validDns` 设为 `TRUE`
- 仅过滤全零地址 `0.0.0.0`
- 不检查 `255.255.255.255`（广播地址）、组播地址等无效 DNS 地址

### 2.4 完整触发链路（以 `256.5.5.5` 为例）

```
1. AT+DNSSERVER=1,256.5.5.5
2. nwy_judge_ip4_or_ip6("256.5.5.5")
   → 所有字符是数字或点 → 返回 NWY_APP_IP_V4                ✓ 分类正确
3. nwy_dss_inet_aton("256.5.5.5", &ip_addr.addr)
   → inet_addr("256.5.5.5") = INADDR_NONE (0xFFFFFFFF)
   → memcpy 存储 255.255.255.255 到 ip_addr
   → 返回 NWY_DSS_SUCCESS (0)                                 ✗ 未检测失败
4. handler 检查: nwy_dss_inet_aton(...) != 0 → false          ✗ 校验绕过
5. nwy_app_set_dns_server(...)
   → validDns = TRUE
   → ip4_addr_isany(255.255.255.255) = false                  ✗ 非全零就通过
   → mwSetAndSaveDefaultDnsConfig() 写入 NVM
   → 返回 0
6. handler 检查: nwy_app_set_dns_server(...) == 0 → true
   → 返回 OK                                                  ✗ 最终结果
```

---

## 3. 相关文件

| 文件 | 角色 |
|------|------|
| `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_comm/platform/EC626/nwy_platform.c` | **根因文件** — `nwy_dss_inet_aton()` (L1443-1452) 始终返回成功；`nwy_app_set_dns_server()` (L1721-1949) 校验不充分 |
| `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_at_proc/src/nwy_app_at_func_nwdns.c` | AT 命令处理入口 — `nwy_app_at_dnsserver_func()` (L135-208)；IP 分类 — `nwy_judge_ip4_or_ip6()` (L10-58) |
| `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_at_proc/src/nwy_app_at_table.c` | AT 命令注册表 (L265) |
| `PLAT/middleware/thirdparty/NWY_FRAMEWORK/nwy_app_at_proc/platform/EC626/nwy_app_at_parser_adpt.c` | CMS 适配层 (L182-185) |
| `PLAT/middleware/eigencomm/at/atcust/src/atec_cust_cmd_table.c` | CMS AT 命令表定义 (L1505-1507, L2108) |