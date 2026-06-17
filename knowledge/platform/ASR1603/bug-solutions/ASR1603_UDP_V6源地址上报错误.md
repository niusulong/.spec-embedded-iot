# Bug 分析报告：UDP V6 链路源地址上报错误

## Section 0：结构化摘要

| 字段 | 内容 |
|------|------|
| 工作项 ID | 7018786802 |
| 平台 | ASR1603 |
| 模块 | AT 命令 / UDP 服务端 / IPv6 地址处理 |
| 问题分类 | IPv6 地址格式化类型转换错误 |
| 症状关键词 | UDPSENDS ERROR、UDPRECV(S) 源 IP 错误、IPv6 地址截断、V6 链路收不到数据 |
| 根因概述 | UDP 服务端 `+UDPRECV(S)` URC 生成时，`nwy_app_ip6addr_ntoa()` 入参类型不匹配，将 `struct nwy_ps_in6_addr*` 直接强转 `void*` 当作 `nwy_ip_addr_type*` 使用，内部按 `addr->addr.v6`（偏移 type 字段后的 uint64[2]）读取，导致取到的 IPv6 地址只有后半段（4组），前半段丢失，生成的 URC 报告了错误且不完整的源 IPv6 地址 |
| 调用链摘要 | `nwy_app_at_udp_server_cb` → `nwy_app_ip6addr_ntoa(&addr_v6->ps_sin6_addr)` ← 类型不匹配 |
| 检索关键词 | UDPSENDS ERROR, UDPRECV, IPv6 地址错误, ip6addr_ntoa, nwy_app_ip6addr_ntoa |

---

## 1 问题描述

### 1.1 缺陷描述

模块同时做 UDP 服务端和客户端，使用 V6 IP 客户端应用连接自身服务端后：
- 客户端向服务端发送数据，服务端上报的 `+UDPRECV(S)` URC 中的源 IP 地址是**截断且错误**的
- 用这个错误地址执行 `AT+UDPSENDS` 向客户端回数据，报 `ERROR`，客户端收不到数据

### 1.2 实际现象（AT 日志证据）

```
模块实际 IPv6 地址: 240E:874:14C:9467:1:0:9046:4F24

AT+UDPLISTEN=10086                    # 开启服务端监听
+UDPLISTEN: 0,SUCCESS
+UDPLISTEN: 1,SUCCESS

AT+UDPSETUP=2,240E:874:14C:9467:1:0:9046:4F24,10086   # V6客户端连接服务端
+UDPSETUP: 2,OK

AT+UDPSEND=2,10                       # 客户端发数据
+UDPSEND: 2,10
+UDPRECV(S): 1,1:0:9046:4F24::,9072,10,1234567890    # ← 服务端收到的源IP错误！

AT+UDPSENDS=1,1:0:9046:4F24::,9072,10,10   # 用错误地址回发 → ERROR
+UDPSENDS: ERROR
```

### 1.3 预期行为

- `+UDPRECV(S)` 上报的源 IP 应为完整 IPv6 地址 `240E:874:14C:9467:1:0:9046:4F24`
- `AT+UDPSENDS` 使用正确源 IP 应能成功发送数据，客户端应收到

---

## 2 根因分析

### 2.1 根因定位

**根因位置：** `pcac/NWY_FRAMEWORK/atcmd/nwy_at_proc/src/nwy_app_at_func_tcp.c:3695`

```c
// BUG 代码 (第 3688-3695 行)
if(addr_in->ps_sin_family == NWY_DSS_AF_INET6)
{
    struct nwy_ps_sockaddr_in6 *addr_v6;
    addr_v6 = (struct nwy_ps_sockaddr_in6*)&recv_ptr->addr;
    ip_type = NWY_APP_IP_V6;
    ip_port = addr_v6->ps_sin6_port;
    memset(ip_address, 0, sizeof(ip_address));
    snprintf(ip_address, sizeof(ip_address), "%s",
        nwy_app_ip6addr_ntoa((void *)&addr_v6->ps_sin6_addr));  // ← 类型不匹配
}
```

**问题：** `nwy_app_ip6addr_ntoa()` 函数签名要求参数类型为 `const nwy_ip_addr_type *addr`，但此处传入的是 `&addr_v6->ps_sin6_addr`，其实际类型为 `struct nwy_ps_in6_addr *`。通过 `void*` 强转绕过了编译器类型检查。

### 2.2 内存布局不匹配导致地址截断

**`nwy_ip_addr_type` 的定义**（`nwy_platform_def.h:235-244`）：

```c
typedef struct
{
    int type;                       // 4 字节: 地址类型标识
    union
    {
        uint32 v4;                  // IPv4: 4 字节
        uint64 v6[2];               // IPv6: 16 字节
    } addr;
} nwy_ip_addr_type;
// 总大小: 4 (type) + 16 (addr.v6) = 20 字节
```

**`struct nwy_ps_in6_addr` 的定义**（`nwy_platform.h:61-84`）：

```c
struct nwy_ps_in6_addr
{
    union
    {
        uint8   u6_addr8[16];
        uint16  u6_addr16[8];
        uint32  u6_addr32[4];
        uint64  u6_addr64[2];
    } in6_u;
};
// 总大小: 16 字节（无 type 前缀字段）
```

**`nwy_app_ip6addr_ntoa` 函数实现**（`nwy_platform.c:1762-1767`）：

```c
char *nwy_app_ip6addr_ntoa(const nwy_ip_addr_type *addr)
{
    ip6_addr_t temp_addr = {0};
    memcpy(temp_addr.addr, addr->addr.v6, sizeof(temp_addr.addr));  // ← 关键
    return ip6addr_ntoa(&temp_addr);
}
```

**错误读取过程：**

传入的指针 `&addr_v6->ps_sin6_addr` 指向的是 `struct nwy_ps_in6_addr`（16字节，直接就是 IPv6 地址数据）。但函数把它当成了 `nwy_ip_addr_type*` 来解析：

1. 按 `nwy_ip_addr_type` 布局，`addr->addr.v6` 的偏移是 `+4` 字节（跳过 `type` 字段）
2. 但实际数据从偏移 `0` 开始就是 IPv6 地址
3. `memcpy` 从 `addr + 4` 开始读 16 字节：
   - 实际 IPv6 地址：`[240E:0874:014C:9467] [0001:0000:9046:4F24]`（共32字节 hex = 16字节）
   - 偏移 +4 后读到的是：第4~19字节，即 `9467` 的后半段 `67 00` 开始... 实际表现为只拿到地址的后半部分

**数据丢失示意（16字节 IPv6 地址，按字节排列）：**

```
字节偏移:  0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15
实际地址: 24 0E 08 74 01 4C 94 67 00 01 00 00 90 46 4F 24
                                       ↑
                          函数从 offset+4 开始读取 (addr->addr.v6)
                          读到的16字节: 01 4C 94 67 00 01 00 00 90 46 4F 24 (+ 后面3字节未知)
```

实际从 offset=4 读 16 字节，但 `ps_sin6_addr` 只有 16 字节，读到 offset 19 会越界 4 字节。最终 `ip6addr_ntoa` 解析出的就是 `1:0:9046:4F24::` 这样的错误地址——正好对应日志中观察到的 `1:0:9046:4F24::`。

### 2.3 对比正确的实现（TCP accept 路径）

同文件中 TCP accept 的处理就是**正确**的（第 548-554 行、第 767-772 行）：

```c
// 正确代码 (TCP accept, 第 548-554 行)
if(addr_in->ps_sin_family == NWY_APP_IP_V6)
{
    struct nwy_ps_sockaddr_in6 *addr_v6;
    addr_v6 = (struct nwy_ps_sockaddr_in6*)&acpt_msg->addr;
    nwy_ip_addr_type addr = {0};                                    // ← 构造正确的类型
    memcpy(&addr.addr.v6, &addr_v6->ps_sin6_addr,                   // ← 先拷贝到 nwy_ip_addr_type
           sizeof(struct nwy_ps_in6_addr));
    nwy_app_at_unsol_str(at_channel, "...",
                         sid,
                         nwy_app_ip6addr_ntoa((void *)&addr),       // ← 传正确类型的指针
                         ...);
}
```

UDP 服务端路径遗漏了这一关键步骤，直接把 `ps_sin6_addr` 指针强转传入。

### 2.4 影响范围

`AT+UDPSENDS` 返回 ERROR 的原因：用户根据 URC 上报的错误地址 `1:0:9046:4F24::` 作为目标地址发送，这是一个无效的 IPv6 地址（无法路由），socket 层 sendto 失败，返回 ERROR。

---

## 3 调用链

```
UDP 数据包到达
  ↓
lwip 协议栈 recvfrom 填充 sockaddr_in6 (源地址正确)
  ↓
nwy_app_at_udp_server_cb()                      [nwy_app_at_func_tcp.c:3589]
  case NWY_SOCKET_DATA_RECVED:                   [line 3675]
    addr_in = &recv_ptr->addr                    [line 3686]
    if (addr_in->ps_sin_family == INET6)         [line 3688]
      addr_v6 = &recv_ptr->addr                  [line 3691]
      nwy_app_ip6addr_ntoa(&addr_v6->ps_sin6_addr)  [line 3695]  ← 根因：类型不匹配
        ↓
        memcpy(temp_addr.addr, addr->addr.v6, 16)  [nwy_platform.c:1765]
        ↑ 按 nwy_ip_addr_type 布局从 offset+4 读取，地址前半段丢失
        ↓
      ip_address = "1:0:9046:4F24::"              ← 错误的截断地址
    URC: "+UDPRECV(S): 1,1:0:9046:4F24::,9072,..."  [line 3710]
  ↓
用户根据 URC 执行 AT+UDPSENDS=1,1:0:9046:4F24::,...
  ↓
sendto 失败 → +UDPSENDS: ERROR
```

---

## 4 代码交叉验证

### 4.1 对比同文件中所有 `nwy_app_ip6addr_ntoa` 调用

| 位置 | 文件:行 | 传参方式 | 正确性 |
|------|---------|----------|--------|
| TCP accept | nwy_app_at_func_tcp.c:554 | `nwy_ip_addr_type addr; memcpy(&addr.addr.v6,...); nwy_app_ip6addr_ntoa(&addr)` | ✅ 正确 |
| TCP accept | nwy_app_at_func_tcp.c:565 | 同上 | ✅ 正确 |
| TCP accept | nwy_app_at_func_tcp.c:771 | 同上 | ✅ 正确 |
| **UDP server recv** | **nwy_app_at_func_tcp.c:3695** | **`nwy_app_ip6addr_ntoa((void *)&addr_v6->ps_sin6_addr)`** | **❌ 错误** |

### 4.2 其他文件的同类调用（均存在相同缺陷）

| 文件:行 | 传参方式 | 状态 |
|---------|----------|------|
| nwy_app_at_func_tcp_ns.c:1830 | `nwy_app_ip6addr_ntoa((void *)&addr_v6->ps_sin6_addr)` | ❌ 相同缺陷（但被 `#if 0` 注释禁用） |
| nwy_app_at_func_tcp_ns.c:1835 | 同上 | ❌ 相同缺陷（`#if 0` 禁用） |
| udp_server_test.c:158 | `strcpy(ip_address, nwy_app_ip6addr_ntoa((void *)&addr_v6->ps_sin6_addr))` | ❌ 相同缺陷（测试代码） |
| nwy_app_at_func_tcp_ns.c:921/1121/1273/1869/2027/2187 | `nwy_app_ip6addr_ntoa((void *)&addr)` 其中 addr 为 `nwy_ip_addr_type` | ✅ 正确 |
| nwy_app_at_func_mytcp.c:316/800 | `nwy_app_ip6addr_ntoa((void *)&addr_v6->ps_sin6_addr)` | ❌ 相同缺陷 |

> **结论：** 该缺陷是系统性问题，多处直接传 `ps_sin6_addr` 指针给 `nwy_app_ip6addr_ntoa`。当前暴露的 bug 路径是 `nwy_app_at_func_tcp.c:3695`。

---

## 5 问题复现路径

### 前置条件
- ASR1603 模块已注网，获取到 IPv6 地址（`AT+XIIC?` 可查到 V6 地址）
- 模块同时作为 UDP 服务端和 V6 客户端

### 复现步骤
1. `AT+XIIC=1` 激活 PDP，查询确认获得 IPv6 地址
2. `AT+UDPLISTEN=10086` 开启 UDP 服务端监听（同时创建 V4 和 V6 socket）
3. `AT+UDPSETUP=2,<模块自身IPv6地址>,10086` 创建 V6 客户端连接自身
4. `AT+UDPSEND=2,10` 客户端发送 10 字节数据
5. 观察 `+UDPRECV(S)` URC 中的源 IP 地址

### 验证方法
- **缺陷现象：** URC 源 IP 为 `1:0:9046:4F24::`（截断错误）
- **修复后预期：** URC 源 IP 应为 `240E:874:14C:9467:1:0:9046:4F24`（完整正确）
- 进一步：`AT+UDPSENDS=1,<正确IPv6>,9072,<len>` 应返回 OK，客户端应收到数据

### 复现概率
100%（只要 V6 客户端向 V6 服务端 socket 发送数据即触发）

---

## 6 修复建议

### 6.1 修复方案（参照 TCP accept 的正确写法）

**修改文件：** `pcac/NWY_FRAMEWORK/atcmd/nwy_at_proc/src/nwy_app_at_func_tcp.c`

**修改位置：** 第 3688-3696 行

**修改前：**
```c
if(addr_in->ps_sin_family == NWY_DSS_AF_INET6)
{
    struct nwy_ps_sockaddr_in6 *addr_v6;
    addr_v6 = (struct nwy_ps_sockaddr_in6*)&recv_ptr->addr;
    ip_type = NWY_APP_IP_V6;
    ip_port = addr_v6->ps_sin6_port;
    memset(ip_address, 0, sizeof(ip_address));
    snprintf(ip_address, sizeof(ip_address), "%s", nwy_app_ip6addr_ntoa((void *)&addr_v6->ps_sin6_addr));
}
```

**修改后：**
```c
if(addr_in->ps_sin_family == NWY_DSS_AF_INET6)
{
    struct nwy_ps_sockaddr_in6 *addr_v6;
    addr_v6 = (struct nwy_ps_sockaddr_in6*)&recv_ptr->addr;
    ip_type = NWY_APP_IP_V6;
    ip_port = addr_v6->ps_sin6_port;
    memset(ip_address, 0, sizeof(ip_address));
    nwy_ip_addr_type nwy_addr = {0};
    memcpy(&nwy_addr.addr.v6, &addr_v6->ps_sin6_addr, sizeof(struct nwy_ps_in6_addr));
    snprintf(ip_address, sizeof(ip_address), "%s", nwy_app_ip6addr_ntoa(&nwy_addr));
}
```

### 6.2 其他受影响位置的修复

同样缺陷应一并修复的位置（当前未启用/测试代码，建议同步修复避免后续启用时踩坑）：
- `nwy_app_at_func_tcp_ns.c:1830, 1835`（`#if 0` 段内）
- `nwy_app_at_func_mytcp.c:316, 800`
- `udp_server_test.c:158`（测试代码）

### 6.3 长期改进建议

考虑修改 `nwy_app_ip6addr_ntoa` 的函数签名，使其直接接受 `const struct nwy_ps_in6_addr*` 或 `const uint8_t[16]`，从根本上消除类型混淆。当前 `void*` 强转的使用模式使得编译器无法在编译期发现类型错误。

---

## 7 关键证据

| 编号 | 证据 | 来源 |
|------|------|------|
| E1 | AT 日志：URC 源 IP 为 `1:0:9046:4F24::`（截断） | atlog.log:45,54 |
| E2 | AT 日志：模块实际 IPv6 为 `240E:874:14C:9467:1:0:9046:4F24` | atlog.log:23 |
| E3 | AT 日志：UDPSENDS 用错误地址报 ERROR | atlog.log:57 |
| E4 | BUG 代码行：直接强转 ps_sin6_addr 指针 | nwy_app_at_func_tcp.c:3695 |
| E5 | 函数实现：按 addr->addr.v6 偏移读取 | nwy_platform.c:1762-1767 |
| E6 | 类型定义：nwy_ip_addr_type 含 type 前缀字段 | nwy_platform_def.h:235-244 |
| E7 | 类型定义：nwy_ps_in6_addr 无前缀，16字节直接数据 | nwy_platform.h:61-84 |
| E8 | 正确写法对比：TCP accept 先 memcpy 到 nwy_ip_addr_type | nwy_app_at_func_tcp.c:548-554 |