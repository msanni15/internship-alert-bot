from contextlib import ExitStack
import unittest
from unittest.mock import MagicMock, patch
import monitor
import monitor_browser


class SourceHealthTests(unittest.TestCase):
    def test_never_worked_source_alerts_after_three_errors_once(self):
        state = {}
        error = [{'company': 'Broken', 'job_count': 0, 'error': 'HTTP 404'}]
        with patch.object(monitor, 'today_string', return_value='2026-11-12'):
            self.assertEqual(monitor.update_company_health(state, error), [])
            self.assertEqual(monitor.update_company_health(state, error), [])
            alert = monitor.update_company_health(state, error)
            self.assertEqual(alert[0]['reason'], 'errors')
            self.assertEqual(monitor.update_company_health(state, error), [])
            self.assertIn('3 consecutive checks', monitor.format_company_health_email(alert))
            monitor.update_company_health(state, [{'company': 'Broken', 'job_count': 0, 'error': None}])
            self.assertEqual(state['Broken']['consecutive_errors'], 0)
            self.assertFalse(state['Broken']['error_alerted'])
            self.assertIsNone(state['Broken']['today_last_error'])
            for _ in range(2):
                self.assertEqual(monitor.update_company_health(state, error), [])
            self.assertEqual(len(monitor.update_company_health(state, error)), 1)

    def test_never_had_jobs_is_not_an_error(self):
        state = {}
        for day in ['2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13']:
            with patch.object(monitor, 'today_string', return_value=day):
                self.assertEqual(monitor.update_company_health(state, [{'company': 'Quiet', 'job_count': 0, 'error': None}]), [])

    def test_browser_records_failed_and_successful_sources(self):
        rows = [{'company': 'Good'}, {'company': 'Bad'}]
        with patch.object(monitor_browser, 'fetch_jobs_for_company', side_effect=[[], ValueError('layout changed')]):
            _, errors, stats = monitor_browser.find_new_jobs(MagicMock(), rows, {})
        self.assertEqual(len(errors), 1)
        self.assertEqual(stats['company_results'][0], {'company': 'Good', 'job_count': 0, 'error': None})
        self.assertIn('layout changed', stats['company_results'][1]['error'])


class DeliveryCheckpointTests(unittest.TestCase):
    def run_scenario(self, module, first_fails=False):
        calls = []
        jobs = [{'company': 'Example', 'title': 'Software Intern', 'url': 'https://example.com/job', 'priority': 'high'}]
        stats = {'companies_checked': 1, 'total_jobs_fetched': 1, 'relevant_jobs_found': 1}
        daily = {'2026-11-10': {'new_jobs_found': 0, 'daily_summary_sent': False}}
        def send(subject, body):
            calls.append(('email', subject, body))
            if first_fails or len([c for c in calls if c[0] == 'email']) > 1:
                raise RuntimeError('Delivery failed')
        with ExitStack() as stack:
            for name, value in [('load_companies', []), ('load_seen_jobs', {}), ('load_daily_state', daily),
                                ('get_pending_daily_summary_dates', ['2026-11-10'])]:
                stack.enter_context(patch.object(module, name, return_value=value))
            stack.enter_context(patch.object(module, 'DRY_RUN', False))
            stack.enter_context(patch.object(module, 'send_or_print_email', side_effect=send))
            for name in ['save_seen_jobs', 'save_daily_state']:
                stack.enter_context(patch.object(module, name, side_effect=lambda *args, name=name: calls.append((name,))))
            stack.enter_context(patch.object(module, 'update_company_health', return_value=[]))
            stack.enter_context(patch.object(monitor, 'send_email', side_effect=AssertionError('Real email blocked')))
            if module is monitor:
                stack.enter_context(patch.object(module, 'TEST_EMAIL_ONLY', False))
                stack.enter_context(patch.object(module, 'load_company_health', return_value={}))
                stack.enter_context(patch.object(module, 'save_company_health', side_effect=AssertionError('State write blocked')))
                result = (jobs, ['Some source failed'], stats, [])
            else:
                stack.enter_context(patch.object(module, 'load_json_file', return_value={}))
                stack.enter_context(patch.object(module, 'save_json_file', side_effect=AssertionError('State write blocked')))
                stack.enter_context(patch.object(module, 'sync_playwright'))
                result = (jobs, ['Some source failed'], stats)
            stack.enter_context(patch.object(module, 'find_new_jobs', return_value=result))
            with self.assertRaisesRegex(RuntimeError, 'Delivery failed'):
                module.main()
        return calls

    def test_delivered_jobs_checkpoint_before_later_failure(self):
        for module in [monitor, monitor_browser]:
            with self.subTest(module=module.__name__):
                calls = self.run_scenario(module)
                self.assertEqual([c[0] for c in calls], ['email', 'save_seen_jobs', 'save_daily_state', 'email'])
                self.assertIn('coverage was incomplete', calls[-1][2])
                self.assertNotIn('ran successfully', calls[-1][2])

    def test_unsent_jobs_are_not_checkpointed(self):
        for module in [monitor, monitor_browser]:
            with self.subTest(module=module.__name__):
                self.assertEqual([c[0] for c in self.run_scenario(module, first_fails=True)], ['email'])
