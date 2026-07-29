"""
LLM-as-judge 评估结果分析 —— 读取 llm_judge_results.json，与金标准对比。
这是 eval_retrieval.py 的 B2 补充，专门处理 LLM judge 输出。

使用方法：
  1. 先让 LLM agent 读 INDEX.md，对每条 query 给出 top-10 候选，输出 JSON
  2. JSON 保存为 .zcode/llm_judge_results.json
  3. 运行本脚本计算指标

金标准 query 列表与 eval_retrieval.py 的 GROUND_TRUTH 一致。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_retrieval import GROUND_TRUTH, evaluate

RESULTS_PATH = Path(__file__).resolve().parent.parent.parent.parent / '.zcode' / 'llm_judge_results.json'


def main():
    if not RESULTS_PATH.exists():
        print(f'找不到 LLM judge 结果文件: {RESULTS_PATH}')
        print('请先让 LLM agent 读 INDEX.md 并输出 JSON 到该路径。')
        return 1

    with open(RESULTS_PATH, encoding='utf-8') as f:
        llm_results = json.load(f)

    llm_by_query = {r['query']: r.get('top10', []) for r in llm_results}

    # 对齐 query（按 GROUND_TRUTH 顺序）
    missing = [g['query'] for g in GROUND_TRUTH if g['query'] not in llm_by_query]
    if missing:
        print(f'警告：{len(missing)} 条 query 在 LLM judge 结果中找不到:')
        for q in missing[:5]:
            print(f'  - {q}')

    retrieved_per_query = [llm_by_query.get(g['query'], []) for g in GROUND_TRUTH]
    gt_per_query = [set(g['rel_entries'] + g['rel_concepts']) for g in GROUND_TRUTH]

    summary, per_query = evaluate(retrieved_per_query, gt_per_query)

    categories = {'simple': [], 'medium': [], 'hard': []}
    for i, g in enumerate(GROUND_TRUTH):
        categories[g['category']].append(i)

    print('=' * 80)
    print('B2: LLM-as-judge（仅读 INDEX.md，模拟真实 agent 第一层加载）')
    print(f'共评估 {len(GROUND_TRUTH)} 条 query')
    print('=' * 80)
    print()
    print(f'{"指标":<18} {"全部":<10} {"简单":<10} {"中等":<10} {"困难":<10}')
    print('─' * 60)
    for metric in ['success@1', 'success@3', 'success@5', 'mrr',
                   'recall@3', 'recall@5', 'recall@10',
                   'precision@3', 'precision@5', 'ap']:
        all_v = summary[metric]
        s = sum(per_query[metric][i] for i in categories['simple']) / max(1, len(categories['simple']))
        m = sum(per_query[metric][i] for i in categories['medium']) / max(1, len(categories['medium']))
        h = sum(per_query[metric][i] for i in categories['hard']) / max(1, len(categories['hard']))
        print(f'{metric:<18} {all_v:<10.3f} {s:<10.3f} {m:<10.3f} {h:<10.3f}')

    # 失败/漏召
    print()
    print('─── 失败用例（success@5 = 0）───')
    fail_count = 0
    for i, g in enumerate(GROUND_TRUTH):
        if per_query['success@5'][i] == 0:
            fail_count += 1
            print(f'  [{g["category"]}] Q: {g["query"]}')
            print(f'         期望: {g["rel_entries"] + g["rel_concepts"]}')
            print(f'         LLM top-5: {retrieved_per_query[i][:5]}')
    if fail_count == 0:
        print('  （无）')

    print()
    print('─── 漏召回用例（recall@10 < 0.7 且期望 ≥ 3 条）───')
    partial_count = 0
    for i, g in enumerate(GROUND_TRUTH):
        n_rel = len(g['rel_entries'] + g['rel_concepts'])
        if per_query['recall@10'][i] < 0.7 and n_rel >= 3:
            partial_count += 1
            missing_set = set(g['rel_entries'] + g['rel_concepts']) - set(retrieved_per_query[i][:10])
            print(f'  [{g["category"]}] Q: {g["query"]}')
            print(f'         recall@10={per_query["recall@10"][i]:.2f} 期望 {n_rel} 条')
            print(f'         漏召: {missing_set}')
    if partial_count == 0:
        print('  （无）')

    print()
    print('─── 三 baseline 对比（综合）───')
    print('  B1   关键词重叠 + 全文索引:   下界（乐观），看下限')
    print('  B1.5 关键词重叠 + 仅 INDEX.md: 真实第一层加载的下界（严格）')
    print('  B2   LLM-as-judge:            真实 agent 行为模拟')
    print()
    print('  注意：B2 由同源 LLM 评判，可能有自我偏好（self-preference bias）。')
    print('  若需独立验证，应换不同家族的 LLM 当 judge（如 GPT 评 Claude）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
