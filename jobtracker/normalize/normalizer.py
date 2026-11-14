"""Normalize a :class:`RawJob` into a :class:`CanonicalJob`.

Deterministic, rule-based: cleans HTML, splits location into
city/province_state/country, infers remote_status and employment_type from
keyword signals (defaulting to ``unknown``), and splits
requirements/preferred_qualifications/skills with section heuristics plus the
SPEC skill-signal list. Computes the stable ``description_hash``.
"""

from __future__ import annotations

import re

from jobtracker.connectors.base import RawJob
from jobtracker.normalize.hashing import description_hash
from jobtracker.normalize.html_clean import clean_html
from jobtracker.schemas.company import CompanyConfig
from jobtracker.schemas.job import (
    CanonicalJob,
    EmploymentType,
    RemoteStatus,
)

# SPEC "Skill signals" list (used for skill extraction).
SKILL_SIGNALS: tuple[str, ...] = (
    "CAD",
    "SolidWorks",
    "CATIA",
    "NX",
    "Creo",
    "Fusion 360",
    "GD&T",
    "FEA",
    "CFD",
    "ANSYS",
    "Abaqus",
    "MATLAB",
    "Simulink",
    "DFM",
    "DFA",
    "tolerance analysis",
    "prototyping",
    "3D printing",
    "CNC",
    "root cause analysis",
    "test fixtures",
    "BOM",
    "PLM",
    "thermal analysis",
    "fluid mechanics",
    "heat transfer",
    "kinematics",
    "dynamics",
)

# Canada province/territory tokens (full + abbreviations).
_CANADA_PROVINCES = {
    "ontario": "ON", "on": "ON",
    "quebec": "QC", "qc": "QC", "québec": "QC",
    "british columbia": "BC", "bc": "BC",
    "alberta": "AB", "ab": "AB",
    "manitoba": "MB", "mb": "MB",
    "saskatchewan": "SK", "sk": "SK",
    "nova scotia": "NS", "ns": "NS",
    "new brunswick": "NB", "nb": "NB",
    "newfoundland and labrador": "NL", "nl": "NL",
    "prince edward island": "PE", "pe": "PE",
    "northwest territories": "NT", "nt": "NT",
    "yukon": "YT", "yt": "YT",
    "nunavut": "NU", "nu": "NU",
}
# Canada is detected ONLY from the unambiguous full word (or province codes
# below). "CA" is NOT here: in job postings "CA" is the US state California far
# more often than Canada (e.g. "Hawthorne, CA" = SpaceX HQ in California).
# Mislabeling those as Canada previously let US-only (ITAR/citizen-only) roles
# bypass the eligibility filter, which is exactly the bug the user kept hitting.
_CANADA_TOKENS = {"canada"}

# A representative set of US state abbreviations + a few full names.
_US_STATES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy",
}
_US_TOKENS = {"united states", "usa", "us", "u.s.", "u.s.a."}

_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
_ONSITE_RE = re.compile(r"\b(on-?site|in-?office|in-?person)\b", re.IGNORECASE)

_COOP_RE = re.compile(r"\bco-?ops?\b|\bcoop\b|\bwork\s+term\b", re.IGNORECASE)
_INTERN_RE = re.compile(
    r"\bintern(?:s|ship|ships)?\b|\bstagiaire\b|\bsummer\s+student\b",
    re.IGNORECASE,
)
_NEWGRAD_RE = re.compile(
    r"\b(?:new|recent)\s*grad(?:uate)?s?\b|\bgraduate\s+(?:engineer|program|"
    r"rotation(?:al)?|development\s+program|trainee)\b|\bengineer[-\s]in[-\s]"
    r"training\b|\bEIT\b|\buniversity\s+graduate\b|\bnouveaux?\s+diplôm",
    re.IGNORECASE,
)
_FULLTIME_RE = re.compile(r"\bfull[-\s]?time\b", re.IGNORECASE)

_SECTION_HEADINGS = {
    "requirements": "requirements",
    "qualifications": "requirements",
    "minimum qualifications": "requirements",
    "basic qualifications": "requirements",
    "what you'll need": "requirements",
    "what you will need": "requirements",
    "preferred qualifications": "preferred",
    "preferred": "preferred",
    "nice to have": "preferred",
    "bonus points": "preferred",
    "preferred skills": "preferred",
}

_BULLET_RE = re.compile(r"^[\-\*\u2022\u2023\u25E6\u2043\u2219]\s*")


class QuarantineError(ValueError):
    """Raised when a job fails the validation gate (missing required URLs)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
}
_US_COUNTRY_WORDS = {
    "united states", "united states of america", "usa", "us", "u.s.", "u.s.a.",
    "america",
}
# Other countries we recognise. A role located in one of these needs that
# country's work authorization, which the Canadian-citizen profile lacks.
_OTHER_COUNTRIES = {
    "united kingdom": "United Kingdom", "uk": "United Kingdom",
    "england": "United Kingdom", "scotland": "United Kingdom",
    "ireland": "Ireland", "france": "France", "germany": "Germany",
    "spain": "Spain", "italy": "Italy", "netherlands": "Netherlands",
    "belgium": "Belgium", "switzerland": "Switzerland", "sweden": "Sweden",
    "norway": "Norway", "denmark": "Denmark", "finland": "Finland",
    "poland": "Poland", "czech republic": "Czech Republic", "austria": "Austria",
    "portugal": "Portugal", "romania": "Romania", "hungary": "Hungary",
    "mexico": "Mexico", "brazil": "Brazil", "chile": "Chile", "peru": "Peru",
    "argentina": "Argentina", "colombia": "Colombia", "india": "India",
    "china": "China", "japan": "Japan", "south korea": "South Korea",
    "korea": "South Korea", "singapore": "Singapore", "malaysia": "Malaysia",
    "philippines": "Philippines", "indonesia": "Indonesia",
    "australia": "Australia", "new zealand": "New Zealand",
    "south africa": "South Africa", "united arab emirates": "United Arab Emirates",
    "uae": "United Arab Emirates", "saudi arabia": "Saudi Arabia",
    "qatar": "Qatar", "israel": "Israel", "turkey": "Turkey", "egypt": "Egypt",
    "morocco": "Morocco", "taiwan": "Taiwan", "vietnam": "Vietnam",
    "thailand": "Thailand", "mongolia": "Mongolia",
}
# Unambiguous Canadian cities (Workday often gives only a city). Deliberately
# excludes names shared with large non-Canadian places (London, Cambridge,
# Windsor, Kingston, Hamilton, Richmond, Victoria, Waterloo-Iowa is rare).
_CANADIAN_CITIES = {
    "toronto", "mississauga", "markham", "ottawa", "montreal", "montréal",
    "kitchener", "waterloo", "guelph", "oshawa", "pickering", "ajax", "whitby",
    "clarington", "calgary", "edmonton", "vancouver", "burnaby", "surrey",
    "winnipeg", "halifax", "dartmouth", "quebec city", "québec", "laval",
    "longueuil", "saskatoon", "regina", "burlington", "oakville", "brampton",
    "vaughan", "concord", "saint-laurent", "dorval", "mirabel", "boisbriand",
    "bromont", "peterborough", "sarnia", "fort mcmurray", "sudbury",
    "thunder bay", "kincardine", "tiverton", "renfrew", "chalk river",
    "brantford", "st. catharines", "barrie", "moncton", "fredericton",
    "st. john's", "saint john", "trois-rivières", "sherbrooke", "gatineau",
    "kanata", "north york", "scarborough", "etobicoke", "woodbridge", "bolton",
    "stoney creek", "ayr", "elmira", "fergus", "milton", "newmarket",
    "richmond hill", "aurora", "bécancour", "sept-îles", "trail", "kamloops",
    "kelowna", "prince george", "nanaimo", "lethbridge", "red deer",
    "medicine hat", "grande prairie", "estevan", "flin flon", "gaspe", "gaspé",
    "brossard", "varennes", "la prairie", "pointe-claire",
    "saint-hubert", "st-laurent", "lachine", "anjou", "terrebonne",
    "drummondville", "granby", "saint-jérôme", "rimouski", "chicoutimi",
    "saguenay", "jonquière", "alma", "baie-comeau",
}
# Uppercase ISO country codes for other countries (SuccessFactors/Workday emit
# "Colon, MEX, MX, 76270"). Two-letter codes that collide with US states or
# Canadian provinces (IN, DE, CO, ID, IL, ...) are deliberately left out.
_OTHER_COUNTRY_CODES = {
    "MX": "Mexico", "MEX": "Mexico", "GB": "United Kingdom",
    "GBR": "United Kingdom", "FR": "France", "FRA": "France", "DEU": "Germany",
    "ES": "Spain", "ESP": "Spain", "IT": "Italy", "ITA": "Italy",
    "NL": "Netherlands", "NLD": "Netherlands", "BE": "Belgium",
    "BEL": "Belgium", "CH": "Switzerland", "CHE": "Switzerland",
    "SE": "Sweden", "SWE": "Sweden", "NO": "Norway", "NOR": "Norway",
    "DK": "Denmark", "DNK": "Denmark", "FI": "Finland", "FIN": "Finland",
    "PL": "Poland", "POL": "Poland", "CZ": "Czech Republic",
    "CZE": "Czech Republic", "AT": "Austria", "AUT": "Austria",
    "PT": "Portugal", "PRT": "Portugal", "RO": "Romania", "ROU": "Romania",
    "HU": "Hungary", "HUN": "Hungary", "IE": "Ireland", "IRL": "Ireland",
    "BR": "Brazil", "BRA": "Brazil", "CL": "Chile", "CHL": "Chile",
    "PE": "Peru", "PER": "Peru", "AR": "Argentina", "ARG": "Argentina",
    "COL": "Colombia", "IND": "India", "CN": "China", "CHN": "China",
    "JP": "Japan", "JPN": "Japan", "KR": "South Korea", "KOR": "South Korea",
    "SG": "Singapore", "SGP": "Singapore", "MY": "Malaysia", "MYS": "Malaysia",
    "PH": "Philippines", "PHL": "Philippines", "IDN": "Indonesia",
    "AU": "Australia", "AUS": "Australia", "NZ": "New Zealand",
    "NZL": "New Zealand", "ZA": "South Africa", "ZAF": "South Africa",
    "AE": "United Arab Emirates", "ARE": "United Arab Emirates",
    "SAU": "Saudi Arabia", "QA": "Qatar", "QAT": "Qatar", "ISR": "Israel",
    "TR": "Turkey", "TUR": "Turkey", "EG": "Egypt", "EGY": "Egypt",
    "TW": "Taiwan", "TWN": "Taiwan", "VN": "Vietnam", "VNM": "Vietnam",
    "TH": "Thailand", "THA": "Thailand", "MN": None, "MNG": "Mongolia",
}
_OTHER_COUNTRY_CODES = {k: v for k, v in _OTHER_COUNTRY_CODES.items() if v}
# Large US cities that boards list without a state ("Pittsburgh",
# "New York City"). Names shared with Canadian cities are excluded.
_US_CITIES = {
    "new york", "new york city", "nyc", "brooklyn", "manhattan",
    "los angeles", "san francisco", "san jose", "san diego", "seattle",
    "boston", "chicago", "pittsburgh", "detroit", "houston", "austin",
    "dallas", "denver", "phoenix", "atlanta", "miami", "philadelphia",
    "washington dc", "washington, d.c.", "minneapolis", "cleveland",
    "cincinnati", "columbus", "indianapolis", "nashville", "charlotte",
    "raleigh", "salt lake city", "las vegas", "portland", "baltimore",
    "st. louis", "kansas city", "milwaukee", "sacramento", "palo alto",
    "mountain view", "sunnyvale", "santa clara", "redmond", "hawthorne",
    "foster city", "menlo park", "cupertino", "irvine", "orlando", "tampa",
    "huntsville", "cape canaveral", "starbase", "mcgregor", "redondo beach",
    "el segundo", "long beach", "fremont", "oakland", "berkeley",
    "ann arbor", "cambridge, ma", "somerville", "waltham", "bedford",
    "schenectady", "niskayuna", "greenville", "findlay township", "pensacola",
    "greer", "wilmington", "charleroi", "fort myers", "cedar rapids",
    "mukilteo", "everett", "renton", "wichita", "fort worth", "san antonio",
    "tempe", "chandler", "mesa", "boulder", "golden", "pasadena", "torrance",
    "carlsbad", "san carlos", "redwood city", "hayward", "livermore",
    "rochester", "albany", "syracuse", "buffalo", "hartford", "stamford",
    "princeton", "newark", "jersey city", "pittsburg", "dearborn",
    "auburn hills", "troy", "warren", "grand rapids", "lansing", "toledo",
    "dayton", "akron", "louisville", "lexington", "memphis", "knoxville",
    "chattanooga", "birmingham, al", "jacksonville", "fort lauderdale",
    "west palm beach", "boca raton", "melbourne, fl", "titusville", "omaha",
    "des moines", "madison", "green bay", "fargo", "boise", "spokane",
    "tacoma", "anchorage", "honolulu", "albuquerque", "el paso", "tucson",
    "reno", "provo", "ogden", "colorado springs", "aurora, co",
}
_CA_POSTAL_RE = re.compile(r"\b[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d\b")
_US_ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
# Workday-style compact codes: "CA-ON-Toronto", "US-TX-Austin", "CAN-ON-...".
_COMPACT_RE = re.compile(r"^(CA|CAN|US|USA)[-\s]+([A-Z]{2})[-\s]+(.+)$")


def split_location(location: str) -> tuple[str | None, str | None, str | None]:
    """Split a free-form location into ``(city, province_state, country)``.

    Handles the formats the connected boards actually emit:

    * ``"Hawthorne, CA"`` -- "CA" is the US state California (SpaceX HQ).
    * ``"Renfrew, ON, CA, K7V 0C2"`` -- SuccessFactors uses "CA" as the
      COUNTRY CODE for Canada after a Canadian province.
    * ``"Nashville, TN, US"``, ``"Toronto, Ontario, Canada"``,
      ``"CA-ON-Toronto"``, ``"Markham"`` (city only),
      ``"Dallas, United States of America"`` (Workday + detail country).

    Priority: an explicit country word wins; otherwise a Canadian province
    (or postal code) means Canada, and only then does a US state code mean the
    United States. Never guesses: unknown stays ``None``.
    """
    if not location:
        return None, None, None
    text = location.strip()
    compact = _COMPACT_RE.match(text)
    if compact:
        cc, region, rest = compact.groups()
        country = "Canada" if cc in ("CA", "CAN") else "United States"
        return rest.strip() or None, region, country

    tokens = [part.strip() for part in re.split(r"[,;|]", text) if part.strip()]
    if not tokens:
        return None, None, None
    # "Wilmington NC USA" / "Charleroi USA" (one token, space separated): peel
    # trailing country / region words off the last token into their own tokens.
    last_words = tokens[-1].split()
    peeled: list[str] = []
    while len(last_words) > 1:
        word = last_words[-1]
        low = word.lower()
        if (
            low in _US_COUNTRY_WORDS or low in _CANADA_TOKENS
            or low in _OTHER_COUNTRIES or word in _OTHER_COUNTRY_CODES
            or (word.isupper() and (low in _US_STATES or low in _CANADA_PROVINCES))
        ):
            peeled.insert(0, last_words.pop())
        else:
            break
    if peeled:
        tokens = tokens[:-1] + [" ".join(last_words)] + peeled
    lowered = [tok.lower() for tok in tokens]

    city: str | None = None
    province_state: str | None = None
    country: str | None = None

    # 1) Explicit country words.
    for low in lowered:
        if low in _CANADA_TOKENS:
            country = "Canada"
        elif low in _US_COUNTRY_WORDS:
            country = country or "United States"
        elif low in _OTHER_COUNTRIES:
            country = country or _OTHER_COUNTRIES[low]
    for raw in tokens:
        if raw in _OTHER_COUNTRY_CODES and country is None:
            country = _OTHER_COUNTRY_CODES[raw]

    # 2) Canadian province (full name or code). ON/QC/BC/AB/... are not US
    #    state codes, so they are unambiguous; once one is present a bare "CA"
    #    token is the Canada country code, not California.
    canadian_province = next(
        (_CANADA_PROVINCES[low] for low in lowered if low in _CANADA_PROVINCES),
        None,
    )
    if canadian_province:
        province_state = canadian_province
        if country is None or (country == "United States" and "ca" in lowered):
            country = "Canada"

    # 3) US state code / name (only when no Canadian province was found).
    if province_state is None:
        for raw, low in zip(tokens, lowered, strict=True):
            if (low in _US_STATES and raw.upper() == raw) or low in _US_STATE_NAMES:
                province_state = raw.upper() if len(raw) == 2 else raw
                country = country or "United States"
                break

    # 4) Postal codes, then unambiguous Canadian city names.
    if country is None and _CA_POSTAL_RE.search(text):
        country = "Canada"
    if country is None and _US_ZIP_RE.search(text):
        country = "United States"
    if country is None and any(low in _CANADIAN_CITIES for low in lowered):
        country = "Canada"
    if country is None and (
        any(low in _US_CITIES for low in lowered) or text.lower() in _US_CITIES
    ):
        country = "United States"

    # City: first token that is not a region/country/postal marker.
    markers = (
        _CANADA_TOKENS | _US_COUNTRY_WORDS | set(_CANADA_PROVINCES) | _US_STATES
        | _US_STATE_NAMES | set(_OTHER_COUNTRIES)
        | {code.lower() for code in _OTHER_COUNTRY_CODES}
    )
    for raw, low in zip(tokens, lowered, strict=True):
        if low not in markers and not _CA_POSTAL_RE.fullmatch(raw) and not (
            _US_ZIP_RE.fullmatch(raw)
        ):
            city = raw
            break

    return city, province_state, country


def infer_remote_status(title: str, description: str) -> RemoteStatus:
    """Infer remote/hybrid/onsite from title+description, default unknown."""
    text = f"{title}\n{description}"
    if _HYBRID_RE.search(text):
        return RemoteStatus.HYBRID
    if _REMOTE_RE.search(text):
        return RemoteStatus.REMOTE
    if _ONSITE_RE.search(text):
        return RemoteStatus.ONSITE
    return RemoteStatus.UNKNOWN


def infer_employment_type(title: str, description: str, hint: str | None) -> EmploymentType:
    """Infer employment type from title/description/hint, default unknown.

    The TITLE is authoritative. A "New Grad" / "New Graduate" title is a
    disqualifying signal that previously lost to an incidental "internship"
    mention in the body (e.g. a posting whose description references the
    company's "internship and new-grad programs"), so such roles slipped onto
    the eligible page. We therefore resolve the title first, and within the
    title check new-grad BEFORE internship. Only when the title is silent do we
    fall back to the description/hint, still preferring the disqualifying
    new-grad / full-time signals over a stray intern mention.
    """
    title_text = title or ""
    # 1) Title wins. Co-op and internship are the roles we want; new-grad is a
    #    disqualifier that must be seen before a same-title "intern" token.
    if _COOP_RE.search(title_text):
        return EmploymentType.CO_OP
    if _NEWGRAD_RE.search(title_text):
        return EmploymentType.NEW_GRAD
    if _INTERN_RE.search(title_text):
        return EmploymentType.INTERNSHIP
    if _FULLTIME_RE.search(title_text):
        return EmploymentType.FULL_TIME

    # 2) Title silent -> consult hint + description. Still check new-grad before
    #    internship so a body that mentions both does not mask the disqualifier.
    body = " ".join(filter(None, [hint or "", description or ""]))
    if _COOP_RE.search(body):
        return EmploymentType.CO_OP
    if _NEWGRAD_RE.search(body):
        return EmploymentType.NEW_GRAD
    if _INTERN_RE.search(body):
        return EmploymentType.INTERNSHIP
    if _FULLTIME_RE.search(body):
        return EmploymentType.FULL_TIME
    return EmploymentType.UNKNOWN


def _heading_bucket(line: str) -> str | None:
    key = line.strip().rstrip(":").lower()
    return _SECTION_HEADINGS.get(key)


def split_sections(description: str) -> tuple[list[str], list[str]]:
    """Split requirements and preferred qualifications via section headings."""
    requirements: list[str] = []
    preferred: list[str] = []
    current: str | None = None

    for line in description.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        bucket = _heading_bucket(stripped)
        if bucket is not None:
            current = bucket
            continue
        bullet = _BULLET_RE.match(stripped)
        if bullet:
            item = _BULLET_RE.sub("", stripped).strip()
            if not item:
                continue
            if current == "preferred":
                preferred.append(item)
            elif current == "requirements":
                requirements.append(item)
        else:
            # A non-bullet line ends the current bullet section.
            current = None if bucket is None else current

    return requirements, preferred


def extract_skills(description: str) -> list[str]:
    """Return SPEC skill signals present in the description (original casing)."""
    found: list[str] = []
    for skill in SKILL_SIGNALS:
        pattern = re.compile(re.escape(skill), re.IGNORECASE)
        if pattern.search(description):
            found.append(skill)
    return found


def normalize(raw: RawJob, company: CompanyConfig) -> CanonicalJob:
    """Convert a :class:`RawJob` into a validated :class:`CanonicalJob`.

    Raises :class:`QuarantineError` when ``application_url`` or ``source_url``
    is missing so the caller can route the payload to quarantine instead of
    dropping it.
    """
    if not raw.application_url or not raw.source_url:
        missing = []
        if not raw.application_url:
            missing.append("application_url")
        if not raw.source_url:
            missing.append("source_url")
        raise QuarantineError(f"missing {', '.join(missing)}")

    description = clean_html(raw.description_html)
    city, province_state, country = split_location(raw.location)
    remote_status = infer_remote_status(raw.title, description)
    employment_type = infer_employment_type(
        raw.title, description, raw.employment_type_hint
    )
    requirements, preferred = split_sections(description)
    skills = extract_skills(description)

    hash_value = description_hash(description, raw.title, raw.location, requirements)

    return CanonicalJob(
        source_job_id=raw.source_job_id,
        company_id=company.id,
        company_name=company.name,
        title=raw.title,
        normalized_title=raw.title.strip().lower(),
        location=raw.location,
        city=city,
        province_state=province_state,
        country=country,
        remote_status=remote_status,
        employment_type=employment_type,
        department=raw.department,
        team=raw.team,
        description=description,
        requirements=requirements,
        preferred_qualifications=preferred,
        skills=skills,
        description_hash=hash_value,
        application_url=raw.application_url,
        source_url=raw.source_url,
        source_type=company.ats,
        posted_at=raw.posted_at,
        updated_at=raw.updated_at,
        raw_payload=raw.extra or None,
    )
