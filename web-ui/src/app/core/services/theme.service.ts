import { Injectable, signal, computed, effect } from '@angular/core';

export type ThemeMode = 'light' | 'dark';

const STORAGE_KEY = 'sba-theme-preference';
const THEME_COLORS: Record<ThemeMode, string> = {
  light: '#f5f7fa',
  dark: '#111827',
};

@Injectable({
  providedIn: 'root',
})
export class ThemeService {
  private readonly themeModeSignal = signal<ThemeMode>(this.loadPreference());

  readonly themeMode = this.themeModeSignal.asReadonly();
  readonly isDark = computed(() => this.themeModeSignal() === 'dark');

  constructor() {
    effect(() => {
      const theme = this.themeModeSignal();
      document.documentElement.setAttribute('data-theme', theme);
      document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEME_COLORS[theme]);
    });
  }

  setTheme(mode: ThemeMode): void {
    this.themeModeSignal.set(mode);
    localStorage.setItem(STORAGE_KEY, mode);
  }

  toggleTheme(): void {
    this.setTheme(this.themeModeSignal() === 'light' ? 'dark' : 'light');
  }

  private loadPreference(): ThemeMode {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark') {
      return saved;
    }
    return 'light';
  }
}
