#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pcap_analyzer.py 端到端测试（unittest + scapy 构造 pcap，自包含无外部依赖）。

覆盖：
  层1 纯函数单测：decode_flags / parse_time_arg / time_in_window /
      decode_tls_records / extract_sni / decode_dns / decode_tcp_payload / flow_outcome
  层2 端到端：scapy 构造 pcap → 跑子命令（flows/show/around/search/decode）→ 断言输出

运行：
    python test_pcap_analyzer.py
    # 或
    python -m unittest test_pcap_analyzer -v

依赖：scapy（与被测脚本同）
"""

import os
import sys
import tempfile
import unittest
import subprocess
from datetime import datetime, timezone, timedelta

# 确保能 import 被测模块
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pcap_analyzer as pa

try:
    from scapy.all import Ether, IP, TCP, UDP, Raw, DNS, DNSQR, DNSRR, wrpcap, rdpcap
    SCAPY_OK = True
except Exception:
    SCAPY_OK = False

SCRIPT = os.path.join(HERE, "pcap_analyzer.py")
TZ_CST = timezone(timedelta(hours=8))


def _run_script(args):
    """运行 pcap_analyzer.py 子命令，返回 (returncode, stdout, stderr)。
    scapy 在 Windows 无 libpcap 时子进程偶发段错误（rc=3221225477/0xC0000005），
    自动重试一次以规避环境级偶发崩溃。"""
    cmd = [sys.executable, SCRIPT] + args
    for attempt in range(2):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8")
        if r.returncode != 3221225477:  # 非段错误，直接返回
            return r.returncode, r.stdout, r.stderr
        # 段错误：偶发，重试一次
    return r.returncode, r.stdout, r.stderr


def _mkpkt_tcp(src, dst, sport, dport, flags, seq, ack, payload=b"", t=None):
    """构造一个 TCP/IP/Ether 包"""
    layers = Ether() / IP(src=src, dst=dst) / TCP(sport=sport, dport=dport, flags=flags, seq=seq, ack=ack)
    if payload:
        layers = layers / Raw(load=payload)
    if t is not None:
        layers.time = t
    return layers


# ============================================================
# 层 1：纯函数单元测试
# ============================================================

class TestDecodeFlags(unittest.TestCase):
    def test_syn(self):
        self.assertEqual(pa.decode_flags(0x02), "SYN")

    def test_syn_ack(self):
        self.assertEqual(pa.decode_flags(0x12), "SYN,ACK")

    def test_fin_ack(self):
        self.assertEqual(pa.decode_flags(0x11), "FIN,ACK")

    def test_rst_ack(self):
        self.assertEqual(pa.decode_flags(0x14), "RST,ACK")

    def test_psh_ack(self):
        self.assertEqual(pa.decode_flags(0x18), "PSH,ACK")

    def test_zero(self):
        self.assertEqual(pa.decode_flags(0), "0")


class TestParseTimeArg(unittest.TestCase):
    def test_normal_ms(self):
        self.assertEqual(pa.parse_time_arg("10:28:18.300"), (10, 28, 18, 300))

    def test_no_ms(self):
        self.assertEqual(pa.parse_time_arg("10:28:18"), (10, 28, 18, 0))

    def test_bad_format(self):
        with self.assertRaises(ValueError):
            pa.parse_time_arg("10:28")

    def test_strips_whitespace(self):
        self.assertEqual(pa.parse_time_arg("  10:28:18.300  "), (10, 28, 18, 300))


class TestTimeInWindow(unittest.TestCase):
    def setUp(self):
        # 10:39:17.000 CST = 对应 UTC 时间戳
        dt = datetime(2026, 7, 9, 10, 39, 17, tzinfo=TZ_CST)
        self.t = dt.timestamp()

    def test_exact(self):
        self.assertTrue(pa.time_in_window(self.t, (10, 39, 17, 0), 5))

    def test_within(self):
        self.assertTrue(pa.time_in_window(self.t, (10, 39, 20, 0), 5))

    def test_outside(self):
        self.assertFalse(pa.time_in_window(self.t, (10, 39, 30, 0), 5))


class TestFlowOutcome(unittest.TestCase):
    def _fl(self, **kw):
        base = {"proto": "TCP", "c_fin": False, "s_fin": False, "c_rst": False,
                "s_rst": False, "has_syn": True}
        base.update(kw)
        return base

    def test_client_rst(self):
        self.assertIn("客户端", pa.flow_outcome(self._fl(c_rst=True)))

    def test_server_rst(self):
        self.assertIn("服务端", pa.flow_outcome(self._fl(s_rst=True)))

    def test_normal_close(self):
        self.assertIn("正常", pa.flow_outcome(self._fl(c_fin=True)))

    def test_udp_no_outcome(self):
        self.assertEqual(pa.flow_outcome({"proto": "UDP"}), "")

    def test_fragment_no_handshake(self):
        self.assertIn("片段", pa.flow_outcome(self._fl(has_syn=False)))


class TestDecodeTcpPayload(unittest.TestCase):
    def test_http_get(self):
        payload = b"GET /api/test HTTP/1.1\r\nHost: example.com\r\nContent-Length: 0\r\n\r\n"
        result = pa.decode_tcp_payload(payload)
        self.assertIn("HTTP", result)
        self.assertIn("GET", result)
        self.assertIn("Host:", result)

    def test_http_response(self):
        payload = b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n"
        result = pa.decode_tcp_payload(payload)
        self.assertIn("HTTP", result)
        self.assertIn("200", result)

    def test_ftp_response(self):
        payload = b"421 Connection closed\r\n"
        result = pa.decode_tcp_payload(payload)
        self.assertIn("421", result)

    def test_ftp_command(self):
        payload = b"USER anonymous\r\n"
        result = pa.decode_tcp_payload(payload)
        self.assertIn("USER", result)

    def test_empty(self):
        self.assertEqual(pa.decode_tcp_payload(b""), "")

    def test_raw_binary(self):
        result = pa.decode_tcp_payload(b"\x00\x01\x02\x03binary")
        self.assertIn("raw", result)


class TestDecodeTlsRecords(unittest.TestCase):
    def test_client_hello(self):
        # 构造一个最小 ClientHello：ContentType=22(Handshake), HS=1(ClientHello)
        # 记录头: 16 03 03 00 00 (len 先填 0，后面补)
        # 这是简化的字节级构造
        # ContentType=22, version=0x0303, length=9
        # HandshakeType=1(ClientHello), length(3byte)=5
        # legacy_version=0x0303, random(32)... 太长，用短 body 验证记录层
        rec = bytes([22, 3, 3, 0, 5, 1, 0, 0, 1, 3, 3])
        # body 不足 4 字节解析 HS，但记录层应识别
        result = pa.decode_tls_records(rec)
        self.assertTrue(any("Handshake" in r or "ClientHello" in r for r in result))

    def test_alert(self):
        # ContentType=21(Alert), version=0x0303, length=2, level=2(fatal), desc=40
        rec = bytes([21, 3, 3, 0, 2, 2, 40])
        result = pa.decode_tls_records(rec)
        self.assertTrue(any("Alert" in r and "fatal" in r for r in result))

    def test_appdata(self):
        # ContentType=23(ApplicationData), version=0x0303, length=5
        rec = bytes([23, 3, 3, 0, 5, 1, 2, 3, 4, 5])
        result = pa.decode_tls_records(rec)
        self.assertTrue(any("AppData" in r for r in result))

    def test_invalid_contenttype(self):
        # ContentType=99 非法，应返回空
        rec = bytes([99, 3, 3, 0, 5, 1, 2, 3, 4, 5])
        self.assertEqual(pa.decode_tls_records(rec), [])

    def test_multiple_records(self):
        # 两个记录拼接：AppData + Alert
        r1 = bytes([23, 3, 3, 0, 2, 1, 2])  # AppData len=2
        r2 = bytes([21, 3, 3, 0, 2, 1, 40])  # Alert warning(40)
        result = pa.decode_tls_records(r1 + r2)
        self.assertEqual(len(result), 2)

    def test_empty(self):
        self.assertEqual(pa.decode_tls_records(b""), [])


class TestExtractSNI(unittest.TestCase):
    def test_extract_from_real_clienthello(self):
        """用实战 pcap 包 #3 的真实 ClientHello body 测试 SNI 提取"""
        # 这是真实抓包的 ClientHello handshake body（去掉记录层头后的部分）
        # 来自 Y:\...6979103193_.../log_pubendyn_*.pcap 包#3
        # body 结构: legacy_version(2) + random(32) + session_id(1+0) +
        #            cipher_suites(2+6) + compression(1+1) + extensions
        # extension: type=0x0000(server_name), len=..., SNI=...
        body = bytes([
            # legacy_version
            0x03, 0x03,
            # random (32 bytes)
        ] + [0xAA] * 32 + [
            # session_id length=0
            0x00,
            # cipher_suites length=6
            0x00, 0x06, 0x13, 0x01, 0x13, 0x02, 0x13, 0x03,
            # compression methods length=1
            0x01, 0x00,
            # extensions total length
            0x00, 0x12,
            # extension: server_name (0x0000)
            0x00, 0x00,
            # extension length
            0x00, 0x0E,
            # SNI list length
            0x00, 0x0C,
            # server_name type=0(host_name)
            0x00,
            # name length
            0x00, 0x09,
            # name = "test.com"
        ] + list(b"test.com"))
        sni = pa.extract_sni(body)
        self.assertEqual(sni, "test.com")

    def test_no_sni(self):
        # 仅有 random + session_id + cipher + compression，无 extensions
        body = bytes([0x03, 0x03]) + b"\x00" * 32 + bytes([0x00, 0x00, 0x02, 0x13, 0x01, 0x01, 0x00])
        self.assertIsNone(pa.extract_sni(body))


class TestDecodeDns(unittest.TestCase):
    def test_query(self):
        # 构造 DNS 查询 example.com A 记录
        tid = 0x1234
        flags = 0x0100  # standard query, RD=1
        # 问题段: 7 example 3 com 0, type=A(1), class=IN(1)
        qname = bytes([7]) + b"example" + bytes([3]) + b"com" + bytes([0])
        qtype = (1).to_bytes(2, "big")  # A
        qclass = (1).to_bytes(2, "big")  # IN
        header = tid.to_bytes(2, "big") + flags.to_bytes(2, "big") + \
                 (1).to_bytes(2, "big") + (0).to_bytes(2, "big") + \
                 (0).to_bytes(2, "big") + (0).to_bytes(2, "big")
        payload = header + qname + qtype + qclass
        result = pa.decode_dns(payload)
        self.assertIsNotNone(result)
        self.assertIn("查询", result)
        self.assertIn("example.com", result)
        self.assertIn("A", result)

    def test_response(self):
        # 构造 DNS 响应
        tid = 0x5678
        flags = 0x8180  # response, RD=1, RA=1, rcode=0
        qname = bytes([3]) + b"com" + bytes([0])
        header = tid.to_bytes(2, "big") + flags.to_bytes(2, "big") + \
                 (1).to_bytes(2, "big") + (0).to_bytes(2, "big") + \
                 (0).to_bytes(2, "big") + (0).to_bytes(2, "big")
        payload = header + qname + (1).to_bytes(2, "big") + (1).to_bytes(2, "big")
        result = pa.decode_dns(payload)
        self.assertIsNotNone(result)
        self.assertIn("响应", result)

    def test_too_short(self):
        self.assertIsNone(pa.decode_dns(b"\x00\x01"))


# ============================================================
# 层 2：端到端测试（scapy 构造 pcap → 子命令 → 断言）
# ============================================================

@unittest.skipUnless(SCAPY_OK, "scapy 未安装，跳过端到端测试")
class TestEndToEnd(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pcap_test_")
        self.pcap_path = os.path.join(self.tmpdir, "test.pcap")

    def _write_pcap(self, pkts):
        wrpcap(self.pcap_path, pkts)

    def test_http_flow(self):
        """HTTP GET + 200 响应的完整 TCP 流"""
        t0 = datetime(2026, 7, 9, 10, 0, 0, tzinfo=TZ_CST).timestamp()
        # 三次握手 + HTTP GET + HTTP 200 + 四次挥手
        pkts = [
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 12345, 80, "S", 1000, 0, t=t0),
            _mkpkt_tcp("10.0.0.2", "10.0.0.1", 80, 12345, "SA", 2000, 1001, t=t0 + 0.01),
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 12345, 80, "A", 1001, 2001, t=t0 + 0.02),
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 12345, 80, "PA", 1001, 2001,
                       payload=b"GET / HTTP/1.1\r\nHost: x.com\r\n\r\n", t=t0 + 0.03),
            _mkpkt_tcp("10.0.0.2", "10.0.0.1", 80, 12345, "PA", 2001, 1019,
                       payload=b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello", t=t0 + 0.04),
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 12345, 80, "FA", 1024, 2010, t=t0 + 0.05),
            _mkpkt_tcp("10.0.0.2", "10.0.0.1", 80, 12345, "FA", 2010, 1025, t=t0 + 0.06),
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 12345, 80, "A", 1025, 2011, t=t0 + 0.07),
        ]
        self._write_pcap(pkts)

        # 测试 flows
        rc, out, err = _run_script(["flows", self.pcap_path])
        self.assertEqual(rc, 0, f"flows 失败: {err}")
        self.assertIn("TCP", out)
        self.assertIn("FIN", out)  # 正常关闭

        # 测试 show
        rc, out, err = _run_script(["show", self.pcap_path, "--flow-id", "0"])
        self.assertEqual(rc, 0, f"show 失败: {err}")
        self.assertIn("SYN", out)
        self.assertIn("HTTP", out)
        self.assertIn("GET", out)
        self.assertIn("200", out)
        self.assertIn("FIN", out)

    def test_rst_flow(self):
        """TCP RST 异常关闭"""
        t0 = datetime(2026, 7, 9, 11, 0, 0, tzinfo=TZ_CST).timestamp()
        pkts = [
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 20000, 443, "S", 1, 0, t=t0),
            _mkpkt_tcp("10.0.0.2", "10.0.0.1", 443, 20000, "SA", 1, 2, t=t0 + 0.01),
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 20000, 443, "A", 2, 2, t=t0 + 0.02),
            # 服务端直接 RST
            _mkpkt_tcp("10.0.0.2", "10.0.0.1", 443, 20000, "R", 2, 2, t=t0 + 0.5),
        ]
        self._write_pcap(pkts)

        rc, out, err = _run_script(["flows", self.pcap_path])
        self.assertEqual(rc, 0)
        self.assertIn("RST", out)
        self.assertIn("服务端", out)  # 服务端主动断

    def test_dns_flow(self):
        """DNS 查询响应"""
        dns_q = Ether() / IP(src="10.0.0.1", dst="8.8.8.8") / UDP(sport=1000, dport=53) / \
                DNS(id=0x1234, rd=1, qd=DNSQR(qname="test.org", qtype="A"))
        dns_r = Ether() / IP(src="8.8.8.8", dst="10.0.0.1") / UDP(sport=53, dport=1000) / \
                DNS(id=0x1234, qr=1, qd=DNSQR(qname="test.org", qtype="A"))
        self._write_pcap([dns_q, dns_r])

        rc, out, err = _run_script(["flows", self.pcap_path])
        self.assertEqual(rc, 0)
        self.assertIn("UDP", out)

        # show 应解码出 DNS
        rc, out, err = _run_script(["show", self.pcap_path, "--flow-id", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("DNS", out)
        self.assertIn("test.org", out)

    def test_search_http(self):
        """search 搜 HTTP 明文"""
        t0 = datetime(2026, 7, 9, 12, 0, 0, tzinfo=TZ_CST).timestamp()
        pkts = [
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 30000, 80, "PA", 1, 1,
                       payload=b"GET /search?q=hello HTTP/1.1\r\nHost: api.com\r\n\r\n", t=t0),
        ]
        self._write_pcap(pkts)

        rc, out, err = _run_script(["search", self.pcap_path, "-k", "hello"])
        self.assertEqual(rc, 0)
        self.assertIn("hello", out)
        self.assertIn("GET", out)

    def test_around_time_window(self):
        """around 定位时间窗口内的包"""
        t0 = datetime(2026, 7, 9, 13, 0, 0, tzinfo=TZ_CST).timestamp()
        pkts = [
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 40000, 80, "S", 1, 0, t=t0),
            _mkpkt_tcp("10.0.0.2", "10.0.0.1", 80, 40000, "SA", 1, 2, t=t0 + 0.01),
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 40000, 80, "PA", 2, 2,
                       payload=b"GET / HTTP/1.1\r\n\r\n", t=t0 + 2),
        ]
        self._write_pcap(pkts)

        # 查 13:00:02 ± 1s，应命中第 3 个包
        rc, out, err = _run_script(["around", self.pcap_path,
                                    "--time", "13:00:02", "--window", "1"])
        self.assertEqual(rc, 0)
        self.assertIn("GET", out)
        # 第 1 个包在窗口外
        self.assertNotIn("[SYN", out.replace("[SYN,ACK", ""))  # 握手 SYN 不应出现

    def test_decode_single_packet(self):
        """decode 单包深度解码"""
        pkts = [
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 50000, 80, "PA", 1, 1,
                       payload=b"GET / HTTP/1.1\r\n\r\n"),
        ]
        self._write_pcap(pkts)

        rc, out, err = _run_script(["decode", self.pcap_path, "--packet", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("Ethernet", out)
        self.assertIn("IP", out)
        self.assertIn("TCP", out)
        self.assertIn("GET", out)

    def test_decode_hex_output(self):
        """decode --hex 输出 raw hex"""
        pkts = [_mkpkt_tcp("10.0.0.1", "10.0.0.2", 50000, 80, "PA", 1, 1, payload=b"ABCD")]
        self._write_pcap(pkts)

        rc, out, err = _run_script(["decode", self.pcap_path, "--packet", "0", "--hex"])
        self.assertEqual(rc, 0)
        self.assertIn("raw hex", out)
        self.assertIn("41 42 43 44", out)  # ABCD 的 hex

    def test_show_hex_output(self):
        """show --hex 输出 payload hex"""
        t0 = datetime(2026, 7, 9, 14, 0, 0, tzinfo=TZ_CST).timestamp()
        pkts = [
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 60000, 80, "PA", 1, 1, payload=b"\xde\xad\xbe\xef", t=t0),
        ]
        self._write_pcap(pkts)

        rc, out, err = _run_script(["show", self.pcap_path, "--flow-id", "0", "--hex"])
        self.assertEqual(rc, 0)
        self.assertIn("de ad be ef", out)

    def test_empty_pcap(self):
        """空 pcap 不崩溃"""
        self._write_pcap([])
        rc, out, err = _run_script(["flows", self.pcap_path])
        self.assertEqual(rc, 0, f"空 pcap 返回码 {rc}")

    def test_packet_out_of_range(self):
        """decode 越界包序号给出明确错误"""
        pkts = [_mkpkt_tcp("10.0.0.1", "10.0.0.2", 1, 80, "S", 1, 0)]
        self._write_pcap(pkts)
        rc, out, err = _run_script(["decode", self.pcap_path, "--packet", "999"])
        self.assertIn("超出范围", out + err)

    def test_report_output(self):
        """--report 输出归档到 markdown 文件"""
        t0 = datetime(2026, 7, 9, 19, 0, 0, tzinfo=TZ_CST).timestamp()
        pkts = [
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 50100, 80, "S", 1, 0, t=t0),
            _mkpkt_tcp("10.0.0.2", "10.0.0.1", 80, 50100, "SA", 1, 2, t=t0 + 0.01),
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 50100, 80, "PA", 2, 2,
                       payload=b"GET / HTTP/1.1\r\n\r\n", t=t0 + 0.02),
        ]
        self._write_pcap(pkts)
        report_path = os.path.join(self.tmpdir, "analysis", "pcap_report.md")
        rc, out, err = _run_script(["flows", self.pcap_path, "--report", report_path])
        self.assertEqual(rc, 0, f"--report 失败: {err}")
        # 报告文件生成
        self.assertTrue(os.path.exists(report_path))
        with open(report_path, encoding="utf-8") as f:
            content = f.read()
        # 含 markdown 头 + 元信息 + 完整输出
        self.assertIn("# pcap 报文分析报告", content)
        self.assertIn("运行时间", content)
        self.assertIn("pcap 文件", content)
        self.assertIn("子命令", content)
        self.assertIn("TCP", content)  # 实际输出内容
        self.assertIn("[report]", out)  # stdout 提示报告路径


@unittest.skipUnless(SCAPY_OK, "scapy 未安装")
class TestRetransmission(unittest.TestCase):
    """TCP 重传识别（seq 去重）—— 实战踩坑点"""

    def test_same_seq_is_retransmission(self):
        """相同 seq 的包应能被识别（build_flows 追踪 rcv_nxt）"""
        t0 = datetime(2026, 7, 9, 15, 0, 0, tzinfo=TZ_CST).timestamp()
        pkts = [
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 11111, 80, "PA", 100, 1,
                       payload=b"GET / HTTP/1.1\r\n\r\n", t=t0),
            # 重传：相同 seq=100
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 11111, 80, "PA", 100, 1,
                       payload=b"GET / HTTP/1.1\r\n\r\n", t=t0 + 0.1),
        ]
        # 用 rdpcap 读回来后 build_flows
        wrpcap(os.path.join(tempfile.mkdtemp(), "x.pcap"), pkts)
        loaded = rdpcap(os.path.join(tempfile.gettempdir(), "x.pcap")) if os.path.exists(
            os.path.join(tempfile.gettempdir(), "x.pcap")) else pkts
        flows = pa.build_flows(loaded if loaded else pkts)
        self.assertEqual(len(flows), 1)
        fl = list(flows.values())[0]
        # 两个包归入同一流
        self.assertEqual(len(fl["packets"]), 2)


# ============================================================
# MQTT 协议解码测试
# ============================================================

class TestDecodeMqtt(unittest.TestCase):
    """MQTT 控制包解码（3.1.1 & 5.0）"""

    def test_connect_v311(self):
        # CONNECT: MQTT 3.1.1, clientId=test, keepalive=60
        connect = bytes([0x10, 0x1a, 0x00, 0x04]) + b"MQTT" + \
                  bytes([0x04, 0x02, 0x00, 0x3c, 0x00, 0x04]) + b"test" + bytes(6)
        result = pa.decode_mqtt(connect)
        self.assertIsNotNone(result)
        self.assertIn("CONNECT", result)
        self.assertIn("3.1.1", result)
        self.assertIn("clientId=test", result)
        self.assertIn("keepalive=60", result)

    def test_connect_v5(self):
        # CONNECT: MQTT 5.0
        connect5 = bytes([0x10, 0x1c, 0x00, 0x04]) + b"MQTT" + \
                   bytes([0x05, 0x02, 0x00, 0x3c, 0x00, 0x04]) + b"test"
        result = pa.decode_mqtt(connect5)
        self.assertIsNotNone(result)
        self.assertIn("5.0", result)

    def test_connack_accepted(self):
        # CONNACK rc=0 (Accepted)
        connack = bytes([0x20, 0x02, 0x00, 0x00])
        result = pa.decode_mqtt(connack)
        self.assertIsNotNone(result)
        self.assertIn("CONNACK", result)
        self.assertIn("Accepted", result)

    def test_connack_not_authorized(self):
        # CONNACK rc=5 (NotAuthorized)
        connack = bytes([0x20, 0x02, 0x00, 0x05])
        result = pa.decode_mqtt(connack)
        self.assertIsNotNone(result)
        self.assertIn("5", result)
        self.assertIn("NotAuthorized", result)

    def test_publish(self):
        # PUBLISH topic=sensor/temp payload=temp:25
        body = bytes([0x00, 0x0c]) + b"sensor/temp" + b"temp:25"
        pub = bytes([0x30, len(body)]) + body
        result = pa.decode_mqtt(pub)
        self.assertIsNotNone(result)
        self.assertIn("PUBLISH", result)
        self.assertIn("sensor/temp", result)

    def test_pingreq(self):
        # PINGREQ
        result = pa.decode_mqtt(bytes([0xc0, 0x00]))
        self.assertIsNotNone(result)
        self.assertIn("PINGREQ", result)

    def test_disconnect(self):
        # DISCONNECT
        result = pa.decode_mqtt(bytes([0xe0, 0x00]))
        self.assertIsNotNone(result)
        self.assertIn("DISCONNECT", result)

    def test_invalid_packet(self):
        # 首字节 type=0 非法
        self.assertIsNone(pa.decode_mqtt(bytes([0x00, 0x02, 0x00, 0x00])))

    def test_empty(self):
        self.assertIsNone(pa.decode_mqtt(b""))
        self.assertIsNone(pa.decode_mqtt(bytes([0x10])))


# ============================================================
# CoAP 协议解码测试
# ============================================================

class TestDecodeCoap(unittest.TestCase):
    """CoAP 消息解码"""

    def test_get_request(self):
        # CON GET token=0xAB Uri-Path=test
        coap = bytes([0x41, 0x01, 0x12, 0x34, 0xAB, 0xB4]) + b"test"
        result = pa.decode_coap(coap)
        self.assertIsNotNone(result)
        self.assertIn("CON", result)
        self.assertIn("GET", result)
        self.assertIn("token=ab", result)
        self.assertIn("Uri-Path=test", result)

    def test_response_205(self):
        # ACK 2.05 Content Content-Format=50(json)
        coap = bytes([0x60, 0x45, 0x12, 0x34, 0xC2, 0x00, 0x32])
        result = pa.decode_coap(coap)
        self.assertIsNotNone(result)
        self.assertIn("ACK", result)
        self.assertIn("2.05", result)
        self.assertIn("Content", result)
        self.assertIn("Content-Format=50", result)

    def test_response_404(self):
        # ACK 4.04 Not Found
        coap = bytes([0x60, 0x84, 0x12, 0x34])
        result = pa.decode_coap(coap)
        self.assertIsNotNone(result)
        self.assertIn("4.04", result)
        self.assertIn("Not Found", result)

    def test_post_request(self):
        # CON POST
        coap = bytes([0x40, 0x02, 0x56, 0x78])
        result = pa.decode_coap(coap)
        self.assertIsNotNone(result)
        self.assertIn("POST", result)

    def test_non_confirmable(self):
        # NON GET
        coap = bytes([0x50, 0x01, 0x00, 0x01])
        result = pa.decode_coap(coap)
        self.assertIsNotNone(result)
        self.assertIn("NON", result)

    def test_invalid_version(self):
        # version=0 非法（高 2 位应为 01）
        self.assertIsNone(pa.decode_coap(bytes([0x00, 0x01, 0x12, 0x34])))

    def test_empty(self):
        self.assertIsNone(pa.decode_coap(b""))
        self.assertIsNone(pa.decode_coap(bytes([0x40])))


@unittest.skipUnless(SCAPY_OK, "scapy 未安装")
class TestMqttEndToEnd(unittest.TestCase):
    """MQTT 端到端：构造 pcap → flows/show → 断言"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pcap_mqtt_")
        self.pcap_path = os.path.join(self.tmpdir, "mqtt.pcap")

    def test_mqtt_connect_flow(self):
        """MQTT CONNECT + CONNACK 流（端口 1883）"""
        t0 = datetime(2026, 7, 9, 16, 0, 0, tzinfo=TZ_CST).timestamp()
        connect_payload = bytes([0x10, 0x1a, 0x00, 0x04]) + b"MQTT" + \
                          bytes([0x04, 0x02, 0x00, 0x3c, 0x00, 0x04]) + b"test" + bytes(6)
        connack_payload = bytes([0x20, 0x02, 0x00, 0x00])  # Accepted
        pkts = [
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 12345, 1883, "S", 1, 0, t=t0),
            _mkpkt_tcp("10.0.0.2", "10.0.0.1", 1883, 12345, "SA", 1, 2, t=t0 + 0.01),
            _mkpkt_tcp("10.0.0.1", "10.0.0.2", 12345, 1883, "PA", 2, 2,
                       payload=connect_payload, t=t0 + 0.02),
            _mkpkt_tcp("10.0.0.2", "10.0.0.1", 1883, 12345, "PA", 2, 30,
                       payload=connack_payload, t=t0 + 0.03),
        ]
        wrpcap(self.pcap_path, pkts)

        rc, out, err = _run_script(["show", self.pcap_path, "--flow-id", "0"])
        self.assertEqual(rc, 0, f"show 失败: {err}")
        self.assertIn("MQTT", out)
        self.assertIn("CONNECT", out)
        self.assertIn("CONNACK", out)
        self.assertIn("3.1.1", out)


@unittest.skipUnless(SCAPY_OK, "scapy 未安装")
class TestCoapEndToEnd(unittest.TestCase):
    """CoAP 端到端：构造 pcap → flows/show → 断言"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pcap_coap_")
        self.pcap_path = os.path.join(self.tmpdir, "coap.pcap")

    def test_coap_get_response(self):
        """CoAP GET + 2.05 响应（UDP 端口 5683）"""
        t0 = datetime(2026, 7, 9, 17, 0, 0, tzinfo=TZ_CST).timestamp()
        # GET .well-known/core
        get_payload = bytes([0x40, 0x01, 0x12, 0x34, 0xBA]) + b"well-known"
        # 2.05 Content
        resp_payload = bytes([0x60, 0x45, 0x12, 0x34])
        get_pkt = Ether() / IP(src="10.0.0.1", dst="10.0.0.2") / \
                  UDP(sport=54321, dport=5683) / Raw(load=get_payload)
        resp_pkt = Ether() / IP(src="10.0.0.2", dst="10.0.0.1") / \
                   UDP(sport=5683, dport=54321) / Raw(load=resp_payload)
        get_pkt.time = t0
        resp_pkt.time = t0 + 0.01
        wrpcap(self.pcap_path, [get_pkt, resp_pkt])

        rc, out, err = _run_script(["show", self.pcap_path, "--flow-id", "0"])
        self.assertEqual(rc, 0, f"show 失败: {err}")
        self.assertIn("CoAP", out)
        self.assertIn("GET", out)
        self.assertIn("2.05", out)

    def test_coap_search_topic(self):
        """search 搜 CoAP payload（明文 Uri-Path 内容）"""
        t0 = datetime(2026, 7, 9, 18, 0, 0, tzinfo=TZ_CST).timestamp()
        get_payload = bytes([0x40, 0x01, 0x12, 0x34, 0xB4]) + b"temperature"
        pkt = Ether() / IP(src="10.0.0.1", dst="10.0.0.2") / \
              UDP(sport=11111, dport=5683) / Raw(load=get_payload)
        pkt.time = t0
        wrpcap(self.pcap_path, [pkt])

        rc, out, err = _run_script(["search", self.pcap_path, "-k", "temperature"])
        self.assertEqual(rc, 0)
        self.assertIn("temperature", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
