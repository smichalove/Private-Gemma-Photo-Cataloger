INSERT INTO photos (
    full_path, 
    rel_path, 
    primary_subject, 
    environment, 
    suggested_tags, 
    technical_details, 
    detected_objects
) VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(full_path) DO UPDATE SET
    rel_path=excluded.rel_path,
    primary_subject=excluded.primary_subject,
    environment=excluded.environment,
    suggested_tags=excluded.suggested_tags,
    technical_details=excluded.technical_details,
    detected_objects=excluded.detected_objects;
