SELECT full_path, file_mtime, acdsee_metadata_imported_at 
FROM photos 
WHERE acdsee_metadata_imported_at IS NOT NULL;
