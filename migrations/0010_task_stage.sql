-- Task board column for open tasks. status stays open/done (everything that counts "open" keeps working);
-- stage only says where an open task sits on the board: to do or in progress.
ALTER TABLE tasks ADD COLUMN stage text NOT NULL DEFAULT 'todo' CHECK (stage IN ('todo', 'doing'));
