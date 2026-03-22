import { Component } from '@angular/core';
import { ThemeService } from '../../../core/services/theme.service';

@Component({
  selector: 'app-theme-toggle',
  standalone: true,
  template: `
    <button
      class="theme-toggle"
      (click)="themeService.toggleTheme()"
      [attr.title]="'Theme: ' + themeService.themeMode()"
    >
      @if (themeService.isDark()) {
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
        </svg>
      } @else {
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="5"/>
          <line x1="12" y1="1" x2="12" y2="3"/>
          <line x1="12" y1="21" x2="12" y2="23"/>
          <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
          <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
          <line x1="1" y1="12" x2="3" y2="12"/>
          <line x1="21" y1="12" x2="23" y2="12"/>
          <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
          <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
        </svg>
      }
    </button>
  `,
  styles: `
    .theme-toggle {
      display: flex;
      align-items: center;
      padding: var(--spacing-sm) var(--spacing-md);
      border-radius: var(--radius-md);
      background: transparent;
      border: none;
      color: white;
      cursor: pointer;
      opacity: 0.8;
      transition: background-color 150ms ease;
    }

    .theme-toggle:hover {
      background-color: rgba(255, 255, 255, 0.1);
      opacity: 1;
    }
  `,
})
export class ThemeToggleComponent {
  constructor(readonly themeService: ThemeService) {}
}
