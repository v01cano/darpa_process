"""
FiveDirections E5 Ground Truth 验证脚本

参照 fivedirections_e3/verify_ground_truth.py：
  - 加载 thread_to_process.pkl，把 GT 中的 thread UUID 映射到父 PROCESS
  - 所有 UUID 强制 .lower() 比较
"""

import pickle
import csv
import ast
import os
import glob
from collections import defaultdict, Counter

EXTRACTED_DIR = "/mnt/disk/darpa/fivedirections_e5_output"
GT_DIR = "/home/cch/CAPTAIN/CAPTAIN-main/cch_repeat/dataprocess/fivedirections_e5/pidsmaker_groundtruth"


def load_uuid2name():
    with open(os.path.join(EXTRACTED_DIR, 'uuid2name.pkl'), 'rb') as f:
        u2n = pickle.load(f)
    u2n = {str(k).lower(): v for k, v in u2n.items()}
    print(f"  uuid2name: {len(u2n):,}")
    type_count = Counter(v[0] for v in u2n.values() if isinstance(v, (list, tuple)))
    print(f"  类型分布: {dict(type_count)}")
    return u2n


def load_thread_map():
    p = os.path.join(EXTRACTED_DIR, 'thread_to_process.pkl')
    if not os.path.exists(p):
        return {}
    with open(p, 'rb') as f:
        t2p = pickle.load(f)
    t2p = {str(k).lower(): str(v).lower() for k, v in t2p.items()}
    print(f"  thread_to_process: {len(t2p):,}")
    return t2p


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
        print(f"  {fname}: {len(nodes)}")
    return gt_files


def resolve_gt(uuid, u2n, t2p):
    if uuid in u2n:
        return uuid, False
    if uuid in t2p:
        parent = t2p[uuid]
        if parent in u2n:
            return parent, True
    return None, False


def collect_attack_edges(extracted_dir, all_uuids):
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
        for e in edges:
            su = str(e[2]).lower()
            du = str(e[5]).lower()
            if su in all_uuids:
                src_index[su].append(e)
                related += 1
            if du in all_uuids:
                dst_index[du].append(e)
                related += 1
        print(f"  {os.path.basename(pf)}: {len(edges):,}, 累计相关 {related:,}")
        del edges
    print(f"\n  总边: {total:,}, 攻击相关边(去重前): {related:,}")
    return src_index, dst_index


def verify_one(attack, gt_nodes, u2n, t2p, src_idx, dst_idx):
    print(f"\n{'='*80}\n攻击场景: {attack}\n{'='*80}")
    print(f"GT 节点: {len(gt_nodes)}")
    for t, c in Counter(t for t, _ in gt_nodes.values()).most_common():
        print(f"  {t}: {c}")

    found, missing = [], []
    for u, (gt_type, gt_name) in gt_nodes.items():
        resolved, is_thread = resolve_gt(u, u2n, t2p)
        if resolved:
            our_type, our_name = u2n[resolved]
            found.append((u, resolved, gt_type, gt_name, our_type, our_name, is_thread))
        else:
            missing.append((u, gt_type, gt_name))

    pct = len(found) / len(gt_nodes) * 100 if gt_nodes else 0
    print(f"\n  找到: {len(found)}/{len(gt_nodes)} ({pct:.1f}%)")
    print(f"  其中 THREAD→PROCESS: {sum(1 for f in found if f[6])}")
    print(f"  缺失: {len(missing)}")

    if missing:
        print(f"\n  缺失节点:")
        for u, gt_type, gt_name in missing[:30]:
            print(f"    [{gt_type:8s}] GT={str(gt_name)[:60]:60s} UUID={u}")

    print(f"\n  匹配详情:")
    for u, r, gt_type, gt_name, our_type, our_name, is_thread in found:
        mark = "✓" if our_type == gt_type else "✗类型"
        tag = ' [T]' if is_thread else ''
        our_s = str(our_name)[:50] if our_name else '?'
        gt_s = str(gt_name)[:50]
        print(f"    [{our_type:8s}]{tag} {mark} 我们={our_s:50s} GT={gt_s}")

    resolved_uuids = set(f[1] for f in found)
    attack_edges = []
    for u in resolved_uuids:
        for e in src_idx.get(u, []):
            if str(e[5]).lower() in resolved_uuids:
                attack_edges.append(e)
    seen = set()
    unique_edges = []
    for e in attack_edges:
        k = (e[0], e[1], e[2], e[5])
        if k not in seen:
            seen.add(k)
            unique_edges.append(e)
    unique_edges.sort(key=lambda x: x[0])
    print(f"\n  攻击节点之间的边: {len(unique_edges)}")
    if unique_edges:
        for et, c in Counter(e[1] for e in unique_edges).most_common():
            print(f"    {et:30s} {c:>6}")

    return found, missing, unique_edges


def main():
    print("加载 uuid2name.pkl ...")
    u2n = load_uuid2name()
    print("加载 thread_to_process.pkl ...")
    t2p = load_thread_map()
    gt_files = load_ground_truth(GT_DIR)
    if not gt_files:
        return

    all_uuids = set()
    for ns in gt_files.values():
        for u in ns.keys():
            all_uuids.add(u)
            if u in t2p:
                all_uuids.add(t2p[u])

    src_idx, dst_idx = collect_attack_edges(EXTRACTED_DIR, all_uuids)

    g_found, g_total = 0, 0
    for attack, gt_nodes in gt_files.items():
        found, missing, edges = verify_one(attack, gt_nodes, u2n, t2p, src_idx, dst_idx)
        g_found += len(found)
        g_total += len(gt_nodes)

    print(f"\n{'='*80}\n总结\n{'='*80}")
    pct = g_found / g_total * 100 if g_total else 0
    print(f"  GT 总节点: {g_total}, 命中: {g_found} ({pct:.1f}%)")


if __name__ == '__main__':
    main()
