import { Component } from '@angular/core';

/**
 * Query-stage analysis surface. Placeholder for now — the real analysis UI
 * (agent queries against the finalized UnifiedUser graph) is a separate
 * piece of work. This tab is only reachable once the project is FINALIZED;
 * the setup tab bar locks it in the PRE_MERGE stage.
 */
@Component({
  selector: 'app-analysis',
  standalone: true,
  imports: [],
  templateUrl: './analysis.component.html',
  styleUrl: './analysis.component.scss',
})
export class AnalysisComponent {}
