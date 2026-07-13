import csv
import json
import os
import re
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


COMPANIES_FILE = "companies.csv"
SEEN_JOBS_FILE = "seen_jobs.json"
DAILY_STATE_FILE = "daily_state.json"

TIMEZONE = "America/Los_Angeles"
DAILY_SUMMARY_HOUR = 23
RECENT_JOB_WINDOW_DAYS = 90
MIN_UPDATED_DATE = datetime.now(timezone.utc) - timedelta(days=RECENT_JOB_WINDOW_DAYS)

REQUEST_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0",
}

INTERN_KEYWORDS = [
    "intern",
    "internship",
    "summer",
    "fall",
    "winter",
    "co-op",
    "coop",
    "student",
    "university",
    "early career",
]

ROLE_KEYWORDS = [
    "software",
    "software engineer",
    "software engineering",
    "quant",
    "quantitative",
    "trading",
    "trader",
    "research",
    "machine learning",
    "ml",
    "ai",
    "computer vision",
    "fpga",
    "rtl",
    "asic",
    "embedded",
    "firmware",
    "hardware",
    "systems",
    "c++",
    "java",
    "python",
    "cuda",
    "gpu",
    "robotics",
    "perception",
    "backend",
    "frontend",
    "full stack",
    "infrastructure",
    "platform",
    "security",
    "data",
]

BLOCKED_TITLE_KEYWORDS = [
    "phd",
    "ph.d",
    "master's",
    "masters",
    "mba",
    "new grad",
    "new college grad",
]

BLOCKED_INTERNATIONAL_LOCATION_KEYWORDS = [
    "london",
    "united kingdom",
    "u.k.",
    "uk",
    "europe",
    "emea",
    "singapore",
    "hong kong",
    "india",
    "bangalore",
    "bengaluru",
    "hyderabad",
    "mumbai",
    "delhi",
    "australia",
    "sydney",
    "melbourne",
    "germany",
    "berlin",
    "munich",
    "france",
    "paris",
    "netherlands",
    "amsterdam",
    "ireland",
    "dublin",
    "saint albans",
    "eindhoven",
    "vienna",
    "dubai",
    "riyadh",
    "shenzhen",
    "switzerland",
    "zurich",
    "poland",
    "warsaw",
    "spain",
    "madrid",
    "barcelona",
    "japan",
    "tokyo",
    "china",
    "shanghai",
    "beijing",
    "taiwan",
    "taipei",
    "israel",
    "tel aviv",
    "petah-tikva",
    "brazil",
    "sao paulo",
    "mexico",
    "guadalajara",
    "argentina",
    "cordoba",
    "chile",
    "peru",
    "colombia",
    "bogota",
    "costa rica",
    "italy",
    "milan",
    "rome",
    "serbia",
    "belgrade",
    "malaysia",
    "penang",
    "kuala lumpur",
    "vietnam",
    "philippines",
    "manila",
    "thailand",
    "bangkok",
    "indonesia",
    "jakarta",
    "south korea",
    "korea",
    "seoul",
    "sweden",
    "stockholm",
    "finland",
    "helsinki",
    "norway",
    "oslo",
    "denmark",
    "copenhagen",
    "belgium",
    "brussels",
    "portugal",
    "lisbon",
    "romania",
    "bucharest",
    "czech republic",
    "prague",
    "hungary",
    "budapest",
    "turkey",
    "istanbul",
    "egypt",
    "cairo",
    "south africa",
    "new zealand",
    "russia",
    "moscow",
    "ukraine",
    "kyiv",
]

SEARCH_TIME_BUDGET_SECONDS = 60

# Reused by any ATS fetcher that has to page through a loose full-text search
# (Workday, Amazon, Eightfold) rather than listing every job directly.
SEARCH_TERMS = [
    "intern",
    "internship",
    "co-op",
    "coop",
    "student",
    "summer",
    "fall",
    "winter",
]


def env_bool(name):
    return os.environ.get(name, "").strip().lower() in ["1", "true", "yes", "y"]


TEST_EMAIL_ONLY = env_bool("TEST_EMAIL_ONLY")
TEST_COMPANY = os.environ.get("TEST_COMPANY", "").strip().lower()
DRY_RUN = env_bool("DRY_RUN")
DEBUG_JOBS = env_bool("DEBUG_JOBS")


def has_keyword(text, keyword):
    text = str(text or "").lower()
    keyword = str(keyword or "").lower()
    pattern = rf"(?<![a-zA-Z0-9]){re.escape(keyword)}(?![a-zA-Z0-9])"
    return re.search(pattern, text) is not None


def get_matched_keywords(text, keywords):
    return [keyword for keyword in keywords if has_keyword(text, keyword)]


def get_json(url, params=None, timeout=45, attempts=3):
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as error:
            last_error = error

            if attempt < attempts:
                wait_seconds = attempt * 5
                print(f"GET request failed. Retrying in {wait_seconds} seconds...")
                time.sleep(wait_seconds)

    raise last_error


def get_html(url, timeout=20):
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def post_workday_json(url, json_body=None):
    response = requests.post(
        url,
        json=json_body,
        headers=REQUEST_HEADERS,
        timeout=(5, 8),
    )

    if response.status_code >= 400:
        raise requests.exceptions.HTTPError(
            f"{response.status_code} error for {url}: {response.text[:500]}",
            response=response,
        )

    return response.json()


def fetch_workday_page(api_url, search_term, limit, offset):
    payloads = [
        {
            "appliedFacets": {},
            "limit": limit,
            "offset": offset,
            "searchText": search_term,
            "sortBy": "relevance",
        },
        {
            "appliedFacets": {},
            "limit": limit,
            "offset": offset,
            "searchText": search_term,
        },
        {
            "limit": limit,
            "offset": offset,
            "searchText": search_term,
            "sortBy": "relevance",
        },
        {
            "limit": limit,
            "offset": offset,
            "searchText": search_term,
        },
    ]

    last_error = None

    for payload in payloads:
        try:
            return post_workday_json(api_url, json_body=payload)
        except requests.exceptions.HTTPError as error:
            last_error = error

    raise last_error


def has_blocked_international_location(location):
    if not location:
        return False

    # Multi-location postings are joined with "|" (see fetch_workday_job_locations).
    # Only block if every listed location is international - a US/Canada option
    # anywhere in the list means the role is still open to us.
    segments = location.split("|") if "|" in location else [location]

    return all(
        any(has_keyword(segment, keyword) for keyword in BLOCKED_INTERNATIONAL_LOCATION_KEYWORDS)
        for segment in segments
    )


def parse_relative_days_ago(text):
    text = re.sub(r"\s+", " ", text.strip().lower())
    text = text.removeprefix("posted ")

    if text in ("today", "just posted"):
        return 0

    if text == "yesterday":
        return 1

    match = re.match(r"a\s+(day|month|year)\s+ago", text)

    if match:
        unit = match.group(1)
        return {"day": 1, "month": 30, "year": 365}[unit]

    match = re.match(r"(\d+)(\+?)\s+(days?|months?|years?)\s+ago", text)

    if not match:
        return None

    count = int(match.group(1))
    unit = match.group(3).rstrip("s")
    days = count * {"day": 1, "month": 30, "year": 365}[unit]

    if match.group(2) == "+":
        # "30+ days ago" means "at least 30" (true age unknown and could be much
        # older) - nudge past the boundary rather than let it slip through.
        days += 1

    return days


def parse_job_datetime(value):
    if not value:
        return None

    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    value = str(value).strip()

    if value.isdigit():
        timestamp = int(value)
        timestamp = timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    days_ago = parse_relative_days_ago(value)

    if days_ago is not None:
        return datetime.now(timezone.utc) - timedelta(days=days_ago)

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Date-only strings ("2026-05-29") parse as naive - assume UTC so the
        # comparison against MIN_UPDATED_DATE below doesn't raise TypeError.
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    normalized = re.sub(r"\s+", " ", value.strip())

    for date_format in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(normalized, date_format).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def is_recent_enough(job):
    job_date = parse_job_datetime(job.get("updated_at", ""))

    if job_date is None:
        return True

    return job_date >= MIN_UPDATED_DATE


def load_companies():
    with open(COMPANIES_FILE, mode="r", newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def filter_companies_for_test(companies):
    if not TEST_COMPANY:
        return companies

    filtered_companies = [
        row for row in companies
        if TEST_COMPANY in row.get("company", "").strip().lower()
    ]

    print(f"TEST_COMPANY is on. Checking {len(filtered_companies)} matching company row(s).")

    return filtered_companies


def load_json_file(path, default_value):
    try:
        with open(path, "r", encoding="utf-8") as file:
            content = file.read().strip()

        if not content:
            return default_value

        return json.loads(content)

    except FileNotFoundError:
        return default_value


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def load_seen_jobs():
    data = load_json_file(SEEN_JOBS_FILE, {})

    if isinstance(data, dict):
        return data

    if isinstance(data, list):
        return {job_id: True for job_id in data}

    return {}


def save_seen_jobs(seen_jobs):
    save_json_file(SEEN_JOBS_FILE, seen_jobs)


def load_daily_state():
    data = load_json_file(DAILY_STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_daily_state(daily_state):
    save_json_file(DAILY_STATE_FILE, daily_state)


def today_string():
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")


def current_hour():
    return datetime.now(ZoneInfo(TIMEZONE)).hour


def get_today_state(daily_state):
    today = today_string()

    if today not in daily_state:
        daily_state[today] = {
            "new_jobs_found": 0,
            "daily_summary_sent": False,
        }

    return daily_state[today]


def get_pending_daily_summary_dates(daily_state):
    # Scheduled runs land irregularly, so a day can end without any run
    # landing in its post-DAILY_SUMMARY_HOUR window - checking only "today"
    # meant that day's summary would never fire. Checking every unsent day
    # (not just today) means whichever run happens to be first after a
    # missed day still catches up and sends it.
    today = today_string()
    hour = current_hour()

    pending = []

    for date, state in sorted(daily_state.items()):
        if state.get("daily_summary_sent"):
            continue

        if state.get("new_jobs_found", 0) != 0:
            continue

        day_has_ended = date < today or (date == today and hour >= DAILY_SUMMARY_HOUR)

        if day_has_ended:
            pending.append(date)

    return pending


def make_job(source, company, token, title, job_id, url, location="", updated_at="", **extra):
    job = {
        "source": source,
        "company": company,
        "token": token,
        "title": title or "",
        "id": str(job_id or url or title or ""),
        "updated_at": updated_at or "",
        "url": url or "",
        "location": location or "",
    }

    job.update(extra)
    return job


def is_internship_metadata(job):
    # Some companies (e.g. Jane Street) post internships under plain titles
    # like "Software Engineer" with no title keyword - Greenhouse's own
    # "Employment Type" metadata field is the only place it's marked.
    for field in job.get("metadata") or []:
        if field.get("name") == "Employment Type" and "intern" in str(field.get("value") or "").lower():
            return True
    return False


def fetch_greenhouse_jobs(company, token):
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    data = get_json(url)
    clean_jobs = []

    for job in data.get("jobs", []):
        location_data = job.get("location") or {}

        if isinstance(location_data, dict):
            location = location_data.get("name", "")
        else:
            location = str(location_data)

        clean_jobs.append(
            make_job(
                source="greenhouse",
                company=company,
                token=token,
                title=job.get("title", ""),
                job_id=job.get("id", ""),
                updated_at=job.get("updated_at", ""),
                url=job.get("absolute_url", ""),
                location=location,
                is_internship_meta=is_internship_metadata(job),
            )
        )

    return clean_jobs


def fetch_lever_jobs(company, token):
    base_url = f"https://api.lever.co/v0/postings/{token}"

    clean_jobs = []
    skip = 0
    limit = 100

    while True:
        raw_jobs = get_json(
            base_url,
            params={
                "mode": "json",
                "skip": skip,
                "limit": limit,
            },
        )

        if not raw_jobs:
            break

        for job in raw_jobs:
            categories = job.get("categories") or {}

            if isinstance(categories, dict):
                location = categories.get("location", "")
                team = categories.get("team", "")
                commitment = categories.get("commitment", "")
            else:
                location = ""
                team = ""
                commitment = ""

            clean_jobs.append(
                make_job(
                    source="lever",
                    company=company,
                    token=token,
                    title=job.get("text", ""),
                    job_id=job.get("id", ""),
                    updated_at=job.get("updatedAt") or job.get("createdAt") or "",
                    url=job.get("hostedUrl", ""),
                    location=location,
                    team=team,
                    commitment=commitment,
                )
            )

        if len(raw_jobs) < limit:
            break

        skip += limit

    return clean_jobs


def fetch_ashby_jobs(company, token):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    data = get_json(
        url,
        params={
            "includeCompensation": "true",
        },
    )

    clean_jobs = []

    for job in data.get("jobs", []):
        job_url = job.get("jobUrl", "")
        apply_url = job.get("applyUrl", "")

        clean_jobs.append(
            make_job(
                source="ashby",
                company=company,
                token=token,
                title=job.get("title", ""),
                job_id=job_url or apply_url or f"{company}:{job.get('title', '')}:{job.get('publishedAt', '')}",
                updated_at=job.get("publishedAt", ""),
                url=job_url or apply_url,
                location=job.get("location", ""),
                team=job.get("team", ""),
                department=job.get("department", ""),
                employment_type=job.get("employmentType", ""),
                workplace_type=job.get("workplaceType", ""),
            )
        )

    return clean_jobs


def get_workday_api_url(token):
    parsed_url = urlparse(token)
    host = parsed_url.netloc
    path_parts = [part for part in parsed_url.path.split("/") if part]

    if not host:
        raise ValueError("Invalid Workday URL")

    if "/wday/cxs/" in token:
        return token, host

    if not path_parts:
        raise ValueError("Invalid Workday URL")

    tenant = host.split(".")[0]
    site = path_parts[0]

    return f"https://{host}/wday/cxs/{tenant}/{site}/jobs", host


AMBIGUOUS_WORKDAY_LOCATION_PATTERN = re.compile(r"^\d+\s+locations?$", re.IGNORECASE)


def resolve_workday_locations(api_url, external_path):
    # The search API collapses multi-location postings into a bare "N Locations"
    # placeholder with no names. The per-job detail endpoint has the real list.
    detail_url = api_url.removesuffix("/jobs") + external_path
    data = get_json(detail_url)
    info = data.get("jobPostingInfo", {})
    locations = [info.get("location", "")] + (info.get("additionalLocations") or [])

    return " | ".join(location for location in locations if location)


def fetch_workday_jobs(company, token):
    api_url, host = get_workday_api_url(token)

    clean_jobs = []
    seen_urls = set()
    # Workday tenants reject limit > 20 with a flat HTTP 400.
    limit = 20
    start_time = time.monotonic()

    for search_term in SEARCH_TERMS:
        print(f"{company}: searching Workday for '{search_term}'")
        offset = 0

        while True:
            # Some Workday boards treat terms like "co-op" as an almost-unfiltered
            # match (thousands of hits). Bail on time, not page count, so a
            # pathological term can't hang the whole run.
            if time.monotonic() - start_time > SEARCH_TIME_BUDGET_SECONDS:
                print(f"{company}: Workday time budget exceeded, stopping early")
                return clean_jobs

            data = fetch_workday_page(api_url, search_term, limit, offset)
            raw_jobs = data.get("jobPostings", [])

            if not raw_jobs:
                break

            for job in raw_jobs:
                title = job.get("title", "")
                external_path = job.get("externalPath", "")

                if external_path.startswith("http"):
                    job_url = external_path
                else:
                    job_url = f"https://{host}{external_path}"

                if job_url in seen_urls:
                    continue

                seen_urls.add(job_url)

                location = job.get("locationsText", "")

                if AMBIGUOUS_WORKDAY_LOCATION_PATTERN.match(location.strip()):
                    try:
                        location = resolve_workday_locations(api_url, external_path) or location
                    except requests.exceptions.RequestException:
                        pass

                clean_jobs.append(
                    make_job(
                        source="workday",
                        company=company,
                        token=token,
                        title=title,
                        job_id=external_path or job_url or title,
                        updated_at=job.get("postedOn", ""),
                        url=job_url,
                        location=location,
                    )
                )

            if len(raw_jobs) < limit:
                break

            offset += limit

    return clean_jobs


def fetch_snap_jobs(company, token):
    soup = BeautifulSoup(get_html(token), "html.parser")

    clean_jobs = []
    seen_urls = set()

    for link in soup.find_all("a"):
        title = link.get_text(" ", strip=True)
        href = link.get("href", "")

        if not title or not href:
            continue

        parent = link.find_parent(["tr", "li", "div"])
        row_text = parent.get_text(" ", strip=True) if parent else title
        row_text = " ".join(row_text.split())

        # Skip nav/footer links.
        if "Regular" not in row_text and "Intern" not in row_text:
            continue

        job_url = urljoin(token, href)

        if job_url in seen_urls:
            continue

        seen_urls.add(job_url)
        details = row_text.replace(title, "", 1).strip()

        clean_jobs.append(
            make_job(
                source="custom/snap",
                company=company,
                token=token,
                title=title,
                job_id=job_url,
                url=job_url,
                location=details,
            )
        )

    return clean_jobs


def fetch_pinpoint_jobs(company, token):
    url = token.rstrip("/") + ".json"
    data = get_json(url)

    clean_jobs = []

    for job in data.get("data", []):
        location_data = job.get("location") or {}
        location = location_data.get("name", "") if isinstance(location_data, dict) else str(location_data)

        clean_jobs.append(
            make_job(
                source="custom/pinpoint",
                company=company,
                token=token,
                title=job.get("title", ""),
                job_id=job.get("id", ""),
                updated_at=job.get("published_at") or job.get("created_at") or "",
                url=job.get("url", ""),
                location=location,
            )
        )

    return clean_jobs


RIPPLING_ALGOLIA_URL = "https://6fnax3tbef-dsn.algolia.net/1/indexes/*/queries"
RIPPLING_ALGOLIA_HEADERS = {
    "x-algolia-api-key": "416caa4690f002ff6fe4a2097623640b",
    "x-algolia-application-id": "6FNAX3TBEF",
    "Content-Type": "application/json",
}


def fetch_rippling_jobs(company, token):
    # Rippling's own careers page runs on Rippling's own ATS product, whose
    # search is a public-key Algolia index - no browser needed.
    clean_jobs = []
    seen_ids = set()

    for search_term in SEARCH_TERMS:
        page = 0

        while True:
            response = requests.post(
                RIPPLING_ALGOLIA_URL,
                headers=RIPPLING_ALGOLIA_HEADERS,
                json={
                    "requests": [
                        {
                            "indexName": "careers_en-US_production",
                            "query": search_term,
                            "hitsPerPage": 50,
                            "page": page,
                        }
                    ]
                },
                timeout=20,
            )
            response.raise_for_status()
            result = response.json()["results"][0]
            hits = result.get("hits", [])

            if not hits:
                break

            for hit in hits:
                job_id = hit.get("objectID", "")

                if job_id in seen_ids:
                    continue

                seen_ids.add(job_id)

                locations = hit.get("locations") or []
                location = " | ".join(
                    f"{loc.get('name', '')}, {loc.get('country', '')}".strip(", ")
                    for loc in locations
                ) or ", ".join(hit.get("locationNames") or [])

                clean_jobs.append(
                    make_job(
                        source="custom/rippling",
                        company=company,
                        token=token,
                        title=hit.get("name", ""),
                        job_id=job_id,
                        url=hit.get("url", ""),
                        location=location,
                    )
                )

            if page + 1 >= result.get("nbPages", 1):
                break

            page += 1

    return clean_jobs


def fetch_gresearch_jobs(company, token):
    soup = BeautifulSoup(get_html(token), "html.parser")

    clean_jobs = []

    for link in soup.select("a.c-vacancy-result"):
        title_el = link.select_one(".c-vacancy-result__title")
        location_el = link.select_one(".c-vacancy-result__location")
        job_url = link.get("href", "")

        if not title_el or not job_url:
            continue

        clean_jobs.append(
            make_job(
                source="custom/gresearch",
                company=company,
                token=token,
                title=title_el.get_text(strip=True),
                job_id=job_url,
                url=job_url,
                location=location_el.get_text(strip=True) if location_el else "",
            )
        )

    return clean_jobs


# Uber's job search API sits behind bot-detection that a bare "Mozilla/5.0"
# UA doesn't satisfy - needs a realistic browser UA and a session cookie from
# an actual page load first, or it intermittently 403s.
UBER_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def fetch_uber_jobs(company, token):
    session = requests.Session()
    session.headers.update({"User-Agent": UBER_BROWSER_USER_AGENT})
    session.get(token, timeout=20)

    search_url = urljoin(token, "/api/jobs/search/")
    clean_jobs = []
    seen_ids = set()

    for search_term in SEARCH_TERMS:
        page = 0

        while True:
            response = session.get(
                search_url,
                params={"search": search_term, "locale": "en", "page": page},
                headers={"Accept": "application/json", "Referer": token},
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
            jobs = data.get("jobs", [])

            if not jobs:
                break

            for job in jobs:
                job_id = job.get("Id") or job.get("Reference")

                if job_id in seen_ids:
                    continue

                seen_ids.add(job_id)

                locations = job.get("Locations") or []
                location = " | ".join(
                    f"{loc.get('City', '')}, {loc.get('Country', '')}".strip(", ")
                    for loc in locations
                )
                url_info = (job.get("Urls") or [{}])[0]
                job_url = urljoin(token, url_info.get("Url", ""))

                clean_jobs.append(
                    make_job(
                        source="custom/uber",
                        company=company,
                        token=token,
                        title=job.get("Title", ""),
                        job_id=job_id,
                        updated_at=job.get("DisplayDate", ""),
                        url=job_url,
                        location=location,
                    )
                )

            if page + 1 >= data.get("totalPages", 1):
                break

            page += 1

    return clean_jobs


def fetch_amazon_jobs(company, token):
    url = "https://www.amazon.jobs/en/search.json"
    limit = 100

    clean_jobs = []
    seen_ids = set()
    start_time = time.monotonic()

    for search_term in SEARCH_TERMS:
        for country in ("USA", "CAN"):
            offset = 0

            while True:
                if time.monotonic() - start_time > SEARCH_TIME_BUDGET_SECONDS:
                    print(f"{company}: search time budget exceeded, stopping early")
                    return clean_jobs

                data = get_json(
                    url,
                    params={
                        "base_query": search_term,
                        "result_limit": limit,
                        "offset": offset,
                        "country": country,
                        "sort": "recent",
                    },
                )
                raw_jobs = data.get("jobs", [])

                if not raw_jobs:
                    break

                for job in raw_jobs:
                    job_id = job.get("id", "")

                    if job_id in seen_ids:
                        continue

                    seen_ids.add(job_id)

                    clean_jobs.append(
                        make_job(
                            source="custom/amazon",
                            company=company,
                            token=token,
                            title=job.get("title", ""),
                            job_id=job_id,
                            updated_at=job.get("posted_date", ""),
                            url=urljoin(url, job.get("job_path", "")),
                            location=job.get("normalized_location", ""),
                        )
                    )

                if len(raw_jobs) < limit:
                    break

                offset += limit

    return clean_jobs


def get_eightfold_api_url(token):
    parsed_url = urlparse(token)
    domain = parse_qs(parsed_url.query).get("domain", [""])[0]

    if not parsed_url.netloc or not domain:
        raise ValueError("Invalid Eightfold URL: expected a ?domain=... query param")

    return f"https://{parsed_url.netloc}/api/apply/v2/jobs", domain


def fetch_eightfold_jobs(company, token):
    api_url, domain = get_eightfold_api_url(token)
    # Eightfold caps page size at 10 regardless of the "num" requested.
    limit = 10

    clean_jobs = []
    seen_ids = set()
    start_time = time.monotonic()

    for search_term in SEARCH_TERMS:
        start = 0

        while True:
            if time.monotonic() - start_time > SEARCH_TIME_BUDGET_SECONDS:
                print(f"{company}: search time budget exceeded, stopping early")
                return clean_jobs

            data = get_json(
                api_url,
                params={"domain": domain, "start": start, "num": limit, "query": search_term},
            )
            positions = data.get("positions", [])

            if not positions:
                break

            for position in positions:
                position_id = position.get("id", "")

                if position_id in seen_ids:
                    continue

                seen_ids.add(position_id)

                clean_jobs.append(
                    make_job(
                        source="custom/eightfold",
                        company=company,
                        token=token,
                        title=position.get("name", ""),
                        job_id=position_id,
                        updated_at=position.get("t_update", ""),
                        url=position.get("canonicalPositionUrl", ""),
                        location=position.get("location", ""),
                    )
                )

            if len(positions) < limit:
                break

            start += limit

    return clean_jobs


def fetch_workable_jobs(company, token):
    url = f"https://apply.workable.com/api/v1/widget/accounts/{token}"
    data = get_json(url)

    clean_jobs = []

    for job in data.get("jobs", []):
        locations = job.get("locations") or []
        location = " | ".join(
            f"{loc.get('city', '')}, {loc.get('country', '')}".strip(", ")
            for loc in locations
        )

        clean_jobs.append(
            make_job(
                source="custom/workable",
                company=company,
                token=token,
                title=job.get("title", ""),
                job_id=job.get("shortcode", ""),
                updated_at=job.get("published_on", ""),
                url=job.get("url", ""),
                location=location,
            )
        )

    return clean_jobs


GEM_GRAPHQL_URL = "https://jobs.gem.com/api/public/graphql/batch"
GEM_JOB_BOARD_LIST_QUERY = """query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings {
      id
      extId
      title
      locations {
        name
        city
        isoCountry
      }
    }
  }
}"""


def fetch_gem_jobs(company, token):
    response = requests.post(
        GEM_GRAPHQL_URL,
        json=[{"operationName": "JobBoardList", "variables": {"boardId": token}, "query": GEM_JOB_BOARD_LIST_QUERY}],
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    response.raise_for_status()
    postings = response.json()[0]["data"]["oatsExternalJobPostings"]["jobPostings"]

    clean_jobs = []

    for job in postings:
        locations = job.get("locations") or []
        location = " | ".join(
            f"{loc.get('city', '')}, {loc.get('name', '')}".strip(", ")
            for loc in locations
        )
        ext_id = job.get("extId", "")

        clean_jobs.append(
            make_job(
                source="custom/gem",
                company=company,
                token=token,
                title=job.get("title", ""),
                job_id=ext_id,
                url=f"https://jobs.gem.com/{token}/{ext_id}",
                location=location,
            )
        )

    return clean_jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse_jobs,
    "lever": fetch_lever_jobs,
    "ashby": fetch_ashby_jobs,
    "workday": fetch_workday_jobs,
    "custom/snap": fetch_snap_jobs,
    "custom/pinpoint": fetch_pinpoint_jobs,
    "custom/amazon": fetch_amazon_jobs,
    "custom/eightfold": fetch_eightfold_jobs,
    "custom/rippling": fetch_rippling_jobs,
    "custom/gresearch": fetch_gresearch_jobs,
    "custom/uber": fetch_uber_jobs,
    "custom/workable": fetch_workable_jobs,
    "custom/gem": fetch_gem_jobs,
}


def fetch_jobs_for_company(row):
    company = row.get("company", "").strip()
    ats_type = row.get("ats_type", "").strip().lower()
    token = row.get("ats_token", "").strip()

    fetcher = FETCHERS.get(ats_type)

    if fetcher is None:
        print(f"Skipping {company}: ATS type '{ats_type}' is not supported yet.")
        return []

    if not token:
        print(f"Skipping {company}: missing ATS token or URL.")
        return []

    return fetcher(company, token)


def is_relevant_job(job):
    title = job.get("title", "").lower()
    location = job.get("location", "")

    if any(has_keyword(title, keyword) for keyword in BLOCKED_TITLE_KEYWORDS):
        return False, []

    if has_blocked_international_location(location):
        return False, []

    if not is_recent_enough(job):
        return False, []

    intern_matches = get_matched_keywords(title, INTERN_KEYWORDS)
    role_matches = get_matched_keywords(title, ROLE_KEYWORDS)

    matched_keywords = sorted(set(intern_matches + role_matches))

    if not intern_matches and job.get("is_internship_meta"):
        matched_keywords = sorted(set(matched_keywords + ["employment type: internship"]))

    is_internship = bool(intern_matches) or job.get("is_internship_meta", False)
    is_relevant = is_internship and bool(role_matches)

    return is_relevant, matched_keywords


def make_seen_key(job):
    return f"{job['source']}:{job['company']}:{job['id']}"


def find_new_jobs(companies, seen_jobs):
    new_jobs = []
    errors = []

    stats = {
        "companies_checked": 0,
        "total_jobs_fetched": 0,
        "relevant_jobs_found": 0,
    }

    for row in companies:
        company = row.get("company", "").strip()
        priority = row.get("priority", "").strip().lower()

        try:
            jobs = fetch_jobs_for_company(row)
            stats["companies_checked"] += 1
            stats["total_jobs_fetched"] += len(jobs)

            print(f"{company}: found {len(jobs)} jobs")

        except Exception as error:
            error_message = f"{company}: {error}"
            errors.append(error_message)
            print(f"ERROR: {error_message}")
            continue

        for job in jobs:
            job["priority"] = priority

            if DEBUG_JOBS:
                print(f"JOB: {job['company']} | {job.get('title', '')} | {job.get('location', '')}")

            is_relevant, matched_keywords = is_relevant_job(job)

            if not is_relevant:
                continue

            stats["relevant_jobs_found"] += 1

            seen_key = make_seen_key(job)

            if seen_key in seen_jobs:
                continue

            job["matched_keywords"] = matched_keywords
            job["seen_key"] = seen_key
            new_jobs.append(job)

            if not DRY_RUN:
                seen_jobs[seen_key] = {
                    "company": job["company"],
                    "title": job["title"],
                    "url": job["url"],
                    "updated_at": job["updated_at"],
                }

            print(f"NEW: {job['company']} - {job['title']}")

    priority_order = {
        "high": 0,
        "medium": 1,
        "low": 2,
    }

    new_jobs.sort(
        key=lambda job: (
            priority_order.get(job.get("priority", ""), 99),
            job.get("company", ""),
            job.get("title", ""),
        )
    )

    return new_jobs, errors, stats


def format_email_body(new_jobs, errors):
    lines = [
        f"{len(new_jobs)} new jobs found!",
        "",
    ]

    for job in new_jobs:
        lines.append(job["company"])

        if job.get("priority"):
            lines.append(f"Priority: {job['priority']}")

        lines.append(job["title"])

        if job.get("location"):
            lines.append(f"Location: {job['location']}")

        if job.get("updated_at"):
            lines.append(f"Updated: {job['updated_at']}")

        if job.get("matched_keywords"):
            lines.append(f"Matched: {', '.join(job['matched_keywords'])}")

        lines.append(job["url"])
        lines.append("")

    if errors:
        lines.append("Errors:")
        for error in errors:
            lines.append(f"- {error}")

    return "\n".join(lines)


def format_run_summary(stats, new_jobs, errors):
    lines = [
        "Run summary:",
        f"Companies checked: {stats['companies_checked']}",
        f"Total jobs fetched: {stats['total_jobs_fetched']}",
        f"Relevant internship roles found: {stats['relevant_jobs_found']}",
        f"New roles found: {len(new_jobs)}",
        f"Errors: {len(errors)}",
    ]

    return "\n".join(lines)


def send_email(subject, body):
    sender = os.environ["EMAIL_USER"]
    password = os.environ["EMAIL_PASS"]
    recipient = os.environ["EMAIL_TO"]

    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.send_message(message)


def send_or_print_email(subject, body):
    if DRY_RUN:
        print("")
        print("DRY_RUN is on. Email was not sent.")
        print(f"Subject: {subject}")
        print(body)
        return

    send_email(subject, body)


def main():
    if TEST_EMAIL_ONLY:
        send_email(
            "Test email from internship alert bot",
            "This is a test email. Your email setup is working.",
        )
        print("Test email sent.")
        return

    companies = filter_companies_for_test(load_companies())

    seen_jobs = load_seen_jobs()
    daily_state = load_daily_state()
    today_state = get_today_state(daily_state)

    new_jobs, errors, stats = find_new_jobs(companies, seen_jobs)

    if new_jobs:
        if not DRY_RUN:
            today_state["new_jobs_found"] += len(new_jobs)

        subject = f"{len(new_jobs)} new internship roles found"
        email_body = format_email_body(new_jobs, errors)
        email_body += "\n\n" + format_run_summary(stats, new_jobs, errors)

        print("")
        print(email_body)

        send_or_print_email(subject, email_body)

    else:
        print("")
        print("No new jobs found this run.")

    pending_dates = get_pending_daily_summary_dates(daily_state)

    if pending_dates:
        subject = "No new internship roles today" if len(pending_dates) == 1 else f"No new internship roles ({len(pending_dates)} quiet days)"
        email_body = "No new internship roles were found on:\n\n"
        email_body += "\n".join(f"- {date}" for date in pending_dates)
        email_body += "\n\nYour internship alert bot ran successfully."
        email_body += "\n\n" + format_run_summary(stats, new_jobs, errors)

        if errors:
            email_body += "\n\nErrors:\n"
            for error in errors:
                email_body += f"- {error}\n"

        print("")
        print(email_body)

        send_or_print_email(subject, email_body)

        if not DRY_RUN:
            for date in pending_dates:
                daily_state[date]["daily_summary_sent"] = True

    if DRY_RUN:
        print("DRY_RUN is on. State files were not saved.")
    else:
        save_seen_jobs(seen_jobs)
        save_daily_state(daily_state)


if __name__ == "__main__":
    main()


"""
LOCAL TEST COMMANDS

Test email only:
TEST_EMAIL_ONLY=true python3 monitor.py

Test one company without saving state:
DRY_RUN=true TEST_COMPANY=zoox python3 monitor.py

Show all jobs found for one company without saving:
DRY_RUN=true DEBUG_JOBS=true TEST_COMPANY=snap python3 monitor.py

Test Intel Workday without saving:
DRY_RUN=true DEBUG_JOBS=true TEST_COMPANY=intel python3 monitor.py

Normal full run:
python3 monitor.py

After changes, commit and push:
git add monitor.py companies.csv daily_state.json seen_jobs.json requirements.txt
git commit -m "Clean up monitor script"
git push
"""