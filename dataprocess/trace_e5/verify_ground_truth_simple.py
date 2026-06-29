"""TRACE E5 GT 验证 — 简化版（不扫边分片）。"""

import pickle
import csv
import ast
import os
from collections import Counter

EXTRACTED_DIR = "/mnt/disk1/darpa_e5/trace/trace_middle"
GT_DIR = "/home/cch/CAPTAIN/CAPTAIN-main/cch_repeat/dataprocess/trace_e5/orthrus_groundtruth"
OUT_CSV = os.path.join(GT_DIR, 'gt_uuid_simple_report.csv')


def load_u2n():
    with open(os.path.join(EXTRACTED_DIR, 'uuid2name.pkl'), 'rb') as f:
        u2n = pickle.load(f)
    return {str(k).lower(): v for k, v in u2n.items()}


def load_cmdline():
    p = os.path.join(EXTRACTED_DIR, 'uuid_cmdline.pkl')
    if not os.path.exists(p):
        return {}
    with open(p, 'rb') as f:
        d = pickle.load(f)
    return {str(k).lower(): v for k, v in d.items()}


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
    u_cmd = load_cmdline()
    print(f"  uuid2name: {len(u2n):,}  cmdline: {len(u_cmd):,}")
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
            if u in u2n:
                our_type, our_name = u2n[u]
                cmd = u_cmd.get(u, '') if our_type == 'process' else ''
                found.append((u, gt_type, gt_name, our_type, str(our_name or ''), str(cmd or '')))
            else:
                missing.append((u, gt_type, gt_name))

        pct = len(found) / len(gt_nodes) * 100 if gt_nodes else 0
        print(f"找到: {len(found)}/{len(gt_nodes)} ({pct:.1f}%)  缺失: {len(missing)}")

        if missing:
            print(f"\n缺失:")
            for u, gt_type, gt_name in missing[:20]:
                print(f"  [{gt_type:8s}] GT={str(gt_name)[:60]:60s} UUID={u}")

        print(f"\n匹配:")
        for u, gt_type, gt_name, our_type, our_name, cmd in found:
            mark = "✓" if our_type == gt_type else "✗类型"
            print(f"  [{our_type:<6}] {mark:<6} 我们={our_name[:40]:40s} GT={str(gt_name)[:50]:50s} {cmd[:50]}")

        for u, gt_type, gt_name, our_type, our_name, cmd in found:
            rows.append({
                'attack': attack, 'gt_uuid': u, 'status': 'direct',
                'gt_type': gt_type, 'gt_name': str(gt_name)[:300],
                'our_type': our_type, 'our_name': our_name[:300],
                'our_cmdline': cmd[:300],
                'type_match': int(our_type == gt_type),
            })
        for u, gt_type, gt_name in missing:
            rows.append({
                'attack': attack, 'gt_uuid': u, 'status': 'MISS',
                'gt_type': gt_type, 'gt_name': str(gt_name)[:300],
                'our_type': '', 'our_name': '', 'our_cmdline': '',
                'type_match': 0,
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
