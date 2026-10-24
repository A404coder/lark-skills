import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/progress-report/scripts'))
from progress_accounting import enrich, narrative_context, render_evidence


class ReportRegressionTest(unittest.TestCase):
    def test_commit_only_work_retains_facts_in_model_input_and_evidence(self):
        commit = {'repo': 'example/service', 'sha': 'a' * 40, 'member': 'dev',
                  'title': 'fix: reject expired sessions', 'branch': 'fix/session',
                  'timestamp': '2026-01-02T10:00:00Z', 'url': 'https://github.com/example/service/commit/' + 'a' * 40,
                  'email': 'private@example.test'}
        raw = enrich({'repos': ['example/service'], 'commits': [commit], 'prs': [],
                      'members': {'dev': {'display_name': 'Dev', 'aliases': ['dev']}},
                      'since': '2026-01-01T13:00:00Z', 'until': '2026-01-02T13:00:00Z', 'date_range': 'sample'},
                     lambda c: {'patch_id': 'p', 'parents': ['base'], 'authored_at': c['timestamp'],
                                'categories': ['business'], 'files': ['src/session.ts']})
        context = narrative_context(raw)
        self.assertEqual(len(context['commits']), 1)
        fact = context['commits'][0]
        for key in ('repo', 'sha', 'title', 'branch', 'member', 'url'):
            self.assertEqual(fact[key], commit[key])
        self.assertEqual(fact['audit']['status'], 'counted')
        self.assertEqual(fact['audit']['files'], ['src/session.ts'])
        self.assertNotIn('email', fact)
        self.assertIn(commit['title'], render_evidence(raw))
        self.assertIn(commit['url'], render_evidence(raw))
