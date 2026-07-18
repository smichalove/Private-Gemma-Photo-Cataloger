-- Query to retrieve all track metadata fields (used for target directory processing)
SELECT file_path, title, artist, album, genre, album_art_path, is_video 
FROM music_tracks;
