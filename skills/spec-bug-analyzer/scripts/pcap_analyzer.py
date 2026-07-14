#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pcap_analyzer.py — pcap 报文解析工具，供 AI 直接替代 Wireshark 完成协议解码。

依赖：scapy (pip install scapy)
  - 优先用 scapy 做全协议栈分层解码（Ether/IP/TCP/HTTP/DNS/TLS 等）
  - scapy.contrib.tls 不可用时，TLS 降级为手动解记录层（ContentType/长度）

子命令：
  flows    列出所有 TCP/UDP 流摘要（端点对/时间/包数/结局 FIN/RST）
  show     展示指定流的逐包完整解码（时间/方向/各层字段/payload摘要）
  around   给定时间点，定位异常前后所有流的报文交互（核心定位命令）
  search   跨所有包搜 payload（明文关键字如 421/GET/TLS SNI 域名）
  decode   单包深度解码（wireshark 风格分层树）

用法示例：
  python pcap_analyzer.py flows capture.pcap
  python pcap_analyzer.py show capture.pcap --lport 49152
  python pcap_analyzer.py around capture.pcap --time 10:28:18.300 --window 5
  python pcap_analyzer.py search capture.pcap -k "421" "RST"
  python pcap_analyzer.py decode capture.pcap --packet 42
"""
import sys
import os
import io
import argparse
from datetime import datetime, timezone, timedelta

# ============ 依赖检测 ============
try:
    from scapy.all import rdpcap, Ether, IP, IPv6, TCP, UDP, ARP, ICMP, Raw, DNS, DHCP
    from scapy.packet import Packet, NoPayload
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False

# MQTT / CoAP 扩展（可选，失败不影响主功能）
MQTT_AVAILABLE = False
COAP_AVAILABLE = False
try:
    from scapy.contrib.mqtt import MQTT
    MQTT_AVAILABLE = True
except Exception:
    pass
try:
    from scapy.contrib.coap import CoAP
    COAP_AVAILABLE = True
except Exception:
    pass


TZ_CST = timezone(timedelta(hours=8))
TCP_FLAG_NAMES = {0x01: "FIN", 0x02: "SYN", 0x04: "RST", 0x08: "PSH",
                  0x10: "ACK", 0x20: "URG"}

# MQTT 控制包类型（MQTT 3.1.1 & 5.0 通用）
MQTT_TYPE_NAMES = {
    1: "CONNECT", 2: "CONNACK", 3: "PUBLISH", 4: "PUBACK", 5: "PUBREC",
    6: "PUBREL", 7: "PUBCOMP", 8: "SUBSCRIBE", 9: "SUBACK", 10: "UNSUBSCRIBE",
    11: "UNSUBACK", 12: "PINGREQ", 13: "PINGRESP", 14: "DISCONNECT",
    15: "AUTH",  # MQTT 5.0 新增
}

# CoAP 方法/响应码映射（code = class.detail，class×32+detail）
COAP_CODES = {
    0: "EMPTY", 1: "GET", 2: "POST", 3: "PUT", 4: "DELETE",
    # 2.xx 成功
    65: "2.01 Created", 66: "2.02 Deleted", 67: "2.03 Valid",
    68: "2.04 Changed", 69: "2.05 Content",
    # 4.xx 客户端错误
    128: "4.00 Bad Request", 129: "4.01 Unauthorized", 130: "4.02 Bad Option",
    131: "4.03 Forbidden", 132: "4.04 Not Found", 133: "4.05 Method Not Allowed",
    140: "4.12 Precondition Failed", 141: "4.13 Request Entity Too Large",
    143: "4.15 Unsupported Content Format",
    # 5.xx 服务端错误
    160: "5.00 Internal Server Error", 161: "5.01 Not Implemented",
    162: "5.02 Bad Gateway", 163: "5.03 Service Unavailable",
}
COAP_TYPE_NAMES = {0: "CON", 1: "NON", 2: "ACK", 3: "RST"}
# CoAP Option Number（常用）
COAP_OPT_NAMES = {
    1: "If-Match", 3: "Uri-Host", 4: "ETag", 5: "If-None-Match",
    6: "Observe", 7: "Uri-Port", 8: "Location-Path", 11: "Uri-Path",
    12: "Content-Format", 14: "Max-Age", 15: "Uri-Query", 17: "Accept",
    20: "Location-Query", 23: "Block2", 27: "Block1", 35: "Proxy-URI",
}


def require_scapy():
    """scapy 未装时给出明确提示并退出"""
    if not SCAPY_AVAILABLE:
        print("[!] 未安装 scapy，无法解析 pcap。", file=sys.stderr)
        print("    安装命令: pip install scapy", file=sys.stderr)
        print("    scapy 提供全协议栈分层解码，是本工具的核心依赖。", file=sys.stderr)
        sys.exit(2)


def fmt_ts(t, base=None):
    """时间戳格式化为 HH:MM:SS.mmm（相对于首个包，便于跨包对齐）"""
    dt = datetime.fromtimestamp(t, tz=timezone.utc).astimezone(TZ_CST)
    return dt.strftime("%H:%M:%S.") + f"{int((t % 1) * 1000):03d}"


def parse_time_arg(s):
    """解析 HH:MM:SS 或 HH:MM:SS.mmm 为秒数（仅当天内，跨天需 pcap 实际日期）
    返回 (h, m, s, ms) 用于与 pcap 包时间戳的 CST 时分秒比较"""
    s = s.strip()
    parts = s.split(":")
    if len(parts) != 3:
        raise ValueError(f"时间格式应为 HH:MM:SS.mmm，得到: {s}")
    h, m = int(parts[0]), int(parts[1])
    sp = parts[2].split(".")
    sec = int(sp[0])
    ms = int(sp[1]) if len(sp) > 1 else 0
    return h, m, sec, ms


def time_in_window(pkt_time, target, window):
    """判断 pkt_time 的 CST 时分秒是否在 target ± window 秒内"""
    h, m, sec, ms = target
    target_total = h * 3600 + m * 60 + sec + ms / 1000.0
    dt = datetime.fromtimestamp(pkt_time, tz=timezone.utc).astimezone(TZ_CST)
    pkt_total = dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1e6
    return abs(pkt_total - target_total) <= window


# ============ 流组织 ============

def get_flow_key(pkt):
    """生成流标识 (proto, lower_ip, lower_port, higher_ip, higher_port)
    端口小的在前，保证双向包归到同一流"""
    if IP in pkt and TCP in pkt:
        ip_s, ip_d = pkt[IP].src, pkt[IP].dst
        sp, dp = pkt[TCP].sport, pkt[TCP].dport
        proto = "TCP"
    elif IP in pkt and UDP in pkt:
        ip_s, ip_d = pkt[IP].src, pkt[IP].dst
        sp, dp = pkt[UDP].sport, pkt[UDP].dport
        proto = "UDP"
    else:
        return None
    # 规范化：端口小的在前
    if (ip_s, sp) <= (ip_d, dp):
        return (proto, ip_s, sp, ip_d, dp)
    return (proto, ip_d, dp, ip_s, sp)


def build_flows(pkts):
    """遍历所有包，按流分组，统计每个流的摘要信息和包列表"""
    flows = {}  # key -> dict
    for idx, pkt in enumerate(pkts):
        if Ether not in pkt and IP not in pkt and IPv6 not in pkt:
            continue
        key = get_flow_key(pkt)
        if key is None:
            continue
        fl = flows.get(key)
        if fl is None:
            fl = {
                "key": key, "proto": key[0],
                "endpoints": (f"{key[1]}:{key[2]}", f"{key[3]}:{key[4]}"),
                "start_t": None, "end_t": None,
                "packets": [],  # [(idx, pkt), ...]
                "byte_count": 0,
                # TCP 专用
                "c_fin": False, "s_fin": False, "c_rst": False, "s_rst": False,
                "has_syn": False, "has_synack": False,
            }
            flows[key] = fl
        t = float(pkt.time)
        if fl["start_t"] is None:
            fl["start_t"] = t
        fl["end_t"] = t
        fl["packets"].append((idx, pkt))
        # 字节数统计（IP 层 payload）
        if IP in pkt:
            fl["byte_count"] += len(pkt[IP].payload)
        # TCP 结局判定
        if TCP in pkt:
            flags = pkt[TCP].flags
            ip_s, sp = pkt[IP].src, pkt[TCP].sport
            # 判方向：端口小的端点 = lower（即 key[1]:key[2]）
            is_from_lower = (ip_s == key[1] and sp == key[2])
            if flags & 0x02:  # SYN
                if flags & 0x10:  # SYN+ACK
                    fl["has_synack"] = True
                else:
                    fl["has_syn"] = True
            if flags & 0x01:  # FIN
                if is_from_lower:
                    fl["c_fin"] = True
                else:
                    fl["s_fin"] = True
            if flags & 0x04:  # RST
                if is_from_lower:
                    fl["c_rst"] = True
                else:
                    fl["s_rst"] = True
    return flows


def flow_outcome(fl):
    """判定 TCP 流结局"""
    if fl["proto"] != "TCP":
        return ""
    if fl["c_rst"] and not fl["c_fin"]:
        return "RST(客户端主动断)"
    if fl["s_rst"] and not fl["s_fin"]:
        return "RST(服务端主动断)"
    if fl["c_fin"] or fl["s_fin"]:
        return "FIN(正常关闭)"
    if not fl["has_syn"]:
        return "无握手(片段)"
    return "未关闭/进行中"


# ============ 协议解码 ============

def decode_flags(flags_val):
    """TCP flags 数值转可读名"""
    names = []
    for bit, name in sorted(TCP_FLAG_NAMES.items()):
        if flags_val & bit:
            names.append(name)
    return ",".join(names) if names else "0"


def decode_tcp_payload(payload, direction="", sport=0, dport=0):
    """解码 TCP payload 的协议层内容，返回摘要字符串。
    sport/dport 用于识别端口型协议（如 MQTT 1883）。"""
    if not payload or len(payload) == 0:
        return ""
    summaries = []

    # MQTT（端口 1883，优先于其他识别）
    is_mqtt_port = (1883 in (sport, dport))
    if is_mqtt_port:
        mqtt = decode_mqtt(payload)
        if mqtt:
            summaries.append(mqtt)
            return " | ".join(summaries)

    # TLS 记录层（ContentType + 版本 + 长度）
    tls = decode_tls_records(payload)
    if tls:
        summaries.extend(tls)

    # HTTP（明文，以方法或 HTTP/x 开头）
    try:
        text = payload.decode("ascii", errors="replace")
        first_line = text.split("\r\n")[0] if "\r\n" in text else text[:80]
        if first_line.startswith(("GET ", "POST", "PUT ", "DELETE", "HEAD ", "OPTIONS", "HTTP/")):
            summaries.append(f"HTTP: {first_line[:80]}")
            # 提取关键头
            for line in text.split("\r\n")[:10]:
                ll = line.lower()
                if ll.startswith(("host:", "content-length:", "content-type:", "transfer-encoding:")):
                    summaries.append(f"  {line.strip()[:80]}")
    except Exception:
        pass

    # FTP / SMTP / POP3（明文命令/响应码）
    try:
        text = payload.decode("ascii", errors="ignore")
        stripped = text.strip()
        if stripped and (stripped[0:3].isdigit() and len(stripped) > 3 and stripped[3] in " -" or
                         stripped.upper().startswith(("USER", "PASS", "CWD", "PASV", "PORT",
                                                       "RETR", "STOR", "LIST", "QUIT", "FEAT",
                                                       "TYPE", "SIZE", "EPSV", "AUTH", "EHLO",
                                                       "HELO", "MAIL", "RCPT", "DATA"))):
            summaries.append(f"FTP/CMD: {stripped[:80]}")
    except Exception:
        pass

    # DNS（UDP 53）
    # 由 decode_dns 单独处理

    if not summaries:
        summaries.append(f"raw {len(payload)}B")
    return " | ".join(summaries)


def decode_tls_records(payload):
    """解析 TLS 记录层（可能有多个记录拼接）"""
    records = []
    i = 0
    n = len(payload)
    # TLS ContentType 定义
    CT_NAMES = {20: "ChangeCipherSpec", 21: "Alert", 22: "Handshake", 23: "ApplicationData"}
    HS_NAMES = {1: "ClientHello", 2: "ServerHello", 11: "Certificate", 12: "ServerKeyExchange",
                13: "CertificateRequest", 14: "ServerHelloDone", 16: "ClientKeyExchange",
                20: "Finished"}
    while i + 5 <= n:
        ct = payload[i]
        # 版本 2 字节
        ver = (payload[i + 1] << 8) | payload[i + 2]
        rlen = (payload[i + 3] << 8) | payload[i + 4]
        # 合理性校验
        if ct not in CT_NAMES or rlen > 16384 + 256:
            break
        ct_name = CT_NAMES[ct]
        rec_body = payload[i + 5: i + 5 + rlen]
        if ct == 22 and len(rec_body) >= 4:
            # Handshake 层：类型 + 长度(3) + body
            hs_type = rec_body[0]
            hs_name = HS_NAMES.get(hs_type, f"Handshake-{hs_type}")
            detail = ""
            if hs_type == 1 and len(rec_body) >= 6:
                # ClientHello: 尝试提取 SNI
                sni = extract_sni(rec_body)
                detail = f" SNI={sni}" if sni else ""
                detail += f" ver=0x{ver:04x}"
            elif hs_type == 2:
                detail = f" ver=0x{ver:04x}"
            elif hs_type == 11:
                detail = " (证书链)"
            elif hs_type == 21 or ct == 21:
                detail = " (Alert)"
            records.append(f"TLS {hs_name}{detail} len={rlen}")
        elif ct == 21:
            # Alert
            level = rec_body[0] if len(rec_body) >= 1 else -1
            desc = rec_body[1] if len(rec_body) >= 2 else -1
            level_name = {1: "warning", 2: "fatal"}.get(level, level)
            records.append(f"TLS Alert {level_name}({desc}) len={rlen}")
        elif ct == 23:
            records.append(f"TLS AppData len={rlen}")
        elif ct == 20:
            records.append(f"TLS ChangeCipherSpec len={rlen}")
        else:
            records.append(f"TLS {ct_name} len={rlen}")
        i += 5 + rlen
        if i > n:
            break
    return records


def extract_sni(client_hello_body):
    """从 ClientHello body 提取 SNI 域名（尽力解析）"""
    try:
        # ClientHello body: legacy_version(2) + random(32) + session_id(1+n) +
        # cipher_suites(2+n) + compression(1+n) + extensions(2+...)
        i = 2 + 32  # skip version + random
        if i >= len(client_hello_body):
            return None
        sid_len = client_hello_body[i]
        i += 1 + sid_len
        if i + 2 > len(client_hello_body):
            return None
        cs_len = (client_hello_body[i] << 8) | client_hello_body[i + 1]
        i += 2 + cs_len
        if i + 1 > len(client_hello_body):
            return None
        comp_len = client_hello_body[i]
        i += 1 + comp_len
        if i + 2 > len(client_hello_body):
            return None
        ext_total = (client_hello_body[i] << 8) | client_hello_body[i + 1]
        i += 2
        ext_end = i + ext_total
        while i + 4 <= ext_end and i + 4 <= len(client_hello_body):
            ext_type = (client_hello_body[i] << 8) | client_hello_body[i + 1]
            ext_len = (client_hello_body[i + 2] << 8) | client_hello_body[i + 3]
            i += 4
            if ext_type == 0:  # server_name extension
                # SNI list: total_len(2) + [type(1)=0_host + len(2) + name]
                if i + 2 <= len(client_hello_body):
                    sni_list_len = (client_hello_body[i] << 8) | client_hello_body[i + 1]
                    j = i + 2
                    if j + 3 <= len(client_hello_body) and client_hello_body[j] == 0:
                        name_len = (client_hello_body[j + 1] << 8) | client_hello_body[j + 2]
                        name = client_hello_body[j + 3: j + 3 + name_len]
                        return name.decode("ascii", errors="replace")
            i += ext_len
        return None
    except Exception:
        return None


def decode_dns(payload):
    """解码 DNS 问答摘要"""
    try:
        if len(payload) < 12:
            return None
        tid = (payload[0] << 8) | payload[1]
        flags = (payload[2] << 8) | payload[3]
        is_resp = bool(flags & 0x8000)
        rcode = flags & 0x000F
        qdcount = (payload[4] << 8) | payload[5]
        # 解析第一个 question 的域名
        i = 12
        labels = []
        while i < len(payload) and payload[i] != 0:
            llen = payload[i]
            if llen > 63 or i + 1 + llen > len(payload):
                break
            labels.append(payload[i + 1: i + 1 + llen].decode("ascii", errors="replace"))
            i += 1 + llen
        domain = ".".join(labels) if labels else "?"
        qtype = "A"
        if i < len(payload) and i + 4 <= len(payload):
            qt = (payload[i + 1] << 8) | payload[i + 2]
            qtype = {1: "A", 28: "AAAA", 5: "CNAME", 15: "MX", 2: "NS"}.get(qt, str(qt))
        action = "响应" if is_resp else "查询"
        rcode_txt = "" if rcode == 0 else f" rcode={rcode}"
        return f"DNS {action} {domain}({qtype}){rcode_txt}"
    except Exception:
        return None


def decode_mqtt(payload):
    """解码 MQTT 控制包（支持 3.1.1 & 5.0），返回摘要字符串"""
    if not MQTT_AVAILABLE:
        return None
    if not payload or len(payload) < 2:
        return None
    # 快速过滤：首个字节高 4 位是控制包类型（1-15）
    pkt_type = (payload[0] >> 4) & 0x0F
    if pkt_type == 0:
        return None  # 0 不是合法 MQTT 类型
    try:
        m = MQTT(payload)
        type_name = MQTT_TYPE_NAMES.get(pkt_type, f"Type{pkt_type}")
        parts = [f"MQTT {type_name}"]
        # CONNECT：协议版本 + clientId + 用户名/keepalive
        if pkt_type == 1:
            level = getattr(m, "protolevel", None)
            ver_name = "5.0" if level == 5 else ("3.1.1" if level == 4 else str(level))
            cid = getattr(m, "clientId", b"")
            if isinstance(cid, bytes):
                cid = cid.decode("ascii", errors="replace")
            parts.append(f"v{ver_name}")
            if cid:
                parts.append(f"clientId={cid}")
            klive = getattr(m, "klive", None)
            if klive:
                parts.append(f"keepalive={klive}s")
        # CONNACK：返回码/原因码（scapy 字段名 retcode，在 connack payload 层）
        elif pkt_type == 2:
            # CONNACK 的返回码在下一层（MQTT connack）
            inner = m.payload if m.payload and not isinstance(m.payload, NoPayload) else m
            resp = getattr(inner, "retcode", None)
            if resp is not None:
                # scapy 已自动映射成可读名（如 "Connection Accepted"），数值也带上
                rc_names = {0: "Accepted", 1: "UnacceptableProtocolVersion",
                            2: "IdentifierRejected", 3: "ServerUnavailable",
                            4: "BadUsernamePassword", 5: "NotAuthorized"}
                rc_txt = rc_names.get(resp, str(resp))
                parts.append(f"rc={resp}({rc_txt})")
        # PUBLISH：topic + QoS + retain
        elif pkt_type == 3:
            topic = getattr(m, "topic", b"")
            if isinstance(topic, bytes):
                topic = topic.decode("ascii", errors="replace")
            qos = getattr(m, "QOS", 0)
            retain = getattr(m, "RETAIN", 0)
            if topic:
                parts.append(f"topic={topic}")
            if qos:
                parts.append(f"QoS={qos}")
            if retain:
                parts.append("retain")
            value = getattr(m, "value", None)
            if value:
                try:
                    val_str = bytes(value).rstrip(b"\x00").decode("utf-8", errors="replace")[:40]
                    parts.append(f'payload="{val_str}"')
                except Exception:
                    parts.append(f"payload={len(bytes(value))}B")
        # SUBSCRIBE：topic 列表
        elif pkt_type == 8:
            # scapy 的 SUBSCRIBE 解析较复杂，尽力取 topic
            for fname in ("topic", "TOPIC"):
                topic = getattr(m, fname, None)
                if topic:
                    if isinstance(topic, bytes):
                        topic = topic.decode("ascii", errors="replace")
                    parts.append(f"topic={topic}")
                    break
        return " ".join(parts)
    except Exception:
        return None


def decode_coap(payload):
    """解码 CoAP 消息，返回摘要字符串"""
    if not COAP_AVAILABLE:
        return None
    if not payload or len(payload) < 4:
        return None
    # 快速过滤：版本号必须是 1（高 2 位）
    ver = (payload[0] >> 6) & 0x03
    if ver != 1:
        return None
    try:
        c = CoAP(payload)
        type_val = int(getattr(c, "type", 0))
        type_name = COAP_TYPE_NAMES.get(type_val, str(type_val))
        code_val = int(getattr(c, "code", 0))
        code_name = COAP_CODES.get(code_val, f"code={code_val}")
        msgid = int(getattr(c, "msg_id", 0))
        parts = [f"CoAP {type_name} {code_name}"]
        if msgid:
            parts.append(f"msgid={msgid}")
        token = getattr(c, "token", b"")
        if token:
            parts.append(f"token={bytes(token).hex()}")
        # options 手动解析（scapy 的 option 解析不可靠，自行按 CoAP option 格式解码）
        opt_strs = _decode_coap_options(payload, int(getattr(c, "tkl", 0)))
        if opt_strs:
            parts.append("[" + ",".join(opt_strs) + "]")
        return " ".join(parts)
    except Exception:
        return None


def _decode_coap_options(payload, tkl):
    """手动解析 CoAP options（scapy 解析不可靠，自行按规范解码）。
    option 格式：delta(4bit) + length(4bit) [+ 扩展delta] [+ 扩展length] + value
    起始偏移 = 4(头) + tkl(token长度)"""
    opts = []
    i = 4 + tkl
    cur_delta = 0
    while i < len(payload):
        if payload[i] == 0xFF:  # payload marker
            break
        b = payload[i]
        delta_nib = (b >> 4) & 0x0F
        len_nib = b & 0x0F
        i += 1
        # 扩展 delta
        if delta_nib == 13:
            if i >= len(payload):
                break
            delta = payload[i] + 13
            i += 1
        elif delta_nib == 14:
            if i + 1 >= len(payload):
                break
            delta = int.from_bytes(payload[i:i + 2], "big") + 269
            i += 2
        elif delta_nib == 15:
            break  # 非法
        else:
            delta = delta_nib
        # 扩展 length
        if len_nib == 13:
            if i >= len(payload):
                break
            olen = payload[i] + 13
            i += 1
        elif len_nib == 14:
            if i + 1 >= len(payload):
                break
            olen = int.from_bytes(payload[i:i + 2], "big") + 269
            i += 2
        elif len_nib == 15:
            break  # 非法
        else:
            olen = len_nib
        if i + olen > len(payload):
            break
        oval = payload[i:i + olen]
        i += olen
        cur_delta += delta
        oname = COAP_OPT_NAMES.get(cur_delta, f"opt{cur_delta}")
        # value 友好化：可打印 ASCII 直接显示；否则尝试当数值（大端）；最后才 hex
        if all(32 <= b < 127 for b in oval):
            oval_str = oval.decode("ascii")
        elif len(oval) <= 4:
            oval_str = str(int.from_bytes(oval, "big"))
        else:
            oval_str = oval.hex()
        opts.append(f"{oname}={oval_str}")
        if len(opts) >= 8:
            break
    return opts


def format_packet(idx, pkt, fl_key, show_hex=False):
    """格式化单个包为一行摘要 + 可选多行解码"""
    t = float(pkt.time)
    ts = fmt_ts(t)
    # 方向判定
    direction = "?"
    if IP in pkt and TCP in pkt:
        ip_s, sp = pkt[IP].src, pkt[TCP].sport
        is_from_lower = (ip_s == fl_key[1] and sp == fl_key[2])
        direction = "C>S" if is_from_lower else "S>C"
        flags = int(pkt[TCP].flags)
        seq = pkt[TCP].seq
        ack = pkt[TCP].ack
        payload = bytes(pkt[TCP].payload) if pkt[TCP].payload else b""
        flag_str = decode_flags(flags)
        line = f"  {ts}  {direction}  [{flag_str:<12}] seq={seq} ack={ack} len={len(payload)}"
        if payload:
            dport = pkt[TCP].dport
            # MQTT（端口 1883 或 MQTT 字节特征）
            decoded = decode_tcp_payload(payload, direction, sport=sp, dport=dport)
            line += f"\n                    {decoded}"
    elif IP in pkt and UDP in pkt:
        ip_s, sp = pkt[IP].src, pkt[UDP].sport
        is_from_lower = (ip_s == fl_key[1] and sp == fl_key[2])
        direction = "C>S" if is_from_lower else "S>C"
        payload = bytes(pkt[UDP].payload) if pkt[UDP].payload else b""
        dport = pkt[UDP].dport
        sport = pkt[UDP].sport
        proto_info = ""
        if dport == 53 or sport == 53:
            d = decode_dns(payload)
            if d:
                proto_info = f"\n                    {d}"
        elif dport == 5683 or sport == 5683 or dport == 5684 or sport == 5684:
            # CoAP（5683 标准 / 5684 DTLS）
            c = decode_coap(payload)
            if c:
                proto_info = f"\n                    {c}"
        line = f"  {ts}  {direction}  UDP len={len(payload)}{proto_info}"
    else:
        line = f"  {ts}  (非 TCP/UDP 包 #{idx})"

    if show_hex and (TCP in pkt or UDP in pkt):
        layer = pkt[TCP] if TCP in pkt else pkt[UDP]
        payload = bytes(layer.payload) if layer.payload else b""
        if payload:
            hex_str = payload[:64].hex(" ")
            line += f"\n      hex: {hex_str}{'...' if len(payload) > 64 else ''}"
    return line


# ============ 子命令实现 ============

def cmd_flows(args):
    """列出所有流摘要"""
    require_scapy()
    pkts = rdpcap(args.input)
    flows = build_flows(pkts)
    if not flows:
        print("[!] 未找到 TCP/UDP 流")
        return

    # 过滤
    filtered = []
    for key, fl in flows.items():
        if args.ip:
            if args.ip not in (key[1], key[3]):
                continue
        if args.port:
            if int(args.port) not in (key[2], key[4]):
                continue
        filtered.append(fl)

    filtered.sort(key=lambda f: f["start_t"] or 0)
    print(f"共 {len(filtered)} 个流（总 {len(pkts)} 包）\n")
    print(f"{'#':>3}  {'协议':<5} {'端点A':<22} {'端点B':<22} {'起止时间':<26} {'包数':>4} {'大小':>7}  结局")
    print("-" * 120)
    for i, fl in enumerate(filtered):
        dur = (fl["end_t"] - fl["start_t"]) if fl["start_t"] and fl["end_t"] else 0
        time_range = f"{fmt_ts(fl['start_t'])}~{fmt_ts(fl['end_t'])}"
        size_str = f"{fl['byte_count'] // 1024}K" if fl["byte_count"] >= 1024 else f"{fl['byte_count']}B"
        outcome = flow_outcome(fl)
        print(f"{i:>3}  {fl['proto']:<5} {fl['endpoints'][0]:<22} {fl['endpoints'][1]:<22} "
              f"{time_range:<26} {len(fl['packets']):>4} {size_str:>7}  {outcome}")


def cmd_show(args):
    """展示指定流的逐包解码"""
    require_scapy()
    pkts = rdpcap(args.input)
    flows = build_flows(pkts)
    ordered = sorted(flows.values(), key=lambda f: f["start_t"] or 0)

    fl = _select_flow(ordered, args)
    if fl is None:
        return

    dur = (fl["end_t"] - fl["start_t"]) if fl["start_t"] and fl["end_t"] else 0
    outcome = flow_outcome(fl)
    print(f"\n=== Flow #{ordered.index(fl)}  {fl['proto']}  {fmt_ts(fl['start_t'])} → {fmt_ts(fl['end_t'])} ({dur:.3f}s) ===")
    print(f"  {fl['endpoints'][0]} ↔ {fl['endpoints'][1]}   {len(fl['packets'])}包   结局: {outcome}\n")

    for idx, pkt in fl["packets"]:
        print(format_packet(idx, pkt, fl["key"], show_hex=args.hex))


def cmd_around(args):
    """定位异常时刻前后的报文交互"""
    require_scapy()
    pkts = rdpcap(args.input)
    target = parse_time_arg(args.time)
    window = args.window

    print(f"\n=== 异常时刻 {args.time} ± {window}s 的报文交互 ===\n")

    # 如指定 lport，只看该流；否则看窗口内所有包
    if args.lport:
        flows = build_flows(pkts)
        target_fl = None
        for fl in flows.values():
            if int(args.lport) in (fl["key"][2], fl["key"][4]):
                target_fl = fl
                break
        if not target_fl:
            print(f"[!] 未找到本地端口 {args.lport} 的流")
            return
        print(f"流: {target_fl['endpoints'][0]} ↔ {target_fl['endpoints'][1]}\n")
        for idx, pkt in target_fl["packets"]:
            t = float(pkt.time)
            if time_in_window(t, target, window):
                print(format_packet(idx, pkt, target_fl["key"], show_hex=False))
    else:
        # 所有窗口内的包，按时间排序
        matched = [(idx, pkt) for idx, pkt in enumerate(pkts)
                   if time_in_window(float(pkt.time), target, window)]
        print(f"窗口内共 {len(matched)} 个包（跨所有流）\n")
        for idx, pkt in matched:
            key = get_flow_key(pkt)
            if key is None:
                continue
            print(format_packet(idx, pkt, key, show_hex=False))


def cmd_search(args):
    """跨所有包搜 payload"""
    require_scapy()
    pkts = rdpcap(args.input)
    keywords = [k.encode("ascii", errors="ignore") for k in args.keywords]
    found = 0
    for idx, pkt in enumerate(pkts):
        payload = b""
        if TCP in pkt and pkt[TCP].payload:
            payload = bytes(pkt[TCP].payload)
        elif UDP in pkt and pkt[UDP].payload:
            payload = bytes(pkt[UDP].payload)
        if not payload:
            continue
        for kw in keywords:
            if kw.lower() in payload.lower():
                t = fmt_ts(float(pkt.time))
                # 方向
                src = f"{pkt[IP].src}:{pkt[TCP].sport}" if TCP in pkt else \
                      f"{pkt[IP].src}:{pkt[UDP].sport}" if UDP in pkt else "?"
                dst = f"{pkt[IP].dst}:{pkt[TCP].dport}" if TCP in pkt else \
                      f"{pkt[IP].dst}:{pkt[UDP].dport}" if UDP in pkt else "?"
                # 上下文：匹配点前后片段
                pos = payload.lower().find(kw.lower())
                ctx_start = max(0, pos - 20)
                ctx_end = min(len(payload), pos + len(kw) + 60)
                ctx = payload[ctx_start:ctx_end].decode("ascii", errors="replace").replace("\r", "\\r").replace("\n", "\\n")
                print(f"  包#{idx:<5} {t}  {src} → {dst}  [{kw.decode()}]")
                print(f"           ...{ctx}...")
                found += 1
                if found >= args.max_results:
                    print(f"\n(已达 --max-results {args.max_results} 上限)")
                    return
                break
    print(f"\n共匹配 {found} 个包")


def cmd_decode(args):
    """单包深度解码"""
    require_scapy()
    pkts = rdpcap(args.input)
    if args.packet >= len(pkts):
        print(f"[!] 包序号 {args.packet} 超出范围（共 {len(pkts)} 包）")
        return
    pkt = pkts[args.packet]
    t = fmt_ts(float(pkt.time))
    print(f"\n=== 包 #{args.packet}  {t} ===\n")
    # scapy 分层树
    pkt.show()
    # 附 raw hex
    if args.hex:
        raw = bytes(pkt)
        print(f"\n--- raw hex ({len(raw)} bytes) ---")
        for off in range(0, min(len(raw), 256), 16):
            chunk = raw[off:off + 16]
            hex_part = " ".join(f"{b:02x}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            print(f"  {off:04x}  {hex_part:<48}  {ascii_part}")
        if len(raw) > 256:
            print(f"  ... (共 {len(raw)} 字节，仅显示前 256)")


# ============ 辅助 ============

def _select_flow(ordered_flows, args):
    """按 flow-id 或 lport 选流"""
    if args.flow_id is not None:
        if args.flow_id >= len(ordered_flows):
            print(f"[!] flow-id {args.flow_id} 超出范围（共 {len(ordered_flows)} 流）")
            return None
        return ordered_flows[args.flow_id]
    if args.lport:
        for fl in ordered_flows:
            if int(args.lport) in (fl["key"][2], fl["key"][4]):
                return fl
        print(f"[!] 未找到本地端口 {args.lport} 的流")
        return None
    print("[!] 请用 --flow-id 或 --lport 指定要展示的流")
    return None


def main():
    parser = argparse.ArgumentParser(
        description="pcap 报文解析工具 — AI 直接替代 Wireshark 完成协议解码",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="依赖 scapy: pip install scapy")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    REPORT_HELP = "输出归档到 markdown 文件。推荐路径 .spec/bug/{id}_{desc}/analysis/pcap_report.md"

    # flows
    p = subparsers.add_parser("flows", help="列出所有 TCP/UDP 流摘要")
    p.add_argument("input", help="pcap 文件路径")
    p.add_argument("--ip", help="过滤 IP 地址")
    p.add_argument("--port", help="过滤端口号")
    p.add_argument("--report", help=REPORT_HELP)
    p.set_defaults(func=cmd_flows)

    # show
    p = subparsers.add_parser("show", help="展示指定流的逐包解码")
    p.add_argument("input", help="pcap 文件路径")
    p.add_argument("--flow-id", type=int, help="流序号（flows 命令输出的 # 列）")
    p.add_argument("--lport", help="本地端口")
    p.add_argument("--hex", action="store_true", help="附 raw hex")
    p.add_argument("--report", help=REPORT_HELP)
    p.set_defaults(func=cmd_show)

    # around
    p = subparsers.add_parser("around", help="定位异常时刻前后的报文交互")
    p.add_argument("input", help="pcap 文件路径")
    p.add_argument("--time", required=True, help="异常时刻 HH:MM:SS.mmm")
    p.add_argument("--window", type=float, default=5.0, help="时间窗口秒数（默认 5）")
    p.add_argument("--lport", help="锁定单个流（本地端口）")
    p.add_argument("--report", help=REPORT_HELP)
    p.set_defaults(func=cmd_around)

    # search
    p = subparsers.add_parser("search", help="跨所有包搜 payload")
    p.add_argument("input", help="pcap 文件路径")
    p.add_argument("-k", "--keywords", nargs="+", required=True, help="搜索关键字")
    p.add_argument("--max-results", type=int, default=50, help="最大结果数")
    p.add_argument("--report", help=REPORT_HELP)
    p.set_defaults(func=cmd_search)

    # decode
    p = subparsers.add_parser("decode", help="单包深度解码")
    p.add_argument("input", help="pcap 文件路径")
    p.add_argument("--packet", type=int, required=True, help="包序号")
    p.add_argument("--hex", action="store_true", help="附 raw hex")
    p.add_argument("--report", help=REPORT_HELP)
    p.set_defaults(func=cmd_decode)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    report_path = getattr(args, "report", None)
    if report_path:
        # tee 模式：同时写 stdout + 文件
        buf = io.StringIO()
        orig_stdout = sys.stdout

        class Tee:
            def __init__(self, *streams):
                self.streams = streams

            def write(self, data):
                for s in self.streams:
                    s.write(data)

            def flush(self):
                for s in self.streams:
                    if hasattr(s, "flush"):
                        s.flush()

        sys.stdout = Tee(orig_stdout, buf)
        try:
            args.func(args)
        finally:
            sys.stdout = orig_stdout
        # 写入报告文件
        if os.path.isdir(report_path):
            report_path = os.path.join(report_path, "pcap_report.md")
        os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("# pcap 报文分析报告\n\n")
            f.write("- **运行时间**: %s\n" % stamp)
            f.write("- **pcap 文件**: `%s`\n" % os.path.abspath(args.input))
            f.write("- **子命令**: `%s`\n\n" % args.command)
            f.write("---\n\n```\n")
            f.write(buf.getvalue())
            f.write("\n```\n")
        print("\n[report] %s" % report_path)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
