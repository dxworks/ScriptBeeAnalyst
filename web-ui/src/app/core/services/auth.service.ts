import { Injectable, signal, computed } from '@angular/core';
import { Router } from '@angular/router';
import { User, Session, AuthError } from '@supabase/supabase-js';
import { SupabaseService } from './supabase.service';

export interface AuthState {
  user: User | null;
  session: Session | null;
  loading: boolean;
}

export interface AuthResult {
  success: boolean;
  error?: string;
}

@Injectable({
  providedIn: 'root',
})
export class AuthService {
  private readonly userSignal = signal<User | null>(null);
  private readonly sessionSignal = signal<Session | null>(null);
  private readonly loadingSignal = signal<boolean>(true);

  readonly user = this.userSignal.asReadonly();
  readonly session = this.sessionSignal.asReadonly();
  readonly loading = this.loadingSignal.asReadonly();
  readonly isAuthenticated = computed(() => !!this.userSignal());

  constructor(
    private supabase: SupabaseService,
    private router: Router
  ) {
    this.initializeAuth();
  }

  private async initializeAuth(): Promise<void> {
    // Get initial session
    const { data: { session } } = await this.supabase.client.auth.getSession();
    this.sessionSignal.set(session);
    this.userSignal.set(session?.user ?? null);
    this.loadingSignal.set(false);

    // Listen for auth changes
    this.supabase.client.auth.onAuthStateChange((_event, session) => {
      this.sessionSignal.set(session);
      this.userSignal.set(session?.user ?? null);
    });
  }

  async signUp(email: string, password: string): Promise<AuthResult> {
    try {
      const { error } = await this.supabase.client.auth.signUp({
        email,
        password,
      });

      if (error) {
        return { success: false, error: error.message };
      }

      return { success: true };
    } catch (err) {
      return { success: false, error: 'An unexpected error occurred' };
    }
  }

  async signIn(email: string, password: string): Promise<AuthResult> {
    try {
      const { error } = await this.supabase.client.auth.signInWithPassword({
        email,
        password,
      });

      if (error) {
        return { success: false, error: error.message };
      }

      return { success: true };
    } catch (err) {
      return { success: false, error: 'An unexpected error occurred' };
    }
  }

  async signOut(): Promise<void> {
    await this.supabase.client.auth.signOut();
    this.router.navigate(['/auth/login']);
  }
}
