-- ============================================================================
-- ClearScope E5 数据库初始化脚本
-- 表结构与 CADETS/THEIA/ClearScope_E3/TRACE/FiveDirections E3 完全一致
--
-- ClearScope E5 特点：
--   - CDM20 schema（命名空间 com.bbn.tc.schema.avro.cdm20）
--   - Android 平台（HOST_MOBILE），3 种节点：subject / file / netflow
--   - subject_node_table.exec_name = Subject.cmdLine.string（包名 / 二进制路径）
--   - file_node_table.path = baseObject.properties.map.path
--   - netflow_node_table 字段从 dict {"string":...} / {"int":...} 解包
--   - event_table.cmdline 永远为 NULL（CDM20 无 properties.cmdLine）
--   - ProvenanceTagNode / IpcObject / SrcSinkObject 全部丢弃
--
-- 用法：
--   psql -U postgres -f init_database.sql
-- 重建：
--   psql -U postgres -c "DROP DATABASE IF EXISTS clearscope_e5;"
--   psql -U postgres -f init_database.sql
-- ============================================================================

CREATE DATABASE clearscope_e5;
\c clearscope_e5;

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
