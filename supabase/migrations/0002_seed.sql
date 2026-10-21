-- Demonstration seed: two isolated organizations, operators with distinct roles, and one
-- provider connection each. User IDs are fixed so AUTH_MODE=dev can reference them.
insert into organizations (id, slug, name) values
  ('11111111-1111-1111-1111-111111111111', 'northwind', 'Northwind Outfitters'),
  ('22222222-2222-2222-2222-222222222222', 'acme', 'Acme Tickets')
on conflict (id) do nothing;

insert into memberships (org_id, user_id, role, display_name) values
  ('11111111-1111-1111-1111-111111111111', 'aaaaaaaa-0000-0000-0000-000000000001', 'analyst',  'Nia (Northwind analyst)'),
  ('11111111-1111-1111-1111-111111111111', 'aaaaaaaa-0000-0000-0000-000000000002', 'approver', 'Omar (Northwind approver)'),
  ('22222222-2222-2222-2222-222222222222', 'bbbbbbbb-0000-0000-0000-000000000001', 'admin',    'Ada (Acme admin)')
on conflict do nothing;

insert into provider_connections (id, org_id, provider, environment, account_ref, secret_ref, webhook_secret_ref) values
  ('33333333-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'stripe', 'simulated', 'acct_sim_northwind', 'STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET'),
  ('33333333-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222', 'stripe', 'simulated', 'acct_sim_acme', 'STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET')
on conflict (id) do nothing;
