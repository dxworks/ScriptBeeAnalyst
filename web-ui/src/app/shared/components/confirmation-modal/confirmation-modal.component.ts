import { Component, input, output } from '@angular/core';

export type ConfirmationModalType = 'warning' | 'danger' | 'info';

@Component({
  selector: 'app-confirmation-modal',
  standalone: true,
  imports: [],
  templateUrl: './confirmation-modal.component.html',
})
export class ConfirmationModalComponent {
  title = input.required<string>();
  message = input.required<string>();
  confirmText = input<string>('Confirm');
  cancelText = input<string>('Cancel');
  type = input<ConfirmationModalType>('warning');
  loading = input<boolean>(false);

  confirmed = output<void>();
  cancelled = output<void>();

  onConfirm(): void {
    this.confirmed.emit();
  }

  onCancel(): void {
    this.cancelled.emit();
  }

  onBackdropClick(event: MouseEvent): void {
    if (event.target === event.currentTarget) {
      this.onCancel();
    }
  }
}
