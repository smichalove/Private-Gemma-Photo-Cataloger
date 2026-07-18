SELECT album, ARRAY_AGG(DISTINCT genre) as genres
FROM music_tracks
WHERE album IS NOT NULL AND album != 'Unknown Album' AND album != 'Non-Music'
GROUP BY album
HAVING COUNT(DISTINCT genre) > 1;
