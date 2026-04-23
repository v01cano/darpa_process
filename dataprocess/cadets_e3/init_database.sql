-- ============================================================================
-- CADETS E3 数据库初始化脚本
--
-- 表结构参考 Orthrus (orthrus-main/postgres/init-create-databases.sh)
-- 改动点：
--   1. subject_node_table: 去掉 cmd 列（cmdLine 是边属性），增加 exec_name 列
--   2. event_table: src/dst 使用 uuid（而非 hash），增加 cmdline 列
--   3. 边方向已在插入时反转（READ/RECV/EXECUTE/OPEN 类）
--   4. 保留 hash_id 和 index_id 在节点表中用于兼容 Orthrus 下游
--
-- 用法：
--   psql -U postgres -f init_database.sql
--
-- 如需重建：
--   psql -U postgres -c "DROP DATABASE IF EXISTS cadets_e3;"
--   psql -U postgres -f init_database.sql
-- ============================================================================

CREATE DATABASE cadets_e3;
\c cadets_e3;

-- ============================================================================
-- 节点表
-- ============================================================================

-- 进程节点表
CREATE TABLE subject_node_table (
    node_uuid   VARCHAR NOT NULL PRIMARY KEY,
    hash_id     VARCHAR NOT NULL,
    exec_name   VARCHAR,            -- 进程的最终 exec 名（如 'nginx', 'wget'）
    index_id    BIGINT
);
ALTER TABLE subject_node_table OWNER TO postgres;

-- 文件节点表
CREATE TABLE file_node_table (
    node_uuid   VARCHAR NOT NULL PRIMARY KEY,
    hash_id     VARCHAR NOT NULL,
    path        VARCHAR,            -- 文件路径（从 Event.predicateObjectPath 获取）
    index_id    BIGINT
);
ALTER TABLE file_node_table OWNER TO postgres;

-- 网络节点表
CREATE TABLE netflow_node_table (
    node_uuid   VARCHAR NOT NULL PRIMARY KEY,
    hash_id     VARCHAR NOT NULL,
    src_addr    VARCHAR,
    src_port    VARCHAR,
    dst_addr    VARCHAR,
    dst_port    VARCHAR,
    index_id    BIGINT
);
ALTER TABLE netflow_node_table OWNER TO postgres;

-- ============================================================================
-- 边表
-- ============================================================================

-- 事件表：src/dst 使用 uuid 而非 hash
CREATE TABLE event_table (
    src_uuid        VARCHAR,        -- 源节点 UUID（反转后的方向）
    src_index_id    BIGINT,         -- 源节点 index_id
    operation       VARCHAR,        -- 事件类型
    dst_uuid        VARCHAR,        -- 目标节点 UUID（反转后的方向）
    dst_index_id    BIGINT,         -- 目标节点 index_id
    event_uuid      VARCHAR NOT NULL,
    timestamp_rec   BIGINT,         -- 纳秒级时间戳
    cmdline         VARCHAR,        -- 仅 EVENT_EXECUTE 和 EVENT_FORK 有值
    _id             SERIAL PRIMARY KEY
);
ALTER TABLE event_table OWNER TO postgres;
CREATE UNIQUE INDEX event_table__id_uindex ON event_table (_id);

-- ============================================================================
-- 索引
-- ============================================================================

CREATE INDEX idx_event_timestamp ON event_table (timestamp_rec);
CREATE INDEX idx_event_src ON event_table (src_uuid);
CREATE INDEX idx_event_dst ON event_table (dst_uuid);
CREATE INDEX idx_event_operation ON event_table (operation);

-- ============================================================================
-- 权限
-- ============================================================================

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO postgres;
