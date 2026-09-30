export type SortKey = "score" | "date" | "company";

export interface Filters {
  q: string;
  company: string | null;
  city: string | null;
  status: string | null;
  remote: boolean | null;
  minScore: number | null;
  hasConnection: boolean | null;
  liked: boolean | null;
  hidden: boolean | null;
  sent: boolean | null;
  profile: string;
  sort: SortKey;
  page: number;
}

export interface JobRow {
  id: string;
  title: string;
  company: string;
  company_id: string;
  url: string | null;
  location: string | null;
  city: string | null;
  is_remote: boolean;
  status: string;
  department: string | null;
  employment_type: string | null;
  posted_at: string | null;
  first_seen: string | null;
  years_required: number | null;
  is_referral: boolean;
  referral_contact: string | null;
  connection_count: number;
  best_score: number | null;
  liked: boolean;
  hidden: boolean;
  sent: boolean;
  reached_out: boolean;
}

export interface JobPage {
  total: number;
  page: number;
  size: number;
  jobs: JobRow[];
}

export interface JobState {
  liked: boolean;
  hidden: boolean;
  sent: boolean;
  reached_out: boolean;
}

export interface ScoreDetail {
  score: number | null;
  coverage: number | null;
  confidence: string | null;
  matched: string[];
}

export interface JobDetail extends JobRow {
  description: string;
  career_url: string | null;
  industry: string | null;
  company_size: string | null;
  scores: Record<string, ScoreDetail>;
  state: JobState;
}

export interface Facets {
  companies: { id: string; name: string; n: number }[];
  cities: { city: string; n: number }[];
  statuses: Record<string, number>;
}
