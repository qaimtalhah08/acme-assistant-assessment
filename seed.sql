-- ============================================================
-- seed.sql — Acme Operations Sample Data
-- ============================================================

-- ─── Table 1: customers ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS customers (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(100) NOT NULL,
    email      VARCHAR(100),
    phone      VARCHAR(20),
    company    VARCHAR(100),
    country    VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO customers (name, email, phone, company, country) VALUES
('James Miller',  'james@acmecorp.com',      '+44 20 1234 5678', 'Acme Corp',     'UK'),
('Sarah Ahmed',   'sarah@techstart.com',     '+44 16 1234 5678', 'TechStart Ltd', 'UK'),
('Robert Singh',  'robert@globalretail.com', '+44 12 1234 5678', 'GlobalRetail',  'UK'),
('Emily Watson',  'emily@finbridge.com',     '+44 11 1234 5678', 'FinBridge',     'UK'),
('Omar Abdullah', 'omar@cloudnova.com',      '+44 13 1234 5678', 'CloudNova',     'UK');


-- ─── Table 2: issues ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS issues (
    id          SERIAL PRIMARY KEY,
    customer_id INT REFERENCES customers(id),
    title       VARCHAR(200) NOT NULL,
    description TEXT,
    status      VARCHAR(20) DEFAULT 'open',
    priority    VARCHAR(20) DEFAULT 'medium',
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

INSERT INTO issues (customer_id, title, description, status, priority) VALUES
(1, 'Login portal not working',
    'Users cannot log into the customer portal since Monday morning.',
    'open', 'critical'),

(1, 'Invoice amount mismatch',
    'Last 3 invoices show wrong amounts. Finance team is concerned.',
    'open', 'high'),

(1, 'Slow dashboard loading',
    'Dashboard takes over 30 seconds to load.',
    'in_progress', 'medium'),

(2, 'API timeout errors',
    'Integration API timing out after 5 seconds under load.',
    'open', 'critical'),

(2, 'Data export failing',
    'CSV export button does nothing. Bug reported by 3 users.',
    'open', 'high'),

(3, 'Password reset email not sending',
    'Users not receiving password reset emails since last deployment.',
    'open', 'high'),

(3, 'Mobile app crashes on login',
    'iOS app crashes immediately after entering credentials.',
    'closed', 'medium'),

(4, 'Payment gateway integration broken',
    'Stripe payments failing with 402 error. Revenue impact.',
    'open', 'critical'),

(5, 'SSL certificate expired',
    'SSL cert expired on staging environment. Blocking QA testing.',
    'open', 'low');


-- ─── Table 3: issue_updates ─────────────────────────────────
CREATE TABLE IF NOT EXISTS issue_updates (
    id         SERIAL PRIMARY KEY,
    issue_id   INT REFERENCES issues(id),
    updated_by VARCHAR(100),
    note       TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO issue_updates (issue_id, updated_by, note, created_at) VALUES
(1, 'support@acme.com',  'Ticket raised. Customer called in.',
    NOW() - INTERVAL '3 days'),
(1, 'engineer@acme.com', 'Root cause: auth service down.',
    NOW() - INTERVAL '2 days'),
(1, 'engineer@acme.com', 'Fix deployed to staging. Testing in progress.',
    NOW() - INTERVAL '1 day'),

(2, 'support@acme.com',  'Customer reported via email. 3 invoices affected.',
    NOW() - INTERVAL '5 days'),
(2, 'finance@acme.com',  'Finance team reviewing. Possible billing system bug.',
    NOW() - INTERVAL '4 days'),

(3, 'support@acme.com',  'Performance issue logged.',
    NOW() - INTERVAL '7 days'),
(3, 'engineer@acme.com', 'DB query optimisation started. ETA 2 days.',
    NOW() - INTERVAL '3 days'),

(4, 'support@acme.com',  'Critical issue raised. Engineering team alerted.',
    NOW() - INTERVAL '2 days'),
(4, 'engineer@acme.com', 'Load testing started. Timeout at 1000 req/min.',
    NOW() - INTERVAL '1 day'),

(6, 'support@acme.com',  'Email issue reported. Checking SMTP config.',
    NOW() - INTERVAL '4 days'),
(6, 'engineer@acme.com', 'SMTP credentials expired. New creds requested.',
    NOW() - INTERVAL '2 days'),

(8, 'support@acme.com',  'URGENT: Payment failures causing revenue loss.',
    NOW() - INTERVAL '1 day'),
(8, 'engineer@acme.com', 'Stripe API key rotated last night. Checking.',
    NOW() - INTERVAL '12 hours');


-- ─── Table 4: next_actions ──────────────────────────────────
CREATE TABLE IF NOT EXISTS next_actions (
    id          SERIAL PRIMARY KEY,
    issue_id    INT REFERENCES issues(id),
    action_text TEXT NOT NULL,
    created_by  VARCHAR(100),
    status      VARCHAR(20) DEFAULT 'pending',
    created_at  TIMESTAMP DEFAULT NOW()
);

INSERT INTO next_actions (issue_id, action_text, created_by, status) VALUES
(1, 'Deploy auth service fix to production and notify customer.',
    'agent', 'pending'),
(2, 'Schedule call with finance team to audit billing records.',
    'adminuser', 'pending'),
(4, 'Scale API server horizontally to handle load.',
    'agent', 'pending'),
(8, 'Verify Stripe API key is active and retest payment flow.',
    'agent', 'pending');


-- ─── Table 5: user_roles ────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_roles (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(100) NOT NULL,
    email       VARCHAR(100),
    role        VARCHAR(50),
    created_at  TIMESTAMP DEFAULT NOW()
);

INSERT INTO user_roles (username, email, role) VALUES
('salesuser',   'sales@acme.com',   'sales_user'),
('supportuser', 'support@acme.com', 'support_user'),
('adminuser',   'admin@acme.com',   'admin');