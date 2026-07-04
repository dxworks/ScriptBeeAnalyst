// Production build: the SPA is served by the FastAPI data-server itself
// (single `docker compose up`, one origin). All API calls use relative URLs,
// so dataServerUrl is the empty string -> `${baseUrl}/projects` === '/projects'.
export const environment = {
  production: true,
  dataServerUrl: '',
};
