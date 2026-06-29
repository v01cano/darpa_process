"""FiveDirections E5 GT 验证 — 简化版（不扫边分片，秒级）。"""

import pickle
import csv
import ast
import os
from collections import Counter

EXTRACTED_DIR = "/mnt/disk/darpa/fivedirections_e5_output"
GT_DIR = "/home/cch/CAPTAIN/CAPTAIN-main/cch_repeat/dataprocess/fivedirections_e5/pidsmaker_groundtruth"
OUT_CSV = os.path.join(GT_DIR, 'gt_uuid_simple_report.csv')


def load_u2n():
    with open(os.path.join(EXTRACTED_DIR, 'uuid2name.pkl'), 'rb') as f:
        u2n = pickle.load(f)
    return {str(k).lower(): v for k, v in u2n.items()}


def load_t2p():
    p = os.path.join(EXTRACTED_DIR, 'thread_to_process.pkl')
    if not os.path.exists(p):
        return {}
    with open(p, 'rb') as f:
        t2p = pickle.load(f)
    return {str(k).lower(): str(v).lower() for k, v in t2p.items()}


def load_gt():
    if not os.path.isdir(GT_DIR):
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
    return gt


def main():
    print("加载 ...")
    u2n = load_u2n()
    t2p = load_t2p()
    print(f"  uuid2name: {len(u2n):,}, thread_map: {len(t2p):,}")
    gt = load_gt()
    if not gt:
        print("[INFO] 无 GT")
        return

    rows = []
    g_total, g_found = 0, 0
    for attack, gt_nodes in gt.items():
        print(f"\n{'='*80}\n{attack}\n{'='*80}")
        print(f"GT: {len(gt_nodes)}")

        found, missing = [], []
        for u, (gt_type, gt_name) in gt_nodes.items():
            resolved = u if u in u2n else (t2p.get(u) if t2p.get(u) in u2n else None)
            is_thread = (resolved is not None and resolved != u)
            if resolved:
                our_type, our_name = u2n[resolved]
                found.append((u, gt_type, gt_name, our_type, str(our_name or ''), is_thread))
            else:
                missing.append((u, gt_type, gt_name))

        pct = len(found) / len(gt_nodes) * 100 if gt_nodes else 0
        print(f"找到: {len(found)}/{len(gt_nodes)} ({pct:.1f}%)  "
              f"THREAD→PROC: {sum(1 for f in found if f[5])}  缺失: {len(missing)}")

        print(f"\n匹配:")
        for u, gt_type, gt_name, our_type, our_name, is_thread in found:
            mark = "✓" if our_type == gt_type else "✗类型"
            tag = '[T]' if is_thread else '   '
            print(f"  {tag}[{our_type:<6}] {mark} 我们={our_name[:50]:50s} GT={str(gt_name)[:50]}")

        if missing:
            print(f"\n缺失:")
            for u, gt_type, gt_name in missing[:20]:
                print(f"  [{gt_type:8s}] GT={str(gt_name)[:60]:60s} UUID={u}")

        for u, gt_type, gt_name, our_type, our_name, is_thread in found:
            rows.append({
                'attack': attack, 'gt_uuid': u,
                'status': 'thread→process' if is_thread else 'direct',
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

    print(f"\n{'='*80}\n总结: {g_found}/{g_total} ({g_found/g_total*100:.1f}%)")
    if rows:
        with open(OUT_CSV, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"详细报告: {OUT_CSV}")


if __name__ == '__main__':
    main()
