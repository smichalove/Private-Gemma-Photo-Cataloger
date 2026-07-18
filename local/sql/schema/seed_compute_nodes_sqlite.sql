-- Seed data to register initial example nodes for the compute fabric (SQLite format)
INSERT INTO compute_nodes (hostname, node_type, supported_models, services, max_batch_size, is_active, description)
VALUES 
    ('localhost', 'workstation', '["gemma4:12b", "gemma4-it-q4:latest"]', '[{"name": "ollama", "port": 11434}]', 2, 1, 'Local fallback workstation'),
    ('gpu-server-1', 'gpu_server', '["gemma4:12b", "gemma4:31b"]', '[{"name": "ollama", "port": 11434}]', 4, 1, 'Example remote GPU server (RTX 4090/5090)'),
    ('jetson-edge-1', 'jetson', '["gemma4-it-q4:latest"]', '[{"name": "ollama", "port": 11434}]', 1, 1, 'Example Jetson Orin Edge Node'),
    ('remote-vlm-server', 'gpu_server', '["gemma4:12b"]', '[{"name": "vlm", "port": 8000}]', 4, 1, 'Example remote VLM FastAPI server')
ON CONFLICT (hostname) DO UPDATE SET
    node_type = EXCLUDED.node_type,
    supported_models = EXCLUDED.supported_models,
    services = EXCLUDED.services,
    max_batch_size = EXCLUDED.max_batch_size,
    is_active = EXCLUDED.is_active,
    description = EXCLUDED.description;
