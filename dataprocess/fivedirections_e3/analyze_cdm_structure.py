"""
FiveDirections E3 CDM数据结构分析脚本

FiveDirections 是 Windows 平台数据集，已知特征（来自 CAPTAIN fivedirections_parser.py）：
  - CDM18 格式
  - 只有 CAPTAIN 支持（Orthrus/PIDSMaker/KAIROS 均不支持）
  - Windows特有实体：RegistryKeyObject, PacketSocketObject
  - Subject: CAPTAIN 使用 cmdLine 作为进程名和命令行
  - FileObject: baseObject.properties.map.path
  - RegistryKeyObject: datum['key'] 作为名字

用法：
  python analyze_cdm_structure.py /mnt/disk/darpa/fivedirections_e3/ta1-fivedirections-e3-official-2.json
"""

import json
import sys
from collections import Counter

def analyze_file(filepath):
    print(f"分析文件: {filepath}")
    print("=" * 80)

    record_type_counter = Counter()

    # Subject
    subject_count = 0
    subject_type_counter = Counter()
    subject_has_path = 0
    subject_has_name = 0
    subject_has_cmdline = 0
    subject_cmdline_str = 0
    subject_cmdline_dict = 0
    subject_cmdline_null = 0
    subject_has_cid = 0
    subject_has_parent = 0
    subject_properties_keys = Counter()
    subject_path_samples = []
    subject_name_samples = []
    subject_cmdline_samples = []

    # FileObject
    file_count = 0
    file_type_counter = Counter()
    file_has_path = 0
    file_has_filename = 0
    file_base_props_keys = Counter()
    file_path_samples = []

    # RegistryKeyObject (Windows)
    reg_count = 0
    reg_has_key = 0
    reg_key_samples = []
    reg_props_keys = Counter()

    # PacketSocketObject (Windows)
    packet_count = 0

    # NetFlowObject
    net_count = 0
    net_has_local = 0
    net_has_remote = 0
    net_local_type = Counter()
    net_remote_type = Counter()

    # Event
    event_count = 0
    event_type_counter = Counter()
    event_has_subject = 0
    event_has_predobj = 0
    event_has_predobj2 = 0
    event_has_predobj_path = 0
    event_has_predobj2_path = 0
    event_has_exec = 0
    event_has_cmdline = 0
    event_has_name = 0
    event_properties_keys = Counter()
    event_exec_samples = []
    event_cmdline_samples = []
    event_name_samples = []
    event_type_has_exec = Counter()
    event_type_has_cmdline = Counter()
    event_type_has_predobj = Counter()
    event_type_has_predobj_path = Counter()

    other_types = Counter()

    loaded = 0
    with open(filepath, 'r') as fin:
        for line in fin:
            loaded += 1
            if loaded % 200000 == 0:
                print(f"  已扫描 {loaded:,} 行...")

            record = json.loads(line)['datum']
            rtype_full = list(record.keys())[0]
            datum = record[rtype_full]
            rtype = rtype_full.split('.')[-1]
            record_type_counter[rtype] += 1

            if rtype == 'Subject':
                subject_count += 1
                subject_type_counter[datum.get('type', 'UNKNOWN')] += 1

                props = datum.get('properties', {})
                props_map = props.get('map', {}) if isinstance(props, dict) else {}
                if not isinstance(props_map, dict): props_map = {}
                for k in props_map:
                    subject_properties_keys[k] += 1

                if props_map.get('path') is not None:
                    subject_has_path += 1
                    if len(subject_path_samples) < 10:
                        subject_path_samples.append(props_map['path'])
                if props_map.get('name') is not None:
                    subject_has_name += 1
                    if len(subject_name_samples) < 10:
                        subject_name_samples.append(props_map['name'])

                if datum.get('cid') is not None: subject_has_cid += 1
                if datum.get('parentSubject') is not None: subject_has_parent += 1

                cmdline = datum.get('cmdLine')
                if cmdline is None:
                    subject_cmdline_null += 1
                elif isinstance(cmdline, str):
                    subject_has_cmdline += 1
                    subject_cmdline_str += 1
                    if len(subject_cmdline_samples) < 10:
                        subject_cmdline_samples.append(cmdline)
                elif isinstance(cmdline, dict):
                    val = cmdline.get('string')
                    if val:
                        subject_has_cmdline += 1
                        subject_cmdline_dict += 1
                        if len(subject_cmdline_samples) < 10:
                            subject_cmdline_samples.append(val)
                    else:
                        subject_cmdline_null += 1

            elif rtype == 'FileObject':
                file_count += 1
                file_type_counter[datum.get('type', 'UNKNOWN')] += 1
                base = datum.get('baseObject', {})
                bp = base.get('properties', {})
                bpm = bp.get('map', {}) if isinstance(bp, dict) else {}
                if not isinstance(bpm, dict): bpm = {}
                for k in bpm:
                    file_base_props_keys[k] += 1
                if bpm.get('path') is not None:
                    file_has_path += 1
                    if len(file_path_samples) < 10:
                        file_path_samples.append(bpm['path'])
                if bpm.get('filename') is not None:
                    file_has_filename += 1

            elif rtype == 'RegistryKeyObject':
                reg_count += 1
                key = datum.get('key')
                if key is not None:
                    reg_has_key += 1
                    if len(reg_key_samples) < 10:
                        reg_key_samples.append(key)
                base = datum.get('baseObject', {})
                bp = base.get('properties', {})
                bpm = bp.get('map', {}) if isinstance(bp, dict) else {}
                if isinstance(bpm, dict):
                    for k in bpm:
                        reg_props_keys[k] += 1

            elif rtype == 'PacketSocketObject':
                packet_count += 1

            elif rtype == 'NetFlowObject':
                net_count += 1
                la = datum.get('localAddress')
                ra = datum.get('remoteAddress')
                if la is not None:
                    net_has_local += 1
                    net_local_type[type(la).__name__] += 1
                if ra is not None:
                    net_has_remote += 1
                    net_remote_type[type(ra).__name__] += 1

            elif rtype == 'Event':
                event_count += 1
                etype = datum.get('type', 'UNKNOWN')
                event_type_counter[etype] += 1

                if isinstance(datum.get('subject'), dict): event_has_subject += 1
                if isinstance(datum.get('predicateObject'), dict):
                    event_has_predobj += 1
                    event_type_has_predobj[etype] += 1
                if isinstance(datum.get('predicateObject2'), dict): event_has_predobj2 += 1
                if isinstance(datum.get('predicateObjectPath'), dict):
                    event_has_predobj_path += 1
                    event_type_has_predobj_path[etype] += 1
                if isinstance(datum.get('predicateObject2Path'), dict): event_has_predobj2_path += 1
                if isinstance(datum.get('name'), dict):
                    event_has_name += 1
                    if len(event_name_samples) < 10:
                        event_name_samples.append(datum['name'].get('string', ''))

                raw_props = datum.get('properties')
                props_map = raw_props.get('map', {}) if isinstance(raw_props, dict) else {}
                if not isinstance(props_map, dict): props_map = {}
                for k in props_map:
                    event_properties_keys[k] += 1
                if 'exec' in props_map:
                    event_has_exec += 1
                    event_type_has_exec[etype] += 1
                    if len(event_exec_samples) < 10:
                        event_exec_samples.append(props_map['exec'])
                if 'cmdLine' in props_map:
                    event_has_cmdline += 1
                    event_type_has_cmdline[etype] += 1
                    if len(event_cmdline_samples) < 10:
                        event_cmdline_samples.append(props_map['cmdLine'])
            else:
                other_types[rtype] += 1

    print(f"\n总行数: {loaded:,}")

    print(f"\n{'='*80}")
    print("一、记录类型分布")
    print(f"{'='*80}")
    for rt, cnt in record_type_counter.most_common():
        print(f"  {rt:30s} {cnt:>12,}")

    print(f"\n{'='*80}")
    print("二、Subject 分析")
    print(f"{'='*80}")
    print(f"  Subject总数: {subject_count:,}")
    print(f"  类型分布:")
    for st, cnt in subject_type_counter.most_common():
        print(f"    {st:30s} {cnt:>10,}")
    if subject_count > 0:
        sc = subject_count
        print(f"\n  --- 字段填充率 ---")
        print(f"  properties.map.path : {subject_has_path:>8,} / {sc:,}  ({subject_has_path/sc*100:5.1f}%)")
        print(f"  properties.map.name : {subject_has_name:>8,} / {sc:,}  ({subject_has_name/sc*100:5.1f}%)")
        print(f"  cid (PID)           : {subject_has_cid:>8,} / {sc:,}  ({subject_has_cid/sc*100:5.1f}%)")
        print(f"  cmdLine (非null)    : {subject_has_cmdline:>8,} / {sc:,}  ({subject_has_cmdline/sc*100:5.1f}%)")
        print(f"    其中 string类型   : {subject_cmdline_str:>8,}")
        print(f"    其中 dict类型     : {subject_cmdline_dict:>8,}")
        print(f"    null/缺失         : {subject_cmdline_null:>8,}")
        print(f"  parentSubject       : {subject_has_parent:>8,} / {sc:,}  ({subject_has_parent/sc*100:5.1f}%)")
    print(f"\n  properties.map中的所有key:")
    for k, cnt in subject_properties_keys.most_common():
        print(f"    {k:30s} {cnt:>10,}")
    print(f"\n  path样例: {subject_path_samples[:5]}")
    print(f"  name样例: {subject_name_samples[:5]}")
    print(f"  cmdLine样例: {subject_cmdline_samples[:5]}")

    print(f"\n{'='*80}")
    print("三、FileObject 分析")
    print(f"{'='*80}")
    print(f"  FileObject总数: {file_count:,}")
    print(f"  类型分布:")
    for ft, cnt in file_type_counter.most_common():
        print(f"    {ft:30s} {cnt:>10,}")
    if file_count > 0:
        print(f"\n  --- 字段填充率 ---")
        print(f"  baseObject.props.map.path     : {file_has_path:>8,} / {file_count:,}  ({file_has_path/file_count*100:5.1f}%)")
        print(f"  baseObject.props.map.filename  : {file_has_filename:>8,} / {file_count:,}  ({file_has_filename/file_count*100:5.1f}%)")
    print(f"\n  baseObject.properties.map中的所有key:")
    for k, cnt in file_base_props_keys.most_common():
        print(f"    {k:30s} {cnt:>10,}")
    print(f"\n  path样例: {file_path_samples[:5]}")

    print(f"\n{'='*80}")
    print("四、RegistryKeyObject 分析（Windows特有）")
    print(f"{'='*80}")
    print(f"  RegistryKeyObject总数: {reg_count:,}")
    if reg_count > 0:
        print(f"  有key字段: {reg_has_key:,} ({reg_has_key/reg_count*100:.1f}%)")
        print(f"  baseObject.props.map中的key:")
        for k, cnt in reg_props_keys.most_common():
            print(f"    {k:30s} {cnt:>10,}")
        print(f"  key样例:")
        for s in reg_key_samples[:5]:
            print(f"    {s}")

    print(f"\n{'='*80}")
    print("五、PacketSocketObject 分析（Windows特有）")
    print(f"{'='*80}")
    print(f"  PacketSocketObject总数: {packet_count:,}")

    print(f"\n{'='*80}")
    print("六、NetFlowObject 分析")
    print(f"{'='*80}")
    print(f"  NetFlowObject总数: {net_count:,}")
    if net_count > 0:
        print(f"  localAddress 非null: {net_has_local:,}  类型: {dict(net_local_type)}")
        print(f"  remoteAddress非null: {net_has_remote:,}  类型: {dict(net_remote_type)}")

    print(f"\n{'='*80}")
    print("七、Event 分析")
    print(f"{'='*80}")
    print(f"  Event总数: {event_count:,}")
    if event_count > 0:
        ec = event_count
        print(f"\n  --- 关联字段填充率 ---")
        print(f"  subject              : {event_has_subject:>10,} / {ec:,}  ({event_has_subject/ec*100:5.1f}%)")
        print(f"  predicateObject      : {event_has_predobj:>10,} / {ec:,}  ({event_has_predobj/ec*100:5.1f}%)")
        print(f"  predicateObject2     : {event_has_predobj2:>10,} / {ec:,}  ({event_has_predobj2/ec*100:5.1f}%)")
        print(f"  predicateObjectPath  : {event_has_predobj_path:>10,} / {ec:,}  ({event_has_predobj_path/ec*100:5.1f}%)")
        print(f"  predicateObject2Path : {event_has_predobj2_path:>10,} / {ec:,}  ({event_has_predobj2_path/ec*100:5.1f}%)")
        print(f"  name字段             : {event_has_name:>10,} / {ec:,}  ({event_has_name/ec*100:5.1f}%)")
        print(f"\n  --- properties.map中exec/cmdLine ---")
        print(f"  有exec字段           : {event_has_exec:>10,} / {ec:,}  ({event_has_exec/ec*100:5.1f}%)")
        print(f"  有cmdLine字段        : {event_has_cmdline:>10,} / {ec:,}  ({event_has_cmdline/ec*100:5.1f}%)")

    print(f"\n  --- 事件类型分布 ---")
    for et, cnt in event_type_counter.most_common():
        exec_cnt = event_type_has_exec.get(et, 0)
        cmd_cnt = event_type_has_cmdline.get(et, 0)
        po_cnt = event_type_has_predobj.get(et, 0)
        pop_cnt = event_type_has_predobj_path.get(et, 0)
        extras = []
        if exec_cnt > 0: extras.append(f"exec={exec_cnt}")
        if cmd_cnt > 0: extras.append(f"cmdLine={cmd_cnt}")
        extra_str = f"  [{', '.join(extras)}]" if extras else ""
        print(f"    {et:35s} {cnt:>10,}  (有predObj={po_cnt:,}, 有path={pop_cnt:,}){extra_str}")

    print(f"\n  properties.map中的所有key:")
    for k, cnt in event_properties_keys.most_common(30):
        print(f"    {k:30s} {cnt:>10,}")
    print(f"\n  exec样例: {event_exec_samples[:5]}")
    print(f"  cmdLine样例: {event_cmdline_samples[:5]}")
    print(f"  Event.name样例: {event_name_samples[:10]}")

    print(f"\n{'='*80}")
    print("八、其他记录类型")
    print(f"{'='*80}")
    for ot, cnt in other_types.most_common():
        print(f"  {ot}: {cnt:,}")

    print(f"\n{'='*80}")
    print("九、与其他4个数据集的关键字段对比")
    print(f"{'='*80}")
    sc = max(subject_count, 1)
    fc = max(file_count, 1)
    ec = max(event_count, 1)
    print(f"  Subject.properties.map.name: {subject_has_name/sc*100:.1f}% (CADETS=0%, THEIA=0%, ClearScope=0%, TRACE=100%)")
    print(f"  Subject.properties.map.path: {subject_has_path/sc*100:.1f}% (CADETS=0%, THEIA=99.3%, ClearScope=0%, TRACE=0%)")
    print(f"  Subject.cmdLine:             {subject_has_cmdline/sc*100:.1f}% (CADETS=0%, THEIA=99.3%, ClearScope=100%, TRACE=27~100%)")
    print(f"  FileObject.path:             {file_has_path/fc*100:.1f}% (CADETS=0%, THEIA=0%, ClearScope=100%, TRACE=100%)")
    print(f"  FileObject.filename:         {file_has_filename/fc*100:.1f}% (CADETS=0%, THEIA=97.4%, ClearScope=0%, TRACE=0%)")
    print(f"  Event.exec:                  {event_has_exec/ec*100:.1f}% (CADETS=99.9%, THEIA=0%, ClearScope=0%, TRACE=0%)")
    print(f"  Event.cmdLine:               {event_has_cmdline/ec*100:.1f}%")
    print(f"  Event.predicateObjectPath:   {event_has_predobj_path/ec*100:.1f}% (CADETS=44.2%, THEIA=0%, ClearScope=0.9%, TRACE=0.1~12.2%)")
    print(f"  RegistryKeyObject存在:        {'是('+str(reg_count)+'个)' if reg_count > 0 else '否'}  ← FiveDirections独有")
    print(f"  PacketSocketObject存在:       {'是('+str(packet_count)+'个)' if packet_count > 0 else '否'}  ← FiveDirections独有")


if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/mnt/disk/darpa/fivedirections_e3/ta1-fivedirections-e3-official-2.json"
    analyze_file(filepath)
