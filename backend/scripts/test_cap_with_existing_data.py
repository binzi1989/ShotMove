#!/usr/bin/env python3
"""
用 merged 目录下已有数据做「贴纸叠加 + 可选 BGM/配音混音」测试，不调用生成视频/BGM 接口，省成本。

用法:
  项目根(D:\\test)下:  python backend/scripts/test_cap_with_existing_data.py <视频名> ...
  已在 backend 下:     python scripts/test_cap_with_existing_data.py <视频名> ...

  python scripts/test_cap_with_existing_data.py seg_000_xfade_0c4dee.mp4
  python scripts/test_cap_with_existing_data.py seg_000_xfade_0c4dee.mp4 --no-mix
  python scripts/test_cap_with_existing_data.py seg_000_xfade_0c4dee.mp4 --rerender-pills   # 用最新样式重绘贴纸再叠加

- 视频：MERGED 目录下已存在的 .mp4（如拼接好的成片）
- 若未传 --title-dir/--sub-dir，会尝试自动发现 merged 下的 title_pill_*、sub_pill_* 目录
- --rerender-pills：按现有 pill 的 (start,end) 用当前代码重绘贴纸（占位文案），再叠加，便于看最新美化效果
- 先多轮 overlay 生成 <视频名_stem>_cap.mp4，再若存在 <stem>_voice.mp3 / <stem>_bgm.mp3 则混音得到 _cap_vo.mp4
"""
import os
import sys
import tempfile
from pathlib import Path

# 保证可 import app（app 在 backend 目录下）
_script_dir = Path(__file__).resolve().parent
_backend = _script_dir.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from app.services.video_concat import MERGED_DIR, mix_audio_into_merged
from app.services.video_post import (
    burn_pill_overlays_multipass,
    _ensure_drawtext_font,
    _render_title_pill_png,
)


def _parse_pill_times(path: Path) -> tuple[float, float] | None:
    import re
    name = path.name
    m = re.match(r"pill_[ts]?_?(\d+\.?\d*)_(\d+\.?\d*)_[a-f0-9]+\.png", name, re.I)
    if not m:
        return None
    return (float(m.group(1)), float(m.group(2)))


def _collect_pills(merged_dir: Path, sub_dirs: list[str], title_dirs: list[str]):
    """(path_abs, start, end, y_expr)，path 为绝对路径供 burn_pill_overlays_multipass。"""
    out: list[tuple[str, float, float, str]] = []
    for d in sub_dirs:
        folder = merged_dir / d
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            if f.suffix.lower() != ".png":
                continue
            t = _parse_pill_times(f)
            if t:
                out.append((str(f), t[0], t[1], "H-h-120"))
    for d in title_dirs:
        folder = merged_dir / d
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            if f.suffix.lower() != ".png":
                continue
            t = _parse_pill_times(f)
            if t:
                out.append((str(f), t[0], t[1], "88"))
    out.sort(key=lambda x: (x[1], x[2]))
    return out


def _discover_pill_dirs(merged_dir: Path) -> tuple[list[str], list[str]]:
    """扫描 merged 下的 title_pill_*、sub_pill_* 目录名。"""
    titles, subs = [], []
    for p in merged_dir.iterdir():
        if not p.is_dir():
            continue
        name = p.name
        if name.startswith("title_pill_"):
            titles.append(name)
        elif name.startswith("sub_pill_"):
            subs.append(name)
    return (titles, subs)


def _rerender_pills_with_current_style(
    merged_dir: Path,
    pills: list[tuple[str, float, float, str]],
    pill_style: str = "bubble_cream",
) -> list[tuple[str, float, float, str]]:
    """
    按 pills 的 (start, end, y_expr) 用当前 _render_title_pill_png 重绘贴纸（占位文案），
    返回新的 [(png_path_abs, start, end, y_expr), ...] 供叠加。文案用「标题」「字幕」占位。
    """
    font_rel = _ensure_drawtext_font()
    if not font_rel:
        print("Rerender pills: no font available (_ensure_drawtext_font failed), skip.")
        return pills
    font_abs = merged_dir / font_rel
    if not font_abs.is_file():
        print(f"Rerender pills: font not found {font_abs}, skip.")
        return pills
    title_fs = 72
    sub_fs = 48
    title_dir = Path(tempfile.mkdtemp(prefix="title_pill_retest_", dir=str(merged_dir)))
    sub_dir = Path(tempfile.mkdtemp(prefix="sub_pill_retest_", dir=str(merged_dir)))
    out: list[tuple[str, float, float, str]] = []
    for path_abs, start, end, y_expr in pills:
        is_title = y_expr.strip() == "88"
        text = "标题" if is_title else "字幕"
        fs = title_fs if is_title else sub_fs
        use_bold = is_title
        folder = title_dir if is_title else sub_dir
        prefix = "pill_t" if is_title else "pill_s"
        fname = f"{prefix}_{start:.1f}_{end:.1f}_{os.urandom(2).hex()}.png"
        out_path = folder / fname
        if _render_title_pill_png(
            text, pill_style, font_abs, fs, out_path, use_bold=use_bold
        ):
            out.append((str(out_path.resolve()), start, end, y_expr))
    out.sort(key=lambda x: (x[1], x[2]))
    if out:
        print(f"Rerendered {len(out)} pills (style={pill_style}) -> {title_dir.name}, {sub_dir.name}")
    return out if out else pills


def main():
    import argparse
    ap = argparse.ArgumentParser(description="用已有 merged 数据测试贴纸+混音，不生成新视频/BGM")
    ap.add_argument("video", help="merged 下已有视频文件名，如 seg_000_xfade_0c4dee.mp4")
    ap.add_argument("--merged-dir", default=None, help="MERGED 目录，默认 app.services.video_concat.MERGED_DIR")
    ap.add_argument("--title-dir", action="append", default=[], help="标题 pill 目录")
    ap.add_argument("--sub-dir", action="append", default=[], help="字幕 pill 目录")
    ap.add_argument("--no-mix", action="store_true", help="只做 overlay，不混 BGM/配音")
    ap.add_argument("--rerender-pills", action="store_true", help="用当前样式重绘贴纸再叠加（占位文案），看最新美化效果")
    ap.add_argument("--pill-style", default="bubble_yellow", help="--rerender-pills 时使用的气泡样式 (默认 bubble_yellow 黄底白字)")
    args = ap.parse_args()

    merged_dir = Path(args.merged_dir or str(MERGED_DIR)).resolve()
    video_path = merged_dir / args.video
    if not video_path.is_file():
        print(f"Error: video not found: {video_path}")
        existing = sorted(p.name for p in merged_dir.iterdir() if p.suffix.lower() == ".mp4")
        if existing:
            print(f"Available .mp4 in merged: {', '.join(existing[:15])}{' ...' if len(existing) > 15 else ''}")
        else:
            print(f"No .mp4 files in {merged_dir}")
        return 1

    title_dirs = args.title_dir or []
    sub_dirs = args.sub_dir or []
    if not title_dirs and not sub_dirs:
        found_t, found_s = _discover_pill_dirs(merged_dir)
        if found_t:
            title_dirs = [found_t[0]]
            print(f"Auto-discovered title dir: {title_dirs[0]}")
        if found_s:
            sub_dirs = [found_s[0]]
            print(f"Auto-discovered sub dir: {sub_dirs[0]}")
        if not title_dirs and not sub_dirs:
            print("No pill dirs given and none found (title_pill_*, sub_pill_*). Exiting.")
            return 1

    pngs = _collect_pills(merged_dir, sub_dirs, title_dirs)
    if not pngs:
        print("No PNG pills found in given dirs.")
        return 1

    if args.rerender_pills:
        pngs = _rerender_pills_with_current_style(merged_dir, pngs, args.pill_style)
        if not pngs:
            return 1

    stem = video_path.stem
    out_cap = f"{stem}_cap{video_path.suffix}"
    api_path = f"/api/merged/{args.video}"

    print(f"Step 1: overlay {len(pngs)} pills -> {out_cap}")
    new_url = burn_pill_overlays_multipass(api_path, pngs, out_cap)
    if not new_url:
        print("Overlay failed.")
        return 1
    print(f"  -> {out_cap} OK")

    if args.no_mix:
        print("Skip mix (--no-mix). Done.")
        return 0

    cap_api = f"/api/merged/{out_cap}"
    voice_path = merged_dir / f"{stem}_voice.mp3"
    bgm_path = merged_dir / f"{stem}_bgm.mp3"
    if not voice_path.is_file() and not bgm_path.is_file():
        print("No _voice.mp3 / _bgm.mp3 found, skip mix.")
        return 0
    voice = str(voice_path) if voice_path.is_file() else None
    bgm = str(bgm_path) if bgm_path.is_file() else None
    if not voice:
        voice = bgm
        bgm = None
    print(f"Step 2: mix audio -> {stem}_cap_vo.mp4")
    vo_url = mix_audio_into_merged(cap_api, voice, bgm)
    if vo_url:
        print(f"  -> {stem}_cap_vo.mp4 OK")
    else:
        print("  Mix failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
