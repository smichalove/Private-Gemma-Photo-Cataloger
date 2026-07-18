-- Query to retrieve music tracks with unidentified artists.
-- Filters:
--   1. (is_video = FALSE OR is_video IS NULL) to isolate audio tracks.
--   2. artist matches unknown/unresolved variants. Excludes 'Non-Music' to prevent loops.

SELECT file_path, title, artist, album, genre, album_art_path, is_video 
FROM music_tracks 
WHERE (is_video = FALSE OR is_video IS NULL) 
  AND (artist = 'Unknown Artist' OR artist IS NULL OR artist = 'Unknown' OR artist LIKE '%common artist name%');
