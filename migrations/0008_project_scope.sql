-- Memories and documents can belong to a project. Optional: untagged items stay global.
-- Retrieval ranks items from the chat's focus project higher; it never hides the rest.
ALTER TABLE memories ADD COLUMN project_id uuid REFERENCES projects(id) ON DELETE SET NULL;
ALTER TABLE documents ADD COLUMN project_id uuid REFERENCES projects(id) ON DELETE SET NULL;
CREATE INDEX memories_project_idx ON memories (project_id);
CREATE INDEX documents_project_idx ON documents (project_id);
