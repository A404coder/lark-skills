import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/progress-report/scripts'))
import run_daily_report as report
from progress_accounting import enrich, narrative_context, render_evidence


class ReportRegressionTest(unittest.TestCase):
    def test_legacy_archive_resume_through_process_keeps_message_and_raw(self):
        raw = {'since': '2026-01-01T13:00:00Z', 'until': '2026-01-02T13:00:00Z',
               'stats': {'active_member_count': 1, 'commit_count': 2, 'active_pr_count': 1,
                         'by_member': {'dev': {'display_name': 'Dev', 'commit_count': 2, 'active_pr_count': 1}}}}
        body = '过去 24 小时团队 1 人活跃，共 2 次提交、1 个活跃 PR\n\n**Dev**（2 提交 / 1 PR）\n进展。'
        mid = 'om_x' + '1' * 31
        state = {'since': raw['since'], 'until': raw['until'], 'date': '2026-01-02',
                 'marker': 'legacy', 'messages': {'ou_test': {'ok': True, 'data': {'message_id': mid}}}}
        cfg = {'delivery': {'targets': [{'type': 'user', 'id': 'ou_test'}]},
               'lark': {'daily_log_doc': {'token': 'doc', 'url': 'https://example.test/doc'}}}
        inserted = []
        def api(*args, **kwargs):
            if args[1] == '+messages-send':
                self.fail('Must not resend an already delivered legacy message')
            if args[1] == '+messages-mget':
                return {'ok': True, 'data': {'messages': [{'message_id': mid}]}}
            if args[1] == '+update':
                inserted.append(True)
                return {'ok': True, 'data': {'result': 'success'}}
            return {'ok': True, 'data': {'document': {'content': '<p>legacy</p>' if inserted else ''}}}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'raw.json').write_text(json.dumps(raw))
            (folder / 'report.md').write_text('legacy facts')
            (folder / 'body.md').write_text(body)
            with patch.object(report, 'lark', side_effect=api), patch.object(report, 'run'), patch.object(report, 'enrich') as enrichment:
                report.process(folder, cfg, state, deliver_only=True)
                enrichment.assert_not_called()
            self.assertTrue(state['archived'])
            self.assertEqual(len(inserted), 1)
            self.assertEqual(json.loads((folder / 'raw.json').read_text()), raw)
            self.assertEqual((folder / 'body.md').read_text(), body)

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

    def test_legacy_ambiguous_send_is_not_recalculated_before_recovery(self):
        raw = {'since': '2026-01-01T13:00:00Z', 'until': '2026-01-02T13:00:00Z',
               'stats': {'active_member_count': 0, 'commit_count': 0, 'active_pr_count': 0, 'by_member': {}}}
        body = '过去 24 小时团队 0 人活跃，共 0 次提交、0 个活跃 PR'
        state = {'since': raw['since'], 'until': raw['until'], 'delivery_fingerprint': 'original',
                 'send_attempted_at': {'ou_test': 1}}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'raw.json').write_text(json.dumps(raw))
            (folder / 'body.md').write_text(body)
            (folder / 'report.md').write_text('original facts')
            with patch.object(report, 'enrich') as enrichment, patch.object(report, 'deliver') as delivery:
                report.process(folder, {}, state)
                enrichment.assert_not_called()
                delivery.assert_called_once_with(folder, {}, state)
            self.assertEqual(json.loads((folder / 'raw.json').read_text()), raw)
