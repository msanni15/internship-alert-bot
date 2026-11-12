import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import monitor


def page(job_id='123', total=1):
    # Representative elements from Google's public server-rendered search cards.
    return f'''<ul><li><h3>Software Engineering Intern</h3>
    <span class="r0wTof">New York, NY, USA</span>
    <span class="r0wTof">; Toronto, ON, Canada</span>
    <span class="r0wTof">New York, NY, USA</span>
    <a href="jobs/results/{job_id}-software-engineering-intern?q=intern">Learn more</a>
    </li></ul><div>Showing 1 to 1 of {total} rows</div>'''


class GoogleFetcherTests(unittest.TestCase):
    def test_parse_identity_location_and_canonical_link(self):
        jobs, total = monitor.parse_google_jobs(page(), 'Google', monitor.GOOGLE_CAREERS_URL)
        self.assertEqual(total, 1)
        self.assertEqual(jobs[0]['id'], '123')
        self.assertEqual(jobs[0]['source'], 'custom/google')
        self.assertEqual(jobs[0]['location'], 'New York, NY, USA | Toronto, ON, Canada')
        self.assertEqual(jobs[0]['url'], monitor.GOOGLE_CAREERS_URL + '123-software-engineering-intern')
        self.assertEqual(jobs[0]['updated_at'], '')
        self.assertTrue(monitor.is_relevant_job(jobs[0])[0])

    def test_explicit_empty_results_only(self):
        self.assertEqual(monitor.parse_google_jobs('<h2>No results</h2>', 'Google', ''), ([], 0))
        with self.assertRaises(ValueError):
            monitor.parse_google_jobs('<h2>Consent required</h2>', 'Google', '')

    def test_layout_changes_fail_visibly(self):
        for html in [page().replace('r0wTof', 'changed'),
                     page().replace('jobs/results/', 'broken/'),
                     page().replace('Showing 1 to 1 of 1 rows', '')]:
            with self.subTest(html=html), self.assertRaises(ValueError):
                monitor.parse_google_jobs(html, 'Google', '')

    def test_pagination_and_cross_query_deduplication(self):
        def get(url):
            params = parse_qs(urlparse(url).query)
            return page(str(100 + int(params['page'][0])), total=2)
        with patch.object(monitor, 'get_html', side_effect=get) as fetch:
            jobs = monitor.fetch_google_jobs('Google', monitor.GOOGLE_CAREERS_URL)
        self.assertEqual([j['id'] for j in jobs], ['101', '102'])
        self.assertEqual(fetch.call_count, 12)
        self.assertEqual({parse_qs(urlparse(c.args[0]).query)['location'][0] for c in fetch.call_args_list},
                         {'United States', 'Canada'})

    def test_repeated_page_is_error(self):
        with patch.object(monitor, 'get_html', return_value=page(total=2)):
            with self.assertRaisesRegex(ValueError, 'repeated'):
                monitor.fetch_google_jobs('Google', monitor.GOOGLE_CAREERS_URL)

    def test_premature_empty_page_is_error(self):
        with patch.object(monitor, 'get_html', side_effect=[page(total=2), '<h2>No results</h2>']):
            with self.assertRaisesRegex(ValueError, 'before all'):
                monitor.fetch_google_jobs('Google', monitor.GOOGLE_CAREERS_URL)

    def test_timeout_is_reported(self):
        with patch.object(monitor.time, 'monotonic', side_effect=[0, 1000]), patch.object(monitor, 'get_html') as fetch:
            with self.assertRaises(TimeoutError):
                monitor.fetch_google_jobs('Google', monitor.GOOGLE_CAREERS_URL)
            fetch.assert_not_called()
