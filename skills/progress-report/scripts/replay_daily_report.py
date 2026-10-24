#!/usr/bin/env python3
"""Recalculate captured windows without changing sent reports or delivery receipts."""
from __future__ import annotations
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from collect_progress import load_config, gh_token, github_get
from progress_accounting import GitEvidence, enrich, prior_patches, render_evidence

ROOT = Path(__file__).resolve().parents[3]


def supplement_descriptions(raw, token):
    cache_root = ROOT / '.state/progress-pr-context'
    def read(pr):
        if pr.get('body'):
            return pr
        path = cache_root / pr['repo'] / (str(pr['number']) + '.json')
        if not path.exists():
            data = github_get(token, f"/repos/{pr['repo']}/issues/{pr['number']}", paginate=False)
            context = dict(body=data.get('body') or '', collected_at=datetime.now(timezone.utc).isoformat())
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(context, ensure_ascii=False))
        context = json.loads(path.read_text())
        return {**pr, 'body': context['body'], 'body_source': 'current_supplement',
                'body_collected_at': context['collected_at']}
    with ThreadPoolExecutor(max_workers=4) as pool:
        raw['prs'] = list(pool.map(read, raw['prs']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshots', nargs='+', type=Path, help='Original raw.json files, including optional baseline windows')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--with-current-descriptions', action='store_true', help='Supplement missing PR descriptions; retain historical states and mark provenance')
    parser.add_argument('--config', type=Path, default=ROOT / 'skills/progress-report/config.yaml')
    args = parser.parse_args()
    config = load_config(args.config)
    cfg = config.get('report', {})
    provider = GitEvidence(ROOT / '.state/progress-evidence', cfg.get('local_git_root'))
    snapshots = [(json.loads(p.read_text()), p) for p in args.snapshots]
    snapshots.sort(key=lambda x: x[0]['since'])
    destinations = [(args.output_dir / path.parent.name).resolve() for _, path in snapshots]
    if len(set(destinations)) != len(destinations):
        raise ValueError('Multiple snapshots would write to the same output directory')
    for folder in destinations:
        if folder.exists() and any(folder.iterdir()):
            raise FileExistsError(f'Replay needs a new or empty output directory: {folder}')
    outputs = []
    for source, path in snapshots:
        folder = args.output_dir / path.parent.name
        folder.mkdir(parents=True, exist_ok=True)
        print('Auditing ' + source['date_range'], flush=True)
        if args.with_current_descriptions:
            supplement_descriptions(source, gh_token(config))
        prior = prior_patches(outputs, source['since'])
        raw = enrich(source, provider, prior, cfg.get('excluded_repos', []))
        raw['replay_source'] = str(path.resolve())
        raw['prior_windows'] = [str(p) for p in outputs]
        output = folder / 'raw.json'
        output.write_text(json.dumps(raw, ensure_ascii=False, indent=2))
        (folder / 'report.md').write_text(render_evidence(raw))
        outputs.append(output)
        print(json.dumps({'window': source['date_range'], 'stats': raw['stats']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
