"""
端到端检索测试 —— 验证 LLM-Wiki 三层渐进加载的检索有效性。

4 类测试：
  T1: 链接可达性（所有 markdown link + wikilink 解析成功）
  T2: 关键词召回（模拟 agent 第一层读 INDEX，对一组真实用户提问能否召回正确条目）
  T3: 路径完整性（concept → entry → raw 三层跳转路径正确）
  T4: 信息完备性（抽样 entry 含必要四要素）

运行：python test_e2e_retrieval.py
"""
import re
import sys
import random
from pathlib import Path

WIKI_ROOT = Path(__file__).resolve().parent.parent.parent.parent / 'knowledge' / 'wiki'


# ── T1: 链接可达性 ─────────────────────────────────────────────────────────

def test_link_reachability():
    all_md = [p.relative_to(WIKI_ROOT).as_posix() for p in WIKI_ROOT.rglob('*.md')]
    issues = []
    md_link_count = 0
    wikilink_count = 0

    for md_file in all_md:
        fp = WIKI_ROOT / md_file
        content = fp.read_text(encoding='utf-8')

        for m in re.finditer(r'(?<!!)\[([^\]]*)\]\(([^)]+)\)', content):
            link = m.group(2).split('#')[0]
            if not link or link.startswith(('http', 'mailto:')):
                continue
            md_link_count += 1
            target = (fp.parent / link).resolve()
            if not target.exists():
                issues.append(f'broken-md-link: {md_file} -> {link}')

        for m in re.finditer(r'\[\[([^\]]+?)\]\]', content):
            target = m.group(1).split('|')[0].strip()
            wikilink_count += 1
            found = any(
                target == wm or target == wm[:-3] or
                target + '.md' == wm or target + '.md' == Path(wm).name
                for wm in all_md
            )
            if not found:
                issues.append(f'broken-wiki-link: {md_file} -> [[{target}]]')

    return {
        'name': 'T1 链接可达性',
        'passed': len(issues) == 0,
        'stats': {'md_links': md_link_count, 'wikilinks': wikilink_count},
        'issues': issues,
    }


# ── T2: 关键词召回 ─────────────────────────────────────────────────────────

# (用户提问, 期望命中的 entry stem, 期望命中的 concept stem)
RECALL_CASES = [
    ("EC626 上 MQTT SSL 双向认证时模组死机怎么排查",
     ['EC626_MQTT_SSL双向认证内存分配崩溃', 'EC626_MQTT_SSL双向认证连接失败'], ['MQTT连接失败']),
    ("EC626 多个 TCP 连接同时存在时执行 XIIC=0 模组死机",
     ['EC626_TCP连接XIIC0去激活死机', 'EC626_ipv6_udp_ppp_crash'], ['XIIC去激活三方死锁']),
    ("UIS8852 FTP 上传大文件死机，dump 显示 timer 任务栈溢出",
     ['UIS8852_7040737599_706C印度卡FTPPUT大文件死机'],
     ['栈溢出与heap_corruption', 'FTP_FOTA大文件死机与压测ERROR']),
    ("EC626 长期挂测后死机，LWIP 内存池耗尽",
     ['EC626_6891620297_长期挂测挂测900多次出现死机'], ['FreeRTOS_heap_OOM_容错与缓冲峰值']),
    ("ASR1603 上 MQTTPUBS 发布 8K 数据经常超时",
     ['ASR1603_7033314470_MQTTPUBS超时与8K发包耗时差异'],
     ['bypass数据模式机制', 'MQTT连接失败']),
    ("EC626 AT+CPSMS 回码和 BC28 不一致",
     ['EC626_7048142056_CPSMS回码与BC28不符'], ['AT回码BC28兼容与重构回归']),
    ("EC626 设置 DNS server 无效地址还返回 OK",
     ['EC626_DNSSERVER无效地址校验缺失'], ['参数校验缺失与编码缺陷']),
    ("UIS8850 NWFTPFOTA 压测有时返回 ERROR",
     ['UIS8850_7051224569_NWFTPFOTA压测ERROR'], ['FTP_FOTA大文件死机与压测ERROR']),
    ("RDA UIS8910 SSLTCPRECV 接收偶尔丢数据",
     ['RDA_UIS8910DM_RLS_6979103193_SSLTCPRECV接收上报丢失'], ['SSL_TLS读取真粘包']),
    ("EC626 FOTA 升级时固定 9600 波特率收不到进度上报",
     ['EC626_6201246834_FOTA固定9600无进度上报'], []),
    ("EC626 AT+COAPOPTION 设置端口号返回 ERROR",
     ['EC626_CoAPOPTION返回ERROR'], ['参数校验缺失与编码缺陷']),
    ("EC626 CoAP 协议持续 GET 模组死机",
     ['EC626_4975090277_COAP协议持续GET操作时模组死机'], []),
    ("EC626 NCDP 回码格式异常",
     ['EC626_7048272604_NCDP回码冒号后多空格'], ['AT回码BC28兼容与重构回归']),
    ("EC626 AT+DNSSERVER 设置 dns2 写到 dns1 了",
     ['EC626_DNSSERVER设置dns2实际写入dns1'], ['参数校验缺失与编码缺陷']),
    ("UIS8850 ftp fota 时 sig_led 任务栈溢出",
     ['UIS8850_8850_ftp_fota_sig_led_stack_overflow'],
     ['栈溢出与heap_corruption', 'FTP_FOTA大文件死机与压测ERROR']),
]

STOP_WORDS = {
    'EC626', 'ASR1603', 'UIS8850', 'UIS8852', 'RDA', 'UIS8910', 'UIS8852',
    '上', '时', '怎么', '排查', '有时', '经常', '偶尔', '问题', '返回',
    '显示', '收到', '死机', 'error', 'fail', '一下', '哪些', '案例',
    '模组', '功能', '了', '异常', '后', '到', '的', '还', '和', '与', '是',
}


def _tokenize(s):
    tokens = set()
    for m in re.finditer(r'[A-Za-z0-9_]+', s):
        tokens.add(m.group(0).lower())
    for m in re.finditer(r'[\u4e00-\u9fff]+', s):
        tokens.add(m.group(0))
        for ch in m.group(0):
            tokens.add(ch)
    return tokens


def _search_index(query, line_stems, top_k=10):
    qt = _tokenize(query) - STOP_WORDS
    qt = {t for t in qt if len(t) > 1 or not re.match(r'[\u4e00-\u9fff]', t)}
    scored = []
    for line, stems in line_stems:
        lt = _tokenize(line)
        overlap = qt & lt
        if overlap:
            scored.append((len(overlap), stems))
    scored.sort(key=lambda x: -x[0])
    result = set()
    for _, stems in scored[:top_k]:
        result.update(stems)
    return result


def test_keyword_recall():
    index_text = (WIKI_ROOT / 'INDEX.md').read_text(encoding='utf-8')
    line_stems = []
    for line in index_text.split('\n'):
        stems = {m.group(1).strip() for m in re.finditer(r'\[([^\]]+)\]\(', line)}
        if stems:
            line_stems.append((line, stems))

    failures = []
    pass_count = 0
    for query, exp_entries, exp_concepts in RECALL_CASES:
        found = _search_index(query, line_stems)
        entry_miss = []
        for e in exp_entries:
            ekey = e.split('_', 2)[-1] if '_' in e else e
            if not any(e in s or ekey in s or s in e for s in found):
                entry_miss.append(e)
        concept_miss = []
        for c in exp_concepts:
            cn = c.replace('_', '')
            if not any(cn in s.replace('_', '').replace(' ', '') for s in found):
                concept_miss.append(c)
        if not entry_miss and not concept_miss:
            pass_count += 1
        else:
            failures.append({
                'query': query, 'entry_miss': entry_miss, 'concept_miss': concept_miss,
            })

    return {
        'name': 'T2 关键词召回',
        'passed': pass_count == len(RECALL_CASES),
        'stats': {'pass': pass_count, 'total': len(RECALL_CASES),
                  'rate': f'{100 * pass_count / len(RECALL_CASES):.0f}%'},
        'issues': failures,
    }


# ── T3: 路径完整性 ─────────────────────────────────────────────────────────

def test_path_integrity():
    all_md_stems = {p.stem for p in WIKI_ROOT.rglob('*.md')}
    issues = []
    concept_count = 0
    raw_link_count = 0

    for cf in sorted((WIKI_ROOT / 'concepts').glob('*.md')):
        content = cf.read_text(encoding='utf-8')
        for m in re.finditer(r'\[\[([^\]]+?)\]\]', content):
            target = m.group(1).split('|')[0].strip()
            concept_count += 1
            if target not in all_md_stems:
                cands = [s for s in all_md_stems
                         if target.replace('_', '').replace(' ', '') in
                         s.replace('_', '').replace(' ', '')]
                if not cands:
                    issues.append(f'concept broken wikilink: {cf.name} -> [[{target}]]')

    for ef in sorted((WIKI_ROOT / 'entries' / 'bug-solutions').glob('*.md')):
        content = ef.read_text(encoding='utf-8')
        for m in re.finditer(r'\]\((\.\./\.\./\.\./raw/[^)]+)\)', content):
            link = m.group(1).split('#')[0]
            raw_link_count += 1
            target = (ef.parent / link).resolve()
            if not target.exists():
                issues.append(f'entry raw broken: {ef.name} -> {link}')

    return {
        'name': 'T3 路径完整性',
        'passed': len(issues) == 0,
        'stats': {'concept_links': concept_count, 'raw_links': raw_link_count},
        'issues': issues,
    }


# ── T4: 信息完备性抽检 ────────────────────────────────────────────────────

def test_entry_completeness(sample_size=10):
    files = sorted((WIKI_ROOT / 'entries' / 'bug-solutions').glob('*.md'))
    random.seed(42)
    sample = random.sample(files, min(sample_size, len(files)))
    issues = []

    required = ['title:', 'date:', 'tags:', 'type: entry', 'platform:',
                'module:', '## 一句话根因', '## 调用链摘要',
                '## 关键证据', '../raw/platform/']
    for fp in sample:
        text = fp.read_text(encoding='utf-8')
        if not re.match(r'^---\n.*?\n---\n', text, re.S):
            issues.append(f'{fp.name}: 缺 frontmatter')
            continue
        missing = [r for r in required if r not in text]
        if missing:
            issues.append(f'{fp.name}: 缺 {missing}')

    return {
        'name': 'T4 信息完备性',
        'passed': len(issues) == 0,
        'stats': {'sampled': len(sample)},
        'issues': issues,
    }


# ── 主入口 ────────────────────────────────────────────────────────────────

def main():
    print('=' * 70)
    print('端到端检索测试（LLM-Wiki 三层渐进加载有效性）')
    print(f'wiki: {WIKI_ROOT}')
    print('=' * 70)
    print()

    tests = [
        test_link_reachability(),
        test_keyword_recall(),
        test_path_integrity(),
        test_entry_completeness(),
    ]

    all_pass = True
    for t in tests:
        mark = '✓' if t['passed'] else '✗'
        print(f"{mark} {t['name']}")
        if t['stats']:
            for k, v in t['stats'].items():
                print(f'    {k}: {v}')
        if t['issues']:
            all_pass = False
            for i in t['issues'][:8]:
                if isinstance(i, dict):
                    print(f'    - Q: {i["query"]}')
                    if i.get('entry_miss'):
                        print(f'        entry 漏召: {i["entry_miss"]}')
                    if i.get('concept_miss'):
                        print(f'        concept 漏召: {i["concept_miss"]}')
                else:
                    print(f'    - {i}')
        print()

    print('=' * 70)
    if all_pass:
        print('RESULT: 全部通过 ✓')
        return 0
    else:
        failed = [t['name'] for t in tests if not t['passed']]
        print(f'RESULT: {len(failed)} 项失败 ✗ ({", ".join(failed)})')
        return 1


if __name__ == '__main__':
    sys.exit(main())
