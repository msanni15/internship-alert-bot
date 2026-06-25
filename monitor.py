import csv
import json
import os
import re
import smtplib
from email.mime.text import MIMEText

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
    "new grad",
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
    "python",
    "cuda",
    "gpu",
    "robotics",
    "perception",
]


def has_keyword(text, keyword):
    pattern = rf"(?<![a-zA-Z0-9]){re.escape(keyword.lower())}(?![a-zA-Z0-9])"
    return re.search(pattern, text.lower()) is not None


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


def fetch_jobs_for_company(row):
    company = row.get("company", "").strip()
    ats_type = row.get("ats_type", "").strip().lower()
    token = row.get("ats_token", "").strip()

    if ats_type == "greenhouse":
        return fetch_greenhouse_jobs(company, token)

    print(f"Skipping {company}: ATS type '{ats_type}' is not supported yet.")
    return []


def is_relevant_job(job):
    title = job["title"].lower()

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

    for row in companies:
        company = row.get("company", "").strip()

        try:
            jobs = fetch_jobs_for_company(row)
            print(f"{company}: found {len(jobs)} jobs")

        except Exception as error:
            error_message = f"{company}: {error}"
            errors.append(error_message)
            print(f"ERROR: {error_message}")
            continue

        for job in jobs:
            is_relevant, matched_keywords = is_relevant_job(job)

            if not is_relevant:
                continue

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

    return new_jobs, errors


def format_email_body(new_jobs, errors):
    lines = []

    lines.append(f"{len(new_jobs)} new jobs found!")
    lines.append("")

    for job in new_jobs:
        lines.append(job["company"])
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

    new_jobs, errors = find_new_jobs(companies, seen_jobs)

    if new_jobs:
        subject = f"{len(new_jobs)} new internship roles found"
        email_body = format_email_body(new_jobs, errors)

        print("")
        print(email_body)

        send_email(subject, email_body)

    else:
        print("")
        print("No new jobs found.")

        if errors:
            print("")
            print("Errors:")
            for error in errors:
                print(f"- {error}")

    save_seen_jobs(seen_jobs)


if __name__ == "__main__":
    main()