"""
TRACE E5 实体与事件关系分析（参照 trace_e3 / cadets_e5 / theia_e5）。

输出每种 Event.type 引用的 subject / predicateObject 类型分布，以及
predicateObjectPath / cmdLine / exec 携带情况，指导提取脚本设计。
"""

import json
import sys
from collections import Counter, defaultdict


def analyze(filepaths, max_lines=None):
    print(f"分析文件: {filepaths}")
    print("=" * 80)

    uuid_to_type = {}

    print("\nPass 1: 收集 uuid → 类型 ...")
    total = 0
    for fp in filepaths:
        with open(fp, 'r') as f:
            for line in f:
                total += 1
                if total % 1000000 == 0:
                    print(f"  Pass1 已扫描 {total:,} 行 (uuid map={len(uuid_to_type):,})")
                if max_lines and total > max_lines:
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
                if short == 'Event':
                    continue
                u = body.get('uuid')
                if isinstance(u, str):
                    if short == 'Subject':
                        uuid_to_type[u] = body.get('type', 'SUBJECT_UNKNOWN')
                    elif short == 'FileObject':
                        uuid_to_type[u] = f"File:{body.get('type', 'UNKNOWN')}"
                    elif short == 'NetFlowObject':
                        uuid_to_type[u] = 'NetFlowObject'
                    elif short == 'SrcSinkObject':
                        uuid_to_type[u] = f"SrcSink:{body.get('type', 'UNKNOWN')}"
                    elif short == 'IpcObject':
                        uuid_to_type[u] = 'IpcObject'
                    elif short == 'UnnamedPipeObject':
                        uuid_to_type[u] = 'UnnamedPipeObject'
                    elif short == 'MemoryObject':
                        uuid_to_type[u] = 'MemoryObject'
                    elif short == 'RegistryKeyObject':
                        uuid_to_type[u] = 'RegistryKeyObject'
                    elif short == 'Principal':
                        uuid_to_type[u] = 'Principal'
                    elif short == 'Host':
                        uuid_to_type[u] = 'Host'
                    else:
                        uuid_to_type[u] = short
        if max_lines and total > max_lines:
            break

    print(f"  Pass1 完成: {len(uuid_to_type):,} 个 uuid")

    print("\nPass 2: 分析 Event ...")
    subj_dist = defaultdict(Counter)
    po_dist = defaultdict(Counter)
    po2_dist = defaultdict(Counter)
    et_pop = Counter()
    et_cmd = Counter()
    et_exec = Counter()
    et_total = Counter()
    et_pop_samples = defaultdict(list)
    et_cmd_samples = defaultdict(list)
    et_exec_samples = defaultdict(list)

    total2 = 0
    for fp in filepaths:
        with open(fp, 'r') as f:
            for line in f:
                total2 += 1
                if total2 % 1000000 == 0:
                    print(f"  Pass2 已扫描 {total2:,} 行")
                if max_lines and total2 > max_lines:
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
                if full_type.rsplit('.', 1)[-1] != 'Event':
                    continue

                etype = body.get('type', 'UNKNOWN')
                et_total[etype] += 1

                def ref_uuid(field):
                    v = body.get(field)
                    if isinstance(v, dict):
                        return (v.get('com.bbn.tc.schema.avro.cdm20.UUID')
                                or v.get('com.bbn.tc.schema.avro.cdm18.UUID')
                                or v.get('UUID'))
                    return None

                su = ref_uuid('subject')
                pu = ref_uuid('predicateObject')
                pu2 = ref_uuid('predicateObject2')

                if su:
                    subj_dist[etype][uuid_to_type.get(su, '?MISSING')] += 1
                if pu:
                    po_dist[etype][uuid_to_type.get(pu, '?MISSING')] += 1
                if pu2:
                    po2_dist[etype][uuid_to_type.get(pu2, '?MISSING')] += 1

                pop = body.get('predicateObjectPath')
                pop_str = pop.get('string') if isinstance(pop, dict) else None
                if pop_str:
                    et_pop[etype] += 1
                    if len(et_pop_samples[etype]) < 3:
                        et_pop_samples[etype].append(pop_str)

                raw_props = body.get('properties')
                pmap = raw_props.get('map', {}) if isinstance(raw_props, dict) else {}
                if isinstance(pmap, dict):
                    if 'cmdLine' in pmap:
                        et_cmd[etype] += 1
                        if len(et_cmd_samples[etype]) < 3:
                            et_cmd_samples[etype].append(pmap['cmdLine'])
                    if 'exec' in pmap:
                        et_exec[etype] += 1
                        if len(et_exec_samples[etype]) < 3:
                            et_exec_samples[etype].append(pmap['exec'])
        if max_lines and total2 > max_lines:
            break

    print(f"\n{'='*80}\nEvent 类型的 subject / predicateObject 类型分布\n{'='*80}")
    for etype, total_cnt in et_total.most_common():
        print(f"\n[{etype}]  共 {total_cnt:,}")
        s = subj_dist.get(etype, Counter())
        p = po_dist.get(etype, Counter())
        p2 = po2_dist.get(etype, Counter())
        print(f"  subject 类型:")
        for t, c in s.most_common(8):
            print(f"    {t:35s} {c:>10,}")
        if p:
            print(f"  predicateObject 类型:")
            for t, c in p.most_common(8):
                print(f"    {t:35s} {c:>10,}")
        if p2:
            print(f"  predicateObject2 类型:")
            for t, c in p2.most_common(8):
                print(f"    {t:35s} {c:>10,}")

        pop_cnt = et_pop.get(etype, 0)
        cmd_cnt = et_cmd.get(etype, 0)
        exec_cnt = et_exec.get(etype, 0)
        if pop_cnt or cmd_cnt or exec_cnt:
            print(f"  字段附加: predObjPath={pop_cnt:,}  cmdLine={cmd_cnt:,}  exec={exec_cnt:,}")
        if et_pop_samples.get(etype):
            print(f"    path 样本: {et_pop_samples[etype]}")
        if et_cmd_samples.get(etype):
            print(f"    cmdLine 样本: {[str(c)[:80] for c in et_cmd_samples[etype]]}")
        if et_exec_samples.get(etype):
            print(f"    exec 样本: {[str(c)[:60] for c in et_exec_samples[etype]]}")


def _default_files():
    prefix = "/mnt/disk1/darpa_e5/trace/example/"
    return [
        prefix + 'ta1-trace-1-e5-official-1.bin.1.json',
        prefix + 'ta1-trace-1-e5-official-1.bin.1.json.1',
    ]


if __name__ == '__main__':
    if len(sys.argv) < 2:
        files = _default_files()
    else:
        files = sys.argv[1:]
    analyze(files)
