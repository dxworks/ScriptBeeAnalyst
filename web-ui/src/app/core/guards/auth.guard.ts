import { inject } from '@angular/core';
import { Router, CanActivateFn } from '@angular/router';
import { AuthService } from '../services/auth.service';

export const authGuard: CanActivateFn = () => {
  const authService = inject(AuthService);
  const router = inject(Router);

  // Wait for auth to initialize if still loading
  if (authService.loading()) {
    // Return a promise that resolves when loading is complete
    return new Promise<boolean>((resolve) => {
      const checkAuth = setInterval(() => {
        if (!authService.loading()) {
          clearInterval(checkAuth);
          if (authService.isAuthenticated()) {
            resolve(true);
          } else {
            router.navigate(['/auth/login']);
            resolve(false);
          }
        }
      }, 50);
    });
  }

  if (authService.isAuthenticated()) {
    return true;
  }

  router.navigate(['/auth/login']);
  return false;
};

// Guard for auth pages (login/register) - redirect to dashboard if already logged in
export const guestGuard: CanActivateFn = () => {
  const authService = inject(AuthService);
  const router = inject(Router);

  if (authService.loading()) {
    return new Promise<boolean>((resolve) => {
      const checkAuth = setInterval(() => {
        if (!authService.loading()) {
          clearInterval(checkAuth);
          if (authService.isAuthenticated()) {
            router.navigate(['/dashboard']);
            resolve(false);
          } else {
            resolve(true);
          }
        }
      }, 50);
    });
  }

  if (authService.isAuthenticated()) {
    router.navigate(['/dashboard']);
    return false;
  }

  return true;
};
