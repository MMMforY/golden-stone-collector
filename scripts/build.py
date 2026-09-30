"""Native builds only. Run with the Python version used for dependency installation."""
import hashlib
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from golden_stone_collector import __version__


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtGui import QImage,QPainter
    from PySide6.QtCore import Qt,QLibraryInfo
    from PIL import Image
    app=QApplication.instance() or QApplication([])
    build=ROOT/"build"
    build.mkdir(exist_ok=True)
    raster=QImage(1024,1024,QImage.Format.Format_ARGB32)
    raster.fill(Qt.GlobalColor.transparent)
    painter=QPainter(raster)
    QSvgRenderer(str(ROOT/"src/golden_stone_collector/assets/feather.svg")).render(painter)
    painter.end()
    raster.save(str(build/"feather.png"))
    mac=sys.platform=="darwin"
    extension="icns" if mac else "ico"
    with Image.open(build/"feather.png") as source:
        source.save(build/f"feather.{extension}")
    command=[sys.executable,"-m","PyInstaller","--noconfirm","--clean","--onedir","--windowed",
        "--name","GoldenStoneCollector","--paths",str(ROOT/"src"),"--specpath",str(build),
        "--icon",str(build/f"feather.{extension}"),"--add-data",str(ROOT/"src/golden_stone_collector/assets")+":golden_stone_collector/assets",
        "--hidden-import","PySide6.QtSvg","--exclude-module","tkinter","--exclude-module","pytest"]
    translation=Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))/"qtbase_zh_CN.qm"
    command += ["--add-data",str(translation)+":golden_stone_collector/assets"]
    if mac:
        if platform.machine()!="arm64":
            raise RuntimeError("Mac 包需要 Apple Silicon arm64 runner。")
        os.environ["MACOSX_DEPLOYMENT_TARGET"]="14.0"
        command += ["--target-architecture","arm64","--osx-bundle-identifier","com.goldenstone.collector",
                    "--hidden-import","AppKit","--hidden-import","Foundation"]
    command.append(str(ROOT/"scripts/launcher.py"))
    build_environment = os.environ.copy()
    if not mac:
        # Do not bundle unrelated ICU/OpenSSL DLLs from Git, WPS, or other PATH
        # entries. Qt uses Windows' own ICU; a same-name Git ICU has a different ABI.
        import PySide6
        system_root = Path(os.environ.get("SystemRoot", "C:/Windows"))
        build_environment["PATH"] = os.pathsep.join(map(str,[Path(sys.executable).parent,
            Path(PySide6.__file__).parent, system_root/"System32", system_root]))
    subprocess.run(command,cwd=ROOT,check=True,env=build_environment)
    dist=ROOT/"dist"
    app_path=dist/("GoldenStoneCollector.app" if mac else "GoldenStoneCollector")
    guide=app_path/"Contents/Resources/使用说明" if mac else app_path/"使用说明"
    guide.mkdir(parents=True,exist_ok=True)
    for document in ("README.md","THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT/document,guide/document)
    for directory in ("docs","templates","licenses"):
        shutil.copytree(ROOT/directory,guide/directory,dirs_exist_ok=True)
    from importlib.metadata import distribution,PackageNotFoundError
    for name in ("Pillow","platformdirs","PySide6","PySide6_Essentials","shiboken6","pyobjc-core","pyobjc-framework-Cocoa"):
        try:
            package=distribution(name)
        except PackageNotFoundError:
            continue
        for entry in package.files or []:
            if ".dist-info/licenses/" in str(entry).replace("\\","/"):
                target=guide/"licenses"/name/Path(str(entry)).name
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(package.locate_file(entry),target)
    if mac:
        import plistlib
        info=app_path/"Contents/Info.plist"
        data=plistlib.loads(info.read_bytes())
        data.update(CFBundleShortVersionString=__version__,CFBundleVersion=__version__,LSMinimumSystemVersion="14.0",CFBundleDisplayName="Golden Stone 收集器")
        info.write_bytes(plistlib.dumps(data))
        subprocess.run(["codesign","--force","--deep","--sign","-",str(app_path)],check=True)
        executable=app_path/"Contents/MacOS/GoldenStoneCollector"
    else:
        executable=app_path/"GoldenStoneCollector.exe"
    report=dist/("smoke-macos-arm64.json" if mac else "smoke-windows-x64.json")
    subprocess.run([str(executable),"--smoke-test",str(report)],check=True,timeout=90)
    label="macos-arm64" if mac else "windows-x64"
    bundle=f"GoldenStoneCollector-{__version__}-{label}"
    if mac:
        archive=dist/(bundle+".zip")
        subprocess.run(["ditto","-c","-k","--sequesterRsrc","--keepParent",str(app_path),str(archive)],check=True)
    else:
        archive=Path(shutil.make_archive(str(dist/bundle),"zip",dist,app_path.name))
    with archive.open("rb") as handle:
        sha=hashlib.file_digest(handle,"sha256").hexdigest()
    (dist/(bundle+".sha256")).write_text(sha+"  "+archive.name+"\n",encoding="ascii")
    print(str(archive))


if __name__=="__main__":
    main()
