import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    redirectTo: '/dashboard',
    pathMatch: 'full',
  },
  {
    path: '',
    loadComponent: () =>
      import('./layout/main-layout/main-layout.component').then(m => m.MainLayoutComponent),
    children: [
      {
        path: 'dashboard',
        loadComponent: () =>
          import('./pages/dashboard/dashboard.component').then(m => m.DashboardComponent),
      },
      {
        path: 'project',
        loadComponent: () =>
          import('./pages/project/project.component').then(m => m.ProjectComponent),
      },
      {
        path: 'project/:id',
        loadComponent: () =>
          import('./pages/project/project.component').then(m => m.ProjectComponent),
        children: [
          { path: '', redirectTo: 'setup', pathMatch: 'full' },
          {
            path: 'setup',
            loadComponent: () =>
              import('./pages/project/setup/setup.component').then(m => m.SetupComponent),
            children: [
              { path: '', redirectTo: 'author-matching', pathMatch: 'full' },
              {
                path: 'author-matching',
                loadComponent: () =>
                  import('./pages/project/setup/author-matching/author-matching.component').then(
                    m => m.AuthorMatchingComponent,
                  ),
              },
              {
                path: 'exclusion-rules',
                loadComponent: () =>
                  import('./pages/project/setup/exclusion-rules/exclusion-rules.component').then(
                    m => m.ExclusionRulesComponent,
                  ),
              },
              {
                path: 'enrichment-config',
                loadComponent: () =>
                  import(
                    './pages/project/setup/enrichment-config/enrichment-config.component'
                  ).then(m => m.EnrichmentConfigComponent),
              },
              {
                path: 'analysis',
                loadComponent: () =>
                  import('./pages/project/setup/analysis/analysis.component').then(
                    m => m.AnalysisComponent,
                  ),
              },
            ],
          },
          {
            path: 'components',
            loadComponent: () =>
              import('./pages/project/components/components-page.component').then(
                m => m.ComponentsPageComponent,
              ),
          },
        ],
      },
      {
        path: 'data-model',
        loadComponent: () =>
          import('./pages/data-model/data-model.component').then(m => m.DataModelComponent),
      },
    ],
  },
  {
    path: '**',
    redirectTo: '/dashboard',
  },
];
