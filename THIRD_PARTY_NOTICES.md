# 第三方组件

- Python — PSF License；https://docs.python.org/3/license.html
- PySide6 / Qt for Python / Shiboken — LGPL-3.0 / GPL-3.0 / 商业许可选项；本项目采用 LGPL 组件和动态链接。https://doc.qt.io/qtforpython-6/licenses.html
- Pillow — HPND；https://github.com/python-pillow/Pillow
- platformdirs — MIT；https://github.com/tox-dev/platformdirs
- PyObjC（仅 Mac）— MIT；https://github.com/ronaldoussoren/pyobjc
- PyInstaller — GPL-2.0-or-later with bootloader exception；https://pyinstaller.org/en/stable/license.html

Qt 为独立动态库，并未静态链接。用于调试这些库修改的逆向工程不被项目限制。源码及版本见 pyproject.toml 和各上游链接，可通过 PyInstaller 重新构建来使用兼容的修改版本。程序包中保留依赖许可元数据；项目整体尚未指定开源许可，当前只作私人自用分发。

羽毛笔 SVG 由本项目绘制，配色参考 Flexoki：https://stephango.com/flexoki。
