import { Component, OnDestroy, OnInit, signal } from '@angular/core';
import { RealtimeChannel } from '@supabase/supabase-js';
import { ConfirmationModalComponent } from '../../../../shared/components/confirmation-modal/confirmation-modal.component';
import { CurrentProjectService } from '../../../../core/services/current-project.service';
import { DataServerService, FilterRuleDto } from '../../../../core/services/data-server.service';
import { SupabaseService } from '../../../../core/services/supabase.service';
import { ToastService } from '../../../../core/services/toast.service';

const TABLE = 'project_filter_rules';

interface FilterRuleRow {
  id: string;
  project_id: string;
  user_id: string | null;
  entity_kind: string;
  name: string;
  nl_description: string;
  dsl: FilterRuleDto['dsl'];
  created_at: string | null;
}

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

  private channel: RealtimeChannel | null = null;

  constructor(
    private dataServer: DataServerService,
    private supabase: SupabaseService,
    private currentProject: CurrentProjectService,
    private toast: ToastService,
  ) {}

  ngOnInit(): void {
    const projectId = this.currentProject.loadedProjectId();
    if (!projectId) return;
    void this.loadRules(projectId);
    this.subscribeRealtime(projectId);
  }

  ngOnDestroy(): void {
    this.unsubscribeRealtime();
  }

  private async loadRules(projectId: string): Promise<void> {
    this.loading.set(true);
    // Read directly from Supabase: RLS scopes rows to the current user, and
    // realtime hooks into the same source of truth — keeps cache-coherency
    // with the agent's writes without an extra round-trip through data-server.
    const { data, error } = await this.supabase.client
      .from(TABLE)
      .select('*')
      .eq('project_id', projectId)
      .order('created_at', { ascending: false });

    if (error) {
      this.toast.error(`Failed to load exclusion rules: ${error.message}`);
      this.loading.set(false);
      return;
    }

    this.rules.set((data ?? []).map(toDto));
    this.loading.set(false);
  }

  private subscribeRealtime(projectId: string): void {
    if (this.channel) return;

    this.channel = this.supabase.client
      .channel(`project_filter_rules:${projectId}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: TABLE,
          filter: `project_id=eq.${projectId}`,
        },
        payload => {
          const row = toDto(payload.new as FilterRuleRow);
          this.rules.update(rs => (rs.some(r => r.id === row.id) ? rs : [row, ...rs]));
        },
      )
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: TABLE,
          filter: `project_id=eq.${projectId}`,
        },
        payload => {
          const row = toDto(payload.new as FilterRuleRow);
          this.rules.update(rs => rs.map(r => (r.id === row.id ? row : r)));
        },
      )
      .on(
        'postgres_changes',
        {
          event: 'DELETE',
          schema: 'public',
          table: TABLE,
          filter: `project_id=eq.${projectId}`,
        },
        payload => {
          const oldId = (payload.old as { id?: string }).id;
          if (!oldId) return;
          this.rules.update(rs => rs.filter(r => r.id !== oldId));
        },
      )
      .subscribe();
  }

  private unsubscribeRealtime(): void {
    if (this.channel) {
      this.supabase.client.removeChannel(this.channel);
      this.channel = null;
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
}

function toDto(row: FilterRuleRow): FilterRuleDto {
  return {
    id: row.id,
    project_id: row.project_id,
    user_id: row.user_id,
    entity_kind: row.entity_kind,
    name: row.name,
    nl_description: row.nl_description,
    dsl: row.dsl,
    created_at: row.created_at,
  };
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
