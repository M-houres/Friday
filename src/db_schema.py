"""数据库 schema 声明。"""

CORE_SCHEMA_STATEMENTS = [
    'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"',
    'CREATE EXTENSION IF NOT EXISTS "pgcrypto"',
    """
CREATE TABLE IF NOT EXISTS agent_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT 'deepseek-chat',
    strategy TEXT NOT NULL DEFAULT 'react',
    tools TEXT[] NOT NULL DEFAULT '{}',
    config JSONB DEFAULT '{}',
    status TEXT DEFAULT 'idle',
    stats JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID REFERENCES agent_definitions(id),
    user_id TEXT NOT NULL,
    task TEXT NOT NULL,
    status TEXT DEFAULT 'created',
    degradation_level INT DEFAULT 0,
    current_step INT DEFAULT 0,
    result JSONB,
    error TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS session_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    step_index INT NOT NULL,
    type TEXT NOT NULL,
    content JSONB NOT NULL,
    model TEXT,
    tokens_used INT DEFAULT 0,
    latency_ms INT DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS session_checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    step_index INT NOT NULL,
    state JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS agent_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    task TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planning',
    plan JSONB,
    nodes_completed TEXT[] DEFAULT '{}',
    nodes_failed TEXT[] DEFAULT '{}',
    result JSONB,
    degradation_level INT DEFAULT 0,
    heartbeat_at TIMESTAMPTZ DEFAULT NOW(),
    coordinator_id TEXT,
    version INT DEFAULT 1,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error TEXT
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS workflow_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES agent_workflows(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    agent_id UUID REFERENCES agent_definitions(id),
    task TEXT NOT NULL,
    dependencies TEXT[] DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    result JSONB,
    model TEXT,
    tokens_used INT DEFAULT 0,
    cost_usd DECIMAL(10,6) DEFAULT 0,
    attempts INT DEFAULT 0,
    max_attempts INT DEFAULT 3,
    priority INT DEFAULT 5,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error TEXT
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS session_memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    summary TEXT,
    raw_messages JSONB,
    cold_storage_key TEXT,
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS long_term_memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,
    content JSONB NOT NULL,
    importance DECIMAL(3,2) DEFAULT 0.5,
    access_count INT DEFAULT 0,
    last_accessed TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS tool_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL,
    parameters JSONB NOT NULL,
    handler TEXT NOT NULL,
    is_expensive BOOLEAN DEFAULT FALSE,
    requires_approval BOOLEAN DEFAULT FALSE,
    timeout_ms INT DEFAULT 30000,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS tool_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id),
    workflow_id UUID REFERENCES agent_workflows(id),
    step_id UUID REFERENCES session_steps(id),
    tool_name TEXT NOT NULL,
    input JSONB NOT NULL,
    output JSONB,
    status TEXT DEFAULT 'pending',
    idempotency_key TEXT UNIQUE NOT NULL,
    latency_ms INT,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'closed',
    failure_count INT DEFAULT 0,
    last_failure TIMESTAMPTZ,
    last_success TIMESTAMPTZ,
    open_until TIMESTAMPTZ,
    half_open_count INT DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (provider, model)
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS task_dlq (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES agent_workflows(id),
    node_id TEXT,
    task_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    attempts INT DEFAULT 0,
    last_error TEXT,
    last_error_type TEXT,
    quarantine_reason TEXT,
    quarantined_at TIMESTAMPTZ DEFAULT NOW(),
    analyzed BOOLEAN DEFAULT FALSE,
    analysis_result JSONB,
    replayed_at TIMESTAMPTZ,
    replay_success BOOLEAN,
    archived BOOLEAN DEFAULT FALSE
)
""".strip(),
    "CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_steps_session ON session_steps(session_id, step_index)",
    "CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON session_checkpoints(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_workflows_status ON agent_workflows(status)",
    "CREATE INDEX IF NOT EXISTS idx_workflows_user ON agent_workflows(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_workflow_nodes ON workflow_nodes(workflow_id, node_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_nodes_epoch ON workflow_nodes(workflow_id, node_id)",
    "CREATE INDEX IF NOT EXISTS idx_memories_session ON session_memories(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_memories_user ON session_memories(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_ltm_user_type ON long_term_memories(user_id, type)",
    "CREATE INDEX IF NOT EXISTS idx_tool_exec_session ON tool_executions(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_tool_exec_ik ON tool_executions(idempotency_key)",
    "CREATE INDEX IF NOT EXISTS idx_dlq_type_reason ON task_dlq(task_type, quarantine_reason)",
    "CREATE INDEX IF NOT EXISTS idx_dlq_unanalyzed ON task_dlq(analyzed) WHERE NOT analyzed",
    """
CREATE INDEX IF NOT EXISTS idx_workflows_heartbeat ON agent_workflows(heartbeat_at)
    WHERE status IN ('dispatching', 'executing', 'aggregating')
""".strip(),
]

PRODUCTIZATION_SCHEMA_STATEMENTS = [
    """
CREATE TABLE IF NOT EXISTS task_durability (
    task_id TEXT PRIMARY KEY,
    status TEXT DEFAULT 'pending',
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS app_users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    password_hash TEXT DEFAULT '',
    password_salt TEXT DEFAULT '',
    email_verified BOOLEAN DEFAULT FALSE,
    last_login_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS app_user_roles (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES app_users(id) ON DELETE CASCADE,
    role_name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS product_records (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT DEFAULT 'draft',
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS result_records (
    id TEXT PRIMARY KEY,
    workflow_id TEXT UNIQUE NOT NULL,
    project_id TEXT DEFAULT '',
    page_id TEXT DEFAULT '',
    user_id TEXT NOT NULL,
    summary TEXT DEFAULT '',
    normalized_result JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS prompt_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    project_id TEXT DEFAULT '',
    scope TEXT DEFAULT 'project',
    content TEXT NOT NULL,
    variables JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id TEXT PRIMARY KEY,
    project_id TEXT DEFAULT '',
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    doc_type TEXT DEFAULT 'note',
    tags JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS async_jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INT DEFAULT 5,
    payload JSONB NOT NULL,
    result JSONB,
    error TEXT,
    worker_name TEXT DEFAULT '',
    attempts INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    project_id TEXT DEFAULT '',
    page_id TEXT DEFAULT '',
    step_id TEXT NOT NULL,
    title TEXT NOT NULL,
    detail JSONB DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    requester_user_id TEXT NOT NULL,
    reviewer_user_id TEXT DEFAULT '',
    review_comment TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS billing_plans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan_type TEXT NOT NULL DEFAULT 'subscription',
    price_cents INT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'CNY',
    interval TEXT NOT NULL DEFAULT 'month',
    credits INT NOT NULL DEFAULT 0,
    features JSONB DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS payment_orders (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    plan_id TEXT REFERENCES billing_plans(id) ON DELETE SET NULL,
    order_type TEXT NOT NULL DEFAULT 'subscription',
    amount_cents INT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'CNY',
    status TEXT NOT NULL DEFAULT 'pending',
    provider TEXT DEFAULT '',
    provider_order_id TEXT DEFAULT '',
    credits_delta INT NOT NULL DEFAULT 0,
    detail JSONB DEFAULT '{}',
    benefits_applied_at TIMESTAMPTZ,
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS user_entitlements (
    user_id TEXT PRIMARY KEY REFERENCES app_users(id) ON DELETE CASCADE,
    active_plan_id TEXT REFERENCES billing_plans(id) ON DELETE SET NULL,
    subscription_status TEXT NOT NULL DEFAULT 'inactive',
    credits_balance INT NOT NULL DEFAULT 0,
    credits_granted_total INT NOT NULL DEFAULT 0,
    credits_used_total INT NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS entitlement_ledger (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    change_type TEXT NOT NULL,
    delta_credits INT NOT NULL DEFAULT 0,
    balance_after INT NOT NULL DEFAULT 0,
    source_type TEXT DEFAULT '',
    source_id TEXT DEFAULT '',
    operator_user_id TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS ops_audit_logs (
    id TEXT PRIMARY KEY,
    actor_user_id TEXT DEFAULT '',
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    target_user_id TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'success',
    detail JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS growth_coupons (
    id TEXT PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    credits_bonus INT NOT NULL DEFAULT 0,
    max_redemptions INT NOT NULL DEFAULT 0,
    redeemed_count INT NOT NULL DEFAULT 0,
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS coupon_redemptions (
    id TEXT PRIMARY KEY,
    coupon_id TEXT REFERENCES growth_coupons(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    credits_granted INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'applied',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS trial_grants (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    grant_type TEXT NOT NULL DEFAULT 'credits',
    credits_amount INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    operator_user_id TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    applied_at TIMESTAMPTZ
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS support_tickets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    ticket_type TEXT NOT NULL DEFAULT 'general',
    title TEXT NOT NULL,
    detail JSONB DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT NOT NULL DEFAULT 'normal',
    assignee_user_id TEXT DEFAULT '',
    resolution TEXT DEFAULT '',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS appeal_records (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    appeal_type TEXT NOT NULL DEFAULT 'general',
    title TEXT NOT NULL,
    detail JSONB DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    related_resource_type TEXT DEFAULT '',
    related_resource_id TEXT DEFAULT '',
    reviewer_user_id TEXT DEFAULT '',
    decision_note TEXT DEFAULT '',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS payment_callback_events (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_event_id TEXT DEFAULT '',
    provider_order_id TEXT DEFAULT '',
    order_id TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'received',
    payload JSONB DEFAULT '{}',
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS risk_cases (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    case_type TEXT NOT NULL DEFAULT 'general',
    title TEXT NOT NULL,
    detail JSONB DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'open',
    severity TEXT NOT NULL DEFAULT 'medium',
    related_resource_type TEXT DEFAULT '',
    related_resource_id TEXT DEFAULT '',
    assignee_user_id TEXT DEFAULT '',
    resolution TEXT DEFAULT '',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
)
""".strip(),
    """
CREATE TABLE IF NOT EXISTS config_releases (
    id TEXT PRIMARY KEY,
    release_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    version_label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published',
    snapshot JSONB DEFAULT '{}',
    actor_user_id TEXT DEFAULT '',
    change_note TEXT DEFAULT '',
    rolled_back_from TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip(),
    "ALTER TABLE app_users ADD COLUMN IF NOT EXISTS password_hash TEXT DEFAULT ''",
    "ALTER TABLE app_users ADD COLUMN IF NOT EXISTS password_salt TEXT DEFAULT ''",
    "ALTER TABLE app_users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE",
    "ALTER TABLE app_users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS idx_app_user_roles_user ON app_user_roles(user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_email ON app_users(email) WHERE email <> ''",
    "CREATE INDEX IF NOT EXISTS idx_billing_plans_status ON billing_plans(status, plan_type)",
    "CREATE INDEX IF NOT EXISTS idx_entitlement_ledger_user ON entitlement_ledger(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_growth_coupons_status ON growth_coupons(status, starts_at, ends_at)",
    "CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_user ON coupon_redemptions(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trial_grants_user ON trial_grants(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_payment_callback_provider_order ON payment_callback_events(provider, provider_order_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ops_audit_logs_resource ON ops_audit_logs(resource_type, resource_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ops_audit_logs_target_user ON ops_audit_logs(target_user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_payment_orders_user_status ON payment_orders(user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_payment_orders_plan ON payment_orders(plan_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_risk_cases_user_status ON risk_cases(user_id, status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_config_releases_target ON config_releases(release_type, target_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_support_tickets_user_status ON support_tickets(user_id, status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_appeal_records_user_status ON appeal_records(user_id, status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_product_records_project ON product_records(project_id, record_type)",
    "CREATE INDEX IF NOT EXISTS idx_result_records_project ON result_records(project_id, page_id)",
    "CREATE INDEX IF NOT EXISTS idx_prompt_templates_project ON prompt_templates(project_id, category)",
    "CREATE INDEX IF NOT EXISTS idx_knowledge_documents_project ON knowledge_documents(project_id, doc_type)",
    "CREATE INDEX IF NOT EXISTS idx_async_jobs_status ON async_jobs(status, priority)",
    "CREATE INDEX IF NOT EXISTS idx_approval_requests_workflow ON approval_requests(workflow_id, status)",
    "ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS worker_name TEXT DEFAULT ''",
    "ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS attempts INT DEFAULT 0",
    "ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ",
    "ALTER TABLE payment_orders ADD COLUMN IF NOT EXISTS benefits_applied_at TIMESTAMPTZ",
]

SCHEMA_STATEMENTS = CORE_SCHEMA_STATEMENTS + PRODUCTIZATION_SCHEMA_STATEMENTS
