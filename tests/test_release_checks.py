"""Tool success is not equivalent to a clean scan; no live tools are called."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_checks',ROOT/'scripts/release_checks.py')
checks=importlib.util.module_from_spec(spec);spec.loader.exec_module(checks)
scan_spec=importlib.util.spec_from_file_location('scan_public_release',ROOT/'scripts/scan_public.py')
scan=importlib.util.module_from_spec(scan_spec);scan_spec.loader.exec_module(scan)


class ReleaseChecksTests(unittest.TestCase):
    def test_zero_exit_with_detected_secret_is_not_a_pass(self):
        result=checks.classify('secrets',0,json.dumps({'results':{'fixture.py':[{'type':'Synthetic'}]}}))
        self.assertTrue(result['completed']);self.assertFalse(result['passed'])
        self.assertEqual(result['finding_count'],1)

    def test_tool_failure_never_becomes_zero_findings(self):
        result=checks.classify('advisories',2,b'network failure')
        self.assertFalse(result['completed']);self.assertIsNone(result['finding_count'])
        self.assertFalse(result['passed'])

    def test_advisory_and_skipped_package_require_review(self):
        result=checks.classify('advisories',1,json.dumps({'dependencies':[{'vulns':[{'id':'SYNTHETIC'}]}]}))
        self.assertTrue(result['completed']);self.assertFalse(result['passed'])
        self.assertEqual(result['finding_count'],1)
        result=checks.classify('advisories',0,json.dumps({'dependencies':[{'skip_reason':'missing','vulns':[]}]}))
        self.assertFalse(result['passed'])

    def test_clean_scans_are_distinct_from_unavailable(self):
        self.assertTrue(checks.classify('secrets',0,b'{"results":{}}')['passed'])
        self.assertTrue(checks.classify('advisories',0,b'{"dependencies":[{"vulns":[]}]}')['passed'])

    def test_private_scan_outputs_are_not_public_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'reports/local/run';out.mkdir(parents=True)
            (out/'raw.txt').write_text('synthetic private diagnostic')
            self.assertEqual(scan.file_bytes(root),{})

    def test_github_tokens_are_reported_without_their_values(self):
        value=b'ghp_'+b'A'*40
        hits,_=scan.findings({'fixture.txt':value},{})
        self.assertEqual(hits,[{'file':'fixture.txt','kind':'github_token'}])
        self.assertNotIn(value.decode(),json.dumps(hits))
