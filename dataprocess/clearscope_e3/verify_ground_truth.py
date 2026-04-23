"""
ClearScope E3 Ground Truth 验证脚本
"""

import pickle
import csv
import ast
import os
from collections import defaultdict, Counter

EXTRACTED_DIR = "/mnt/disk/darpa/clearscope_e3_20260421"
GT_DIR = "/home/cch/CAPTAIN/CAPTAIN-main/cch_repeat/dataprocess/clearscope_e3/orthrus_groundtruth"


def load_extracted_data():
    print("加载提取数据...")
    with open(os.path.join(EXTRACTED_DIR, 'uuid2name.pkl'), 'rb') as f:
        uuid2name = pickle.load(f)
    print(f"  uuid2name: {len(uuid2name):,} 个实体")

    with open(os.path.join(EXTRACTED_DIR, 'datalist.pkl'), 'rb') as f:
        datalist = pickle.load(f)
    print(f"  datalist: {len(datalist):,} 条边")

    return uuid2name, datalist


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


def build_index(datalist):
    print("构建边索引...")
    src_index = defaultdict(list)
    dst_index = defaultdict(list)
    for i, edge in enumerate(datalist):
        src_index[edge[2]].append(i)
        dst_index[edge[5]].append(i)
    return src_index, dst_index


def verify_one_attack(attack_name, gt_nodes, uuid2name, datalist, src_index, dst_index):
    print(f"\n{'='*80}")
    print(f"攻击场景: {attack_name}")
    print(f"{'='*80}")
    print(f"Ground truth 节点数: {len(gt_nodes)}")

    # 按类型统计
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
        gt_name_str = gt_name[:50]
        print(f"    [{our_type:8s}] 我们={our_name_str:50s} GT={gt_name_str}")

    # ---- 2. 攻击节点关联事件 ----
    print(f"\n--- 2. 攻击节点关联事件 ---")
    gt_uuids = set(gt_nodes.keys())
    attack_edges = []

    for uuid in gt_uuids:
        for idx in src_index.get(uuid, []):
            edge = datalist[idx]
            if edge[5] in gt_uuids:
                attack_edges.append(edge)

    attack_edges = list({id(e): e for e in attack_edges}.values())
    attack_edges.sort(key=lambda x: x[0])

    print(f"  攻击节点之间的边: {len(attack_edges)}")

    if attack_edges:
        edge_type_count = Counter(e[1] for e in attack_edges)
        print(f"\n  事件类型分布:")
        for etype, cnt in edge_type_count.most_common():
            print(f"    {etype:30s} {cnt:>6}")

        print(f"\n--- 3. 攻击链事件序列 ---")
        for edge in attack_edges[:100]:
            ts, etype, su, st, sn, du, dt, dn, cmd = edge
            src_label = f"{sn}({st})" if sn else f"?({st})"
            dst_label = f"{dn}({dt})" if dn else f"?({dt})"
            # 截断长路径
            if len(src_label) > 40: src_label = "..." + src_label[-37:]
            if len(dst_label) > 40: dst_label = "..." + dst_label[-37:]
            print(f"    [{ts}] {src_label:42s} --{etype:20s}--> {dst_label}")

    # ---- 4. 每个攻击节点行为 ----
    print(f"\n--- 4. 每个攻击节点的行为概览 ---")
    for uuid, (gt_type, gt_name) in sorted(gt_nodes.items(), key=lambda x: x[1][0]):
        if uuid not in uuid2name:
            continue
        our_type, our_name = uuid2name[uuid]
        src_edges = [datalist[i] for i in src_index.get(uuid, [])]
        dst_edges = [datalist[i] for i in dst_index.get(uuid, [])]

        if not src_edges and not dst_edges:
            name_short = str(our_name)[-40:] if our_name else '?'
            print(f"\n  [{our_type:8s}] ...{name_short} — 无关联事件")
            continue

        name_short = str(our_name)[-50:] if our_name else '?'
        print(f"\n  [{our_type:8s}] ...{name_short}")
        if src_edges:
            src_types = Counter(e[1] for e in src_edges)
            print(f"    作为src({len(src_edges)}条): {dict(src_types.most_common(5))}")
        if dst_edges:
            dst_types = Counter(e[1] for e in dst_edges)
            print(f"    作为dst({len(dst_edges)}条): {dict(dst_types.most_common(5))}")

        # 与其他攻击节点的关联
        related = []
        for e in src_edges:
            if e[5] in gt_uuids:
                related.append(('→', e))
        for e in dst_edges:
            if e[2] in gt_uuids:
                related.append(('←', e))
        if related:
            related.sort(key=lambda x: x[1][0])
            print(f"    与其他攻击节点关联({len(related)}条):")
            for direction, e in related[:5]:
                ts, etype, su, st, sn, du, dt, dn, cmd = e
                if direction == '→':
                    other_name = str(dn)[-40:] if dn else '?'
                    other = f"...{other_name}({dt})"
                else:
                    other_name = str(sn)[-40:] if sn else '?'
                    other = f"...{other_name}({st})"
                print(f"      {direction} {etype:20s} {other}")


def main():
    uuid2name, datalist = load_extracted_data()
    gt_files = load_ground_truth(GT_DIR)
    src_index, dst_index = build_index(datalist)

    print(f"\nGround truth 文件: {list(gt_files.keys())}")

    for attack_name, gt_nodes in gt_files.items():
        verify_one_attack(attack_name, gt_nodes, uuid2name, datalist, src_index, dst_index)

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
