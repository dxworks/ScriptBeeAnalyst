export interface Project {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  user_id: string;
  status: ProjectStatus;
  // Files are fetched separately via SerializedFile[]
}

export type ProjectStatus = 'draft' | 'processing' | 'ready' | 'idle' | 'resuming' | 'error';

export interface CreateProjectDto {
  name: string;
  description?: string;
}

export interface UpdateProjectDto {
  name?: string;
  description?: string;
}

// Serialized file types
export type FileType = 'git' | 'github' | 'jira';

export interface SerializedFile {
  id: string;
  name: string;
  file_type: FileType;
  repo_name: string | null;
  storage_path: string;
  size_bytes: number;
  project_id: string;
  created_at: string;
  updated_at: string;
}

// File type detection from filename (case-insensitive)
// Any *.iglog file maps to 'git'; github.json and jira.json are exact matches.
export const FILE_TYPE_MAP: Record<string, FileType> = {
  'github.json': 'github',
  'jira.json': 'jira',
};

export function getFileTypeFromName(filename: string): FileType | null {
  const lowerName = filename.toLowerCase();
  if (lowerName.endsWith('.iglog')) return 'git';
  return FILE_TYPE_MAP[lowerName] ?? null;
}

export function isValidSerializedFileName(filename: string): boolean {
  return getFileTypeFromName(filename) !== null;
}

/**
 * Extracts repo name from an iglog filename (stem).
 * e.g., "backend.iglog" -> "backend", "github.json" -> null
 */
export function getRepoNameFromFile(filename: string): string | null {
  const lowerName = filename.toLowerCase();
  if (!lowerName.endsWith('.iglog')) return null;
  const dotIndex = filename.lastIndexOf('.');
  return dotIndex > 0 ? filename.substring(0, dotIndex) : null;
}
