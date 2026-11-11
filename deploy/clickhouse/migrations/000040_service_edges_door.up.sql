-- Which door an edge came through, and which attribute named a told-only callee
-- (F3 T1.0, A63 and A76).
--
-- A site key is printed in its page, so anyone can send spans through the browser
-- door under any service name. The counters already leave those spans out
-- (docs/changes/browser-door); edges still take them, so the map can draw a
-- browser's calls. F3's dependency rules must not read them, so each row now says
-- `server` or `browser`: browser when either of the call's spans came through the
-- browser door. Empty is a row written before this column existed, which nothing
-- reads as either door.
--
-- `door` joins the sorting key, at its end. Outside it, a browser row and a server
-- row for the same pair and minute would merge, and the merge would keep one door
-- for both. ClickHouse lets a sorting key gain only a column added in the same
-- ALTER, with no default expression, and the primary key stays as it was, so this
-- is a metadata change with no rewrite.
--
-- `server_peer_attr` is the attribute a told-only callee's name came from:
-- `service.peer.name`, `peer.service` or `server.address`, and empty for a callee
-- that reported for itself or was named by its technology. It is a property of the
-- call, like `server_peer_kind`, so it stays out of the key and is read with `max`.
ALTER TABLE service_edges_1m
    ADD COLUMN IF NOT EXISTS door LowCardinality(String) CODEC(ZSTD(1)),
    ADD COLUMN IF NOT EXISTS server_peer_attr LowCardinality(String) CODEC(ZSTD(1)),
    MODIFY ORDER BY (tenant_id, project_id, client_service, server_service, edge_kind, minute, door)
