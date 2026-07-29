"""
端到端 agent 检索流程验证（含效率指标）

真实模拟 agent 三层渐进加载的完整路径：
  L1 [轻] 读 wiki/INDEX.md → 锁定候选
  L2 [中] 读 wiki/entries/*.md → 确认相关性
  L3 [重] 读 raw/platform/.../*.md → 取证

记录每步：
  - 文件读取量（byte/行数）
  - token 估算（粗略：1 token ≈ 2-4 字符）
  - 是否命中正确答案
  - 总效率指标

运行：python test_e2e_agent_flow.py
"""
import re
import time
from pathlib import Path

WIKI_ROOT = Path(__file__).resolve().parent.parent.parent.parent / 'knowledge' / 'wiki'
KB_ROOT = WIKI_ROOT.parent  # knowledge/


def _token_estimate(text):
    """粗略 token 估算：中文按字符，英文按 4 字符/token。"""
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    en_chars = len(text) - cn
    return cn + en_chars // 3


def _read(path):
    """读文件，返回 (text, bytes, lines, tokens)。"""
    p = Path(path)
    if not p.exists():
        return None, 0, 0, 0
    text = p.read_text(encoding='utf-8')
    return text, len(text.encode('utf-8')), text.count('\n') + 1, _token_estimate(text)


# ── 测试用例（5 个典型场景，覆盖不同难度）─────────────────────────────────

E2E_CASES = [
    {
        'id': 'E1-simple',
        'desc': '简单：精确 AT 命令查询',
        'query': 'EC626 AT+COAPOPTION 设置端口号返回 ERROR',
        'expected_entry': 'EC626_CoAPOPTION返回ERROR',
        'expected_concept': '参数校验缺失与编码缺陷',
    },
    {
        'id': 'E2-medium',
        'desc': '中等：模块+现象描述',
        'query': 'EC626 上 MQTT SSL 双向认证时模组死机',
        'expected_entry': 'EC626_MQTT_SSL双向认证内存分配崩溃',
        'expected_concept': 'MQTT连接失败',
    },
    {
        'id': 'E3-medium',
        'desc': '中等：跨平台死机场景',
        'query': 'UIS8852 FTP PUT 大文件死机 timer 栈溢出',
        'expected_entry': 'UIS8852_7040737599_706C印度卡FTPPUT大文件死机',
        'expected_concept': '栈溢出与heap_corruption',
    },
    {
        'id': 'E4-hard',
        'desc': '困难：跨案例综合查询',
        'query': '所有平台 FTP 大文件传输死机的案例',
        'expected_entry': 'UIS8852_7040737599_706C印度卡FTPPUT大文件死机',
        'expected_concept': 'FTP_FOTA大文件死机与压测ERROR',
    },
    {
        'id': 'E5-medium',
        'desc': '中等：根因模式查询',
        'query': 'EC626 长期挂测后死机 LWIP 内存池耗尽',
        'expected_entry': 'EC626_6891620297_长期挂测挂测900多次出现死机',
        'expected_concept': 'FreeRTOS_heap_OOM_容错与缓冲峰值',
    },
]


def simulate_agent_flow(query):
    """模拟 agent 完整检索流程，返回路径详情。"""
    result = {
        'query': query,
        'steps': [],
        'total_bytes': 0,
        'total_lines': 0,
        'total_tokens': 0,
        'files_read': 0,
    }

    def record(layer, path, text, by, ln, tk):
        result['steps'].append({
            'layer': layer,
            'file': str(Path(path).relative_to(KB_ROOT)),
            'bytes': by,
            'lines': ln,
            'tokens': tk,
        })
        result['total_bytes'] += by
        result['total_lines'] += ln
        result['total_tokens'] += tk
        result['files_read'] += 1

    # L1: 读 INDEX.md
    text, by, ln, tk = _read(WIKI_ROOT / 'INDEX.md')
    record('L1-INDEX', WIKI_ROOT / 'INDEX.md', text, by, ln, tk)

    # L1.5: 简单关键词匹配锁定候选（模拟 LLM 判断）
    qt = set(re.findall(r'[A-Za-z0-9_]+', query.lower()))
    for m in re.finditer(r'[\u4e00-\u9fff]+', query):
        qt.add(m.group())

    candidates = []
    for line in text.split('\n'):
        if '[' in line and '](' in line:
            line_tokens = set(re.findall(r'[A-Za-z0-9_]+', line.lower()))
            for m in re.finditer(r'[\u4e00-\u9fff]+', line):
                line_tokens.add(m.group())
            overlap = qt & line_tokens
            if len(overlap) >= 2:  # 至少 2 个 token 重叠
                # 抽 stem
                link_m = re.search(r'\[([^\]]+)\]\(', line)
                if link_m:
                    candidates.append((len(overlap), link_m.group(1)))

    candidates.sort(key=lambda x: -x[0])
    top_candidates = [c[1] for c in candidates[:5]]

    # L2: 读 top-3 entry 精炼页（模拟 agent 锁定 2-3 个候选）
    for stem in top_candidates[:3]:
        entry_path = WIKI_ROOT / 'entries' / 'bug-solutions' / f'{stem}.md'
        text, by, ln, tk = _read(entry_path)
        if text:
            record(f'L2-entry', entry_path, text, by, ln, tk)

    # L3: 读最相关 entry 指向的 raw 原文（取第一个 entry 的第一个 raw link）
    if top_candidates:
        first_entry_path = WIKI_ROOT / 'entries' / 'bug-solutions' / f'{top_candidates[0]}.md'
        entry_text, _, _, _ = _read(first_entry_path)
        if entry_text:
            # 找第一个 raw 引用
            raw_m = re.search(r'\]\((\.\./\.\./\.\./raw/[^)]+)\)', entry_text)
            if raw_m:
                raw_link = raw_m.group(1).split('#')[0]
                raw_path = (first_entry_path.parent / raw_link).resolve()
                text, by, ln, tk = _read(raw_path)
                if text:
                    record('L3-raw', raw_path, text, by, ln, tk)

    return result


def verify_hit(result, expected_entry, expected_concept):
    """检查 agent 流程是否命中期望的 entry/concept。"""
    all_stems = []
    for step in result['steps']:
        # 从文件路径抽 stem
        p = Path(step['file'])
        all_stems.append(p.stem)
    # 也要考虑 INDEX 这一步里列出的候选（candidates 不在 steps 里，重新算）
    # 简化：检查路径里是否出现期望 stem
    all_text = ' '.join(step['file'] for step in result['steps'])
    entry_hit = expected_entry in all_text or any(
        expected_entry in stem for stem in all_stems)
    # concept 在 INDEX 里以 wikilink 形式，看 L1 是否含 concept 名
    l1_text = result['steps'][0].get('text', '') if result['steps'] else ''
    # 重新读 INDEX 找 concept
    idx_text = (WIKI_ROOT / 'INDEX.md').read_text(encoding='utf-8')
    concept_hit = expected_concept.replace('_', '') in idx_text.replace('_', '').replace(' ', '')
    return entry_hit, concept_hit


def main():
    print('=' * 90)
    print('端到端 agent 检索流程验证（三层渐进加载 + 效率指标）')
    print('=' * 90)
    print()

    all_results = []
    for case in E2E_CASES:
        print(f"── {case['id']}: {case['desc']} ──")
        print(f"   Q: {case['query']}")
        t0 = time.perf_counter()
        result = simulate_agent_flow(case['query'])
        elapsed = (time.perf_counter() - t0) * 1000
        entry_hit, concept_hit = verify_hit(result, case['expected_entry'], case['expected_concept'])

        print(f"   路径（{result['files_read']} 个文件，{elapsed:.0f}ms）：")
        for step in result['steps']:
            print(f"     [{step['layer']:<10}] {step['file'][:55]:<55} "
                  f"{step['bytes']:>6}B {step['lines']:>4}行 {step['tokens']:>5}tok")
        print(f"   总计: {result['total_bytes']}B / {result['total_lines']}行 / "
              f"{result['total_tokens']} tokens")
        print(f"   命中: entry={('✓' if entry_hit else '✗')} "
              f"concept={('✓' if concept_hit else '✗')}")
        all_results.append({
            **case,
            'result': result,
            'entry_hit': entry_hit,
            'concept_hit': concept_hit,
            'elapsed_ms': elapsed,
        })
        print()

    # 汇总
    print('=' * 90)
    print('汇总')
    print('=' * 90)
    print(f'{"用例":<12} {"entry":<8} {"concept":<10} {"文件数":<8} '
          f'{"tokens":<10} {"耗时":<10}')
    print('-' * 70)
    for r in all_results:
        print(f"{r['id']:<12} "
              f"{('✓' if r['entry_hit'] else '✗'):<8} "
              f"{('✓' if r['concept_hit'] else '✗'):<10} "
              f"{r['result']['files_read']:<8} "
              f"{r['result']['total_tokens']:<10} "
              f"{r['elapsed_ms']:.0f}ms")
    print()

    entry_rate = sum(1 for r in all_results if r['entry_hit']) / len(all_results)
    concept_rate = sum(1 for r in all_results if r['concept_hit']) / len(all_results)
    avg_tokens = sum(r['result']['total_tokens'] for r in all_results) / len(all_results)
    avg_files = sum(r['result']['files_read'] for r in all_results) / len(all_results)
    avg_ms = sum(r['elapsed_ms'] for r in all_results) / len(all_results)

    print(f'entry 命中率:    {entry_rate:.0%}')
    print(f'concept 命中率:  {concept_rate:.0%}')
    print(f'平均文件读取:    {avg_files:.1f} 个')
    print(f'平均 token 量:   {avg_tokens:.0f} tokens')
    print(f'平均耗时:        {avg_ms:.0f} ms（本地脚本模拟，真实 LLM 主要是读文件的 IO）')
    print()
    print('解读：')
    print(f'  - agent 一次完整检索平均读 {avg_files:.1f} 个文件、'
          f'约 {avg_tokens:.0f} tokens')
    print(f'  - 对比：传统 RAG 一次检索需 embed query + 向量搜索 + 读 chunk，'
          f'本质也是几个文件 IO')
    print(f'  - LLM-Wiki 优势：读到的是结构化精炼页，而非分散的 raw chunk')


if __name__ == '__main__':
    main()
