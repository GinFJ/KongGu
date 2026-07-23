export type QualityState = "accepted" | "needs_review" | "blocked";

export type ResourceStatus = {
  ready: boolean;
  app_data_root: string;
  resources_root: string;
  ocr_models_root: string;
  missing: Array<{ target: string; kind: string; issue: string }>;
  invalid: Array<{ target: string; kind: string; issue: string }>;
  ocr: { ready: boolean; models: Array<{ name: string; path: string; exists: boolean; valid: boolean }> };
};

export type CalendarSettings = {
  semester_start_date: string;
  teaching_weeks: number;
};

export type ParseIssue = {
  issue_id: string;
  code: string;
  message: string;
  severity: "info" | "warning" | "error";
  field?: string;
  source_hash?: string;
  source_file?: string;
  block_id?: string;
  suggestion?: string;
  blocks_export: boolean;
  confirmed: boolean;
};

export type CourseRow = {
  block_id: string;
  member_key: string;
  name: string;
  department?: string;
  role?: string;
  week: number;
  weekday: string;
  period?: number;
  periods: number[];
  course?: string;
  source_file?: string;
  source_hash?: string;
  page?: number;
  bbox?: [number, number, number, number];
  page_width?: number;
  page_height?: number;
  confidence?: number;
};

export type ParseResult = {
  ok: boolean;
  result_ref: string;
  quality_state: QualityState;
  can_export: boolean;
  parser_signature: string;
  summary: Record<string, number>;
  detected_weeks: number[];
  detected_week_count: number;
  detected_max_week: number;
  availability_preview: Array<Record<string, unknown>>;
  members: Array<Record<string, unknown>>;
  details: Array<Record<string, unknown>>;
  courses: CourseRow[];
  issues: ParseIssue[];
  corrections: Array<Record<string, unknown>>;
  warnings: string[];
  errors: string[];
};

export type ReviewPayload = {
  ok: boolean;
  job_id: string;
  quality_state: QualityState;
  parser_signature: string;
  sources: Array<{
    file_name: string;
    kind: string;
    source_path: string;
    content_hash: string;
  }>;
  blocks: CourseRow[];
  issues: ParseIssue[];
  corrections: Array<Record<string, unknown>>;
};

export type ProgressEvent = {
  type: "event";
  event: string;
  job_id: string;
  source_id: string;
  stage: string;
  current: number;
  total: number;
  message: string;
};

export type JobResponse = {
  ok: boolean;
  job: {
    id: string;
    status: string;
    error: string;
    quality_state: QualityState;
    current: number;
    total: number;
    output?: ParseResult;
    files: Array<Record<string, unknown>>;
  };
};
