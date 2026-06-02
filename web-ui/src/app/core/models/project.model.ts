export interface Project {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  status: ProjectStatus;
  /**
   * UnifiedUsers redesign lifecycle stage, mirrored from the data-server's
   * `MergeState` (data-server/src/common/kernel/merge_state.py). `PRE_MERGE`
   * = setup stage (author matches / enrichment config still editable);
   * `FINALIZED` = query stage (refs rewritten to UnifiedUsers, setup frozen).
   * Optional because older project rows pre-date the column; treat a missing
   * value as `PRE_MERGE`.
   */
  merge_state?: MergeState;
  /**
   * Live pipeline progress (0..100), present only while a build/finalize is
   * running on the data-server (in-memory registry, `src/progress.py`),
   * surfaced through `GET /projects`. Absent when no pipeline is active —
   * drives the dashboard card's top-edge loading bar. See `progress_stage`
   * for the current checkpoint label.
   */
  progress?: number;
  /** Human-readable checkpoint label for the current `progress` value. */
  progress_stage?: string;
  // Files are fetched separately via SerializedFile[]
}

export type ProjectStatus = 'draft' | 'processing' | 'ready' | 'idle' | 'resuming' | 'error';

/**
 * Project lifecycle stage. The string values match the data-server's
 * `StrEnum` exactly — both `/projects/current` and `POST .../finalize` emit
 * these literals, and the Supabase `projects.merge_state` column stores them.
 */
export type MergeState = 'PRE_MERGE' | 'FINALIZED';

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
// constraint set by migrations 20260503000004_insider_quality_issues.sql and
// 20260522000001_appinspector_tags.sql.
export type FileType =
  | 'git'
  | 'github'
  | 'jira'
  | 'lizard'
  | 'codeframe'
  | 'dude_external'
  | 'dude_internal'
  | 'quality_issues'
  | 'app_inspector';

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
//   *-codeframe.jsonl                    → codeframe        (repo = stem before "-codeframe")
//   *-code_smells.json                   → quality_issues   (repo = stem before "-code_smells")
//   *-chronos-tags.json                  → app_inspector    (repo = stem before "-chronos-tags")
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
  { suffix: '-codeframe.jsonl',            fileType: 'codeframe',      repoFromStem: true },
  { suffix: '-code_smells.json',           fileType: 'quality_issues', repoFromStem: true },
  { suffix: '-chronos-tags.json',          fileType: 'app_inspector',  repoFromStem: true },
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
 *   "zeppelin-codeframe.jsonl"       → "zeppelin"
 *   "zeppelin-code_smells.json"      → "zeppelin"
 *   "zeppelin-chronos-tags.json"     → "zeppelin"
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
