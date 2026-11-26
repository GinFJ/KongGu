import type { CourseRow } from "./types";

export type DateFlag = {
  member_key:string;
  source_hash:string;
  date:string;
  period:number;
  page:number;
  bbox?: CourseRow["bbox"];
  page_width?:number;
  page_height?:number;
};

export type DateReviewMember = {
  member_key: string;
  name: string;
  department?: string;
  role?: string;
  source_hashes: string[];
  confirmed: boolean;
  missing_dates: string[];
};

export type DateReview = {
  start_date: string;
  end_date: string;
  members: DateReviewMember[];
  calendar?: Array<{date:string; week:number; weekday:string}>;
  flags?: DateFlag[];
  removed_positions?: CourseRow[];
  log: Array<{
    operation_id: string;
    action: string;
    member_key: string;
    source_hash?: string;
    date?: string;
    period?: number;
    marked?: boolean;
    open?: boolean;
    created_at: string;
  }>;
};
