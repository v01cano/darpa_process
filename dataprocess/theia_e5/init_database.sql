-- ============================================================================
-- THEIA E5 数据库初始化脚本
-- 表结构与其他 7 个数据集完全一致（统一设计）
--
-- THEIA E5 特点：
--   - CDM20 schema（命名空间 com.bbn.tc.schema.avro.cdm20）
--   - Linux 平台，3 种节点：subject / file / netflow
--   - subject_node_table.exec_name = Subject.properties.map.path（100% 填充）
--   - file_node_table.path        = baseObject.properties.map.filename（88.6%）
--   - netflow_node_table 字段从 dict 解包
--   - event_table.cmdline 仅在 EXECUTE / CLONE 边有值
--   - MemoryObject / IpcObject / Principal / Host 全部丢弃
--   - predicateObject2（与 subject 冗余）忽略
--
-- 用法：
--   psql -U postgres -f init_database.sql
-- 重建：
--   psql -U postgres -c "DROP DATABASE IF EXISTS theia_e5;"
--   psql -U postgres -f init_database.sql
-- ============================================================================

CREATE DATABASE theia_e5;
\c theia_e5;

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
