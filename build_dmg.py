#!/usr/bin/env python3
"""
Kiyomi Lite — macOS Build Script
Creates Kiyomi.app via PyInstaller, then wraps it in a .dmg with
an Applications symlink for drag-and-drop install.

Usage:
    python build_dmg.py          # Build .app + .dmg
    python build_dmg.py --app    # Build .app only (skip dmg)
    python build_dmg.py --clean  # Clean build artifacts first

Target: arm64 macOS (Apple Silicon)
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------
APP_NAME = "Kiyomi"
BUNDLE_ID = "com.kiyomi.lite"
VERSION = "3.0.0"
ENTRY_POINT = "app.py"
ICON_FILE = "resources/icon.icns"       # Will be created as placeholder
DIST_DIR = Path("dist")
BUILD_DIR = Path("build")
DMG_NAME = f"{APP_NAME}-{VERSION}.dmg"

# Directories to bundle as data (relative to project root)
DATA_DIRS = [
    ("engine", "engine"),               # engine/ → engine/
    ("onboarding", "onboarding"),       # onboarding/ → onboarding/
]

DATA_FILES = [
    ("import_brain.py", "."),           # import_brain.py → ./
    ("requirements.txt", "."),          # for reference
]

# Hidden imports that PyInstaller might miss
HIDDEN_IMPORTS = [
    "rumps",
    "google.generativeai",
    "anthropic",
    "openai",
    "telegram",
    "telegram.ext",
    "pytz",
    "docx",
    "docx.opc",
    "docx.oxml",
    "lxml",
    "lxml.etree",
    "html",
    "html.parser",
    "smtplib",
    "email",
    "email.mime",
    "email.mime.text",
    "email.mime.multipart",
]

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def run(cmd: list[str], **kwargs):
    """Run a command and stream output."""
    print(f"  → {' '.join(cmd)}")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        print(f"  ✗ Command failed with exit code {result.returncode}")
        sys.exit(1)
    return result


def ensure_pyinstaller():
    """Make sure PyInstaller is installed."""
    try:
        import PyInstaller
        print(f"  ✓ PyInstaller {PyInstaller.__version__}")
    except ImportError:
        print("  Installing PyInstaller...")
        run([sys.executable, "-m", "pip", "install", "pyinstaller"])


def ensure_icon():
    """Create a placeholder .icns file if none exists."""
    global ICON_FILE
    icon_path = Path(ICON_FILE)
    if icon_path.exists():
        print(f"  ✓ Icon found: {ICON_FILE}")
        return

    icon_path.parent.mkdir(parents=True, exist_ok=True)

    # Create a minimal placeholder icon using sips (macOS built-in)
    # Generate a 512x512 pink square PNG, then convert to icns
    tmp_png = icon_path.parent / "icon_tmp.png"

    try:
        # Use Python to create a simple PNG (no PIL needed — raw PNG)
        _write_placeholder_png(tmp_png, size=512, r=255, g=107, b=157)
        # iconutil needs an iconset; use sips as a shortcut
        run(["sips", "-s", "format", "icns", str(tmp_png), "--out", str(icon_path)],
            capture_output=True)
        tmp_png.unlink(missing_ok=True)
        print(f"  ✓ Placeholder icon created: {ICON_FILE}")
    except Exception as e:
        print(f"  ⚠ Could not create icon ({e}), building without icon")
        # Remove the flag so PyInstaller doesn't fail looking for it
        ICON_FILE = None


def _write_placeholder_png(path: Path, size: int, r: int, g: int, b: int):
    """Write a solid-color PNG file without any external libs."""
    import struct
    import zlib

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    # IHDR
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    # IDAT — raw pixel data
    raw_row = b'\x00' + bytes([r, g, b]) * size  # filter byte + RGB * width
    raw_data = raw_row * size
    idat = zlib.compress(raw_data)
    # Assemble
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', ihdr)
    png += chunk(b'IDAT', idat)
    png += chunk(b'IEND', b'')
    path.write_bytes(png)


# -------------------------------------------------------------------
# Build Steps
# -------------------------------------------------------------------

def clean():
    """Remove previous build artifacts."""
    print("\n🧹 Cleaning build artifacts...")
    for d in [BUILD_DIR, DIST_DIR]:
        if d.exists():
            shutil.rmtree(d)
            print(f"  Removed {d}/")
    spec = Path(f"{APP_NAME}.spec")
    if spec.exists():
        spec.unlink()
        print(f"  Removed {spec}")


def build_app():
    """Build Kiyomi.app with PyInstaller."""
    print("\n📦 Building Kiyomi.app with PyInstaller...")
    ensure_pyinstaller()
    ensure_icon()

    # Assemble PyInstaller args
    args = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--windowed",                    # .app bundle (no terminal)
        "--onedir",                      # faster startup than onefile
        "--noconfirm",
        "--clean",
        f"--osx-bundle-identifier={BUNDLE_ID}",
    ]

    # Target architecture
    if platform.machine() == "arm64":
        args.extend(["--target-architecture", "arm64"])

    # Icon
    if ICON_FILE and Path(ICON_FILE).exists():
        args.extend(["--icon", ICON_FILE])

    # Add data directories
    sep = ":" if sys.platform != "win32" else ";"
    for src, dst in DATA_DIRS:
        if Path(src).exists():
            args.extend(["--add-data", f"{src}{sep}{dst}"])
        else:
            print(f"  ⚠ Data dir not found (skipping): {src}")

    for src, dst in DATA_FILES:
        if Path(src).exists():
            args.extend(["--add-data", f"{src}{sep}{dst}"])

    # Hidden imports
    for imp in HIDDEN_IMPORTS:
        args.extend(["--hidden-import", imp])

    # Entry point
    args.append(ENTRY_POINT)

    run(args)

    app_path = DIST_DIR / f"{APP_NAME}.app"
    if not app_path.exists():
        # PyInstaller --onedir puts app in a subfolder
        app_path = DIST_DIR / APP_NAME / f"{APP_NAME}.app"

    if app_path.exists():
        print(f"\n  ✅ Built: {app_path}")
    else:
        print(f"\n  ✗ Build failed — .app not found in {DIST_DIR}")
        sys.exit(1)

    return app_path


def build_dmg(app_path: Path):
    """Create a professional .dmg with drag-to-Applications UI."""
    print(f"\n💿 Creating {DMG_NAME}...")

    dmg_output = DIST_DIR / DMG_NAME
    dmg_output.unlink(missing_ok=True)

    bg_image = Path("assets/dmg_background.png")

    # Use create-dmg for a professional installer look
    create_dmg = shutil.which("create-dmg")
    if create_dmg and bg_image.exists():
        print("  Using create-dmg for professional installer...")
        cmd = [
            create_dmg,
            "--volname", APP_NAME,
            "--background", str(bg_image),
            "--window-pos", "200", "120",
            "--window-size", "660", "400",
            "--icon-size", "80",
            "--icon", f"{APP_NAME}.app", "165", "230",
            "--app-drop-link", "495", "230",
            "--text-size", "14",
            "--no-internet-enable",
            str(dmg_output),
            str(app_path),
        ]
        # create-dmg returns 2 if it can't set custom icon (non-fatal)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode not in (0, 2):
            print(f"  ⚠ create-dmg failed (code {result.returncode}), falling back to hdiutil")
            print(f"  stderr: {result.stderr[:300]}")
            create_dmg = None

    if not create_dmg or not bg_image.exists() or not dmg_output.exists():
        # Fallback: plain hdiutil DMG with Applications symlink
        print("  Using hdiutil fallback...")
        dmg_staging = BUILD_DIR / "dmg_staging"
        if dmg_staging.exists():
            shutil.rmtree(dmg_staging)
        dmg_staging.mkdir(parents=True)

        dst_app = dmg_staging / f"{APP_NAME}.app"
        shutil.copytree(app_path, dst_app, symlinks=True)
        apps_link = dmg_staging / "Applications"
        apps_link.symlink_to("/Applications")

        run([
            "hdiutil", "create",
            "-volname", APP_NAME,
            "-srcfolder", str(dmg_staging),
            "-ov", "-format", "UDZO",
            str(dmg_output),
        ])

    if dmg_output.exists():
        size_mb = dmg_output.stat().st_size / 1024 / 1024
        print(f"\n  ✅ DMG created: {dmg_output} ({size_mb:.1f} MB)")
    else:
        print(f"\n  ✗ DMG creation failed")
        sys.exit(1)

    return dmg_output


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=f"Build {APP_NAME}.app and .dmg")
    parser.add_argument("--app", action="store_true", help="Build .app only (skip dmg)")
    parser.add_argument("--clean", action="store_true", help="Clean build artifacts before building")
    parser.add_argument("--clean-only", action="store_true", help="Just clean, don't build")
    args = parser.parse_args()

    print(f"🌸 Kiyomi Build Script v{VERSION}")
    print(f"   Platform: {platform.system()} {platform.machine()}")
    print(f"   Python:   {sys.version.split()[0]}")

    os.chdir(Path(__file__).parent)

    if args.clean_only:
        clean()
        return

    if args.clean:
        clean()

    app_path = build_app()

    if not args.app:
        build_dmg(app_path)

    print(f"\n🎉 Build complete!")
    print(f"   App:  dist/{APP_NAME}.app")
    if not args.app:
        print(f"   DMG:  dist/{DMG_NAME}")


if __name__ == "__main__":
    main()
