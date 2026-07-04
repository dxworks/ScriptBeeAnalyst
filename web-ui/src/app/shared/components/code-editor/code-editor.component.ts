import {
  AfterViewInit,
  Component,
  ElementRef,
  OnDestroy,
  effect,
  inject,
  input,
  output,
  viewChild,
} from '@angular/core';
import { EditorState, Compartment } from '@codemirror/state';
import { EditorView, keymap } from '@codemirror/view';
import { basicSetup } from 'codemirror';
import { python } from '@codemirror/lang-python';
import { oneDark } from '@codemirror/theme-one-dark';
import { ThemeService } from '../../../core/services/theme.service';

/**
 * Thin standalone wrapper around a CodeMirror 6 editor configured for Python.
 *
 * Keeps the editor's text in sync with the parent via a `value` input (used as
 * the *initial* document) and a `valueChange` output emitted on every edit.
 * `disabled` makes the editor read-only while code is running, and Ctrl/Cmd+Enter
 * fires the `run` output so the user can execute without leaving the keyboard.
 * The CodeMirror theme tracks the app's light/dark toggle through `ThemeService`.
 */
@Component({
  selector: 'app-code-editor',
  standalone: true,
  imports: [],
  template: `<div class="cm-host" #host></div>`,
  styles: [
    `
      :host {
        display: block;
        min-height: 0;
      }
      .cm-host {
        height: 100%;
      }
      .cm-host ::ng-deep .cm-editor {
        height: 100%;
        border-radius: var(--radius-md, 8px);
        border: 1px solid var(--color-border, #e5e7eb);
        font-size: 13px;
      }
      .cm-host ::ng-deep .cm-editor.cm-focused {
        outline: none;
        border-color: var(--color-accent, #6366f1);
      }
      .cm-host ::ng-deep .cm-scroller {
        font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
      }
    `,
  ],
})
export class CodeEditorComponent implements AfterViewInit, OnDestroy {
  private readonly theme = inject(ThemeService);

  /** Initial document. Read once when the editor is created. */
  readonly value = input<string>('');
  /** Locks the editor (read-only) while code is executing. */
  readonly disabled = input<boolean>(false);

  /** Emitted on every document change. */
  readonly valueChange = output<string>();
  /** Emitted when the user presses Ctrl/Cmd+Enter. */
  readonly run = output<void>();

  private readonly host = viewChild.required<ElementRef<HTMLDivElement>>('host');

  private view?: EditorView;
  private readonly themeCompartment = new Compartment();
  private readonly editableCompartment = new Compartment();

  constructor() {
    // Keep the CodeMirror theme and read-only state in lockstep with the app
    // theme toggle / `disabled` input once the view exists.
    effect(() => {
      const dark = this.theme.isDark();
      this.view?.dispatch({
        effects: this.themeCompartment.reconfigure(dark ? oneDark : []),
      });
    });
    effect(() => {
      const disabled = this.disabled();
      this.view?.dispatch({
        effects: this.editableCompartment.reconfigure(EditorView.editable.of(!disabled)),
      });
    });
  }

  ngAfterViewInit(): void {
    const state = EditorState.create({
      doc: this.value(),
      extensions: [
        basicSetup,
        python(),
        keymap.of([
          {
            key: 'Mod-Enter',
            run: () => {
              this.run.emit();
              return true;
            },
          },
        ]),
        this.themeCompartment.of(this.theme.isDark() ? oneDark : []),
        this.editableCompartment.of(EditorView.editable.of(!this.disabled())),
        EditorView.updateListener.of(update => {
          if (update.docChanged) {
            this.valueChange.emit(update.state.doc.toString());
          }
        }),
      ],
    });

    this.view = new EditorView({
      state,
      parent: this.host().nativeElement,
    });
  }

  ngOnDestroy(): void {
    this.view?.destroy();
  }
}
