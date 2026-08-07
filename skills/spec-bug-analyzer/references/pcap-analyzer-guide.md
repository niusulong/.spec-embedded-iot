# pcap 报文分析指南（TShark2MCP / tshark 后端）

> 本技能的 pcap 解析由内嵌的 **TShark2MCP** MCP server 提供（封装 Wireshark 的 `tshark`/`capinfos`）。AI 通过 5 个 MCP 工具直接做协议解码，替代手写 Wireshark 操作或自写 struct 解析。Windows 下 tshark 随子模块自带，**无需另装 Wireshark**。

## 前置：依赖就绪

| 项 | 说明 |
|----|------|
| 装依赖 | `pip install -r requirements.txt`（其中 `-e ./vendor/TShark2MCP` 自动拉 `mcp`+`pydantic`） |
| 子模块 | `git submodule update --init vendor/TShark2MCP`（首次或克隆后必做） |
| Claude Code | 插件根 `.mcp.json` 自动注册 `tshark` MCP server，启用即用，无需手动配置 |
| Codex / OpenCode | 需一次性手动注册（见 `spec-using-agents/references/codex-tools.md` / `opencode-tools.md`） |
| tshark 路径 | 自动发现 `vendor/TShark2MCP/vendor/wireshark/tshark.exe`；非 Windows 或想用系统 Wireshark 时设环境变量 `TSHARK_PATH` |
| 未就绪 | SessionStart 钩子会向 stderr 打印修复指引，不静默失效 |

> Python 需 ≥3.10（TShark2MCP 要求）。MCP server 未就绪时，pcap 分析这条路不通——按钩子指引修复后即可。

## 5 个 MCP 工具速查

| 工具 | 用途 | 关键参数 |
|------|------|----------|
| **`get_pcap_overview`** | 文件元信息 + 协议层次树（`io,phs`），不加载单包 | `pcap_file` |
| **`list_conversations`** | TCP 流 / UDP 会话列表 + 双向包/字节统计 | `pcap_file` `protocol="both"\|"tcp"\|"udp"` `limit=100` |
| **`extract_packets`** | 按协议 + 时间窗过滤（可组合） | `pcap_file` `protocol`(tcp/http/dns/tls/ftp/mqtt…) `time_window` `limit` `output_format="summary"\|"full"` |
| **`extract_stream`** | 按 5-tuple 深挖**单流双向** | `pcap_file` `protocol="tcp"\|"udp"` `endpoint_a/b={address,port}` `time_window` `output_format` |
| **`get_statistics`** | 重传率/吞吐/重复ACK/乱序/HTTP延迟/每连接统计（**旧脚本没有的新能力**） | `pcap_file` `metric="all"\|"latency"\|"throughput"\|"retransmission"\|"tcp"\|"packet_loss"` `time_window` |

**时间窗两种**（`extract_packets` / `extract_stream` / `get_statistics` 通用）：
- `RelativeWindow(start_seconds, end_seconds)` —— 相对首包的秒数。**推荐**，跨时区稳健，契合"断连前 30 秒"这类描述。
- `AbsoluteWindow(start, end)` —— 绝对墙钟时间（ISO 8601）。

> `extract_*` 返回 `truncated=True` 表示还有更多匹配包——收窄过滤或调大 `limit`（上限 2000）。`output_format="full"` 返回每包完整 JSON 字段（看 SNI/CONNACK 码/CoAP options 等细节时用）。

## 典型工作流

### 1. 连接断开 / 重置类问题
```
get_pcap_overview        # 先看全局：包数、时长、协议分布
list_conversations(protocol="tcp")   # 找可疑流：看 forward/reverse 包数是否对称、有无反向数据
extract_stream(endpoint_a=..., endpoint_b=..., protocol="tcp", output_format="full")
                         # 看完整握手序列、谁先 FIN/RST、应用层关闭
```
**判读**：`forward/reverse` 包数严重不对称 = 单向死掉；`extract_stream` full 里看 flags 序列，谁先发 `RST`/`FIN` 即谁主动断。

### 2. 概率性失败定位（核心场景）
知道异常相对时刻 T 秒（从 AT/AP 日志推算相对首包的偏移）：
```
extract_packets(time_window=RelativeWindow(T-5, T+5))           # 窗口内所有流交互
extract_stream(endpoint_a=..., endpoint_b=..., time_window=RelativeWindow(T-10, T+2))  # 锁单流
```
不知确切时刻时，先用 `list_conversations` 按 `duration`（跨度）挑异常会话，再 `extract_stream`。

> **长抓包 + 端口复用（NB-IoT/PSM 周期常见）**：模组跨 PSM 周期重用同一本地端口（如 `59266`），5-tuple 不再唯一标识一个会话——`extract_stream` 会把跨越数小时/数十次重连的同 5-tuple 包混到一起、且双向匹配会失真。此时**用 `extract_packets` + `time_window` 按时刻隔离单个会话**（先 `list_conversations` 看 `relative_start` 定位时刻，再切 ±十几秒窗口）。这是隔离单次失败现场的正解。

### 3. TLS 握手失败
```
extract_packets(protocol="tls", output_format="full")   # 自动解 ClientHello(SNI)/ServerHello/Alert
extract_stream(endpoint_a=..., endpoint_b=..., output_format="full")
```
注：TLS **加密的 ApplicationData 不可解**（无会话密钥，Wireshark 同样需要 keylog），但 ClientHello(含 SNI)、ServerHello、Alert level/desc、ChangeCipherSpec 都是明文，足够定位大多数 TLS 问题。

### 4. 明文应用协议
```
extract_packets(protocol="http")     # HTTP 请求行/状态码/头
extract_packets(protocol="ftp")      # FTP 命令/响应码（421/530/230…）
extract_packets(protocol="dns")      # DNS 问答
```
`output_format="full"` 看完整字段。tshark 解码完整，无需手写正则。

### 5. MQTT / CoAP（IoT 常见）
```
extract_packets(protocol="mqtt", output_format="full")          # CONNECT/CONNACK/PUBLISH/SUBSCRIBE…
extract_stream(endpoint_a=..., endpoint_b=..., protocol="tcp", output_format="full")  # 锁定 1883 流
```
- **MQTT** 判读：CONNECT(版本/clientId/keepalive) → CONNACK 返回码(rc=0 连接成功 / rc=5 鉴权失败) → PUBLISH(topic) → PINGREQ/RESP 心跳。连接失败重点看 CONNACK 的 rc。
- **CoAP**：端口 5683/5684；tshark 解 类型(CON/NON/ACK/RST)、方法/响应码(GET/POST/2.05/4.04/5.00)、options(Uri-Path/Content-Format)。LWM2M 设备管理走 CoAP，报文层故障先看响应码。

### 6. 会话级时序分析（命令-响应卡在哪、延迟多久）
定位"协议交互卡在某条命令、响应丢失/延迟"（FTP/HTTP/MQTT/CoAP 登录或请求偶发超时）：
```
list_conversations(protocol="tcp")      # 用 duration + 包数挑异常会话：跨度远超同类 = 卡在某步等响应
extract_stream(endpoint_a=..., endpoint_b=..., output_format="full")  # 看命令(C→S)-响应(S→C)序列
get_statistics(metric="latency")        # 量化 HTTP 请求-响应延迟（avg/min/max）
```
**判读**：一条命令(C→S)后长时间无对应响应(S→C)即"卡点"。对比同类正常会话的同一步延迟，判定偶发慢响应 vs 丢响应。

> 实战教训（迁移自旧脚本）：FTP 压测偶发 ERROR，`list_conversations` 看出某会话跨度 51 秒（其余 ~2 秒），`extract_stream` 该会话发现 PASS 命令发出后 30 秒无 230 响应——模块超时返回 ERROR，而服务器 230 其实更晚才到（延迟响应）。靠"会话跨度异常 + 命令-响应间隔"两步定位。方法论不变，工具换成 tshark。

### 7. 量化统计（新能力，旧脚本没有）
```
get_statistics(metric="retransmission")   # 重传率% + 重传包列表（带 5-tuple）
get_statistics(metric="packet_loss")      # 重复 ACK / 快速重传 / 乱序 计数 + 事件
get_statistics(metric="throughput")       # 总包/字节、平均 bps、fps
get_statistics(metric="tcp")              # 每连接包数/字节数
get_statistics(metric="all")              # 全量
```
怀疑"丢包/重传/带宽瓶颈/乱序"类网络层问题时，先跑统计拿量化证据，再 `extract_stream` 看具体包。

## 判据与避坑（迁移：工具换了仍然适用）

### 1. TCP 重传 vs 粘包（关键避坑点）
TCP 重传的包 seq 与原包相同，会被误判为"同段多条记录"。
- `get_statistics(metric="retransmission")` 已自动识别重传；
- `extract_stream` full 看 seq，**相同 seq 的包是重传，不是新数据/粘包**。
> 实战教训：用户曾纠正 AI"从报文看这个时间点没有粘包"——AI 把 TCP 重传捎带误判为粘包。解读时务必用 seq 去重。

### 2. 应用层假设必须在报文字节找证据
- 假设"服务器 421 空闲超时" → 必须 `extract_packets`/`extract_stream` 在 info/payload 找到字面 "421"；
- 假设"服务端主动关闭" → 必须看到 S→C 方向的 RST/FIN 先于客户端；
- **找不到字节证据即排除该假设**，不能靠 AT 日志猜。

### 3. TLS 加密内容的边界（与 Wireshark 相同）
TLS **加密的 ApplicationData 不可解**。但以下都是**明文**，足够定位大多数 TLS 问题：ClientHello(含 SNI 域名、密码套件、版本)、ServerHello、Alert level/desc、ChangeCipherSpec、记录层 ContentType。MQTT over TLS 同理。

### 4. Windows 环境注记
- tshark/capinfos 输出为 UTF-8；若经 GBK 终端管道出现乱码，设 `PYTHONUTF8=1`。
- 大日志含非 ASCII 字节时，`grep "ERROR"` 可能返回 `Binary file matches` 就停住——加 `-a` 强制按文本处理。

## 文本格式报文 dump

模块内置抓包 / 串口抓包 / QXDM 类工具常导出 **ASCII hex dump**（框线分隔 + `|偏移|hex|` 或 Wireshark 式 `0000  xx xx`），而非标准 pcap/pcapng。**tshark 只认标准 pcap/pcapng**。
- **注意**：子模块仅捆绑 `tshark` + `capinfos`，**未含 `text2pcap`**。
- 处理方式：
  1. **优先**用抓包工具的"导出 pcap/pcapng"选项（最省事）；
  2. 装完整 Wireshark 取其 `text2pcap` 转换：`text2pcap <dump.txt> out.pcap`，再喂给本工具；
  3. （可选）若现场频繁只有文本 dump，可后续补一个极小的 hex→pcap 预处理脚本。

## 报告留痕规范

报文相关结论写进 Bug 分析报告时，把 `extract_stream`(output_format="full") / `get_statistics` 的关键输出用 Write 落盘到 `.spec/bug/{工作项ID}_{问题描述}/analysis/pcap_report.md`，附 raw 字段让读者可独立复核，不依赖分析者的转述。

## 能力边界（诚实声明）

- ✅ **全协议栈解码（tshark 级，远超 scapy）**：HTTP/FTP/DNS/SMTP/TLS/MQTT/CoAP 等，分层树完整
- ✅ TCP 流双向重建 + 重传/重复ACK/乱序/HTTP 延迟等**定量统计**
- ✅ TLS 握手层 + SNI + Alert（覆盖大多数 TLS 问题）
- ✅ MQTT 3.1.1/5.0、CoAP（LWM2M 底层）完整解码
- ⚠️ TLS 加密内容不可解（需 keylog，Wireshark 同样限制）——MQTT over TLS 同理
- ⚠️ 文本格式 dump 需先转 pcap（子模块未含 text2pcap，见上节）
- ⚠️ MCP server 未注册 / 依赖未装 / 子模块未 init 时该路不通——SessionStart 钩子会打印指引
- ✅ 对 IoT 模组常见报文类 bug（连接断开、握手失败、MQTT 连接、CoAP 响应异常、重传/丢包、协议交互错误）覆盖率高
