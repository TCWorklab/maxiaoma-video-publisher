"""Build on a native macOS runner. No personal data enters the application."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
NAME = '马小马视频发布平台'
VERSION = '1.2.8.1'
PYTHON_VERSION = '3.12.14'
FFMPEG_RELEASE = 'https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/'
FFMPEG_HASHES = {
    'arm64': {
        'ffmpeg': 'a90e3db6a3fd35f6074b013f948b1aa45b31c6375489d39e572bea3f18336584',
        'ffprobe': 'bb2db6f5d8cef919da12fbf592119a987202a8c060a886f3cab091f9cab90b64',
    },
    'x64': {
        'ffmpeg': 'ebdddc936f61e14049a2d4b549a412b8a40deeff6540e58a9f2a2da9e6b18894',
        'ffprobe': 'fa3add0ce901f7241abe0dfc0155d958fc834aca3f8ce61f87cc712ae669c1e0',
    },
}
EXCLUDED_DIRS = {
    '.git', '.venv', 'venv', '__pycache__', '.pytest_cache', 'node_modules',
    'data', 'tests', 'bin', 'build', 'dist', '.claude', '.codex', '.agents',
    'cookies', 'cookiesFile', 'uploads', 'materials', 'frames', 'thumbnails',
    'upload_chunks', 'logs', 's3_video_cache',
}
EXCLUDED_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.log', '.pyc', '.p12', '.pfx', '.key', '.pem',
                     '.mp4', '.mov', '.mkv', '.webm', '.avi', '.mp3', '.wav'}


def source_files(source: Path):
    """Explicit clean source walk used by both the build and source exporter."""
    for directory, directories, filenames in os.walk(source):
        directories[:] = sorted(d for d in directories if d not in EXCLUDED_DIRS and not d.startswith('.'))
        for name in sorted(filenames):
            path = Path(directory) / name
            if name.startswith('.') or path.suffix.lower() in EXCLUDED_SUFFIXES or path.is_symlink():
                continue
            yield path


def copy_source(source, destination):
    for path in source_files(source):
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def run(*command, **kwargs):
    print('+', ' '.join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def download(url, destination, expected_hash=None):
    with urlopen(url, timeout=90) as response, destination.open('wb') as output:
        shutil.copyfileobj(response, output)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    if expected_hash and digest != expected_hash:
        raise RuntimeError(f'下载文件校验失败: {destination.name}')
    return digest


def macho_files(root):
    seen = set()
    magic = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
             b'\xfe\xed\xfa\xce', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}
    for path in root.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open('rb') as stream:
            header = stream.read(4)
        if header in magic and path.resolve() not in seen:
            seen.add(path.resolve())
            yield path


def sign_bundle(app, identity):
    entitlements = ROOT / 'packaging/macos/entitlements.plist'
    options = ['--options', 'runtime', '--timestamp'] if identity != '-' else []
    # Sign nested Mach-O code explicitly, including Python extension modules.
    for path in sorted(macho_files(app), key=lambda p: len(p.parts), reverse=True):
        run('codesign', '--force', '--sign', identity, *options, '--entitlements', entitlements, path)
    run('codesign', '--force', '--sign', identity, *options, '--entitlements', entitlements, app)
    run('codesign', '--verify', '--deep', '--strict', app)


def make_icon(source, work, target):
    iconset = work / 'Brand.iconset'
    iconset.mkdir()
    for size in [16, 32, 128, 256, 512]:
        for scale in [1, 2]:
            suffix = '@2x' if scale == 2 else ''
            run('sips', '-z', str(size * scale), str(size * scale), source,
                '--out', iconset / f'icon_{size}x{size}{suffix}.png', stdout=subprocess.DEVNULL)
    run('iconutil', '-c', 'icns', iconset, '-o', target)


def build(arch, output, identity, notary_profile):
    expected = 'arm64' if arch == 'arm64' else 'x86_64'
    if sys.platform != 'darwin' or platform.machine() != expected:
        raise SystemExit(f'{arch} 安装包须在 {expected} macOS 构建机上生成和验收。')
    for tool in ['uv', 'node', 'npm', 'xcrun', 'codesign', 'hdiutil', 'ditto']:
        if not shutil.which(tool):
            raise SystemExit(f'构建机缺少 {tool}；最终用户不需要安装这些工具。')
    if notary_profile and identity == '-':
        raise SystemExit('Apple 公证需要 Developer ID 签名，不能使用临时签名。')
    output.mkdir(parents=True, exist_ok=True)
    stem = f'MaXiaoMa-{VERSION}-macOS-{arch}'
    if any((output / (stem + suffix)).exists() for suffix in ['.dmg', '.zip']):
        raise SystemExit('输出文件已存在，请使用新的输出目录，避免覆盖已验收的安装包。')
    with tempfile.TemporaryDirectory(prefix='maxiaoma-macos-') as temporary:
        work = Path(temporary)
        app = work / f'{NAME}.app'
        contents = app / 'Contents'
        resources = contents / 'Resources'
        executable_dir = contents / 'MacOS'
        resources.mkdir(parents=True)
        executable_dir.mkdir()
        notices = resources / 'ThirdPartyNotices'
        notices.mkdir()
        shutil.copy2(ROOT / 'LICENSE', notices / 'Project-LICENSE.txt')
        shutil.copy2(ROOT / 'packaging/macos/THIRD-PARTY.md', notices / 'README.md')
        copy_source(ROOT / 'backend', resources / 'backend')
        shutil.copy2(ROOT / 'packaging/macos/runtime.py', resources / 'runtime.py')
        shutil.copy2(ROOT / 'versions', resources / 'versions')
        frontend_work = work / 'frontend-source'
        copy_source(ROOT / 'frontend', frontend_work)
        env = dict(os.environ, VITE_API_BASE_URL='.', PYTHONDONTWRITEBYTECODE='1',
                   UV_PYTHON_INSTALL_BIN='0', UV_CACHE_DIR=str(work / 'uv-cache'))
        run('npm', 'ci', cwd=frontend_work, env=env)
        run('npm', 'run', 'build', cwd=frontend_work, env=env)
        shutil.copytree(frontend_work / 'dist', resources / 'frontend')
        for directory, _, filenames in os.walk(frontend_work / 'node_modules'):
            for filename in filenames:
                if filename.lower().startswith(('license', 'licence', 'copying', 'notice')):
                    license_path = Path(directory) / filename
                    target = notices / 'frontend' / license_path.relative_to(frontend_work / 'node_modules')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(license_path, target)
        # A complete relocatable interpreter, installed at the final bundle location.
        python_store = work / 'python-download'
        run('uv', 'python', 'install', PYTHON_VERSION, '--install-dir', python_store, '--no-bin', env=env)
        discovery_env = dict(env, UV_PYTHON_INSTALL_DIR=str(python_store))
        discovered = run('uv', 'python', 'find', PYTHON_VERSION, '--managed-python',
                         '--no-project', '--no-config', env=discovery_env,
                         capture_output=True, text=True).stdout.strip()
        installation = Path(discovered).resolve().parent.parent
        if python_store.resolve() not in installation.parents:
            raise RuntimeError(f'Python 运行环境不在指定下载目录内: {installation}')
        shutil.copytree(installation, resources / 'python', symlinks=True)
        python = resources / 'python/bin/python3'
        constraints = ROOT / 'packaging/macos/constraints.txt'
        run('uv', 'pip', 'install', '--python', python, '--system', '--break-system-packages',
            '-r', ROOT / 'backend/requirements.txt', '-c', constraints, env=env)
        with (resources / 'requirements-resolved.txt').open('w') as freeze:
            run('uv', 'pip', 'freeze', '--python', python, stdout=freeze, env=env)
        # Include the original library metadata/license files inside site-packages.
        binaries = resources / 'bin'
        binaries.mkdir()
        hashes = {}
        for name, digest in FFMPEG_HASHES[arch].items():
            filename = f'{name}-darwin-{arch}'
            hashes[filename] = download(FFMPEG_RELEASE + filename, binaries / name, digest)
            (binaries / name).chmod(0o755)
            run(binaries / name, '-version', stdout=subprocess.DEVNULL)
        for suffix in ['LICENSE', 'README']:
            download(FFMPEG_RELEASE + f'darwin-{arch}.{suffix}', notices / f'FFmpeg-{suffix}.txt')
        # No CloakBrowser binary is redistributed. The recipient downloads it officially.
        run('xcrun', 'swiftc', '-swift-version', '5', '-O', '-target', f'{expected}-apple-macos15.0',
            ROOT / 'packaging/macos/Launcher.swift', '-o', executable_dir / 'MaXiaoMa', '-framework', 'AppKit')
        make_icon(resources / 'frontend/brand/maxiaoma-cutout.png', work, resources / 'Brand.icns')
        info = {
            'CFBundleName': NAME, 'CFBundleDisplayName': NAME,
            'CFBundleIdentifier': 'com.maxiaoma.publisher', 'CFBundleExecutable': 'MaXiaoMa',
            'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': '1.2.8',
            'CFBundleVersion': VERSION, 'CFBundleIconFile': 'Brand.icns',
            'LSMinimumSystemVersion': '15.0', 'NSHighResolutionCapable': True,
            'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
        }
        (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
        # Explicit architecture check; wrong-architecture bundles never become installers.
        for path in macho_files(app):
            run('lipo', path, '-verify_arch', expected, stdout=subprocess.DEVNULL)
        manifest = {'name': NAME, 'version': VERSION, 'arch': arch, 'minimum_macos': '15.0',
                    'python': PYTHON_VERSION, 'browser_mode': 'official-first-launch-download',
                    'browser_version': '145.0.7632.109.2', 'ffmpeg_sha256': hashes,
                    'personal_data_included': False, 'developer_id_signed': identity != '-',
                    'notarization_requested': bool(notary_profile)}
        (resources / 'build-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        sign_bundle(app, identity)
        # Real isolated runtime verification must pass on EACH architecture.
        run(python, '-B', ROOT / 'packaging/macos/smoke.py', '--app', app,
            '--report', output / (stem + '-verification.json'), env=env)
        # Runtime checks must leave the sealed application unchanged.
        run('codesign', '--verify', '--deep', '--strict', app)
        archive = output / (stem + '.zip')
        if notary_profile:
            submission = work / 'notary.zip'
            run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, submission)
            run('xcrun', 'notarytool', 'submit', submission, '--keychain-profile', notary_profile, '--wait')
            run('xcrun', 'stapler', 'staple', app)
            run('spctl', '--assess', '--type', 'execute', '--verbose', app)
        run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, archive)
        image_root = work / 'image-root'
        image_root.mkdir()
        run('ditto', app, image_root / app.name)
        (image_root / 'Applications').symlink_to('/Applications')
        shutil.copy2(ROOT / 'packaging/macos/给朋友的使用说明.txt', image_root / '使用说明.txt')
        disk = output / (stem + '.dmg')
        run('hdiutil', 'create', '-volname', NAME, '-srcfolder', image_root, '-format', 'UDZO', disk)
        run('hdiutil', 'verify', disk)
        if notary_profile:
            run('codesign', '--force', '--sign', identity, '--timestamp', disk)
            run('xcrun', 'notarytool', 'submit', disk, '--keychain-profile', notary_profile, '--wait')
            run('xcrun', 'stapler', 'staple', disk)
        (output / (stem + '.sha256')).write_text(''.join(
            f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n' for path in [disk, archive]
        ), encoding='utf-8')
        print(f'生成并完成运行检查：{disk}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--arch', choices=['arm64', 'x64'], required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/macos')
    parser.add_argument('--identity', default='-', help='Developer ID Application identity, or ad-hoc (-)')
    parser.add_argument('--notary-profile', default='', help='Preconfigured notarytool keychain profile')
    args = parser.parse_args()
    build(args.arch, args.output.resolve(), args.identity, args.notary_profile)
