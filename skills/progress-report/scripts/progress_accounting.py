"""Content evidence and reporting units, separate from GitHub activity discovery.

A patch is an audit unit, not a feature or a measure of effort. Old authored
changes are retained as historical activity rather than advertised as new work.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

VERSION = 2
CATEGORIES = {'business': '业务代码', 'tests': '测试', 'ci': 'CI／构建配置',
              'docs': '文档', 'config': '运行配置／其他'}
STATUS_LABELS = {'counted': '窗口内不同补丁', 'duplicate': '窗口内重复补丁',
                 'previous_patch': '此前已出现补丁', 'historical': '早于窗口编写的改动',
                 'merge': '合并提交', 'empty': '空补丁'}


def utc(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def within(value, since, until):
    return bool(value and utc(since) < utc(value) <= utc(until))


def file_category(path):
    path = path.lower()
    parts = path.split('/')
    name = parts[-1]
    if name.endswith(('.md', '.mdx', '.rst', '.adoc')) or 'docs' in parts:
        return 'docs'
    if any(x in parts for x in ('test', 'tests', '__tests__', 'fixtures')) or re.search(r'(\.test\.|\.spec\.|_test\.)', name):
        return 'tests'
    if (path.startswith(('.github/', '.gitlab/', '.circleci/')) or any(x in parts for x in ('ci', '.buildkite'))
            or name.startswith(('dockerfile', 'makefile')) or name in ('package.json', 'bun.lock', 'bun.lockb',
            'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'go.mod', 'go.sum', 'action.yml', 'action.yaml')
            or name.startswith(('tsconfig', 'eslint', '.prettier'))):
        return 'ci'
    if name.endswith(('.ts', '.tsx', '.js', '.jsx', '.go', '.py', '.rs', '.java', '.vue', '.svelte', '.css', '.scss', '.sql', '.proto')):
        return 'business'
    return 'config'


class GitEvidence:
    """Read immutable Git objects. Fetch missing SHAs without touching worktrees."""
    def __init__(self, cache, local_root=None):
        self.cache = Path(cache)
        self.local_root = Path(local_root) if local_root else None
        self.locks = defaultdict(threading.Lock)

    def __call__(self, commit):
        repo, sha = commit['repo'], commit['sha']
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo) or not re.fullmatch(r'[0-9a-f]{40,64}', sha):
            raise ValueError('Invalid repository or commit identity')
        cache = self.cache / 'objects' / repo / (sha + '.json')
        if cache.exists():
            cached = json.loads(cache.read_text())
            if cached.get('fingerprint_version') == 2:
                return cached
        local = self.local_root / repo.split('/')[1] if self.local_root else None
        path = local if local and (local / '.git').exists() else self.cache / 'repos' / (repo + '.git')
        with self.locks[repo]:
            if not path.exists():
                path.mkdir(parents=True)
                self.command(path, 'init', '--bare')
            if subprocess.run(['git', '-C', str(path), 'cat-file', '-e', sha + '^{commit}'], capture_output=True).returncode:
                self.command(path, 'fetch', '--no-tags', 'https://github.com/' + repo + '.git', sha)
        metadata = self.command(path, 'show', '-s', '--format=%aI%n%cI%n%P', sha).decode().splitlines()
        parents = metadata[2].split() if len(metadata) > 2 else []
        result = {'authored_at': metadata[0], 'committed_at': metadata[1], 'parents': parents,
                  'patch_id': '', 'categories': [], 'files': [], 'fingerprint_version': 2}
        if len(parents) <= 1:
            diff = self.command(path, 'show', '--format=', '--no-ext-diff', '--no-textconv', '--no-renames', '--binary', sha)
            fingerprint = subprocess.run(['git', 'patch-id', '--verbatim'], input=diff, capture_output=True, check=True).stdout.split()
            result['patch_id'] = fingerprint[0].decode() if fingerprint else ''
            names = self.command(path, 'diff-tree', '--root', '--no-commit-id', '--name-only', '--no-renames', '-r', '-z', sha)
            result['files'] = [n.decode('utf-8', errors='replace') for n in names.split(b'\0') if n]
            result['categories'] = sorted({file_category(n) for n in result['files']})
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result, ensure_ascii=False))
        return result

    @staticmethod
    def command(path, *args):
        result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError(f'Git evidence unavailable: {path.name} {args[0]}: {result.stderr.decode(errors="replace")[-300:]}')
        return result.stdout


def audit_commits(commits, evidence, since, prior=None):
    prior = prior or {}
    seen = {}
    result = []
    for commit in commits:
        detail = dict(evidence(commit))
        key = (commit['repo'], detail['patch_id'])
        equivalent = None
        if len(detail['parents']) > 1:
            status = 'merge'
        elif not detail['patch_id']:
            status = 'empty'
        elif key in prior:
            status, equivalent = 'previous_patch', prior[key]
        elif key in seen:
            status, equivalent = 'duplicate', seen[key]
        elif utc(detail['authored_at']) <= utc(since):
            # Author time alone does not prove a duplicate. Keep a distinct label.
            status = 'historical'
        else:
            status = 'counted'
        if detail['patch_id']:
            seen.setdefault(key, commit['sha'])
        result.append({**commit, 'audit': {**detail, 'status': status, 'equivalent_sha': equivalent}})
    return result


def pr_counts(prs, since, until):
    counts = dict.fromkeys(('opened', 'merged_main', 'merged_branch', 'closed_unmerged', 'ongoing', 'draft', 'state_unknown'), 0)
    for p in prs:
        counts['opened'] += within(p.get('created_at'), since, until)
        if within(p.get('merged_at'), since, until):
            counts['merged_main' if p.get('base') in ('main', 'master') else 'merged_branch'] += 1
        if not p.get('merged_at') and within(p.get('closed_at'), since, until):
            counts['closed_unmerged'] += 1
        # Use the captured state. A historical live re-fetch cannot reconstruct
        # reopen transitions, so do not pretend that it is an end-of-window state.
        counts['ongoing'] += p.get('state') == 'open'
        counts['draft'] += p.get('state') == 'open' and p.get('draft') is True
        counts['state_unknown'] += 'draft' not in p
    return counts


def group_prs(prs):
    """Stack edges are facts; shared issue links are grouping candidates for prose."""
    parents = list(range(len(prs)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    heads = defaultdict(list)
    for i, p in enumerate(prs):
        heads[(p['repo'], p.get('head'))].append(i)
    for i, p in enumerate(prs):
        for j in heads.get((p['repo'], p.get('base')), []):
            parents[root(i)] = root(j)
    groups = defaultdict(list)
    for i, p in enumerate(prs):
        groups[root(i)].append(p)
    result = []
    for members in groups.values():
        members.sort(key=lambda p: (p['repo'], p['number']))
        links = sorted(set(re.findall(r'https://(?:linear\.app/[^\s)<>]+|github\.com/[\w.-]+/[\w.-]+/issues/\d+)',
                                     '\n'.join(p.get('body', '') for p in members))))
        result.append({'basis': 'stack' if len(members) > 1 else 'single_pr',
                       'title': members[0]['title'], 'issue_links': links,
                       'prs': [p['url'] if 'url' in p else f"{p['repo']}#{p['number']}" for p in members],
                       'members': sorted({p.get('user', '') for p in members})})
    return sorted(result, key=lambda g: (-len(g['prs']), g['title']))


def enrich(raw, provider, prior=None, excluded=()):
    raw = dict(raw)
    raw['previous_stats'] = raw.get('stats', {})
    raw['repos'] = [r for r in raw['repos'] if r not in excluded]
    commits = [c for c in raw['commits'] if c['repo'] not in excluded]
    # Stable ordering makes representative selection reproducible.
    commits.sort(key=lambda c: (c['timestamp'], c['repo'], c['sha']))
    with ThreadPoolExecutor(max_workers=6) as pool:
        evidence = list(pool.map(provider, commits))
    details = {(c['repo'], c['sha']): d for c, d in zip(commits, evidence)}
    raw['commits'] = audit_commits(commits, lambda c: details[(c['repo'], c['sha'])], raw['since'], prior)
    raw['prs'] = list({(p['repo'], p['number']): p for p in raw['prs'] if p['repo'] not in excluded}.values())
    raw['ignored_prs'] = [p for p in raw.get('ignored_prs', []) if p['repo'] not in excluded]
    raw['groups'] = group_prs(raw['prs'])
    def stats(cs, ps):
        statuses = Counter(c['audit']['status'] for c in cs)
        categories = Counter(k for c in cs if c['audit']['status'] == 'counted' for k in c['audit']['categories'])
        return {'observed_commit_count': len(cs), 'patch_count': statuses['counted'],
                'audit_statuses': dict(statuses), 'categories': dict(categories),
                'active_pr_count': len(ps), 'prs': pr_counts(ps, raw['since'], raw['until'])}
    raw['stats'] = stats(raw['commits'], raw['prs'])
    raw['stats']['by_member'] = {}
    for member, meta in raw['members'].items():
        aliases = meta.get('aliases', [])
        entry = stats([c for c in raw['commits'] if c['member'] == member],
                      [p for p in raw['prs'] if p['user'] in aliases])
        raw['stats']['by_member'][member] = {**entry, 'display_name': meta['display_name']}
    raw['stats']['active_member_count'] = sum(bool(m['observed_commit_count'] or m['active_pr_count']) for m in raw['stats']['by_member'].values())
    raw['accounting_version'] = VERSION
    raw['accounting_notes'] = [
        '窗口内不同补丁为审计指标，不代表功能数、交付数或工作量；相同补丁按仓库去重。',
        '分类按文件路径；一个补丁可跨类别，各类别不能相加。测试独立列出；未知文件归入运行配置／其他。',
        '作者时间早于窗口的改动单列为历史活动，不据此认定重复劳动。',
        '补丁去重不能证明语义等价；拆分／改写补丁及未关联的 squash 不承诺自动识别。PR 原提交与已识别合并 SHA 由采集层关联排除。',
        'PR 新建和合并可重叠；进行中是采集快照中有窗口活动的开放 PR，不是全仓待办总数。分支合并不等于进入主干。',
        '事项分组优先用实际堆叠关系；关联 Issue 为进一步归组依据，标题相似不能自动证明是同一事项。',
    ]
    return raw


def prior_patches(paths, until):
    result = {}
    for path in paths:
        data = json.loads(Path(path).read_text())
        if data.get('accounting_version') != VERSION or utc(data['until']) > utc(until):
            continue
        for c in data['commits']:
            if c['audit']['patch_id'] and c['audit'].get('fingerprint_version') == 2:
                result.setdefault((c['repo'], c['audit']['patch_id']), c['sha'])
    return result


def statistics_markdown(raw):
    lines = ['**统计附表（活动记录，不作产出排名）**', '',
             '| 成员 | 业务代码 | 测试 | CI／构建 | 文档 | 配置／其他 | PR 新建 | 合入主干 | 合入分支 | 关闭未合并 | 进行中 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for m in raw['stats']['by_member'].values():
        cats, prs = m['categories'], m['prs']
        values = [cats.get(k, 0) for k in CATEGORIES] + [prs.get(k, 0) for k in ('opened', 'merged_main', 'merged_branch', 'closed_unmerged', 'ongoing')]
        lines.append('| ' + m['display_name'] + ' | ' + ' | '.join(map(str, values)) + ' |')
    lines += ['', '分类数字为作者时间在窗口内、经去重的补丁涉及该类文件的次数，可交叉，不能相加；不等于净新增代码或工作量。PR 新建与合并可重叠；进行中仅为本期活跃 PR 的采集快照。',
              '', '**提交审计**', '', '| 成员 | 原始记录 | 不同补丁 | 本期重复 | 此前补丁 | 历史编写 | 合并提交 | 空补丁 |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for m in raw['stats']['by_member'].values():
        s = m['audit_statuses']
        values = [m['observed_commit_count']] + [s.get(k, 0) for k in STATUS_LABELS]
        lines.append('| ' + m['display_name'] + ' | ' + ' | '.join(map(str, values)) + ' |')
    return '\n'.join(lines)


def render_evidence(raw):
    lines = [f"# 日报事实底稿：{raw['date_range']}", '', statistics_markdown(raw), '', '## 事项分组证据', '']
    by_url = {p['url']: p for p in raw['prs']}
    for g in raw['groups']:
        lines += [f"### {g['title']}（{len(g['prs'])} PR，{g['basis']}）", '']
        for url in g['prs']:
            p = by_url[url]
            state = '已合并到 ' + p['base'] if p.get('merged_at') else ('进行中' if p['state'] == 'open' else '关闭未合并')
            lines.append(f"- [{p['repo']} #{p['number']}]({url}) {p['title']}；{state}；目标 {p['base']}")
            if p.get('body'):
                lines.append(p['body'])
    lines += ['', '## 口径', '', *('- ' + n for n in raw['accounting_notes'])]
    return '\n'.join(lines) + '\n'


def narrative_context(raw):
    """Keep immutable patch/file evidence out of the model's normal reading path."""
    keys = ('repo', 'number', 'title', 'url', 'user', 'base', 'head', 'state',
            'created_at', 'closed_at', 'merged_at', 'draft', 'body', 'body_source')
    return {k: raw[k] for k in ('date_range', 'members', 'stats', 'groups', 'accounting_notes')} | {
        'prs': [{k: p[k] for k in keys if k in p} for p in raw['prs']]}
