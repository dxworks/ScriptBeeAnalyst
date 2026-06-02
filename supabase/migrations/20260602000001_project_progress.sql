-- Live pipeline progress for the dashboard loading bar.
--
-- The graph build/finalize work runs in the `processor` container (and the
-- HTTP /build path in `app`), while the dashboard reads `GET /projects` from
-- the `app` container. Those are separate processes, so progress can't live in
-- process memory — it goes on the projects row instead, written at hardcoded
-- checkpoints by the worker (see data-server/src/progress.py) and read back by
-- the API serializer (projects/router.py::_project_to_dict).
--
--   progress        smallint 0..100, NULL = no pipeline running (bar hidden)
--   progress_stage  human-readable checkpoint label for `progress`
--
-- The row's `projects_updated_at` trigger bumps `updated_at` on every progress
-- write, which the web-ui's 4s poll already reconciles on — so the card's
-- top-edge bar advances without any extra transport.
alter table public.projects
  add column if not exists progress smallint,
  add column if not exists progress_stage text;
