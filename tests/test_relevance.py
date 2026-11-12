import unittest
import monitor


class ResearchTitleTests(unittest.TestCase):
    def relevant(self, title):
        return monitor.is_relevant_job({'title': title, 'location': 'Mountain View, CA, USA'})[0]

    def test_student_researcher_is_relevant(self):
        self.assertTrue(self.relevant('Student Researcher, BS/MS, Winter-Summer 2027'))
        self.assertTrue(self.relevant('Summer Intern - Researcher'))

    def test_degree_and_season_exclusions_preserved(self):
        self.assertFalse(self.relevant('Student Researcher, PhD, Winter-Summer 2027'))
        self.assertFalse(self.relevant('Student Researcher, BS/MS, Fall 2026'))

    def test_full_time_and_unrelated_roles_still_excluded(self):
        self.assertFalse(self.relevant('Researcher'))
        self.assertFalse(self.relevant('Summer Marketing Intern'))
        self.assertFalse(self.relevant('Summer Researchership Coordinator'))
