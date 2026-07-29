"""
LLM-Wiki 检索准确率评估（IR 标准指标）

评估对象：~/.spec-embedded-iot/knowledge/wiki 的检索有效性
指标：Recall@K / Precision@K / MRR / Success@K / MAP

两个 baseline：
  B1 关键词重叠（机械）：用 Jaccard/TF 重叠排序，作为下界
  B2 LLM-as-judge（真实）：读 INDEX 候选 + 读 entry 内容判断相关性

金标准：30 条 query，每条手工标注"相关条目集合"（ground truth）。
分 3 类 query 难度：
  - 简单（带 WID/具体 AT 命令名）：单条目精确匹配
  - 中等（带模块/现象描述）：1-3 条目
  - 困难（跨平台/概念性）：3-8 条目，需 concept 介入

运行：python eval_retrieval.py
可选：python eval_retrieval.py --use-llm  （跑 B2 LLM-as-judge，需 API）
"""
import re
import sys
import json
import math
import argparse
from pathlib import Path
from collections import Counter

WIKI_ROOT = Path(__file__).resolve().parent.parent.parent.parent / 'knowledge' / 'wiki'


# ──────────────────────────────────────────────────────────────────────────
# 金标准评估集（30 条 query，覆盖 53 个条目中的 47 个 + 9 个 concept）
# 每条标注：相关 entry stem 列表 + 相关 concept stem 列表
# 标注原则：用户提这个问题时，agent 最终应该读到哪些条目才能给出完整答案
# ──────────────────────────────────────────────────────────────────────────

GROUND_TRUTH = [
    # ── 简单类（10 条）：具体 AT 命令、WID、明确单点问题 ──
    {
        'query': 'EC626 AT+CPSMS 回码和 BC28 不一致',
        'rel_entries': ['EC626_7048142056_CPSMS回码与BC28不符'],
        'rel_concepts': ['AT回码BC28兼容与重构回归'],
        'category': 'simple',
    },
    {
        'query': 'EC626 AT+NCDP 回码冒号后多了一个空格',
        'rel_entries': ['EC626_7048272604_NCDP回码冒号后多空格'],
        'rel_concepts': ['AT回码BC28兼容与重构回归'],
        'category': 'simple',
    },
    {
        'query': 'EC626 AT+COAPOPTION 设置端口号返回 ERROR',
        'rel_entries': ['EC626_CoAPOPTION返回ERROR'],
        'rel_concepts': ['参数校验缺失与编码缺陷'],
        'category': 'simple',
    },
    {
        'query': 'EC626 AT+DNSSERVER 设置 dns2 实际写到了 dns1',
        'rel_entries': ['EC626_DNSSERVER设置dns2实际写入dns1'],
        'rel_concepts': ['参数校验缺失与编码缺陷'],
        'category': 'simple',
    },
    {
        'query': 'EC626 AT+HTTPSEND 只能发送 512 字节 hex 数据',
        'rel_entries': ['EC626_6729240465_HTTPSEND只支持发送512字节hex数据'],
        'rel_concepts': [],
        'category': 'simple',
    },
    {
        'query': 'EC626 AT+TCPLISTEN 返回 ERROR 不能监听',
        'rel_entries': ['EC626_6284937120_TCPServer_TCPLISTEN返回ERROR'],
        'rel_concepts': [],
        'category': 'simple',
    },
    {
        'query': 'UIS8850 PBREADY2 上报不出来',
        'rel_entries': ['UIS8850_7048275809_PBREADY2未上报'],
        'rel_concepts': [],
        'category': 'simple',
    },
    {
        'query': 'ASR1603 ATI 和 GMR 显示的版本号不一样',
        'rel_entries': ['ASR1603_7032861939_ATI与GMR版本号不一致'],
        'rel_concepts': [],
        'category': 'simple',
    },
    {
        'query': 'RDA UIS8910 多条 AT 命令响应后有多余 ERROR',
        'rel_entries': ['RDA_UIS8910DM_RLS_7024342099_命令响应后多余ERROR'],
        'rel_concepts': [],
        'category': 'simple',
    },
    {
        'query': 'EC626 AT+NWBLEPSTR 返回 ERROR 配不出 BLE 服务',
        'rel_entries': ['EC626_NWblePSTR返回ERROR'],
        'rel_concepts': [],
        'category': 'simple',
    },

    # ── 中等类（12 条）：带模块名/现象描述，1-3 条目 ──
    {
        'query': 'EC626 上 MQTT SSL 双向认证时模组死机怎么排查',
        'rel_entries': ['EC626_MQTT_SSL双向认证内存分配崩溃',
                        'EC626_MQTT_SSL双向认证连接失败',
                        'EC626_MQTT_SSL连接成功但MQTTConnect失败'],
        'rel_concepts': ['MQTT连接失败', 'FreeRTOS_heap_OOM_容错与缓冲峰值'],
        'category': 'medium',
    },
    {
        'query': 'EC626 多个 TCP 连接存在时执行 XIIC=0 死机',
        'rel_entries': ['EC626_TCP连接XIIC0去激活死机',
                        'EC626_6973174788_TCP连接后xiic0去激活概率性死机'],
        'rel_concepts': ['XIIC去激活三方死锁'],
        'category': 'medium',
    },
    {
        'query': 'EC626 长期挂测后死机，dump 显示 LWIP 内存池耗尽',
        'rel_entries': ['EC626_6891620297_长期挂测挂测900多次出现死机'],
        'rel_concepts': ['FreeRTOS_heap_OOM_容错与缓冲峰值'],
        'category': 'medium',
    },
    {
        'query': 'ASR1603 上 MQTTPUBS 发布 8K 数据经常 Timeout',
        'rel_entries': ['ASR1603_7033314470_MQTTPUBS超时与8K发包耗时差异'],
        'rel_concepts': ['bypass数据模式机制', 'MQTT连接失败'],
        'category': 'medium',
    },
    {
        'query': 'EC626 设置错误 DNS 地址但还能用域名建 TCP 连接',
        'rel_entries': ['EC626_7043649062_设置错误DNS仍能域名建链TCP'],
        'rel_concepts': [],
        'category': 'medium',
    },
    {
        'query': 'EC626 AT+COPS=2 后 UDP socket 没关闭',
        'rel_entries': ['EC626_6974267507_UDP链路未关闭'],
        'rel_concepts': ['XIIC去激活三方死锁'],
        'category': 'medium',
    },
    {
        'query': 'UIS8850 FTP FOTA 期间 sig_led 任务栈溢出死机',
        'rel_entries': ['UIS8850_8850_ftp_fota_sig_led_stack_overflow'],
        'rel_concepts': ['栈溢出与heap_corruption', 'FTP_FOTA大文件死机与压测ERROR'],
        'category': 'medium',
    },
    {
        'query': 'UIS8852 HTTPS FOTA 压测死机',
        'rel_entries': ['UIS8852_7046941811_https-fota死机'],
        'rel_concepts': ['栈溢出与heap_corruption', 'FTP_FOTA大文件死机与压测ERROR'],
        'category': 'medium',
    },
    {
        'query': 'RDA UIS8910 SSLTCPRECV 接收偶尔丢数据',
        'rel_entries': ['RDA_UIS8910DM_RLS_6979103193_SSLTCPRECV接收上报丢失'],
        'rel_concepts': ['SSL_TLS读取真粘包'],
        'category': 'medium',
    },
    {
        'query': 'EC626 TCP UDP 单包发 4096 死机',
        'rel_entries': ['EC626_6285098033_TCP_UDP发送4096数据死机'],
        'rel_concepts': ['FreeRTOS_heap_OOM_容错与缓冲峰值'],
        'category': 'medium',
    },
    {
        'query': 'EC626 引入 ctwing 后开机不自动注册 LWM2M 了',
        'rel_entries': ['EC626_7020676586引入ctwing开机不自动注册回归'],
        'rel_concepts': ['AT回码BC28兼容与重构回归'],
        'category': 'medium',
    },
    {
        'query': 'EC626 LWM2M 加密连接 REGISTER TIMEOUT',
        'rel_entries': ['EC626_LWM2M加密连接REGISTER_TIMEOUT'],
        'rel_concepts': [],
        'category': 'medium',
    },

    # ── 困难类（8 条）：跨平台/概念性，需 concept 综合 ──
    {
        'query': '所有平台 FTP 大文件传输死机的案例有哪些',
        'rel_entries': ['UIS8852_7040737599_706C印度卡FTPPUT大文件死机',
                        'UIS8852_7046941811_https-fota死机',
                        'UIS8852_timer_stack_overflow_heap_corruption',
                        'UIS8850_8850_ftp_fota_sig_led_stack_overflow',
                        'UIS8852_7052653202_波特率19200_FTP透传大文件上传死机'],
        'rel_concepts': ['FTP_FOTA大文件死机与压测ERROR', '栈溢出与heap_corruption'],
        'category': 'hard',
    },
    {
        'query': 'FreeRTOS heap 不足导致死机的根因模式',
        'rel_entries': ['EC626_6285098033_TCP_UDP发送4096数据死机',
                        'EC626_6891620297_长期挂测挂测900多次出现死机',
                        'EC626_7045253863_下载大文件后上传死机',
                        'EC626_MQTT_SSL双向认证内存分配崩溃'],
        'rel_concepts': ['FreeRTOS_heap_OOM_容错与缓冲峰值'],
        'category': 'hard',
    },
    {
        'query': '小栈任务做重活导致栈溢出踩坏堆的案例',
        'rel_entries': ['UIS8852_7040737599_706C印度卡FTPPUT大文件死机',
                        'UIS8852_7046941811_https-fota死机',
                        'UIS8852_timer_stack_overflow_heap_corruption',
                        'UIS8850_8850_ftp_fota_sig_led_stack_overflow'],
        'rel_concepts': ['栈溢出与heap_corruption'],
        'category': 'hard',
    },
    {
        'query': 'EC626 BC28 兼容回码问题汇总',
        'rel_entries': ['EC626_7048142056_CPSMS回码与BC28不符',
                        'EC626_7048272604_NCDP回码冒号后多空格',
                        'EC626_7035794978_NMSTATUS回码与BC28不一致'],
        'rel_concepts': ['AT回码BC28兼容与重构回归'],
        'category': 'hard',
    },
    {
        'query': 'FTP 服务器响应慢导致压测 ERROR 的案例',
        'rel_entries': ['UIS8850_7051224569_NWFTPFOTA压测ERROR',
                        'UIS8852_7051920161_FOTA压测发送升级指令返回ERROR',
                        'ASR1603_7042087077_FOTA下载FAIL回码NEODOWNLODD拼写错误'],
        'rel_concepts': ['FTP_FOTA大文件死机与压测ERROR'],
        'category': 'hard',
    },
    {
        'query': 'AT 命令参数校验缺失导致用户输入非法值引发异常的案例',
        'rel_entries': ['EC626_DNSSERVER无效地址校验缺失',
                        'EC626_DNSSERVER设置dns2实际写入dns1',
                        'EC626_CoAPOPTION返回ERROR',
                        'ASR1603_7018786802_UDP_V6源地址上报错误'],
        'rel_concepts': ['参数校验缺失与编码缺陷'],
        'category': 'hard',
    },
    {
        'query': 'EC626 XIIC 去激活/socket 关闭相关的一系列死机',
        'rel_entries': ['EC626_TCP连接XIIC0去激活死机',
                        'EC626_6973174788_TCP连接后xiic0去激活概率性死机',
                        'EC626_ipv6_udp_ppp_crash',
                        'EC626_CeuTask_ASSERT_PsifSuspendInd',
                        'EC626_6974267507_UDP链路未关闭'],
        'rel_concepts': ['XIIC去激活三方死锁'],
        'category': 'hard',
    },
    {
        'query': 'ASR1603 AT 口数据通路（bypass 模式）相关的 bug',
        'rel_entries': ['ASR1603_7015499106_TCP压力测试未收到提示符',
                        'ASR1603_7033314470_MQTTPUBS超时与8K发包耗时差异'],
        'rel_concepts': ['bypass数据模式机制'],
        'category': 'hard',
    },
]


# ──────────────────────────────────────────────────────────────────────────
# IR 指标实现
# ──────────────────────────────────────────────────────────────────────────

def recall_at_k(retrieved, relevant, k):
    """Recall@K: top-K 中相关条目数 / 全部相关条目数"""
    if not relevant:
        return 1.0
    top_k = retrieved[:k]
    hits = sum(1 for r in top_k if r in relevant)
    return hits / len(relevant)


def precision_at_k(retrieved, relevant, k):
    """Precision@K: top-K 中相关条目数 / K"""
    top_k = retrieved[:k]
    hits = sum(1 for r in top_k if r in relevant)
    return hits / k


def mrr(retrieved, relevant):
    """MRR: 第一个相关条目排名的倒数"""
    for i, r in enumerate(retrieved, 1):
        if r in relevant:
            return 1.0 / i
    return 0.0


def success_at_k(retrieved, relevant, k):
    """Success@K: top-K 中是否至少有 1 个相关"""
    return 1.0 if any(r in relevant for r in retrieved[:k]) else 0.0


def average_precision(retrieved, relevant):
    """AP: 单查询的平均精度（用于 MAP）"""
    if not relevant:
        return 1.0
    hits = 0
    sum_prec = 0.0
    for i, r in enumerate(retrieved, 1):
        if r in relevant:
            hits += 1
            sum_prec += hits / i
    return sum_prec / len(relevant)


def evaluate(retrieved_per_query, gt_per_query, k_values=(1, 3, 5, 10)):
    """对一组查询统一计算指标。"""
    results = {}
    for k in k_values:
        results[f'recall@{k}'] = []
        results[f'precision@{k}'] = []
        results[f'success@{k}'] = []
    results['mrr'] = []
    results['ap'] = []

    for retrieved, gt in zip(retrieved_per_query, gt_per_query):
        relevant = set(gt)
        for k in k_values:
            results[f'recall@{k}'].append(recall_at_k(retrieved, relevant, k))
            results[f'precision@{k}'].append(precision_at_k(retrieved, relevant, k))
            results[f'success@{k}'].append(success_at_k(retrieved, relevant, k))
        results['mrr'].append(mrr(retrieved, relevant))
        results['ap'].append(average_precision(retrieved, relevant))

    summary = {k: (sum(v) / len(v) if v else 0.0) for k, v in results.items()}
    return summary, results


# ──────────────────────────────────────────────────────────────────────────
# Baseline B1: 关键词重叠（机械排序）
# ──────────────────────────────────────────────────────────────────────────

STOP_WORDS = {
    'EC626', 'ASR1603', 'UIS8850', 'UIS8852', 'RDA', 'UIS8910', 'UIS8852', 'N58',
    '上', '时', '怎么', '排查', '有时', '经常', '偶尔', '问题', '返回',
    '显示', '收到', '死机', 'error', 'fail', '一下', '哪些', '案例',
    '模组', '功能', '了', '异常', '后', '到', '的', '还', '和', '与', '是',
    '不能', '不出', '没', '不', '一', '个', '多', '少',
}


def tokenize(s):
    tokens = set()
    for m in re.finditer(r'[A-Za-z0-9_]+', s):
        t = m.group(0).lower()
        if t not in STOP_WORDS and len(t) > 1:
            tokens.add(t)
        elif re.match(r'^[A-Za-z]', t) and len(t) >= 2:
            tokens.add(t)
    for m in re.finditer(r'[\u4e00-\u9fff]+', s):
        tokens.add(m.group(0))
        for ch in m.group(0):
            if ch not in STOP_WORDS:
                tokens.add(ch)
    return tokens


def build_keyword_index():
    """构造可检索单元：(单元全文, stem) 列表。包括 INDEX 行 + 所有 entry 的内容 + 所有 concept。"""
    units = []
    # INDEX.md 的每一行（带 stem 的）
    index_text = (WIKI_ROOT / 'INDEX.md').read_text(encoding='utf-8')
    for line in index_text.split('\n'):
        stems = [m.group(1).strip() for m in re.finditer(r'\[([^\]]+)\]\(', line)]
        if stems:
            for stem in stems:
                units.append((line, stem))
    # 每个 entry 的 frontmatter + 一句话根因 + 调用链
    for ef in sorted((WIKI_ROOT / 'entries' / 'bug-solutions').glob('*.md')):
        text = ef.read_text(encoding='utf-8')
        # 抽 frontmatter + 前两个章节
        m = re.match(r'(---\n.*?\n---\n)(.*?)(## 关键证据|$)', text, re.S)
        if m:
            content = m.group(1) + m.group(2)
        else:
            content = text[:1500]
        units.append((content, ef.stem))
    # 每个 concept 的全文
    for cf in sorted((WIKI_ROOT / 'concepts').glob('*.md')):
        text = cf.read_text(encoding='utf-8')
        units.append((text, cf.stem))
    return units


def build_index_only_units():
    """只读 INDEX.md 的检索单元（模拟 agent 第一层渐进加载的真实场景）。"""
    units = []
    index_text = (WIKI_ROOT / 'INDEX.md').read_text(encoding='utf-8')
    for line in index_text.split('\n'):
        stems = [m.group(1).strip() for m in re.finditer(r'\[([^\]]+)\]\(', line)]
        if stems:
            for stem in stems:
                units.append((line, stem))
    return units


def keyword_search(query, units, top_k=20):
    """关键词重叠搜索：返回 top_k 个 stem。"""
    qt = tokenize(query)
    scored = []
    for content, stem in units:
        ct = tokenize(content)
        overlap = qt & ct
        if overlap:
            # 加权：英文 token 命中权重高（更具体）
            score = sum(2.0 if re.match(r'^[a-z0-9]', t) else 1.0 for t in overlap)
            scored.append((score, stem))
    scored.sort(key=lambda x: -x[0])
    # 去重（同一 stem 可能出现多次）
    seen = set()
    result = []
    for _, stem in scored:
        if stem not in seen:
            seen.add(stem)
            result.append(stem)
        if len(result) >= top_k:
            break
    return result


# ──────────────────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--top-k', type=int, default=20, help='每个 query 检索 top-K')
    parser.add_argument('--detail', action='store_true', help='打印每条 query 详情')
    args = parser.parse_args()

    print('=' * 80)
    print('LLM-Wiki 检索准确率评估')
    print(f'wiki: {WIKI_ROOT}')
    print(f'金标准: {len(GROUND_TRUTH)} 条 query '
          f'(简单 {sum(1 for g in GROUND_TRUTH if g["category"]=="simple")}/'
          f'中等 {sum(1 for g in GROUND_TRUTH if g["category"]=="medium")}/'
          f'困难 {sum(1 for g in GROUND_TRUTH if g["category"]=="hard")})')
    print('=' * 80)
    print()

    # 构造关键词索引
    units = build_keyword_index()
    index_only_units = build_index_only_units()
    print(f'关键词索引：{len(units)} 个检索单元（含全文） / {len(index_only_units)} 个（仅 INDEX）')

    # 对每条 query 用两种 baseline 搜索
    print()
    for baseline_name, baseline_units in [
        ('B1: 关键词重叠（全文索引）', units),
        ('B1.5: 关键词重叠（仅 INDEX.md，模拟真实第一层加载）', index_only_units),
    ]:
        retrieved_per_query = []
        for gt in GROUND_TRUTH:
            retrieved = keyword_search(gt['query'], baseline_units, top_k=args.top_k)
            retrieved_per_query.append(retrieved)

        gt_per_query = [set(g['rel_entries'] + g['rel_concepts']) for g in GROUND_TRUTH]
        summary, per_query = evaluate(retrieved_per_query, gt_per_query)

        categories = {'simple': [], 'medium': [], 'hard': []}
        for i, g in enumerate(GROUND_TRUTH):
            categories[g['category']].append(i)

        print(f'─── {baseline_name} ────────────────────────────')
        print()
        print(f'{"指标":<18} {"全部":<10} {"简单":<10} {"中等":<10} {"困难":<10}')
        print('─' * 60)
        for metric in ['success@1', 'success@3', 'success@5', 'mrr',
                       'recall@3', 'recall@5', 'recall@10',
                       'precision@3', 'precision@5', 'ap']:
            all_v = summary[metric]
            simple_v = sum(per_query[metric][i] for i in categories['simple']) / len(categories['simple'])
            medium_v = sum(per_query[metric][i] for i in categories['medium']) / len(categories['medium'])
            hard_v = sum(per_query[metric][i] for i in categories['hard']) / len(categories['hard'])
            print(f'{metric:<18} {all_v:<10.3f} {simple_v:<10.3f} {medium_v:<10.3f} {hard_v:<10.3f}')
        print()

        # 只对 B1.5 打印失败/漏召回（这是更真实的下界）
        if '仅 INDEX' in baseline_name:
            print('  ─ 失败用例（success@5 = 0）─')
            fail_count = 0
            for i, g in enumerate(GROUND_TRUTH):
                if per_query['success@5'][i] == 0:
                    fail_count += 1
                    print(f'    [{g["category"]}] Q: {g["query"]}')
                    print(f'           期望: {g["rel_entries"] + g["rel_concepts"]}')
                    print(f'           实际 top-5: {retrieved_per_query[i][:5]}')
            if fail_count == 0:
                print('    （无）')

            print()
            print('  ─ 漏召回用例（recall@10 < 0.7 且期望 ≥ 3 条）─')
            partial_count = 0
            for i, g in enumerate(GROUND_TRUTH):
                n_rel = len(g['rel_entries'] + g['rel_concepts'])
                if per_query['recall@10'][i] < 0.7 and n_rel >= 3:
                    partial_count += 1
                    missing = set(g['rel_entries'] + g['rel_concepts']) - set(retrieved_per_query[i][:10])
                    print(f'    [{g["category"]}] Q: {g["query"]}')
                    print(f'           recall@10={per_query["recall@10"][i]:.2f} 期望 {n_rel} 条')
                    print(f'           漏召: {missing}')
            if partial_count == 0:
                print('    （无）')

    # 详情
    if args.detail:
        print()
        print('─── 每条 query 详情（仅 INDEX baseline）────────────────────')
        retrieved_per_query = []
        for gt in GROUND_TRUTH:
            retrieved = keyword_search(gt['query'], index_only_units, top_k=args.top_k)
            retrieved_per_query.append(retrieved)
        gt_per_query = [set(g['rel_entries'] + g['rel_concepts']) for g in GROUND_TRUTH]
        _, per_query = evaluate(retrieved_per_query, gt_per_query)
        for i, g in enumerate(GROUND_TRUTH):
            print(f'\n[{g["category"]}] Q: {g["query"]}')
            print(f'  retrieved top-5: {retrieved_per_query[i][:5]}')
            print(f'  relevant: {g["rel_entries"] + g["rel_concepts"]}')
            print(f'  recall@5={per_query["recall@5"][i]:.2f} '
                  f'P@5={per_query["precision@5"][i]:.2f} '
                  f'MRR={per_query["mrr"][i]:.2f}')

    print()
    print('=' * 80)


if __name__ == '__main__':
    main()
