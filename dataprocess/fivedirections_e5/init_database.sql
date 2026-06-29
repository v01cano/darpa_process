-- ============================================================================
-- FiveDirections E5 数据库初始化脚本
-- 表结构与其他 8 个数据集完全一致（统一设计）
--
-- FD E5 特点：
--   - CDM20 schema
--   - Windows 平台
--   - SUBJECT_THREAD 占 97.6%，提取时合并到父 PROCESS（97.2% 可达）
--   - RegistryKeyObject 归入 file_node_table（key 作 path）
--   - subject_node_table.exec_name = EXECUTE.predicateObjectPath 提取的 .exe basename
--   - file_node_table.path = Event.predicateObjectPath 或 RegistryKey.key
--   - netflow_node_table 字段从 dict 解包
--   - event_table.cmdline 在 FORK / EXECUTE 边上是 predicateObjectPath（被执行的 exe 全路径）
--   - MemoryObject / IpcObject / SrcSinkObject / TimeMarker / EVENT_CREATE_THREAD 全部丢弃
-- ============================================================================

CREATE DATABASE fivedirections_e5;
\c fivedirections_e5;

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
