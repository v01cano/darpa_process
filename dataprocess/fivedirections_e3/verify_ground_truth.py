"""
FiveDirections E3 Ground Truth 验证脚本

参照 CADETS/THEIA/ClearScope/TRACE 的验证脚本，适配 FiveDirections 分片的 edges_part_*.pkl。

流程：
1. 读取 uuid2name.pkl 检查 ground truth UUID 是否在实体中
2. 流式遍历所有 edges_part_*.pkl，构建攻击节点相关的边索引
3. 分析攻击链（攻击节点之间的事件，以及每个攻击节点的行为概览）

注意：FiveDirections 提取结果里的 UUID 保留原始大小写，GT CSV 也可能是大写。
      脚本内部统一转成小写再匹配。
"""

import pickle
import csv
import ast
import os
import glob
from collections import defaultdict, Counter

EXTRACTED_DIR = "/mnt/disk/darpa/fivedirections_e3_20260423"
GT_DIR = "/home/cch/CAPTAIN/CAPTAIN-main/cch_repeat/dataprocess/fivedirections_e3/pidsmaker_groundtruth"


def load_uuid2name():
    path = os.path.join(EXTRACTED_DIR, 'uuid2name.pkl')
    print(f"加载 uuid2name.pkl ...")
    with open(path, 'rb') as f:
        uuid2name = pickle.load(f)
    uuid2name = {
        str(uuid).lower(): value
        for uuid, value in uuid2name.items()
    }
    print(f"  uuid2name: {len(uuid2name):,} 个实体")
    # 类型分布
    type_count = Counter(v[0] for v in uuid2name.values() if isinstance(v, (list, tuple)) and len(v) >= 1)
    print(f"  类型分布: {dict(type_count)}")
    return uuid2name


def load_thread_map():
    """FiveDirections 有 SUBJECT_THREAD → parent PROCESS 的 UUID 映射。
    在 GT 查找时，先把 thread uuid 映射到 process uuid。"""
    path = os.path.join(EXTRACTED_DIR, 'cmdlines.pkl')
    if not os.path.exists(path):
        return {}
    with open(path, 'rb') as f:
        data = pickle.load(f)
    t2p = data.get('thread_to_process', {}) if isinstance(data, dict) else {}
    t2p = {
        str(thread_uuid).lower(): str(process_uuid).lower()
        for thread_uuid, process_uuid in t2p.items()
    }
    print(f"  thread_to_process: {len(t2p):,} 条映射")
    return t2p


def load_ground_truth(gt_dir):
    """
    CSV 格式: UUID, "{'type': 'info_str'}", index_id
    type 可能为 'subject' / 'file' / 'netflow'
    """
    gt_files = {}
    for fname in sorted(os.listdir(gt_dir)):
        if not fname.endswith('.csv'):
            continue
        attack_name = fname.replace('node_', '').replace('.csv', '')
        nodes = {}
        with open(os.path.join(gt_dir, fname), 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 2:
                    continue
                uuid = row[0].strip().lower()  # 统一小写
                try:
                    attrs = ast.literal_eval(row[1])
                except Exception as e:
                    print(f"  [WARN] 无法解析 {fname} UUID={uuid}: {e}")
                    continue
                if 'subject' in attrs:
                    nodes[uuid] = ('process', attrs['subject'])
                elif 'file' in attrs:
                    nodes[uuid] = ('file', attrs['file'])
                elif 'netflow' in attrs:
                    nodes[uuid] = ('netflow', attrs['netflow'])
                else:
                    nodes[uuid] = ('unknown', str(attrs))
        gt_files[attack_name] = nodes
        print(f"  {fname}: {len(nodes)} 个节点")
    return gt_files


def collect_attack_edges(extracted_dir, all_gt_uuids):
    """流式扫描分片边文件，只收集攻击节点相关的边。"""
    print(f"\n流式扫描 edges_part_*.pkl，只保留攻击相关边 ...")
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
            # edge: (timestamp, op, src_uuid, src_type, src_name,
            #                      dst_uuid, dst_type, dst_name, cmdline)
            src_uuid = str(edge[2]).lower()
            dst_uuid = str(edge[5]).lower()
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


def resolve_gt_uuid(uuid, uuid2name, thread_map):
    """返回 (resolved_uuid, is_thread_mapped)。
    thread uuid 会被映射到 parent process uuid。"""
    if uuid in uuid2name:
        return uuid, False
    if uuid in thread_map:
        parent = thread_map[uuid]
        if parent in uuid2name:
            return parent, True
    return None, False


def verify_one_attack(attack_name, gt_nodes, uuid2name, thread_map, src_index, dst_index):
    print(f"\n{'='*80}")
    print(f"攻击场景: {attack_name}")
    print(f"{'='*80}")
    print(f"Ground truth 节点数: {len(gt_nodes)}")

    gt_type_count = Counter(t for t, _ in gt_nodes.values())
    for t, cnt in gt_type_count.most_common():
        print(f"  {t}: {cnt}")

    # ---- 1. UUID 匹配 ----
    print(f"\n--- 1. UUID 匹配验证 ---")
    found = []           # (gt_uuid, resolved_uuid, gt_type, gt_name, our_type, our_name, is_thread)
    missing = []

    for uuid, (gt_type, gt_name) in gt_nodes.items():
        resolved, is_thread = resolve_gt_uuid(uuid, uuid2name, thread_map)
        if resolved:
            our_type, our_name = uuid2name[resolved]
            found.append((uuid, resolved, gt_type, gt_name, our_type, our_name, is_thread))
        else:
            missing.append((uuid, gt_type, gt_name))

    print(f"  找到: {len(found)} / {len(gt_nodes)} ({len(found)/len(gt_nodes)*100:.1f}%)")
    print(f"  其中 THREAD→PROCESS 映射: {sum(1 for f in found if f[6])}")
    print(f"  缺失: {len(missing)}")

    if missing:
        print(f"\n  缺失的节点:")
        for uuid, gt_type, gt_name in missing:
            gt_name_str = str(gt_name)[:60]
            print(f"    [{gt_type:8s}] {gt_name_str:60s}  UUID={uuid}")

    print(f"\n  匹配的节点详情:")
    for uuid, resolved, gt_type, gt_name, our_type, our_name, is_thread in found:
        our_name_str = str(our_name)[:50] if our_name else '?'
        gt_name_str = str(gt_name)[:60]
        tag = ' [THREAD]' if is_thread else ''
        print(f"    [{our_type:8s}]{tag} 我们={our_name_str:50s} GT={gt_name_str}")

    # ---- 2. 攻击节点之间的关联事件 ----
    # 用 resolved uuid 构造集合
    resolved_uuids = set(f[1] for f in found)

    print(f"\n--- 2. 攻击节点之间的关联边（攻击图） ---")
    attack_edges = []
    for uuid in resolved_uuids:
        for edge in src_index.get(uuid, []):
            if str(edge[5]).lower() in resolved_uuids:
                attack_edges.append(edge)

    # 去重
    seen = set()
    unique_edges = []
    for e in attack_edges:
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

        print(f"\n--- 3. 攻击链事件序列（时间顺序，最多 200 条） ---")
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

    # ---- 4. 每个攻击节点行为概览 ----
    print(f"\n--- 4. 每个攻击节点的行为概览 ---")
    for item in sorted(found, key=lambda x: x[4]):  # sort by our_type
        gt_uuid, resolved, gt_type, gt_name, our_type, our_name, is_thread = item
        src_edges = src_index.get(resolved, [])
        dst_edges = dst_index.get(resolved, [])

        name_short = str(our_name)[-50:] if our_name else '?'
        tag = ' [THREAD]' if is_thread else ''
        gt_short = str(gt_name)[:40]
        print(f"\n  [{our_type:8s}]{tag} {name_short}  (GT={gt_short})")

        if not src_edges and not dst_edges:
            print(f"    — 无关联事件")
            continue

        if src_edges:
            src_types = Counter(e[1] for e in src_edges)
            print(f"    作为src({len(src_edges)}条): {dict(src_types.most_common(5))}")
        if dst_edges:
            dst_types = Counter(e[1] for e in dst_edges)
            print(f"    作为dst({len(dst_edges)}条): {dict(dst_types.most_common(5))}")

        # 与其他攻击节点的关联
        related = []
        for e in src_edges:
            if str(e[5]).lower() in resolved_uuids:
                related.append(('→', e))
        for e in dst_edges:
            if str(e[2]).lower() in resolved_uuids:
                related.append(('←', e))
        if related:
            related.sort(key=lambda x: x[1][0])
            seen_k = set()
            unique_related = []
            for d, e in related:
                k = (e[0], e[1], e[2], e[5])
                if k not in seen_k:
                    seen_k.add(k)
                    unique_related.append((d, e))
            print(f"    与其他攻击节点关联({len(unique_related)}条，最多 8 条):")
            for direction, e in unique_related[:8]:
                ts, etype, su, st, sn, du, dt, dn, cmd = e
                if direction == '→':
                    other_name = str(dn)[-40:] if dn else '?'
                    other = f"{other_name}({dt})"
                else:
                    other_name = str(sn)[-40:] if sn else '?'
                    other = f"{other_name}({st})"
                print(f"      {direction} {etype:20s} {other}")

    return found, missing, unique_edges


def export_attack_graph(attack_name, found, unique_edges, out_dir):
    """导出攻击图为 CSV 便于后续可视化。"""
    os.makedirs(out_dir, exist_ok=True)
    nodes_csv = os.path.join(out_dir, f'{attack_name}_nodes.csv')
    edges_csv = os.path.join(out_dir, f'{attack_name}_edges.csv')

    with open(nodes_csv, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['gt_uuid', 'resolved_uuid', 'gt_type', 'gt_name',
                    'our_type', 'our_name', 'is_thread_mapped'])
        for item in found:
            gt_uuid, resolved, gt_type, gt_name, our_type, our_name, is_thread = item
            w.writerow([gt_uuid, resolved, gt_type, str(gt_name),
                        our_type, str(our_name), int(is_thread)])

    with open(edges_csv, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['timestamp', 'operation', 'src_uuid', 'src_type', 'src_name',
                    'dst_uuid', 'dst_type', 'dst_name', 'cmdline'])
        for e in unique_edges:
            w.writerow(e)

    print(f"  导出: {nodes_csv}")
    print(f"  导出: {edges_csv}")


def main():
    uuid2name = load_uuid2name()
    thread_map = load_thread_map()
    gt_files = load_ground_truth(GT_DIR)
    print(f"\nGround truth 文件: {list(gt_files.keys())}")

    # 收集所有 GT UUID（加上 thread→process 映射后的 uuid 用于边扫描）
    all_gt_uuids = set()
    for nodes in gt_files.values():
        for u in nodes.keys():
            all_gt_uuids.add(u)
            if u in thread_map:
                all_gt_uuids.add(thread_map[u])
    print(f"总 ground truth UUID 数（含 thread→process 解析）: {len(all_gt_uuids)}")

    # 流式扫描攻击相关边
    src_index, dst_index = collect_attack_edges(EXTRACTED_DIR, all_gt_uuids)

    # 输出目录（只导出 CSV，图分析结果写到这里）
    out_dir = os.path.join(GT_DIR, '_attack_graphs')

    # 逐攻击场景验证
    global_found = 0
    global_total = 0
    for attack_name, gt_nodes in gt_files.items():
        found, missing, unique_edges = verify_one_attack(
            attack_name, gt_nodes, uuid2name, thread_map, src_index, dst_index
        )
        export_attack_graph(attack_name, found, unique_edges, out_dir)
        global_found += len(found)
        global_total += len(gt_nodes)

    # 总结
    print(f"\n{'='*80}")
    print("总结")
    print(f"{'='*80}")
    print(f"  Ground truth 总节点: {global_total}")
    print(f"  在提取数据中找到: {global_found} ({global_found/global_total*100:.1f}%)")
    print(f"  攻击图 CSV 导出到: {out_dir}")


if __name__ == '__main__':
    main()
