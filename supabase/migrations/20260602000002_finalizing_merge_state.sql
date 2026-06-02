-- Add the transient FINALIZING merge_state.
--
-- Finalize (PRE_MERGE -> FINALIZED) runs Phase B in the background for a couple
-- of minutes. Previously the projects row only flipped to FINALIZED at the very
-- end, so a browser refresh mid-finalize saw PRE_MERGE and re-opened the
-- editable setup (author matching / configs) instead of the Analysis loading
-- bar. The finalize endpoint now writes FINALIZING onto the row immediately
-- (data-server/src/server.py) and `/projects/current` surfaces it, so the
-- web-ui keeps the setup locked and the loading bar up across a refresh.
--
-- The graph's in-memory merge_state still only ever holds PRE_MERGE / FINALIZED
-- (see common/kernel/merge_state.py) — FINALIZING is a row-only state.
--
-- The original CHECK constraint is the table-anonymous `projects_merge_state_check`
-- created inline in 20241122000001_initial_schema.sql.
alter table public.projects
  drop constraint if exists projects_merge_state_check;

alter table public.projects
  add constraint projects_merge_state_check
  check (merge_state in ('PRE_MERGE', 'FINALIZING', 'FINALIZED'));
