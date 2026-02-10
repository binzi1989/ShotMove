"""
在 MERGED 目录下生成「视频 + 多 PNG 按时间 overlay」的 ffmpeg 命令。
用法（在 merged 目录或指定 MERGED 路径）:
  python build_cap_ffmpeg.py <视频名> [--title-dir title_pill_xxx] [--sub-dir sub_pill_xxx] [--out out_cap.mp4]
  python build_cap_ffmpeg.py seg_000_xfade_0c4dee.mp4 --title-dir title_pill_sm6p1dmo --sub-dir sub_pill_ebztof9g
PNG 文件名约定: pill_t_0.0_5.0_xxxx.png / pill_s_0.0_5.0_xxxx.png → start=0, end=5
"""
import argparse
import re
import os
import tempfile
from pathlib import Path


def parse_pill_times(path: Path) -> tuple[float, float] | None:
    """从 pill_*_start_end_*.png 解析 (start, end)。"""
    name = path.name
    # pill_t_0.0_5.0_484b.png 或 pill_s_0.0_5.0_4d43.png
    m = re.match(r"pill_[ts]?_?(\d+\.?\d*)_(\d+\.?\d*)_[a-f0-9]+\.png", name, re.I)
    if not m:
        return None
    return (float(m.group(1)), float(m.group(2)))


def collect_pills(merged_dir: Path, sub_dirs: list[str], title_dirs: list[str]):
    """
    收集 (相对路径, start, end, y_expr)。
    先所有 sub（底部 H-h-120），再所有 title（顶部居中 88），按 start 排序。
    """
    out: list[tuple[str, float, float, str]] = []
    for d in sub_dirs:
        folder = merged_dir / d
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            if f.suffix.lower() != ".png":
                continue
            t = parse_pill_times(f)
            if t:
                rel = str(f.relative_to(merged_dir)).replace("\\", "/")
                out.append((rel, t[0], t[1], "H-h-120"))
    for d in title_dirs:
        folder = merged_dir / d
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            if f.suffix.lower() != ".png":
                continue
            t = parse_pill_times(f)
            if t:
                rel = str(f.relative_to(merged_dir)).replace("\\", "/")
                out.append((rel, t[0], t[1], "88"))
    out.sort(key=lambda x: (x[1], x[2]))
    return out


def build_filter_complex(
    pngs_with_y: list[tuple[str, float, float, str]],
    enable_mode: str = "between_escaped",
) -> str:
    """生成 overlay 链，最后输出 [vout]。
    enable_mode: 'between_escaped' 写脚本文件时用 between(t\\,start\\,end)；'step' 用 step(t-s)*step(e-t)（曾导致 No such filter）。
    """
    if not pngs_with_y:
        return "[0:v]copy[vout]"
    parts = ["[0:v]copy[v0]"]
    prev = "[v0]"
    for i, (_, start, end, y_expr) in enumerate(pngs_with_y):
        inp_idx = i + 1
        if enable_mode == "step":
            en = f"step(t-{start})*step({end}-t)"
        else:
            # 脚本文件里逗号是滤镜分隔符，enable 内用 \, 转义
            en = f"'between(t\\,{start}\\,{end})'"
        out_label = "[vout]" if i == len(pngs_with_y) - 1 else f"[v{i+1}]"
        parts.append(
            f"{prev},[{inp_idx}:v]overlay=x=(W-w)/2:y={y_expr}:enable={en}{out_label}"
        )
        prev = out_label
    return ",".join(parts)


def main():
    ap = argparse.ArgumentParser(description="Build ffmpeg command for video + PNG overlays in merged dir")
    ap.add_argument("video", help="视频文件名（在 merged 目录下）")
    ap.add_argument("--merged-dir", default=None, help="MERGED 目录，默认当前目录")
    ap.add_argument("--title-dir", action="append", default=[], help="标题 pill 目录，可多次")
    ap.add_argument("--sub-dir", action="append", default=[], help="字幕 pill 目录，可多次")
    ap.add_argument("--out", default=None, help="输出文件名，默认 <视频名_base>_cap.mp4")
    ap.add_argument("--dry-run", action="store_true", help="只打印命令不执行")
    ap.add_argument("--filter-only-copy", action="store_true", help="调试：仅 [0:v]copy[vout]，不 overlay")
    ap.add_argument("--filter-one-overlay", action="store_true", help="调试：仅 1 个 overlay、enable=1，需至少一个 pill 目录")
    ap.add_argument("--all-enable-1", action="store_true", help="调试：完整 overlay 链但全部 enable=1（不按时段）")
    args = ap.parse_args()

    merged_dir = Path(args.merged_dir or os.getcwd()).resolve()
    video_path = merged_dir / args.video
    if not video_path.is_file():
        print(f"Error: video not found: {video_path}")
        return 1

    pngs = collect_pills(merged_dir, args.sub_dir or [], args.title_dir or [])
    if not args.filter_only_copy and not pngs:
        print("No PNG pills found. Use --title-dir and/or --sub-dir (e.g. title_pill_sm6p1dmo).")
        return 1

    base_name = Path(args.video).stem
    out_name = args.out or f"{base_name}_cap.mp4"
    if args.filter_only_copy:
        filter_complex_str = "[0:v]copy[vout]"
    elif args.filter_one_overlay and pngs:
        r, _, _, y = pngs[0]
        filter_complex_str = f"[0:v][1:v]overlay=x=(W-w)/2:y={y}:enable=1[vout]"
    elif args.all_enable_1 and pngs:
        # 完整 overlay 链但全部 enable=1，用于确认长链本身是否 OK
        parts = ["[0:v]copy[v0]"]
        prev = "[v0]"
        for i, (_, _s, _e, y_expr) in enumerate(pngs):
            inp_idx = i + 1
            out_label = "[vout]" if i == len(pngs) - 1 else f"[v{i+1}]"
            parts.append(f"{prev},[{inp_idx}:v]overlay=x=(W-w)/2:y={y_expr}:enable=1{out_label}")
            prev = out_label
        filter_complex_str = ",".join(parts)
    else:
        filter_complex_str = build_filter_complex(pngs, enable_mode="between_escaped")

    import subprocess

    # Windows 下多段 overlay 链会触发 No such filter: ''，改为多轮 ffmpeg：每轮只叠一张 PNG（单 overlay）
    use_multipass = not (args.filter_only_copy or args.filter_one_overlay or args.all_enable_1) and pngs and not args.dry_run

    if use_multipass:
        # 每轮: 当前视频 + 一张 PNG -> 单 overlay enable=between(t,s,e) -> 下一轮输入（绕过 Windows 多段链解析问题）
        print(f"Multipass: {len(pngs)} overlays (one ffmpeg per PNG)...")
        pid = os.getpid()
        temp_a = merged_dir / f"_cap_tmp_a_{pid}.mp4"
        temp_b = merged_dir / f"_cap_tmp_b_{pid}.mp4"
        try:
            current_in = args.video
            for i, (rel, start, end, y_expr) in enumerate(pngs):
                print(f"  overlay {i+1}/{len(pngs)}: {rel}")
                is_last = i == len(pngs) - 1
                current_out = out_name if is_last else (temp_b.name if i % 2 == 0 else temp_a.name)
                filt = f"[0:v][1:v]overlay=x=(W-w)/2:y={y_expr}:enable='between(t\\,{start}\\,{end})'[vout]"
                cmd = ["ffmpeg", "-y", "-i", current_in, "-i", rel, "-filter_complex", filt, "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", current_out]
                r = subprocess.run(cmd, cwd=str(merged_dir), timeout=300)
                if r.returncode != 0:
                    return r.returncode
                current_in = current_out
            return 0
        finally:
            for p in (temp_a, temp_b):
                if p.is_file():
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
        return 0

    # 调试模式或单次调用：用脚本文件或单次 -filter_complex
    script_path = None
    if not args.dry_run and not use_multipass:
        fd, script_path = tempfile.mkstemp(suffix=".txt", prefix="filter_", dir=str(merged_dir))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(filter_complex_str.encode("ascii"))
        except Exception:
            try:
                os.unlink(script_path)
            except Exception:
                pass
            script_path = None
    script_path = Path(script_path) if script_path and os.path.isfile(script_path) else None

    if script_path:
        cmd = ["ffmpeg", "-y", "-i", args.video]
        if not args.filter_only_copy:
            n_inputs = 1 if args.filter_one_overlay and pngs else len(pngs)
            for rel, *_ in pngs[:n_inputs]:
                cmd.extend(["-i", rel])
        cmd.extend([
            "-filter_complex_script", script_path.name,
            "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            out_name,
        ])
    else:
        cmd = ["ffmpeg", "-y", "-i", args.video]
        if not args.filter_only_copy:
            n_inputs = 1 if args.filter_one_overlay and pngs else len(pngs)
            for rel, *_ in pngs[:n_inputs]:
                cmd.extend(["-i", rel])
        cmd.extend([
            "-filter_complex", filter_complex_str,
            "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            out_name,
        ])

    print("Command (run from MERGED dir):")
    print(" ".join(f'"{x}"' if " " in x or "=" in x else x for x in cmd))
    print()

    if not args.dry_run:
        try:
            r = subprocess.run(cmd, cwd=str(merged_dir), timeout=600)
            return r.returncode
        finally:
            if script_path and script_path.is_file():
                try:
                    script_path.unlink(missing_ok=True)
                except Exception:
                    pass
    return 0


if __name__ == "__main__":
    exit(main() or 0)
