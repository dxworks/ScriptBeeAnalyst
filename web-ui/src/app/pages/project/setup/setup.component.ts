import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

interface SetupTab {
  id: string;
  label: string;
  path: string;
}

@Component({
  selector: 'app-project-setup',
  standalone: true,
  imports: [RouterLink, RouterLinkActive, RouterOutlet],
  templateUrl: './setup.component.html',
  styleUrl: './setup.component.scss',
})
export class SetupComponent {
  // Tab list. Add new entries here as Setup grows; each one needs a matching
  // child route under `/project/:id/setup/` in app.routes.ts.
  readonly tabs: SetupTab[] = [
    { id: 'author-matching', label: 'Author Matching', path: 'author-matching' },
    { id: 'exclusion-rules', label: 'Exclusion Rules', path: 'exclusion-rules' },
    { id: 'enrichment-config', label: 'Enrichment Config', path: 'enrichment-config' },
  ];
}
