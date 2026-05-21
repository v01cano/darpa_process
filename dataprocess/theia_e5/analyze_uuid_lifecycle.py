"""
THEIA E5 UUID 生命周期分析

参照 cadets_e5/analyze_uuid_lifecycle.py。

重点验证：
  1. Subject 类型分布（PROCESS / THREAD / UNIT）
  2. UUID 大小写
  3. parentSubject 链
  4. FORK / CLONE / EXECUTE 数量 + 模型识别：
     - THEIA E3：fork + exec 分离，EXECUTE 不造新 UUID（同 CADETS）
     - 本数据集是否仍是该模型？
  5. EXECUTE 中的 subject UUID 是否在 Subject 表已定义
"""

import json
import sys
from collections import Counter


def analyze(filepaths, max_lines=None):
    print(f"分析文件: {filepaths}")
    print("=" * 80)

    subject_type_counter = Counter()
    subject_total = 0
    process_uuids = set()
    thread_uuids = set()
    unit_uuids = set()
    parent_links = []
    parent_link_count = 0
    cmdline_by_uuid = {}

    uuid_case_counter = Counter()

    event_type_counter = Counter()
    fork_pairs = []
    execute_records = []

    subject_first_seen = {}
    execute_subject_uuids = set()

    total = 0
    for fp in filepaths:
        print(f"\n  扫描 {fp} ...")
        with open(fp, 'r') as f:
            for line_no, line in enumerate(f, 1):
                total += 1
                if total % 500000 == 0:
                    print(f"    已扫描 {total:,} 行...")
                if max_lines and line_no > max_lines:
                    break
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                d = rec.get('datum')
                if not isinstance(d, dict) or not d:
                    continue
                full_type = next(iter(d.keys()))
                body = d[full_type]
                if not isinstance(body, dict):
                    continue
                short = full_type.rsplit('.', 1)[-1]

                if short == 'Subject':
                    subject_total += 1
                    stype = body.get('type', 'UNKNOWN')
                    subject_type_counter[stype] += 1
                    uuid = body.get('uuid')
                    if isinstance(uuid, str):
                        letters = [c for c in uuid if c.isalpha()]
                        if not letters:
                            uuid_case_counter['no_letters'] += 1
                        elif all(c.isupper() for c in letters):
                            uuid_case_counter['all_upper'] += 1
                        elif all(c.islower() for c in letters):
                            uuid_case_counter['all_lower'] += 1
                        else:
                            uuid_case_counter['mixed'] += 1

                        if stype == 'SUBJECT_PROCESS':
                            process_uuids.add(uuid)
                        elif stype == 'SUBJECT_THREAD':
                            thread_uuids.add(uuid)
                        elif stype == 'SUBJECT_UNIT':
                            unit_uuids.add(uuid)

                    parent = body.get('parentSubject')
                    if isinstance(parent, dict):
                        pu = (parent.get('com.bbn.tc.schema.avro.cdm20.UUID')
                              or parent.get('com.bbn.tc.schema.avro.cdm18.UUID')
                              or parent.get('UUID'))
                        if pu:
                            parent_link_count += 1
                            if len(parent_links) < 50:
                                parent_links.append((uuid, pu))

                    cmd = body.get('cmdLine')
                    cmd_str = None
                    if isinstance(cmd, str):
                        cmd_str = cmd
                    elif isinstance(cmd, dict):
                        cmd_str = cmd.get('string')
                    if cmd_str and isinstance(uuid, str) and len(cmdline_by_uuid) < 30:
                        cmdline_by_uuid[uuid] = cmd_str

                elif short == 'Event':
                    etype = body.get('type', 'UNKNOWN')
                    event_type_counter[etype] += 1

                    subj = body.get('subject')
                    su = (subj.get('com.bbn.tc.schema.avro.cdm20.UUID')
                          if isinstance(subj, dict) else None)
                    if su:
                        if su not in subject_first_seen:
                            subject_first_seen[su] = body.get('timestampNanos', 0)

                    if etype in ('EVENT_FORK', 'EVENT_CLONE', 'EVENT_EXECUTE'):
                        po = body.get('predicateObject')
                        pu = (po.get('com.bbn.tc.schema.avro.cdm20.UUID')
                              if isinstance(po, dict) else None)
                        if etype in ('EVENT_FORK', 'EVENT_CLONE'):
                            if su and pu and len(fork_pairs) < 30:
                                fork_pairs.append((etype, su, pu))
                        else:
                            if su:
                                execute_subject_uuids.add(su)
                            if su and pu and len(execute_records) < 30:
                                execute_records.append((su, pu, body.get('predicateObjectPath')))

    print(f"\n总扫描行数: {total:,}")

    print(f"\n=== Subject 类型分布 ===")
    for st, cnt in subject_type_counter.most_common():
        print(f"  {st:30s} {cnt:>10,}")

    print(f"\n=== UUID 数量 ===")
    print(f"  PROCESS UUIDs: {len(process_uuids):,}")
    print(f"  THREAD  UUIDs: {len(thread_uuids):,}")
    print(f"  UNIT    UUIDs: {len(unit_uuids):,}")
    print(f"  PROCESS ∩ THREAD: {len(process_uuids & thread_uuids):,}")

    print(f"\n=== UUID 大小写分布 ===")
    for c, n in uuid_case_counter.most_common():
        print(f"  {c:15s} {n:>10,}")

    print(f"\n=== parentSubject 链 ===")
    print(f"  有 parent 的 Subject: {parent_link_count:,} / {subject_total:,}")
    print(f"  样本（child → parent）:")
    for c, p in parent_links[:10]:
        print(f"    {c} → {p}")

    print(f"\n=== cmdLine 样本 ===")
    for u, cmd in list(cmdline_by_uuid.items())[:15]:
        print(f"  {u}  cmd={str(cmd)[:80]}")

    print(f"\n=== 事件类型 top 30 ===")
    for et, cnt in event_type_counter.most_common(30):
        print(f"  {et:35s} {cnt:>12,}")

    fork_total = (event_type_counter.get('EVENT_FORK', 0)
                  + event_type_counter.get('EVENT_CLONE', 0))
    exec_total = event_type_counter.get('EVENT_EXECUTE', 0)
    print(f"\n=== FORK / EXECUTE ===")
    print(f"  EVENT_FORK + EVENT_CLONE: {fork_total:,}")
    print(f"  EVENT_EXECUTE:            {exec_total:,}")
    print(f"  Distinct subject UUIDs in EXECUTE: {len(execute_subject_uuids):,}")

    print(f"\n=== FORK / CLONE 样本 ===")
    for et, su, pu in fork_pairs[:10]:
        print(f"  [{et}] subject={su}  predObj={pu}")

    print(f"\n=== EXECUTE 样本 ===")
    for su, pu, pop in execute_records[:10]:
        path = pop.get('string') if isinstance(pop, dict) else pop
        print(f"  subject={su}  predObj={pu}  path={path}")

    if execute_subject_uuids:
        defined_in_subj = sum(1 for u in execute_subject_uuids if u in process_uuids)
        print(f"\n=== EXECUTE subject UUID 是否定义为 SUBJECT_PROCESS ===")
        print(f"  EXECUTE 中独立 subject UUID: {len(execute_subject_uuids):,}")
        print(f"    其中已定义为 PROCESS: {defined_in_subj:,}")
        print(f"    其中未定义:           {len(execute_subject_uuids) - defined_in_subj:,}")
        print(f"  → 若大量未定义 → TRACE 风格新 UUID")
        print(f"  → 若 100% 已定义 → 标准 fork+exec 分离（同 THEIA E3 / CADETS）")


def _default_files():
    prefix = "/mnt/disk/darpa/cch_refine/theia_e5_json/"
    return [
        prefix + 'ta1-theia-1-e5-official-1.json',
        prefix + 'ta1-theia-1-e5-official-1.json.1',
        prefix + 'ta1-theia-1-e5-official-1.json.2',
    ]


if __name__ == '__main__':
    if len(sys.argv) < 2:
        files = _default_files()
    else:
        files = sys.argv[1:]
    analyze(files)
