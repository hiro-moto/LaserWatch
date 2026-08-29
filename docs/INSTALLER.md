# Windows installer build

LaserWatch uses two build stages:

1. **PyInstaller** creates a self-contained application folder at `dist/LaserWatch/`.
2. **Inno Setup 6** packages that folder into a normal Windows installer (`.exe`).

The final installer installs LaserWatch under `Program Files`, adds a Start Menu shortcut,
and can optionally create a desktop shortcut.

## Prerequisites

Install:

- Windows 10 or 11 (64-bit)
- [uv](https://docs.astral.sh/uv/)
- Inno Setup 6

Inno Setup can be installed with Windows Package Manager:

```powershell
winget install --id JRSoftware.InnoSetup -e
```

## One-command local build

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_installer.ps1
```

or:

```cmd
build_installer.bat
```

The script will:

- create/sync the uv environment,
- install the build dependency group,
- run the regression tests,
- run PyInstaller,
- locate `ISCC.exe`,
- compile `installer/LaserWatch.iss`.

Expected outputs:

```text
dist\LaserWatch\LaserWatch.exe
installer\output\LaserWatch_Setup_0.8.4.exe
```

## PyInstaller only

If you only want the portable application folder:

```powershell
uv sync --extra build
uv run pyinstaller --noconfirm --clean LaserWatch.spec
```

Then run:

```text
dist\LaserWatch\LaserWatch.exe
```

No Python installation is required on the target PC for this built version.

## Inno Setup only

After PyInstaller has created `dist/LaserWatch/`, compile:

```text
installer\LaserWatch.iss
```

from the Inno Setup GUI, or run:

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" .\installer\LaserWatch.iss
```

## GitHub Actions

`.github/workflows/windows-build.yml` performs the same process on a Windows GitHub
runner. Every workflow run uploads:

- the portable `dist/LaserWatch/` folder,
- the Inno Setup installer executable.

For version tags such as `v0.8.4`, the workflow also creates or updates a GitHub
Release automatically and attaches both:

- `LaserWatch_Setup_0.8.4.exe`,
- a portable ZIP of the PyInstaller application folder.

Example release command:

```powershell
git tag v0.8.4
git push origin v0.8.4
```

After the Windows workflow finishes, the files appear under the repository's
**Releases** page.

## Important acceptance check

Before distributing an installer broadly, build it on a clean Windows environment and
verify at least one monochrome and one color UVC camera. In particular, check DirectShow
device enumeration, exposure control, requested/actual FOURCC and frame rate, unplug/replug
recovery, and any 10/12/16-bit camera path you intend to support.
