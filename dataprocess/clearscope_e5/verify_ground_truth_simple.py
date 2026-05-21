"""
ClearScope E5 Ground Truth 验证 — 简化版（不扫边分片）

参照 fivedirections_e3/verify_ground_truth_simple.py。
只做 UUID 命中 + 名称对比，秒级完成。
"""

import pickle
import csv
import ast
import os
from collections import Counter

EXTRACTED_DIR = "/mnt/disk/darpa/clearscope_e5_output"
GT_DIR = "/home/cch/CAPTAIN/CAPTAIN-main/cch_repeat/dataprocess/clearscope_e5/pidsmaker_groundtruth"
OUT_CSV = os.path.join(GT_DIR, 'gt_uuid_simple_report.csv')


def load_u2n():
    print("加载 uuid2name.pkl ...")
    with open(os.path.join(EXTRACTED_DIR, 'uuid2name.pkl'), 'rb') as f:
        u2n = pickle.load(f)
    u2n = {str(k).lower(): v for k, v in u2n.items()}
    print(f"  uuid2name: {len(u2n):,}")
    return u2n


def load_gt():
    if not os.path.isdir(GT_DIR):
        print(f"[INFO] GT 目录不存在: {GT_DIR}")
        return {}
    gt = {}
    for fname in sorted(os.listdir(GT_DIR)):
        if not fname.endswith('.csv') or fname.startswith('gt_uuid'):
            continue
        attack = fname.replace('node_', '').replace('.csv', '')
        nodes = {}
        with open(os.path.join(GT_DIR, fname), 'r') as f:
            for row in csv.reader(f):
                if len(row) < 2:
                    continue
                uuid = row[0].strip().lower()
                try:
                    attrs = ast.literal_eval(row[1])
                except Exception:
                    attrs = {}
                if 'subject' in attrs:
                    nodes[uuid] = ('process', str(attrs['subject']))
                elif 'file' in attrs:
                    nodes[uuid] = ('file', str(attrs['file']))
                elif 'netflow' in attrs:
                    nodes[uuid] = ('netflow', str(attrs['netflow']))
                else:
                    nodes[uuid] = ('unknown', str(attrs))
        gt[attack] = nodes
        print(f"  {fname}: {len(nodes)}")
    return gt


def main():
    u2n = load_u2n()
    gt = load_gt()
    if not gt:
        return

    rows = []
    g_total, g_found = 0, 0
    for attack, gt_nodes in gt.items():
        print(f"\n{'='*80}\n攻击场景: {attack}\n{'='*80}")
        print(f"GT 节点数: {len(gt_nodes)}")
        for t, c in Counter(t for t, _ in gt_nodes.values()).most_common():
            print(f"  {t}: {c}")

        found = []
        missing = []
        for uuid, (gt_type, gt_name) in gt_nodes.items():
            if uuid in u2n:
                our_type, our_name = u2n[uuid]
                found.append((uuid, gt_type, gt_name, our_type, str(our_name)))
            else:
                missing.append((uuid, gt_type, gt_name))

        pct = len(found) / len(gt_nodes) * 100 if gt_nodes else 0
        print(f"\n找到: {len(found)}/{len(gt_nodes)} ({pct:.1f}%)  缺失: {len(missing)}")

        if missing:
            print(f"\n缺失节点:")
            for u, gt_type, gt_name in missing[:20]:
                print(f"  [{gt_type:8s}] GT={str(gt_name)[:60]:60s} UUID={u}")
            if len(missing) > 20:
                print(f"  ... 还有 {len(missing)-20} 个")

        print(f"\n匹配详情:")
        print(f"  {'类型':<8} {'✓/✗':<6} {'我们=name':<50} GT=name")
        print("  " + "-" * 130)
        for u, gt_type, gt_name, our_type, our_name in found:
            mark = "✓" if our_type == gt_type else "✗类型"
            our_s = our_name[:46]
            gt_s = str(gt_name)[:60]
            print(f"  [{our_type:<6}] {mark:<6} 我们={our_s:<46} GT={gt_s}")

        for u, gt_type, gt_name, our_type, our_name in found:
            rows.append({
                'attack': attack, 'gt_uuid': u, 'status': 'direct',
                'gt_type': gt_type, 'gt_name': str(gt_name)[:300],
                'our_type': our_type, 'our_name': our_name[:300],
                'type_match': int(our_type == gt_type),
            })
        for u, gt_type, gt_name in missing:
            rows.append({
                'attack': attack, 'gt_uuid': u, 'status': 'MISS',
                'gt_type': gt_type, 'gt_name': str(gt_name)[:300],
                'our_type': '', 'our_name': '', 'type_match': 0,
            })
        g_total += len(gt_nodes)
        g_found += len(found)

    print(f"\n{'='*80}\n总结\n{'='*80}")
    pct = g_found / g_total * 100 if g_total else 0
    print(f"  GT 总节点: {g_total}")
    print(f"  命中: {g_found} ({pct:.1f}%)")
    print(f"  缺失: {g_total - g_found}")

    if rows:
        with open(OUT_CSV, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n详细报告: {OUT_CSV}")


if __name__ == '__main__':
    main()
