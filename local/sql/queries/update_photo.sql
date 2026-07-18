UPDATE photos 
SET 
    rel_path = ?,
    detected_faces = ?,
    acdsee_tags = ?,
    rating = ?,
    label = ?,
    author = ?,
    gps_latitude = ?,
    gps_longitude = ?,
    gps_altitude = ?,
    raw_metadata = ?,
    acdsee_metadata_imported_at = ?,
    file_mtime = ?
WHERE full_path = ?;
