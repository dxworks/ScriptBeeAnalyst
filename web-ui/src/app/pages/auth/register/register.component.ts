import { Component, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { AuthService } from '../../../core/services/auth.service';

@Component({
  selector: 'app-register',
  standalone: true,
  imports: [FormsModule, RouterLink],
  templateUrl: './register.component.html',
})
export class RegisterComponent {
  email = signal('');
  password = signal('');
  confirmPassword = signal('');
  error = signal<string | null>(null);
  success = signal(false);
  loading = signal(false);

  constructor(
    private authService: AuthService,
    private router: Router
  ) {}

  async onSubmit(): Promise<void> {
    if (!this.email() || !this.password() || !this.confirmPassword()) {
      this.error.set('Please fill in all fields');
      return;
    }

    if (this.password() !== this.confirmPassword()) {
      this.error.set('Passwords do not match');
      return;
    }

    if (this.password().length < 6) {
      this.error.set('Password must be at least 6 characters');
      return;
    }

    this.loading.set(true);
    this.error.set(null);

    const result = await this.authService.signUp(this.email(), this.password());

    this.loading.set(false);

    if (result.success) {
      this.success.set(true);
      // Auto-confirm is enabled in dev, so we can redirect to login
      setTimeout(() => {
        this.router.navigate(['/auth/login']);
      }, 2000);
    } else {
      this.error.set(result.error ?? 'Registration failed');
    }
  }
}
