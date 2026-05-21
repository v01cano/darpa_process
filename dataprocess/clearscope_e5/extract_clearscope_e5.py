"""
ClearScope E5 数据提取脚本（本地磁盘版）

基于 analyze_cdm_structure.py / analyze_uuid_lifecycle.py / analyze_all_entity_types.py
对前 1000 万行的实测分析，设计的提取方案。

ClearScope E5 是 Android 平台数据集（HOST_MOBILE），CDM20 schema。

与 ClearScope E3 的关键差异：
  1. CDM 命名空间: cdm20（E3 是 cdm18）
  2. NetFlow 地址是 dict 格式 {"string":"..."}，端口是 dict {"int":n}（E3 直接 string/int）
  3. UUID 大部分是大写（含 00000000-... 全零占位）→ 提取时统一 .lower()
  4. 极少量 EVENT_FORK(14) / EVENT_CLONE(128) / EVENT_EXECUTE(1) / EVENT_LOADLIBRARY(21)
     → 保留作 lineage 信息，但仍是单遍提取
  5. 数据量较 E3 大（~24GB 双 bin.json）→ 采用分批输出 edges_part_*.pkl
  6. ProvenanceTagNode / IpcObject / SrcSinkObject / PacketSocketObject / Host / Principal 全部丢弃

节点类型（与 E3 一致，3 种）：
  - subject  : SUBJECT_PROCESS, name = cmdLine.string
  - file     : FileObject (FILE/DIR), name = baseObject.properties.map.path
  - netflow  : NetFlowObject, name = "la:lp->ra:rp"

边过滤 + 反转：
  保留: READ, WRITE, OPEN, CONNECT, SENDTO, RECVFROM, SENDMSG, RECVMSG,
        UNLINK, RENAME, CREATE_OBJECT, EXECUTE, FORK, CLONE, LOADLIBRARY
  反转: READ, RECVFROM, RECVMSG, OPEN, EXECUTE, LOADLIBRARY (file→process)

用法：
  python extract_clearscope_e5.py \\
      --input_dir /mnt/disk/darpa/clearscope_e5 \\
      --output_dir /mnt/disk/darpa/clearscope_e5_output \\
      --batch_size 5
"""

import json
import os
import time
import argparse
import pickle
import glob
from collections import Counter

# ============================================================================
# 配置
# ============================================================================

DEFAULT_FILE_LIST = [
    'ta1-clearscope-1-e5-official-1.bin.json',
    'ta1-clearscope-1-e5-official-1.bin.json.1',
    'ta1-clearscope-1-e5-official-1.bin.json.2',
    'ta1-clearscope-1-e5-official-1.bin.1.json',
    'ta1-clearscope-1-e5-official-1.bin.1.json.1',
    'ta1-clearscope-1-e5-official-1.bin.1.json.2',
    'ta1-clearscope-1-e5-official-1.bin.2.json',
    'ta1-clearscope-1-e5-official-1.bin.2.json.1',
    'ta1-clearscope-1-e5-official-1.bin.2.json.2',
    'ta1-clearscope-1-e5-official-1.bin.3.json',
    'ta1-clearscope-1-e5-official-1.bin.3.json.1',
    'ta1-clearscope-1-e5-official-1.bin.3.json.2',
    'ta1-clearscope-1-e5-official-1.bin.4.json',
    'ta1-clearscope-1-e5-official-1.bin.4.json.1',
    'ta1-clearscope-1-e5-official-1.bin.4.json.2',
    'ta1-clearscope-1-e5-official-1.bin.5.json',
    'ta1-clearscope-1-e5-official-1.bin.5.json.1',
    'ta1-clearscope-1-e5-official-1.bin.5.json.2',
    'ta1-clearscope-1-e5-official-1.bin.6.json',
    'ta1-clearscope-1-e5-official-1.bin.6.json.1',
    'ta1-clearscope-1-e5-official-1.bin.6.json.2',
    'ta1-clearscope-1-e5-official-1.bin.7.json',
    'ta1-clearscope-1-e5-official-1.bin.7.json.1',
    'ta1-clearscope-1-e5-official-1.bin.7.json.2',
    'ta1-clearscope-1-e5-official-1.bin.8.json',
    'ta1-clearscope-1-e5-official-1.bin.8.json.1',
    'ta1-clearscope-1-e5-official-1.bin.8.json.2',
    'ta1-clearscope-1-e5-official-1.bin.9.json',
    'ta1-clearscope-1-e5-official-1.bin.9.json.1',
    'ta1-clearscope-1-e5-official-1.bin.9.json.2',
    'ta1-clearscope-1-e5-official-1.bin.10.json',
    'ta1-clearscope-1-e5-official-1.bin.10.json.1',
    'ta1-clearscope-1-e5-official-1.bin.10.json.2',
    'ta1-clearscope-1-e5-official-1.bin.11.json',
    'ta1-clearscope-1-e5-official-1.bin.11.json.1',
    'ta1-clearscope-1-e5-official-1.bin.11.json.2',
    'ta1-clearscope-1-e5-official-1.bin.12.json',
    'ta1-clearscope-1-e5-official-1.bin.12.json.1',
    'ta1-clearscope-1-e5-official-1.bin.12.json.2',
    'ta1-clearscope-1-e5-official-1.bin.13.json',
    'ta1-clearscope-1-e5-official-1.bin.13.json.1',
    'ta1-clearscope-1-e5-official-1.bin.13.json.2',
    'ta1-clearscope-1-e5-official-1.bin.14.json',
    'ta1-clearscope-1-e5-official-1.bin.14.json.1',
    'ta1-clearscope-1-e5-official-1.bin.14.json.2',
    'ta1-clearscope-1-e5-official-1.bin.15.json',
    'ta1-clearscope-1-e5-official-1.bin.15.json.1',
    'ta1-clearscope-1-e5-official-1.bin.15.json.2',
    'ta1-clearscope-1-e5-official-1.bin.16.json',
    'ta1-clearscope-1-e5-official-1.bin.16.json.1',
    'ta1-clearscope-1-e5-official-1.bin.16.json.2',
    'ta1-clearscope-1-e5-official-1.bin.17.json',
    'ta1-clearscope-1-e5-official-1.bin.17.json.1',
    'ta1-clearscope-1-e5-official-1.bin.17.json.2',
    'ta1-clearscope-1-e5-official-1.bin.18.json',
    'ta1-clearscope-1-e5-official-1.bin.18.json.1',
    'ta1-clearscope-1-e5-official-1.bin.18.json.2',
    'ta1-clearscope-1-e5-official-1.bin.19.json',
    'ta1-clearscope-1-e5-official-1.bin.19.json.1',
    'ta1-clearscope-1-e5-official-1.bin.19.json.2',
    'ta1-clearscope-1-e5-official-1.bin.20.json',
    'ta1-clearscope-1-e5-official-1.bin.21.json',
    'ta1-clearscope-1-e5-official-1.bin.21.json.1',
    'ta1-clearscope-1-e5-official-1.bin.21.json.2',
    'ta1-clearscope-1-e5-official-1.bin.22.json',
    'ta1-clearscope-1-e5-official-1.bin.22.json.1',
    'ta1-clearscope-1-e5-official-1.bin.22.json.2',
    'ta1-clearscope-1-e5-official-1.bin.23.json',
    'ta1-clearscope-1-e5-official-1.bin.23.json.1',
    'ta1-clearscope-1-e5-official-1.bin.23.json.2',
    'ta1-clearscope-1-e5-official-1.bin.24.json',
    'ta1-clearscope-1-e5-official-1.bin.24.json.1',
    'ta1-clearscope-1-e5-official-1.bin.24.json.2',
    'ta1-clearscope-1-e5-official-1.bin.25.json',
    'ta1-clearscope-1-e5-official-1.bin.25.json.1',
    'ta1-clearscope-1-e5-official-1.bin.25.json.2',
    'ta1-clearscope-1-e5-official-1.bin.26.json',
    'ta1-clearscope-1-e5-official-1.bin.26.json.1',
    'ta1-clearscope-1-e5-official-1.bin.26.json.2',
    'ta1-clearscope-1-e5-official-1.bin.27.json',
    'ta1-clearscope-1-e5-official-1.bin.27.json.1',
    'ta1-clearscope-1-e5-official-1.bin.27.json.2',
    'ta1-clearscope-1-e5-official-1.bin.28.json',
    'ta1-clearscope-1-e5-official-1.bin.28.json.1',
    'ta1-clearscope-1-e5-official-1.bin.28.json.2',
    'ta1-clearscope-1-e5-official-1.bin.29.json',
    'ta1-clearscope-1-e5-official-1.bin.29.json.1',
    'ta1-clearscope-1-e5-official-1.bin.29.json.2',
    'ta1-clearscope-1-e5-official-1.bin.30.json',
    'ta1-clearscope-1-e5-official-1.bin.30.json.1',
    'ta1-clearscope-1-e5-official-1.bin.30.json.2',
    'ta1-clearscope-1-e5-official-1.bin.31.json',
    'ta1-clearscope-1-e5-official-1.bin.31.json.1',
    'ta1-clearscope-1-e5-official-1.bin.31.json.2',
    'ta1-clearscope-1-e5-official-1.bin.32.json',
    'ta1-clearscope-1-e5-official-1.bin.32.json.1',
    'ta1-clearscope-1-e5-official-1.bin.32.json.2',
    'ta1-clearscope-1-e5-official-1.bin.33.json',
    'ta1-clearscope-1-e5-official-1.bin.33.json.1',
    'ta1-clearscope-1-e5-official-1.bin.33.json.2',
    'ta1-clearscope-1-e5-official-1.bin.34.json',
    'ta1-clearscope-1-e5-official-1.bin.34.json.1',
    'ta1-clearscope-1-e5-official-1.bin.34.json.2',
    'ta1-clearscope-1-e5-official-1.bin.35.json',
    'ta1-clearscope-1-e5-official-1.bin.35.json.1',
    'ta1-clearscope-1-e5-official-1.bin.35.json.2',
    'ta1-clearscope-1-e5-official-1.bin.36.json',
    'ta1-clearscope-2-e5-official-1.bin.json',
    'ta1-clearscope-2-e5-official-1.bin.json.1',
    'ta1-clearscope-2-e5-official-1.bin.json.2',
    'ta1-clearscope-2-e5-official-1.bin.1.json',
    'ta1-clearscope-2-e5-official-1.bin.1.json.1',
    'ta1-clearscope-2-e5-official-1.bin.1.json.2',
    'ta1-clearscope-2-e5-official-1.bin.2.json',
    'ta1-clearscope-2-e5-official-1.bin.2.json.1',
    'ta1-clearscope-2-e5-official-1.bin.2.json.2',
    'ta1-clearscope-2-e5-official-1.bin.3.json',
    'ta1-clearscope-2-e5-official-1.bin.3.json.1',
    'ta1-clearscope-2-e5-official-1.bin.3.json.2',
    'ta1-clearscope-2-e5-official-1.bin.4.json',
    'ta1-clearscope-2-e5-official-1.bin.4.json.1',
    'ta1-clearscope-2-e5-official-1.bin.4.json.2',
    'ta1-clearscope-2-e5-official-1.bin.5.json',
    'ta1-clearscope-2-e5-official-1.bin.5.json.1',
    'ta1-clearscope-2-e5-official-1.bin.5.json.2',
    'ta1-clearscope-2-e5-official-1.bin.6.json',
    'ta1-clearscope-2-e5-official-1.bin.6.json.1',
    'ta1-clearscope-2-e5-official-1.bin.6.json.2',
    'ta1-clearscope-2-e5-official-1.bin.7.json',
    'ta1-clearscope-2-e5-official-1.bin.7.json.1',
    'ta1-clearscope-2-e5-official-1.bin.7.json.2',
    'ta1-clearscope-2-e5-official-1.bin.8.json',
    'ta1-clearscope-2-e5-official-1.bin.8.json.1',
    'ta1-clearscope-2-e5-official-1.bin.8.json.2',
    'ta1-clearscope-2-e5-official-1.bin.9.json',
    'ta1-clearscope-2-e5-official-1.bin.9.json.1',
    'ta1-clearscope-2-e5-official-1.bin.9.json.2',
    'ta1-clearscope-2-e5-official-1.bin.10.json',
    'ta1-clearscope-2-e5-official-1.bin.10.json.1',
    'ta1-clearscope-2-e5-official-1.bin.10.json.2',
    'ta1-clearscope-2-e5-official-1.bin.11.json',
    'ta1-clearscope-2-e5-official-1.bin.11.json.1',
    'ta1-clearscope-2-e5-official-1.bin.11.json.2',
    'ta1-clearscope-2-e5-official-1.bin.12.json',
    'ta1-clearscope-2-e5-official-1.bin.12.json.1',
    'ta1-clearscope-2-e5-official-1.bin.12.json.2',
    'ta1-clearscope-2-e5-official-1.bin.13.json',
    'ta1-clearscope-2-e5-official-1.bin.13.json.1',
    'ta1-clearscope-2-e5-official-1.bin.13.json.2',
    'ta1-clearscope-2-e5-official-1.bin.14.json',
    'ta1-clearscope-2-e5-official-1.bin.14.json.1',
    'ta1-clearscope-2-e5-official-1.bin.14.json.2',
    'ta1-clearscope-2-e5-official-1.bin.15.json',
    'ta1-clearscope-2-e5-official-1.bin.15.json.1',
    'ta1-clearscope-2-e5-official-1.bin.15.json.2',
    'ta1-clearscope-2-e5-official-1.bin.16.json',
    'ta1-clearscope-2-e5-official-1.bin.16.json.1',
    'ta1-clearscope-2-e5-official-1.bin.16.json.2',
    'ta1-clearscope-2-e5-official-1.bin.17.json',
    'ta1-clearscope-2-e5-official-1.bin.17.json.1',
    'ta1-clearscope-2-e5-official-1.bin.17.json.2',
    'ta1-clearscope-2-e5-official-1.bin.18.json',
    'ta1-clearscope-2-e5-official-1.bin.18.json.1',
    'ta1-clearscope-2-e5-official-1.bin.18.json.2',
    'ta1-clearscope-2-e5-official-1.bin.19.json',
    'ta1-clearscope-2-e5-official-1.bin.19.json.1',
    'ta1-clearscope-2-e5-official-1.bin.19.json.2',
    'ta1-clearscope-2-e5-official-1.bin.20.json',
    'ta1-clearscope-2-e5-official-1.bin.20.json.1',
    'ta1-clearscope-2-e5-official-1.bin.20.json.2',
    'ta1-clearscope-2-e5-official-1.bin.21.json',
    'ta1-clearscope-2-e5-official-1.bin.21.json.1',
    'ta1-clearscope-2-e5-official-1.bin.21.json.2',
    'ta1-clearscope-2-e5-official-1.bin.22.json',
    'ta1-clearscope-2-e5-official-1.bin.22.json.1',
    'ta1-clearscope-2-e5-official-1.bin.22.json.2',
    'ta1-clearscope-2-e5-official-1.bin.23.json',
    'ta1-clearscope-2-e5-official-1.bin.23.json.1',
    'ta1-clearscope-2-e5-official-1.bin.23.json.2',
    'ta1-clearscope-2-e5-official-1.bin.24.json',
    'ta1-clearscope-2-e5-official-1.bin.24.json.1',
    'ta1-clearscope-2-e5-official-1.bin.24.json.2',
    'ta1-clearscope-2-e5-official-1.bin.25.json',
    'ta1-clearscope-2-e5-official-1.bin.25.json.1',
    'ta1-clearscope-2-e5-official-1.bin.25.json.2',
    'ta1-clearscope-2-e5-official-1.bin.26.json',
    'ta1-clearscope-2-e5-official-1.bin.26.json.1',
    'ta1-clearscope-2-e5-official-1.bin.26.json.2',
    'ta1-clearscope-2-e5-official-1.bin.27.json',
    'ta1-clearscope-2-e5-official-1.bin.27.json.1',
    'ta1-clearscope-2-e5-official-1.bin.27.json.2',
    'ta1-clearscope-2-e5-official-1.bin.28.json',
    'ta1-clearscope-2-e5-official-1.bin.28.json.1',
    'ta1-clearscope-2-e5-official-1.bin.28.json.2',
    'ta1-clearscope-2-e5-official-1.bin.29.json',
    'ta1-clearscope-2-e5-official-1.bin.29.json.1',
    'ta1-clearscope-2-e5-official-1.bin.29.json.2',
    'ta1-clearscope-2-e5-official-1.bin.30.json',
    'ta1-clearscope-2-e5-official-1.bin.30.json.1',
    'ta1-clearscope-2-e5-official-1.bin.30.json.2',
    'ta1-clearscope-2-e5-official-1.bin.31.json',
    'ta1-clearscope-2-e5-official-1.bin.31.json.1',
    'ta1-clearscope-2-e5-official-1.bin.31.json.2',
    'ta1-clearscope-2-e5-official-1.bin.32.json',
    'ta1-clearscope-2-e5-official-1.bin.32.json.1',
    'ta1-clearscope-2-e5-official-1.bin.32.json.2',
    'ta1-clearscope-2-e5-official-1.bin.33.json',
    'ta1-clearscope-2-e5-official-1.bin.33.json.1',
    'ta1-clearscope-2-e5-official-1.bin.33.json.2',
    'ta1-clearscope-2-e5-official-1.bin.34.json',
    'ta1-clearscope-3-e5-official-1.bin.json',
    'ta1-clearscope-3-e5-official-1.bin.json.1',
    'ta1-clearscope-3-e5-official-1.bin.json.2',
    'ta1-clearscope-3-e5-official-1.bin.1.json',
    'ta1-clearscope-3-e5-official-1.bin.1.json.1',
    'ta1-clearscope-3-e5-official-1.bin.1.json.2',
    'ta1-clearscope-3-e5-official-1.bin.2.json',
    'ta1-clearscope-3-e5-official-1.bin.2.json.1',
    'ta1-clearscope-3-e5-official-1.bin.2.json.2',
    'ta1-clearscope-3-e5-official-1.bin.3.json',
    'ta1-clearscope-3-e5-official-1.bin.3.json.1',
    'ta1-clearscope-3-e5-official-1.bin.3.json.2',
    'ta1-clearscope-3-e5-official-1.bin.4.json',
    'ta1-clearscope-3-e5-official-1.bin.4.json.1',
    'ta1-clearscope-3-e5-official-1.bin.4.json.2',
    'ta1-clearscope-3-e5-official-1.bin.5.json',
    'ta1-clearscope-3-e5-official-1.bin.5.json.1',
    'ta1-clearscope-3-e5-official-1.bin.5.json.2',
    'ta1-clearscope-3-e5-official-1.bin.6.json',
    'ta1-clearscope-3-e5-official-1.bin.6.json.1',
    'ta1-clearscope-3-e5-official-1.bin.6.json.2',
    'ta1-clearscope-3-e5-official-1.bin.7.json',
    'ta1-clearscope-3-e5-official-1.bin.7.json.1',
    'ta1-clearscope-3-e5-official-1.bin.7.json.2',
    'ta1-clearscope-3-e5-official-1.bin.8.json',
    'ta1-clearscope-3-e5-official-1.bin.8.json.1',
    'ta1-clearscope-3-e5-official-1.bin.8.json.2',
    'ta1-clearscope-3-e5-official-1.bin.9.json',
    'ta1-clearscope-3-e5-official-1.bin.9.json.1',
    'ta1-clearscope-3-e5-official-1.bin.9.json.2',
    'ta1-clearscope-3-e5-official-1.bin.10.json',
    'ta1-clearscope-3-e5-official-1.bin.10.json.1',
    'ta1-clearscope-3-e5-official-1.bin.10.json.2',
    'ta1-clearscope-3-e5-official-1.bin.11.json',
    'ta1-clearscope-3-e5-official-1.bin.11.json.1',
    'ta1-clearscope-3-e5-official-1.bin.11.json.2',
    'ta1-clearscope-3-e5-official-1.bin.12.json',
    'ta1-clearscope-3-e5-official-1.bin.12.json.1',
    'ta1-clearscope-3-e5-official-1.bin.12.json.2',
    'ta1-clearscope-3-e5-official-1.bin.13.json',
]

INCLUDE_EDGE_TYPE = {
    # 文件 I/O
    'EVENT_READ',
    'EVENT_WRITE',
    'EVENT_OPEN',
    'EVENT_UNLINK',
    'EVENT_RENAME',
    'EVENT_CREATE_OBJECT',
    # 网络 / IPC
    'EVENT_CONNECT',
    'EVENT_SENDTO',
    'EVENT_RECVFROM',
    'EVENT_SENDMSG',
    'EVENT_RECVMSG',
    # 进程 lineage（量极少但保留）
    'EVENT_EXECUTE',
    'EVENT_FORK',
    'EVENT_CLONE',
    'EVENT_LOADLIBRARY',
}

# 反转的边（让边方向 = 数据流向）
EDGE_REVERSED = {
    'EVENT_READ',
    'EVENT_RECVFROM',
    'EVENT_RECVMSG',
    'EVENT_OPEN',
    'EVENT_EXECUTE',     # file → process（被执行的二进制 → 新进程）
    'EVENT_LOADLIBRARY', # so → process
}


# ============================================================================
# 辅助
# ============================================================================

def norm_uuid(u):
    """ClearScope E5 UUID 大部分大写，统一转小写避免下游不一致。"""
    return u.lower() if isinstance(u, str) else u


def unpack_dict_str(v):
    """E5 地址字段是 dict {"string": "..."}, 解包成纯 str。"""
    if isinstance(v, dict):
        return v.get('string', '')
    return v if v is not None else ''


def unpack_dict_int(v):
    if isinstance(v, dict):
        return v.get('int', '')
    return v if v is not None else ''


def get_uuid(ref):
    """从 {"com.bbn.tc.schema.avro.cdm20.UUID": "..."} 取 UUID。"""
    if isinstance(ref, dict):
        for v in ref.values():
            if isinstance(v, str):
                return norm_uuid(v)
    return ''


def list_input_files(input_dir, override=None):
    if override:
        files = [os.path.join(input_dir, f) for f in override]
    else:
        # ClearScope E5 使用固定文件列表，严格按 DEFAULT_FILE_LIST 顺序处理。
        files = [os.path.join(input_dir, f) for f in DEFAULT_FILE_LIST]
    return [f for f in files if os.path.exists(f)]


# ============================================================================
# 主提取
# ============================================================================

def extract_all(input_files, output_dir, batch_size_files=5):
    """
    单遍扫描所有文件。

    内存友好策略（参照 trace_e3）：
      - 累积每 batch_size_files 个文件就把 datalist 落盘为 edges_part_NNN.pkl
      - uuid2name 全程在内存（实体规模小，~30k 量级）
    """
    uuid2name = {}              # uuid (lowercased) → [type, name]
    netflow_info = {}           # uuid → (la, lp, ra, rp)  保留四元组用于 PG 版

    edge_type_count = Counter()
    skipped_no_node = 0
    skipped_filtered = 0

    # 当前批
    cur_batch = []
    part_idx = 0
    files_in_batch = 0

    loaded_line = 0
    begin = time.time()

    edges_part_paths = []

    def flush_batch():
        nonlocal cur_batch, part_idx, files_in_batch
        if not cur_batch:
            return
        path = os.path.join(output_dir, f'edges_part_{part_idx:03d}.pkl')
        with open(path, 'wb') as f:
            pickle.dump(cur_batch, f)
        edges_part_paths.append(path)
        print(f"  [flush] edges_part_{part_idx:03d}.pkl  ({len(cur_batch):,} 条边)")
        cur_batch = []
        part_idx += 1
        files_in_batch = 0

    for volume_idx, volume_path in enumerate(input_files):
        print(f"  处理 [{volume_idx+1}/{len(input_files)}]: {os.path.basename(volume_path)}")

        with open(volume_path, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 1000000 == 0:
                    print(f"    已扫描 {loaded_line:,} 行... uuid2name={len(uuid2name):,} edges_buf={len(cur_batch):,}")

                try:
                    record = json.loads(line)['datum']
                except Exception:
                    continue
                if not isinstance(record, dict) or not record:
                    continue
                rtype_full = next(iter(record.keys()))
                datum = record[rtype_full]
                if not isinstance(datum, dict):
                    continue
                rtype = rtype_full.rsplit('.', 1)[-1]

                # ---------- Subject ----------
                if rtype == 'Subject':
                    if datum.get('type') == 'SUBJECT_PROCESS':
                        uid = norm_uuid(datum.get('uuid'))
                        cmdline = datum.get('cmdLine')
                        if isinstance(cmdline, dict):
                            cmdline = cmdline.get('string')
                        if uid:
                            uuid2name[uid] = ['process', cmdline]

                # ---------- FileObject ----------
                elif rtype == 'FileObject':
                    uid = norm_uuid(datum.get('uuid'))
                    base = datum.get('baseObject') or {}
                    props = base.get('properties') if isinstance(base, dict) else None
                    pmap = props.get('map') if isinstance(props, dict) else None
                    path = pmap.get('path') if isinstance(pmap, dict) else None
                    if uid:
                        uuid2name[uid] = ['file', path]

                # ---------- NetFlowObject ----------
                elif rtype == 'NetFlowObject':
                    uid = norm_uuid(datum.get('uuid'))
                    la = unpack_dict_str(datum.get('localAddress'))
                    lp = unpack_dict_int(datum.get('localPort'))
                    ra = unpack_dict_str(datum.get('remoteAddress'))
                    rp = unpack_dict_int(datum.get('remotePort'))
                    if uid:
                        uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]
                        netflow_info[uid] = (str(la), str(lp), str(ra), str(rp))

                # ---------- Event ----------
                elif rtype == 'Event':
                    eventtype = datum.get('type', '')
                    if eventtype not in INCLUDE_EDGE_TYPE:
                        skipped_filtered += 1
                        continue

                    eventtime = datum.get('timestampNanos', 0)
                    src = get_uuid(datum.get('subject'))
                    dst = get_uuid(datum.get('predicateObject'))
                    dst2 = get_uuid(datum.get('predicateObject2'))

                    # RENAME 用 predicateObject2 作为目标
                    actual_dst = dst2 if eventtype == 'EVENT_RENAME' else dst

                    if not src or not actual_dst:
                        skipped_no_node += 1
                        continue
                    if src not in uuid2name or actual_dst not in uuid2name:
                        skipped_no_node += 1
                        continue

                    if eventtype in EDGE_REVERSED:
                        edge_src, edge_dst = actual_dst, src
                    else:
                        edge_src, edge_dst = src, actual_dst

                    cur_batch.append((
                        eventtime,
                        eventtype,
                        edge_src,
                        uuid2name[edge_src][0],
                        uuid2name[edge_src][1],
                        edge_dst,
                        uuid2name[edge_dst][0],
                        uuid2name[edge_dst][1],
                        None,  # cmdLine: ClearScope EXECUTE 没有 properties.exec/cmdLine
                    ))
                    edge_type_count[eventtype] += 1

                # 其他类型（ProvenanceTagNode, IpcObject, SrcSinkObject,
                #         PacketSocketObject, Host, Principal）→ 丢弃

        files_in_batch += 1
        if files_in_batch >= batch_size_files:
            flush_batch()

    # 收尾 flush
    flush_batch()

    elapsed = time.time() - begin
    print(f"\n  完成: {loaded_line:,} 行, {elapsed:.1f}s")

    # 实体统计
    type_count = Counter(t for t, _ in uuid2name.values())
    name_filled = Counter(t for t, n in uuid2name.values() if n is not None)
    print(f"  实体统计:")
    for t in ['process', 'file', 'netflow']:
        total = type_count.get(t, 0)
        filled = name_filled.get(t, 0)
        pct = filled / total * 100 if total > 0 else 0
        print(f"    {t:10s}: {total:>10,}  (有名字: {filled:,} = {pct:.1f}%)")

    total_edges = sum(edge_type_count.values())
    print(f"  提取的边: {total_edges:,}（分 {len(edges_part_paths)} 个 part）")
    print(f"  过滤的边(类型不在列表): {skipped_filtered:,}")
    print(f"  跳过的边(节点不存在):   {skipped_no_node:,}")
    print(f"\n  边类型分布:")
    for etype, cnt in edge_type_count.most_common():
        print(f"    {etype:30s} {cnt:>12,}")

    return uuid2name, netflow_info, edges_part_paths


# ============================================================================
# 下游辅助
# ============================================================================

def iter_edges(output_dir):
    """流式迭代所有 edges_part_*.pkl。"""
    for path in sorted(glob.glob(os.path.join(output_dir, 'edges_part_*.pkl'))):
        with open(path, 'rb') as f:
            edges = pickle.load(f)
        for e in edges:
            yield e


def load_all_edges(output_dir):
    """全量加载（小心内存）。"""
    edges = []
    for path in sorted(glob.glob(os.path.join(output_dir, 'edges_part_*.pkl'))):
        with open(path, 'rb') as f:
            edges.extend(pickle.load(f))
    return edges


# ============================================================================
# 主流程
# ============================================================================

def main(args):
    print("=" * 60)
    print("ClearScope E5 数据提取（本地版）")
    print("=" * 60)

    print(f"\n输入目录:  {args.input_dir}")
    print(f"输出目录:  {args.output_dir}")
    print(f"批大小:    {args.batch_size} 个文件 / part")
    os.makedirs(args.output_dir, exist_ok=True)

    input_files = list_input_files(args.input_dir)
    print(f"\n输入文件 ({len(input_files)} 个):")
    for f in input_files:
        print(f"  {os.path.basename(f)}")

    uuid2name, netflow_info, parts = extract_all(
        input_files, args.output_dir, batch_size_files=args.batch_size,
    )

    print(f"\n{'='*60}\n保存结果\n{'='*60}")

    with open(os.path.join(args.output_dir, 'uuid2name.pkl'), 'wb') as f:
        pickle.dump(uuid2name, f)
    print(f"  uuid2name.pkl 已保存 ({len(uuid2name):,} 实体)")

    with open(os.path.join(args.output_dir, 'netflow_info.pkl'), 'wb') as f:
        pickle.dump(netflow_info, f)
    print(f"  netflow_info.pkl 已保存 ({len(netflow_info):,})")

    # 导出前 100k 条边到 CSV 方便预览
    csv_path = os.path.join(args.output_dir, 'edges.csv')
    with open(csv_path, 'w') as f:
        f.write('timestamp,event_type,src_uuid,src_type,src_name,dst_uuid,dst_type,dst_name,cmdline\n')
        cnt = 0
        for row in iter_edges(args.output_dir):
            fields = [str(x) if x is not None else '' for x in row]
            fields = [field.replace(',', ';') for field in fields]
            f.write(','.join(fields) + '\n')
            cnt += 1
            if cnt >= 100000:
                break
    print(f"  edges.csv (前 10 万条) 已保存")

    print(f"\n{'='*60}\n完成\n{'='*60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="ClearScope E5 Data Extractor")
    parser.add_argument("--input_dir", type=str, default="/mnt/disk/darpa/clearscope_e5")
    parser.add_argument("--output_dir", type=str, default="/mnt/disk/darpa/clearscope_e5_output")
    parser.add_argument("--batch_size", type=int, default=5,
                        help="每多少个输入文件 flush 一次 edges_part_*.pkl")
    args = parser.parse_args()
    main(args)
