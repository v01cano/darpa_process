"""
FiveDirections E3 Ground Truth UUID 诊断脚本

目的：verify_ground_truth.py 的 UUID 匹配率是 0%，需要诊断原因。

流程：
1. 从三个 GT CSV 读取所有 UUID
2. 检查这些 UUID 在我们提取的 uuid2name.pkl / thread_to_process 中的存在情况
3. 扫描原始 CDM JSON，查找这些 UUID 在原始数据中的类型/字段位置
4. 输出报告：这些 GT UUID 究竟是 CDM 中的什么实体/记录

输出：
- 每个 GT UUID → 在原始数据中首次出现的记录类型 + 关键字段
- 汇总：GT UUID 对应的 CDM datum 类型分布（是否是被我们过滤掉的类型）
"""

import pickle
import csv
import ast
import os
import glob
import json
import gzip
from collections import defaultdict, Counter

EXTRACTED_DIR = "/mnt/disk/darpa/fivedirections_e3_20260423"
RAW_DIR = "/mnt/disk/darpa/fivedirections_e3"
GT_DIR = "/home/cch/CAPTAIN/CAPTAIN-main/cch_repeat/dataprocess/fivedirections_e3/pidsmaker_groundtruth"
OUT_DIR = "/home/cch/CAPTAIN/CAPTAIN-main/cch_repeat/dataprocess/fivedirections_e3/pidsmaker_groundtruth"


def load_all_gt_uuids():
    """返回 {lower_uuid: {'raw': original_cased, 'attack': fname, 'gt_type': .., 'gt_name': ..}}。
    同时保留大小写原样用于在原始 JSON 中搜索。"""
    gt = {}
    for fname in sorted(os.listdir(GT_DIR)):
        if not fname.endswith('.csv'):
            continue
        with open(os.path.join(GT_DIR, fname), 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 2:
                    continue
                raw_uuid = row[0].strip()
                try:
                    attrs = ast.literal_eval(row[1])
                except Exception:
                    attrs = {}
                gt_type = next(iter(attrs.keys()), 'unknown') if isinstance(attrs, dict) else 'unknown'
                gt_name = attrs.get(gt_type, '') if isinstance(attrs, dict) else ''
                gt[raw_uuid.lower()] = {
                    'raw': raw_uuid,
                    'attack': fname,
                    'gt_type': gt_type,
                    'gt_name': str(gt_name)[:80],
                }
    return gt


def check_in_extracted(gt):
    """检查 GT UUID 在我们提取文件中的覆盖情况。"""
    print(f"\n=== Step 1: 检查 GT UUID 是否在 uuid2name.pkl 中 ===")
    with open(os.path.join(EXTRACTED_DIR, 'uuid2name.pkl'), 'rb') as f:
        uuid2name = pickle.load(f)

    thread_map = {}
    cm = os.path.join(EXTRACTED_DIR, 'cmdlines.pkl')
    if os.path.exists(cm):
        with open(cm, 'rb') as f:
            data = pickle.load(f)
        thread_map = data.get('thread_to_process', {}) if isinstance(data, dict) else {}

    # 我们的 key 格式是什么？
    sample_keys = list(uuid2name.keys())[:5]
    print(f"  uuid2name 样本 key: {sample_keys}")
    print(f"  uuid2name 总量: {len(uuid2name):,}")
    print(f"  thread_map 总量: {len(thread_map):,}")

    found_direct = 0
    found_thread = 0
    missing = []
    for lu, info in gt.items():
        if lu in uuid2name:
            found_direct += 1
            info['status'] = 'uuid2name'
            info['our_type'] = uuid2name[lu][0]
            info['our_name'] = str(uuid2name[lu][1])[:80]
        elif lu in thread_map:
            parent = thread_map[lu]
            found_thread += 1
            info['status'] = 'thread_map→' + (uuid2name[parent][0] if parent in uuid2name else '?')
            info['our_type'] = uuid2name[parent][0] if parent in uuid2name else '?'
            info['our_name'] = str(uuid2name[parent][1])[:80] if parent in uuid2name else '?'
        else:
            info['status'] = 'MISSING'
            missing.append(lu)

    print(f"  直接命中 uuid2name: {found_direct}/{len(gt)}")
    print(f"  通过 thread_map 命中: {found_thread}/{len(gt)}")
    print(f"  完全缺失: {len(missing)}/{len(gt)}")
    return missing


def scan_raw_for_uuids(missing_lower_set, gt):
    """
    扫描原始 CDM JSON，查找每个缺失 UUID 在原始数据中的位置和类型。

    CDM datum 结构通常是:
      {"datum": {"<full.type.name.with.dots>": {"uuid": "...", ...}}}

    我们记录：
      - 第一次出现时的 CDM 类型（Subject / FileObject / NetFlowObject / Event / ...）
      - 如果是 Event，关键字段是 subject / predicateObject / predicateObject2 等
      - 是否作为"被引用"出现而从未定义自己（孤儿 UUID）
    """
    print(f"\n=== Step 2: 扫描原始 JSON，查找缺失 UUID 的来源 ===")
    # 同时保留大写和小写形式用于字符串匹配
    targets_lower = set(missing_lower_set)

    raw_files = sorted(glob.glob(os.path.join(RAW_DIR, '*.json')) +
                       glob.glob(os.path.join(RAW_DIR, '*.json.*')))
    print(f"  找到 {len(raw_files)} 个原始文件")
    if not raw_files:
        print(f"  [ERROR] {RAW_DIR} 下没有 JSON 文件，请确认路径。")
        return {}

    # uuid -> list of (cdm_type, role, filename, summary)
    uuid_occurrences = defaultdict(list)
    # role 可能是 'defined'（自身是主实体）或 'referenced_by_<event_field>'

    CDM_TYPES_CARE = [
        'Subject', 'FileObject', 'NetFlowObject', 'SrcSinkObject',
        'UnnamedPipeObject', 'RegistryKeyObject', 'MemoryObject',
        'IpcObject', 'PrincipalObject', 'Event', 'Host',
    ]

    for fidx, rf in enumerate(raw_files):
        found_in_this_file = 0
        opener = gzip.open if rf.endswith('.gz') else open
        try:
            with opener(rf, 'rt', errors='ignore') as f:
                for line_no, line in enumerate(f):
                    # 快速字符串过滤：line 里是否出现任一目标 UUID（保留大写小写都匹配）
                    # 为了速度，先判断小写副本是否包含 targets
                    ll = line.lower()
                    hit_any = False
                    hits = []
                    for t in targets_lower:
                        if t in ll:
                            hits.append(t)
                            hit_any = True
                    if not hit_any:
                        continue

                    # 真命中，解析 JSON
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    datum = rec.get('datum', {})
                    if not isinstance(datum, dict) or not datum:
                        continue
                    cdm_full_type = next(iter(datum.keys()))
                    cdm_type_short = cdm_full_type.split('.')[-1]
                    body = datum[cdm_full_type]
                    if not isinstance(body, dict):
                        continue

                    # 主实体 UUID
                    self_uuid = body.get('uuid')
                    if isinstance(self_uuid, str) and self_uuid.lower() in targets_lower:
                        summary = make_summary(cdm_type_short, body)
                        uuid_occurrences[self_uuid.lower()].append(
                            (cdm_type_short, 'defined', os.path.basename(rf), summary)
                        )
                        found_in_this_file += 1

                    # 如果是 Event，检查它引用的各种 UUID
                    if cdm_type_short == 'Event':
                        for field in ['subject', 'predicateObject', 'predicateObject2']:
                            ref = body.get(field)
                            if isinstance(ref, dict):
                                ref_uuid = ref.get('com.bbn.tc.schema.avro.cdm18.UUID') or ref.get('UUID')
                            else:
                                ref_uuid = ref
                            if isinstance(ref_uuid, str) and ref_uuid.lower() in targets_lower:
                                summary = make_summary(cdm_type_short, body)
                                uuid_occurrences[ref_uuid.lower()].append(
                                    (cdm_type_short, f'ref_by_event.{field}',
                                     os.path.basename(rf), summary)
                                )
                                found_in_this_file += 1

                    # 其它实体的常见引用字段（例如 Subject.parentSubject, Subject.localPrincipal 等）
                    for field_name, field_val in body.items():
                        if field_name in ('uuid',):
                            continue
                        if isinstance(field_val, dict):
                            r = (field_val.get('com.bbn.tc.schema.avro.cdm18.UUID')
                                 or field_val.get('UUID'))
                            if isinstance(r, str) and r.lower() in targets_lower:
                                uuid_occurrences[r.lower()].append(
                                    (cdm_type_short, f'ref_by_{field_name}',
                                     os.path.basename(rf), make_summary(cdm_type_short, body))
                                )
        except Exception as e:
            print(f"  [WARN] 读取 {rf} 出错: {e}")

        print(f"  [{fidx+1}/{len(raw_files)}] {os.path.basename(rf)}: 命中 {found_in_this_file} 条")

        # 提前终止：所有 UUID 都已找到 defined 记录
        defined_uuids = {u for u, occ in uuid_occurrences.items()
                         if any(r == 'defined' for _, r, _, _ in occ)}
        if defined_uuids >= targets_lower:
            print(f"  所有目标 UUID 都已找到 defined 记录，提前结束扫描")
            break

    return uuid_occurrences


def make_summary(cdm_type, body):
    """根据 CDM 类型提取关键字段。"""
    try:
        if cdm_type == 'Subject':
            return f"type={body.get('type')} cmdLine={str(body.get('cmdLine'))[:60]}"
        if cdm_type == 'FileObject':
            bo = body.get('baseObject', {}) or {}
            props = bo.get('properties', {}) or {}
            m = props.get('map', {}) if isinstance(props, dict) else {}
            return f"type={body.get('type')} path={str(m.get('path') or m.get('filename'))[:60]}"
        if cdm_type == 'NetFlowObject':
            return (f"{body.get('localAddress')}:{body.get('localPort')}->"
                    f"{body.get('remoteAddress')}:{body.get('remotePort')}")
        if cdm_type == 'RegistryKeyObject':
            return f"key={str(body.get('key'))[:60]}"
        if cdm_type == 'Event':
            return (f"type={body.get('type')} "
                    f"name={str(body.get('name'))[:30]} "
                    f"ts={body.get('timestampNanos')}")
        if cdm_type == 'SrcSinkObject':
            return f"type={body.get('type')}"
        return f"keys={list(body.keys())[:5]}"
    except Exception:
        return "?"


def report(gt, uuid_occurrences, missing_lower):
    """输出诊断报告。"""
    out_csv = os.path.join(OUT_DIR, 'gt_uuid_diagnosis.csv')
    summary_txt = os.path.join(OUT_DIR, 'gt_uuid_diagnosis_summary.txt')
    print(f"\n=== Step 3: 生成报告 ===")

    # CSV
    with open(out_csv, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['uuid_lower', 'attack', 'gt_type', 'gt_name',
                    'extracted_status', 'cdm_defined_type', 'cdm_occurrences',
                    'first_occurrence_summary'])
        for lu, info in gt.items():
            occ = uuid_occurrences.get(lu, [])
            defined = [o for o in occ if o[1] == 'defined']
            cdm_def_type = defined[0][0] if defined else ''
            first_summary = occ[0][3] if occ else ''
            w.writerow([
                lu, info.get('attack', ''),
                info.get('gt_type', ''), info.get('gt_name', ''),
                info.get('status', ''),
                cdm_def_type,
                len(occ),
                first_summary,
            ])

    # 汇总
    lines = []
    lines.append("=== GT UUID 诊断汇总 ===\n")
    lines.append(f"GT UUID 总数: {len(gt)}")
    lines.append(f"在 uuid2name 或 thread_map 命中: {len(gt) - len(missing_lower)}")
    lines.append(f"完全缺失: {len(missing_lower)}\n")

    # 缺失 UUID 在原始数据中的 CDM 类型分布
    cdm_type_counter = Counter()
    role_counter = Counter()
    orphan = []  # 在原始数据里完全找不到
    for lu in missing_lower:
        occ = uuid_occurrences.get(lu, [])
        if not occ:
            orphan.append(lu)
            continue
        defined = [o for o in occ if o[1] == 'defined']
        if defined:
            cdm_type_counter[defined[0][0]] += 1
        else:
            cdm_type_counter['NOT_DEFINED'] += 1
        for o in occ:
            role_counter[f"{o[0]}:{o[1]}"] += 1

    lines.append(f"--- 缺失 UUID 在原始数据中的 CDM 'defined' 类型分布 ---")
    for t, cnt in cdm_type_counter.most_common():
        lines.append(f"  {t:20s}: {cnt}")

    lines.append(f"\n--- 缺失 UUID 在原始数据中的出现 role 分布（前 20）---")
    for r, cnt in role_counter.most_common(20):
        lines.append(f"  {r:40s}: {cnt}")

    lines.append(f"\n--- 完全找不到的 UUID（孤儿）: {len(orphan)} ---")
    for u in orphan[:30]:
        info = gt.get(u, {})
        lines.append(f"  {u}  attack={info.get('attack')} gt_type={info.get('gt_type')} "
                     f"gt_name={info.get('gt_name')[:60]}")
    if len(orphan) > 30:
        lines.append(f"  ... 还有 {len(orphan)-30} 个")

    # 抽样：每种 CDM 类型取 3 个展示
    lines.append(f"\n--- 抽样（每种 CDM 类型前 3 个）---")
    by_cdm = defaultdict(list)
    for lu in missing_lower:
        occ = uuid_occurrences.get(lu, [])
        defined = [o for o in occ if o[1] == 'defined']
        key = defined[0][0] if defined else ('NOT_DEFINED' if not occ else 'REF_ONLY')
        by_cdm[key].append(lu)
    for cdm_t, lst in by_cdm.items():
        lines.append(f"\n  [{cdm_t}] 共 {len(lst)}")
        for u in lst[:3]:
            info = gt[u]
            occ = uuid_occurrences.get(u, [])
            lines.append(f"    {u}  gt_type={info['gt_type']} gt_name={info['gt_name'][:50]}")
            for o in occ[:3]:
                lines.append(f"      - {o[0]} / {o[1]} / {o[2]} / {o[3][:80]}")

    text = "\n".join(lines)
    print(text)
    with open(summary_txt, 'w') as f:
        f.write(text)

    print(f"\n  详细 CSV: {out_csv}")
    print(f"  汇总 TXT: {summary_txt}")


def main():
    gt = load_all_gt_uuids()
    print(f"加载 GT UUID: {len(gt)} 个")

    missing = check_in_extracted(gt)

    uuid_occurrences = scan_raw_for_uuids(set(missing), gt)

    report(gt, uuid_occurrences, missing)


if __name__ == '__main__':
    main()
