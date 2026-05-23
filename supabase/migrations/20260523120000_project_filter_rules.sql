-- Per-project filter rules: agent-authored exclusion rules that the
-- data-server applies at view time via FilteredSandboxView. Each row is a
-- single-entity-kind predicate (see data-server/src/filter_rules/models.py)
-- the user described in natural language and the OpenCode assistant
-- translated into a structured DSL.
--
-- Mutating writes are user-scoped (RLS by user_id = auth.uid()); the
-- data-server uses get_user_client(jwt) on POST/DELETE so a row's user_id
-- is auth.uid() at insert time. Realtime is enabled so the web-ui's
-- "Exclusion Rules" tab refreshes without polling.
--
-- user_id is nullable: dev/standalone mode has no JWT to attribute the
-- row to, so the data-server inserts NULL rather than impersonate the
-- project owner. RLS policies treat NULL as "not anyone's row" — only
-- the service-role client (used by the in-memory cache) can read them.

create table public.project_filter_rules (
  id uuid primary key default uuid_generate_v4(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid references auth.users(id) on delete cascade,
  entity_kind text not null,
  name text not null,
  nl_description text not null,
  dsl jsonb not null,
  created_at timestamp with time zone default now() not null
);

create index project_filter_rules_project_id_idx
  on public.project_filter_rules(project_id);
create index project_filter_rules_user_id_idx
  on public.project_filter_rules(user_id);

alter table public.project_filter_rules enable row level security;

create policy "Users can view own filter rules"
  on public.project_filter_rules for select
  using ((select auth.uid()) = user_id);

create policy "Users can create own filter rules"
  on public.project_filter_rules for insert
  with check ((select auth.uid()) = user_id);

create policy "Users can delete own filter rules"
  on public.project_filter_rules for delete
  using ((select auth.uid()) = user_id);

alter publication supabase_realtime add table public.project_filter_rules;
