import datetime
import os

import requests

from monitor import load_json_file, save_json_file, send_email

USAGE_ALERT_STATE_FILE = "usage_alert_state.json"
WARNING_THRESHOLD_PERCENT = 80
# Not returned by the new usage-report endpoint (it's itemized billing data,
# not a quota check) - GitHub Free's included Actions minutes is a plan fact.
INCLUDED_ACTIONS_MINUTES = 2000

GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "").strip()
GITHUB_BILLING_TOKEN = os.environ.get("GITHUB_BILLING_TOKEN", "").strip()


def get_billing_usage_items():
    # The old /users/{username}/settings/billing/actions endpoint was
    # retired (410 Gone) when GitHub moved to the new billing platform.
    url = f"https://api.github.com/users/{GITHUB_USERNAME}/settings/billing/usage"
    headers = {
        "Authorization": f"Bearer {GITHUB_BILLING_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json().get("usageItems", [])


def sum_actions_minutes(usage_items):
    return sum(
        item.get("quantity", 0)
        for item in usage_items
        if "actions" in item.get("product", "").lower()
        and "minute" in item.get("unitType", "").lower()
    )


def current_billing_period():
    # GitHub's plan quota resets on the 1st of each calendar month.
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
    usage_items = get_billing_usage_items()
    minutes_used = sum_actions_minutes(usage_items)
    percent_used = minutes_used / INCLUDED_ACTIONS_MINUTES * 100

    print(f"Actions minutes used: {minutes_used}/{INCLUDED_ACTIONS_MINUTES} ({percent_used:.1f}%)")

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

    subject = f"GitHub Actions usage at {percent_used:.0f}% of monthly quota"
    body = (
        f"Your GitHub Actions usage for internship-alert-bot has reached "
        f"{minutes_used}/{INCLUDED_ACTIONS_MINUTES} minutes ({percent_used:.0f}%) for this billing period.\n\n"
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
