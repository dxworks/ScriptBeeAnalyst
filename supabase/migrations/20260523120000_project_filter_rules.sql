-- Per-project filter rules: agent-authored exclusion rules that the
-- data-server applies at view time via FilteredSandboxView. Each row is a
-- single-entity-kind predicate (see data-server/src/filter_rules/models.py)
-- the user described in natural language and the OpenCode assistant
-- translated into a structured DSL. Single-tenant local mode — no RLS, no
-- user attribution; realtime stays on so the web-ui's "Exclusion Rules"
-- tab refreshes without polling.

create table public.project_filter_rules (
  id uuid primary key default uuid_generate_v4(),
  project_id uuid not null references public.projects(id) on delete cascade,
  entity_kind text not null,
  name text not null,
  nl_description text not null,
  dsl jsonb not null,
  created_at timestamp with time zone default now() not null
);

create index project_filter_rules_project_id_idx
  on public.project_filter_rules(project_id);

alter publication supabase_realtime add table public.project_filter_rules;
