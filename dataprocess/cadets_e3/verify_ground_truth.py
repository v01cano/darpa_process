"""
Ground Truth 验证脚本

功能：
1. 读取 ground truth CSV 中的攻击节点 UUID
2. 在提取的 uuid2name 中查找是否存在、名称是否正确
3. 在提取的 datalist 中查找这些节点的关联事件
4. 构建攻击子图并输出攻击链
"""

import pickle
import csv
import ast
import sys
import os
from collections import defaultdict, Counter

# ============================================================================
# 配置
# ============================================================================

EXTRACTED_DIR = "/mnt/disk/darpa/cadets_e3_20260420"
GT_DIR = "/home/cch/CAPTAIN/CAPTAIN-main/cch_repeat/dataprocess/cadets_e3/orthrus_groundtruth"


def load_extracted_data():
    """加载提取的数据"""
    print("加载提取数据...")
    with open(os.path.join(EXTRACTED_DIR, 'uuid2name.pkl'), 'rb') as f:
        uuid2name = pickle.load(f)
    print(f"  uuid2name: {len(uuid2name):,} 个实体")

    with open(os.path.join(EXTRACTED_DIR, 'datalist.pkl'), 'rb') as f:
        datalist = pickle.load(f)
    print(f"  datalist: {len(datalist):,} 条边")

    return uuid2name, datalist


def load_ground_truth(gt_dir):
    """加载所有 ground truth 文件"""
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
    """构建 UUID → 边的索引"""
    print("构建边索引...")
    src_index = defaultdict(list)  # uuid → [edge_indices]
    dst_index = defaultdict(list)
    for i, edge in enumerate(datalist):
        src_uuid = edge[2]
        dst_uuid = edge[5]
        src_index[src_uuid].append(i)
        dst_index[dst_uuid].append(i)
    return src_index, dst_index


def verify_one_attack(attack_name, gt_nodes, uuid2name, datalist, src_index, dst_index):
    """验证一个攻击场景"""
    print(f"\n{'='*80}")
    print(f"攻击场景: {attack_name}")
    print(f"{'='*80}")
    print(f"Ground truth 节点数: {len(gt_nodes)}")

    # ---- 1. UUID 匹配 ----
    print(f"\n--- 1. UUID 匹配验证 ---")
    found = []
    missing = []
    name_mismatch = []

    for uuid, (gt_type, gt_name) in gt_nodes.items():
        if uuid in uuid2name:
            our_type, our_name = uuid2name[uuid]
            found.append((uuid, gt_type, gt_name, our_type, our_name))
            # 检查名称
            if our_name and gt_name:
                # gt_name 格式是 "None nginx" 或 "/tmp/main" 或 "128.55.12.73:80->..."
                if gt_type == 'process':
                    # Orthrus 格式: "path cmdLine"，如 "None nginx"
                    gt_exec = gt_name.split()[-1] if gt_name else ''
                    if our_name != gt_exec and gt_exec.lower() not in (our_name or '').lower():
                        name_mismatch.append((uuid, gt_type, gt_name, our_name))
        else:
            missing.append((uuid, gt_type, gt_name))

    print(f"  找到: {len(found)} / {len(gt_nodes)} ({len(found)/len(gt_nodes)*100:.1f}%)")
    print(f"  缺失: {len(missing)}")
    if missing:
        print(f"\n  缺失的节点:")
        for uuid, gt_type, gt_name in missing:
            print(f"    [{gt_type:8s}] {gt_name:50s}  UUID={uuid}")

    print(f"\n  匹配的节点详情:")
    for uuid, gt_type, gt_name, our_type, our_name in found:
        match_mark = "✓" if our_type == gt_type else "✗类型不同"
        print(f"    [{our_type:8s}] 我们={str(our_name):45s} GT={gt_name:30s} {match_mark}  UUID={uuid[:20]}...")

    if name_mismatch:
        print(f"\n  名称不一致的节点({len(name_mismatch)}):")
        for uuid, gt_type, gt_name, our_name in name_mismatch:
            print(f"    UUID={uuid[:20]}...  GT={gt_name}  我们={our_name}")

    # ---- 2. 攻击节点关联事件 ----
    print(f"\n--- 2. 攻击节点关联事件 ---")

    gt_uuids = set(gt_nodes.keys())
    # 找出所有攻击节点之间的边
    attack_edges = []
    attack_edges_external = []  # 攻击节点与非攻击节点之间的边

    for uuid in gt_uuids:
        # 作为源
        for idx in src_index.get(uuid, []):
            edge = datalist[idx]
            dst_uuid = edge[5]
            if dst_uuid in gt_uuids:
                attack_edges.append(edge)
            else:
                attack_edges_external.append(('as_src', edge))

        # 作为目标
        for idx in dst_index.get(uuid, []):
            edge = datalist[idx]
            src_uuid = edge[2]
            if src_uuid in gt_uuids:
                pass  # 已在 src_index 中统计
            else:
                attack_edges_external.append(('as_dst', edge))

    print(f"  攻击节点之间的边: {len(attack_edges)}")
    print(f"  攻击节点与外部的边: {len(attack_edges_external)}")

    # ---- 3. 攻击节点之间的边（攻击子图） ----
    print(f"\n--- 3. 攻击子图（攻击节点间的边，按时间排序） ---")

    attack_edges.sort(key=lambda x: x[0])  # 按时间排序

    # 按事件类型统计
    edge_type_count = Counter(e[1] for e in attack_edges)
    print(f"\n  事件类型分布:")
    for etype, cnt in edge_type_count.most_common():
        print(f"    {etype:30s} {cnt:>6}")

    print(f"\n  攻击链事件序列（时间顺序）:")
    shown = 0
    for edge in attack_edges:
        ts, etype, src_uuid, src_type, src_name, dst_uuid, dst_type, dst_name, cmdline = edge
        src_label = f"{src_name}({src_type})" if src_name else f"?({src_type})"
        dst_label = f"{dst_name}({dst_type})" if dst_name else f"?({dst_type})"
        cmd_str = f"  cmdLine={cmdline}" if cmdline else ""
        print(f"    [{ts}] {src_label:35s} --{etype:25s}--> {dst_label:35s}{cmd_str}")
        shown += 1
        if shown >= 200:
            print(f"    ... (共 {len(attack_edges)} 条，仅显示前 200 条)")
            break

    # ---- 4. 每个攻击节点的行为概览 ----
    print(f"\n--- 4. 每个攻击节点的行为概览 ---")

    for uuid, (gt_type, gt_name) in sorted(gt_nodes.items(), key=lambda x: x[1][0]):
        if uuid not in uuid2name:
            continue
        our_type, our_name = uuid2name[uuid]
        src_edges = [datalist[i] for i in src_index.get(uuid, [])]
        dst_edges = [datalist[i] for i in dst_index.get(uuid, [])]

        if not src_edges and not dst_edges:
            print(f"\n  [{our_type:8s}] {our_name} — 无关联事件")
            continue

        print(f"\n  [{our_type:8s}] {our_name}  (UUID={uuid[:20]}...)")
        if src_edges:
            src_types = Counter(e[1] for e in src_edges)
            print(f"    作为src({len(src_edges)}条): {dict(src_types.most_common(5))}")
        if dst_edges:
            dst_types = Counter(e[1] for e in dst_edges)
            print(f"    作为dst({len(dst_edges)}条): {dict(dst_types.most_common(5))}")

        # 显示攻击相关的具体边（仅另一端也是攻击节点的）
        related = []
        for e in src_edges:
            if e[5] in gt_uuids:
                related.append(('→', e))
        for e in dst_edges:
            if e[2] in gt_uuids:
                related.append(('←', e))

        if related:
            related.sort(key=lambda x: x[1][0])
            print(f"    与其他攻击节点的关联({len(related)}条):")
            for direction, e in related[:10]:
                ts, etype, src_uuid, src_type, src_name, dst_uuid, dst_type, dst_name, cmdline = e
                if direction == '→':
                    other = f"{dst_name}({dst_type})"
                else:
                    other = f"{src_name}({src_type})"
                cmd_str = f" cmd={cmdline[:50]}" if cmdline else ""
                print(f"      {direction} {etype:25s} {other:30s}{cmd_str}")


def main():
    uuid2name, datalist = load_extracted_data()
    gt_files = load_ground_truth(GT_DIR)
    src_index, dst_index = build_index(datalist)

    print(f"\nGround truth 文件: {list(gt_files.keys())}")

    for attack_name, gt_nodes in gt_files.items():
        verify_one_attack(attack_name, gt_nodes, uuid2name, datalist, src_index, dst_index)

    # ---- 总结 ----
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
