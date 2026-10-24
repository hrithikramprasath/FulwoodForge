-- Fulwood Forge — Wave 2 Initial MySQL 8.4 LTS InnoDB Schema (UP Migration)
-- Enforces BIGINT UNSIGNED AUTO_INCREMENT clustered PKs, BINARY(16) RFC 9562 UUIDv7
-- public identifiers, DECIMAL(19,4) cost accounting, DATETIME(6) UTC timestamps,
-- and composite keyset pagination indexes.

CREATE TABLE IF NOT EXISTS system_settings (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    registration_mode ENUM('INVITE_ONLY', 'ADMIN_APPROVAL', 'OPEN') NOT NULL DEFAULT 'INVITE_ONLY',
    default_hourly_quota INT UNSIGNED NOT NULL DEFAULT 30,
    maintenance_mode TINYINT(1) NOT NULL DEFAULT 0,
    allowed_subjects_json JSON NOT NULL,
    allowed_task_types_json JSON NOT NULL,
    updated_by_user_id BIGINT UNSIGNED NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS users (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    username VARCHAR(32) NOT NULL UNIQUE,
    display_name VARCHAR(64) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('USER', 'ADMIN') NOT NULL DEFAULT 'USER',
    status ENUM('ACTIVE', 'PENDING', 'SUSPENDED') NOT NULL DEFAULT 'ACTIVE',
    must_change_password TINYINT(1) NOT NULL DEFAULT 0,
    hourly_generation_limit INT UNSIGNED NOT NULL DEFAULT 30,
    last_login_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    INDEX idx_users_status_created (status, created_at DESC, id DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS user_sessions (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    user_id BIGINT UNSIGNED NOT NULL,
    token_sha256 BINARY(32) NOT NULL UNIQUE,
    ip_address VARCHAR(45) NOT NULL,
    user_agent VARCHAR(255) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    revoked_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_sessions_user_active (user_id, revoked_at, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS invite_codes (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    code VARCHAR(32) NOT NULL UNIQUE,
    max_uses INT UNSIGNED NOT NULL DEFAULT 1,
    uses_count INT UNSIGNED NOT NULL DEFAULT 0,
    expires_at DATETIME(6) NULL,
    created_by_user_id BIGINT UNSIGNED NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_invites_creator FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
    INDEX idx_invites_active_created (is_active, created_at DESC, id DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS lesson_generations (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    user_id BIGINT UNSIGNED NOT NULL,
    request_hash BINARY(32) NOT NULL,
    subject VARCHAR(64) NOT NULL,
    year_group VARCHAR(32) NOT NULL,
    tier VARCHAR(32) NULL,
    exam_board VARCHAR(32) NULL,
    topic VARCHAR(1000) NOT NULL,
    task_type ENUM('worksheet', 'powerpoint', 'booklet') NOT NULL,
    title VARCHAR(255) NOT NULL,
    content_json JSON NOT NULL,
    slide_count INT UNSIGNED NOT NULL DEFAULT 0,
    is_archived TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_generations_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_generations_user_hash_created (user_id, request_hash, created_at DESC),
    INDEX idx_generations_user_keyset (user_id, is_archived, created_at DESC, id DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS artifacts (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    generation_id BIGINT UNSIGNED NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,
    format ENUM('docx', 'pptx', 'pdf', 'answers-docx', 'answers-pdf') NOT NULL,
    file_path VARCHAR(512) NULL,
    sha256_checksum BINARY(32) NOT NULL,
    byte_size BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_artifacts_generation FOREIGN KEY (generation_id) REFERENCES lesson_generations(id) ON DELETE CASCADE,
    CONSTRAINT fk_artifacts_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_artifacts_gen_format (generation_id, format)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS idempotency_keys (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT UNSIGNED NOT NULL,
    idempotency_key VARCHAR(64) NOT NULL,
    request_hash BINARY(32) NOT NULL,
    status ENUM('IN_PROGRESS', 'COMPLETED', 'FAILED') NOT NULL DEFAULT 'IN_PROGRESS',
    response_status SMALLINT UNSIGNED NULL,
    response_body_json JSON NULL,
    expires_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_idempotency_user_key (user_id, idempotency_key),
    CONSTRAINT fk_idempotency_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_idempotency_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS token_ledger (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    user_id BIGINT UNSIGNED NOT NULL,
    generation_id BIGINT UNSIGNED NULL,
    model_name VARCHAR(64) NOT NULL,
    prompt_tokens INT UNSIGNED NOT NULL DEFAULT 0,
    candidate_tokens INT UNSIGNED NOT NULL DEFAULT 0,
    total_tokens INT UNSIGNED NOT NULL DEFAULT 0,
    estimated_cost_usd DECIMAL(19,4) NOT NULL DEFAULT 0.0000,
    latency_ms INT UNSIGNED NOT NULL DEFAULT 0,
    cache_hit TINYINT(1) NOT NULL DEFAULT 0,
    status ENUM('SUCCESS', 'CACHE_HIT', 'UPSTREAM_ERROR') NOT NULL DEFAULT 'SUCCESS',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_ledger_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_ledger_generation FOREIGN KEY (generation_id) REFERENCES lesson_generations(id) ON DELETE SET NULL,
    INDEX idx_ledger_user_created (user_id, created_at DESC, id DESC),
    INDEX idx_ledger_created_keyset (created_at DESC, id DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS audit_events (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    actor_user_id BIGINT UNSIGNED NULL,
    target_user_id BIGINT UNSIGNED NULL,
    event_type VARCHAR(64) NOT NULL,
    ip_address VARCHAR(45) NOT NULL,
    metadata_json JSON NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_audit_actor FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT fk_audit_target FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_audit_keyset (created_at DESC, id DESC),
    INDEX idx_audit_actor_keyset (actor_user_id, created_at DESC, id DESC),
    INDEX idx_audit_type_keyset (event_type, created_at DESC, id DESC),
    INDEX idx_audit_type_ip_created (event_type, ip_address, created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
