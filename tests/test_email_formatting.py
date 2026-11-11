import copy
import unittest
from unittest.mock import MagicMock, patch

import monitor
import monitor_browser


def job(company, priority='High', title='Software Intern'):
    return dict(company=company, priority=priority, title=title,
                location='New York', updated_at='2026-11-11',
                matched_keywords=['software', 'intern'], url='https://example.com/job')


class EmailFormattingTests(unittest.TestCase):
    def test_single_role(self):
        jobs = [job('Apple')]
        self.assertEqual(monitor.format_email_subject(jobs), 'Apple — 1 new internship')
        self.assertEqual(monitor.format_email_subject(jobs, True),
                         'Apple — 1 new internship (browser bot)')

    def test_unique_companies_and_overflow(self):
        jobs = [job(name) for name in 'FEDCBA'] + [job('A', title='Hardware Intern')]
        self.assertEqual(monitor.format_email_subject(jobs),
                         'A, B, C, D, E +1 more — 7 new internships')
        self.assertEqual(monitor.format_email_subject(jobs[1:5] + [job('A')]),
                         'A, B, C, D, E — 5 new internships')
        self.assertEqual(monitor.format_email_subject([job('A'), job('A')]),
                         'A — 2 new internships')

    def test_priorities_and_alphabetical_ties(self):
        jobs = [job('A', 'Low'), job('Z', 'HIGH'), job('B', 'medium'),
                job('Y', ' high '), job('D', ''), job('C', 'Other'),
                dict(company='E', title='Intern')]
        self.assertEqual([name for name, _ in monitor.group_email_jobs(jobs)],
                         ['Y', 'Z', 'B', 'A', 'C', 'D', 'E'])

    def test_body_groups_roles_and_preserves_details(self):
        jobs = [job('Z', title='Software Intern'), job('A', 'low'),
                job('Z', title='Hardware Intern')]
        original = copy.deepcopy(jobs)
        body = monitor.format_email_body(jobs, ['Source failed'])
        self.assertEqual(body.count('\nZ\n'), 1)
        self.assertEqual(body.count('Priority: High'), 1)
        self.assertIn('Z\nPriority: High\n2 new roles', body)
        self.assertLess(body.index('Hardware Intern'), body.index('Software Intern'))
        self.assertLess(body.index('\nZ\n'), body.index('\nA\n'))
        for detail in ['Location: New York', 'Updated: 2026-11-11',
                       'Matched: software, intern', 'https://example.com/job']:
            self.assertEqual(body.count(detail), 3)
        self.assertTrue(body.endswith('Errors:\n- Source failed'))
        self.assertEqual(jobs, original)

    def test_both_main_paths_without_delivery_or_state_writes(self):
        jobs = [job('NVIDIA'), job('Apple', 'Medium')]
        stats = dict(companies_checked=2, total_jobs_fetched=2, relevant_jobs_found=2)
        for module, is_browser in [(monitor, False), (monitor_browser, True)]:
            with self.subTest(browser=is_browser):
                with patch.multiple(module,
                        load_companies=MagicMock(return_value=[]),
                        load_seen_jobs=MagicMock(return_value={}),
                        load_daily_state=MagicMock(return_value={}),
                        get_pending_daily_summary_dates=MagicMock(return_value=[]),
                        save_seen_jobs=MagicMock(side_effect=AssertionError('State write')),
                        save_daily_state=MagicMock(side_effect=AssertionError('State write')),
                        DRY_RUN=True), \
                     patch.object(monitor, 'send_email', side_effect=AssertionError('Email send')), \
                     patch.object(module, 'send_or_print_email') as preview, \
                     patch.object(module, 'find_new_jobs', return_value=(
                         (jobs, [], stats) if is_browser else (jobs, [], stats, []))):
                    if is_browser:
                        with patch.object(module, 'sync_playwright'):
                            module.main()
                    else:
                        with patch.object(module, 'TEST_EMAIL_ONLY', False), \
                             patch.object(module, 'load_company_health', return_value={}), \
                             patch.object(module, 'update_company_health', return_value=[]), \
                             patch.object(module, 'save_company_health', side_effect=AssertionError('State write')):
                            module.main()
                    preview.assert_called_once()
                    subject, body = preview.call_args.args
                    self.assertEqual(subject, monitor.format_email_subject(jobs, is_browser))
                    self.assertIn('Run summary:', body)
                    self.assertIn('NVIDIA\nPriority: High\n1 new role', body)


if __name__ == '__main__':
    unittest.main()
