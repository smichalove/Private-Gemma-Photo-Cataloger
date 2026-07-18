-- SQLite schema definition for the compute fabric registry (staging parity)
CREATE TABLE IF NOT EXISTS compute_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT UNIQUE NOT NULL,
    node_type TEXT NOT NULL,
    supported_models TEXT NOT NULL, -- JSON string representation
    services TEXT NOT NULL,         -- JSON string representation
    max_batch_size INTEGER DEFAULT 1,
    is_active INTEGER DEFAULT 1,    -- 1 for True, 0 for False
    description TEXT
);

CREATE INDEX IF NOT EXISTS idx_compute_nodes_hostname ON compute_nodes (hostname);
