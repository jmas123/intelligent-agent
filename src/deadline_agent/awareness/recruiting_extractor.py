"""Extract company names from recruiting files/emails and infer application status."""

import re
from pathlib import Path

from deadline_agent.config import settings

# Document-type tokens to strip from filenames (case-insensitive)
_DOC_TYPE_TOKENS = {
    "resume", "res", "cv", "cl", "coverletter", "cover", "letter",
    "application", "app", "draft", "final", "v1", "v2", "v3",
}

# Personal email domains to ignore when extracting company from sender
_PERSONAL_DOMAINS = {
    "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
    "aol.com", "protonmail.com", "mail.com", "live.com", "msn.com",
    "ncsu.edu",  # user's university
}

# Third-party recruiting platform domains (company is in subject, not sender)
_PLATFORM_DOMAINS = {
    "greenhouse.io", "lever.co", "ashbyhq.com", "workday.com",
    "myworkdayjobs.com", "icims.com", "smartrecruiters.com",
    "jobs.lever.co", "hire.lever.co", "app.greenhouse.io",
    "teamtailor.com",
}

# Job aggregator / platform domains — emails from these are about jobs at OTHER companies
_AGGREGATOR_DOMAINS = {
    "lensa.com", "jobright.ai", "glassdoor.com", "indeed.com",
    "linkedin.com", "ziprecruiter.com", "monster.com", "dice.com",
    "hired.com", "otta.com", "wellfound.com", "simplyhired.com",
    "careerbuilder.com", "handshake.com", "joinhandshake.com",
    "twitchjobs.com", "peoplelooker.com", "people.com",
    "teamtailor.com", "e.]linkedin.com", "meridianlink.com",
}

# Normalized company names to skip (job boards, not employers)
_AGGREGATOR_NAMES = {
    "linkedin", "glassdoor", "indeed", "jobright", "lensa",
    "ziprecruiter", "monster", "dice", "hired", "otta",
    "wellfound", "simplyhired", "careerbuilder", "handshake",
    "twitchjobs", "people", "teamtailor", "teamtailor mail",
    "greenhouse mail", "greenhouse", "myworkday", "workday",
    "ashby", "ashbyhq", "lever", "icims", "smartrecruiters",
    "the software engineer", "software engineer",
}

# Keywords that confirm an email is actually about a job (not a promo)
_JOB_CONTEXT_SIGNALS = [
    "application", "applied", "resume", "position", "role", "candidate",
    "hiring", "recruiter", "recruiting", "talent", "career",
    "interview", "phone screen", "technical screen", "onsite",
    "coding challenge", "take-home", "assessment",
    "offer letter", "start date", "onboarding", "background check",
    "we regret", "not moving forward", "unfortunately",
    "your application", "your candidacy", "your resume",
]

# Status inference keywords
_STATUS_SIGNALS: dict[str, list[str]] = {
    "applied": [
        "application received", "application submitted", "thank you for applying",
        "we received your application",
    ],
    "response": [
        "next steps", "moved forward", "under review", "shortlisted",
        "we'd like to", "pleased to inform", "like to move forward",
        "application update", "application status",
    ],
    "interview": [
        "interview scheduled", "phone screen", "technical screen", "onsite",
        "on-site", "final round", "superday", "coding challenge",
        "take-home", "assessment", "virtual interview", "video interview",
    ],
    "offer": [
        "offer letter", "offer of employment", "compensation package",
        "onboarding", "background check",
    ],
    "closed": [
        "rejected", "not moving forward", "unfortunately", "we regret",
        "position has been filled", "decided not to", "not selected",
        "other candidates", "will not be moving", "not be proceeding",
        "will not be advancing", "unable to move forward",
        "we have decided to pursue", "move forward with other",
        "not able to offer", "after careful consideration",
        "while we were impressed", "not the right fit",
        "we've decided to move", "we decided to move",
        "pursuing other candidates", "won't be moving forward",
        "after reviewing your qualifications",
    ],
}

STATUS_ORDER = ["applied", "response", "interview", "offer", "closed"]


def extract_company_from_filename(filename: str) -> str | None:
    """Extract company name from a recruiting filename.

    Preferred format: <OwnerName><Company><DocType>.ext
    Example: JudeElMasriGoogleResume.pdf → Google

    Fallback: strips doc-type tokens from any recruiting filename.
    Example: Google_Resume.pdf → Google
    """
    stem = Path(filename).stem  # strip extension

    # Strip copy indicators: "file (1)" → "file", "file (2)" → "file"
    stem = re.sub(r"\s*\(\d+\)$", "", stem)

    owner = settings.owner_name
    remainder: str | None = None

    # Try owner_name prefix first (preferred path)
    if owner and stem.lower().startswith(owner.lower()):
        remainder = stem[len(owner):]

    if remainder:
        # Strip leading/trailing underscores, hyphens, spaces
        remainder = remainder.strip("_- ")

    if not remainder:
        # Fallback: use entire stem, strip doc-type tokens from both ends
        remainder = stem

    # Split on camelCase boundaries and underscores/hyphens
    remainder = remainder.replace("_", " ").replace("-", " ")
    tokens = []
    for part in remainder.split():
        tokens.extend(_split_camel_case(part))

    # Split doc-type abbreviations fused to the end of tokens (e.g. "IXLCL" → "IXL" + "CL")
    expanded: list[str] = []
    for token in tokens:
        split = False
        for dt in _DOC_TYPE_TOKENS:
            if len(token) > len(dt) and token.lower().endswith(dt):
                expanded.append(token[: -len(dt)])
                expanded.append(token[-len(dt) :])
                split = True
                break
        if not split:
            expanded.append(token)
    tokens = expanded

    # Remove trailing doc-type tokens
    while tokens and tokens[-1].lower() in _DOC_TYPE_TOKENS:
        tokens.pop()
    # Remove leading doc-type tokens too
    while tokens and tokens[0].lower() in _DOC_TYPE_TOKENS:
        tokens.pop(0)

    if not tokens:
        return None

    company = " ".join(tokens)
    return _clean_company_name(company)


def _split_camel_case(text: str) -> list[str]:
    """Split camelCase/PascalCase into tokens.

    'JaneStreet' → ['Jane', 'Street']
    'IBM' → ['IBM']
    'NetApp' → ['Net', 'App']
    """
    # Split on boundaries between lowercase→uppercase or uppercase→uppercase+lowercase
    parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    parts = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", parts)
    return [p for p in parts.split() if p]


def extract_company_from_email(sender: str, subject: str, snippet: str = "") -> str | None:
    """Extract company name from email sender domain or subject line.

    Priority: sender domain → subject line patterns.
    """
    # Try sender domain first
    domain = _extract_domain(sender)
    if not domain:
        return None

    # Skip personal email providers and job aggregator platforms
    if domain in _PERSONAL_DOMAINS or domain in _PLATFORM_DOMAINS or domain in _AGGREGATOR_DOMAINS:
        # For aggregators, try to extract the actual company from the subject
        return _extract_company_from_subject(subject, snippet)

    company = _domain_to_company(domain)
    if company:
        company = _clean_company_name(company)
        if company and company.lower() not in _AGGREGATOR_NAMES:
            return company

    # Fallback: parse subject for company name
    result = _extract_company_from_subject(subject, snippet)
    if result and result.lower() not in _AGGREGATOR_NAMES:
        return result
    return None


def _extract_company_from_subject(subject: str, snippet: str = "") -> str | None:
    """Try to extract a company name from email subject/snippet text."""
    text = f"{subject} {snippet}"
    patterns = [
        # "your application was sent to <Company>"
        r"application\s+(?:was\s+)?sent\s+to\s+(.+?)(?:\s*[-–—.|,!]|$)",
        # "You applied to <Title> - <Company>" (Indeed format)
        r"applied to\s+.+?\s*[-–—]\s*([A-Z][A-Za-z\s&.]+?)(?:\s*[!.,]|$)",
        # "<Company> has received your application"
        r"([A-Z][A-Za-z\s&.]+?)\s+has\s+received\s+your\s+application",
        # "Your application to <Title> at <Company>" / "application for <Title> at <Company>"
        r"application\s+(?:to|for)\s+.+?\s+at\s+([A-Z][A-Za-z\s&.]+?)(?:\s*[!.,]|$)",
        # "<Company> Application Update" or "<Company> | Application"
        r"^([A-Z][A-Za-z\s&.]+?)\s*(?:\||[-��—])\s*(?:Application|Update|Thank)",
        # "Update on Your Application for ... at <Company>" (Ashby pattern)
        r"(?:opportunity|role|position)\s+(?:at|with)\s+([A-Z][A-Za-z\s&.]+?)(?:\.|,|$)",
        # "Thank you for applying to <Company>"
        r"(?:applying|applied|application)\s+to\s+([A-Z][A-Za-z\s&.]+?)(?:\s*[!.,]|\s+for\b|$)",
        # "your interest in <Company>"
        r"interest\s+in\s+([A-Z][A-Za-z\s&.]+?)(?:\s*[!.,]|$)",
        # "application at/with/to <Company>"
        r"(?:application|applied|interview)\s+(?:at|with|to|from)\s+([A-Z][A-Za-z\s&]+?)(?:\s*[-–—.|,!]|\s+for\b|$)",
        # generic "at/with <Company>"
        r"(?:at|with|from)\s+([A-Z][A-Za-z\s&]+?)(?:\s*[-–—.|,!]|\s+for\b|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            company = _clean_company_name(m.group(1).strip())
            if company and len(company) > 1 and len(company) < 50:
                if company.lower() not in _AGGREGATOR_NAMES:
                    return company
    return None


_NOISE_WORDS = {
    "this", "that", "time", "us", "me", "you", "your", "the", "a", "an",
    "it", "is", "are", "was", "we", "our", "they", "them", "their",
    "should", "check", "out", "here", "there", "just", "now",
    "team", "for", "in", "on", "to", "and", "or", "of", "with",
}

# Verb prefixes that leak into company names from snippet text
_VERB_PREFIXES = re.compile(
    r"^(?:joining|applying|regarding|your|our|the|about|re)\s+",
    re.IGNORECASE,
)

# Job title fragments that are not company names
_JOB_TITLE_FRAGMENTS = {
    "software engineer", "data scientist", "product manager",
    "frontend engineer", "backend engineer", "full stack",
    "machine learning", "devops engineer", "site reliability",
    "engineering manager", "technical program", "solutions architect",
    "data engineer", "cloud engineer", "security engineer",
    "mobile engineer", "ios engineer", "android engineer",
    "staff engineer", "senior engineer", "principal engineer",
    "swe", "sde", "mle",
}


def _clean_company_name(name: str) -> str | None:
    """Remove greeting artifacts and noise from extracted company names."""
    # Remove trailing greetings: "MeridianLink Hi Jude" → "MeridianLink"
    name = re.sub(r"\s+(?:Hi|Hello|Dear|Hey)\s+\w+$", "", name, flags=re.IGNORECASE)
    # Remove leading greetings
    name = re.sub(r"^(?:Hi|Hello|Dear|Hey)\s+\w+\s*[-–—,]?\s*", "", name, flags=re.IGNORECASE)
    # Remove "you should check out" prefixes
    name = re.sub(r"^(?:you\s+should\s+check\s+out|check\s+out)\s+", "", name, flags=re.IGNORECASE)
    # Strip verb prefixes that leak from snippet text ("joining Borderless" → "Borderless")
    name = _VERB_PREFIXES.sub("", name)
    name = name.strip()

    if not name:
        return None

    # Reject if the name is just common words (not a proper noun)
    words = name.lower().split()
    if all(w in _NOISE_WORDS for w in words):
        return None

    # Reject pure numbers, single characters, or very short names
    if len(name) <= 2 or name.isdigit():
        return None

    # Reject names that are just doc-type tokens that slipped through
    if name.lower() in _DOC_TYPE_TOKENS:
        return None

    # Reject names that look like version/copy suffixes
    if re.match(r"^\(\d+\)$|^\d+$|^[A-Z]$", name):
        return None

    # Reject if the name contains the user's owner_name (leaked from filename/email)
    owner = settings.owner_name
    if owner and owner.lower() in name.lower():
        # Strip the owner name and re-check what remains
        stripped = re.sub(re.escape(owner), "", name, flags=re.IGNORECASE).strip()
        if not stripped or len(stripped) <= 2:
            return None
        name = stripped

    # Reject job title fragments that aren't company names
    if name.lower() in _JOB_TITLE_FRAGMENTS:
        return None

    # Reject if the name is a recruiting keyword (signal, not a company)
    if name.lower() in {s.lower() for s in _JOB_CONTEXT_SIGNALS}:
        return None

    # Reject if every word is a noise word, signal word, or job title fragment
    # e.g. "Application for Software Engineer" → all non-company words
    _title_words = set()
    for frag in _JOB_TITLE_FRAGMENTS:
        _title_words.update(frag.split())
    _all_reject_words = (
        _NOISE_WORDS
        | {s.lower() for s in _JOB_CONTEXT_SIGNALS}
        | _JOB_TITLE_FRAGMENTS
        | _title_words
        | _DOC_TYPE_TOKENS
    )
    if all(w in _all_reject_words for w in words):
        return None

    return name


def extract_company_from_calendar(title: str) -> str | None:
    """Extract company name from a calendar event title.

    Examples: "Google Technical Screen" → "Google"
              "Interview with Jane Street" → "Jane Street"
    """
    interview_keywords = {
        "interview", "phone screen", "technical screen", "onsite",
        "screen", "superday", "coding challenge", "final round",
    }

    title_lower = title.lower()

    # "Interview with <Company>" pattern
    m = re.search(r"(?:interview|screen|call)\s+(?:with|at)\s+(.+)", title, re.IGNORECASE)
    if m:
        return m.group(1).strip().rstrip(".")

    # "<Company> <InterviewKeyword>" pattern
    for kw in interview_keywords:
        if kw in title_lower:
            idx = title_lower.index(kw)
            before = title[:idx].strip()
            if before and len(before) > 1:
                # Remove leading dash/colon
                before = before.rstrip(" -–—:")
                if before:
                    return before

    return None


def infer_status(current_status: str, text: str) -> str:
    """Infer the highest application status from text signals.

    Only advances in the hierarchy. 'closed' can override any status.
    Returns the new status, or current_status if no advancement.
    """
    text_lower = text.lower()
    current_idx = STATUS_ORDER.index(current_status) if current_status in STATUS_ORDER else 0

    best_status = current_status
    best_idx = current_idx

    for status, keywords in _STATUS_SIGNALS.items():
        status_idx = STATUS_ORDER.index(status)
        for keyword in keywords:
            if keyword in text_lower:
                if status == "closed" or status_idx > best_idx:
                    best_status = status
                    best_idx = status_idx
                break

    return best_status


def has_recruiting_signal(text: str) -> bool:
    """Check if text is genuinely about a job application (not a promo)."""
    text_lower = text.lower()
    return any(signal in text_lower for signal in _JOB_CONTEXT_SIGNALS)


def _extract_domain(sender: str) -> str | None:
    """Extract domain from email address or From header."""
    m = re.search(r"[\w.+-]+@([\w.-]+)", sender)
    if m:
        return m.group(1).lower()
    return None


def extract_resume_variant(filename: str) -> str | None:
    """Identify which resume variant a file represents.

    Strips company name and doc-type tokens, leaving the variant identifier.
    e.g. "JudeElMasriGoogleResumeSWE.pdf" → "swe"
         "resume_v2.pdf" → "v2"
         "JudeElMasriGoogleResume.pdf" → None (no variant)
    """
    stem = Path(filename).stem.lower()

    # Must be a resume/cv file
    resume_indicators = {"resume", "res", "cv"}
    if not any(ind in stem for ind in resume_indicators):
        return None

    # Strip owner name
    owner = settings.owner_name
    if owner:
        stem = stem.replace(owner.lower(), "")

    # Remove all doc-type tokens and known fragments
    tokens = re.split(r"[_\-\s]+", stem)
    # Also split camelCase
    expanded: list[str] = []
    for t in tokens:
        expanded.extend(p.lower() for p in _split_camel_case(t))

    # Remove doc-type tokens and company-like tokens
    variant_tokens = [
        t for t in expanded
        if t and t not in _DOC_TYPE_TOKENS and len(t) <= 10
        and not t.isdigit()
        and t not in {"resume", "cv", "cover", "letter", "pdf", "docx"}
    ]

    # Common variant patterns: v1, v2, swe, data, ml, pm, general
    _VARIANT_PATTERNS = {"v1", "v2", "v3", "v4", "swe", "data", "ml", "pm",
                         "infra", "general", "tech", "quant", "finance",
                         "backend", "frontend", "fullstack", "devops"}
    for t in variant_tokens:
        if t in _VARIANT_PATTERNS or re.match(r"v\d+", t):
            return t

    return None


def infer_application_method(signals: list[dict], source: str) -> str:
    """Determine how an application was submitted.

    Returns: "direct", "referral", "aggregator", or "career_fair".
    """
    all_text = " ".join(
        s.get("summary", "") for s in signals
    ).lower()

    if any(w in all_text for w in ("referral", "referred", "recommendation")):
        return "referral"
    if any(w in all_text for w in ("career fair", "info session", "campus event")):
        return "career_fair"

    # Check source signals for aggregator platforms
    for s in signals:
        summary = s.get("summary", "").lower()
        if any(agg in summary for agg in (
            "linkedin", "indeed", "glassdoor", "handshake",
            "wellfound", "ziprecruiter", "lensa", "jobright",
        )):
            return "aggregator"

    if source == "email":
        # Check if any signal mentions a platform
        for s in signals:
            summary = s.get("summary", "").lower()
            if any(plat in summary for plat in (
                "greenhouse", "lever", "workday", "ashby", "icims",
            )):
                return "direct"

    return "direct"


def _domain_to_company(domain: str) -> str | None:
    """Convert a domain to a company name.

    'google.com' → 'Google'
    'jane-street.com' → 'Jane Street'
    """
    # Strip TLD
    parts = domain.split(".")
    if len(parts) < 2:
        return None
    name = parts[-2]  # e.g. 'google' from 'google.com'

    # Convert hyphens to spaces and title-case
    company = name.replace("-", " ").replace("_", " ").title()
    return company if company else None
