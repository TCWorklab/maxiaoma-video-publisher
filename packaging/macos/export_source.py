"""Export the customized source/build kit, excluding local accounts and media."""
import argparse
import hashlib
from pathlib import Path
import zipfile

from build import ROOT, source_files


def export(destination):
    included = []
    for part in ['backend', 'frontend', 'packaging/macos']:
        included.extend(source_files(ROOT / part))
    included.extend(ROOT / name for name in ['LICENSE', 'versions', '.gitattributes', '.github/workflows/macos-installers.yml'])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f'不覆盖已有构建源码包：{destination}')
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(included)):
            archive.write(path, str(path.relative_to(ROOT)).replace('\\', '/'))
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.sha256').write_text(f'{digest}  {destination.name}\n', encoding='utf-8')
    print(f'构建源码包（不是安装包）：{destination}; {len(included)} 个文件; SHA256 {digest}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    export(parser.parse_args().destination.resolve())
