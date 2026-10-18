"""Synthetic credentials only; helper output must never expose input contents."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


HELPER = Path(__file__).resolve().parents[1] / 'stage_persistent_profile.sh'


class PersistentProfileTests(unittest.TestCase):
    def run_fixture(self, original, expected=None, existing=None):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.ini'
            output = Path(directory) / 'output.ini'
            source.write_bytes(original)
            if existing == 'symlink':
                output.symlink_to(source)
            elif existing == 'file':
                output.write_bytes(b'original-output')
            result = subprocess.run(['bash', str(HELPER), str(source), str(output)], capture_output=True)
            self.assertNotIn(b'SYNTHETIC-SECRET', result.stdout + result.stderr)
            self.assertNotIn(str(source).encode(), result.stdout + result.stderr)
            self.assertEqual(source.read_bytes(), original)
            if expected is None:
                self.assertNotEqual(result.returncode, 0)
                if existing == 'symlink':
                    self.assertTrue(output.is_symlink())
                elif existing == 'file':
                    self.assertEqual(output.read_bytes(), b'original-output')
                else:
                    self.assertFalse(output.exists())
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout + result.stderr, b'')
                self.assertEqual(output.read_bytes(), expected)
                self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertFalse(list(Path(directory).glob('.mt5-profile-*')))

    def test_crlf_and_unrelated_sections(self):
        self.run_fixture(b'[Common]\r\nPassword=SYNTHETIC-SECRET\r\nKeepPrivate=0\r\n[Experts]\r\nEnabled=1\r\n',
                         b'[Common]\r\nPassword=SYNTHETIC-SECRET\r\nKeepPrivate=1\r\n[Experts]\r\nEnabled=1\r\n')

    def test_absent_option(self):
        self.run_fixture(b'[Common]\nPassword=SYNTHETIC-SECRET\n[Other]\nx=y\n',
                         b'[Common]\nPassword=SYNTHETIC-SECRET\nKeepPrivate=1\n[Other]\nx=y\n')

    def test_absent_common(self):
        self.run_fixture(b'[Other]\nx=y', b'[Other]\nx=y\n[Common]\nKeepPrivate=1\n')

    def test_case_bom_and_no_final_newline(self):
        self.run_fixture(b'\xef\xbb\xbf[common]\n keepPRIVATE = 0',
                         b'\xef\xbb\xbf[common]\nKeepPrivate=1')

    def test_duplicates_fail_closed(self):
        for fixture in (b'[Common]\nKeepPrivate=0\nkeepprivate=1\n', b'[Common]\n[common]\n'):
            with self.subTest(fixture=fixture):
                self.run_fixture(fixture)

    def test_unsupported_encoding(self):
        self.run_fixture('[Common]\nPassword=SYNTHETIC-SECRET'.encode('utf-16'))
        self.run_fixture(b'[Common]\nPassword=\xff')

    def test_existing_outputs_refused(self):
        for existing in ('symlink', 'file'):
            self.run_fixture(b'[Common]\nPassword=SYNTHETIC-SECRET\n', existing=existing)

    def test_source_symlink_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            actual = Path(directory) / 'actual.ini'
            actual.write_bytes(b'[Common]\nPassword=SYNTHETIC-SECRET\n')
            source = Path(directory) / 'input.ini'
            source.symlink_to(actual)
            output = Path(directory) / 'output.ini'
            result = subprocess.run(['bash', str(HELPER), str(source), str(output)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b'SYNTHETIC-SECRET', result.stdout + result.stderr)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
