import { Component, OnDestroy, OnInit, signal } from '@angular/core';
import { ConfirmationModalComponent } from '../../../../shared/components/confirmation-modal/confirmation-modal.component';
import { CurrentProjectService } from '../../../../core/services/current-project.service';
import { DataServerService, FilterRuleDto } from '../../../../core/services/data-server.service';
import { ToastService } from '../../../../core/services/toast.service';

/**
 * Interval (ms) at which the rules list is re-fetched from the data-server.
 * Replaces the old Supabase realtime channel `project_filter_rules:{id}`;
 * a tighter poll than the projects list because rules added by the agent
 * want near-live feedback.
 */
const POLL_INTERVAL_MS = 3000;

@Component({
  selector: 'app-exclusion-rules',
  standalone: true,
  imports: [ConfirmationModalComponent],
  templateUrl: './exclusion-rules.component.html',
  styleUrl: './exclusion-rules.component.scss',
})
export class ExclusionRulesComponent implements OnInit, OnDestroy {
  rules = signal<FilterRuleDto[]>([]);
  loading = signal(false);

  showDeleteModal = signal(false);
  ruleToDelete = signal<FilterRuleDto | null>(null);
  deleting = signal(false);

  private pollTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private dataServer: DataServerService,
    private currentProject: CurrentProjectService,
    private toast: ToastService,
  ) {}

  ngOnInit(): void {
    const projectId = this.currentProject.loadedProjectId();
    if (!projectId) return;
    void this.loadRules(projectId, true);
    this.startPolling(projectId);
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  /**
   * Fetch the rules list (with match counts) from the single data-server
   * endpoint, which already orders by created_at DESC and folds in the
   * per-rule match_count. Replaces the old two-source load (Supabase rows +
   * separate count call).
   *
   * @param showSpinner only the initial load flips the loading flag; the
   *   poll refreshes silently so the list doesn't flicker.
   */
  private async loadRules(projectId: string, showSpinner = false): Promise<void> {
    if (showSpinner) this.loading.set(true);
    try {
      const rules = await this.dataServer.listFilterRules(projectId);
      this.rules.set(rules);
    } catch {
      if (showSpinner) {
        this.toast.error('Failed to load exclusion rules');
      }
      // Poll failures are silent; the next tick retries.
    } finally {
      if (showSpinner) this.loading.set(false);
    }
  }

  private startPolling(projectId: string): void {
    if (this.pollTimer) return;
    this.pollTimer = setInterval(() => {
      void this.loadRules(projectId);
    }, POLL_INTERVAL_MS);
  }

  private stopPolling(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  requestDelete(rule: FilterRuleDto): void {
    this.ruleToDelete.set(rule);
    this.showDeleteModal.set(true);
  }

  cancelDelete(): void {
    this.showDeleteModal.set(false);
    this.ruleToDelete.set(null);
  }

  async confirmDelete(): Promise<void> {
    const rule = this.ruleToDelete();
    const projectId = this.currentProject.loadedProjectId();
    if (!rule || !projectId || this.deleting()) return;

    this.deleting.set(true);
    const ok = await this.dataServer.deleteFilterRule(projectId, rule.id);
    this.deleting.set(false);
    this.showDeleteModal.set(false);
    this.ruleToDelete.set(null);

    if (!ok) {
      this.toast.error(`Failed to delete rule "${rule.name}"`);
      return;
    }

    // Optimistic removal; the realtime DELETE event will reconcile if needed.
    this.rules.update(rs => rs.filter(r => r.id !== rule.id));
    this.toast.success(`Removed rule "${rule.name}"`);
  }

  dslSummary(rule: FilterRuleDto): string {
    return formatDsl(rule.dsl, rule.entity_kind);
  }

  entityKindLabel(kind: string): string {
    return kind.toLowerCase().split('_').map(capitalize).join(' ');
  }

  matchCountLabel(rule: FilterRuleDto): string {
    const n = rule.match_count;
    if (n === null || n === undefined) return 'matches: —';
    const noun = this.entityNoun(rule.entity_kind, n);
    return `excludes ${n.toLocaleString()} ${noun}`;
  }

  private entityNoun(kind: string, count: number): string {
    const lower = kind.toLowerCase();
    const singular: Record<string, string> = {
      file: 'file',
      commit: 'commit',
      issue: 'issue',
      pull_request: 'pull request',
    };
    const base = singular[lower] ?? lower.replace(/_/g, ' ');
    return count === 1 ? base : `${base}s`;
  }
}

function capitalize(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

function formatDsl(dsl: FilterRuleDto['dsl'], entityKind: string): string {
  const kind = capitalize(entityKind.toLowerCase());
  const predicate = dsl?.predicate;
  if (!predicate) return kind;

  if ('all_of' in predicate) {
    const parts = predicate.all_of.map(p => formatLeaf(kind, p));
    return parts.join(' AND ');
  }

  return formatLeaf(kind, predicate);
}

function formatLeaf(
  kind: string,
  leaf: { field: string; op: string; value: unknown },
): string {
  const op = OP_LABELS[leaf.op] ?? leaf.op;
  const value = formatValue(leaf.value);
  return `${kind}.${leaf.field} ${op} ${value}`;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return 'null';
  if (Array.isArray(value)) return `[${value.map(formatValue).join(', ')}]`;
  if (typeof value === 'string') return JSON.stringify(value);
  return String(value);
}

const OP_LABELS: Record<string, string> = {
  lt: '<',
  le: '<=',
  gt: '>',
  ge: '>=',
  eq: '=',
  ne: '!=',
  in: 'in',
  not_in: 'not in',
  contains: 'contains',
  regex: 'matches',
};
