CREATE TABLE IF NOT EXISTS music_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT UNIQUE NOT NULL,
    title TEXT,
    artist TEXT,
    album TEXT,
    genre TEXT,
    track_number INTEGER,
    rating INTEGER,
    album_art_path TEXT,
    jriver_genre TEXT,
    suggested_genre TEXT,
    xml_metadata_path TEXT,
    date_imported TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
