## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **工作项 ID** | 7017934398 |
| **平台** | ASR1603 |
| **模块** | FTP 客户端（pcac/duster/src/ftp_client.c + pcac/NWY_FRAMEWORK AT 层） |
| **问题分类** | 协议异常 + 状态机异常（非空闲超时/非 keepalive 缺失） |
| **症状关键词** | Server Control Link Disconnect, Error Not Login, PASV/STOR, FTP 压测概率性断开, 服务器 RST |
| **根因概述** | 服务器（FileZilla Server 0.9.60 beta）在一次 PASV 后异常连发两条 227（端口 65448→1052），FTP 控制链路协议状态机失步；模块端 STOR 后等待 150 的 while 循环对“非 150/125/非 -1”的应答无界循环、无重试上限，被动消耗服务器持续回吐的 227 约 21 秒，最终服务器主动 RST 控制链路；模块 5 秒定时器 reactive 检测到 socket 关闭 → 上报 "+FTP: Server Control Link Disconnect"，后续 AT+FTPPUT 返回 "Error Not Login"。 |
| **调用链摘要** | AT+FTPPUT → nwy_app_ftp_put_file → send_ftpcmd("STOR") → get_ftpreply while 无界循环（消费垃圾 227）→ 服务器 RST 控制链路 → readline 返回 -1 → gftp_timer(5s) select() → ftp_close() → MI_UCR_FTP_CLOSE_IND → nwy_ftp_rsp_cb(NWY_SOCKET_CLOSED) → "+FTP: Server Control Link Disconnect" → 下一条 AT+FTPPUT → nwy_app_ftp_status()=NOT_EXIST → "Error Not Login" |
| **检索关键词** | FTP 控制链路断开, Server Control Link Disconnect, PASV 227 重复, STOR 150 等待, FileZilla RST, Error Not Login, ftp_tout_cmd_parse, get_ftpreply while |

---

# FTP 压测概率性“服务器控制链路断开”原因分析

## 目录
- [0. 结构化摘要](#0-结构化摘要)
- [1. 问题描述](#1-问题描述)
- [2. 根本原因](#2-根本原因)
- [3. 相关文件](#3-相关文件)
- [4. 修复建议](#4-修复建议)

---

## 1. 问题描述

FTP 上传/下载压测（AT+FTPPUT 连续 ~492 次）概率性出现 **5/492 ≈ 1%** 的“服务器控制链路断开”，导致当次及后续文件传输失败。用户提供的 AT 口日志片段：

```
[12:59:16.997] AT+FTPLOGIN=123.139.59.166,21,admin,admin
[12:59:17.120] +FTPLOGIN: User logged in
[12:59:17.179] AT+FTPPUT=TC_P_BZ_FTP_001_at.txt,1,1,2000
[12:59:17.945] +FTPPUT: OK,2000                    ← 第 1 个文件成功
[12:59:18.003] AT+FTPPUT=TC_P_BZ_FTP_001_letters.txt,1,1,2000
[12:59:18.308] >                                   ← 进入 bypass 等待主机送数据
[12:59:40.016] +FTP: Server Control Link Disconnect ← 约 22 秒后控制链路断开
[13:00:18.526] AT+FTPPUT=TC_P_BZ_FTP_001_digit.txt,1,1,2000
              +FTPPUT: Error Not Login             ← 会话已失效
```

**关键现象**：
- 每次复现都是“会话内第 1 个文件 PUT 成功、第 2 个文件 PUT 卡死后断开”；
- 断开前约有 **21~22 秒** 的静默/卡死窗口；
- 断开后无自动重连，后续命令一律 `Error Not Login`，必须重新 `AT+FTPLOGIN`。

**问题类型**：单日志分析（AT 口日志 + 抓包日志 + 代码交叉验证）。

## 2. 根本原因

> **一句话**：服务器在一次 PASV 后异常连发两条内容矛盾的 227（先是正常端口 65448，紧接着又给一个低端口号 1052），导致 FTP 控制链路协议状态机失步；模块端“STOR 后等 150”的等待循环（`ftp_client.c:1514-1523`）对任何非 150/125 的应答既不校验语义也无重试上限，被动循环消费服务器持续回吐的 227 约 21 秒，最终服务器主动发 TCP RST 切断控制链路。**这不是控制链路空闲超时，也不是 keepalive 缺失**（全流程无任何 421/426 应用层超时报文，链路在持续收发 STOR/227 时被 RST）。

### 2.1 关键日志证据

#### 抓包日志（决定性证据）

来源：`logs/ftp控制端异常断开.txt`（ETHER 抓包，12 244 个包，跨 04:47~05:05 约 18 分钟）。服务器为 **FileZilla Server 0.9.60 beta**（`220-FileZilla Server 中文版 0.9.60 beta`）。

**正常文件 `at.txt`（同一会话第 1 个文件，对比基准）：**

```
04:59:17,820  M->S  PASV
04:59:17,820  S->M  227 Entering Passive Mode (123,139,59,166,200,190)   ← 端口 51390
04:59:17,851  M->S  TCP SYN  → 51390                                      ← 模块连数据端口
04:59:17,851  S->M  SYN-ACK / M->S ACK                                    ← 数据连接建立
04:59:17,881  M->S  STOR TC_P_BZ_FTP_001_at.txt
04:59:17,881  S->M  150 Opening data channel ...                          ← 正常 150
04:59:17,911  M->S  (发送文件数据 + FIN)
04:59:17,942  S->M  226 Successfully transferred                          ← 第 1 个文件成功
```

**异常文件 `letters.txt`（同一会话第 2 个文件，故障）：**

```
04:59:18,614  M->S  PASV                                                   ← 只发了 1 次 PASV
04:59:18,946  S->M  227 Entering Passive Mode (123,139,59,166,255,168)    ← 端口 65448（正常）
04:59:18,946  M->S  TCP SYN  → 65448                                       ← 模块连 65448
04:59:18,976  S->M  227 Entering Passive Mode (123,139,59,166,4,28)       ← 异常：服务器又发一条 227，端口 1052！
04:59:18,976  S->M  SYN-ACK on 65448 / M->S ACK                            ← 数据连接在 65448 上确实建立了
04:59:18,976  M->S  STOR TC_P_BZ_FTP_001_letters.txt                       ← 模块发 STOR
-------------  服务器再未回 150，也再未 ACK 这条 STOR -----------------
04:59:19,520  M->S  STOR ...  （TCP 重传，间隔 ~0.5s）
04:59:19,551  S->M  227 (...4,29)  端口 1053
04:59:20,738  S->M  227 (...4,30)  端口 1054
04:59:21,531  M->S  STOR ...  （间隔 ~2s）
04:59:21,988  S->M  227 (...4,31)  端口 1055
04:59:23,178  S->M  227 (...4,32)  端口 1056
04:59:23,515  M->S  STOR ...  （间隔 ~2s）
04:59:25,583  S->M  227 (...4,33)  端口 1057
04:59:27,505  M->S  STOR ...  （间隔 ~4s）
04:59:30,367  S->M  227 (...4,34)  端口 1058
04:59:31,518  M->S  STOR ...
04:59:39,528  M->S  STOR ...  （间隔 ~8s，指数退避）
04:59:39,982  S->M  TCP RST+ACK  on :21                                    ← 服务器主动 RST 控制链路！
04:59:39,982  S->M  FIN on 65448（数据连接同时被拆）
04:59:40,013  M->S  TCP RST+ACK  on :21
[此后约 6 分钟无任何控制链路流量，直到下一次重新 FTPLOGIN]
```

**抓包结论（硬证据）**：
1. 针对 `letters.txt` 这一次传输，模块**只发了 1 次 PASV**（`04:59:18,614`），服务器却回了**两条端口不同的 227**（65448、1052）——第二条为**服务器单方面异常发送**（无对应的第二个 PASV，也不可能是 TCP 重传，因为两条字节内容不同，字节级证据见下文 2.1.1）。
2. 数据连接在 65448 上**实际已建立**（SYN/SYN-ACK/ACK 完整），但服务器**始终未回 150**，且**不再 ACK 模块的 STOR**。
3. 此后双方进入失步震荡：模块 TCP 因 STOR 未被 ACK 而按指数退避重传 STOR（0.5s→2s→2s→4s→4s→8s，共 7 次，典型 TCP RTO 行为）；服务器持续回吐端口递增的低端口号 227（1053→1058）。
4. 持续约 **21 秒**后，**服务器主动发 TCP RST** 切断控制链路（`04:59:39,982`）。
5. **全程无 421 / 426**——排除“服务器应用层空闲超时/keepalive 超时”假设。控制链路是在“持续有 STOR/227 流量”的情况下被服务器 RST 的，不是因空闲而死。

#### 2.1.1 原始报文字节级佐证（raw hex）

> 以下数据**直接从 `logs/ftp控制端异常断开.txt`（ETHER 抓包）逐包解析得出**，非解码转述，可逐字节复核。每条 227/RST 均给出原始 hex，端口字段与 TCP flags 字段均标注字节位置。

**(a) 故障窗口内服务器→模组的全部 9 条 227（证明“连发 + 端口递增” Cascade）**

| # | 时间戳 | 方向 | 服务器被动端口 | 备注 |
|---|---|---|---|---|
| 1 | 04:59:17,820 | S→M | 51390 | `at.txt` 的 227（前有对应 PASV，正常） |
| 2 | **04:59:18,946** | S→M | **65448** | `letters.txt` 的**第 1 条** 227 |
| 3 | **04:59:18,976** | S→M | **1052** | **第 2 条**，仅晚 30ms，**无对应 PASV** ← 失步起点 |
| 4 | 04:59:19,551 | S→M | 1053 | 服务器持续回吐 |
| 5 | 04:59:20,738 | S→M | 1054 | 端口一路递增 |
| 6 | 04:59:21,988 | S→M | 1055 |  |
| 7 | 04:59:23,178 | S→M | 1056 |  |
| 8 | 04:59:25,583 | S→M | 1057 |  |
| 9 | 04:59:30,367 | S→M | 1058 | 之后 ~9.6s 服务器发 RST |

**(b) 关键两条 227 的原始 hex ↔ ASCII（证明不是 TCP 重传）**

第 [2] 条（端口 65448）——端口字段字节 `…2C 32 35 35 2C 31 36 38 29` = `,255,168)`：
```
32|32|37|20|45|6E|74|65|72|69|6E|67|20|50|61|73|73|69|76|65|20|4D|6F|64|65|20|28|31|32|33|2C|31|33|39|2C|35|39|2C|31|36|36|2C|32|35|35|2C|31|36|38|29|0D|0A
"227 Entering Passive Mode (123,139,59,166,255,168)\r\n"   端口 = 255×256+168 = 65448
```

第 [3] 条（端口 1052）——端口字段字节 `…2C 34 2C 32 38 29` = `,4,28)`：
```
32|32|37|20|45|6E|74|65|72|69|6E|67|20|50|61|73|73|69|76|65|20|4D|6F|64|65|20|28|31|32|33|2C|31|33|39|2C|35|39|2C|31|36|36|2C|34|2C|32|38|29|0D|0A
"227 Entering Passive Mode (123,139,59,166,4,28)\r\n"   端口 = 4×256+28 = 1052
```

两条均以 `32 32 37 20`（"227 "）开头、`0D 0A`（CRLF）结尾，是完整的应用层 FTP 应答行；但**端口字段字节不同**（`,255,168` vs `,4,28`）→ 必然是服务器在应用层发了两次内容不同的 227，**不可能是同一条报文的 TCP 层重传**（重传须逐字节相同）。

**(c) PASV / STOR 计数（证明“1 次 PASV → 2 条 227”与“STOR 未被 ACK 而重传”）**

窗口 `04:59:17 ~ 04:59:40` 内，逐包统计模组→服务器方向的 FTP 命令：
- **PASV = 2 次**：`04:59:17,820`（`at.txt`）、`04:59:18,614`（`letters.txt`）。即针对 `letters.txt` 这次传输，**PASV 仅 1 次**，却收到端口不同的 227 共 8 条（上表 [2]~[9]）。
- **STOR = 8 段**：`04:59:17,881`（`at.txt`，已正常完成 226）；其余 7 段均承载 `letters.txt` 的 STOR——`04:59:18,976` 为应用层原始发送，`04:59:19,520 / 21,531 / 23,515 / 27,505 / 31,518 / 39,528` 为 **TCP 层重传**（间隔 0.5s/2s/2s/4s/4s/8s 指数退避），系因服务器从未 ACK 该 STOR。

**(d) RST 报文原始字节（证明“服务器主动断开”）**

```
04:59:39,982  S→M  sp=21 → dp=61042   TCP flags = 0x14 (RST=0x04 | ACK=0x10 → RST+ACK)
  TCP头20字节: 00|15|EE|72|B9|0E|48|A1|80|8F|56|3D|50|14|00|00|7F|18|00|00
                                ^^ flags(tcp[13])=0x14
04:59:40,013  M→S  sp=61042 → dp=21   TCP flags = 0x14 (RST+ACK)
  TCP头20字节: EE|72|00|15|80|8F|56|5F|B9|0E|48|AF|50|14|7D|78|01|70|00|00
                                ^^ flags(tcp[13])=0x14
```
服务器→模组的 RST（`04:59:39,982`）**先于**模组→服务器的 RST（`04:59:40,013`）31ms → **由服务器主动发起**断开。`sp=00 15`（21）确认这是控制链路连接。

#### AT 命令日志

与抓包为同一故障的两次复现，相对时序完全吻合（第 1 个文件 STOR 后 ~60ms 成功；第 2 个文件 STOR 后 ~22s 报断开）：

```
AT+FTPPUT=...at.txt       → +FTPPUT: OK,2000            （成功，对应 226）
AT+FTPPUT=...letters.txt  → > ...（22s）...
                          → +FTP: Server Control Link Disconnect   （对应服务器 RST）
AT+FTPPUT=...digit.txt    → +FTPPUT: Error Not Login    （会话已 RST，状态 = FTP_CLOSED）
```

### 2.2 代码调用链

| 信息 | 值 |
|------|-----|
| **AT 入口** | `nwy_app_at_ftpput_func()`（`pcac/NWY_FRAMEWORK/atcmd/nwy_at_proc/src/nwy_app_at_func_ftp.c:1273`） |
| **size 模式 PUT 流程** | 发 `>` 提示符 → `nwy_tcpip_recv_pause()` → `nwy_app_at_enter_bypass(...)` 等待主机送满 size 字节 → `nwy_app_ftp_put_file()` |
| **底层 PUT** | `nwy_app_ftp_put_file()`（`pcac/NWY_FRAMEWORK/components/nwy_net/src/nwy_app_api.cpp:3087`）→ `ftp_client.c` put 流程 → `send_ftpcmd("STOR", ...)` → `get_ftpreply()` 等待 150 |
| **断开 URC 产生** | `ftp_close()` → `MI_UCR_FTP_CLOSE_IND(status=0)` → AT 回调 `nwy_ftp_rsp_cb()` 的 `NWY_SOCKET_CLOSED` 分支 → `+FTP: Server Control Link Disconnect` |
| **后续命令失败** | `nwy_app_at_ftpput_func()` 调 `nwy_app_ftp_status()` 返回 `NWY_APP_ERR_SOCKET_NOT_EXIST`（会话已 `FTP_CLOSED`）→ 输出 `+FTPPUT: Error Not Login` |

**模块侧放大问题的核心代码（`pcac/duster/src/ftp_client.c:1514-1523`）**：

```c
ret_value = get_ftpreply(GFTP_SOCKET, NULL);
while (ret_value != 150 && ret_value != 125)            // ← 只要不是 150/125 就一直循环
{
    if (-1 == ret_value) {                               // ← 仅当 readline 出错/超时才退出
        printf("ftp: send STOR cmd error!"NEWLINE);
        close(sockfd);
        return FTPA_SERVICE_NOT_AVAILABLE;
    }
    ret_value = get_ftpreply(GFTP_SOCKET, NULL);         // ← 继续读下一条应答
}
```

**问题点**：
- 该循环**只认 150/125 为成功、-1 为失败**，对**任何其它应答码（如失步后服务器回吐的 227）既不校验语义、也无最大重试次数/总时长上限**。
- `get_ftpreply()` 每次给控制 socket 设置 `SO_RCVTIMEO = FTP_GET_REPLY_TOUT_DEFAULT*1000 = 25s`（`ftp_client.c:55, 2383`），单次 `readline` 最多阻塞 25s。但只要服务器持续往控制链路塞数据（本例中每 1~8s 一条 227），`readline` 立即返回，循环就一直转——**总等待时长实际由服务器行为决定，不受 25s 约束**。本例中服务器持续喂 227 约 21s 后发 RST，正好在 25s 超时之前。

**控制链路断开检测路径（reactive，非主动）**：
- FTP 控制 socket **没有独立的接收线程、没有 select/poll 事件循环**，只在发命令同步等应答时读取。
- 唯一的异步监视是 `gftp_timer`（每 5s 触发一次，`ftp_client.c:5631/5711`），其 `"cont"` 分支做一次**非阻塞** `select()`（`ftp_client.c:5339-5369`）：仅当 socket **可读**时 `get_ftpreply` 读出应答，若 `ret_code >= 400` 才 `ftp_close()`。
- 该机制**只能“发现已发生的断开”，不能“防止”**；且本例服务器发的是 TCP RST（不是 4xx 应用应答），是由 RST 导致 `readline` 返回 -1 / socket 关闭后被这条路径感知。

**后续命令报错的代码（`pcac/NWY_FRAMEWORK/.../nwy_app_at_func_ftp.c:1300-1304`）**：

```c
ret = nwy_app_ftp_status(nwy_get_pdp_cid(), AtRet);
if (ret == NWY_APP_ERR_SOCKET_NOT_EXIST) {
    return nwy_app_at_func_resp_str(arg->at_channel, "+FTPPUT:"PADDING"Error Not Login");
}
```

`ftp_close()` 已把 `GFTP_SOCKET = -1`、`GFTP_STATUS = FTP_CLOSED`，故 `nwy_app_ftp_status()` 必然返回 `NOT_EXIST`，且代码中**无任何自动重连/重登**逻辑（全仓库 grep `relogin`/`auto_login` 无命中）。

### 2.3 问题分析

**为什么会进入失步？——服务器侧异常（根触发点）**

- 正常 FTP 语义：客户端发 1 次 `PASV` → 服务器回 1 条 `227`（给出 1 个数据端口）→ 客户端连该端口 → 发 `STOR` → 服务器回 `150`。
- 本例服务器在客户端只发 1 次 `PASV` 的情况下，**连发 2 条端口不同的 227**（65448、1052），且第二条端口是**非常规的低端口号 1052**（FileZilla 正常被动端口为高端口，如本会话此前的 51390、65448）。随后服务器端口一路递增 1053→1058，这是服务器“被动端口分配器”在被反复触发却未被正常消费的典型表现。
- 触发该服务器异常的诱因**无法从模块侧单方面确定**：服务器版本是 **FileZilla Server 0.9.60 beta**（2007~2009 年的古老 beta 版本），其被动端口管理/状态机在长压测下偶发错乱的可能性较高；亦不排除中间网络设备（NAT/防火墙）对高端口数据连接的偶发干扰。但**模块侧抓包明确证明：模块没有多发 PASV、没有发畸形命令**，第二条 227 是服务器单方面发出的。→ **根触发在服务器侧。**

**为什么一次服务器抖动会变成硬故障？——模块侧放大（可改进点）**

- 模块 `STOR` 后的 `while (ret_value != 150 && ret_value != 125)` 循环**不识别协议违例**：STOR 之后合法应答只应是 1xx（150/125）/4xx/5xx，**收到 227 本身就是“协议状态机已错乱”的铁证**，但代码把它当成“再读下一条”继续循环，于是一直被动消费服务器回吐的 227，整整卡了 ~21 秒。
- 期间模块既不主动中止本次传输、也不向上层报错触发重试/重登，只能等服务器 RST（或 25s readline 超时）才退出，**把一次瞬时服务器抖动放大成 21s 卡死 + 控制链路彻底断开**。
- 断开后**无自动重登**，下一个 `AT+FTPPUT` 直接 `Error Not Login`，必须上位机重新 `AT+FTPLOGIN`，压测用例即判失败。

**时序复核（抓包 04:59:xx 与 AT 口 12:59:xx 为不同设备时钟，相对时序一致）**：
- T0 `STOR` 发出 → 服务器本应 ~300ms 内回 150（参考第 1 个文件 04:59:17,881 STOR → 04:59:17,881 150，即 ~0ms）；
- 异常时 T0 之后**始终没有 150**，模块 TCP 在 ~0.5s 后开始重传 STOR（RTO 指数退避）；
- T0+21s 服务器发 RST；模块 5s 定时器在下一周期感知 socket 关闭 → 上报 URC（AT 口体现为 STOR 后 ~22s 的 `+FTP: Server Control Link Disconnect`）。时序完全自洽。

### 2.4 问题复现路径

| 项目 | 内容 |
|------|------|
| **前置条件** | 1. ASR1603 模组已注网、`AT+XIIC` 已拿到 IP；<br>2. 测试服务器为 FTP `123.139.59.166:21`（实测为 FileZilla Server 0.9.60 beta，账号 admin/admin）；<br>3. `AT+FTPLOGIN` 成功，控制链路保持长连接、跨多次 PUT 复用。 |
| **必要状态** | 会话已登录（`FTP_LOGGED`），上一个文件 PUT 已完成（`226`），正在发起下一个 `AT+FTPPUT`（PASV 模式）。 |
| **操作步骤** | 1. 压测脚本循环执行 `AT+FTPPUT=<file>,1,1,2000` 上传小文件（每个文件 size 模式，~2000 字节）；<br>2. 持续 ~492 次量级，期间观察 AT 口 `+FTP: Server Control Link Disconnect` 与 `+FTPPUT: Error Not Login`。 |
| **复现概率** | **约 1%（5/492）**，概率性，依赖服务器侧偶发状态机错乱，连续压测数十~数百次可复现一次。 |
| **验证方法** | 1. AT 口出现 `+FTP: Server Control Link Disconnect` 后紧跟 `+FTPPUT: Error Not Login`；<br>2. 同期抓包（PC 旁路 / 模组侧 mirror）可见：服务器在单次 PASV 后连发两条端口不同的 `227`，随后约 21s 发 TCP RST 切断 :21 控制链路；全程无 `421`/`426`。 |

> **关于根触发的诚实结论**：第二条 227 由服务器单方面发出，模块抓包侧无任何触发证据，因此“是否能 100% 在模组侧复现”取决于服务器侧能否再现该异常。建议同时在【稳定版 FTP 服务器（vsftpd / FileZilla Server 1.x 正式版）】上回归，以判定该异常是否为 0.9.60 beta 环境特有。

## 3. 相关文件

- `pcac/duster/src/ftp_client.c`
  - `:37` `FTP_SUPPORT_KICK_OFF = 1`
  - `:55` `FTP_GET_REPLY_TOUT_DEFAULT = 25`（单次 readline 超时 25s）
  - `:1514-1523` **STOR 后等待 150 的无界 while 循环（模块侧放大点）**
  - `:2359-2413` `get_ftpreply()`（`SO_RCVTIMEO=25s` + `readline`）
  - `:5317-5370` `ftp_tout_cmd_parse()` 的 `"cont"` 分支（5s 定时器非阻塞 select，reactive 检测断开）
  - `:5631 / :5711` `OSATimerStart(gftp_timer, 2, 1000, ...)`（5s 周期）
  - `:3587-3617` `ftp_close()`（置 `FTP_CLOSED` + 发 `MI_UCR_FTP_CLOSE_IND`）
- `pcac/NWY_FRAMEWORK/atcmd/nwy_at_proc/src/nwy_app_at_func_ftp.c`
  - `:419-426` `+FTP: Server Control Link Disconnect` / `Server Data Link Disconnect` URC 产生
  - `:1273` `nwy_app_at_ftpput_func()`（AT+FTPPUT 入口）
  - `:1300-1304` `Error Not Login` 判定门
  - `:1351-1380` size 模式 `>` 提示符 + `nwy_tcpip_recv_pause()` + enter bypass
- `pcac/NWY_FRAMEWORK/components/nwy_net/src/nwy_app_api.cpp:3087` `nwy_app_ftp_put_file()`
- `pcac/NWY_FRAMEWORK/atcmd/nwy_at_proc/src/nwy_app_at_func_tcp.c:125-144` `nwy_tcpip_recv_pause()`（仅暂停 TCP/UDP/SSL client socket，与 FTP 控制 socket 无关）
- 抓包日志：`logs/ftp控制端异常断开.txt`
- AT 口日志：原始问题报告内联片段（`.spec/logs/控制端异常断开/`）

## 4. 结论与建议

### 4.1 结论：根因为服务器侧，模组侧不构成缺陷、不强制修改

抓包逐字节对比证明：同一会话内**成功文件 `at.txt` 与失败文件 `letters.txt` 模组侧行为完全一致**（各发 1 次 PASV、收到 227 后立即连数据端口并发 STOR），唯一差异在服务器——失败那次服务器**单方面多发 1 条端口矛盾的 227（1052）、其后不回 150、不 ACK STOR、持续回吐低端口号 227（1053→1058）、21s 后主动 RST**。模组未多发 PASV、未发畸形命令，协议行为符合规范。**本 bug 根因为服务器侧（FileZilla Server 0.9.60 beta 偶发协议状态机错乱），模组侧无需修改。**

### 4.2 主建议：测试环境换稳定版 FTP 服务器回归（坐实服务器侧责任）

将同一压测用例在**稳定版 FTP 服务器**（vsftpd 或 FileZilla Server 1.x 正式版）上回归：
- 若不再复现 → 证实为 0.9.60 beta 服务器侧缺陷，本 bug 关闭，交付结论明确“测试服务器版本过旧偶发异常”；
- 若仍复现 → 才需要回头排查模组侧（PASV 数据连接时序 / 数据 socket 泄漏），可结合模组 AP 日志（284MB `Log 22-6月-26...txt`）定位 `ftp_tout_cmd_parse` / 数据 socket 分配路径。

### 4.3 可选：模组侧鲁棒性增强（非必需，视现网而定）

> 以下为**韧性增强**，不是本 bug 的修复；仅当现网可能遇到不稳定服务器 / NAT 超时等抖动时才值得做。若现网使用稳定版服务器，可不做。

- **给 STOR 等待循环加上界 + 协议违例识别**（`ftp_client.c:1514-1523`）：加最大读取次数/总时长（如 ≤3 次或 ≤5s），并校验应答码（STOR 后收到 `227` 即判协议失步、主动 `ftp_close()` 报错），把“21s 卡死 + 硬断开”降为“快速失败”，便于上层重试/重登。
- **控制链路异常断开后自动重登**（可选，需评估 AT 语义，建议加开关宏如 `NWY_FTP_AUTO_RELOGIN`）：避免下一次 `AT+FTPPUT` 直接 `Error Not Login`。

### 4.4 关于 NOOP keepalive（澄清，非本 bug 修复项）

代码确实缺少控制链路 NOOP keepalive（`ftp_tout_cmd_parse` 的 `"cont"` 分支只做非阻塞读、不发 NOOP），但本案例**不是**空闲超时（全程无 421/426，链路在持续收发 STOR/227 时被服务器 RST）。故 NOOP 改动**对本案例无效，不应作为本 bug 的修复依据**；仅当其它场景确有空闲超时才另行评估。