CREATE TABLE runs (
  id TEXT PRIMARY KEY,
  agent TEXT NOT NULL,
  phase TEXT,
  subject TEXT NOT NULL,
  trigger TEXT,
  issue_number INTEGER,
  pr_number INTEGER,
  dispatched_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'dispatched',
  stop_reason TEXT,
  model_calls INTEGER NOT NULL DEFAULT 0,
  reserved_calls INTEGER NOT NULL DEFAULT 0,
  workflow_run_url TEXT
);
CREATE INDEX runs_status ON runs(status);
CREATE INDEX runs_dispatched ON runs(dispatched_at);

CREATE TABLE locks (
  agent TEXT NOT NULL,
  subject TEXT NOT NULL,
  run_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (agent, subject)
);

CREATE TABLE events (
  delivery_id TEXT PRIMARY KEY,
  received_at TEXT NOT NULL,
  event TEXT,
  action TEXT
);

CREATE TABLE config (
  key TEXT PRIMARY KEY,
  value TEXT
);
INSERT INTO config (key, value) VALUES ('paused', '0');

CREATE TABLE tokens (
  key TEXT PRIMARY KEY,
  token TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
