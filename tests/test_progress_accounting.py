import sys
import tempfile
import subprocess
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/progress-report/scripts'))
from progress_accounting import audit_commits, group_prs, pr_counts, GitEvidence, file_category, enrich, statistics_markdown


class AccountingTest(unittest.TestCase):
    def test_replayed_patch_merge_and_mixed_files(self):
        commits = [dict(repo='o/r', sha=s, member='dev', timestamp='2026-10-24T10:00:00Z')
                   for s in ('a', 'rebased', 'merge', 'old', 'mixed')]
        evidence = {
            'a': dict(patch_id='same', authored_at='2026-10-24T09:00:00Z', parents=['p'], categories=['business']),
            'rebased': dict(patch_id='same', authored_at='2026-10-24T09:00:00Z', parents=['q'], categories=['business']),
            'merge': dict(patch_id='', authored_at='2026-10-24T09:00:00Z', parents=['p', 'q'], categories=[]),
            'old': dict(patch_id='prior', authored_at='2026-10-22T09:00:00Z', parents=['p'], categories=['docs']),
            'mixed': dict(patch_id='mixed', authored_at='2026-10-24T09:00:00Z', parents=['p'], categories=['tests', 'ci']),
        }
        result = audit_commits(commits, lambda c: evidence[c['sha']], '2026-10-23T13:00:00Z',
                               {('o/r', 'prior'): 'earlier'})
        self.assertEqual([c['audit']['status'] for c in result],
                         ['counted', 'duplicate', 'merge', 'previous_patch', 'counted'])
        self.assertEqual(result[-1]['audit']['categories'], ['tests', 'ci'])

    def test_stack_is_one_group_and_merge_targets_stay_distinct(self):
        def pr(n, base, head, **kw):
            return dict(repo='o/r', title='item', number=n, base=base, head=head, created_at='2026-10-24T01:00:00Z',
                        merged_at=None, closed_at=None, state='open', **kw)
        ps = [pr(1, 'main', 'one'), pr(2, 'one', 'two'), pr(3, 'two', 'three')]
        self.assertEqual(len(group_prs(ps)), 1)
        ps[1].update(merged_at='2026-10-24T02:00:00Z', state='closed')
        counts = pr_counts(ps, '2026-10-23T13:00:00Z', '2026-10-24T13:00:00Z')
        self.assertEqual((counts['opened'], counts['merged_main'], counts['merged_branch'], counts['ongoing']), (3, 0, 1, 2))

    def test_real_git_patch_identity_survives_cherry_pick(self):
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root) / 'r'; repo.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init'); git('config', 'user.name', 'test'); git('config', 'user.email', 'test@example.test')
            (repo / 'app.py').write_text('a = 1\n'); git('add', '.'); git('commit', '-m', 'base'); base = git('rev-parse', 'HEAD')
            (repo / 'app.py').write_text('a = 2\n'); git('commit', '-am', 'change'); first = git('rev-parse', 'HEAD')
            git('checkout', '-b', 'other', base)
            (repo / 'README.md').write_text('context\n'); git('add', '.'); git('commit', '-m', 'context')
            git('cherry-pick', first); second = git('rev-parse', 'HEAD')
            provider = GitEvidence(Path(root) / 'cache', Path(root))
            a = provider(dict(repo='o/r', sha=first)); b = provider(dict(repo='o/r', sha=second))
            self.assertNotEqual(first, second)
            self.assertEqual(a['patch_id'], b['patch_id'])
            self.assertEqual(a['categories'], ['business'])
            git('checkout', '-b', 'whitespace', base)
            (repo / 'app.py').write_text('a =  2\n'); git('commit', '-am', 'different whitespace')
            third = provider(dict(repo='o/r', sha=git('rev-parse', 'HEAD')))
            self.assertNotEqual(a['patch_id'], third['patch_id'])

    def test_historical_author_date_is_not_claimed_as_proven_duplicate(self):
        c = dict(repo='o/r', sha='x', member='dev')
        detail = dict(patch_id='unknown', authored_at='2026-10-20T00:00:00Z', parents=['p'], categories=['tests'])
        result = audit_commits([c], lambda c: detail, '2026-10-23T13:00:00Z')
        self.assertEqual(result[0]['audit']['status'], 'historical')
        self.assertIsNone(result[0]['audit']['equivalent_sha'])

    def test_exclusion_and_duplicate_diagnostics_apply_to_every_member(self):
        commits = [dict(repo=repo, sha=sha, member=member, timestamp='2026-10-24T00:00:00Z')
                   for repo, sha, member in [('example/excluded-repo', 'a', 'one'), ('o/r', 'b', 'one'), ('o/r', 'c', 'two')]]
        raw = dict(since='2026-10-23T13:00:00Z', until='2026-10-24T13:00:00Z',
                   repos=['example/excluded-repo', 'o/r'], commits=commits, prs=[],
                   members={m: dict(display_name=m, aliases=[m]) for m in ['one', 'two']})
        calls = []
        def provider(c):
            calls.append(c['repo'])
            return dict(patch_id='same', authored_at='2026-10-24T00:00:00Z', parents=['p'], categories=['tests'])
        result = enrich(raw, provider, excluded=["example/excluded-repo"])
        self.assertNotIn('example/excluded-repo', calls)
        self.assertEqual(result['stats']['patch_count'], 1)
        self.assertEqual(result['stats']['by_member']['two']['audit_statuses'], {'duplicate': 1})
        self.assertIn('测试', statistics_markdown(result))

    def test_pr_boundary_is_not_counted_twice(self):
        pr = dict(created_at='2026-10-23T13:00:00Z', state='open', draft=True)
        self.assertEqual(pr_counts([pr], '2026-10-23T13:00:00Z', '2026-10-24T13:00:00Z')['opened'], 0)

    def test_category_separates_tests_ci_and_docs(self):
        self.assertEqual([file_category(x) for x in ['src/a.ts', 'test/a.ts', '.github/workflows/test.yml', 'docs/a.md']],
                         ['business', 'tests', 'ci', 'docs'])

if __name__ == '__main__':
    unittest.main()
