-- Daily paid budget (CONTRACTS.md, Daily paid budget): estimated paid-key spend reported at finished, and the
-- allowance handed out at started (counted against today's cap while the run is in flight).
ALTER TABLE runs ADD COLUMN paid_cost_usd REAL NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN paid_reserved_usd REAL NOT NULL DEFAULT 0;
