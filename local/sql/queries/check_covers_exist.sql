SELECT full_path FROM photos WHERE full_path = ANY(?) AND primary_subject IS NOT NULL;
