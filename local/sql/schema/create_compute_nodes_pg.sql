-- PostgreSQL schema definition for the compute fabric registry
CREATE TABLE IF NOT EXISTS compute_nodes (
    id SERIAL PRIMARY KEY,
    hostname VARCHAR(100) UNIQUE NOT NULL,
    node_type VARCHAR(50) NOT NULL,
    supported_models JSONB NOT NULL,
    services JSONB NOT NULL,
    max_batch_size INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    description TEXT
);

CREATE INDEX IF NOT EXISTS idx_compute_nodes_hostname ON compute_nodes (hostname);
