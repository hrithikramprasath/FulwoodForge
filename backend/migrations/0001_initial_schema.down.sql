-- Fulwood Forge — Wave 2 Initial MySQL 8.4 LTS InnoDB Schema (DOWN Migration)
-- Drops all 9 tables in strict reverse foreign-key dependency order.

DROP TABLE IF EXISTS audit_events;
DROP TABLE IF EXISTS token_ledger;
DROP TABLE IF EXISTS idempotency_keys;
DROP TABLE IF EXISTS artifacts;
DROP TABLE IF EXISTS lesson_generations;
DROP TABLE IF EXISTS invite_codes;
DROP TABLE IF EXISTS user_sessions;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS system_settings;
