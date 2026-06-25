import csv
import requests
import re
import json
import smtplib
import os
from email.mime.text import MIMEText

internkeywords = ["intern", "internship", "summer", "winter", "co-op", "coop", "student", "university", "early career"]
rolekeywords = ["software", "software engineer", "software engineering", "quant", "quantitative", "trading", "trader", "research", "machine learning", "ml", "ai", "computer vision", "fpga", "rtl", "asic", "embedded", "firmware", "hardware", "systems", "c++", "python", "cuda", "gpu", "robotics", "perception"]
new_jobs = []

def has_keyword(text, keyword):
    pattern = rf"(?<![a-zA-Z0-9]){re.escape(keyword.lower())}(?![a-zA-Z0-9])"
    return re.search(pattern, text.lower()) is not None

def send_email(subject, body):
    sender = os.environ["EMAIL_USER"]
    password = os.environ["EMAIL_PASS"]
    recipient = os.environ["EMAIL_TO"]

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.send_message(msg)

with open('seen_jobs.json', 'r') as jfile:
    seen_jobs = json.load(jfile)

with open('companies.csv', mode = 'r', newline = '', encoding='utf-8') as cfile:
    reader = csv.DictReader(cfile)
    rows = list(reader)

for row in rows:
    if row['ats_type'].strip().lower() == 'greenhouse':
        company = row["company"].strip()
        token = row["ats_token"].strip()
            
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        #print(f"{row['company']} -> {url}")
        response = requests.get(url)
        data = response.json()

        jobs = data.get("jobs", [])

        print(f"{company}: found {len(jobs)} jobs")

        for job in jobs:
            clean_job = {
                "c": company,
                "t": job.get("title", ""),
                "id": job.get("id"),
                "up": job.get("updated_at"),
                "url": job.get("absolute_url")
            }

            title = clean_job["t"].lower()

            has_intern_keyword = any(has_keyword(title, word) for word in internkeywords)
            has_role_keyword = any(has_keyword(title, word) for word in rolekeywords)

            if has_intern_keyword and has_role_keyword:
                jobformat = f"greenhouse:{clean_job['c']}:{clean_job['id']}"
                if jobformat not in seen_jobs:
                    seen_jobs[jobformat] = True
                    new_jobs.append(clean_job)
                    print(clean_job["c"], clean_job["t"])

email_body = ""

if len(new_jobs) > 0:
    email_body += f"{len(new_jobs)} new jobs found!!\n\n"
    for job in new_jobs:
        email_body += f"{job['c']}\n{job['t']}\n{job['url']}\n\n"

    print(email_body)

    subject = f"{len(new_jobs)} new internship roles found"
    send_email(subject, email_body)

else:
    print(f"\nNo new jobs found.")

with open("seen_jobs.json", "w") as file:
    json.dump(seen_jobs, file, indent=2)
