import os
import unittest
from unittest.mock import patch
import monitor


class TargetedRunTests(unittest.TestCase):
    def test_exact_case_insensitive_targets(self):
        rows = [{'company': name} for name in ['Old Mission', 'Mistral AI', 'World Labs', 'Other']]
        with patch.dict(os.environ, {'TARGET_COMPANIES': ' old mission, Mistral AI,World Labs, '}), patch.object(monitor, 'TEST_COMPANY', ''):
            self.assertEqual(monitor.filter_companies_for_test(rows), rows[:3])

    def test_unknown_target_fails_before_checking(self):
        with patch.dict(os.environ, {'TARGET_COMPANIES': 'Wrong Name'}):
            with self.assertRaisesRegex(ValueError, 'Unknown target companies'):
                monitor.filter_companies_for_test([{'company': 'Other'}])

    def test_default_keeps_all_companies(self):
        rows = [{'company': 'Other'}]
        with patch.dict(os.environ, {'TARGET_COMPANIES': ''}), patch.object(monitor, 'TEST_COMPANY', ''):
            self.assertEqual(monitor.filter_companies_for_test(rows), rows)

    def test_partial_run_skips_daily_summary(self):
        with patch.dict(os.environ, {'TARGET_COMPANIES': 'Old Mission'}), \
             patch.object(monitor, 'TEST_EMAIL_ONLY', False), \
             patch.object(monitor, 'DRY_RUN', True), \
             patch.object(monitor, 'TEST_COMPANY', ''), \
             patch.object(monitor, 'load_companies', return_value=[{'company': 'Old Mission'}]), \
             patch.object(monitor, 'load_seen_jobs', return_value={}), \
             patch.object(monitor, 'load_daily_state', return_value={}), \
             patch.object(monitor, 'load_company_health', return_value={}), \
             patch.object(monitor, 'find_new_jobs', return_value=([], [], {}, [])), \
             patch.object(monitor, 'update_company_health', return_value=[]), \
             patch.object(monitor, 'get_pending_daily_summary_dates', side_effect=AssertionError('Partial summary')), \
             patch.object(monitor, 'send_email', side_effect=AssertionError('Email send')), \
             patch.object(monitor, 'save_json_file', side_effect=AssertionError('State write')):
            monitor.main()
