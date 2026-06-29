"""TRACE E5 Ground Truth 验证脚本（与 cadets_e5 / theia_e5 同模板）。"""

import pickle
import csv
import ast
import os
import glob
from collections import defaultdict, Counter

EXTRACTED_DIR = "/mnt/disk1/darpa_e5/trace_output"
GT_DIR = "/home/cch/CAPTAIN/CAPTAIN-main/cch_repeat/dataprocess/trace_e5/pidsmaker_groundtruth"


def load_uuid2name():
    with open(os.path.join(EXTRACTED_DIR, 'uuid2name.pkl'), 'rb') as f:
        u2n = pickle.load(f)
    u2n = {str(k).lower(): v for k, v in u2n.items()}
    print(f"  uuid2name: {len(u2n):,}")
    type_count = Counter(v[0] for v in u2n.values() if isinstance(v, (list, tuple)))
    print(f"  类型分布: {dict(type_count)}")
    return u2n


def load_ground_truth(gt_dir):
    if not os.path.isdir(gt_dir):
        print(f"  [INFO] GT 目录不存在: {gt_dir}")
        return {}
    gt_files = {}
    for fname in sorted(os.listdir(gt_dir)):
        if not fname.endswith('.csv') or fname.startswith('gt_uuid'):
            continue
        attack = fname.replace('node_', '').replace('.csv', '')
        nodes = {}
        with open(os.path.join(gt_dir, fname), 'r') as f:
            for row in csv.reader(f):
                if len(row) < 2:
                    continue
                uuid = row[0].strip().lower()
                try:
                    attrs = ast.literal_eval(row[1])
                except Exception:
                    continue
                if 'subject' in attrs:
                    nodes[uuid] = ('process', str(attrs['subject']))
                elif 'file' in attrs:
                    nodes[uuid] = ('file', str(attrs['file']))
                elif 'netflow' in attrs:
                    nodes[uuid] = ('netflow', str(attrs['netflow']))
                else:
                    nodes[uuid] = ('unknown', str(attrs))
        gt_files[attack] = nodes
        print(f"  {fname}: {len(nodes)} 个节点")
    return gt_files


def collect_attack_edges(extracted_dir, all_gt_uuids):
    print(f"\n流式扫描 edges_part_*.pkl ...")
    src_index = defaultdict(list)
    dst_index = defaultdict(list)
    parts = sorted(glob.glob(os.path.join(extracted_dir, 'edges_part_*.pkl')))
    print(f"  共 {len(parts)} 个分片")
    total, related = 0, 0
    for pf in parts:
        with open(pf, 'rb') as f:
            edges = pickle.load(f)
        total += len(edges)
        for edge in edges:
            su = str(edge[2]).lower()
            du = str(edge[5]).lower()
            if su in all_gt_uuids:
                src_index[su].append(edge)
                related += 1
            if du in all_gt_uuids:
                dst_index[du].append(edge)
                related += 1
        print(f"  {os.path.basename(pf)}: {len(edges):,}, 累计相关 {related:,}")
        del edges
    print(f"\n  总边: {total:,}, 攻击相关边(去重前): {related:,}")
    return src_index, dst_index


def verify_one(attack, gt_nodes, u2n, src_idx, dst_idx):
    print(f"\n{'='*80}\n攻击场景: {attack}\n{'='*80}")
    print(f"GT 节点: {len(gt_nodes)}")
    for t, c in Counter(t for t, _ in gt_nodes.values()).most_common():
        print(f"  {t}: {c}")

    found, missing = [], []
    for u, (gt_type, gt_name) in gt_nodes.items():
        if u in u2n:
            our_type, our_name = u2n[u]
            found.append((u, gt_type, gt_name, our_type, our_name))
        else:
            missing.append((u, gt_type, gt_name))

    pct = len(found) / len(gt_nodes) * 100 if gt_nodes else 0
    print(f"\n  找到: {len(found)}/{len(gt_nodes)} ({pct:.1f}%)  缺失: {len(missing)}")

    if missing:
        print(f"\n  缺失节点:")
        for u, gt_type, gt_name in missing[:30]:
            print(f"    [{gt_type:8s}] GT={str(gt_name)[:60]:60s} UUID={u}")

    print(f"\n  匹配详情:")
    for u, gt_type, gt_name, our_type, our_name in found:
        mark = "✓" if our_type == gt_type else "✗类型"
        our_s = str(our_name)[:50] if our_name else '?'
        gt_s = str(gt_name)[:50]
        print(f"    [{our_type:8s}] {mark} 我们={our_s:50s} GT={gt_s}")

    gt_uuids = set(f[0] for f in found)
    attack_edges = []
    for u in gt_uuids:
        for e in src_idx.get(u, []):
            if str(e[5]).lower() in gt_uuids:
                attack_edges.append(e)
    seen, unique = set(), []
    for e in attack_edges:
        k = (e[0], e[1], e[2], e[5])
        if k not in seen:
            seen.add(k)
            unique.append(e)
    unique.sort(key=lambda x: x[0])
    print(f"\n  攻击节点之间的边: {len(unique)}")
    if unique:
        for et, c in Counter(e[1] for e in unique).most_common():
            print(f"    {et:30s} {c:>6}")
        print(f"\n--- 攻击链事件序列（最多 200 条） ---")
        for e in unique[:200]:
            ts, etype, su, st, sn, du, dt, dn, cmd = e
            sl = f"{sn}({st})"[:42] if sn else f"?({st})"
            dl = f"{dn}({dt})"[:42] if dn else f"?({dt})"
            cmd_s = f"  cmdLine={str(cmd)[:50]}" if cmd else ""
            print(f"    [{ts}] {sl:42s} --{etype:18s}--> {dl}{cmd_s}")

    return found, missing, unique


def main():
    print("加载 uuid2name.pkl ...")
    u2n = load_uuid2name()
    gt_files = load_ground_truth(GT_DIR)
    if not gt_files:
        return

    all_gt = set()
    for ns in gt_files.values():
        all_gt.update(ns.keys())
    print(f"\n总 GT UUID 数: {len(all_gt)}")

    src_idx, dst_idx = collect_attack_edges(EXTRACTED_DIR, all_gt)

    g_found, g_total = 0, 0
    for attack, gt_nodes in gt_files.items():
        found, missing, _ = verify_one(attack, gt_nodes, u2n, src_idx, dst_idx)
        g_found += len(found)
        g_total += len(gt_nodes)

    print(f"\n{'='*80}\n总结\n{'='*80}")
    pct = g_found / g_total * 100 if g_total else 0
    print(f"  GT 总节点: {g_total}, 命中: {g_found} ({pct:.1f}%)")


if __name__ == '__main__':
    main()
