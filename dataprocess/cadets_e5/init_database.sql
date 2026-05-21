-- ============================================================================
-- CADETS E5 数据库初始化脚本
-- 表结构与 CADETS_E3 / THEIA_E3 / CLEARSCOPE_E3 / TRACE_E3 / FIVEDIRECTIONS_E3 /
-- CLEARSCOPE_E5 完全一致（七个数据集统一设计）
--
-- CADETS E5 特点：
--   - CDM20 schema（命名空间 com.bbn.tc.schema.avro.cdm20）
--   - FreeBSD 平台，3 种节点：subject / file / netflow
--   - Subject / FileObject 字段全空 → 命名靠 Event.exec 和 Event.predicateObjectPath
--   - NetFlow 地址 / 端口从 dict {"string":...} / {"int":...} 解包
--   - UUID 原始全大写，提取时统一 .lower()
--   - event_table.cmdline 仅在 EXECUTE / FORK 边有值（与 CADETS E3 一致）
--   - SrcSinkObject / IpcObject / EVENT_FLOWS_TO / EVENT_MODIFY_PROCESS 全部丢弃
--
-- 用法：
--   psql -U postgres -f init_database.sql
-- 重建：
--   psql -U postgres -c "DROP DATABASE IF EXISTS cadets_e5;"
--   psql -U postgres -f init_database.sql
-- ============================================================================

CREATE DATABASE cadets_e5;
\c cadets_e5;

CREATE TABLE subject_node_table (
    node_uuid   VARCHAR NOT NULL PRIMARY KEY,
    hash_id     VARCHAR NOT NULL,
    exec_name   VARCHAR,
    index_id    BIGINT
);

CREATE TABLE file_node_table (
    node_uuid   VARCHAR NOT NULL PRIMARY KEY,
    hash_id     VARCHAR NOT NULL,
    path        VARCHAR,
    index_id    BIGINT
);

CREATE TABLE netflow_node_table (
    node_uuid   VARCHAR NOT NULL PRIMARY KEY,
    hash_id     VARCHAR NOT NULL,
    src_addr    VARCHAR,
    src_port    VARCHAR,
    dst_addr    VARCHAR,
    dst_port    VARCHAR,
    index_id    BIGINT
);

CREATE TABLE event_table (
    src_uuid        VARCHAR,
    src_index_id    BIGINT,
    operation       VARCHAR,
    dst_uuid        VARCHAR,
    dst_index_id    BIGINT,
    event_uuid      VARCHAR NOT NULL,
    timestamp_rec   BIGINT,
    cmdline         VARCHAR,
    _id             SERIAL PRIMARY KEY
);

CREATE INDEX idx_event_timestamp ON event_table (timestamp_rec);
CREATE INDEX idx_event_src ON event_table (src_uuid);
CREATE INDEX idx_event_dst ON event_table (dst_uuid);
CREATE INDEX idx_event_operation ON event_table (operation);

ALTER TABLE subject_node_table OWNER TO postgres;
ALTER TABLE file_node_table OWNER TO postgres;
ALTER TABLE netflow_node_table OWNER TO postgres;
ALTER TABLE event_table OWNER TO postgres;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO postgres;
