import unittest
from unittest.mock import MagicMock, patch
import monitor
import monitor_browser


class CountryTests(unittest.TestCase):
    def test_country_aliases_and_missing_information(self):
        for countries in [['USA'], ['US'], ['United States'], ['CA'], ['Canada'], ['GB', 'Canada']]:
            self.assertEqual(monitor.country_eligibility(countries), 'domestic')
        self.assertEqual(monitor.country_eligibility(['Iceland']), 'foreign')
        for countries in [[], [''], ['GB', '']]:
            self.assertEqual(monitor.country_eligibility(countries), 'unknown')

    def test_secondary_domestic_location_prevents_false_exclusion(self):
        data = {'location': 'London', 'address': {'postalAddress': {'addressCountry': 'United Kingdom'}},
                'secondaryLocations': [{'location': 'Toronto', 'address': {'addressCountry': 'Canada'}}]}
        location, countries = monitor.ashby_location_details(data)
        self.assertEqual(location, 'London | Toronto')
        job = {'title': 'Software Intern', 'location': location, 'location_countries': countries}
        self.assertTrue(monitor.is_relevant_job(job)[0])

    def test_foreign_country_outside_old_city_list_is_excluded(self):
        job = {'title': 'Software Intern', 'location': 'Reykjavik', 'location_countries': ['Iceland']}
        self.assertFalse(monitor.is_relevant_job(job)[0])
        job.update(location='London, Ontario', location_countries=['Canada'])
        self.assertTrue(monitor.is_relevant_job(job)[0])

    def test_ashby_extracts_structured_countries_and_unknown_indicator(self):
        raw = {'title': 'Software Intern', 'location': 'Remote', 'employmentType': 'Intern',
               'jobUrl': 'https://jobs.ashbyhq.com/example/123'}
        with patch.object(monitor, 'get_json', return_value={'jobs': [raw]}):
            jobs = monitor.fetch_ashby_jobs('Example', 'example')
        self.assertEqual(jobs[0]['location_countries'], [''])
        self.assertIn('Location country: not confirmed', monitor.format_email_body(jobs, []))
        self.assertTrue(monitor.is_relevant_job(jobs[0])[0])


class PartialCoverageTests(unittest.TestCase):
    def test_partial_results_preserve_jobs_and_surface_errors_in_both_bots(self):
        row = {'company': 'Example', 'priority': 'High', 'ats_type': 'ashby', 'ats_token': 'example'}
        job = monitor.make_job('ashby', 'Example', 'example', 'Software Intern', '123', 'https://example.com/job')
        partial = monitor.PartialJobResults([job], 'time budget exceeded; coverage incomplete')
        with patch.object(monitor, 'fetch_jobs_for_company', return_value=partial), \
             patch.object(monitor, 'is_url_healthy', return_value=True), patch.object(monitor, 'DRY_RUN', True):
            jobs, errors, stats, results = monitor.find_new_jobs([row], {})
        self.assertEqual(len(jobs), 1)
        self.assertIn('coverage incomplete', errors[0])
        self.assertIn('coverage incomplete', results[0]['error'])
        with patch.object(monitor_browser, 'fetch_jobs_for_company', return_value=partial), patch.object(monitor_browser, 'DRY_RUN', True):
            jobs, errors, stats = monitor_browser.find_new_jobs(MagicMock(), [row], {})
        self.assertEqual(len(jobs), 1)
        self.assertIn('coverage incomplete', errors[0])
        self.assertIsNotNone(stats['company_results'][0]['error'])

    def test_manual_placeholders_are_not_counted_as_checked(self):
        row = {'company': 'Manual', 'ats_type': 'custom/manual'}
        with patch.object(monitor, 'fetch_one_company') as fetch:
            _, _, stats, results = monitor.find_new_jobs([row], {})
        fetch.assert_not_called()
        self.assertEqual(stats['companies_checked'], 0)
        self.assertEqual(stats['manual_sources_skipped'], 1)
        self.assertEqual(results, [])

    def test_repeated_lever_pages_return_partial_instead_of_looping(self):
        raw = [{'id': str(i), 'text': 'Software Intern', 'hostedUrl': f'https://example.com/{i}'} for i in range(100)]
        with patch.object(monitor, 'get_json', return_value=raw) as fetch:
            jobs = monitor.fetch_lever_jobs('Example', 'example')
        self.assertEqual(len(jobs), 100)
        self.assertIn('repeated', jobs.coverage_warning)
        self.assertEqual(fetch.call_count, 2)

    def test_timeout_returns_explicit_partial_results(self):
        cases = [(monitor, 'fetch_amazon_jobs', 'https://example.com'),
                 (monitor, 'fetch_eightfold_jobs', 'https://example.com/careers?domain=example.com'),
                 (monitor, 'fetch_workday_jobs', 'https://example.wd1.myworkdayjobs.com/Careers')]
        for module, function, token in cases:
            with self.subTest(source=function), patch.object(module.time, 'monotonic', side_effect=[0, 1000]):
                jobs = getattr(module, function)('Example', token)
                self.assertIn('coverage incomplete', jobs.coverage_warning)
        with patch.object(monitor_browser, 'goto_checked'), \
             patch.object(monitor_browser.time, 'monotonic', side_effect=[0, 1000]):
            jobs = monitor_browser.fetch_icims_jobs(MagicMock(), 'Example', 'https://example.com')
        self.assertIn('coverage incomplete', jobs.coverage_warning)

    def test_eightfold_continues_past_cross_query_duplicates(self):
        page = MagicMock()
        state = {'query': -1, 'page': 0}
        ids = [['a', 'b'], ['a', 'c']]
        def goto(*args, **kwargs):
            state['query'] += 1
            state['page'] = 0
        def links(selector):
            link = MagicMock()
            link.get_attribute.return_value = '/careers/job/' + ids[state['query']][state['page']]
            link.inner_text.return_value = 'Software Intern\nSeattle, WA'
            return [link]
        page.query_selector_all.side_effect = links
        button = page.query_selector.return_value
        button.get_attribute.side_effect = lambda _: 'true' if state['page'] else None
        button.click.side_effect = lambda: state.update(page=state['page'] + 1)
        with patch.object(monitor_browser, 'goto_checked', side_effect=goto), \
             patch.object(monitor_browser, 'SEARCH_TERMS', ['intern', 'student']):
            jobs = monitor_browser.fetch_eightfold_browser_jobs(page, 'Example', 'https://example.com')
        self.assertEqual({job['id'] for job in jobs}, {
            'https://example.com/careers/job/a', 'https://example.com/careers/job/b', 'https://example.com/careers/job/c'})
