"""Paths and tuned scoring config for Dan Hanegbi's job search.

The role/keyword/location config here mirrors linkedin-match's backend/core/keywords.py
(same person, same preferences) — copied rather than imported so this app stays a
self-contained, independently runnable project with no cross-repo coupling.
"""

from pathlib import Path

# --- Local cache/output ---
ROOT = Path(__file__).parent
TECHMAP_CACHE_DIR = ROOT / "cache" / "techmap"
COMPANY_JOBS_CACHE = ROOT / "cache" / "company_jobs.json"
LM_LIVE_SCRAPED_CACHE = ROOT / "cache" / "lm_live_scraped.json"
GENERIC_DESC_CACHE = ROOT / "cache" / "generic_descriptions.json"
COMPANY_CAREER_PAGES_CACHE = ROOT / "cache" / "company_career_pages.json"
REFERRAL_JOBS_PATH = Path(r"C:\Users\user\Downloads\jobs_by_company.json")
GENERIC_DESC_TTL_HOURS = 24 * 14
CONNECTIONS_CACHE = ROOT / "cache" / "connections_index.json"
JOBS_OUTPUT_JSON = ROOT / "data" / "jobs_v2.json"
OUTPUT_HTML = ROOT.parent / "jobfit.html"

# --- Personal inputs (uploaded through the control panel, gitignored) ---
CONNECTIONS_CSV = ROOT / "data" / "connections.csv"
CV_PROFILES_DIR = ROOT / "data" / "cvs"
CV_PROFILES_REGISTRY = ROOT / "data" / "profiles.json"
REFERRAL_UPLOADS_DIR = ROOT / "data" / "referrals"

COMPANY_JOBS_TTL_HOURS = 24
COMPANY_RECHECK_TTL_HOURS = 12
RUN_HISTORY_PATH = ROOT / "data" / "run_history.json"

# The curated {company: url|null} map (781 companies) that drives which
# companies scrape_stage() checks - distinct from COMPANY_CAREER_PAGES_CACHE
# above, which is the older pipeline.py's tiered scrape-result cache.
COMPANIES_CAREER_PAGES_PATH = ROOT / "companies_career_pages.json"
# Triage decisions for companies with no known career URL: "techmap" (use
# techmap's own title/location/url row as a fallback source) or "skip"
# (reviewed, deliberately not pursuing this one). Absence = not yet reviewed.
COMPANY_REVIEW_PATH = ROOT / "data" / "company_review.json"

# --- Techmap source ---
TECHMAP_RAW_BASE = "https://raw.githubusercontent.com/mluggy/techmap/main/jobs/{category}.csv"
TECHMAP_CATEGORIES = [
    "software", "devops", "data-science", "security", "frontend", "hardware",
    "product", "qa", "support", "design", "project-management", "business",
    "finance", "marketing", "sales", "hr", "legal", "admin", "procurement-operations",
]

# --- Scoring config (tuned for Dan Hanegbi; shared by both CV profiles) ---
TARGET_ROLES: list[str] = [
    "backend engineer", "backend developer", "software engineer", "software developer",
    "platform engineer", "infrastructure engineer", "ml engineer", "machine learning engineer",
    "ai engineer", "ai infrastructure", "distributed systems engineer", "python engineer",
    "site reliability engineer", "devops engineer", "data engineer", "full stack engineer",
    "full-stack engineer", "fullstack engineer", "full stack developer",
    # Dan's actual specialization at Hailo: deploying/serving AI+LLM models,
    "mlops engineer", "ml infrastructure engineer", "ml platform engineer",
    "ai platform engineer", "llm infrastructure engineer", "inference engineer",
    "machine learning infrastructure engineer", "ml systems engineer",
]

# One shared role/title weighting for every CV profile. Profiles no longer get
# their own hand-tuned table - what differentiates them is the skill vocabulary
# extracted from each CV's own text (see cv.py), and coverage against a job's
# real description already dominates the score (see scoring.FULL_WEIGHTS).
ROLE_WEIGHTS: dict[str, int] = {
    "software engineer": 58, "python engineer": 55, "backend engineer": 54,
    "ai engineer": 53, "ml engineer": 52, "machine learning engineer": 52,
    "ai infrastructure": 52, "backend developer": 50, "software developer": 50,
    "platform engineer": 48, "infrastructure engineer": 48, "distributed systems engineer": 48,
    "site reliability engineer": 45, "full stack engineer": 42, "full-stack engineer": 42,
    "fullstack engineer": 42, "full stack developer": 40, "devops engineer": 40,
    "data engineer": 38, "mlops engineer": 56, "ml infrastructure engineer": 56,
    "ml platform engineer": 55, "ai platform engineer": 55, "llm infrastructure engineer": 57,
    "inference engineer": 55, "machine learning infrastructure engineer": 56, "ml systems engineer": 53,
}

TITLE_INCLUDE_KEYWORDS: list[str] = [
    "software", "engineer", "developer", "programmer", "architect", "sde", "swe",
    "backend", "back end", "back-end", "full stack", "fullstack", "full-stack",
    "infrastructure", "infra", "platform", "devops", "sre", "site reliability",
    "python", "ai", "ml", "machine learning", "deep learning", "llm", "genai",
    "gen ai", "mlops", "inference", "data engineer",
]

TITLE_EXCLUDE_KEYWORDS: list[str] = [
    "team lead", "tech lead", "team leader", "tech leader", "manager", "director",
    "sales", "presales", "pre-sales", "solution engineer", "solutions engineer",
    "account executive", "account manager", "marketing", "recruiter",
    "talent acquisition", "human resources", "finance", "accountant", "legal",
    "customer success", "customer support", "technical support", "mechanical",
    "electrical", "electronics", "hardware", "analog", "rf engineer", "vlsi",
    "asic", "physical design", "civil engineer", "industrial engineer", "chemical",
    "technician", "field application", "designer", "ui/ux", "ux/ui",
    "ios", "android", "swift developer", "objective-c", "mobile developer",
    "mobile engineer", "react native", "flutter", "unity", "game developer",
    "game engineer", "frontend engineer", "front-end engineer", "front end engineer",
    "frontend developer", "ui engineer",
]

EXCLUDE_KEYWORDS: list[str] = [
    "qa", "manual testing", "account executive", "recruiter", "customer success",
    "support engineer", "frontend only",
]

OVERQUALIFIED_TITLE_TERMS: list[str] = [
    "principal", "staff", "distinguished", "director", "head of", "vp",
    "vice president", "chief", "fellow",
]

REMOTE_TERMS: list[str] = ["remote", "anywhere", "work from home", "wfh", "distributed"]

PINNED_COMPANIES: list[str] = ["Zscaler"]

USER_YEARS_EXPERIENCE = 5

ISRAEL_LOCATION_TERMS: list[str] = [
    "israel", "ישראל", "tel aviv", "tel-aviv", "telaviv", "tlv", "herzliya", "herzilya",
    "haifa", "raanana", "ra'anana", "jerusalem", "petah tikva", "petach tikva",
    "petah-tikva", "netanya", "beer sheva", "be'er sheva", "beersheba", "rehovot",
    "yokneam", "yoqneam", "kfar saba", "ramat gan", "caesarea", "hod hasharon",
    "or yehuda", "airport city", "modiin", "modi'in", "lod", "ness ziona",
    "givatayim", "holon", "bnei brak", "תל אביב", "חיפה", "ירושלים", "הרצליה",
    "רעננה", "רמת גן", "פתח תקווה", "נתניה", "ראש העין", "באר שבע", "רחובות",
    "יקנעם", "כפר סבא", "הוד השרון", "מודיעין", "נס ציונה", "גבעתיים", "חולון",
    "בני ברק", "לוד", "אור יהודה", "קיסריה", "פתח תקוה", "עומר",
]

CITY_ALIASES: dict[str, list[str]] = {
    "tel aviv": ["tel aviv", "tel-aviv", "telaviv", "tlv", "תל אביב"],
    "herzliya": ["herzliya", "herzilya", "הרצליה"],
    "haifa": ["haifa", "חיפה"],
    "jerusalem": ["jerusalem", "ירושלים"],
    "raanana": ["raanana", "ra'anana", "רעננה"],
    "petah tikva": ["petah tikva", "petach tikva", "petah-tikva", "פתח תקווה", "פתח תקוה"],
    "netanya": ["netanya", "נתניה"],
    "ramat gan": ["ramat gan", "ramat-gan", "רמת גן"],
    "beer sheva": ["beer sheva", "be'er sheva", "beersheba", "באר שבע"],
    "rehovot": ["rehovot", "רחובות"],
    "yokneam": ["yokneam", "yoqneam", "יקנעם"],
    "kfar saba": ["kfar saba", "כפר סבא"],
    "caesarea": ["caesarea", "קיסריה"],
    "hod hasharon": ["hod hasharon", "הוד השרון"],
    "or yehuda": ["or yehuda", "אור יהודה"],
    "airport city": ["airport city"],
    "modiin": ["modiin", "modi'in", "מודיעין"],
    "lod": ["lod", "לוד"],
    "ness ziona": ["ness ziona", "נס ציונה"],
    "givatayim": ["givatayim", "גבעתיים"],
    "holon": ["holon", "חולון"],
    "bnei brak": ["bnei brak", "בני ברק"],
    "rosh haayin": ["rosh haayin", "ראש העין"],
    "omer": ["omer", "עומר"],
    "israel": ["israel", "ישראל"],
}

# Skill vocabulary pulled out of CV text to build each CV's must-have keyword set.
SKILLS_VOCAB: list[str] = [
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "golang", "rust",
    "react", "angular", "vue", "node", "node.js", "django", "flask", "fastapi",
    "sql", "postgresql", "postgres", "mysql", "mongodb", "redis", "cassandra",
    "kafka", "rabbitmq", "elasticsearch", "graphql", "grpc", "rest", "microservices",
    "docker", "kubernetes", "terraform", "ansible", "helm", "aws", "gcp", "azure",
    "ci/cd", "jenkins", "github actions", "git", "linux", "bash",
    "machine learning", "deep learning", "pytorch", "tensorflow", "scikit-learn",
    "nlp", "llm", "rag", "computer vision", "data science", "pandas", "numpy",
    "spark", "hadoop", "airflow", "etl", "snowflake", "databricks",
    "devops", "sre", "observability", "prometheus", "grafana", "datadog",
    "distributed systems", "scalability", "asyncio", "inference", "api",
    "selenium", "cypress", "playwright", "pytest", "automation", "qa",
    "vector database", "mlops", "genai", "gen ai", "vllm", "gpu", "cuda",
    "inference pipeline", "inference infrastructure", "model evaluation",
    "model validation", "model deployment", "deployment infrastructure",
    "infrastructure automation", "site reliability", "reliability engineering",
    "ml infrastructure", "scalable infrastructure",
]
