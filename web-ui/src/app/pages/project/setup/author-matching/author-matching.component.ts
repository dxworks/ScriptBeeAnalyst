import { Component, OnInit, signal } from '@angular/core';
import { DataServerService } from '../../../../core/services/data-server.service';
import { ToastService } from '../../../../core/services/toast.service';
import { CurrentProjectService } from '../../../../core/services/current-project.service';
import {
  SuggestionDto,
  UnifiedUserDto,
  OVERSIZE_CLUSTER_WARNING_THRESHOLD,
  getIdentityKey,
  getSourceLabel,
  getConfidenceLevel,
  getConfidenceLabel,
} from '../../../../core/models/author-merge.model';
import { ConfirmationModalComponent } from '../../../../shared/components/confirmation-modal/confirmation-modal.component';

@Component({
  selector: 'app-author-matching',
  standalone: true,
  imports: [ConfirmationModalComponent],
  templateUrl: './author-matching.component.html',
  styleUrl: './author-matching.component.scss',
})
export class AuthorMatchingComponent implements OnInit {
  suggestions = signal<SuggestionDto[]>([]);
  suggestionsLoading = signal(false);
  suggestionsTotal = signal(0);
  totalIdentities = signal(0);
  unifiedUsers = signal<UnifiedUserDto[]>([]);
  setupInitialized = signal(false);
  processingSuggestionId = signal<string | null>(null);
  suggestionEdits = signal<Record<string, { name: string; email: string }>>({});

  showDeleteUnifiedUserModal = signal(false);
  unifiedUserToDelete = signal<UnifiedUserDto | null>(null);

  suggestionUnchecked = signal<Record<string, Set<string>>>({});
  suggestionExpanded = signal<Record<string, boolean>>({});
  suggestionLoadingMore = signal<Record<string, boolean>>({});

  applyingAll = signal(false);
  showApplyAllModal = signal(false);

  savingGraphState = signal(false);

  deletingAllLinks = signal(false);
  showDeleteAllLinksModal = signal(false);

  readonly oversizeThreshold = OVERSIZE_CLUSTER_WARNING_THRESHOLD;

  // Once the project is FINALIZED the author refs have been rewritten to
  // UnifiedUsers and the matching endpoints 409. Mirror that in the UI by
  // turning this tab read-only. Getter (not a field initializer) so it doesn't
  // read the injected service before the constructor assigns it.
  get isFinalized() {
    return this.currentProject.isFinalized;
  }

  constructor(
    private dataServerService: DataServerService,
    private toastService: ToastService,
    private currentProject: CurrentProjectService,
  ) {}

  /**
   * Guard for every author-matching write. Returns true (and surfaces a
   * toast) when the project is finalized, so callers can early-return. The
   * data-server already 409s these endpoints — this stops the round-trip
   * and gives immediate feedback.
   */
  private blockedByFinalize(): boolean {
    if (this.isFinalized()) {
      this.toastService.info(
        'Author matching is finalized and read-only. Re-import the project to change merges.',
      );
      return true;
    }
    return false;
  }

  ngOnInit(): void {
    // Auto-load suggestions if a project is loaded.
    if (this.currentProject.loadedProjectId() && !this.setupInitialized()) {
      this.loadSuggestions();
    }
  }

  private getProjectId(): string | null {
    return this.currentProject.loadedProjectId();
  }

  async loadSuggestions(): Promise<void> {
    const projectId = this.getProjectId();
    if (!projectId) return;

    this.suggestionsLoading.set(true);
    this.setupInitialized.set(true);

    const [suggestionsResponse, usersResponse] = await Promise.all([
      this.dataServerService.getSuggestions(projectId),
      this.dataServerService.getUnifiedUsers(projectId),
    ]);

    if (suggestionsResponse) {
      this.suggestions.set(suggestionsResponse.suggestions);
      this.suggestionsTotal.set(suggestionsResponse.suggestions.length);
      this.totalIdentities.set(suggestionsResponse.total_identities);

      const edits: Record<string, { name: string; email: string }> = {};
      for (const s of suggestionsResponse.suggestions) {
        edits[s.suggestion_id] = { name: s.default_name, email: s.default_email };
      }
      this.suggestionEdits.set(edits);
      this.suggestionUnchecked.set({});
    } else {
      this.toastService.error('Failed to load author suggestions');
    }

    if (usersResponse) {
      this.unifiedUsers.set(usersResponse.users);
    }

    this.suggestionsLoading.set(false);
  }

  async onApplySuggestion(suggestion: SuggestionDto): Promise<void> {
    if (this.blockedByFinalize()) return;
    const projectId = this.getProjectId();
    if (!projectId || this.processingSuggestionId()) return;

    this.processingSuggestionId.set(suggestion.suggestion_id);

    const edits = this.suggestionEdits()[suggestion.suggestion_id];
    const unchecked = this.suggestionUnchecked()[suggestion.suggestion_id] || new Set<string>();

    const selectedKeys = suggestion.identities
      .map(i => getIdentityKey(i))
      .filter(k => !unchecked.has(k));

    const unselectedKeys = suggestion.identities
      .map(i => getIdentityKey(i))
      .filter(k => unchecked.has(k));

    if (selectedKeys.length < 2) {
      this.toastService.warning('Select at least 2 identities to merge');
      this.processingSuggestionId.set(null);
      return;
    }

    const result = await this.dataServerService.applySuggestion(projectId, {
      suggestion_id: suggestion.suggestion_id,
      selected_identity_keys: selectedKeys,
      unselected_identity_keys: unselectedKeys,
      name: edits?.name || suggestion.default_name,
      email: edits?.email || suggestion.default_email,
    });

    this.processingSuggestionId.set(null);

    if (result) {
      this.toastService.success(`Merged as "${result.display_name}"`);
      this.suggestions.update(list => list.filter(s => s.suggestion_id !== suggestion.suggestion_id));
      this.unifiedUsers.update(users => [...users, result]);
    } else {
      this.toastService.error('Failed to apply suggestion');
    }
  }

  async onRejectSuggestion(suggestion: SuggestionDto): Promise<void> {
    if (this.blockedByFinalize()) return;
    const projectId = this.getProjectId();
    if (!projectId || this.processingSuggestionId()) return;

    this.processingSuggestionId.set(suggestion.suggestion_id);

    const identityKeys = suggestion.identities.map(i => getIdentityKey(i));
    const success = await this.dataServerService.rejectSuggestion(projectId, identityKeys);

    this.processingSuggestionId.set(null);

    if (success) {
      this.toastService.info('Suggestion rejected');
      this.suggestions.update(list => list.filter(s => s.suggestion_id !== suggestion.suggestion_id));
    } else {
      this.toastService.error('Failed to reject suggestion');
    }
  }

  onSuggestionNameChange(suggestionId: string, name: string): void {
    this.suggestionEdits.update(edits => ({
      ...edits,
      [suggestionId]: { ...edits[suggestionId], name },
    }));
  }

  onSuggestionEmailChange(suggestionId: string, email: string): void {
    this.suggestionEdits.update(edits => ({
      ...edits,
      [suggestionId]: { ...edits[suggestionId], email },
    }));
  }

  onToggleIdentity(suggestionId: string, identityKey: string): void {
    this.suggestionUnchecked.update(map => {
      const current = new Set(map[suggestionId] || []);
      if (current.has(identityKey)) {
        current.delete(identityKey);
      } else {
        current.add(identityKey);
      }
      return { ...map, [suggestionId]: current };
    });
  }

  isIdentityChecked(suggestionId: string, identityKey: string): boolean {
    const unchecked = this.suggestionUnchecked()[suggestionId];
    return !unchecked || !unchecked.has(identityKey);
  }

  isSuggestionOversize(suggestion: SuggestionDto): boolean {
    return suggestion.total_identities > this.oversizeThreshold;
  }

  isSuggestionCollapsed(suggestion: SuggestionDto): boolean {
    if (!this.isSuggestionOversize(suggestion)) return false;
    return !this.suggestionExpanded()[suggestion.suggestion_id];
  }

  toggleSuggestionExpanded(suggestionId: string): void {
    this.suggestionExpanded.update(map => ({
      ...map,
      [suggestionId]: !map[suggestionId],
    }));
  }

  hasMoreIdentities(suggestion: SuggestionDto): boolean {
    return suggestion.identities.length < suggestion.total_identities;
  }

  isLoadingMore(suggestionId: string): boolean {
    return !!this.suggestionLoadingMore()[suggestionId];
  }

  async onLoadMoreIdentities(suggestion: SuggestionDto, pageSize = 50): Promise<void> {
    const projectId = this.getProjectId();
    if (!projectId) return;
    if (this.isLoadingMore(suggestion.suggestion_id)) return;
    if (!this.hasMoreIdentities(suggestion)) return;

    this.suggestionLoadingMore.update(map => ({
      ...map,
      [suggestion.suggestion_id]: true,
    }));

    const page = await this.dataServerService.getSuggestionIdentitiesPage(
      projectId,
      suggestion.suggestion_id,
      suggestion.identities.length,
      pageSize,
    );

    this.suggestionLoadingMore.update(map => ({
      ...map,
      [suggestion.suggestion_id]: false,
    }));

    if (!page) {
      this.toastService.error('Failed to load more identities');
      return;
    }

    const existingKeys = new Set(suggestion.identities.map(i => getIdentityKey(i)));
    const newOnes = page.identities.filter(i => !existingKeys.has(getIdentityKey(i)));
    if (newOnes.length === 0) return;

    this.suggestions.update(list =>
      list.map(s => {
        if (s.suggestion_id !== suggestion.suggestion_id) return s;
        return { ...s, identities: [...s.identities, ...newOnes] };
      }),
    );
  }

  confirmDeleteUnifiedUser(user: UnifiedUserDto): void {
    if (this.blockedByFinalize()) return;
    this.unifiedUserToDelete.set(user);
    this.showDeleteUnifiedUserModal.set(true);
  }

  async deleteUnifiedUser(): Promise<void> {
    const user = this.unifiedUserToDelete();
    const projectId = this.getProjectId();
    if (!user || !projectId) return;

    this.showDeleteUnifiedUserModal.set(false);

    const success = await this.dataServerService.deleteUnifiedUser(projectId, user.id);
    if (success) {
      this.toastService.success(`Removed unified user "${user.display_name}"`);
      this.unifiedUsers.update(users => users.filter(u => u.id !== user.id));
    } else {
      this.toastService.error('Failed to delete unified user');
    }

    this.unifiedUserToDelete.set(null);
  }

  cancelDeleteUnifiedUser(): void {
    this.showDeleteUnifiedUserModal.set(false);
    this.unifiedUserToDelete.set(null);
  }

  requestApplyAllSuggestions(): void {
    if (this.blockedByFinalize()) return;
    if (this.applyingAll()) return;
    if (this.suggestions().length === 0) return;
    this.showApplyAllModal.set(true);
  }

  cancelApplyAllSuggestions(): void {
    this.showApplyAllModal.set(false);
  }

  async applyAllSuggestions(): Promise<void> {
    const projectId = this.getProjectId();
    if (!projectId || this.applyingAll()) return;

    this.showApplyAllModal.set(false);
    this.applyingAll.set(true);

    const result = await this.dataServerService.applyAllSuggestions(projectId);

    this.applyingAll.set(false);

    if (!result) {
      this.toastService.error('Failed to apply all suggestions');
      return;
    }

    this.suggestions.set([]);
    this.suggestionEdits.set({});
    this.suggestionUnchecked.set({});
    this.suggestionExpanded.set({});
    if (result.users.length > 0) {
      this.unifiedUsers.update(users => [...users, ...result.users]);
    }

    const failedCount = result.failed.length;
    if (failedCount === 0) {
      this.toastService.success(`Created ${result.created} unified users`);
    } else {
      this.toastService.warning(`Created ${result.created} unified users (${failedCount} failed)`);
    }
  }

  confirmDeleteAllLinks(): void {
    if (this.blockedByFinalize()) return;
    if (this.deletingAllLinks()) return;
    this.showDeleteAllLinksModal.set(true);
  }

  cancelDeleteAllLinks(): void {
    this.showDeleteAllLinksModal.set(false);
  }

  async deleteAllLinks(): Promise<void> {
    const projectId = this.getProjectId();
    if (!projectId || this.deletingAllLinks()) return;

    this.showDeleteAllLinksModal.set(false);
    this.deletingAllLinks.set(true);
    const result = await this.dataServerService.deleteAllUnifiedUsers(projectId);
    this.deletingAllLinks.set(false);

    if (!result) {
      this.toastService.error('Failed to reset author matching');
      return;
    }

    this.unifiedUsers.set([]);
    this.suggestions.set([]);
    this.suggestionsTotal.set(0);
    this.suggestionEdits.set({});
    this.suggestionUnchecked.set({});
    this.suggestionExpanded.set({});
    this.suggestionLoadingMore.set({});
    this.setupInitialized.set(false);

    this.toastService.success(
      `Author matching reset (${result.deleted_users} user(s), ${result.deleted_rejected} rejected pair(s) cleared)`,
    );
  }

  async onSaveGraphState(): Promise<void> {
    const projectId = this.getProjectId();
    if (!projectId || this.savingGraphState()) return;

    this.savingGraphState.set(true);
    const result = await this.dataServerService.saveGraphState(projectId);
    this.savingGraphState.set(false);

    if (!result) {
      this.toastService.error('Failed to save graph state');
      return;
    }

    this.toastService.success(
      `Graph state saved (${result.size_mb.toFixed(2)} MB, ${result.user_count} unified users)`,
    );
  }

  // Helpers exposed to template
  getIdentityKey = getIdentityKey;
  getSourceLabel = getSourceLabel;
  getConfidenceLevel = getConfidenceLevel;
  getConfidenceLabel = getConfidenceLabel;
}
