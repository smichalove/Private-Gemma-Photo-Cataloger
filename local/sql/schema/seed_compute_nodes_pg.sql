-- Seed data to register initial example nodes for the compute fabric (PostgreSQL format)
INSERT INTO compute_nodes (hostname, node_type, supported_models, services, max_batch_size, is_active, description)
VALUES 
    ('localhost', 'workstation', '["gemma4:12b", "gemma4-it-q4:latest"]'::jsonb, '[{"name": "ollama", "port": 11434}]'::jsonb, 2, TRUE, 'Local fallback workstation'),
    ('gpu-server-1', 'gpu_server', '["gemma4:12b", "gemma4:31b"]'::jsonb, '[{"name": "ollama", "port": 11434}]'::jsonb, 4, TRUE, 'Example remote GPU server (RTX 4090/5090)'),
    ('jetson-edge-1', 'jetson', '["gemma4-it-q4:latest"]'::jsonb, '[{"name": "ollama", "port": 11434}]'::jsonb, 1, TRUE, 'Example Jetson Orin Edge Node'),
    ('remote-vlm-server', 'gpu_server', '["gemma4:12b"]'::jsonb, '[{"name": "vlm", "port": 8000}]'::jsonb, 4, TRUE, 'Example remote VLM FastAPI server')
ON CONFLICT (hostname) DO UPDATE SET
    node_type = EXCLUDED.node_type,
    supported_models = EXCLUDED.supported_models,
    services = EXCLUDED.services,
    max_batch_size = EXCLUDED.max_batch_size,
    is_active = EXCLUDED.is_active,
    description = EXCLUDED.description;
