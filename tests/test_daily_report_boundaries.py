import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/progress-report/scripts'))
import replay_daily_report as replay


class BoundaryTest(unittest.TestCase):
    def test_replay_does_not_overwrite_input_or_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'window'; folder.mkdir()
            source = folder / 'raw.json'
            raw = dict(since='2026-01-01T00:00:00Z', until='2026-01-02T00:00:00Z', date_range='sample',
                       repos=[], members={}, commits=[], prs=[])
            source.write_text(json.dumps(raw)); (folder / 'report.md').write_text('sent report')
            for destination in [root, root / 'output']:
                out = destination / 'window'; out.mkdir(parents=True, exist_ok=True)
                (out / 'state.json').write_text('{"messages":{"recipient":"receipt"}}')
                with patch.object(sys, 'argv', ['replay', str(source), '--output-dir', str(destination)]), \
                        patch.object(replay, 'load_config', return_value={}):
                    with self.assertRaises((ValueError, FileExistsError)):
                        replay.main()
                self.assertEqual(json.loads(source.read_text()), raw)
                self.assertEqual((folder / 'report.md').read_text(), 'sent report')

    def test_historical_description_is_not_replaced(self):
        raw = {'prs': [{'repo': 'example/service', 'number': 1, 'body': 'captured historical description'}]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(replay, 'ROOT', Path(tmp)), \
                patch.object(replay, 'github_get', return_value={'body': 'current description'}) as remote:
            replay.supplement_descriptions(raw, 'unused')
            remote.assert_not_called()
        self.assertEqual(raw['prs'][0]['body'], 'captured historical description')
