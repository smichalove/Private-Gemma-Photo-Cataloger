CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_path TEXT UNIQUE NOT NULL,
    rel_path TEXT NOT NULL,
    primary_subject TEXT,
    environment TEXT,
    suggested_tags TEXT,
    technical_details TEXT,
    detected_objects TEXT
);
