CREATE UNIQUE INDEX IF NOT EXISTS idx_photos_full_path ON photos (full_path);
CREATE INDEX IF NOT EXISTS idx_photos_rel_path ON photos (rel_path);
