#!/usr/bin/env python3
"""
图标构建脚本
=============

从 desktop/build/assets/ 的源图标生成各平台所需图标到 desktop/build/：
  - Windows: icon.ico（直接复制）
  - Linux:   icon.png（复制 512x512）
  - macOS:   icon.icns（用 iconutil，仅 mac 平台可执行）

同时复制一份 512 png 到 desktop/resources/icon.png，作为运行时托盘图标
（打包后由 extraResources 放入 Resources/，运行时 process.resourcesPath/icon.png）。

用法：python scripts/build_icons.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parent.parent / "desktop"
ASSETS = DESKTOP_ROOT / "build" / "assets"
BUILD = DESKTOP_ROOT / "build"
RESOURCES = DESKTOP_ROOT / "resources"


def build_ico_from_pngs() -> bool:
    """从 assets/ 下的多分辨率 PNG 合成 icon.ico，写入 build/ 和 assets/

    用 Pillow 编码（dev 依赖，见 pyproject dev extras）：小尺寸（<256）以
    32bit BMP 存储、主图 256 打底，是 Windows 最常见且兼容性最好的 ICO
    布局。此前"全部尺寸直接嵌 PNG 字节"的写法会被 electron-builder 注入
    exe 时丢弃 16px，导致系统通知/toast 的小图标回退成通用占位。
    """
    try:
        from PIL import Image
    except ImportError:
        print(
            "错误: 生成 icon.ico 需要 Pillow，请安装 dev 依赖后重试"
            "（pip install -e .[dev] 或 pip install Pillow）",
            file=sys.stderr,
        )
        return False

    sizes = [16, 32, 64, 128, 256]
    images: list[Image.Image] = []
    for size in sizes:
        path = ASSETS / f"icon_{size}x{size}.png"
        if not path.exists():
            print(f"警告: 缺少 {path.name}", file=sys.stderr)
            return False
        images.append(Image.open(path).convert("RGBA"))

    ico_path = BUILD / "icon.ico"
    # Pillow ICO 编码：sizes 声明目标尺寸，主图取最大尺寸（Pillow 只处理
    # <= 主图尺寸的 size），其余尺寸经 append_images 原样嵌入（尺寸一一
    # 对应不缩放）；bitmap_format="bmp" 让条目以 DIB 存储，避免 PNG 压缩
    # 的小尺寸条目在 electron-builder 注入 exe 时被丢弃。
    images[-1].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1],
        bitmap_format="bmp",
    )
    shutil.copy2(ico_path, ASSETS / "icon.ico")
    print(f"icon.ico ({len(images)} resolutions, {ico_path.stat().st_size} bytes)")
    return True


def build_menu_bar_template() -> bool:
    """生成 macOS 菜单栏托盘模板图标（透明底 + 纯黑形状）到 resources/。

    macOS 菜单栏图标的规范是 template image：黑色形状 + 透明背景，系统
    自动适配深/浅色菜单栏并随强调色渲染。规范要求同时提供 @1x 与 @2x
    两档（22px 与 44px，对应菜单栏 22pt 与视网膜屏 2x），Electron 按
    `@2x` 文件名约定自动选择高清档；44px 位数为图形提供充足细节，显示
    时与 22pt 物理像素一一对应，放大不糊。源图标为"黑底白形"，此处把
    白色形状提取为纯黑、黑色背景置为透明。
    """
    try:
        from PIL import Image
    except ImportError:
        print(
            "错误: 生成菜单栏模板图标需要 Pillow，请安装 dev 依赖后重试"
            "（pip install -e .[dev] 或 pip install Pillow）",
            file=sys.stderr,
        )
        return False

    src = ASSETS / "icon_1024x1024.png"
    if not src.exists():
        print(f"警告: 缺少 {src.name}，跳过菜单栏模板图标", file=sys.stderr)
        return False
    img = Image.open(src).convert("RGBA")
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            # 亮度高的白色图形部分 → 纯黑不透明；背景（黑）→ 透明
            if a > 128 and r > 128 and g > 128 and b > 128:
                px[x, y] = (0, 0, 0, 255)
            else:
                px[x, y] = (0, 0, 0, 0)
    # 先缩放到 44 再降采样 22，保证两档同源；resize 会引入半透明过渡，
    # template 也支持半透明黑
    hi = img.resize((44, 44), Image.Resampling.LANCZOS)
    lo = hi.resize((22, 22), Image.Resampling.LANCZOS)
    out_1x = RESOURCES / "iconTemplate.png"
    out_2x = RESOURCES / "iconTemplate@2x.png"
    lo.save(out_1x, format="PNG")
    hi.save(out_2x, format="PNG")
    print(
        f"iconTemplate.png ({lo.size[0]}x{lo.size[1]}, {out_1x.stat().st_size} bytes), "
        f"iconTemplate@2x.png ({hi.size[0]}x{hi.size[1]}, {out_2x.stat().st_size} bytes)"
    )
    return True


def main() -> None:
    if not ASSETS.exists():
        print(f"图标源目录不存在：{ASSETS}", file=sys.stderr)
        sys.exit(1)

    BUILD.mkdir(parents=True, exist_ok=True)
    RESOURCES.mkdir(parents=True, exist_ok=True)

    # --- Windows: 从多分辨率 PNG 合成 ico ---
    if not build_ico_from_pngs():
        # 回退：直接复制旧 ico
        ico_src = ASSETS / "icon.ico"
        if ico_src.exists():
            shutil.copy2(ico_src, BUILD / "icon.ico")
            print("icon.ico (fallback copy)")
        else:
            print("错误: 无法生成 icon.ico", file=sys.stderr)
            sys.exit(1)

    # --- macOS: 菜单栏模板图标（template image，透明底黑形） ---
    build_menu_bar_template()

    # --- Linux / 运行时托盘: 复制 512 png ---
    png512 = ASSETS / "icon_512x512.png"
    if png512.exists():
        shutil.copy2(png512, BUILD / "icon.png")
        shutil.copy2(png512, RESOURCES / "icon.png")
        shutil.copy2(png512, ASSETS / "icon.png")
        print("icon.png (build + resources + assets)")
    else:
        print("警告: 未找到源 icon_512x512.png", file=sys.stderr)

    # --- macOS: iconutil 生成 icns（仅 mac 平台可执行）---
    if sys.platform == "darwin":
        iconset = BUILD / "icon.iconset"
        if iconset.exists():
            shutil.rmtree(iconset)
        iconset.mkdir(parents=True)

        sizes = [16, 32, 128, 256, 512]
        for s in sizes:
            dest1 = iconset / f"icon_{s}x{s}.png"
            dest2 = iconset / f"icon_{s}x{s}@2x.png"
            src1 = ASSETS / f"icon_{s}x{s}.png"
            src2 = ASSETS / f"icon_{s * 2}x{s * 2}.png"
            if src1.exists():
                shutil.copy2(src1, dest1)
            if src2.exists():
                shutil.copy2(src2, dest2)
            elif src1.exists():
                # 回退：用 1x 充当 @2x
                shutil.copy2(src1, dest2)
                print(f"警告: icon_{s}x{s}@2x.png 缺失，用 {s}x{s} 回退", file=sys.stderr)

        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(BUILD / "icon.icns")],
            check=True,
        )
        shutil.rmtree(iconset)
        print("icon.icns")
    else:
        print("icon.icns 需在 macOS 上生成（CI mac runner 会处理）")


if __name__ == "__main__":
    main()
