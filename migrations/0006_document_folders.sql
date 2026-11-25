-- Documents added from a folder remember that folder, so the UI can group them and remove them together.
ALTER TABLE documents ADD COLUMN folder text;
CREATE INDEX documents_folder_idx ON documents (folder);
