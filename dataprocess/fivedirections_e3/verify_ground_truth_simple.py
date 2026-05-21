"""
FiveDirections E3 Ground Truth 验证 — 简化版（不扫描 edges_part_*.pkl）

参照 CADETS/THEIA 的打印风格，重点展示：
  [类型] 我们=<name>   GT=<gt_name>   ✓/✗   UUID=...

还会补充 cmdLine（我们抽出的 subject_cmdline），便于判断提取的 name 是否
和 GT 一致。

流程：
1. 加载 uuid2name.pkl + thread_to_process + subject_cmdline
2. 读三个 GT CSV
3. 对每个 GT UUID：直接查 uuid2name → 回退 thread_to_process → 失败即缺失
4. 打印详情 + 命中统计 + 名称不一致列表
5. 导出 CSV 报告

**不扫 edges_part_*.pkl**，秒级完成。
"""

import pickle
import csv
import ast
import os
import re
from collections import Counter

EXTRACTED_DIR = "/mnt/disk/darpa/fivedirections_e3_20260423"
GT_DIR = "/home/cch/CAPTAIN/CAPTAIN-main/cch_repeat/dataprocess/fivedirections_e3/pidsmaker_groundtruth"
OUT_CSV = os.path.join(GT_DIR, 'gt_uuid_simple_report.csv')


def load_all():
    print("加载 uuid2name.pkl ...")
    with open(os.path.join(EXTRACTED_DIR, 'uuid2name.pkl'), 'rb') as f:
        u2n = pickle.load(f)
    u2n = {str(k).lower(): v for k, v in u2n.items()}
    print(f"  uuid2name: {len(u2n):,}")

    print("加载 cmdlines.pkl ...")
    with open(os.path.join(EXTRACTED_DIR, 'cmdlines.pkl'), 'rb') as f:
        cm = pickle.load(f)
    t2p = {str(k).lower(): str(v).lower() for k, v in cm.get('thread_to_process', {}).items()}
    sc = {str(k).lower(): v for k, v in cm.get('subject_cmdline', {}).items()}
    print(f"  thread_to_process: {len(t2p):,}")
    print(f"  subject_cmdline:   {len(sc):,}")
    return u2n, t2p, sc


def load_gt():
    gt_files = {}
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
        gt_files[attack] = nodes
        print(f"  {fname}: {len(nodes)} 个节点")
    return gt_files


def gt_type_to_our(gt_type):
    return 'process' if gt_type == 'process' else gt_type


def extract_exe_from_cmdline(cmd):
    """从 GT 的 'None <cmdLine>' 或原始 cmdLine 中提取 exe 名。"""
    if not cmd:
        return ''
    s = str(cmd)
    # 去掉 "None " 前缀
    if s.startswith('None '):
        s = s[5:]
    # 去引号和大括号
    s = s.strip().strip('"').strip("'").strip('{').strip('}')
    # 取第一个 token
    m = re.match(r'"?([^"\s]+\.exe)', s, re.IGNORECASE)
    if m:
        return os.path.basename(m.group(1)).lower()
    # fallback：第一个 token 的 basename
    first = s.split()[0] if s.split() else ''
    return os.path.basename(first).lower()


def name_match(our_name, gt_type, gt_name):
    """比较我们的 name 和 GT 的 name 是否一致。返回 (bool, 原因)。"""
    if not our_name or not gt_name:
        return False, 'empty'
    our_l = str(our_name).lower()
    gt_l = str(gt_name).lower()

    if gt_type == 'process':
        # GT 格式 "None <cmdline>" 或 "None None"
        if gt_name.strip() in ('None None', 'None'):
            return True, 'gt_empty_accepted'  # GT 本身无名，视作一致
        gt_exe = extract_exe_from_cmdline(gt_name)
        if gt_exe and gt_exe == our_l:
            return True, 'exe_match'
        if gt_exe and gt_exe in our_l:
            return True, 'exe_substring'
        return False, f'gt_exe={gt_exe}'
    elif gt_type == 'file':
        # GT 是 {"path"} 格式
        gt_clean = gt_l.strip('{}').strip('"').strip("'")
        # 取文件名部分
        tail = gt_clean.replace('\\\\', '\\').split('\\')[-1]
        if tail and tail in our_l.replace('\\\\', '\\').split('\\')[-1]:
            return True, 'filename_match'
        # 路径片段互相包含
        if gt_clean and (gt_clean in our_l or our_l in gt_clean):
            return True, 'path_contains'
        return False, 'path_diff'
    elif gt_type == 'netflow':
        # GT 是 "ip:port->ip:port"
        if gt_l == our_l:
            return True, 'exact'
        return False, 'netflow_diff'
    return False, 'unknown_type'


def main():
    u2n, t2p, sc = load_all()
    gt_files = load_gt()

    rows = []
    global_found = 0
    global_total = 0
    global_thread = 0
    global_name_mismatch = 0

    for attack, gt_nodes in gt_files.items():
        print(f"\n{'='*80}")
        print(f"攻击场景: {attack}")
        print(f"{'='*80}")
        print(f"Ground truth 节点数: {len(gt_nodes)}")

        gt_type_count = Counter(t for t, _ in gt_nodes.values())
        for t, cnt in gt_type_count.most_common():
            print(f"  {t}: {cnt}")

        # ---- 1. 匹配 ----
        print(f"\n--- 1. UUID 匹配验证 ---")
        found = []       # (uuid, resolved, gt_type, gt_name, our_type, our_name, cmdline, is_thread)
        missing = []
        name_mismatch = []

        for uuid, (gt_type, gt_name) in gt_nodes.items():
            resolved = None
            is_thread = False
            if uuid in u2n:
                resolved = uuid
            elif uuid in t2p:
                parent = t2p[uuid]
                if parent in u2n:
                    resolved = parent
                    is_thread = True

            if resolved:
                our_type, our_name = u2n[resolved]
                cmdline = sc.get(resolved, '') if our_type == 'process' else ''
                found.append((uuid, resolved, gt_type, gt_name, our_type,
                              str(our_name), str(cmdline), is_thread))

                # 类型匹配检查 + 名称一致性
                expected_our = gt_type_to_our(gt_type)
                ok, reason = name_match(our_name, gt_type, gt_name)
                if our_type != expected_our or not ok:
                    name_mismatch.append((uuid, gt_type, gt_name, our_type,
                                          str(our_name), reason))
            else:
                missing.append((uuid, gt_type, gt_name))

        hit = len(found)
        print(f"  找到: {hit} / {len(gt_nodes)} ({hit/len(gt_nodes)*100:.1f}%)")
        print(f"  其中 THREAD→PROCESS 映射: {sum(1 for f in found if f[7])}")
        print(f"  缺失: {len(missing)}")

        if missing:
            print(f"\n  缺失的节点:")
            for uuid, gt_type, gt_name in missing:
                print(f"    [{gt_type:8s}] GT={gt_name[:60]:60s}  UUID={uuid}")

        # 匹配详情（CADETS 风格）
        print(f"\n  匹配的节点详情:")
        print(f"  {'类型':<8}  {'✓/✗':<8} {'我们=name':<50} {'GT=name':<50} cmdLine")
        print("  " + "-" * 140)
        for uuid, resolved, gt_type, gt_name, our_type, our_name, cmdline, is_thread in found:
            expected_our = gt_type_to_our(gt_type)
            ok, reason = name_match(our_name, gt_type, gt_name)
            if our_type != expected_our:
                mark = f"✗类型({gt_type}→{our_type})"
            elif ok:
                mark = "✓"
            else:
                mark = "✗名称"
            tag = '[T]' if is_thread else '   '
            our_s = f"{tag}{our_name[:46]}"
            gt_s = gt_name[:48]
            cmd_s = cmdline[:60] if cmdline else ''
            print(f"  [{our_type:<6}]{mark:<8} 我们={our_s:<50} GT={gt_s:<50} {cmd_s}")

        # 名称不一致汇总
        if name_mismatch:
            print(f"\n  类型或名称不一致 ({len(name_mismatch)} 个):")
            for uuid, gt_type, gt_name, our_type, our_name, reason in name_mismatch:
                print(f"    UUID={uuid}")
                print(f"      GT  [{gt_type:<8}]  {gt_name[:100]}")
                print(f"      我们[{our_type:<8}]  {our_name[:100]}  (reason={reason})")

        # 攻击场景累计
        global_total += len(gt_nodes)
        global_found += hit
        global_thread += sum(1 for f in found if f[7])
        global_name_mismatch += len(name_mismatch)

        # 累积到 CSV 行
        for uuid, resolved, gt_type, gt_name, our_type, our_name, cmdline, is_thread in found:
            expected_our = gt_type_to_our(gt_type)
            ok, reason = name_match(our_name, gt_type, gt_name)
            rows.append({
                'attack': attack,
                'gt_uuid': uuid,
                'status': 'thread→process' if is_thread else 'direct',
                'gt_type': gt_type,
                'gt_name': gt_name[:300],
                'our_type': our_type,
                'our_name': our_name[:300],
                'our_cmdline': cmdline[:300],
                'type_match': int(our_type == expected_our),
                'name_match': int(ok),
                'mismatch_reason': '' if ok else reason,
                'resolved_uuid': resolved,
            })
        for uuid, gt_type, gt_name in missing:
            rows.append({
                'attack': attack,
                'gt_uuid': uuid,
                'status': 'MISS',
                'gt_type': gt_type,
                'gt_name': gt_name[:300],
                'our_type': '',
                'our_name': '',
                'our_cmdline': '',
                'type_match': 0,
                'name_match': 0,
                'mismatch_reason': 'missing',
                'resolved_uuid': '',
            })

    # ---- 全局汇总 ----
    print(f"\n{'='*80}\n总结\n{'='*80}")
    print(f"  Ground truth 总节点: {global_total}")
    print(f"  命中: {global_found} ({global_found/global_total*100:.1f}%)")
    print(f"    其中 THREAD→PROCESS: {global_thread}")
    print(f"  类型或名称不一致: {global_name_mismatch}")
    print(f"  缺失: {global_total - global_found}")

    # 写 CSV
    if rows:
        with open(OUT_CSV, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n详细报告: {OUT_CSV}")


if __name__ == '__main__':
    main()
