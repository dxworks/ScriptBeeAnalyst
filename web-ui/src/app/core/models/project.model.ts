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
// 'git' / 'github' / 'jira' are the original sources. The remaining values
// match the data-server's processor.py file_type dispatch and the DB CHECK
// constraint set by migration 20260503000004_insider_quality_issues.sql.
// 'codeframe' is NOT exposed here on purpose — its parser is stub-only.
export type FileType =
  | 'git'
  | 'github'
  | 'jira'
  | 'lizard'
  | 'jafax'
  | 'dude_external'
  | 'dude_internal'
  | 'quality_issues';

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

// Filename → file_type detection (case-insensitive on the part being matched).
// Tested in order; the first matching pattern wins. Patterns:
//   *.iglog                              → git              (repo = stem)
//   github.json                          → github
//   jira.json                            → jira
//   *-layout.json                        → jafax            (repo = stem before "-layout")
//   *-code_smells.json                   → quality_issues   (repo = stem before "-code_smells")
//   *-external_duplication.csv           → dude_external    (repo = stem before "-external_duplication")
//   *-internal_duplication.json          → dude_internal    (repo = stem before "-internal_duplication")
//   *-lizard.csv                         → lizard           (repo = stem before "-lizard")
// Lizard is the only source whose upstream tool emits just "<repo>.csv";
// because plain ".csv" can't be disambiguated from arbitrary CSV uploads, we
// require the user to rename it to "<repo>-lizard.csv" before upload.
interface SuffixRule {
  suffix: string;
  fileType: FileType;
  repoFromStem: boolean;  // true = repo_name = filename stem before this suffix
}

const SUFFIX_RULES: SuffixRule[] = [
  { suffix: '-layout.json',                fileType: 'jafax',          repoFromStem: true },
  { suffix: '-code_smells.json',           fileType: 'quality_issues', repoFromStem: true },
  { suffix: '-external_duplication.csv',   fileType: 'dude_external',  repoFromStem: true },
  { suffix: '-internal_duplication.json',  fileType: 'dude_internal',  repoFromStem: true },
  { suffix: '-lizard.csv',                 fileType: 'lizard',         repoFromStem: true },
];

const EXACT_NAME_MAP: Record<string, FileType> = {
  'github.json': 'github',
  'jira.json': 'jira',
};

export function getFileTypeFromName(filename: string): FileType | null {
  const lowerName = filename.toLowerCase();
  if (lowerName.endsWith('.iglog')) return 'git';
  if (EXACT_NAME_MAP[lowerName]) return EXACT_NAME_MAP[lowerName];
  for (const rule of SUFFIX_RULES) {
    if (lowerName.endsWith(rule.suffix)) return rule.fileType;
  }
  return null;
}

export function isValidSerializedFileName(filename: string): boolean {
  return getFileTypeFromName(filename) !== null;
}

/**
 * Extracts repo name from a filename:
 *   "backend.iglog"                  → "backend"
 *   "zeppelin-layout.json"           → "zeppelin"
 *   "zeppelin-code_smells.json"      → "zeppelin"
 *   "zeppelin-external_duplication.csv" → "zeppelin"
 *   "zeppelin-internal_duplication.json" → "zeppelin"
 *   "zeppelin-lizard.csv"            → "zeppelin"
 *   "github.json" / "jira.json"      → null  (single-source files have no repo)
 */
export function getRepoNameFromFile(filename: string): string | null {
  const lowerName = filename.toLowerCase();
  if (lowerName.endsWith('.iglog')) {
    const dotIndex = filename.lastIndexOf('.');
    return dotIndex > 0 ? filename.substring(0, dotIndex) : null;
  }
  for (const rule of SUFFIX_RULES) {
    if (rule.repoFromStem && lowerName.endsWith(rule.suffix)) {
      const stemEnd = filename.length - rule.suffix.length;
      return stemEnd > 0 ? filename.substring(0, stemEnd) : null;
    }
  }
  return null;
}
