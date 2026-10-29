#!/usr/bin/env python3
"""Applies the two changes the vocals-only feature needs in the app module itself.

They cannot live in core-patch/ (that only overlays the core submodule), so the
build applies them here and prints what it changed:

1. R8 keep rules for ai.onnxruntime. The release build has isMinifyEnabled = true,
   and R8 renames or strips ONNX Runtime's Java classes while the native library
   still resolves them by name — the first inference then dies with
   UnsatisfiedLinkError, which is an Error, not an Exception. That was the
   "crashes on vocals only" bug in the previous build.
2. android:largeHeap="true". The separator holds ~28 MB of buffers (two 8 MB
   complex spectra plus the overlap-add chain) on top of a 64 MB model session
   held by native code, inside an app that is already showing Compose artwork.
"""
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")

PROGUARD_RULES = """
# --- vocals-only (MDX-Net via ONNX Runtime) ---
# The native library looks these classes and methods up by name, so R8 must not
# rename, strip or inline anything under this package.
-keep class ai.onnxruntime.** { *; }
-keepclasseswithmembers class ai.onnxruntime.** { native <methods>; }
-keepclassmembers class ai.onnxruntime.** { <fields>; <methods>; }
-dontwarn ai.onnxruntime.**
"""


def app_overlay() -> None:
    """Copies app-overlay/ into the checkout (it is beside this repo's simpmusic folder)."""
    overlay = Path(__file__).resolve().parents[2] / "simpmusic-vocals-only" / "app-overlay"
    if not overlay.is_dir():
        print(f"[warn] no app overlay at {overlay}")
        return
    copied = 0
    for source in overlay.rglob("*"):
        if not source.is_file():
            continue
        target = ROOT / source.relative_to(overlay)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
        print(f"[ok] {target.relative_to(ROOT)}")
    print(f"[ok] copied {copied} app overlay file(s)")


def install_diagnostics() -> None:
    """Hooks the crash recorder into Application.onCreate, right after file logging starts."""
    path = ROOT / "androidApp" / "src" / "main" / "java" / "com" / "maxrave" / "simpmusic" / "SimpMusicApplication.kt"
    text = path.read_text()
    if "VocalsOnlyDiagnostics.install" in text:
        print(f"[ok] {path} already installs diagnostics")
        return
    anchor = 'Logger.enableFileLogging(File(filesDir, "logs").path)'
    if anchor not in text:
        raise SystemExit("could not find the file-logging line in SimpMusicApplication")
    text = text.replace(anchor, anchor + "\n        VocalsOnlyDiagnostics.install(this)", 1)
    path.write_text(text)
    print(f"[ok] installed VocalsOnlyDiagnostics in {path}")


def keep_rules() -> None:
    path = ROOT / "androidApp" / "proguard-rules.pro"
    text = path.read_text() if path.exists() else ""
    if "ai.onnxruntime" in text:
        print(f"[ok] {path} already keeps ONNX Runtime")
        return
    path.write_text(text.rstrip() + "\n" + PROGUARD_RULES)
    print(f"[ok] appended ONNX Runtime keep rules to {path}")


def large_heap() -> None:
    path = ROOT / "androidApp" / "src" / "main" / "AndroidManifest.xml"
    text = path.read_text()
    if 'android:largeHeap' in text:
        print(f"[ok] {path} already sets largeHeap")
        return
    # Insert right after the opening <application tag's name attribute line.
    match = re.search(r"<application\b[^>]*>", text)
    if not match:
        raise SystemExit("no <application> tag in the manifest")
    tag = match.group(0)
    if tag.endswith("/>"):
        new_tag = tag[:-2] + '\n        android:largeHeap="true" />'
    else:
        new_tag = tag[:-1] + '\n        android:largeHeap="true">'
    path.write_text(text.replace(tag, new_tag, 1))
    print(f"[ok] added android:largeHeap to {path}")


app_overlay()
install_diagnostics()
keep_rules()
large_heap()
