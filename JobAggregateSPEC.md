# SPEC: Waterloo Mechanical Engineering Co-op Job Tracker

Build a private, multi-user, local-first web application that continuously
monitors public job postings from engineering companies, detects new and removed
postings quickly, screens them for relevance to a University of Waterloo
Mechanical Engineering student seeking a Spring co-op, and provides a dashboard,
AI relevance analysis, export tools, and tailored PDF resume drafts.

Accuracy, freshness, direct source links, and truthful AI output are the highest
priorities.

## USER PROFILE

The primary user is:

- University: University of Waterloo
- Program: Mechanical Engineering
- Year: 1st year
- Expected graduation: 2031
- Citizenship: Canadian citizen
- Location preference: Ontario and Canada-wide
- U.S. roles: acceptable only if visa sponsorship is explicitly provided
- Co-op availability: Spring term only for now
- Spring term definition: May to August
- Preferred duration: 4-month co-op
- Initial company target: approximately 25 companies

The system must be designed for multiple users. Each user has their own profile,
saved jobs, application status, AI analyses, and generated resumes. Company
sources may be shared across users, but user-specific data must remain private.

## CORE PRODUCT RULES

1. Never bypass login walls, CAPTCHAs, authentication, IP blocks, or access
   controls.
2. Prioritize official/public ATS APIs over HTML scraping.
3. A job must always include a direct link to the original company career page
   or individual job posting.
4. New jobs must appear in the dashboard as soon as possible after ingestion.
5. Removed or closed jobs must be marked removed and excluded from active
   dashboard views, while retaining historical data.
6. Do not silently hide ambiguous mechanical-engineering roles. Classify them as
   possible matches and surface them for review.
7. AI must never invent experience, skills, dates, companies, projects, or
   qualifications.
8. AI-generated resumes must be edited versions of the user's uploaded master
   resume/portfolio, not newly written resumes from scratch.
9. AI analyses must be cached by job content hash and user profile version.
10. The system must continue operating if one company source fails.
11. All configuration must be editable through YAML/JSON and the dashboard.
12. Include comprehensive tests, logging, source-health monitoring, and a
    quarantine queue.

## TECH STACK

Use:

- Python 3.11+
- FastAPI for backend API
- SQLAlchemy 2.0
- SQLite for local development
- PostgreSQL-compatible schema design for future hosted deployment
- Alembic for migrations
- Pydantic v2
- httpx
- feedparser
- BeautifulSoup4 + lxml
- PyYAML
- typer
- Streamlit OR React frontend; choose Streamlit for Phase 1 unless otherwise
  instructed
- pytest
- GitHub Actions
- Discord webhook notifications
- Optional AI provider through an abstraction layer

Use Streamlit for the first working version. Structure the code so the frontend
can later be replaced by React/Next.js without rewriting the backend.

## HIGH-LEVEL ARCHITECTURE

```text
Company source registry
        |
        v
ATS API / RSS / sitemap / JSON-LD / public HTML monitor
        |
        v
Job normalizer
        |
        v
New / changed / removed detector
        |
        v
Rule-based MechE classifier
        |
        v
Term, duration, location, citizenship, sponsorship extractor
        |
        v
AI relevance classifier for ambiguous jobs
        |
        v
SQLite / PostgreSQL
        |
        +--> Dashboard
        +--> Discord/email alerts
        +--> AI export
        +--> Resume generator
```

## DATA FRESHNESS REQUIREMENTS

Implement source polling intervals:

- High-priority companies: every 15 minutes
- Medium-priority companies: every 30-60 minutes
- Low-priority companies: every 6-24 hours

For each source:

- Record `last_checked_at`
- Record `last_successful_check_at`
- Record `last_change_detected_at`
- Record `current_job_count`
- Record `new_jobs_last_run`
- Record `removed_jobs_last_run`
- Record `error_count`
- Record `last_error`

A job is considered:

- `new` if its source job ID has never been seen before.
- `changed` if its description hash or important metadata changes.
- `removed` if it was previously active but no longer appears in the current
  source listing.
- `active` if it currently appears in the source listing.
- `expired` if its application deadline has passed.

Removed jobs must remain searchable in an archive but must not appear in default
active dashboard views.

## COMPANY CONFIGURATION

Create `config/companies.yaml`.

Each company entry must support:

```yaml
companies:
  - id: acme
    name: "Acme Engineering"
    ats: greenhouse
    board_token: acme
    careers_url: https://boards.greenhouse.io/acme
    enabled: true
    priority: high
    check_interval_minutes: 15
    locations:
      - "Ontario"
      - "Canada"
      - "United States"
    notes: "Strong mechanical and manufacturing teams."
```

Supported ATS values:

- greenhouse
- lever
- ashby
- smartrecruiters
- workable
- rss
- sitemap
- json_ld
- html

The user must be able to add, edit, disable, and remove companies from both the
YAML file and the dashboard.

Company removal must not delete historical jobs; it must disable future checks
and mark the source inactive.

## ATS CONNECTORS

Implement public API connectors for:

### Greenhouse

```http
GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
```

### Lever

```http
GET https://api.lever.co/v0/postings/{board_token}
```

### Ashby

```http
GET https://api.ashbyhq.com/posting-api/job-board/{board_token}
```

### SmartRecruiters

```http
GET https://api.smartrecruiters.com/v1/companies/{company_identifier}/postings
```

### Workable

Implement a configurable public widget endpoint pattern, but do not assume all
Workable employers expose public data. If no public endpoint works, mark the
company as requiring RSS, sitemap, JSON-LD, or HTML fallback.

Each connector must:

- Normalize job data into the canonical schema.
- Handle pagination.
- Respect rate limits.
- Retry transient failures using exponential backoff.
- Detect new, changed, and removed jobs.
- Preserve the original application URL.
- Preserve source-specific metadata.
- Save enough raw data to debug parsing failures.

## CANONICAL JOB SCHEMA

Every job must be stored with:

```json
{
  "job_id": "",
  "source_job_id": "",
  "company_id": "",
  "company_name": "",
  "title": "",
  "normalized_title": "",
  "location": "",
  "city": "",
  "province_state": "",
  "country": "",
  "remote_status": "onsite | hybrid | remote | unknown",
  "employment_type": "internship | co-op | new_grad | full_time | unknown",
  "department": "",
  "team": "",
  "description": "",
  "requirements": [],
  "preferred_qualifications": [],
  "skills": [],
  "description_hash": "",
  "application_url": "",
  "source_url": "",
  "source_type": "",
  "posted_at": null,
  "updated_at": null,
  "first_seen_at": "",
  "last_seen_at": "",
  "removed_at": null,
  "status": "active | removed | expired",
  "term_season": "spring | fall | winter | summer | unknown",
  "term_start_date": null,
  "term_end_date": null,
  "duration_months": null,
  "duration_label": "",
  "citizenship_requirement": "",
  "work_authorization_requirement": "",
  "visa_sponsorship": "provided | not_provided | unknown",
  "sponsorship_confidence": 0.0,
  "sponsorship_evidence": "",
  "classification": "strong_match | possible_match | weak_match | not_relevant",
  "fit_score": 0.0,
  "classification_confidence": 0.0,
  "classification_reasons": [],
  "ai_analysis": null
}
```

## TERM AND DURATION EXTRACTION

Implement deterministic extraction first.

### Supported terms

| Term | Date range |
|---|---|
| Spring | May 1 to August 31 |
| Summer | May 1 to August 31, when explicitly called Summer |
| Fall | September 1 to December 31 |
| Winter | January 1 to April 30 |

Extract:

- `term_season`
- `term_start_date`
- `term_end_date`
- `duration_months`
- `duration_label`

Examples to support:

- "4-month Spring 2027 co-op"
- "May-August 2027"
- "12-month internship beginning May 2027"
- "Winter 2027 work term"
- "Fall 2026 co-op"
- "September to December"

If the term cannot be determined, set `term_season` to `unknown` and add the job
to a "Needs term information" filter. Do not guess.

## CITIZENSHIP AND SPONSORSHIP EXTRACTION

Create a dedicated work-authorization classifier.

It must identify:

- Canadian citizen eligibility
- Canadian permanent resident eligibility
- U.S. citizen or permanent resident requirement
- U.S. work authorization requirement
- Visa sponsorship provided
- Visa sponsorship not provided
- No sponsorship available
- Unknown

For each job, produce:

- `citizenship_requirement`
- `work_authorization_requirement`
- `visa_sponsorship`
- `sponsorship_confidence`
- `sponsorship_evidence`

The dashboard must display the exact evidence quote from the posting.

For a Canadian citizen:

- Canadian roles should generally be eligible unless they explicitly require
  permanent residency/citizenship in another country.
- U.S. roles should be classified as eligible only if sponsorship is explicitly
  provided or the role explicitly accepts Canadian citizens/work authorization.
- If a U.S. role says sponsorship is unavailable, classify it as
  `likely_ineligible_sponsorship_required`.
- If information is missing, classify it as `possibly_eligible_unknown`, not
  ineligible.

## MECHANICAL ENGINEERING CLASSIFIER

Use a recall-first, accuracy-focused classifier.

### Strong match signals

Title or description signals:

- Mechanical Engineering Intern
- Mechanical Engineering Co-op
- Mechanical Design Intern
- Mechanical Design Co-op
- Mechanical Engineer Intern
- Hardware Engineering Intern
- Hardware Co-op
- Product Design Intern
- Product Development Intern
- Manufacturing Engineering Intern
- Manufacturing Co-op
- Mechatronics Intern
- Robotics Intern
- Automotive Engineering Intern
- Aerospace Engineering Intern
- Thermal Engineering Intern
- HVAC Engineering Intern
- Test Engineering Intern
- R&D Engineering Intern

### Skill signals

- CAD
- SolidWorks
- CATIA
- NX
- Creo
- Fusion 360
- GD&T
- FEA
- CFD
- ANSYS
- Abaqus
- MATLAB
- Simulink
- DFM
- DFA
- tolerance analysis
- prototyping
- 3D printing
- CNC
- root cause analysis
- test fixtures
- BOM
- PLM
- thermal analysis
- fluid mechanics
- heat transfer
- kinematics
- dynamics

### Adjacent roles

Do not reject these automatically:

- Engineering Intern
- Engineering Co-op
- Design Intern
- Product Intern
- R&D Intern
- Systems Engineering Intern
- Electrical Hardware Intern
- Process Engineering Intern
- Quality Engineering Intern
- Continuous Improvement Intern

Classify them as `possible_match` and provide a reason.

### Negative signals

Automatically classify as `not_relevant` only when both title and description
clearly indicate unrelated fields:

- Sales
- Marketing
- Finance
- Accounting
- Human Resources
- Legal
- Customer Support
- Recruiting
- Pure software roles with no mechanical/hardware/product-design signals

## USER PROFILE

Create a per-user profile with:

```yaml
user:
  name: ""
  email: ""
  university: "University of Waterloo"
  program: "Mechanical Engineering"
  level: "undergraduate"
  year: 1
  expected_graduation_year: 2031
  citizenship:
    - "Canadian"
  work_authorization:
    - "Canada"
  preferred_locations:
    - "Ontario"
    - "Canada"
  acceptable_locations_with_sponsorship:
    - "United States"
  available_terms:
    - "Spring"
  preferred_duration_months: 4
  minimum_duration_months: 4
  maximum_duration_months: 4
  interests:
    - robotics
    - manufacturing
    - automotive
    - aerospace
    - product design
    - thermal systems
  skills:
    - CAD
    - SolidWorks
    - MATLAB
    - Python
    - prototyping
```

The profile must be editable in the dashboard.

Whenever the profile changes, increment a `profile_version` value. Cached AI
analyses must be invalidated or re-run only when the profile version changes.

## SCREENING AND FIT SCORE

Calculate a fit score from 0 to 100.

Suggested weights:

- Mechanical relevance: 30
- Co-op/internship type: 15
- Spring term match: 15
- Duration match: 10
- Location eligibility: 10
- Citizenship/sponsorship eligibility: 10
- Skill/interest alignment: 10

Hard filters:

- Job is removed or expired.
- Term is incompatible with user availability.
- Duration is incompatible.
- Location is incompatible.
- Citizenship/sponsorship requirement is incompatible.

Soft filters:

- Company priority.
- Skill alignment.
- Interest alignment.
- Remote/hybrid preference.
- Posting freshness.

Every result must include:

- Score
- Matched reasons
- Concerns
- Missing information
- Evidence quotes
- Confidence score

## AI RELEVANCE ANALYSIS

Build AI analysis directly into the dashboard.

The AI should receive:

- User profile
- Job title
- Company
- Location
- Term/duration
- Citizenship/sponsorship evidence
- Cleaned job description
- Requirements
- Skills
- Application URL

It must return strict JSON:

```json
{
  "relevance_score": 0.0,
  "recommendation": "apply | maybe | skip",
  "mechanical_relevance": "strong | possible | weak | none",
  "term_assessment": "",
  "duration_assessment": "",
  "location_assessment": "",
  "work_authorization_assessment": "",
  "matched_skills": [],
  "missing_skills": [],
  "concerns": [],
  "evidence": [],
  "explanation": "",
  "confidence": 0.0
}
```

AI rules:

- Analyze only new or changed jobs.
- Cache results by `job_id + description_hash + profile_version`.
- Never send raw HTML.
- Truncate excessively long descriptions.
- Use AI only when rule-based classification is ambiguous or when the user
  requests deeper analysis.
- Allow the user to manually override AI classification.
- Log every AI request, token usage, and cache hit.

## EXPORT FOR EXTERNAL AI

Add a one-click export feature.

The user can select:

- One job
- Multiple jobs
- All jobs matching current filters
- All saved/hearted jobs

Export formats:

- Markdown
- JSON
- CSV

The export must include:

- Company
- Title
- Location
- Term and duration
- Citizenship/sponsorship status
- Posted date
- Application URL
- Fit score
- Full description
- Requirements
- Preferred qualifications
- Skills
- User profile summary

The export must be clean enough to paste into ChatGPT, Gemini, Claude, or
another AI.

## RESUME GENERATOR

Implement a resume personalization module.

### Master resume upload

Each user can upload:

- PDF
- DOCX
- Markdown
- Plain text

Extract and store structured sections:

- Contact information
- Education
- Experience
- Projects
- Skills
- Certifications
- Leadership
- Awards
- Links

The original uploaded file must always be preserved.

### Tailored resume generation

For each saved/hearted job, generate a tailored PDF resume draft.

Rules:

- Use only facts present in the user's master resume.
- Reorder sections to emphasize relevant experience.
- Adjust bullet emphasis to match the job description.
- Incorporate only keywords the user truthfully has evidence for.
- Preserve truthful dates, employers, schools, tools, and accomplishments.
- Never fabricate experience, metrics, tools, or qualifications.
- Include a change log explaining what was emphasized or reordered.
- Include a missing-keywords report for skills the user may want to develop or
  add if they genuinely have evidence.

Output:

- ATS-friendly PDF
- Markdown source
- JSON structured resume

Use `pdfkit`/WeasyPrint or LibreOffice for PDF conversion. Choose the most
reliable option available in the environment.

## DASHBOARD

Build a private dashboard with authentication.

### User accounts

Implement:

- Email/password login
- Optional Google OAuth later
- Per-user profiles
- Per-user saved jobs
- Per-user application tracking
- Per-user AI analyses
- Per-user generated resumes
- Shared company/source registry
- Admin ability to manage shared companies

### Pages

1. **Relevant For Me**
   - Default landing page
   - Shows strong and possible matches
   - Filters by term, duration, location, sponsorship, freshness, score
   - New-job badges
   - Save/heart button
   - AI analysis button
   - Apply link

2. **All Internships**
   - Complete active job database
   - Advanced filters
   - Removed jobs archive
   - Source information
   - Direct application links

3. **Saved / Application Pipeline**
   - Hearted jobs
   - Status: saved, researching, applying, submitted, interviewing, offer,
     rejected, closed
   - Notes
   - Deadline reminders
   - Resume drafts

4. **AI Analysis**
   - Select jobs
   - Run built-in AI analysis
   - View cached results
   - Export selected jobs for external AI

5. **Resume Studio**
   - Upload master resume
   - View extracted resume sections
   - Generate tailored resume per saved job
   - Download PDF, Markdown, and JSON
   - View change log and missing-keyword report

6. **Companies**
   - Add company by careers URL
   - Edit company settings
   - Enable/disable company
   - Remove company
   - View source health
   - Set priority and polling interval

7. **Source Health**
   - Last successful check
   - New/changed/removed job counts
   - Errors
   - API status
   - Quarantined records

### Filters

Implement filters for:

- Posted within last hour
- Posted within last 6 hours
- Posted within last 24 hours
- Posted within last 3 days
- Posted within last week
- Posted within last month
- Custom date range
- Company
- Location
- Country
- Remote/hybrid/onsite
- Employment type
- Term: Spring, Fall, Winter, Summer, unknown
- Duration: 4 months, 8 months, 12 months, custom
- Citizenship/sponsorship status
- Classification
- Fit score range
- Source type
- Company priority
- Application status
- Saved status

## NOTIFICATIONS

Use Discord webhooks.

Send immediate notifications for:

- New strong match
- New possible match from a high-priority company
- Job matching Spring term and 4-month duration
- Job matching preferred location
- Job with sponsorship explicitly provided, if U.S.-based
- Source failure
- Job removed after being saved

Notification rules:

- No duplicate notifications.
- Store notification history.
- Respect user notification preferences.
- Include direct application URL.
- Include company, title, location, term, duration, fit score, and reason.

## ADD-COMPANY ASSISTANT

Implement:

```bash
python -m jobtracker add-company https://boards.greenhouse.io/acme
```

The command must:

1. Detect ATS from URL/domain.
2. Extract board token/company identifier.
3. Test the public endpoint.
4. Save the company.
5. Run an initial baseline import.
6. Mark all existing jobs as already seen.
7. Ensure existing jobs do not trigger false new-job notifications.

Also provide dashboard-based company addition.

## CLI COMMANDS

Implement:

```bash
python -m jobtracker init-db
python -m jobtracker add-company <careers_url>
python -m jobtracker list-companies
python -m jobtracker enable-company <company_id>
python -m jobtracker disable-company <company_id>
python -m jobtracker remove-company <company_id>
python -m jobtracker run
python -m jobtracker run --company <company_id>
python -m jobtracker screen
python -m jobtracker analyze --job <job_id>
python -m jobtracker notify
python -m jobtracker digest
python -m jobtracker export --format markdown --output output/jobs.md
python -m jobtracker dashboard
python -m jobtracker health
python -m jobtracker test-source <company_id>
```

## ACCURACY AND VALIDATION

Implement validation checks:

- Every active job must have a valid application URL.
- Every job must have a source URL.
- Every job must have a source job ID where available.
- Every removed job must retain its historical record.
- Every AI classification must include evidence or an explanation.
- Every sponsorship conclusion must include evidence.
- Every term conclusion must include evidence or be marked unknown.
- Every resume generation must verify that generated claims exist in the master
  resume.

Add a built-in "Accuracy Report" showing:

- Jobs with missing application URL
- Jobs with unknown term
- Jobs with unknown sponsorship
- Jobs with low-confidence AI classification
- Jobs whose source has not been checked recently
- Jobs marked removed but still appearing in source data
- Jobs marked active but no longer appearing in source data

## TESTS

Use local fixture files. No live network requests in unit tests.

Test:

- Each ATS connector
- Job normalization
- New-job detection
- Changed-job detection
- Removed-job detection
- Description hashing
- Term extraction
- Duration extraction
- Season mapping
- Citizenship/sponsorship classification
- Mechanical relevance classifier
- Fit score calculation
- Notification deduplication
- Resume fact preservation
- AI cache invalidation
- Source failure handling

## IMPLEMENTATION PHASES

Implement in this exact order. Stop after each phase for user review.

### Phase 0: Project scaffold

- Repository structure
- Dependencies
- Environment configuration
- Database migrations
- Logging
- Testing setup
- README

### Phase 1: Core ingestion and dashboard

- Company registry
- Greenhouse connector
- Lever connector
- Ashby connector
- SmartRecruiters connector
- Job normalization
- New/changed/removed detection
- Mechanical relevance rules
- Term/duration extraction
- Citizenship/sponsorship extraction
- SQLite database
- Basic Streamlit dashboard
- Direct application links
- CLI
- Tests

### Phase 2: User accounts and personalization

- Authentication
- User profiles
- Saved jobs
- Application pipeline
- Per-user filters
- Notification preferences
- Multi-user permissions

### Phase 3: AI relevance engine

- AI provider abstraction
- Prompt templates
- Strict JSON output
- Caching
- Confidence scoring
- Dashboard AI analysis page
- Export for external AI

### Phase 4: Resume studio

- Master resume upload
- Resume parsing
- Tailored resume generation
- PDF export
- Change log
- Missing-keyword report

### Phase 5: Production deployment

- GitHub Actions
- Hosted database option
- Secrets management
- Background scheduler
- Discord notifications
- Backup and restore
- Private sharing / multi-user deployment

## FIRST IMPLEMENTATION TASK

Implement Phase 0 and Phase 1 only.

Deliver:

1. A runnable local application.
2. A SQLite database.
3. Company configuration.
4. Public ATS connectors.
5. New/changed/removed job detection.
6. Mechanical relevance screening.
7. Term/duration extraction.
8. Citizenship/sponsorship extraction.
9. Basic dashboard.
10. CLI.
11. Tests.
12. README.

Do not implement AI screening, resume generation, authentication, or deployment
until Phase 1 is reviewed.
