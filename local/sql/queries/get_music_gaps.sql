SELECT file_path, title, artist, album, genre, album_art_path, is_video 
FROM music_tracks 
WHERE artist = 'Unknown Artist' OR artist IS NULL
   OR genre = 'Unknown Genre' OR genre IS NULL
   OR album = 'Unknown Album' OR album IS NULL;
