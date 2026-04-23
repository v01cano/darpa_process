-- FiveDirections E3 数据库初始化脚本
-- 表结构与 CADETS/THEIA/ClearScope/TRACE E3 完全一致
--
-- FiveDirections 特点：
--   - Windows 平台，有 THREAD 和 RegistryKey
--   - SUBJECT_THREAD 通过 UUID 替换合并到父 PROCESS
--   - RegistryKeyObject 归入 file 类型（用 key 作为 path）
--   - 节点 name = EXECUTE path（简洁程序名，如 svchost.exe）
--   - cmdLine 在 FORK/EXECUTE 边上

CREATE DATABASE fivedirections_e3;
\c fivedirections_e3;

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
