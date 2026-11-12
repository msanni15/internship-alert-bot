import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import monitor
import monitor_browser


def linkedin_card(job_id='123', employer='linkedin'):
    return f'''<div class="base-search-card" data-entity-urn="urn:li:jobPosting:{job_id}">
    <h3 class="base-search-card__title">Software Engineer Intern</h3>
    <h4><a href="https://www.linkedin.com/company/{employer}?trk=guest">LinkedIn</a></h4>
    <span class="job-search-card__location">Mountain View, CA</span>
    <time datetime="2026-11-11">Today</time></div>'''


def meta_link(job_id, location='Sunnyvale, CA\n⋅\nRedmond, WA'):
    link = MagicMock()
    link.get_attribute.return_value = f'/profile/job_details/{job_id}'
    link.inner_text.return_value = f'Hardware Engineer Intern\n{location}\nAR/VR\nHardware'
    return link


def meta_page(pages):
    page = MagicMock()
    position = [0]
    page.inner_text.side_effect = lambda _: f'Page {position[0] + 1} of {len(pages)}'
    page.query_selector_all.side_effect = lambda _: pages[position[0]]
    next_button = page.locator.return_value
    next_button.count.return_value = 1
    next_button.get_attribute.return_value = None
    def advance(**kwargs):
        position[0] += 1
    next_button.click.side_effect = advance
    return page


class LinkedInTests(unittest.TestCase):
    def test_parse_identity_employer_and_date(self):
        jobs, count = monitor.parse_linkedin_jobs(linkedin_card(), 'LinkedIn', '1337')
        self.assertEqual(count, 1)
        job = jobs[0]
        self.assertEqual(job['id'], '123')
        self.assertEqual(job['source'], 'custom/linkedin')
        self.assertEqual(job['updated_at'], '2026-11-11')
        self.assertEqual(job['url'], 'https://www.linkedin.com/jobs/view/123/')
        self.assertEqual(job['location'], 'Mountain View, CA')

    def test_empty_page_vs_unexpected_page(self):
        self.assertEqual(monitor.parse_linkedin_jobs('<!DOCTYPE html>\n<!---->', 'LinkedIn', '1337'), ([], 0))
        for html in ['<h1>Sign in</h1>', '<script>redirect()</script>', '<html><body></body></html>',
                     linkedin_card().replace('<h3 class="base-search-card__title">', '<h2>')]:
            with self.subTest(html=html), self.assertRaises(ValueError):
                monitor.parse_linkedin_jobs(html, 'LinkedIn', '1337')

    def test_wrong_employer_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'different company'):
            monitor.parse_linkedin_jobs(linkedin_card(employer='other'), 'LinkedIn', '1337')

    def test_paginate_short_pages_deduplicate_queries(self):
        def response(url):
            args = parse_qs(urlparse(url).query)
            return linkedin_card() if args['start'] == ['0'] else '<!---->'
        with patch.object(monitor, 'get_html', side_effect=response) as fetch:
            jobs = monitor.fetch_linkedin_jobs('LinkedIn', '1337')
        self.assertEqual(len(jobs), 1)
        self.assertEqual(fetch.call_count, 12)
        self.assertEqual({parse_qs(urlparse(c.args[0]).query)['start'][0] for c in fetch.call_args_list}, {'0', '1'})

    def test_repeated_page_and_timeout_are_visible(self):
        with patch.object(monitor, 'get_html', return_value=linkedin_card()):
            with self.assertRaisesRegex(ValueError, 'repeated'):
                monitor.fetch_linkedin_jobs('LinkedIn', '1337')
        with patch.object(monitor.time, 'monotonic', side_effect=[0, 1000]):
            with self.assertRaises(TimeoutError):
                monitor.fetch_linkedin_jobs('LinkedIn', '1337')


class MetaTests(unittest.TestCase):
    def test_all_locations_and_no_tags(self):
        title, location = monitor_browser.parse_meta_card(
            'Hardware Engineer Intern\nSunnyvale, CA\n⋅\nRedmond, WA\n⋅\nSeattle, WA\nAR/VR\nEngineering')
        self.assertEqual(location, 'Sunnyvale, CA | Redmond, WA | Seattle, WA')
        self.assertEqual(title, 'Hardware Engineer Intern')
        with self.assertRaises(ValueError):
            monitor_browser.parse_meta_card('Title only')

    def test_full_pagination_with_stable_existing_ids(self):
        page = meta_page([[meta_link(123)], [meta_link(456)]])
        with patch.object(monitor_browser, 'goto_checked') as goto:
            jobs = monitor_browser.fetch_meta_jobs(page, 'Meta', 'https://www.metacareers.com/jobs/')
        self.assertIn('roles[0]=Internship', goto.call_args.args[1])
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]['id'], 'https://www.metacareers.com/profile/job_details/123')
        self.assertEqual(jobs[0]['source'], 'browser/meta')
        self.assertTrue(jobs[0]['is_internship_meta'])
        self.assertEqual(page.locator.return_value.click.call_count, 1)

    def test_duplicates_do_not_produce_duplicate_jobs(self):
        page = meta_page([[meta_link(123)], [meta_link(123), meta_link(456)]])
        with patch.object(monitor_browser, 'goto_checked'):
            self.assertEqual(len(monitor_browser.fetch_meta_jobs(page, 'Meta', 'https://www.metacareers.com/jobs/')), 2)

    def test_broken_pagination_fails(self):
        for pages in [[[meta_link(123)], [meta_link(123)]], [[meta_link(123)], []]]:
            page = meta_page(pages)
            with patch.object(monitor_browser, 'goto_checked'), self.assertRaises(ValueError):
                monitor_browser.fetch_meta_jobs(page, 'Meta', 'https://www.metacareers.com/jobs/')
        page = meta_page([[meta_link(123)], [meta_link(456)]])
        page.locator.return_value.count.return_value = 0
        with patch.object(monitor_browser, 'goto_checked'), self.assertRaisesRegex(ValueError, 'control'):
            monitor_browser.fetch_meta_jobs(page, 'Meta', 'https://www.metacareers.com/jobs/')

    def test_true_empty_and_unknown_empty(self):
        page = MagicMock()
        page.query_selector_all.return_value = []
        page.inner_text.return_value = '0 Items'
        with patch.object(monitor_browser, 'goto_checked'):
            self.assertEqual(monitor_browser.fetch_meta_jobs(page, 'Meta', ''), [])
            page.inner_text.return_value = 'Loading failed'
            with self.assertRaises(ValueError):
                monitor_browser.fetch_meta_jobs(page, 'Meta', '')

    def test_timeout_is_visible(self):
        page = meta_page([[meta_link(123)]])
        with patch.object(monitor_browser, 'goto_checked'), \
             patch.object(monitor_browser.time, 'monotonic', side_effect=[0, 1000]):
            with self.assertRaises(TimeoutError):
                monitor_browser.fetch_meta_jobs(page, 'Meta', '')
