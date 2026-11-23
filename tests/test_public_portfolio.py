"""Portfolio defaults remain offline, evidence-backed and fail-closed."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.verify import verify
from benchmarks import collect
from operations.ollama_bridge import connection

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('public_scan',ROOT/'scripts/scan_public.py')
scan=importlib.util.module_from_spec(spec);spec.loader.exec_module(scan)


class PortfolioTests(unittest.TestCase):
    def test_original_benchmark_scores_are_recomputed(self):
        rows=verify()
        self.assertEqual(len(rows),6)
        self.assertEqual(sum(x['strict_score'] for x in rows if x['model']=='gemma4:e2b'),0)
        self.assertEqual(sum(x['strict_score'] for x in rows if x['model']=='gpt-5.6-sol'),12)

    def test_default_collection_never_constructs_backend_or_reads_profile(self):
        with patch('sys.argv',['collect']),patch.object(collect,'HermesBackend') as backend,patch('builtins.print'):
            self.assertEqual(collect.main(),0)
            backend.assert_not_called()

    def test_unconfigured_bridge_cannot_open_a_destination(self):
        with self.assertRaises(ValueError):connection('unconfigured.invalid')
        with self.assertRaises(ValueError):connection('attacker.example')

    def test_scanner_reports_patterns_without_secret_values(self):
        value=('sk-'+'A'*40).encode()
        hits,_=scan.findings({'example.txt':value},{})
        self.assertEqual(hits,[{'file':'example.txt','kind':'api_key'}])
        self.assertNotIn(value.decode(),json.dumps(hits))

    def test_candidate_scan_refuses_private_paths_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'secrets').mkdir();(root/'secrets/private.json').write_text('{}')
            with self.assertRaises(ValueError):scan.file_bytes(root)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'link').symlink_to('/missing')
            with self.assertRaises(ValueError):scan.file_bytes(root)
