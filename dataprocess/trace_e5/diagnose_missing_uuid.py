"""
TRACE E5 缺失 UUID 诊断脚本

针对 verify_ground_truth_simple.py 的 missing 列表，回到原始 JSON 中查找
这些 UUID 真实定义在哪里、被什么类型记录、为什么被我们的提取脚本丢弃。

用法：
  python diagnose_missing_uuid.py
  # 或者指定 UUID（小写）
  python diagnose_missing_uuid.py 68c0ed0e-d3e2-... 5165bcdd-fa7b-...

输出：
  - 每个 UUID 第一次出现时的 CDM 类型、关键字段、所属角色
  - 是否作为 Event.subject / predicateObject / predObj2 被引用
  - 给出"为什么被丢弃"的诊断结论
"""

import json
import os
import sys
from collections import Counter, defaultdict

# 默认要查的 3 个 UUID（来自 Trace_Firefox_Drakon GT 的 missing）
DEFAULT_TARGETS = [
    '68c0ed0e-d3e2-765d-54a8-9e7f16355095',  # /dev/null
    '5165bcdd-fa7b-9008-c5b2-792825f594f7',  # /dev/urandom
    '6c7a9e96-e2a1-1287-df73-183f0ee5b82d',  # firefox process
]

# 提取脚本里被我们"接受"的类型，方便判断"为什么丢"
FILE_OBJECT_KEEP = {'FILE_OBJECT_FILE', 'FILE_OBJECT_DIR', 'FILE_OBJECT_LINK'}

# 与 extract_trace_e5.py 保持一致
SUBJECT_KEEP = {'SUBJECT_PROCESS'}


def explain_drop(short_type, body):
    """根据 record 给出我们脚本丢弃它的原因。"""
    if short_type == 'Subject':
        stype = body.get('type')
        if stype in SUBJECT_KEEP:
            return f"✓ 应该保留 (SUBJECT_PROCESS)"
        return f"✗ 丢弃: SUBJECT 类型 = {stype}（脚本只保留 SUBJECT_PROCESS）"
    if short_type == 'FileObject':
        ftype = body.get('type')
        if ftype in FILE_OBJECT_KEEP:
            return f"✓ 应该保留 (FileObject {ftype})"
        return f"✗ 丢弃: FileObject 类型 = {ftype}（脚本只保留 FILE/DIR/LINK）"
    if short_type == 'NetFlowObject':
        return f"✓ 应该保留 (NetFlowObject)"
    return f"✗ 丢弃: 整个 datum 类型 = {short_type}（不在 3 种节点之列）"


def summarize_body(short_type, body):
    """提取核心字段做样本展示。"""
    if short_type == 'Subject':
        cmd = body.get('cmdLine')
        if isinstance(cmd, dict):
            cmd = cmd.get('string')
        pmap = (body.get('properties') or {}).get('map') or {}
        return (
            f"type={body.get('type')}  cid={body.get('cid')}  "
            f"name={pmap.get('name')!r}  cmdLine={str(cmd)[:80]!r}"
        )
    if short_type == 'FileObject':
        ftype = body.get('type')
        base = body.get('baseObject') or {}
        bmap = (base.get('properties') or {}).get('map') or {}
        return f"type={ftype}  path={bmap.get('path')!r}  filename={bmap.get('filename')!r}"
    if short_type == 'NetFlowObject':
        la = body.get('localAddress')
        ra = body.get('remoteAddress')
        return f"la={la}  lp={body.get('localPort')}  ra={ra}  rp={body.get('remotePort')}"
    if short_type == 'Event':
        return f"event_type={body.get('type')}  ts={body.get('timestampNanos')}"
    return f"keys={list(body.keys())[:8]}"


def scan_file(filepath, targets_lower, results):
    """扫一个 JSON 文件。targets_lower 是 set，results 是 dict[uuid] -> list of dicts。"""
    found = 0
    with open(filepath, 'r', errors='ignore') as f:
        for line_no, line in enumerate(f):
            # 快速字符串筛选
            line_l = line.lower()
            hits = [t for t in targets_lower if t in line_l]
            if not hits:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            datum = rec.get('datum')
            if not isinstance(datum, dict) or not datum:
                continue
            full_type = next(iter(datum.keys()))
            body = datum[full_type]
            if not isinstance(body, dict):
                continue
            short_type = full_type.rsplit('.', 1)[-1]

            # 主 uuid（自身）
            self_uuid = body.get('uuid')
            if isinstance(self_uuid, str) and self_uuid.lower() in targets_lower:
                results[self_uuid.lower()].append({
                    'role': 'defined',
                    'cdm_type': short_type,
                    'file': os.path.basename(filepath),
                    'line': line_no,
                    'summary': summarize_body(short_type, body),
                    'drop_reason': explain_drop(short_type, body),
                })
                found += 1

            # Event 中引用的 uuid
            if short_type == 'Event':
                for field in ('subject', 'predicateObject', 'predicateObject2'):
                    v = body.get(field)
                    if isinstance(v, dict):
                        ref = (v.get('com.bbn.tc.schema.avro.cdm20.UUID')
                               or v.get('com.bbn.tc.schema.avro.cdm18.UUID')
                               or v.get('UUID'))
                        if isinstance(ref, str) and ref.lower() in targets_lower:
                            results[ref.lower()].append({
                                'role': f'ref_by_event.{field}',
                                'cdm_type': 'Event',
                                'file': os.path.basename(filepath),
                                'line': line_no,
                                'summary': summarize_body('Event', body),
                                'drop_reason': None,
                            })
                            found += 1

            # 其它实体里的 parentSubject 等引用
            for field_name, field_val in body.items():
                if field_name == 'uuid':
                    continue
                if isinstance(field_val, dict):
                    r = (field_val.get('com.bbn.tc.schema.avro.cdm20.UUID')
                         or field_val.get('com.bbn.tc.schema.avro.cdm18.UUID')
                         or field_val.get('UUID'))
                    if isinstance(r, str) and r.lower() in targets_lower:
                        results[r.lower()].append({
                            'role': f'ref_by_{short_type}.{field_name}',
                            'cdm_type': short_type,
                            'file': os.path.basename(filepath),
                            'line': line_no,
                            'summary': summarize_body(short_type, body),
                            'drop_reason': None,
                        })
                        found += 1
    return found


def list_input_files(input_dir):
    """复用 extract_trace_e5 的文件列表。"""
    from extract_trace_e5 import DEFAULT_FILE_LIST
    files = [os.path.join(input_dir, f) for f in DEFAULT_FILE_LIST]
    return [f for f in files if os.path.exists(f)]


def main():
    if len(sys.argv) > 1:
        targets = [t.strip().lower() for t in sys.argv[1:]]
    else:
        targets = [t.lower() for t in DEFAULT_TARGETS]

    print(f"待诊断 UUID ({len(targets)} 个):")
    for t in targets:
        print(f"  {t}")

    input_dir = '/mnt/disk1/darpa_e5/trace/all38tjson'
    print(f"\n扫描 {input_dir} ...")

    targets_set = set(targets)
    results = defaultdict(list)
    files = list_input_files(input_dir)
    print(f"  共 {len(files)} 个文件")

    # 提前终止条件：每个 target 都已找到 'defined' 记录
    defined_set = set()
    total_hits = 0
    for idx, fp in enumerate(files):
        n = scan_file(fp, targets_set, results)
        total_hits += n
        # 检查是否所有目标都有 defined
        for u, recs in results.items():
            if any(r['role'] == 'defined' for r in recs):
                defined_set.add(u)
        if idx % 50 == 0 or n > 0:
            print(f"  [{idx+1}/{len(files)}] {os.path.basename(fp)}: 命中 {n}, "
                  f"已 defined: {len(defined_set)}/{len(targets_set)}, 累计 {total_hits}")
        if defined_set >= targets_set:
            print(f"  → 所有目标都已找到 defined 记录，提前结束")
            break

    print(f"\n{'='*80}\n诊断报告\n{'='*80}")
    for u in targets:
        recs = results.get(u, [])
        print(f"\n--- UUID: {u} ---")
        if not recs:
            print(f"  原始数据中**完全找不到**该 UUID（孤儿）")
            continue

        # defined 记录
        defined_recs = [r for r in recs if r['role'] == 'defined']
        if defined_recs:
            r = defined_recs[0]
            print(f"  [DEFINED] {r['cdm_type']}  ({r['file']}:{r['line']})")
            print(f"    summary    : {r['summary']}")
            print(f"    drop_reason: {r['drop_reason']}")
            if len(defined_recs) > 1:
                print(f"    (共 {len(defined_recs)} 个 defined 记录，可能重复定义)")
        else:
            print(f"  [NOT DEFINED] 没有作为主实体定义，仅被 Event/parent 引用")

        # 被引用情况
        ref_recs = [r for r in recs if r['role'] != 'defined']
        if ref_recs:
            roles = Counter(r['role'] for r in ref_recs)
            print(f"  被引用 {len(ref_recs)} 次:")
            for role, cnt in roles.most_common(8):
                print(f"    {role}: {cnt}")
            # 显示前 3 个引用样本
            print(f"  引用样本:")
            for r in ref_recs[:3]:
                print(f"    [{r['role']}] {r['summary'][:120]}")


if __name__ == '__main__':
    main()
