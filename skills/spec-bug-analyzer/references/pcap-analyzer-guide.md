# pcap 报文分析脚本使用指南

脚本路径：`scripts/pcap_analyzer.py`（相对于本技能目录，执行时按技能加载给出的目录拼接）。

> 本工具让 AI **直接解析 .pcap 抓包、替代 Wireshark 完成协议解码**，无需现场手写 struct 解析代码。

## 依赖

- **scapy**：`pip install scapy`（提供全协议栈分层解码，是本工具核心依赖）
- 未安装时脚本会明确提示并退出，不会静默降级——降级到纯 stdlib 会丢失完整协议解码能力

## 子命令速查

| 子命令 | 用途 | 关键参数 |
|--------|------|----------|
| `flows` | 列出所有 TCP/UDP 流摘要（端点对/时间/包数/结局 FIN/RST） | `input` `--ip`(过滤IP) `--port`(过滤端口) `--report`(归档) |
| `show` | 展示指定流的逐包完整解码（时间/方向/各层字段/payload摘要） | `input` `--flow-id` 或 `--lport` `--hex`(附raw hex) `--report`(归档) |
| `around` | **核心**：给定时刻，定位异常前后的报文交互（默认 ±5s） | `input` `--time`(HH:MM:SS.mmm) `--window`(秒) `--lport`(锁单流) `--report`(归档) |
| `search` | 跨所有包搜 **明文** payload（如 HTTP 状态码、FTP 命令、DNS 域名） | `input` `-k`(关键字) `--max-results` `--report`(归档) |
| `decode` | 单包深度解码（wireshark 风格分层树） | `input` `--packet`(序号) `--hex` `--report`(归档) |

> **`--report` 留痕**：所有子命令支持 `--report PATH`，输出同时打印到屏幕并归档为 markdown 文件（含运行时间、pcap 路径、子命令、完整输出）。推荐路径 `.spec/bug/{工作项ID}_{问题描述}/analysis/pcap_report.md`，与 dump 分析的 `analysis/` 归档统一。

## 典型工作流

### 1. 连接断开/重置类问题

```bash
# 先看全局：哪些流异常结局（RST/无FIN）
python scripts/pcap_analyzer.py flows capture.pcap

# 选中异常流，看完整交互序列，确认谁先 RST、有无应用层关闭
python scripts/pcap_analyzer.py show capture.pcap --flow-id 5

# 或按端口定位
python scripts/pcap_analyzer.py show capture.pcap --lport 49152
```

**判读**：结局列 `RST(服务端主动断)` = 服务端先发 RST；`FIN(正常关闭)` = 正常挥手；`无握手(片段)` = 抓包不全。

### 2. 概率性失败定位（核心场景）

```bash
# 给定异常时刻，看该时刻前后所有流的报文交互
python scripts/pcap_analyzer.py around capture.pcap --time 10:28:18.300 --window 5

# 锁定单流看异常点上下文
python scripts/pcap_analyzer.py around capture.pcap --time 10:28:18.300 --window 5 --lport 49152
```

### 3. TLS 握手失败

```bash
# show 命令会自动解码 TLS 握手层（ClientHello/ServerHello/Alert）
python scripts/pcap_analyzer.py show capture.pcap --flow-id 3

# 搜明文 Alert（注：TLS 加密内容搜不到，但 Alert 的 level/desc 在记录层）
# 握手层的 ClientHello 含 SNI 域名（明文），show 会自动提取
```

### 4. 明文应用协议分析

```bash
# HTTP 状态码 / FTP 命令响应 / DNS 域名
python scripts/pcap_analyzer.py search capture.pcap -k "421" "530" "GET" "POST"

# DNS 查询（show/around 会自动解码 DNS 问答摘要）
python scripts/pcap_analyzer.py flows capture.pcap --port 53
```

### 5. 单包深度解码（需看完整字节）

```bash
# wireshark 风格分层树 + raw hex
python scripts/pcap_analyzer.py decode capture.pcap --packet 42 --hex
```

### 6. MQTT 连接/发布分析（IoT 常见）

```bash
# MQTT 流自动识别端口 1883，show 解码控制包
python scripts/pcap_analyzer.py show capture.pcap --flow-id 3

# 判读：CONNECT（版本/clientId/keepalive）→ CONNACK（rc=0 连接成功 / rc=5 鉴权失败）
# PUBLISH（topic/payload）→ SUBSCRIBE → PINGREQ/PINGRESP（心跳）
# 连接失败类 bug 重点看 CONNACK 的返回码
```

### 7. CoAP 请求/响应分析（LWM2M 底层协议）

```bash
# CoAP 流自动识别端口 5683，show 解码方法/响应码/options
python scripts/pcap_analyzer.py show capture.pcap --flow-id 5

# 判读：CON GET [Uri-Path=test] → ACK 2.05 Content（成功）
#       CON POST → ACK 4.04 Not Found（资源不存在）
#       CON GET → ACK 5.00 Internal Server Error（服务端异常）
# LWM2M 设备管理走 CoAP，报文层故障先看响应码
```

## 协议解码深度

| 层 | 解码内容 | 说明 |
|----|----------|------|
| 链路层 | Ethernet src/dst/type | scapy 自动分层 |
| 网络层 | IP src/dst/TTL/proto | scapy 自动分层 |
| TCP 传输层 | flags 解码（SYN/ACK/FIN/RST/PSH）、seq/ack、payload 长度、**重传标注** | flags 自动转可读名 |
| TLS 记录层 | ContentType（Handshake/Alert/AppData/CCS）、版本、长度 | 手动解记录层，多记录段自动拆分 |
| TLS 握手层 | ClientHello（**SNI 域名提取**）、ServerHello、Certificate、Alert level/desc | 从 Handshake body 提取 |
| **MQTT**（3.1.1 & 5.0） | CONNECT（版本/clientId/keepalive）、CONNACK（返回码）、PUBLISH（topic/payload）、SUBSCRIBE、PINGREQ/RESP、DISCONNECT | 端口 1883 自动识别，scapy.contrib.mqtt 解码 |
| **CoAP** | 类型（CON/NON/ACK/RST）、方法/响应码（GET/POST/2.05/4.04 等）、token、options（Uri-Path/Content-Format/Observe 等） | 端口 5683/5684 自动识别，scapy.contrib.coap + 手动解 options |
| 明文应用协议 | HTTP 请求行/状态码/关键头、FTP/SMTP 命令响应、DNS 问答 | payload 正则识别 |

## 判据与避坑（来自实战纠错）

### 1. TCP 重传 vs 粘包（关键避坑点）
TCP 重传的包 seq 与原包相同，会被误判为"同段多条记录"。**判别靠 seq 去重**：维护每流 `rcv_nxt`，`seq < rcv_nxt` 即重传。
> 实战教训：用户曾纠正 AI "从报文看这个时间点没有粘包"——AI 把 TCP 重传捎带误判为粘包。解读 `show` 输出时，注意相同 seq 的包是重传，不应当作新数据处理。

### 2. 应用层假设必须在报文字节找证据
- 假设"服务器 421 空闲超时" → 必须 `search -k "421"` 在 payload 找到字面 "421"
- 假设"服务端主动关闭" → 必须 `show` 看到 S>C 方向的 RST 先于客户端
- **找不到字节证据即排除该假设**，不能靠 AT 日志猜

### 3. TCP flags 速查
| flags 值 | 含义 |
|---------|------|
| `FIN,ACK` (0x11) | 正常关闭（四次挥手） |
| `RST,ACK` (0x14) | 强制断开（异常关闭或拒绝） |
| `PSH,ACK` (0x18) | 携带数据的正常包 |
| `SYN` (0x02) | 连接请求 |
| `SYN,ACK` (0x12) | 连接接受 |

### 4. 加密内容的边界（与 Wireshark 相同）
TLS **加密的 ApplicationData 不可解**（无会话密钥，Wireshark 也需要 keylog）。但以下都是**明文**，足够定位大多数 TLS 问题：
- ClientHello（含 SNI 域名、密码套件列表、版本）
- ServerHello（版本、选定套件）
- Alert 的 level/desc
- ChangeCipherSpec
- 记录层 ContentType（区分握手/告警/应用数据）

## 能力边界（诚实声明）

- ✅ 明文协议完整解码（HTTP/FTP/DNS/SMTP）
- ✅ **MQTT 3.1.1 & 5.0**（CONNECT/CONNACK/PUBLISH/SUBSCRIBE 等）
- ✅ **CoAP**（方法/响应码/options，覆盖 LWM2M 底层）
- ✅ TCP 流交互重建 + 重传识别
- ✅ TLS 握手层 + SNI + Alert（覆盖大多数 TLS 问题）
- ⚠️ TLS 加密内容不可解（需 keylog，Wireshark 同样限制）——MQTT over TLS 同理
- ⚠️ 不支持 .pcapng（本轮仅验证原始 .pcap）
- ⚠️ scapy.contrib 对 MQTT 5.0 新增控制包（AUTH）和部分属性的解析可能不完整，核心控制包（CONNECT/CONNACK/PUBLISH）稳定
- ✅ 对 IoT 模组常见报文类 bug（连接断开、握手失败、MQTT 连接、CoAP 响应异常、协议交互错误）覆盖率 > 90%

## 证据留痕规范

报文相关结论写进 Bug 分析报告时，用 `show --hex` 或 `decode --hex` 取出 **raw hex + 解码对照**附入报告，让读者可独立复核，不依赖分析者的转述。
