export type SortKey = "score" | "date" | "company" | "title";
export type Scope = "all" | "title";

export interface Filters {
  q: string;
  scope: Scope;
  exclude: string;
  company: string | null;
  city: string | null;
  department: string | null;
  industry: string | null;
  language: string | null;
  status: string | null;
  remote: boolean | null;
  minScore: number | null;
  maxYears: number | null;
  postedAfter: string | null;
  hasConnection: boolean | null;
  hasDescription: boolean | null;
  referral: boolean | null;
  liked: boolean | null;
  hidden: boolean | null;
  sent: boolean | null;
  reachedOut: boolean | null;
  profile: string;
  sort: SortKey;
  /** UI only - the server has no opinion about grouping. */
  group: boolean;
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
  last_seen: string | null;
  years_required: number | null;
  is_referral: boolean;
  referral_contact: string | null;
  connection_count: number;
  industry: string | null;
  source_language: string | null;
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
  /** The pre-translation title, when the listing was not in English. */
  title_original: string | null;
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
  departments: { department: string; n: number }[];
  industries: { industry: string; n: number }[];
  languages: { language: string; n: number }[];
  years: { years: number; n: number }[];
}
