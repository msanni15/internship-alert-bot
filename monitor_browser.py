import csv
import re
import time
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from monitor import (
    DAILY_SUMMARY_HOUR,
    DEBUG_JOBS,
    DRY_RUN,
    SEARCH_TERMS,
    TEST_COMPANY,
    current_hour,
    format_email_body,
    format_run_summary,
    get_today_state,
    is_relevant_job,
    load_json_file,
    make_job,
    make_seen_key,
    save_json_file,
    send_or_print_email,
)

# This bot handles career sites that only render their job list via JavaScript
# (iCIMS, Avature, and Arm's search widget). It's kept separate from monitor.py
# on purpose: Playwright is a much heavier, slower, more fragile dependency than
# plain requests, so a broken page render here should never risk the main bot's
# run. Shared filtering/email logic is imported from monitor.py; company list,
# state files, and fetchers are independent.

COMPANIES_FILE = "companies_browser.csv"
SEEN_JOBS_FILE = "seen_jobs_browser.json"
DAILY_STATE_FILE = "daily_state_browser.json"

BROWSER_TIME_BUDGET_SECONDS = 90
PAGE_LOAD_TIMEOUT_MS = 30000
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


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


def load_seen_jobs():
    data = load_json_file(SEEN_JOBS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_seen_jobs(seen_jobs):
    save_json_file(SEEN_JOBS_FILE, seen_jobs)


def load_daily_state():
    data = load_json_file(DAILY_STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_daily_state(daily_state):
    save_json_file(DAILY_STATE_FILE, daily_state)


def extract_icims_location(panel_text):
    lines = [line.strip() for line in panel_text.split("\n") if line.strip()]

    if "Location" not in lines:
        return ""

    end_markers = {"Categories", "Category", "Job Category", "Start Date", "Apply Now", "Apply"}
    location_lines = []

    for line in lines[lines.index("Location") + 1:]:
        if line in end_markers:
            break
        location_lines.append(line)

    return ", ".join(location_lines)


def fetch_icims_jobs(page, company, token):
    # AMD and SIG both run the same iCIMS "UNIFi" Angular Material template:
    # job-title-link anchors inside mat-expansion-panel cards, paginated with
    # a Material paginator. The token URL is expected to already be scoped to
    # an intern/co-op category so no extra keyword search is needed here.
    clean_jobs = []
    seen_urls = set()
    start_time = time.monotonic()

    page.goto(token, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="networkidle")
    page.wait_for_timeout(2500)

    while True:
        if time.monotonic() - start_time > BROWSER_TIME_BUDGET_SECONDS:
            print(f"{company}: browser time budget exceeded, stopping early")
            break

        titles = page.query_selector_all("a.job-title-link")
        new_this_page = False

        for link in titles:
            href = link.get_attribute("href") or ""
            job_url = urljoin(token, href)

            if not href or job_url in seen_urls:
                continue

            seen_urls.add(job_url)
            new_this_page = True

            panel = link.evaluate_handle("el => el.closest('mat-expansion-panel')").as_element()
            panel_text = panel.inner_text() if panel else ""

            clean_jobs.append(
                make_job(
                    source="browser/icims",
                    company=company,
                    token=token,
                    title=link.inner_text().strip(),
                    job_id=job_url,
                    url=job_url,
                    location=extract_icims_location(panel_text),
                )
            )

        next_button = page.query_selector(".mat-paginator-navigation-next")
        is_disabled = next_button is None or next_button.get_attribute("disabled") is not None

        if is_disabled or not new_this_page:
            break

        next_button.click()
        page.wait_for_timeout(2000)

    return clean_jobs


ARM_BOILERPLATE_TEXT = {"save job", "save", "apply", "apply now", "view job"}


def parse_arm_location(card_text):
    lines = [line.strip() for line in card_text.split("\n") if line.strip()]
    lines = [line for line in lines if line.lower() not in ARM_BOILERPLATE_TEXT]

    # Card shape is [title, (optional description), location, category]. The
    # location is always the line right before the trailing category line.
    if len(lines) >= 3:
        return lines[-2]

    if len(lines) == 2:
        return lines[1]

    return ""


def fetch_arm_jobs(page, company, token):
    # Arm's public search is its own aggregator (not the iCIMS template) that
    # requires actually using the on-page search box per keyword.
    clean_jobs = []
    seen_urls = set()
    start_time = time.monotonic()

    page.goto(token, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="networkidle")
    page.wait_for_timeout(1500)

    try:
        page.click("text=Accept And Hide This Message", timeout=3000)
    except Exception:
        pass

    for search_term in SEARCH_TERMS:
        if time.monotonic() - start_time > BROWSER_TIME_BUDGET_SECONDS:
            print(f"{company}: browser time budget exceeded, stopping early")
            break

        keyword_input = page.query_selector("input[name='k']")

        if not keyword_input:
            continue

        keyword_input.fill(search_term)
        keyword_input.press("Enter")
        page.wait_for_timeout(2500)

        while True:
            if time.monotonic() - start_time > BROWSER_TIME_BUDGET_SECONDS:
                break

            links = page.query_selector_all("a[href*='/job/']")
            new_this_page = False

            for link in links:
                href = link.get_attribute("href") or ""
                job_url = urljoin(token, href)

                if not href or job_url in seen_urls:
                    continue

                seen_urls.add(job_url)
                new_this_page = True

                card = link.evaluate_handle(
                    "el => el.closest('li') || el.closest('article') || el.parentElement.parentElement.parentElement"
                ).as_element()
                card_text = card.inner_text() if card else ""

                clean_jobs.append(
                    make_job(
                        source="browser/arm",
                        company=company,
                        token=token,
                        title=link.inner_text().strip(),
                        job_id=job_url,
                        url=job_url,
                        location=parse_arm_location(card_text),
                    )
                )

            next_link = page.query_selector("a.next[data-display-type='Jobs']")

            if not next_link or not next_link.is_visible() or not new_this_page:
                break

            next_link.click()
            page.wait_for_timeout(2000)

        page.goto(token, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="networkidle")
        page.wait_for_timeout(1000)

    return clean_jobs


AVATURE_BOILERPLATE_TEXT = {"apply", "save", "view role"}


def parse_avature_card(card_text):
    lines = [line.strip() for line in card_text.split("\n") if line.strip()]
    content_lines = [line for line in lines if line.lower() not in AVATURE_BOILERPLATE_TEXT]

    title = content_lines[0] if content_lines else ""
    location = content_lines[1] if len(content_lines) > 1 else ""

    return title, location


def fetch_avature_jobs(page, company, token):
    # Two Sigma and Bloomberg are both Avature-hosted with the same URL shape
    # (/careers/JobDetail/<slug>/<id>, ?jobRecordsPerPage=N&jobOffset=M). Their
    # own keyword search is inconsistent about matching "campus"-style titles,
    # so this walks the full open-roles listing and lets is_relevant_job()
    # filter locally, same as every other fetcher in this project.
    clean_jobs = []
    seen_hrefs = set()
    start_time = time.monotonic()
    offset = 0
    page_size = 10

    while True:
        if time.monotonic() - start_time > BROWSER_TIME_BUDGET_SECONDS:
            print(f"{company}: browser time budget exceeded, stopping early")
            break

        url = token if offset == 0 else f"{token}?jobRecordsPerPage={page_size}&jobOffset={offset}"
        page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="networkidle")
        page.wait_for_timeout(2000)

        anchors = page.query_selector_all("a[href*='/JobDetail/']")
        cards_by_href = {}

        for link in anchors:
            href = link.get_attribute("href") or ""
            if href and href not in cards_by_href:
                cards_by_href[href] = link

        new_hrefs = set(cards_by_href) - seen_hrefs

        if not cards_by_href or not new_hrefs:
            break

        for href, link in cards_by_href.items():
            if href in seen_hrefs:
                continue

            seen_hrefs.add(href)

            card = link.evaluate_handle(
                "el => el.closest('li') || el.closest('article') || el.parentElement.parentElement.parentElement"
            ).as_element()
            card_text = card.inner_text() if card else link.inner_text()
            title, location = parse_avature_card(card_text)

            clean_jobs.append(
                make_job(
                    source="browser/avature",
                    company=company,
                    token=token,
                    title=title,
                    job_id=href,
                    url=href,
                    location=location,
                )
            )

        next_link = page.query_selector("a[href*='jobOffset']")

        if next_link:
            match = re.search(r"jobRecordsPerPage=(\d+)", next_link.get_attribute("href") or "")
            if match:
                page_size = int(match.group(1))

        offset += page_size

    return clean_jobs


FETCHERS = {
    "browser/icims": fetch_icims_jobs,
    "browser/arm": fetch_arm_jobs,
    "browser/avature": fetch_avature_jobs,
}


def fetch_jobs_for_company(page, row):
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

    return fetcher(page, company, token)


def find_new_jobs(page, companies, seen_jobs):
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
            jobs = fetch_jobs_for_company(page, row)
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


def main():
    companies = filter_companies_for_test(load_companies())

    seen_jobs = load_seen_jobs()
    daily_state = load_daily_state()
    today_state = get_today_state(daily_state)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        new_jobs, errors, stats = find_new_jobs(page, companies, seen_jobs)
        browser.close()

    if new_jobs:
        if not DRY_RUN:
            today_state["new_jobs_found"] += len(new_jobs)

        subject = f"{len(new_jobs)} new internship roles found (browser bot)"
        email_body = format_email_body(new_jobs, errors)
        email_body += "\n\n" + format_run_summary(stats, new_jobs, errors)

        print("")
        print(email_body)

        send_or_print_email(subject, email_body)

    else:
        print("")
        print("No new jobs found this run.")

        should_send_daily_summary = (
            current_hour() >= DAILY_SUMMARY_HOUR
            and today_state["new_jobs_found"] == 0
            and today_state["daily_summary_sent"] is False
        )

        if should_send_daily_summary:
            subject = "No new internship roles today (browser bot)"
            email_body = (
                "No new internship roles were found today by the browser bot.\n\n"
                "Your internship alert bot ran successfully."
            )
            email_body += "\n\n" + format_run_summary(stats, new_jobs, errors)

            if errors:
                email_body += "\n\nErrors:\n"
                for error in errors:
                    email_body += f"- {error}\n"

            print("")
            print(email_body)

            send_or_print_email(subject, email_body)

            if not DRY_RUN:
                today_state["daily_summary_sent"] = True

    if DRY_RUN:
        print("DRY_RUN is on. State files were not saved.")
    else:
        save_seen_jobs(seen_jobs)
        save_daily_state(daily_state)


if __name__ == "__main__":
    main()
