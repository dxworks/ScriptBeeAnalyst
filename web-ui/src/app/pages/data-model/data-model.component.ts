import { Component, computed, signal } from '@angular/core';
import { ENTITY_DEFINITIONS, EntityDefinition, EntityField } from './data-model.data';

@Component({
  selector: 'app-data-model',
  standalone: true,
  templateUrl: './data-model.component.html',
  styleUrl: './data-model.component.scss',
})
export class DataModelComponent {
  private selectedEntities = signal<Set<string>>(new Set());

  selectedDefinitions = computed<EntityDefinition[]>(() => {
    const selected = this.selectedEntities();
    return Array.from(selected)
      .map(name => ENTITY_DEFINITIONS[name])
      .filter(Boolean);
  });

  toggleEntity(name: string): void {
    const current = new Set(this.selectedEntities());
    if (current.has(name)) {
      current.delete(name);
    } else {
      current.add(name);
    }
    this.selectedEntities.set(current);
  }

  isSelected(name: string): boolean {
    return this.selectedEntities().has(name);
  }

  closeEntity(name: string): void {
    const current = new Set(this.selectedEntities());
    current.delete(name);
    this.selectedEntities.set(current);
  }

  filterFields(fields: EntityField[], category: EntityField['category']): EntityField[] {
    return fields.filter(f => f.category === category);
  }
}
