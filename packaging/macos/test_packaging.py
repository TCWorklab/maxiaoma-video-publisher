"""Cross-platform checks; these do NOT claim to validate a native Mac binary."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('mac_builder', HERE / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PackagingTests(unittest.TestCase):
    def test_clean_source_excludes_sessions_databases_and_private_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            names = ['app.py', 'services/account_maintenance.py', 'impl/douyin/platform.py',
                     'data/cookiesFile/session.json', 'data/materials/private.mp4',
                     '.venv/Lib/site-packages/example.py', '.env', '.env.production',
                     'private.key', 'signing.p12', 'database.db', 'debug.log',
                     'cookiesFile/legacy.json', 'cookies/account.json', 'example.mp4',
                     'tests/fixtures/private.json', '__pycache__/x.pyc']
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture', encoding='utf-8')
            actual = {p.relative_to(root).as_posix() for p in builder.source_files(root)}
            self.assertEqual(actual, {'app.py', 'services/account_maintenance.py', 'impl/douyin/platform.py'})

    def test_non_mac_host_cannot_claim_an_installer(self):
        with patch.object(builder.sys, 'platform', 'win32'):
            with self.assertRaises(SystemExit):
                builder.build('arm64', Path('unused-output'), '-', '')

    def test_packaged_frontend_and_brand_are_served_from_configured_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frontend = root / 'frontend'
            (frontend / 'brand').mkdir(parents=True)
            (frontend / 'assets').mkdir()
            (frontend / 'index.html').write_text('<h1>isolated production frontend</h1>', encoding='utf-8')
            (frontend / 'brand/logo.png').write_bytes(b'brand-fixture')
            (frontend / 'assets/main.js').write_text('window.bundleReady = true', encoding='utf-8')
            env = dict(os.environ, SAU_DATA_DIR=str(root / 'new-user'), SAU_FRONTEND_DIR=str(frontend),
                       PYTHONPATH=str(ROOT / 'backend'), PYTHONUTF8='1')
            script = '''
import app
client = app.app.test_client()
assert client.get('/').data == b'<h1>isolated production frontend</h1>'
assert client.get('/brand/logo.png').data == b'brand-fixture'
assert client.get('/assets/main.js').status_code == 200
assert client.get('/brand/../../private.key').status_code == 404
print('isolated-static-routes-ok')
'''
            result = subprocess.run([sys.executable, '-c', script], cwd=ROOT / 'backend', env=env,
                                    text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr[-2500:])
            self.assertIn('isolated-static-routes-ok', result.stdout)

    def test_each_ffmpeg_architecture_has_distinct_verified_binaries(self):
        for arch in ['arm64', 'x64']:
            self.assertEqual(set(builder.FFMPEG_HASHES[arch]), {'ffmpeg', 'ffprobe'})
            for digest in builder.FFMPEG_HASHES[arch].values():
                self.assertEqual(len(bytes.fromhex(digest)), 32)
        self.assertNotEqual(builder.FFMPEG_HASHES['arm64'], builder.FFMPEG_HASHES['x64'])


if __name__ == '__main__':
    unittest.main()
