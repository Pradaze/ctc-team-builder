"""
Settings for the LBS Cleantech Challenge 2027 team builder.

This is the only file you should need to edit. Run
    python team_builder.py check applicants.csv
after any change to confirm every column below is found in your data.
"""

# ---------------------------------------------------------------------------
# 1. Tally
# ---------------------------------------------------------------------------
# Form ID: the part after /forms/ in the editor URL (tally.so/forms/<ID>/edit).
# The API key is NOT stored here; set the TALLY_API_KEY environment variable.
TALLY_FORM_ID = "your-form-id"

# ---------------------------------------------------------------------------
# 2. Which form question feeds which criterion
# ---------------------------------------------------------------------------
# Column titles exactly as in applicants.csv. Follow-up fields that Tally
# leaves untitled are named "<previous question> (details)" automatically.
NAME = "1. Full Name"
EMAIL = "2. Email adress"
COUNTRY = "3. Country of Residence"
COUNTRY_CODE = "3. Country of Residence (details)"   # 2-letter code Tally adds
INSTITUTION = "4. Please share your current academic institution and programme"
LINKEDIN = "5. Please share your LinkedIn profile"
CV = "6. Please upload your CV (PDF format only)"
BIO = ("7. Please submit a short bio of yourself. This will be used for matching "
       "purposes to create or join a team (1,500 character max)")
IDEA = ("8. Do you already have a Cleantech idea? (if yes, please briefly describe "
        "your Cleantech idea)")
IDEA_DETAILS = IDEA + " (details)"
AREAS = "9. Which area(s) are you interested in for purposes of team matching?"
AREAS_OTHER = AREAS + " (details)"
FINALS = ("11. All teams selected for finals will be required to attend the Cleantech "
          "Challenge in London at LBS Campus on April 27-28, 2027. Please confirm that "
          "you are able to attend in person.")

COLUMNS = {
    # identity: shown in the output, never used for matching
    "name": NAME,
    "email": EMAIL,                  # also used to spot repeat submissions
    "linkedin": LINKEDIN,
    "submitted_at": "Submitted at",  # the latest submission per person is kept

    # Criterion 2 - interests
    "interest_tags": AREAS,                               # multi-select
    "interest_text": [BIO, IDEA_DETAILS, AREAS_OTHER],    # free text

    # Criterion 3 - idea status, and the idea descriptions (used to keep
    # idea-holders who share a team working on similar ideas)
    "idea_status": IDEA,
    "idea_text": [IDEA_DETAILS],

    # Criterion 4 - time zone
    "country": COUNTRY,
    "country_code": COUNTRY_CODE,    # fallback when the country isn't recognised

    # Criterion 5 - background
    "degree": INSTITUTION,

    # Criterion 6 - skills (keywords are searched in these and in the CV text)
    "skill_text": [BIO, INSTITUTION],

    # CVs: `python team_builder.py cvs applicants.csv` reads these PDFs into a
    # cv_text column, which feeds keywords, skills, background and interests.
    "cv_file": CV,
    # Any other text columns to mine for keywords, e.g. if you later collect
    # LinkedIn profiles exported as PDF ("More > Save to PDF") in another column.
    "extra_keyword_text": [],

    # Criterion 7 - commitment
    "availability": FINALS,
}

# Multi-select options that say nothing about interests (their typed details
# are still read through interest_text).
INTEREST_TAGS_IGNORE = ["Other (please specify)"]

# ---------------------------------------------------------------------------
# 3. How answers are interpreted
# ---------------------------------------------------------------------------
IDEA_YES_KEYWORDS = ["yes"]

# Commitment: answer -> score 0-1. Anyone scoring below 0.5 is flagged.
AVAILABILITY_SCORES = {
    "Yes": 1.0,
    "No": 0.3,
}
LOW_AVAILABILITY_LABEL = "Can't attend the London finals"

# Time zones. Same zone (or within TZ_FULL_MATCH_HOURS) scores 1.0, falling
# to 0 at TZ_ZERO_MATCH_HOURS apart. Unknown locations score TZ_UNKNOWN_SCORE
# with everyone, so they're neither favoured nor penalised.
TZ_FULL_MATCH_HOURS = 1
TZ_ZERO_MATCH_HOURS = 6
TZ_UNKNOWN_SCORE = 0.5
TZ_FLAG_GAP_HOURS = 5          # teams spread wider than this get flagged

# Answers the lookup doesn't recognise: answer (any case) -> 2-letter country
# code, or a UTC offset in hours. `check` lists anything unrecognised.
TIMEZONE_OVERRIDES = {
    # "kilifi": "KE",
    # "somewhere remote": 3,
}

# Background categories (criterion 5): matched against the institution and
# programme answer, and separately against the CV to find each person's main
# field. First match wins for the programme; the CV uses the most frequent.
BACKGROUND_CATEGORIES = {
    "Engineering & tech": [
        "engineer", "engineering", "computer science", "software", "electrical",
        "mechanical", "civil", "chemical engineering", "technology", "computing",
        "robotics", "information technology", "electronics", "mechatronics",
    ],
    "Natural & environmental sciences": [
        "physics", "chemistry", "biology", "environmental science", "earth science",
        "geology", "materials", "biochemistry", "ecology", "natural sciences",
        "environmental", "geography", "marine", "nanotechnology", "biotechnology",
    ],
    "Agriculture & food": [
        "agriculture", "agricultural", "agronomy", "agribusiness", "food science",
        "forestry", "horticulture", "fisheries", "veterinary",
    ],
    "Maths & data": ["mathematics", "maths", "statistics", "data science", "data analytics"],
    "Finance & economics": [
        "finance", "economics", "accounting", "banking", "investment",
        "private equity", "venture capital", "actuarial",
    ],
    "Business & management": [
        "business", "management", "mba", "marketing", "consulting", "strategy",
        "entrepreneurship", "innovation", "operations", "supply chain",
    ],
    "Policy, law & social sciences": [
        "policy", "law", "legal", "politics", "international relations",
        "sociology", "development studies", "public administration",
        "government", "public health", "anthropology", "urban planning",
    ],
    "Design & architecture": [
        "design", "architecture", "media", "journalism", "communications", "arts",
    ],
    "Energy & sustainability": [
        "energy", "renewable", "renewables", "sustainability", "sustainable development",
        "climate", "power systems",
    ],
}

# Skill areas (criterion 6) and the keywords that signal each one.
SKILLS = {
    "technical": [
        "engineering", "engineer", "python", "coding", "programming", "software",
        "hardware", "prototype", "prototyping", "cad", "autocad", "solidworks",
        "matlab", "simulation", "machine learning", "iot", "arduino", "gis",
        "laboratory", "lab", "design and installation",
    ],
    "analytical": [
        "analytical", "analytics", "data analysis", "statistics", "econometrics",
        "quantitative", "sql", "excel", "stata", "spss", "modelling", "modeling",
        "power bi", "tableau", "problem solving", "life cycle assessment", "lca",
    ],
    "commercial": [
        "business development", "sales", "marketing", "strategy", "go-to-market",
        "partnerships", "consulting", "product management", "customer",
        "entrepreneur", "entrepreneurship", "startup", "start-up", "founder",
        "co-founder", "commercial", "business model",
    ],
    "financial": [
        "finance", "financial", "valuation", "dcf", "investment", "accounting",
        "fundraising", "venture capital", "banking", "financial modelling",
        "financial modeling", "private equity", "cfa", "budgeting", "carbon credits",
        "carbon markets", "impact investing", "grant",
    ],
    "research": [
        "research", "researcher", "literature review", "academic", "phd", "publication",
        "published", "journal", "thesis", "dissertation", "fieldwork", "survey",
        "peer-reviewed", "conference",
    ],
    "communication": [
        "communication", "communications", "presentation", "public speaking",
        "pitching", "pitch", "writing", "storytelling", "advocacy", "outreach",
        "social media", "content creation", "graphic design", "ux", "branding",
        "community engagement", "facilitation",
    ],
}

# ---------------------------------------------------------------------------
# 4. Keyword extraction (NLP)
# ---------------------------------------------------------------------------
KEYWORDS_PER_PERSON = 10
# A keyword must appear for at least this many applicants to count, which
# filters out names, one-off phrases and CV clutter.
KEYWORD_MIN_APPLICANTS = 2
# ...and for no more than this share of applicants (too common to be useful).
KEYWORD_MAX_SHARE = 0.5

# Optional: sentence embeddings for the free-text interests, which handle
# synonyms and other languages better. Needs `pip install sentence-transformers`.
USE_EMBEDDINGS = False

# ---------------------------------------------------------------------------
# 5. Scoring weights
# ---------------------------------------------------------------------------
WEIGHTS = {
    "interest_similarity": 4.0,    # criterion 2
    "timezone_match": 2.0,         # criterion 4
    "background_diversity": 1.0,   # criterion 5
    "skill_coverage": 1.0,         # criterion 6
    "commitment_balance": 0.5,     # criterion 7: spreads people who can't attend the finals
    "idea_mix": 1.5,               # criterion 3: closer to half-and-half is better
    "idea_alignment": 2.0,         # criterion 3: idea-holders in a team have similar ideas
}

# Idea similarity is scaled so the closest 10% of idea pairs score 1. Teams whose
# idea-holders' ideas score below this on average are flagged.
IDEA_ALIGNMENT_FLAG = 0.15

# How interest similarity blends its three sources (re-weighted per pair when
# someone is missing one, e.g. no CV text).
INTEREST_SHARES = {
    "tags": 0.4,   # the multi-select areas
    "text": 0.4,   # bio, idea description, "other" details
    "cv": 0.2,     # keywords from the CV (and extra_keyword_text)
}

# ---------------------------------------------------------------------------
# 6. Team and pool rules
# ---------------------------------------------------------------------------
MIN_TEAM_SIZE = 3
TARGET_TEAM_SIZE = 5
MAX_TEAM_SIZE = 6            # criterion 8

POOL_TARGET_SIZE = 20
POOL_MAX_SIZE = 40
POOL_MIN_SIZE = 6
POOL_INTEREST_SHARE = 0.6    # interest vs time zone when forming pools

# Leaving people out is based on INTEREST fit only, so nobody is dropped just
# for living somewhere few other applicants do.
LEAVE_OUT_RELATIVE_FIT = 0.5   # below this share of the median fit...
MAX_UNASSIGNED_SHARE = 0.10    # ...up to this share of applicants

# Review: blank 'include' = included unless you mark the person "no".
# Set to False to include only people marked "yes".
INCLUDE_BY_DEFAULT = False

RANDOM_SEED = 2027
