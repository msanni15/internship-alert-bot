import json
import os

import requests

from monitor import load_json_file, save_json_file, send_email

USAGE_ALERT_STATE_FILE = "usage_alert_state.json"
WARNING_THRESHOLD_PERCENT = 80

GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "").strip()
GITHUB_BILLING_TOKEN = os.environ.get("GITHUB_BILLING_TOKEN", "").strip()


def get_actions_usage():
    url = f"https://api.github.com/users/{GITHUB_USERNAME}/settings/billing/actions"
    headers = {
        "Authorization": f"Bearer {GITHUB_BILLING_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()


def current_billing_period():
    # GitHub's plan quota resets on the 1st of each calendar month.
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def main():
    if not GITHUB_USERNAME or not GITHUB_BILLING_TOKEN:
        print("GITHUB_USERNAME or GITHUB_BILLING_TOKEN not set, skipping usage check.")
        return

    # This check is a nice-to-have, not core bot functionality - never let a
    # failure here (bad token, API hiccup) stop seen_jobs.json from being
    # committed, which would cause duplicate alerts on the next run.
    try:
        run_usage_check()
    except Exception as error:
        print(f"Usage check failed, skipping: {error}")


def run_usage_check():
    usage = get_actions_usage()
    minutes_used = usage.get("total_minutes_used", 0)
    included_minutes = usage.get("included_minutes", 2000)
    percent_used = (minutes_used / included_minutes * 100) if included_minutes else 0

    print(f"Actions minutes used: {minutes_used}/{included_minutes} ({percent_used:.1f}%)")

    period = current_billing_period()
    alert_state = load_json_file(USAGE_ALERT_STATE_FILE, {})

    if percent_used < WARNING_THRESHOLD_PERCENT:
        # Below threshold again (new billing period) - clear any stale alert flag.
        if alert_state.get("last_alerted_period") and alert_state["last_alerted_period"] != period:
            alert_state = {}
            save_json_file(USAGE_ALERT_STATE_FILE, alert_state)
        return

    if alert_state.get("last_alerted_period") == period:
        print("Already alerted for this billing period, skipping email.")
        return

    breakdown = usage.get("minutes_used_breakdown", {})
    breakdown_lines = "\n".join(f"- {os_name}: {mins} min" for os_name, mins in breakdown.items())

    subject = f"GitHub Actions usage at {percent_used:.0f}% of monthly quota"
    body = (
        f"Your GitHub Actions usage for internship-alert-bot has reached "
        f"{minutes_used}/{included_minutes} minutes ({percent_used:.0f}%) for this billing period.\n\n"
        f"Breakdown:\n{breakdown_lines}\n\n"
        "Once you hit the included quota, GitHub will stop running the scheduled "
        "workflows until next month's reset (you have a $0 spending limit set, so "
        "you will not be charged) - this alert is a heads-up before that happens."
    )

    print(body)
    send_email(subject, body)

    alert_state["last_alerted_period"] = period
    save_json_file(USAGE_ALERT_STATE_FILE, alert_state)


if __name__ == "__main__":
    main()
