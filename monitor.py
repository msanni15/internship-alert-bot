import csv
import json
import os
import re
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests


COMPANIES_FILE = "companies.csv"
SEEN_JOBS_FILE = "seen_jobs.json"

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
]

DAILY_STATE_FILE = "daily_state.json"
TIMEZONE = "America/Los_Angeles"
DAILY_SUMMARY_HOUR = 23

MIN_UPDATED_DATE = datetime(2026, 4, 1, tzinfo=timezone.utc)

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
    "brazil",
    "sao paulo",
]

def has_keyword(text, keyword):
    pattern = rf"(?<![a-zA-Z0-9]){re.escape(keyword.lower())}(?![a-zA-Z0-9])"
    return re.search(pattern, text.lower()) is not None


def has_blocked_international_location(location):
    if not location:
        return False

    return any(
        has_keyword(location, keyword)
        for keyword in BLOCKED_INTERNATIONAL_LOCATION_KEYWORDS
    )


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

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_recent_enough(job):
    job_date = parse_job_datetime(job.get("updated_at", ""))

    if job_date is None:
        return True

    return job_date >= MIN_UPDATED_DATE


def get_matched_keywords(text, keywords):
    return [keyword for keyword in keywords if has_keyword(text, keyword)]


def load_companies():
    with open(COMPANIES_FILE, mode="r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        return list(reader)


def load_seen_jobs():
    try:
        with open(SEEN_JOBS_FILE, "r", encoding="utf-8") as file:
            content = file.read().strip()

            if not content:
                return {}

            data = json.loads(content)

            if isinstance(data, dict):
                return data

            if isinstance(data, list):
                return {job_id: True for job_id in data}

            return {}

    except FileNotFoundError:
        return {}


def save_seen_jobs(seen_jobs):
    with open(SEEN_JOBS_FILE, "w", encoding="utf-8") as file:
        json.dump(seen_jobs, file, indent=2)

def today_string():
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")


def current_hour():
    return datetime.now(ZoneInfo(TIMEZONE)).hour


def load_daily_state():
    try:
        with open(DAILY_STATE_FILE, "r", encoding="utf-8") as file:
            content = file.read().strip()

            if not content:
                return {}

            return json.loads(content)

    except FileNotFoundError:
        return {}


def save_daily_state(daily_state):
    with open(DAILY_STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(daily_state, file, indent=2)


def get_today_state(daily_state):
    today = today_string()

    if today not in daily_state:
        daily_state[today] = {
            "new_jobs_found": 0,
            "daily_summary_sent": False,
        }

    return daily_state[today]

def fetch_greenhouse_jobs(company, token):
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"

    response = requests.get(url, timeout=20)
    response.raise_for_status()

    data = response.json()
    raw_jobs = data.get("jobs", [])

    clean_jobs = []

    for job in raw_jobs:
        location_data = job.get("location") or {}

        if isinstance(location_data, dict):
            location = location_data.get("name", "")
        else:
            location = str(location_data)

        clean_jobs.append(
            {
                "source": "greenhouse",
                "company": company,
                "token": token,
                "title": job.get("title", ""),
                "id": str(job.get("id", "")),
                "updated_at": job.get("updated_at", ""),
                "url": job.get("absolute_url", ""),
                "location": location,
            }
        )

    return clean_jobs

def fetch_lever_jobs(company, token):
    base_url = f"https://api.lever.co/v0/postings/{token}"

    all_jobs = []
    skip = 0
    limit = 100

    while True:
        response = requests.get(
            base_url,
            params={
                "mode": "json",
                "skip": skip,
                "limit": limit,
            },
            timeout=20,
        )
        response.raise_for_status()

        raw_jobs = response.json()

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

            all_jobs.append(
                {
                    "source": "lever",
                    "company": company,
                    "token": token,
                    "title": job.get("text", ""),
                    "id": str(job.get("id", "")),
                    "updated_at": job.get("updatedAt") or job.get("createdAt") or "",
                    "url": job.get("hostedUrl", ""),
                    "location": location,
                    "team": team,
                    "commitment": commitment,
                }
            )

        if len(raw_jobs) < limit:
            break

        skip += limit

    return all_jobs

def fetch_ashby_jobs(company, token):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"

    response = requests.get(
        url,
        params={
            "includeCompensation": "true",
        },
        timeout=45,
    )
    response.raise_for_status()

    data = response.json()
    raw_jobs = data.get("jobs", [])

    clean_jobs = []

    for job in raw_jobs:
        job_url = job.get("jobUrl", "")
        apply_url = job.get("applyUrl", "")

        clean_jobs.append(
            {
                "source": "ashby",
                "company": company,
                "token": token,
                "title": job.get("title", ""),
                "id": job_url or apply_url or f"{company}:{job.get('title', '')}:{job.get('publishedAt', '')}",
                "updated_at": job.get("publishedAt", ""),
                "url": job_url or apply_url,
                "location": job.get("location", ""),
                "team": job.get("team", ""),
                "department": job.get("department", ""),
                "employment_type": job.get("employmentType", ""),
                "workplace_type": job.get("workplaceType", ""),
            }
        )

    return clean_jobs

def fetch_jobs_for_company(row):
    company = row.get("company", "").strip()
    ats_type = row.get("ats_type", "").strip().lower()
    token = row.get("ats_token", "").strip()

    if ats_type == "greenhouse":
        return fetch_greenhouse_jobs(company, token)

    if ats_type == "lever":
        return fetch_lever_jobs(company, token)

    if ats_type == "ashby":
        return fetch_ashby_jobs(company, token)

    print(f"Skipping {company}: ATS type '{ats_type}' is not supported yet.")
    return []


def is_relevant_job(job):
    title = job["title"].lower()
    location = job.get("location", "")

    if has_blocked_international_location(location):
        return False, []

    if not is_recent_enough(job):
        return False, []

    intern_matches = get_matched_keywords(title, INTERN_KEYWORDS)
    role_matches = get_matched_keywords(title, ROLE_KEYWORDS)

    is_relevant = bool(intern_matches) and bool(role_matches)

    matched_keywords = sorted(set(intern_matches + role_matches))

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

            is_relevant, matched_keywords = is_relevant_job(job)

            if not is_relevant:
                continue

            stats["relevant_jobs_found"] += 1

            seen_key = make_seen_key(job)

            if seen_key not in seen_jobs:
                job["matched_keywords"] = matched_keywords
                job["seen_key"] = seen_key

                new_jobs.append(job)

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
    lines = []

    lines.append(f"{len(new_jobs)} new jobs found!")
    lines.append("")

    for job in new_jobs:
        lines.append(job["company"])
        if job.get("priority"):
            lines.append(f"Priority: {job['priority']}")
        lines.append(job["title"])

        if job["location"]:
            lines.append(f"Location: {job['location']}")

        if job["updated_at"]:
            lines.append(f"Updated: {job['updated_at']}")

        if job["matched_keywords"]:
            lines.append(f"Matched: {', '.join(job['matched_keywords'])}")

        lines.append(job["url"])
        lines.append("")

    if errors:
        lines.append("Errors:")
        for error in errors:
            lines.append(f"- {error}")

    return "\n".join(lines)


def format_run_summary(stats, new_jobs, errors):
    lines = []

    lines.append("Run summary:")
    lines.append(f"Companies checked: {stats['companies_checked']}")
    lines.append(f"Total jobs fetched: {stats['total_jobs_fetched']}")
    lines.append(f"Relevant internship roles found: {stats['relevant_jobs_found']}")
    lines.append(f"New roles found: {len(new_jobs)}")
    lines.append(f"Errors: {len(errors)}")

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


def main():
    companies = load_companies()
    seen_jobs = load_seen_jobs()
    daily_state = load_daily_state()
    today_state = get_today_state(daily_state)

    new_jobs, errors, stats = find_new_jobs(companies, seen_jobs)
    
    if new_jobs:
        today_state["new_jobs_found"] += len(new_jobs)

        subject = f"{len(new_jobs)} new internship roles found"
        email_body = format_email_body(new_jobs, errors)
        email_body += "\n\n" + format_run_summary(stats, new_jobs, errors)

        print("")
        print(email_body)

        send_email(subject, email_body)

    else:
        print("")
        print("No new jobs found this run.")

        should_send_daily_summary = (
            current_hour() >= DAILY_SUMMARY_HOUR
            and today_state["new_jobs_found"] == 0
            and today_state["daily_summary_sent"] is False
        )

        if should_send_daily_summary:
            subject = "No new internship roles today"
            email_body = "No new internship roles were found today.\n\nYour internship alert bot ran successfully."
            email_body += "\n\n" + format_run_summary(stats, new_jobs, errors)

            if errors:
                email_body += "\n\nErrors:\n"
                for error in errors:
                    email_body += f"- {error}\n"

            print("")
            print(email_body)

            send_email(subject, email_body)

            today_state["daily_summary_sent"] = True

    save_seen_jobs(seen_jobs)
    save_daily_state(daily_state)


if __name__ == "__main__":
    main()