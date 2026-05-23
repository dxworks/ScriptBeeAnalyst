-- Per-project component mapping: a JSONB blob on the projects row that
-- replaces the operator-level EnrichmentConfig.components_mapping_path knob.
-- The data-server reads this column at build time, hands the dict to
-- EnrichmentConfig.components_mapping_data, and the path field stays as a
-- dev/test fallback when this column is null.
--
-- Payload shape (mirrors src/common/domains/components/resolver.py):
--   {
--     "<component_name>": {
--       "path_prefix": "src/foo/",
--       "extra_paths": ["lib/foo-helpers/"]
--     },
--     ...
--   }
-- Null means "no curated mapping yet" — the resolver falls back to the
-- top-folder heuristic (and to components_mapping_path when set).
--
-- A column on projects (not a side table) is enough for v1: no history,
-- one mapping per project, atomic with the rest of the project row.

alter table public.projects
  add column component_mapping jsonb;
