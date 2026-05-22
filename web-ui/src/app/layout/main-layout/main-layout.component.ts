import { Component, OnDestroy, OnInit, computed } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { AuthService } from '../../core/services/auth.service';
import { CurrentProjectService } from '../../core/services/current-project.service';
import { ThemeToggleComponent } from '../../shared/components/theme-toggle/theme-toggle.component';

@Component({
  selector: 'app-main-layout',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ThemeToggleComponent],
  templateUrl: './main-layout.component.html',
  styleUrl: './main-layout.component.scss',
})
export class MainLayoutComponent implements OnInit, OnDestroy {
  readonly loadedProjectId;
  readonly loadedProjectName;
  readonly indicatorLabel;

  constructor(
    private authService: AuthService,
    public currentProject: CurrentProjectService,
  ) {
    this.loadedProjectId = this.currentProject.loadedProjectId;
    this.loadedProjectName = this.currentProject.loadedProjectName;
    this.indicatorLabel = computed(() => this.loadedProjectName() ?? 'Project');
  }

  ngOnInit(): void {
    this.currentProject.startPolling();
  }

  ngOnDestroy(): void {
    this.currentProject.stopPolling();
  }

  onLogout(): void {
    this.authService.signOut();
  }
}
