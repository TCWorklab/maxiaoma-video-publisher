# Third-party components

The application includes the project's original MIT license in Project-LICENSE.txt.
Python is the relocatable CPython distribution supplied by python-build-standalone:
https://github.com/astral-sh/python-build-standalone
Its original license files are retained in the Python runtime. Python dependency
metadata and license files are retained in site-packages; exact installed versions
are recorded in requirements-resolved.txt.

FFmpeg and FFprobe are separate command-line programs from the b6.1.1 release:
https://github.com/eugeneware/ffmpeg-static/releases/tag/b6.1.1
The architecture-specific license and upstream README (including build/source
information) are included as FFmpeg-LICENSE.txt and FFmpeg-README.txt. Their binary
SHA-256 values are pinned and recorded in build-manifest.json.

The CloakBrowser Python wrapper is a dependency. Its Chromium binary is NOT
included in this distribution. On first launch the end user obtains the unmodified
binary directly from official CloakHQ distribution servers via the wrapper.
The browser's separate license is available at:
https://github.com/CloakHQ/CloakBrowser/blob/main/BINARY-LICENSE.md

No developer account database, login session, video, draft, publication record,
signing private key, or GitHub credential is included.
