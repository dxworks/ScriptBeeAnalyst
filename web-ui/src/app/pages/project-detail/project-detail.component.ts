import { Component, OnInit, computed, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { ProjectService } from '../../core/services/project.service';
import { Project } from '../../core/models/project.model';

type Section = 'description' | 'files' | 'chat';

@Component({
  selector: 'app-project-detail',
  standalone: true,
  imports: [],
  templateUrl: './project-detail.component.html',
  styleUrl: './project-detail.component.scss',
})
export class ProjectDetailComponent implements OnInit {
  selectedProjectId = signal<string | null>(null);
  selectedSection = signal<Section>('description');

  // Sorted projects alphabetically
  sortedProjects = computed(() => {
    return [...this.projectService.projects()].sort((a, b) =>
      a.name.localeCompare(b.name)
    );
  });

  // Currently selected project
  selectedProject = computed(() => {
    const id = this.selectedProjectId();
    if (!id) return null;
    return this.projectService.projects().find(p => p.id === id) || null;
  });

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    public projectService: ProjectService
  ) {}

  ngOnInit(): void {
    // Load projects if not already loaded
    if (this.projectService.projects().length === 0) {
      this.projectService.loadProjects();
    }

    // Get project ID from route
    this.route.paramMap.subscribe(params => {
      const id = params.get('id');
      if (id) {
        this.selectedProjectId.set(id);
      }
    });

    // Check for section query param
    this.route.queryParamMap.subscribe(params => {
      const section = params.get('section') as Section;
      if (section && ['description', 'files', 'chat'].includes(section)) {
        this.selectedSection.set(section);
      }
    });
  }

  selectProject(project: Project): void {
    this.selectedProjectId.set(project.id);
    this.router.navigate(['/projects', project.id], { replaceUrl: true });
  }

  selectSection(section: Section): void {
    this.selectedSection.set(section);
  }

  isProjectSelected(project: Project): boolean {
    return this.selectedProjectId() === project.id;
  }

  isSectionSelected(section: Section): boolean {
    return this.selectedSection() === section;
  }

  formatDate(dateString: string): string {
    return new Date(dateString).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  }

  onFileDrop(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    // TODO: Handle file drop
    console.log('Files dropped:', event.dataTransfer?.files);
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
  }

  onFileSelect(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files) {
      // TODO: Handle file selection
      console.log('Files selected:', input.files);
    }
  }
}
