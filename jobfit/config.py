"""Paths and tuned scoring config for Dan Hanegbi's job search.

The role/keyword/location config here mirrors linkedin-match's backend/core/keywords.py
(same person, same preferences) — copied rather than imported so this app stays a
self-contained, independently runnable project with no cross-repo coupling.
"""

from pathlib import Path

# --- Personal inputs ---
CV_DEFAULT = Path(r"C:\Users\user\Documents\Job\2026\Dan_Hanegbi_Resume.docx")
CV_INFRA = Path(r"C:\Users\user\Documents\Job\2026\Infra\Dan_Hanegbi_Resume.docx")
CONNECTIONS_CSV = Path(r"C:\Users\user\Code\linkedin-match\Connections.csv")

# --- Control-panel CV profile registry (Task 5 relocates these next to the
# other personal-input constants and removes CV_DEFAULT/CV_INFRA above) ---
CV_PROFILES_DIR = Path(__file__).parent / "data" / "cvs"
CV_PROFILES_REGISTRY = Path(__file__).parent / "data" / "profiles.json"

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

COMPANY_JOBS_TTL_HOURS = 24

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

# Two CVs, same person/experience, different ATS-facing emphasis: "default" leans
# AI/software, "infra" leans platform/SRE/MLOps. Role-title weighting is the real
# differentiator between them (the CV text itself only differs in a few phrases).
ROLE_WEIGHTS_DEFAULT: dict[str, int] = {
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

ROLE_WEIGHTS_INFRA: dict[str, int] = {
    "site reliability engineer": 58, "platform engineer": 56, "infrastructure engineer": 55,
    "devops engineer": 54, "ai infrastructure": 54, "distributed systems engineer": 50,
    "data engineer": 48, "backend engineer": 47, "python engineer": 46,
    "software engineer": 45, "backend developer": 44, "software developer": 44,
    "ml engineer": 40, "machine learning engineer": 40, "ai engineer": 38,
    "full stack engineer": 35, "full-stack engineer": 35, "fullstack engineer": 35,
    "full stack developer": 33, "mlops engineer": 60, "ml infrastructure engineer": 60,
    "ml platform engineer": 59, "ai platform engineer": 58, "llm infrastructure engineer": 60,
    "inference engineer": 58, "machine learning infrastructure engineer": 60, "ml systems engineer": 56,
}

ROLE_WEIGHTS_BY_PROFILE: dict[str, dict[str, int]] = {
    "default": ROLE_WEIGHTS_DEFAULT,
    "infra": ROLE_WEIGHTS_INFRA,
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
