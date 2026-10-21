-- Payment Incident Investigator: core schema.
-- Applies to Supabase Postgres and to plain Postgres (docker-compose). On plain Postgres the
-- auth.uid() shim below lets the same Row Level Security policies compile.

create extension if not exists pgcrypto;

-- Google ADK session tables live in their own schema so they are never exposed through the
-- public API schema (Supabase exposes only `public` by default).
create schema if not exists adk;

create schema if not exists auth;
do $$
begin
  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'auth' and p.proname = 'uid'
  ) then
    create function auth.uid() returns uuid language sql stable as
      $f$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $f$;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- Tenancy
-- ---------------------------------------------------------------------------
create table if not exists organizations (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  created_at timestamptz not null default now()
);

create table if not exists memberships (
  org_id uuid not null references organizations(id) on delete cascade,
  user_id uuid not null,
  role text not null check (role in ('analyst', 'approver', 'admin')),
  display_name text not null,
  created_at timestamptz not null default now(),
  primary key (org_id, user_id)
);

create table if not exists provider_connections (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  provider text not null check (provider in ('stripe')),
  environment text not null check (environment in ('sandbox', 'simulated')),
  account_ref text not null,
  -- Names of server-side secrets; never the secrets themselves.
  secret_ref text not null,
  webhook_secret_ref text not null,
  created_at timestamptz not null default now(),
  unique (org_id, provider, account_ref)
);

-- ---------------------------------------------------------------------------
-- Merchant application state (written by the sample checkout)
-- ---------------------------------------------------------------------------
create table if not exists orders (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  external_ref text not null,
  customer_ref text not null,            -- redacted/opaque customer handle, never a raw email
  amount_minor bigint not null check (amount_minor > 0),
  currency text not null,
  status text not null check (status in ('pending', 'requires_action', 'paid', 'failed', 'needs_review')),
  version integer not null default 1,
  checkout_response text,                -- what the customer saw: success | failure | lost | requires_action
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (org_id, external_ref)
);

create table if not exists payment_attempts (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  order_id uuid not null references orders(id) on delete cascade,
  provider_connection_id uuid not null references provider_connections(id),
  provider_payment_ref text not null,    -- e.g. pi_...
  idempotency_key text not null,
  status text not null,                  -- last status the merchant observed from the provider
  amount_minor bigint not null,
  currency text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (org_id, provider_payment_ref),
  unique (org_id, idempotency_key)
);
create index if not exists payment_attempts_order_idx on payment_attempts(order_id);

create table if not exists provider_events (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  provider_connection_id uuid not null references provider_connections(id),
  provider_event_id text not null,       -- e.g. evt_...
  event_type text not null,
  provider_payment_ref text,
  environment text not null check (environment in ('sandbox', 'simulated')),
  signature_verified boolean not null,
  payload jsonb not null,                -- redacted event payload
  event_time timestamptz not null,       -- provider's created time
  received_at timestamptz not null default now(),   -- first ingestion time
  last_received_at timestamptz not null default now(),
  delivery_count integer not null default 1,
  unique (org_id, provider_event_id)
);
create index if not exists provider_events_payment_idx on provider_events(org_id, provider_payment_ref);

-- Durable identity of business effects. One row per effect; a second insert is a no-op.
create table if not exists business_operations (
  org_id uuid not null references organizations(id) on delete cascade,
  op_key text not null,
  provider_event_row_id uuid references provider_events(id),
  applied_at timestamptz not null default now(),
  primary key (org_id, op_key)
);

create table if not exists processing_attempts (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  provider_event_row_id uuid not null references provider_events(id) on delete cascade,
  order_id uuid references orders(id),
  triggered_by text not null check (triggered_by in ('webhook', 'reprocess')),
  recovery_job_id uuid,
  business_op_key text,
  outcome text not null check (outcome in ('running', 'applied', 'duplicate', 'failed', 'skipped')),
  error text,
  order_version_before integer,
  order_version_after integer,
  started_at timestamptz not null default now(),
  finished_at timestamptz
);
create index if not exists processing_attempts_event_idx on processing_attempts(provider_event_row_id);
create unique index if not exists processing_attempts_job_idx on processing_attempts(recovery_job_id) where recovery_job_id is not null;

create table if not exists application_logs (
  id bigserial primary key,
  org_id uuid not null references organizations(id) on delete cascade,
  ts timestamptz not null default now(),
  level text not null,
  component text not null,
  event text not null,
  order_id uuid,
  provider_payment_ref text,
  provider_event_id text,
  request_id text,
  message text not null,
  fields jsonb not null default '{}'::jsonb
);
create index if not exists application_logs_order_idx on application_logs(org_id, order_id, ts);
create index if not exists application_logs_payment_idx on application_logs(org_id, provider_payment_ref, ts);

-- Simulated provider state (only used when PROVIDER_MODE=simulated). Labeled as such in evidence.
create table if not exists simulated_provider_payments (
  org_id uuid not null references organizations(id) on delete cascade,
  provider_payment_ref text not null,
  status text not null,
  amount_minor bigint not null,
  currency text not null,
  idempotency_key text,
  metadata jsonb not null default '{}'::jsonb,
  lookup_unavailable boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (org_id, provider_payment_ref)
);

-- Local fault injection registry. This is test scaffolding: the investigator has no tool that
-- reads it, and evaluation ground truth is kept outside the database.
create table if not exists fault_injections (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  order_id uuid references orders(id) on delete cascade,
  kind text not null,
  params jsonb not null default '{}'::jsonb,
  remaining integer not null default 1,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Incident workspace (written by the API, agent and worker)
-- ---------------------------------------------------------------------------
create table if not exists incidents (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  created_by uuid not null,
  assigned_to uuid,
  status text not null check (status in (
    'open', 'investigating', 'awaiting_evidence', 'awaiting_approval',
    'recovering', 'verifying', 'resolved', 'escalated', 'needs_review', 'failed')),
  source_type text not null check (source_type in ('order_id', 'payment_ref', 'complaint')),
  source_value text not null,
  complaint_text text,
  order_id uuid references orders(id),
  summary text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists incidents_org_idx on incidents(org_id, created_at desc);

create table if not exists evidence_items (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  incident_id uuid not null references incidents(id) on delete cascade,
  source text not null check (source in (
    'order_store', 'payment_attempts', 'provider', 'webhook_store', 'processing_history',
    'application_logs', 'operator', 'evidence_gap')),
  source_id text not null,
  environment text not null,             -- sandbox | simulated | application | operator
  retrieved_at timestamptz not null default now(),
  event_time timestamptz,
  ingested_at timestamptz,
  label text not null,
  fields jsonb not null default '{}'::jsonb,
  unique (incident_id, source, source_id)
);

create table if not exists diagnoses (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  incident_id uuid not null references incidents(id) on delete cascade,
  category text not null,
  observed_inconsistency text not null,
  explanation text not null,
  limits text not null,
  supporting_evidence_ids uuid[] not null default '{}',
  conflicting_evidence text[] not null default '{}',
  missing_evidence text[] not null default '{}',
  next_step text not null,
  recommended_action text not null,      -- reprocess_stored_event | request_information | escalate | none
  produced_by text not null,             -- adk | deterministic
  model_version text,
  prompt_version text,
  created_at timestamptz not null default now()
);

create table if not exists action_proposals (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  incident_id uuid not null references incidents(id) on delete cascade,
  action_type text not null check (action_type in ('reprocess_stored_event')),
  provider_event_row_id uuid not null references provider_events(id),
  order_id uuid not null references orders(id),
  order_version integer not null,
  rationale text not null,
  guard_checks jsonb not null default '[]'::jsonb,
  snapshot jsonb not null default '{}'::jsonb,   -- state captured at proposal time (attempt counts, provider status)
  status text not null check (status in ('proposed', 'approved', 'rejected', 'superseded', 'executed', 'failed')),
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists approvals (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  incident_id uuid not null references incidents(id) on delete cascade,
  proposal_id uuid not null references action_proposals(id),
  approved_by uuid not null,
  action_type text not null,
  provider_event_row_id uuid not null,
  order_id uuid not null,
  order_version integer not null,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  revoke_reason text,
  created_at timestamptz not null default now()
);

create table if not exists recovery_jobs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  incident_id uuid not null references incidents(id) on delete cascade,
  proposal_id uuid not null references action_proposals(id),
  approval_id uuid not null references approvals(id),
  status text not null check (status in ('queued', 'running', 'succeeded', 'failed', 'rejected')),
  attempt_count integer not null default 0,
  locked_at timestamptz,
  locked_by text,
  result jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists recovery_jobs_status_idx on recovery_jobs(status, created_at);

create table if not exists verification_results (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  incident_id uuid not null references incidents(id) on delete cascade,
  recovery_job_id uuid not null references recovery_jobs(id),
  passed boolean not null,
  checks jsonb not null,
  created_at timestamptz not null default now()
);

create table if not exists audit_events (
  id bigserial primary key,
  org_id uuid not null references organizations(id) on delete cascade,
  incident_id uuid references incidents(id) on delete cascade,
  actor_type text not null check (actor_type in ('operator', 'agent', 'worker', 'system')),
  actor_id text not null,
  action text not null,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists audit_events_incident_idx on audit_events(incident_id, id);

create table if not exists agent_runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  incident_id uuid not null references incidents(id) on delete cascade,
  session_id text not null,
  mode text not null,
  model text,
  prompt_version text,
  status text not null,
  tool_calls integer not null default 0,
  input_tokens integer,
  output_tokens integer,
  latency_ms integer,
  trace jsonb not null default '[]'::jsonb,
  error text,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Row Level Security: the frontend only ever sees rows for organizations the
-- user belongs to. Backend/worker connections bypass RLS (service role) and
-- enforce org scope explicitly in code.
-- ---------------------------------------------------------------------------
create or replace function app_is_member(target_org uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from memberships m where m.org_id = target_org and m.user_id = auth.uid());
$$;

do $$
declare t text;
begin
  foreach t in array array[
    'provider_connections','orders','payment_attempts','provider_events','business_operations',
    'processing_attempts','application_logs','simulated_provider_payments','fault_injections',
    'incidents','evidence_items','diagnoses','action_proposals','approvals','recovery_jobs',
    'verification_results','audit_events','agent_runs']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists %I on %I', t || '_member_read', t);
    execute format('create policy %I on %I for select using (app_is_member(org_id))', t || '_member_read', t);
  end loop;
end $$;

alter table organizations enable row level security;
drop policy if exists organizations_member_read on organizations;
create policy organizations_member_read on organizations for select using (app_is_member(id));
alter table memberships enable row level security;
drop policy if exists memberships_self_read on memberships;
create policy memberships_self_read on memberships for select using (user_id = auth.uid() or app_is_member(org_id));

-- Supabase Realtime (no-op on plain Postgres where the publication does not exist).
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table incidents, recovery_jobs, action_proposals;
  end if;
exception when others then null;
end $$;
