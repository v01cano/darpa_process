"""
CADETS E5 CDM20 数据结构分析脚本

参照 clearscope_e5/analyze_cdm_structure.py。

E5 vs E3 待验证关键点：
  - CDM20 命名空间（vs E3 的 cdm18）
  - Subject 字段是否还是全空（E3 是 0%）
  - 进程命名来源：Event.exec 还在？或改用 cmdLine？
  - 文件路径：predicateObjectPath 还在？或改放到其他字段？
  - NetFlow 地址：是否改为 dict（同 ClearScope E5 / TRACE E5）
  - UUID 大小写：是否改为大写

用法：
  python analyze_cdm_structure.py /mnt/disk/darpa/cch_refine/cadets_e5_json/ta1-cadets-1-e5-official-2.bin.json
"""

import json
import sys
from collections import Counter


def analyze_files(filepaths, max_lines=None):
    print(f"分析文件: {filepaths}")
    if max_lines:
        print(f"  限制每个文件最多 {max_lines:,} 行")
    print("=" * 80)

    record_type_counter = Counter()
    schema_namespace_counter = Counter()

    # Subject
    subject_count = 0
    subject_type_counter = Counter()
    subject_has_props_path = 0
    subject_has_props_name = 0
    subject_has_cmdline = 0
    subject_cmdline_is_str = 0
    subject_cmdline_is_dict = 0
    subject_cmdline_is_null = 0
    subject_has_tgid = 0
    subject_has_ppid = 0
    subject_has_parent = 0
    subject_has_cid = 0
    subject_properties_keys = Counter()
    subject_toplevel_keys = Counter()
    subject_path_samples = []
    subject_name_samples = []
    subject_cmdline_samples = []

    # FileObject
    file_count = 0
    file_type_counter = Counter()
    file_has_filename = 0
    file_has_path = 0
    file_has_name = 0
    file_base_props_keys = Counter()
    file_filename_samples = []
    file_path_samples = []

    # NetFlowObject
    net_count = 0
    net_has_local_addr = 0
    net_has_remote_addr = 0
    net_local_addr_type = Counter()
    net_remote_addr_type = Counter()
    net_samples = []

    # Event
    event_count = 0
    event_type_counter = Counter()
    event_has_subject = 0
    event_has_predicate_object = 0
    event_has_predicate_object2 = 0
    event_has_predicate_object_path = 0
    event_has_predicate_object2_path = 0
    event_has_exec = 0
    event_has_cmdline = 0
    event_has_name = 0
    event_properties_keys = Counter()
    event_exec_samples = []
    event_cmdline_samples = []
    event_name_samples = []
    event_type_has_exec = Counter()
    event_type_has_cmdline = Counter()
    event_type_has_pred_obj = Counter()
    event_type_has_pred_obj_path = Counter()

    # UUID 大小写
    uuid_case_counter = Counter()

    other_record_types = Counter()
    other_record_samples = {}

    total = 0
    for fp in filepaths:
        print(f"\n  扫描 {fp} ...")
        in_file = 0
        with open(fp, 'r') as fin:
            for line in fin:
                in_file += 1
                total += 1
                if total % 500000 == 0:
                    print(f"    已扫描 {total:,} 行...")
                if max_lines and in_file > max_lines:
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

                parts = full_type.rsplit('.', 1)
                if len(parts) == 2:
                    schema_namespace_counter[parts[0]] += 1
                    record_type = parts[1]
                else:
                    record_type = full_type
                record_type_counter[record_type] += 1

                # UUID 大小写
                u = body.get('uuid')
                if isinstance(u, str):
                    letters = [c for c in u if c.isalpha()]
                    if not letters:
                        uuid_case_counter['no_letters'] += 1
                    elif all(c.isupper() for c in letters):
                        uuid_case_counter['all_upper'] += 1
                    elif all(c.islower() for c in letters):
                        uuid_case_counter['all_lower'] += 1
                    else:
                        uuid_case_counter['mixed'] += 1

                if record_type == 'Subject':
                    subject_count += 1
                    subject_type_counter[body.get('type', 'UNKNOWN')] += 1
                    for k in body.keys():
                        subject_toplevel_keys[k] += 1

                    props = body.get('properties')
                    if isinstance(props, dict):
                        pmap = props.get('map', {})
                        if isinstance(pmap, dict):
                            for k in pmap:
                                subject_properties_keys[k] += 1
                            if pmap.get('path') is not None:
                                subject_has_props_path += 1
                                if len(subject_path_samples) < 10:
                                    subject_path_samples.append(pmap['path'])
                            if pmap.get('name') is not None:
                                subject_has_props_name += 1
                                if len(subject_name_samples) < 10:
                                    subject_name_samples.append(pmap['name'])
                            if pmap.get('tgid') is not None:
                                subject_has_tgid += 1
                            if pmap.get('ppid') is not None:
                                subject_has_ppid += 1

                    if body.get('cid') is not None:
                        subject_has_cid += 1
                    if body.get('parentSubject') is not None:
                        subject_has_parent += 1

                    cmd = body.get('cmdLine')
                    if cmd is None:
                        subject_cmdline_is_null += 1
                    elif isinstance(cmd, str):
                        subject_has_cmdline += 1
                        subject_cmdline_is_str += 1
                        if len(subject_cmdline_samples) < 10:
                            subject_cmdline_samples.append(cmd)
                    elif isinstance(cmd, dict):
                        v = cmd.get('string')
                        if v:
                            subject_has_cmdline += 1
                            subject_cmdline_is_dict += 1
                            if len(subject_cmdline_samples) < 10:
                                subject_cmdline_samples.append(v)
                        else:
                            subject_cmdline_is_null += 1

                elif record_type == 'FileObject':
                    file_count += 1
                    file_type_counter[body.get('type', 'UNKNOWN')] += 1
                    base = body.get('baseObject', {}) or {}
                    base_props = base.get('properties', {}) or {}
                    base_map = base_props.get('map', {}) if isinstance(base_props, dict) else {}
                    if isinstance(base_map, dict):
                        for k in base_map:
                            file_base_props_keys[k] += 1
                        if base_map.get('filename') is not None:
                            file_has_filename += 1
                            if len(file_filename_samples) < 10:
                                file_filename_samples.append(base_map['filename'])
                        if base_map.get('path') is not None:
                            file_has_path += 1
                            if len(file_path_samples) < 10:
                                file_path_samples.append(base_map['path'])
                        if base_map.get('name') is not None:
                            file_has_name += 1

                elif record_type == 'NetFlowObject':
                    net_count += 1
                    la = body.get('localAddress')
                    ra = body.get('remoteAddress')
                    if la is not None:
                        net_has_local_addr += 1
                        net_local_addr_type[type(la).__name__] += 1
                    if ra is not None:
                        net_has_remote_addr += 1
                        net_remote_addr_type[type(ra).__name__] += 1
                    if len(net_samples) < 5:
                        net_samples.append({
                            'la': la, 'lp': body.get('localPort'),
                            'ra': ra, 'rp': body.get('remotePort'),
                        })

                elif record_type == 'Event':
                    event_count += 1
                    etype = body.get('type', 'UNKNOWN')
                    event_type_counter[etype] += 1

                    has_subj = isinstance(body.get('subject'), dict)
                    has_po = isinstance(body.get('predicateObject'), dict)
                    has_po2 = isinstance(body.get('predicateObject2'), dict)
                    has_pop = isinstance(body.get('predicateObjectPath'), dict)
                    has_po2p = isinstance(body.get('predicateObject2Path'), dict)
                    if has_subj: event_has_subject += 1
                    if has_po:
                        event_has_predicate_object += 1
                        event_type_has_pred_obj[etype] += 1
                    if has_po2: event_has_predicate_object2 += 1
                    if has_pop:
                        event_has_predicate_object_path += 1
                        event_type_has_pred_obj_path[etype] += 1
                    if has_po2p: event_has_predicate_object2_path += 1

                    if isinstance(body.get('name'), dict):
                        event_has_name += 1
                        if len(event_name_samples) < 15:
                            event_name_samples.append(body['name'].get('string', ''))

                    raw_props = body.get('properties')
                    pmap = raw_props.get('map', {}) if isinstance(raw_props, dict) else {}
                    if not isinstance(pmap, dict):
                        pmap = {}
                    if pmap:
                        for k in pmap:
                            event_properties_keys[k] += 1
                        if 'exec' in pmap:
                            event_has_exec += 1
                            event_type_has_exec[etype] += 1
                            if len(event_exec_samples) < 10:
                                event_exec_samples.append(pmap['exec'])
                        if 'cmdLine' in pmap:
                            event_has_cmdline += 1
                            event_type_has_cmdline[etype] += 1
                            if len(event_cmdline_samples) < 10:
                                event_cmdline_samples.append(pmap['cmdLine'])

                else:
                    other_record_types[record_type] += 1
                    if record_type not in other_record_samples:
                        other_record_samples[record_type] = list(body.keys())[:8]

    # ---- 输出 ----
    print(f"\n总行数: {total:,}")

    print(f"\n{'='*80}\n零、Schema namespace\n{'='*80}")
    for ns, cnt in schema_namespace_counter.most_common():
        print(f"  {ns:60s} {cnt:>12,}")

    print(f"\n{'='*80}\nA、UUID 大小写分布\n{'='*80}")
    for c, n in uuid_case_counter.most_common():
        print(f"  {c:15s} {n:>12,}")

    print(f"\n{'='*80}\n一、记录类型分布\n{'='*80}")
    for rt, cnt in record_type_counter.most_common():
        print(f"  {rt:30s} {cnt:>12,}")

    print(f"\n{'='*80}\n二、Subject 分析\n{'='*80}")
    print(f"  Subject总数: {subject_count:,}")
    print(f"  类型分布:")
    for st, cnt in subject_type_counter.most_common():
        print(f"    {st:30s} {cnt:>10,}")
    if subject_count > 0:
        print(f"\n  --- 字段填充率 ---")
        print(f"  properties.map.path : {subject_has_props_path:>8,} / {subject_count:,}  ({subject_has_props_path/subject_count*100:5.1f}%)")
        print(f"  properties.map.name : {subject_has_props_name:>8,} / {subject_count:,}  ({subject_has_props_name/subject_count*100:5.1f}%)")
        print(f"  properties.map.tgid : {subject_has_tgid:>8,} / {subject_count:,}  ({subject_has_tgid/subject_count*100:5.1f}%)")
        print(f"  properties.map.ppid : {subject_has_ppid:>8,} / {subject_count:,}  ({subject_has_ppid/subject_count*100:5.1f}%)")
        print(f"  cid (PID)           : {subject_has_cid:>8,} / {subject_count:,}  ({subject_has_cid/subject_count*100:5.1f}%)")
        print(f"  cmdLine (非null)    : {subject_has_cmdline:>8,} / {subject_count:,}  ({subject_has_cmdline/subject_count*100:5.1f}%)")
        print(f"    string类型        : {subject_cmdline_is_str:>8,}")
        print(f"    dict类型          : {subject_cmdline_is_dict:>8,}")
        print(f"    null/缺失         : {subject_cmdline_is_null:>8,}")
        print(f"  parentSubject       : {subject_has_parent:>8,} / {subject_count:,}  ({subject_has_parent/subject_count*100:5.1f}%)")
    print(f"\n  properties.map 中所有 key:")
    for k, cnt in subject_properties_keys.most_common():
        print(f"    {k:30s} {cnt:>10,}")
    print(f"\n  Subject 顶层 key:")
    for k, cnt in subject_toplevel_keys.most_common():
        print(f"    {k:30s} {cnt:>10,}")
    print(f"\n  path 样例: {subject_path_samples[:5]}")
    print(f"  name 样例: {subject_name_samples[:5]}")
    print(f"  cmdLine 样例: {subject_cmdline_samples[:5]}")

    print(f"\n{'='*80}\n三、FileObject 分析\n{'='*80}")
    print(f"  FileObject总数: {file_count:,}")
    for ft, cnt in file_type_counter.most_common():
        print(f"    {ft:30s} {cnt:>10,}")
    if file_count > 0:
        print(f"  baseObject.props.map.filename  : {file_has_filename:>8,} / {file_count:,}  ({file_has_filename/file_count*100:5.1f}%)")
        print(f"  baseObject.props.map.path      : {file_has_path:>8,} / {file_count:,}  ({file_has_path/file_count*100:5.1f}%)")
        print(f"  baseObject.props.map.name      : {file_has_name:>8,} / {file_count:,}  ({file_has_name/file_count*100:5.1f}%)")
    print(f"\n  baseObject.properties.map 中所有 key:")
    for k, cnt in file_base_props_keys.most_common():
        print(f"    {k:30s} {cnt:>10,}")
    print(f"\n  filename 样例: {file_filename_samples[:5]}")
    print(f"  path 样例: {file_path_samples[:5]}")

    print(f"\n{'='*80}\n四、NetFlowObject 分析\n{'='*80}")
    print(f"  NetFlowObject总数: {net_count:,}")
    if net_count > 0:
        print(f"  localAddress  非null: {net_has_local_addr:>8,} / {net_count:,}  类型: {dict(net_local_addr_type)}")
        print(f"  remoteAddress 非null: {net_has_remote_addr:>8,} / {net_count:,}  类型: {dict(net_remote_addr_type)}")
        print(f"  样例: {net_samples[:3]}")

    print(f"\n{'='*80}\n五、Event 分析\n{'='*80}")
    print(f"  Event总数: {event_count:,}")
    if event_count > 0:
        print(f"  subject              : {event_has_subject:>10,} / {event_count:,}  ({event_has_subject/event_count*100:5.1f}%)")
        print(f"  predicateObject      : {event_has_predicate_object:>10,} / {event_count:,}  ({event_has_predicate_object/event_count*100:5.1f}%)")
        print(f"  predicateObject2     : {event_has_predicate_object2:>10,} / {event_count:,}  ({event_has_predicate_object2/event_count*100:5.1f}%)")
        print(f"  predicateObjectPath  : {event_has_predicate_object_path:>10,} / {event_count:,}  ({event_has_predicate_object_path/event_count*100:5.1f}%)")
        print(f"  predicateObject2Path : {event_has_predicate_object2_path:>10,} / {event_count:,}  ({event_has_predicate_object2_path/event_count*100:5.1f}%)")
        print(f"  name 字段            : {event_has_name:>10,} / {event_count:,}  ({event_has_name/event_count*100:5.1f}%)")
        print(f"  有 exec 的 Event     : {event_has_exec:>10,} / {event_count:,}  ({event_has_exec/event_count*100:5.1f}%)")
        print(f"  有 cmdLine 的 Event  : {event_has_cmdline:>10,} / {event_count:,}  ({event_has_cmdline/event_count*100:5.1f}%)")

    print(f"\n  --- 事件类型分布 ---")
    for et, cnt in event_type_counter.most_common():
        exec_cnt = event_type_has_exec.get(et, 0)
        cmd_cnt = event_type_has_cmdline.get(et, 0)
        po_cnt = event_type_has_pred_obj.get(et, 0)
        pop_cnt = event_type_has_pred_obj_path.get(et, 0)
        extras = []
        if exec_cnt > 0: extras.append(f"exec={exec_cnt}")
        if cmd_cnt > 0: extras.append(f"cmdLine={cmd_cnt}")
        extra_str = f"  [{', '.join(extras)}]" if extras else ""
        print(f"    {et:35s} {cnt:>10,}  (有predObj={po_cnt:,}, 有path={pop_cnt:,}){extra_str}")

    print(f"\n  properties.map 中所有 key (top 30):")
    for k, cnt in event_properties_keys.most_common(30):
        print(f"    {k:30s} {cnt:>10,}")
    print(f"\n  exec 样例: {event_exec_samples[:5]}")
    print(f"  cmdLine 样例: {event_cmdline_samples[:5]}")
    print(f"  Event.name 样例: {event_name_samples[:10]}")

    print(f"\n{'='*80}\n六、其他记录类型\n{'='*80}")
    for ot, cnt in other_record_types.most_common():
        print(f"  {ot}: {cnt:,}  顶层key样本={other_record_samples.get(ot)}")

    print(f"\n{'='*80}\n七、E5 vs CADETS E3 关键差异自检\n{'='*80}")
    print(f"  Subject.cmdLine dict 比例    : {subject_cmdline_is_dict/max(subject_count,1)*100:.1f}% (E3 CADETS=0)")
    print(f"  Subject.cmdLine str 比例     : {subject_cmdline_is_str/max(subject_count,1)*100:.1f}%")
    print(f"  Subject.props.path 填充      : {subject_has_props_path/max(subject_count,1)*100:.1f}% (E3 CADETS=0)")
    print(f"  FileObject path 填充         : {file_has_path/max(file_count,1)*100:.1f}% (E3 CADETS=0)")
    print(f"  FileObject filename 填充     : {file_has_filename/max(file_count,1)*100:.1f}% (E3 CADETS=0)")
    print(f"  Event.exec 填充              : {event_has_exec/max(event_count,1)*100:.1f}% (E3 CADETS=99.9)")
    print(f"  Event.cmdLine 填充           : {event_has_cmdline/max(event_count,1)*100:.1f}% (E3 CADETS=0.5)")
    print(f"  Event.predicateObjectPath    : {event_has_predicate_object_path/max(event_count,1)*100:.1f}% (E3 CADETS=44.2)")


def _default_files():
    prefix = "/mnt/disk/darpa/cch_refine/cadets_e5_json/"
    base = [
        prefix + 'ta1-cadets-1-e5-official-2.bin.json',
        prefix + 'ta1-cadets-1-e5-official-2.bin.json.1',
        prefix + 'ta1-cadets-1-e5-official-2.bin.json.2',
    ]
    mid = [
        f"{prefix}ta1-cadets-1-e5-official-2.bin.{i}.json{suffix}"
        for i in range(1, 121)
        for suffix in ('', '.1', '.2')
    ]
    tail = [
        prefix + 'ta1-cadets-1-e5-official-2.bin.121.json',
        prefix + 'ta1-cadets-1-e5-official-2.bin.121.json.1',
    ]
    return base + mid + tail


if __name__ == '__main__':
    if len(sys.argv) < 2:
        files = _default_files()
    else:
        files = sys.argv[1:]
    analyze_files(files)
