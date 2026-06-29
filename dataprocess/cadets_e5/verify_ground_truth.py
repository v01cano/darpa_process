"""
CADETS E5 Ground Truth 验证脚本

参照 clearscope_e5/verify_ground_truth.py。

CADETS E5 没有 thread_to_process（不像 FiveDirections），UUID 已经在提取时
统一 .lower()。GT CSV 里如果也是大写，需要在加载时 .lower()。
"""

import pickle
import csv
import ast
import os
import glob
from collections import defaultdict, Counter

EXTRACTED_DIR = "/hy-tmp/cadets_e5_bin/middle"
GT_DIR = "/hy-tmp/analyse/cadets_e5/orthrus_groundtruth"


def load_uuid2name():
    path = os.path.join(EXTRACTED_DIR, 'uuid2name.pkl')
    print(f"加载 uuid2name.pkl ...")
    with open(path, 'rb') as f:
        u2n = pickle.load(f)
    u2n = {str(k).lower(): v for k, v in u2n.items()}
    print(f"  uuid2name: {len(u2n):,}")
    type_count = Counter(v[0] for v in u2n.values()
                         if isinstance(v, (list, tuple)) and len(v) >= 1)
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
    total = 0
    related = 0
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
        print(f"  {os.path.basename(pf)}: {len(edges):,} 边, 累计相关 {related:,}")
        del edges
    print(f"\n  总边数: {total:,}, 攻击相关边(去重前): {related:,}")
    return src_index, dst_index


def verify_one_attack(attack_name, gt_nodes, u2n, src_index, dst_index):
    print(f"\n{'='*80}\n攻击场景: {attack_name}\n{'='*80}")
    print(f"GT 节点数: {len(gt_nodes)}")
    for t, c in Counter(t for t, _ in gt_nodes.values()).most_common():
        print(f"  {t}: {c}")

    print(f"\n--- 1. UUID 匹配 ---")
    found, missing = [], []
    for uuid, (gt_type, gt_name) in gt_nodes.items():
        if uuid in u2n:
            our_type, our_name = u2n[uuid]
            found.append((uuid, gt_type, gt_name, our_type, our_name))
        else:
            missing.append((uuid, gt_type, gt_name))

    pct = len(found) / len(gt_nodes) * 100 if gt_nodes else 0
    print(f"  找到: {len(found)}/{len(gt_nodes)} ({pct:.1f}%)  缺失: {len(missing)}")

    if missing:
        print(f"\n  缺失节点:")
        for u, gt_type, gt_name in missing[:30]:
            print(f"    [{gt_type:8s}] GT={str(gt_name)[:60]:60s} UUID={u}")
        if len(missing) > 30:
            print(f"    ... 还有 {len(missing)-30} 个")

    print(f"\n  匹配详情:")
    for u, gt_type, gt_name, our_type, our_name in found:
        mark = "✓" if our_type == gt_type else "✗类型"
        our_s = str(our_name)[:50] if our_name else '?'
        gt_s = str(gt_name)[:50]
        print(f"    [{our_type:8s}] {mark} 我们={our_s:50s} GT={gt_s}")

    gt_uuids = set(f[0] for f in found)
    attack_edges = []
    for uuid in gt_uuids:
        for edge in src_index.get(uuid, []):
            if str(edge[5]).lower() in gt_uuids:
                attack_edges.append(edge)

    seen = set()
    unique_edges = []
    for e in attack_edges:
        k = (e[0], e[1], e[2], e[5])
        if k not in seen:
            seen.add(k)
            unique_edges.append(e)
    unique_edges.sort(key=lambda x: x[0])

    print(f"\n--- 2. 攻击节点之间的边: {len(unique_edges)} ---")
    if unique_edges:
        for et, c in Counter(e[1] for e in unique_edges).most_common():
            print(f"    {et:30s} {c:>6}")

        print(f"\n--- 3. 攻击链事件序列（最多 200 条） ---")
        for edge in unique_edges[:200]:
            ts, etype, su, st, sn, du, dt, dn, cmd = edge
            sl = f"{sn}({st})"[:42] if sn else f"?({st})"
            dl = f"{dn}({dt})"[:42] if dn else f"?({dt})"
            cmd_s = f"  cmdLine={str(cmd)[:50]}" if cmd else ""
            print(f"    [{ts}] {sl:42s} --{etype:18s}--> {dl}{cmd_s}")
        if len(unique_edges) > 200:
            print(f"    ... 共 {len(unique_edges)} 条")

    print(f"\n--- 4. 每个攻击节点行为概览 ---")
    for u, gt_type, gt_name, our_type, our_name in sorted(found, key=lambda x: x[3]):
        s_edges = src_index.get(u, [])
        d_edges = dst_index.get(u, [])
        nm = str(our_name)[-50:] if our_name else '?'
        print(f"\n  [{our_type:8s}] {nm}  (GT={str(gt_name)[:40]})")
        if not s_edges and not d_edges:
            print(f"    — 无关联事件")
            continue
        if s_edges:
            t = Counter(e[1] for e in s_edges)
            print(f"    作为src({len(s_edges)}): {dict(t.most_common(5))}")
        if d_edges:
            t = Counter(e[1] for e in d_edges)
            print(f"    作为dst({len(d_edges)}): {dict(t.most_common(5))}")

    return found, missing, unique_edges


def export_attack_graph(attack_name, found, edges, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    nodes_csv = os.path.join(out_dir, f'{attack_name}_nodes.csv')
    edges_csv = os.path.join(out_dir, f'{attack_name}_edges.csv')
    with open(nodes_csv, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['gt_uuid', 'gt_type', 'gt_name', 'our_type', 'our_name'])
        for u, gt_type, gt_name, our_type, our_name in found:
            w.writerow([u, gt_type, str(gt_name), our_type, str(our_name)])
    with open(edges_csv, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['timestamp', 'operation', 'src_uuid', 'src_type', 'src_name',
                    'dst_uuid', 'dst_type', 'dst_name', 'cmdline'])
        for e in edges:
            w.writerow(e)
    print(f"  导出: {nodes_csv}")
    print(f"  导出: {edges_csv}")


def main():
    u2n = load_uuid2name()
    gt_files = load_ground_truth(GT_DIR)
    if not gt_files:
        print("\n[退出] 没有 GT CSV。")
        return

    all_gt = set()
    for ns in gt_files.values():
        all_gt.update(ns.keys())
    print(f"\n总 GT UUID 数: {len(all_gt)}")

    src_idx, dst_idx = collect_attack_edges(EXTRACTED_DIR, all_gt)
    out_dir = os.path.join(GT_DIR, '_attack_graphs')

    g_found, g_total = 0, 0
    for attack, gt_nodes in gt_files.items():
        found, missing, edges = verify_one_attack(
            attack, gt_nodes, u2n, src_idx, dst_idx
        )
        export_attack_graph(attack, found, edges, out_dir)
        g_found += len(found)
        g_total += len(gt_nodes)

    print(f"\n{'='*80}\n总结\n{'='*80}")
    pct = g_found / g_total * 100 if g_total else 0
    print(f"  Ground truth 总节点: {g_total}")
    print(f"  命中: {g_found} ({pct:.1f}%)")
    print(f"  攻击图 CSV: {out_dir}")


if __name__ == '__main__':
    main()
