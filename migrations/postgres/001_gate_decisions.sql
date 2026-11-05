-- Looper3 gate decision audit table (FR-9 / M7) for Postgres (Supabase).
-- NOT applied by this project (paper mode, no network; NFR-4 is open). Column set must match
-- the SQLite table in src/audit/auditStore.ts - tests/audit.test.ts checks the parity.
--
-- Connect on the direct or session pooler URL, never the transaction pooler (port 6543) - NFR-4.

create table if not exists gate_decisions (
  idem_key       text primary key,            -- sha256(thread_id + tool_call_id)
  thread_id      text not null,
  tool           text not null,
  tier           smallint not null check (tier between 1 and 4),
  effective_mode text not null check (effective_mode in ('in_loop','on_loop','autonomous')),
  binding        text not null,
  floor_source   text not null,
  harness        text not null,
  sme_id         text not null,
  decision       text not null check (decision in ('approved','rejected','vetoed','timeout')),
  decided_by     text not null check (decided_by in ('policy','human','timeout')),
  config_version text not null,
  latency_ms     integer not null check (latency_ms >= 0),
  reject_reason  text,
  created_at     timestamptz not null default now()
);

create index if not exists gate_decisions_created_at on gate_decisions (created_at);

-- Append-only, twice over: no grant to change rows, and a trigger that refuses even the owner.
revoke update, delete, truncate on gate_decisions from public;

create or replace function gate_decisions_append_only() returns trigger
language plpgsql as $$
begin
  raise exception 'gate_decisions is append-only: % is rejected', tg_op;
end;
$$;

drop trigger if exists gate_decisions_no_update_delete on gate_decisions;
create trigger gate_decisions_no_update_delete
  before update or delete on gate_decisions
  for each row execute function gate_decisions_append_only();

drop trigger if exists gate_decisions_no_truncate on gate_decisions;
create trigger gate_decisions_no_truncate
  before truncate on gate_decisions
  for each statement execute function gate_decisions_append_only();
