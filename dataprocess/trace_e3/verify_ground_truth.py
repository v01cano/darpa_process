"""
TRACE E3 Ground Truth 验证脚本

与 CADETS/THEIA/ClearScope 的验证逻辑一致，但适配了分片的 edges_part_*.pkl。

流程：
1. 读取 uuid2name.pkl 检查 ground truth UUID 是否在实体中
2. 流式遍历所有 edges_part_*.pkl，构建攻击节点相关的边索引
3. 分析攻击链
"""

import pickle
import csv
import ast
import os
import glob
from collections import defaultdict, Counter

EXTRACTED_DIR = "/mnt/disk/darpa/trace_e3_20260422"
GT_DIR = "/home/cch/CAPTAIN/CAPTAIN-main/cch_repeat/dataprocess/trace_e3/pidsmaker_groundtruth"


def load_uuid2name():
    path = os.path.join(EXTRACTED_DIR, 'uuid2name.pkl')
    print(f"加载 uuid2name.pkl...")
    with open(path, 'rb') as f:
        uuid2name = pickle.load(f)
    print(f"  uuid2name: {len(uuid2name):,} 个实体")
    return uuid2name


def load_ground_truth(gt_dir):
    gt_files = {}
    for fname in sorted(os.listdir(gt_dir)):
        if not fname.endswith('.csv'):
            continue
        attack_name = fname.replace('node_', '').replace('.csv', '')
        nodes = {}
        with open(os.path.join(gt_dir, fname), 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 3:
                    continue
                uuid = row[0]
                attrs = ast.literal_eval(row[1])
                if 'subject' in attrs:
                    nodes[uuid] = ('process', attrs['subject'])
                elif 'file' in attrs:
                    nodes[uuid] = ('file', attrs['file'])
                elif 'netflow' in attrs:
                    nodes[uuid] = ('netflow', attrs['netflow'])
        gt_files[attack_name] = nodes
    return gt_files


def collect_attack_edges(extracted_dir, all_gt_uuids):
    """
    流式遍历所有分片，只收集攻击节点相关的边（避免把所有边加载到内存）。

    返回: {attack_uuid → [edges_as_src + edges_as_dst]}
    """
    print(f"\n流式扫描所有 edges_part_*.pkl，只保留攻击相关边...")
    src_index = defaultdict(list)
    dst_index = defaultdict(list)

    part_files = sorted(glob.glob(os.path.join(extracted_dir, 'edges_part_*.pkl')))
    print(f"  共 {len(part_files)} 个分片")

    total_edges = 0
    related_edges = 0
    for pf in part_files:
        with open(pf, 'rb') as f:
            edges = pickle.load(f)
        total_edges += len(edges)
        for edge in edges:
            src_uuid = edge[2]
            dst_uuid = edge[5]
            if src_uuid in all_gt_uuids:
                src_index[src_uuid].append(edge)
                related_edges += 1
            if dst_uuid in all_gt_uuids:
                dst_index[dst_uuid].append(edge)
                related_edges += 1
        print(f"  {os.path.basename(pf)}: {len(edges):,} 边, 累计相关 {related_edges:,}")
        del edges

    print(f"\n  总边数: {total_edges:,}")
    print(f"  攻击相关边（去重前）: {related_edges:,}")
    return src_index, dst_index


def verify_one_attack(attack_name, gt_nodes, uuid2name, src_index, dst_index):
    print(f"\n{'='*80}")
    print(f"攻击场景: {attack_name}")
    print(f"{'='*80}")
    print(f"Ground truth 节点数: {len(gt_nodes)}")

    gt_type_count = Counter(t for t, _ in gt_nodes.values())
    for t, cnt in gt_type_count.most_common():
        print(f"  {t}: {cnt}")

    # ---- 1. UUID 匹配 ----
    print(f"\n--- 1. UUID 匹配验证 ---")
    found = []
    missing = []

    for uuid, (gt_type, gt_name) in gt_nodes.items():
        if uuid in uuid2name:
            our_type, our_name = uuid2name[uuid]
            found.append((uuid, gt_type, gt_name, our_type, our_name))
        else:
            missing.append((uuid, gt_type, gt_name))

    print(f"  找到: {len(found)} / {len(gt_nodes)} ({len(found)/len(gt_nodes)*100:.1f}%)")
    print(f"  缺失: {len(missing)}")

    if missing:
        print(f"\n  缺失的节点:")
        for uuid, gt_type, gt_name in missing:
            print(f"    [{gt_type:8s}] {gt_name[:60]:60s}  UUID={uuid}")

    print(f"\n  匹配的节点详情:")
    for uuid, gt_type, gt_name, our_type, our_name in found:
        our_name_str = str(our_name)[:50] if our_name else '?'
        print(f"    [{our_type:8s}] 我们={our_name_str:50s} GT={gt_name}")

    # ---- 2. 攻击节点关联事件 ----
    print(f"\n--- 2. 攻击节点关联事件 ---")
    gt_uuids = set(gt_nodes.keys())
    attack_edges = []

    for uuid in gt_uuids:
        for edge in src_index.get(uuid, []):
            if edge[5] in gt_uuids:
                attack_edges.append(edge)

    # 去重（同一条边可能出现在src_index和dst_index中）
    seen = set()
    unique_edges = []
    for e in attack_edges:
        # 用 (timestamp, event_type, src, dst) 作为唯一键
        k = (e[0], e[1], e[2], e[5])
        if k not in seen:
            seen.add(k)
            unique_edges.append(e)
    unique_edges.sort(key=lambda x: x[0])

    print(f"  攻击节点之间的边: {len(unique_edges)}")

    if unique_edges:
        edge_type_count = Counter(e[1] for e in unique_edges)
        print(f"\n  事件类型分布:")
        for etype, cnt in edge_type_count.most_common():
            print(f"    {etype:30s} {cnt:>6}")

        print(f"\n--- 3. 攻击链事件序列（时间顺序） ---")
        shown = 0
        for edge in unique_edges:
            ts, etype, su, st, sn, du, dt, dn, cmd = edge
            src_label = f"{sn}({st})" if sn else f"?({st})"
            dst_label = f"{dn}({dt})" if dn else f"?({dt})"
            if len(src_label) > 40: src_label = "..." + src_label[-37:]
            if len(dst_label) > 40: dst_label = "..." + dst_label[-37:]
            cmd_str = f"  cmdLine={str(cmd)[:50]}" if cmd else ""
            print(f"    [{ts}] {src_label:42s} --{etype:20s}--> {dst_label}{cmd_str}")
            shown += 1
            if shown >= 200:
                print(f"    ... (共 {len(unique_edges)} 条)")
                break

    # ---- 4. 每个攻击节点行为 ----
    print(f"\n--- 4. 每个攻击节点的行为概览 ---")
    for uuid, (gt_type, gt_name) in sorted(gt_nodes.items(), key=lambda x: x[1][0]):
        if uuid not in uuid2name:
            continue
        our_type, our_name = uuid2name[uuid]
        src_edges = src_index.get(uuid, [])
        dst_edges = dst_index.get(uuid, [])

        if not src_edges and not dst_edges:
            name_short = str(our_name)[-40:] if our_name else '?'
            print(f"\n  [{our_type:8s}] {name_short} — 无关联事件")
            continue

        name_short = str(our_name)[-50:] if our_name else '?'
        print(f"\n  [{our_type:8s}] {name_short}  (GT={gt_name[:40]})")
        if src_edges:
            src_types = Counter(e[1] for e in src_edges)
            print(f"    作为src({len(src_edges)}条): {dict(src_types.most_common(5))}")
        if dst_edges:
            dst_types = Counter(e[1] for e in dst_edges)
            print(f"    作为dst({len(dst_edges)}条): {dict(dst_types.most_common(5))}")

        related = []
        for e in src_edges:
            if e[5] in gt_uuids:
                related.append(('→', e))
        for e in dst_edges:
            if e[2] in gt_uuids:
                related.append(('←', e))
        if related:
            related.sort(key=lambda x: x[1][0])
            # 去重
            seen_k = set()
            unique_related = []
            for d, e in related:
                k = (e[0], e[1], e[2], e[5])
                if k not in seen_k:
                    seen_k.add(k)
                    unique_related.append((d, e))
            print(f"    与其他攻击节点关联({len(unique_related)}条):")
            for direction, e in unique_related[:8]:
                ts, etype, su, st, sn, du, dt, dn, cmd = e
                if direction == '→':
                    other_name = str(dn)[-40:] if dn else '?'
                    other = f"{other_name}({dt})"
                else:
                    other_name = str(sn)[-40:] if sn else '?'
                    other = f"{other_name}({st})"
                print(f"      {direction} {etype:20s} {other}")


def main():
    uuid2name = load_uuid2name()
    gt_files = load_ground_truth(GT_DIR)
    print(f"\nGround truth 文件: {list(gt_files.keys())}")

    # 收集所有ground truth UUID
    all_gt_uuids = set()
    for nodes in gt_files.values():
        all_gt_uuids.update(nodes.keys())
    print(f"总 ground truth UUID 数: {len(all_gt_uuids)}")

    # 流式扫描边分片
    src_index, dst_index = collect_attack_edges(EXTRACTED_DIR, all_gt_uuids)

    # 逐攻击场景验证
    for attack_name, gt_nodes in gt_files.items():
        verify_one_attack(attack_name, gt_nodes, uuid2name, src_index, dst_index)

    # 总结
    print(f"\n{'='*80}")
    print("总结")
    print(f"{'='*80}")
    total_gt = sum(len(nodes) for nodes in gt_files.values())
    total_found = sum(
        sum(1 for uuid in nodes if uuid in uuid2name)
        for nodes in gt_files.values()
    )
    print(f"  Ground truth 总节点: {total_gt}")
    print(f"  在提取数据中找到: {total_found} ({total_found/total_gt*100:.1f}%)")


if __name__ == '__main__':
    main()
