-- Which extractor produced a document's chunks. A stored PDF read with an older one is re-read on the next folder
-- rescan (or re-upload). NULL = before this column existed, so every PDF added from a folder is refreshed once.
ALTER TABLE documents ADD COLUMN parser text;
UPDATE documents SET parser = 'text-1' WHERE kind <> 'pdf';

-- Chunk vectors now include the document title; re-embed them all in the background.
UPDATE chunks SET embedding = NULL;
