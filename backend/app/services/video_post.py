"""成片后期：转场拼接、字幕（drawtext 滤镜脚本）与花纸/氛围层叠加（ffmpeg）。"""
from __future__ import annotations

import logging
import os
import random
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from typing import Literal

from app.services.video_concat import MERGED_DIR, _ensure_merged_dir

logger = logging.getLogger(__name__)

FONTS_DIR = MERGED_DIR / "fonts"
# Windows 系统字体，用于复制到 MERGED_DIR/fonts 供 drawtext 使用（相对路径避免盘符问题）
WINDOWS_FONT_CANDIDATES = ["msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc"]


def _ffprobe_duration_sec(path: str) -> float | None:
    """返回视频/音频时长（秒）；失败返回 None。需要 ffprobe。"""
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None
        s = (r.stdout or "").strip()
        return float(s) if s else None
    except Exception:
        return None


def _ffprobe_video_size(path: str) -> tuple[int | None, int | None]:
    """返回 (width, height)；失败或非视频返回 (None, None)。"""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if r.returncode != 0 or not (r.stdout or "").strip():
            return (None, None)
        parts = (r.stdout or "").strip().split(",")
        if len(parts) >= 2:
            return (int(parts[0]), int(parts[1]))
        return (None, None)
    except Exception:
        return (None, None)


# 短剧配音轨：与视频分镜一一对应，无台词镜头用静音填充；TTS 输出 24kHz
TTS_SAMPLE_RATE = 24000


def build_voice_track_from_segments(
    segments: list[tuple[str | None, float]],
    out_path: str | Path,
    align_mode: Literal["time_stretch", "pad_trim"] = "time_stretch",
) -> str | None:
    """
    将每镜的配音（TTS 文件路径或 None 表示静音）按时长裁切/填充后拼接成一条完整音轨。
    segments: [(audio_mp3_path or None, duration_sec), ...]
    align_mode:
      - time_stretch: 尝试用 atempo 在合理范围内将 TTS 伸缩到镜头时长（旧行为，适合镜头时长已固定时）
      - pad_trim: 不做语速伸缩，仅裁切或静音填充（适合「镜头时长已按配音对齐」的流程，保证语速自然）
    返回 out_path 的字符串形式；失败返回 None。
    """
    if not segments:
        return None
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="drama_voice_")
    segment_files: list[str] = []
    try:
        for i, (audio_path, duration_sec) in enumerate(segments):
            if duration_sec <= 0:
                duration_sec = 0.5
            seg_file = os.path.join(temp_dir, f"seg_{i:03d}.mp3")
            if audio_path and os.path.isfile(audio_path):
                current = _ffprobe_duration_sec(audio_path)
                if current is None or current <= 0:
                    current = 0.0
                if align_mode == "pad_trim":
                    # 不改变语速：只裁切或用静音补齐
                    if current >= duration_sec:
                        r = subprocess.run(
                            [
                                "ffmpeg",
                                "-y",
                                "-i",
                                audio_path,
                                "-t",
                                str(duration_sec),
                                "-acodec",
                                "libmp3lame",
                                "-q:a",
                                "5",
                                seg_file,
                            ],
                            capture_output=True,
                            timeout=30,
                        )
                    else:
                        r = subprocess.run(
                            [
                                "ffmpeg",
                                "-y",
                                "-i",
                                audio_path,
                                "-af",
                                f"apad=whole_dur={duration_sec}",
                                "-t",
                                str(duration_sec),
                                "-acodec",
                                "libmp3lame",
                                "-q:a",
                                "5",
                                seg_file,
                            ],
                            capture_output=True,
                            timeout=30,
                        )
                else:
                    # 与镜头时长对齐：优先用 atempo 将 TTS 伸缩到 segment 时长，避免裁断或长段静音
                    # atempo 为速度倍率，输出时长 = current/atempo，故 atempo = current/duration_sec
                    ATEMPO_MIN, ATEMPO_MAX = 0.5, 2.0
                    ratio = (current / duration_sec) if (duration_sec > 0 and current > 0) else 1.0
                    if 0.95 <= ratio <= 1.05:
                        # 几乎一致，直接裁切或微补
                        if current >= duration_sec:
                            r = subprocess.run(
                                [
                                    "ffmpeg",
                                    "-y",
                                    "-i",
                                    audio_path,
                                    "-t",
                                    str(duration_sec),
                                    "-acodec",
                                    "copy",
                                    seg_file,
                                ],
                                capture_output=True,
                                timeout=30,
                            )
                        else:
                            r = subprocess.run(
                                [
                                    "ffmpeg",
                                    "-y",
                                    "-i",
                                    audio_path,
                                    "-af",
                                    f"apad=whole_dur={duration_sec}",
                                    "-t",
                                    str(duration_sec),
                                    "-acodec",
                                    "libmp3lame",
                                    "-q:a",
                                    "5",
                                    seg_file,
                                ],
                                capture_output=True,
                                timeout=30,
                            )
                    elif ATEMPO_MIN <= ratio <= ATEMPO_MAX:
                        # 用 atempo 伸缩到镜头时长，使 TTS 与时间节点高度匹配（输出时长 = current/ratio）
                        r = subprocess.run(
                            [
                                "ffmpeg",
                                "-y",
                                "-i",
                                audio_path,
                                "-af",
                                f"atempo={ratio}",
                                "-t",
                                str(duration_sec),
                                "-acodec",
                                "libmp3lame",
                                "-q:a",
                                "5",
                                seg_file,
                            ],
                            capture_output=True,
                            timeout=30,
                        )
                    else:
                        # 超出合理伸缩范围则裁切或静音填充
                        if current >= duration_sec:
                            r = subprocess.run(
                                [
                                    "ffmpeg",
                                    "-y",
                                    "-i",
                                    audio_path,
                                    "-t",
                                    str(duration_sec),
                                    "-acodec",
                                    "copy",
                                    seg_file,
                                ],
                                capture_output=True,
                                timeout=30,
                            )
                        else:
                            r = subprocess.run(
                                [
                                    "ffmpeg",
                                    "-y",
                                    "-i",
                                    audio_path,
                                    "-af",
                                    f"apad=whole_dur={duration_sec}",
                                    "-t",
                                    str(duration_sec),
                                    "-acodec",
                                    "libmp3lame",
                                    "-q:a",
                                    "5",
                                    seg_file,
                                ],
                                capture_output=True,
                                timeout=30,
                            )
                if r.returncode == 0 and os.path.isfile(seg_file):
                    segment_files.append(seg_file)
                else:
                    subprocess.run([
                        "ffmpeg", "-y", "-f", "lavfi", "-i",
                        f"anullsrc=r={TTS_SAMPLE_RATE}:cl=1", "-t", str(duration_sec),
                        "-acodec", "libmp3lame", "-q:a", "5", seg_file,
                    ], capture_output=True, timeout=30)
                    if os.path.isfile(seg_file):
                        segment_files.append(seg_file)
            else:
                r = subprocess.run([
                    "ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"anullsrc=r={TTS_SAMPLE_RATE}:cl=1", "-t", str(duration_sec),
                    "-acodec", "libmp3lame", "-q:a", "5", seg_file,
                ], capture_output=True, timeout=30)
                if os.path.isfile(seg_file):
                    segment_files.append(seg_file)
        if not segment_files:
            return None
        list_file = os.path.join(temp_dir, "concat_list.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for p in segment_files:
                esc = p.replace("\\", "\\\\").replace("'", "\\'")
                f.write(f"file '{esc}'\n")
        r = subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy", str(out_path),
        ], capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not out_path.exists():
            logger.warning("build_voice_track_from_segments ffmpeg concat failed: %s", (r.stderr or "")[:500])
            return None
        return str(out_path)
    except Exception as e:
        logger.warning("build_voice_track_from_segments exception: %s", e)
        return None
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def _api_to_local_path(merged_api_path: str) -> Path | None:
    if not merged_api_path or not merged_api_path.strip().startswith("/api/merged/"):
        return None
    name = merged_api_path.strip().replace("/api/merged/", "").strip("/")
    if not name or ".." in name:
        return None
    p = MERGED_DIR / name
    return p if p.exists() else None


def _local_to_api_path(local_path: Path) -> str:
    return f"/api/merged/{local_path.name}"


def _ensure_drawtext_font() -> str | None:
    """
    确保 MERGED_DIR/fonts/ 下有中文字体，供 drawtext 使用（相对路径 fonts/xxx.ttc 避免 Windows 盘符问题）。
    优先从 Windows Fonts 复制；也可设置环境变量 SUBTITLE_FONT_FILE 指向已存在的字体路径（会复制到 fonts/）。
    Linux 下会尝试 /usr/share/fonts 等常见路径。返回相对路径如 'fonts/msyh.ttc'，失败返回 None。
    """
    _ensure_merged_dir()
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    env_font = os.getenv("SUBTITLE_FONT_FILE", "").strip()
    if env_font and os.path.isfile(env_font):
        dest = FONTS_DIR / Path(env_font).name
        try:
            shutil.copy2(env_font, dest)
            return f"fonts/{dest.name}"
        except Exception as e:
            logger.warning("copy SUBTITLE_FONT_FILE to fonts dir failed: %s", e)
    # Windows: 从系统字体目录复制
    system_root = os.environ.get("SystemRoot", "C:\\Windows")
    if Path(system_root).is_dir():
        for name in WINDOWS_FONT_CANDIDATES:
            src = Path(system_root) / "Fonts" / name
            if src.is_file():
                dest = FONTS_DIR / name
                try:
                    shutil.copy2(src, dest)
                    return f"fonts/{name}"
                except Exception as e:
                    logger.warning("copy %s to fonts dir failed: %s", name, e)
                    continue
    # Linux/其他: 尝试常见字体路径（取第一个可复制的）
    for prefix in ("/usr/share/fonts/truetype", "/usr/share/fonts/TTF", "/usr/share/fonts"):
        try:
            prefix_path = Path(prefix)
            if not prefix_path.is_dir():
                continue
            for f in prefix_path.rglob("*.ttf"):
                if f.is_file():
                    dest = FONTS_DIR / f.name
                    try:
                        shutil.copy2(f, dest)
                        return f"fonts/{dest.name}"
                    except Exception as e:
                        logger.warning("copy %s to fonts dir failed: %s", f.name, e)
                    break
            for f in prefix_path.rglob("*.ttc"):
                if f.is_file():
                    dest = FONTS_DIR / f.name
                    try:
                        shutil.copy2(f, dest)
                        return f"fonts/{dest.name}"
                    except Exception as e:
                        logger.warning("copy %s to fonts dir failed: %s", f.name, e)
                    break
        except Exception:
            continue
    # 若已有 fonts 下任意 ttf/ttc 也可用
    try:
        for f in FONTS_DIR.iterdir():
            if f.suffix.lower() in (".ttf", ".ttc", ".otf"):
                return f"fonts/{f.name}"
    except Exception:
        pass
    logger.warning("no drawtext font found in MERGED_DIR/fonts, Windows Fonts, or /usr/share/fonts")
    return None


def _escape_drawtext(s: str) -> str:
    """drawtext 的 text= 内需转义反斜杠和单引号；换行改为空格避免破坏 filter 语法。"""
    s = " ".join(s.split())
    return s.replace("\\", "\\\\").replace("'", "\\'")


# 标题花字样式：剪映风格柔和气泡 / 深色底经典。FFmpeg drawtext 使用 fontcolor/boxcolor 等。
@dataclass
class TitleCaptionStyle:
    fontcolor: str
    bordercolor: str
    borderw: int
    box: int
    boxcolor: str
    boxborderw: int


# 多组标题样式，可选：柔和气泡（浅底+深色字）、经典（白字+深色底）
TITLE_CAPTION_STYLES: dict[str, TitleCaptionStyle] = {
    # 剪映风格：奶白/米色气泡 + 深色字，不突兀
    "bubble_cream": TitleCaptionStyle(
        fontcolor="0x333333",
        bordercolor="0x333333",
        borderw=0,
        box=1,
        boxcolor="0xF5E6D6@0.95",
        boxborderw=14,
    ),
    # 淡薄荷气泡
    "bubble_mint": TitleCaptionStyle(
        fontcolor="0x2d4a3e",
        bordercolor="0x2d4a3e",
        borderw=0,
        box=1,
        boxcolor="0xE0F0E8@0.95",
        boxborderw=14,
    ),
    # 淡粉气泡
    "bubble_pink": TitleCaptionStyle(
        fontcolor="0x4a3d4d",
        bordercolor="0x4a3d4d",
        borderw=0,
        box=1,
        boxcolor="0xF0E6F4@0.95",
        boxborderw=14,
    ),
    # 淡青气泡
    "bubble_sky": TitleCaptionStyle(
        fontcolor="0x2d404a",
        bordercolor="0x2d404a",
        borderw=0,
        box=1,
        boxcolor="0xE0F4F4@0.95",
        boxborderw=14,
    ),
    # 极简：半透明白底 + 深色字
    "bubble_soft": TitleCaptionStyle(
        fontcolor="0x333333",
        bordercolor="0x333333",
        borderw=0,
        box=1,
        boxcolor="0xFFFFFF@0.88",
        boxborderw=12,
    ),
    # 黄底白字（图1样式）
    "bubble_yellow": TitleCaptionStyle(
        fontcolor="0xFFFFFF",
        bordercolor="0xFFFFFF",
        borderw=0,
        box=1,
        boxcolor="0xFFC107@0.95",
        boxborderw=14,
    ),
    # 经典（原样式）：白字 + 黑描边 + 深色半透明底
    "classic": TitleCaptionStyle(
        fontcolor="white",
        bordercolor="black",
        borderw=3,
        box=1,
        boxcolor="black@0.55",
        boxborderw=8,
    ),
}


def _get_title_caption_style(name: str, seed: str | None = None) -> TitleCaptionStyle:
    """按名称或随机返回标题样式；无效名称回退到 bubble_yellow（黄底白字）。"""
    if name in TITLE_CAPTION_STYLES:
        return TITLE_CAPTION_STYLES[name]
    # 可选：按 seed 在气泡样式里随机
    bubble_only = ["bubble_yellow", "bubble_cream", "bubble_mint", "bubble_pink", "bubble_sky", "bubble_soft"]
    if seed:
        rnd = random.Random(seed)
        return TITLE_CAPTION_STYLES[rnd.choice(bubble_only)]
    return TITLE_CAPTION_STYLES["bubble_yellow"]


# 圆角/药丸形标题 PNG 用色（RGB）：(背景, 文字)，仅用于非 classic 的 bubble 样式
# 花字样式：柔和背景 + 轻微阴影 + 细描边；bubble_yellow 为图1样式黄底白字
TITLE_PILL_COLOURS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "bubble_cream": ((253, 246, 238), (45, 42, 38)),
    "bubble_mint": ((232, 248, 242), (42, 72, 62)),
    "bubble_pink": ((250, 242, 252), (72, 58, 82)),
    "bubble_sky": ((235, 250, 250), (42, 64, 74)),
    "bubble_soft": ((255, 255, 255), (42, 42, 42)),
    "bubble_yellow": ((255, 193, 7), (255, 255, 255)),  # 黄底白字，图1样式
}


def _title_pill_bold_font(font_path: Path, font_size: int):
    """优先加载粗体：同目录/系统字体 msyhbd、SimHei 等，或 TTC index=1。"""
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    font_path = Path(font_path)
    stem, suffix = font_path.stem.lower(), font_path.suffix.lower()
    # 1) 同目录下常见粗体
    for candidate in (
        font_path.parent / f"{stem}bd{suffix}",
        font_path.parent / f"{stem}_bold{suffix}",
        font_path.parent / "msyhbd.ttc",
        font_path.parent / "simhei.ttf",
    ):
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), font_size)
            except Exception:
                continue
    # 2) Windows 系统字体（标题花字粗体）
    try:
        import platform
        if platform.system() == "Windows":
            for sys_font in ("msyhbd.ttc", "simhei.ttf", "simheib.ttf"):
                p = Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts" / sys_font
                if p.is_file():
                    try:
                        return ImageFont.truetype(str(p), font_size)
                    except Exception:
                        continue
    except Exception:
        pass
    # 3) TTC 多数 index=0 常规、1 粗体
    if suffix == ".ttc" and font_path.is_file():
        try:
            return ImageFont.truetype(str(font_path), font_size, index=1)
        except Exception:
            pass
    return None


def _render_title_pill_png(
    text: str,
    style_name: str,
    font_path: str | Path,
    font_size: int,
    out_path: str | Path,
    padding: int = 28,
    use_bold: bool = True,
) -> bool:
    """
    用 Pillow 绘制圆角药丸形气泡 + 文字，保存为 PNG（透明底，气泡+字不透明）。
    只调整贴纸形态与样式（阴影、气泡描边），不改变字体字重/描边。style_name 须在 TITLE_PILL_COLOURS 中。
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("Pillow not installed, cannot render pill title PNG")
        return False
    bg_rgb, text_rgb = TITLE_PILL_COLOURS.get(
        style_name, TITLE_PILL_COLOURS.get(style_name, TITLE_PILL_COLOURS["bubble_yellow"])
    )
    font_path = Path(font_path)
    if not font_path.is_file():
        logger.warning("_render_title_pill_png: font not found %s", font_path)
        return False
    # 只改贴纸形态与样式，不改字体（统一常规字重，不加粗、不加重字描边）
    try:
        font = ImageFont.truetype(str(font_path), font_size)
    except Exception as e:
        logger.warning("_render_title_pill_png: font load failed %s", e)
        return False
    # 先测文字宽高（textbbox 自 Pillow 8.0；更早版本用 textsize 或近似）
    dummy = Image.new("RGBA", (1, 1))
    draw_temp = ImageDraw.Draw(dummy)
    try:
        bbox = draw_temp.textbbox((0, 0), text, font=font)
    except (AttributeError, TypeError):
        try:
            tw, th = draw_temp.textsize(text, font=font)
            bbox = (0, 0, max(1, tw), max(1, th))
        except Exception:
            bbox = (0, 0, max(1, len(text) * font_size), font_size)
    except Exception:
        bbox = (0, 0, max(1, len(text) * font_size), font_size)
    tw = max(1, bbox[2] - bbox[0])
    th = max(1, bbox[3] - bbox[1])
    # 稍大一点 padding，花字更舒展、不死板
    pad = max(padding, font_size // 3)
    w = tw + 2 * pad
    h = th + 2 * pad
    radius = min(w, h) // 2
    # 贴纸形态/样式：轻微阴影 + 细描边（只改气泡，不改字）
    shadow_off = max(3, font_size // 22)
    canvas_w, canvas_h = w + 2 * shadow_off, h + 2 * shadow_off
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 1) 贴纸阴影：柔和、不抢眼
    shadow_rgb = (35, 35, 40)
    draw.rounded_rectangle(
        (shadow_off + 1, shadow_off + 1, shadow_off + w, shadow_off + h),
        radius=radius,
        fill=(*shadow_rgb, 50),
        outline=None,
    )
    # 2) 主气泡：圆角药丸形 + 细描边（形态与样式）
    outline_rgb = tuple(max(0, c - 40) for c in text_rgb)
    outline_width = max(1, min(2, font_size // 28))
    try:
        draw.rounded_rectangle(
            (shadow_off, shadow_off, shadow_off + w - 1, shadow_off + h - 1),
            radius=radius,
            fill=(*bg_rgb, 250),
            outline=(*outline_rgb, 175),
            width=outline_width,
        )
    except TypeError:
        draw.rounded_rectangle(
            (shadow_off, shadow_off, shadow_off + w - 1, shadow_off + h - 1),
            radius=radius,
            fill=(*bg_rgb, 250),
            outline=(*outline_rgb, 175),
        )
    # 3) 文字：不加重描边，保持干净（只调贴纸不调字）
    x = shadow_off + (w - tw) // 2 - bbox[0]
    y = shadow_off + (h - th) // 2 - bbox[1]
    stroke_w = max(1, font_size // 32)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
        for s in range(1, stroke_w + 1):
            draw.text((x + dx * s, y + dy * s), text, font=font, fill=(*outline_rgb, 150))
    draw.text((x, y), text, font=font, fill=(*text_rgb, 255))
    img.save(out_path, "PNG")
    return True


def _render_title_caption_pngs(
    storyboard: list[dict],
    title_style: str,
    video_w: int,
    video_h: int,
    font_path: str | Path,
    default_dur_sec: int = 5,
    max_items: int | None = None,
) -> list[tuple[str, float, float]]:
    """
    按分镜生成每条标题（画面上方）的圆角气泡 PNG，返回 [(png_path, start_sec, end_sec), ...]。
    若 Pillow 不可用或样式为 classic 则返回 []。
    """
    if title_style == "classic":
        return []
    _, title_lines = _storyboard_to_timeline(storyboard, default_dur_sec, max_items)
    if not title_lines:
        return []
    try:
        title_fs = int(os.getenv("TITLE_FONT_SIZE", "72"))
    except Exception:
        title_fs = 72
    title_fs = max(52, min(120, title_fs))
    font_path = Path(font_path)
    if not font_path.is_file():
        return []
    out_dir = tempfile.mkdtemp(prefix="title_pill_", dir=str(MERGED_DIR))
    result: list[tuple[str, float, float]] = []
    for start, end, text in title_lines:
        if not text:
            continue
        fname = f"pill_t_{start:.1f}_{end:.1f}_{os.urandom(2).hex()}.png"
        path = os.path.join(out_dir, fname)
        if _render_title_pill_png(text, title_style, font_path, title_fs, path):
            result.append((path, start, end))
    return result


def _render_subtitle_caption_pngs(
    storyboard: list[dict],
    title_style: str,
    video_w: int,
    video_h: int,
    font_path: str | Path,
    default_dur_sec: int = 5,
    max_items: int | None = None,
    skip_narration_caption: bool = False,
) -> list[tuple[str, float, float]]:
    """
    按分镜生成每条底部字幕（口播/对白）的圆角气泡 PNG，返回 [(png_path, start_sec, end_sec), ...]。
    与标题同款气泡样式，字号略小，供叠加在画面底部。
    skip_narration_caption=True 时仅对话加字幕，旁白不加。
    """
    if title_style == "classic":
        return []
    lines, _ = _storyboard_to_timeline(
        storyboard, default_dur_sec, max_items, skip_narration_caption=skip_narration_caption
    )
    if not lines:
        return []
    try:
        sub_fs = int(os.getenv("TITLE_FONT_SIZE", "72")) - 24
    except Exception:
        sub_fs = 48
    sub_fs = max(36, min(72, sub_fs))
    font_path = Path(font_path)
    if not font_path.is_file():
        return []
    out_dir = tempfile.mkdtemp(prefix="sub_pill_", dir=str(MERGED_DIR))
    result: list[tuple[str, float, float]] = []
    for start, end, text in lines:
        if not text:
            continue
        # 长句截断，避免气泡过宽
        text = (text[:24] + "…") if len(text) > 24 else text
        fname = f"pill_s_{start:.1f}_{end:.1f}_{os.urandom(2).hex()}.png"
        path = os.path.join(out_dir, fname)
        if _render_title_pill_png(text, title_style, font_path, sub_fs, path, use_bold=False):
            result.append((path, start, end))
    return result


def _is_narration_text(text: str) -> bool:
    """判断是否为旁白（旁白不加字幕时可跳过）。"""
    if not text:
        return False
    t = text.strip()
    return t.startswith("旁白") or t.startswith("旁白：") or t.startswith("旁白:")


def _storyboard_to_timeline(
    storyboard: list[dict],
    default_dur_sec: int = 5,
    max_items: int | None = None,
    skip_narration_caption: bool = False,
    segment_durations: list[float] | None = None,
) -> tuple[list[tuple[float, float, str]], list[tuple[float, float, str]]]:
    """按分镜生成字幕与画面标题的时间轴：(start_sec, end_sec, text) 列表。
    segment_durations 不为空时优先使用（与每镜实际时长对齐）；否则用分镜的 duration_sec。
    skip_narration_caption=True 时，旁白不加入字幕轨，仅对话加字幕。"""
    items = storyboard[: max_items or len(storyboard)]
    lines: list[tuple[float, float, str]] = []
    title_lines: list[tuple[float, float, str]] = []
    t = 0.0
    for i, it in enumerate(items):
        if segment_durations and i < len(segment_durations) and segment_durations[i] > 0:
            dur_f = float(segment_durations[i])
        else:
            dur = it.get("duration_sec") or default_dur_sec
            dur_f = float(dur or default_dur_sec)
        start, end = t, t + dur_f
        text = (it.get("copy") or it.get("copy_text") or "").strip()
        if text:
            norm = " ".join(text.split())
            if not skip_narration_caption or not _is_narration_text(norm):
                lines.append((start, end, norm))
        shot_title = (it.get("shot_title") or "").strip()
        if shot_title:
            title_lines.append((start, end, " ".join(shot_title.split())[:32]))
        t = end
    return lines, title_lines


def build_drawtext_filter_script(
    storyboard: list[dict],
    default_dur_sec: int = 5,
    max_items: int | None = None,
    font_rel: str = "fonts/msyh.ttc",
    style_kind: str = "clean",
    seed: str | None = None,
    title_style: str = "bubble_cream",
    titles_only: bool = False,
    include_titles: bool = True,
    skip_narration_caption: bool = False,
    segment_durations: list[float] | None = None,
    plain_subtitle_only: bool = False,
) -> str | None:
    """
    根据分镜生成 drawtext 滤镜脚本文件，写入 MERGED_DIR，返回脚本文件名（相对 MERGED_DIR）。
    segment_durations 不为空时用于精确对齐每镜字幕时间。plain_subtitle_only=True 时仅纯字幕（无背景、无花字）。
    """
    lines, title_lines = _storyboard_to_timeline(
        storyboard, default_dur_sec, max_items,
        skip_narration_caption=skip_narration_caption,
        segment_durations=segment_durations,
    )
    if not include_titles or plain_subtitle_only:
        title_lines = []
    if not lines and not title_lines:
        return None
    if titles_only and not lines and title_lines:
        script_content = "[0:v]copy[v0]"
        script_path = _ensure_merged_dir() / f"drawtext_{os.urandom(4).hex()}.txt"
        try:
            script_path.write_text(script_content, encoding="utf-8")
            return script_path.name
        except Exception as e:
            logger.warning("build_drawtext_filter_script write failed: %s", e)
            return None
    if not lines and not title_lines:
        return None
    st = _default_subtitle_style(style_kind, seed=seed)
    sub_fs = st.font_size
    sub_y = f"h-th-{st.margin_v}"
    try:
        title_fs = int(os.getenv("TITLE_FONT_SIZE", "72"))
    except Exception:
        title_fs = 72
    title_fs = max(52, min(120, title_fs))
    title_y = "88"
    tcap = _get_title_caption_style(title_style, seed=seed)
    parts: list[str] = []
    inp = "[0:v]"
    idx = 1
    font_esc = font_rel.replace("\\", "\\\\").replace("'", "\\'")
    for start, end, text in lines:
        if not text:
            continue
        esc = _escape_drawtext(text)
        en = f"'between(t\\,{start}\\,{end})'"
        if plain_subtitle_only:
            parts.append(
                f"{inp}drawtext=fontfile='{font_esc}':text='{esc}':fontsize={sub_fs}:fontcolor=white:"
                f"box=0:x=(w-text_w)/2:y={sub_y}:enable={en}[v{idx}]"
            )
        else:
            parts.append(
                f"{inp}drawtext=fontfile='{font_esc}':text='{esc}':fontsize={sub_fs}:fontcolor=white:"
                f"x=(w-text_w)/2:y={sub_y}:enable={en}[v{idx}]"
            )
        inp = f"[v{idx}]"
        idx += 1
    if not titles_only and title_lines:
        for start, end, text in title_lines:
            if not text:
                continue
            esc = _escape_drawtext(text)
            en = f"'between(t\\,{start}\\,{end})'"
            parts.append(
                f"{inp}drawtext=fontfile='{font_esc}':text='{esc}':fontsize={title_fs}:"
                f"fontcolor={tcap.fontcolor}:borderw={tcap.borderw}:bordercolor={tcap.bordercolor}:"
                f"box={tcap.box}:boxcolor={tcap.boxcolor}:boxborderw={tcap.boxborderw}:"
                f"x=(w-text_w)/2:y={title_y}:enable={en}[v{idx}]"
            )
            inp = f"[v{idx}]"
            idx += 1
    if not parts and not (titles_only and title_lines):
        return None
    if titles_only and title_lines:
        # 输出 [v0] 供 overlay 用
        last_label = f"[v{idx-1}]"
        parts[-1] = parts[-1].replace(last_label, "[v0]")
    else:
        last_label = f"[v{idx-1}]"
        parts[-1] = parts[-1].replace(last_label, "[vout]")
    script_content = ",".join(parts)
    script_path = _ensure_merged_dir() / f"drawtext_{os.urandom(4).hex()}.txt"
    try:
        script_path.write_text(script_content, encoding="utf-8")
        return script_path.name
    except Exception as e:
        logger.warning("build_drawtext_filter_script write failed: %s", e)
        return None


def _build_combined_pill_overlay_script(
    base_content: str,
    pngs_with_y: list[tuple[str, float, float, str]],
) -> str | None:
    """
    将 base 输出 [v0] 与多条 overlay 拼接。pngs_with_y: [(path, start, end, y_expr), ...]，
    y_expr 如 "88"（顶部居中）或 "H-h-120"（底部）。对应 [1:v][2:v]...
    """
    if not pngs_with_y:
        return None
    content = (base_content or "[0:v]copy[v0]").strip()
    if "[vout]" in content:
        content = content.replace("[vout]", "[v0]")
    if not content.endswith("[v0]"):
        content = "[0:v]copy[v0]"
    parts = [content]
    prev = "[v0]"
    for i, (_, start, end, y_expr) in enumerate(pngs_with_y):
        inp_idx = i + 1
        # 单引号内逗号可不转义；避免 Windows 下反斜杠导致解析异常
        en = f"'between(t,{start},{end})'"
        out_label = "[vout]" if i == len(pngs_with_y) - 1 else f"[v{i+1}]"
        parts.append(
            f"{prev},[{inp_idx}:v]overlay=x=(W-w)/2:y={y_expr}:enable={en}{out_label}"
        )
        prev = out_label
    return ",".join(parts)


def build_and_write_combined_subtitle_title_script(
    subtitle_script_path: Path | None,
    title_pngs: list[tuple[str, float, float]],
    subtitle_pngs: list[tuple[str, float, float]] | None = None,
) -> Path | None:
    """
    拼接 base（[v0]）与 overlay 链并写入新文件。
    subtitle_script_path 为 None 时使用 [0:v]copy[v0]。
    subtitle_pngs 在前（底部 y=H-h-120），title_pngs 在后（顶部居中 y=88）。
    """
    pngs_with_y: list[tuple[str, float, float, str]] = []
    if subtitle_pngs:
        pngs_with_y.extend((p, s, e, "H-h-120") for p, s, e in subtitle_pngs)
    # 标题贴纸顶部居中
    pngs_with_y.extend((p, s, e, "88") for p, s, e in title_pngs)
    if not pngs_with_y:
        return None
    base = "[0:v]copy[v0]"
    if subtitle_script_path and subtitle_script_path.is_file():
        try:
            base = subtitle_script_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    content = _build_combined_pill_overlay_script(base, pngs_with_y)
    if not content:
        return None
    out = _ensure_merged_dir() / f"drawtext_combined_{os.urandom(4).hex()}.txt"
    try:
        out.write_text(content, encoding="utf-8")
        return out
    except Exception as e:
        logger.warning("build_and_write_combined_subtitle_title_script write failed: %s", e)
        return None


def burn_subtitles_with_title_overlays(
    merged_api_path: str,
    combined_script_path: str | Path,
    title_pngs_with_times: list[tuple[str, float, float]],
) -> str | None:
    """
    使用「字幕脚本 + 标题 PNG overlay」烧录，输出 _cap.mp4。
    在 MERGED_DIR 下用相对路径 -i，且用 -filter_complex 内联传入滤镜（不读脚本文件），
    避免 Windows 上 -filter_complex_script 文件路径或编码导致崩溃（returncode 异常大）。
    """
    video_path = _api_to_local_path(merged_api_path)
    if not video_path or not video_path.is_file():
        logger.warning("burn_subtitles_with_title_overlays: video not found")
        return None
    script_path = Path(combined_script_path)
    if not script_path.is_file():
        logger.warning("burn_subtitles_with_title_overlays: script not found %s", script_path)
        return None
    try:
        filter_complex_content = script_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning("burn_subtitles_with_title_overlays: read script failed %s", e)
        return None
    if not filter_complex_content:
        return None
    try:
        if video_path.resolve().parent != MERGED_DIR.resolve():
            logger.warning("burn_subtitles_with_title_overlays: video not under MERGED_DIR")
            return None
    except Exception:
        return None
    base, ext = os.path.splitext(video_path.name)
    out = MERGED_DIR / f"{base}_cap{ext}"
    video_rel = video_path.name
    cmd = ["ffmpeg", "-y", "-i", video_rel]
    for path, _, _ in title_pngs_with_times:
        p = Path(path)
        try:
            rel = p.relative_to(MERGED_DIR)
        except ValueError:
            rel = p.name
        cmd.extend(["-i", str(rel).replace("\\", "/")])
    # 内联 -filter_complex，不传脚本文件路径，避免 Windows 下崩溃
    cmd.extend([
        "-filter_complex", filter_complex_content,
        "-map", "[vout]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        out.name,
    ])
    try:
        r = subprocess.run(
            cmd,
            cwd=str(MERGED_DIR),
            capture_output=True,
            timeout=600,
        )
        if r.returncode != 0:
            stderr = (r.stderr or b"").decode("utf-8", errors="replace").strip()[:1200]
            logger.warning(
                "burn_subtitles_with_title_overlays ffmpeg failed returncode=%s stderr=%s",
                r.returncode, stderr,
            )
            return None
        out_full = MERGED_DIR / out.name
        if not out_full.exists():
            return None
        return _local_to_api_path(out_full)
    except Exception as e:
        logger.warning("burn_subtitles_with_title_overlays exception: %s", e)
        return None


def run_drawtext_script_to_video(merged_api_path: str, script_path: Path) -> str | None:
    """
    仅执行 drawtext 脚本，输出临时视频（供后续 multipass overlay 使用）。
    在 MERGED_DIR 下执行，返回临时文件相对名（如 _pill_base_xxx.mp4），失败返回 None。
    """
    video_path = _api_to_local_path(merged_api_path)
    if not video_path or not video_path.is_file():
        return None
    try:
        if video_path.resolve().parent != MERGED_DIR.resolve():
            return None
    except Exception:
        return None
    script_path = Path(script_path)
    if not script_path.is_file() or script_path.resolve().parent != MERGED_DIR.resolve():
        return None
    out_name = f"_pill_base_{os.urandom(6).hex()}.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", video_path.name,
        "-filter_complex_script", script_path.name,
        "-map", "[v0]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        out_name,
    ]
    try:
        r = subprocess.run(cmd, cwd=str(MERGED_DIR), capture_output=True, timeout=300)
        if r.returncode != 0:
            return None
        if (MERGED_DIR / out_name).is_file():
            return out_name
    except Exception:
        pass
    return None


def burn_pill_overlays_multipass(
    video_input: str | Path,
    pngs_with_y: list[tuple[str, float, float, str]],
    out_relative_name: str,
) -> str | None:
    """
    多轮 ffmpeg：每轮只叠一张 PNG（单 overlay），绕过 Windows 下多段 overlay 链解析问题。
    video_input: merged_api_path（如 /api/merged/xxx.mp4）或 MERGED_DIR 下视频相对名。
    pngs_with_y: [(png_path, start_sec, end_sec, y_expr), ...]，y_expr 如 "88"（顶部居中）或 "H-h-120"。
    返回成片 API 路径，失败返回 None。
    """
    if not pngs_with_y:
        return None
    if isinstance(video_input, Path):
        video_path = video_input
    else:
        video_path = _api_to_local_path(video_input) if str(video_input).startswith("/") else MERGED_DIR / str(video_input)
    if not video_path or not video_path.is_file():
        return None
    try:
        if video_path.resolve().parent != MERGED_DIR.resolve():
            return None
    except Exception:
        return None
    video_name = video_path.name
    pid = os.getpid()
    temp_a = MERGED_DIR / f"_cap_tmp_a_{pid}.mp4"
    temp_b = MERGED_DIR / f"_cap_tmp_b_{pid}.mp4"
    try:
        current_in = video_name
        for i, (png_path, start, end, y_expr) in enumerate(pngs_with_y):
            p = Path(png_path)
            try:
                rel = str(p.relative_to(MERGED_DIR)).replace("\\", "/")
            except ValueError:
                rel = p.name
            is_last = i == len(pngs_with_y) - 1
            current_out = out_relative_name if is_last else (temp_b.name if i % 2 == 0 else temp_a.name)
            filt = f"[0:v][1:v]overlay=x=(W-w)/2:y={y_expr}:enable='between(t\\,{start}\\,{end})'[vout]"
            cmd = [
                "ffmpeg", "-y", "-i", current_in, "-i", rel,
                "-filter_complex", filt,
                "-map", "[vout]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                current_out,
            ]
            r = subprocess.run(cmd, cwd=str(MERGED_DIR), capture_output=True, timeout=300)
            if r.returncode != 0:
                logger.warning("burn_pill_overlays_multipass overlay %s failed", rel)
                return None
            current_in = current_out
        out_full = MERGED_DIR / out_relative_name
        if out_full.is_file():
            return _local_to_api_path(out_full)
        return None
    finally:
        for p in (temp_a, temp_b):
            if p.is_file():
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
    return None


def burn_subtitles_drawtext(merged_api_path: str, filter_script_name: str) -> str | None:
    """
    使用 drawtext 滤镜脚本烧录字幕与标题到视频，输出 _cap.mp4。
    filter_script_name 为 MERGED_DIR 下的脚本文件名；在 MERGED_DIR 下执行 ffmpeg 以使用相对路径字体。
    """
    video_path = _api_to_local_path(merged_api_path)
    if not video_path or not video_path.is_file():
        logger.warning("burn_subtitles_drawtext: video not found merged_api_path=%s", merged_api_path)
        return None
    # 确保视频在 MERGED_DIR 下，否则 cwd=MERGED_DIR 时 -i video_path.name 找不到文件
    try:
        if video_path.resolve().parent != MERGED_DIR.resolve():
            logger.warning("burn_subtitles_drawtext: video not under MERGED_DIR")
            return None
    except Exception:
        return None
    script_path = MERGED_DIR / Path(filter_script_name).name
    if not script_path.is_file():
        logger.warning("burn_subtitles_drawtext: script not found %s", script_path)
        return None
    base, ext = os.path.splitext(video_path.name)
    out = MERGED_DIR / f"{base}_cap{ext}"
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path.name,
        "-filter_complex_script", script_path.name,
        "-map", "[vout]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        str(out),
    ]
    try:
        r = subprocess.run(
            cmd,
            cwd=str(MERGED_DIR),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if r.returncode != 0:
            logger.warning(
                "burn_subtitles_drawtext ffmpeg failed returncode=%s stderr=%s",
                r.returncode,
                (r.stderr or "").strip()[:800],
            )
            try:
                script_path.unlink()
            except Exception:
                pass
            return None
        if not out.exists():
            try:
                script_path.unlink()
            except Exception:
                pass
            return None
        try:
            script_path.unlink()
        except Exception:
            pass
        return _local_to_api_path(out)
    except Exception as e:
        logger.warning("burn_subtitles_drawtext exception: %s", e)
        try:
            script_path.unlink()
        except Exception:
            pass
        return None


def concat_with_transitions(
    local_paths: list[Path],
    transition: str = "fade",
    transition_sec: float = 0.35,
    out_ext: str = ".mp4",
) -> Path | None:
    """
    用 xfade 给多段视频加转场并输出新文件。
    - 需要 ffmpeg 支持 xfade。
    - 若探测不到时长/转场失败，返回 None（上层可 fallback 到 concat copy）。
    """
    if len(local_paths) < 2:
        return local_paths[0] if local_paths else None

    # 取各段时长，计算每个 xfade offset
    durs: list[float] = []
    for p in local_paths:
        d = _ffprobe_duration_sec(str(p))
        if not d:
            return None
        durs.append(d)

    # 为保证 xfade 稳定，统一 fps/像素格式；不强制 scale（默认输入同规格）
    # 计算 offset：第 i 次 xfade 在累计时长 - i*transition_sec - transition_sec 处开始
    offsets: list[float] = []
    acc = durs[0]
    for i in range(1, len(durs)):
        off = max(0.0, acc - transition_sec)
        offsets.append(off)
        acc += durs[i] - transition_sec

    out = _ensure_merged_dir() / f"{Path(local_paths[0]).stem}_xfade_{os.urandom(3).hex()}{out_ext}"

    # filter_complex 链： [0:v][1:v]xfade=...:offset=... [v01]; [v01][2:v]xfade ... [v012]...
    # 音频：简单 acrossfade；如果输入没音频也没关系（会自动忽略）
    # 为简化：只处理视频转场，音频直接拼接（更稳）；最终音频由后续混音覆盖。
    parts = []
    last_label = "v0"
    parts.append(f"[0:v]fps=30,format=yuv420p[{last_label}]")
    for idx in range(1, len(local_paths)):
        parts.append(f"[{idx}:v]fps=30,format=yuv420p[v{idx}]")
        out_label = f"v{idx}o"
        off = offsets[idx - 1]
        parts.append(f"[{last_label}][v{idx}]xfade=transition={transition}:duration={transition_sec}:offset={off}[{out_label}]")
        last_label = out_label
    filt = ";".join(parts)

    cmd = ["ffmpeg", "-y"]
    for p in local_paths:
        cmd += ["-i", str(p)]
    cmd += [
        "-filter_complex",
        filt,
        "-map",
        f"[{last_label}]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not out.exists():
            return None
        return out
    except Exception:
        return None


@dataclass
class SubtitleStyle:
    font: str
    font_size: int
    margin_v: int
    primary: str = "&H00FFFFFF"  # BGR in ASS, with alpha in first byte
    outline: str = "&H80000000"
    outline_w: int = 3
    shadow: int = 0


# 可选字幕字体池（按 seed 选其一，保证同一成片一致、不同成片有随机性）
SUBTITLE_FONT_POOL = [
    "Microsoft YaHei",
    "SimHei",
    "SimSun",
    "KaiTi",
    "FangSong",
    "Microsoft JhengHei",
]


def _default_subtitle_style(kind: str, seed: str | None = None) -> SubtitleStyle:
    # 若配置了 SUBTITLE_FONT 则只用该字体；否则用字体池 + seed 选一个（保证随机性）
    env_font = os.getenv("SUBTITLE_FONT", "").strip()
    if env_font:
        font = env_font
    elif seed:
        rnd = random.Random(seed)
        font = rnd.choice(SUBTITLE_FONT_POOL)
    else:
        font = "Microsoft YaHei"
    # 按 seed 做小幅字号/边距扰动，避免每次完全一致
    delta = 0
    if seed:
        rnd = random.Random(seed + "size")
        delta = rnd.randint(-2, 2)
    if kind == "bold":
        return SubtitleStyle(font=font, font_size=56 + delta, margin_v=92 + delta, outline_w=7, shadow=0)
    if kind == "note":
        return SubtitleStyle(font=font, font_size=50 + delta, margin_v=120 + delta, outline_w=3, shadow=0)
    return SubtitleStyle(font=font, font_size=48 + delta, margin_v=110 + delta, outline_w=4, shadow=0)


# 画面标题（卖点标题）气泡样式：ASS BackColour 为 BGR+alpha，常用 pastel 色（多组保证随机性）
TITLE_BUBBLE_COLOURS = [
    "&H80F5E6D6",  # 奶白/米
    "&H80E8F4E8",  # 淡绿
    "&H80E6E0F4",  # 淡紫
    "&H80E0F4F4",  # 淡青
    "&H80F4E8E0",  # 淡橙
    "&H80F0E6F4",  # 淡粉
    "&H80E0F0E8",  # 薄荷
    "&H80F4E0EC",  # 浅玫
]


def _build_title_bubble_styles(seed: str | None, base_font: str, base_size: int) -> tuple[str, list[str]]:
    """生成多组气泡样式（Title1/Title2/Title3），每组随机配色与字号/边距，返回 (styles_str, style_names)。"""
    rnd = random.Random(seed) if seed else random.Random()
    names = ["Title1", "Title2", "Title3"]
    # 每组：随机气泡色、字号±4、MarginV 80~130
    colours = rnd.sample(TITLE_BUBBLE_COLOURS, min(3, len(TITLE_BUBBLE_COLOURS)))
    if len(colours) < 3:
        colours = colours + [rnd.choice(TITLE_BUBBLE_COLOURS) for _ in range(3 - len(colours))]
    sizes = [base_size + rnd.randint(-2, 4) for _ in range(3)]
    margins = [rnd.randint(80, 130) for _ in range(3)]
    lines = []
    for i, name in enumerate(names):
        lines.append(
            f"Style: {name},{base_font},{sizes[i]},"
            "&H00000000,&H000000FF,&H80000000,"
            f"{colours[i]},"
            "0,0,0,0,100,100,0,0,3,1,0,2,60,60,"
            f"{margins[i]},1\n"
        )
    return "".join(lines), names


def build_ass_from_storyboard(
    storyboard: list[dict],
    default_dur_sec: int = 5,
    max_items: int | None = None,
    style_kind: str = "clean",
    seed: str | None = None,
) -> str | None:
    """按分镜 copy（对白/旁白）生成 ASS 字幕；若有 shot_title 则同时生成画面卖点标题（气泡样式，多组随机）。
    返回 ASS 文件路径，失败返回 None。同一 ASS 内包含字幕与标题，烧录时一并生效。"""
    items = storyboard[: max_items or len(storyboard)]
    lines: list[tuple[float, float, str]] = []
    title_lines: list[tuple[float, float, str]] = []
    t = 0.0
    for it in items:
        dur = it.get("duration_sec") or default_dur_sec
        dur_f = float(dur or default_dur_sec)
        start = t
        end = t + dur_f
        text = (it.get("copy") or it.get("copy_text") or "").strip()
        if text:
            text = " ".join(text.split())
            lines.append((start, end, text))
        shot_title = (it.get("shot_title") or "").strip()
        if shot_title:
            shot_title = " ".join(shot_title.split())[:32]
            title_lines.append((start, end, shot_title))
        t = end
    if not lines and not title_lines:
        return None

    st = _default_subtitle_style(style_kind, seed=seed)
    env_font = os.getenv("SUBTITLE_FONT", "").strip()
    title_font = env_font or (random.Random(seed).choice(SUBTITLE_FONT_POOL) if seed else "Microsoft YaHei")
    title_base_size = st.font_size + 4
    title_styles_str, title_style_names = _build_title_bubble_styles(seed, title_font, title_base_size)

    ass = tempfile.NamedTemporaryFile(suffix=".ass", delete=False)
    ass.close()

    def ts(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    # Default 样式（字幕）
    style_line = (
        f"Style: Default,{st.font},{st.font_size},"
        f"{'&H00FFFFFF' if style_kind != 'bold' else '&H00000000'},&H000000FF,"
        f"{'&H80000000' if style_kind != 'note' else '&H40000000'},"
        f"{'&H00000000' if style_kind != 'note' else '&H70F0E6D6'},"
        "0,0,0,0,100,100,0,0,"
        f"{3 if style_kind == 'note' else 1},"
        f"{st.outline_w},{st.shadow},2,60,60,{st.margin_v},1\n"
    )
    styles_block = style_line + (title_styles_str if title_lines else "")
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 0\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        + styles_block
        + "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    with open(ass.name, "w", encoding="utf-8") as f:
        f.write(header)
        for start, end, text in lines:
            text = text.replace("{", "").replace("}", "")
            f.write(f"Dialogue: 0,{ts(start)},{ts(end)},Default,,0,0,0,,{text}\n")
        for idx, (start, end, text) in enumerate(title_lines):
            text = text.replace("{", "").replace("}", "")
            style_name = title_style_names[idx % len(title_style_names)]
            f.write(f"Dialogue: 1,{ts(start)},{ts(end)},{style_name},,0,0,0,,{text}\n")
    return ass.name


def burn_subtitles_ass(merged_api_path: str, ass_path: str) -> str | None:
    """将 ASS 烧录到视频，输出新文件 _cap.mp4。ASS 内同时含字幕（Default）与画面标题（Title1/2/3），
    时间轴与分镜 copy/shot_title 一致，与配音衔接。"""
    video_path = _api_to_local_path(merged_api_path)
    if not video_path or not video_path.is_file():
        logger.warning("burn_subtitles_ass: video not found merged_api_path=%s", merged_api_path)
        return None
    if not ass_path or not os.path.isfile(ass_path):
        logger.warning("burn_subtitles_ass: ASS file not found ass_path=%s", ass_path)
        return None
    base, ext = os.path.splitext(video_path.name)
    out = _ensure_merged_dir() / f"{base}_cap{ext}"
    ass_path_ff = Path(ass_path).resolve().as_posix()
    # Windows 盘符冒号必须转义，否则 ffmpeg 会把它当成 filter 选项分隔符导致路径被截断
    if len(ass_path_ff) >= 2 and ass_path_ff[1] == ":":
        ass_path_ff = ass_path_ff[0] + "\\:" + ass_path_ff[2:]
    ass_esc = ass_path_ff.replace("'", "'\\''")
    vf_subtitles = f"subtitles='{ass_esc}':charenc=UTF-8"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        vf_subtitles,
        "-c:a",
        "copy",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            logger.warning(
                "burn_subtitles_ass ffmpeg failed returncode=%s stderr=%s",
                r.returncode,
                (r.stderr or "").strip()[:800],
            )
            return None
        if not out.exists():
            logger.warning("burn_subtitles_ass: output file not created %s", out)
            return None
        return _local_to_api_path(out)
    except Exception as e:
        logger.warning("burn_subtitles_ass exception: %s", e)
        return None


def apply_ambient_and_stickers(merged_api_path: str, style_kind: str = "film", seed: str | None = None) -> str | None:
    """
    叠加氛围层 + 简易“花纸”（不依赖外部素材）：颗粒、暗角、轻微调色 + 角落小星星。
    说明：这是无素材版本的自动化兜底，后续可接入素材包 PNG/WebM 做更丰富的花纸。
    """
    video_path = _api_to_local_path(merged_api_path)
    if not video_path or not video_path.is_file():
        return None
    base, ext = os.path.splitext(video_path.name)
    out = _ensure_merged_dir() / f"{base}_fx{ext}"

    rnd = random.Random(seed or os.urandom(6).hex())
    dur = _ffprobe_duration_sec(str(video_path)) or 20.0
    env_font = os.getenv("STICKER_FONT", "").strip()
    font = env_font or rnd.choice(["Microsoft YaHei", "SimHei", "SimSun"])

    # 贴纸「内容」池：按风格分多组，每次从池里随机选，保证不每次都一样
    if style_kind == "note":
        sticker_contents = [
            "✎", "✿", "•", "◇", "♡", "♥", "♪", "★", "☆", "→", "·", "◆", "○",
            "★ ·", "♪ ", "♥ ", "☆ ★",
        ]
    elif style_kind == "sparkle":
        sticker_contents = [
            "✦", "✧", "✺", "✶", "✷", "✸", "✹", "★", "☆", "·", "◆", "◇",
            "✦ ✧", "☆ ★", "✶ ✷",
        ]
    else:
        sticker_contents = [
            "✦", "·", "◇", "•", "◆", "○", "☆", "★", "♪", "✧", "✶",
            "✦ ·", "◇ •", "☆ ",
        ]

    stickers: list[str] = []
    tapes: list[str] = []
    n = rnd.randint(4, 9)
    for i in range(n):
        start = rnd.uniform(0.2, max(0.21, dur - 1.0))
        length = rnd.uniform(0.25, 0.95)
        end = min(dur, start + length)
        corner = rnd.choice(["tl", "tr", "bl", "br"])
        # 位置小幅随机，避免每次完全重合
        ox = rnd.randint(50, 75)
        oy_top = rnd.randint(70, 100)
        oy_bot = rnd.randint(260, 300)
        if corner == "tl":
            x, y = str(ox), str(oy_top)
        elif corner == "tr":
            x, y = f"w-tw-{ox}", str(oy_top)
        elif corner == "bl":
            x, y = str(ox), f"h-th-{oy_bot}"
        else:
            x, y = f"w-tw-{ox}", f"h-th-{oy_bot}"
        alpha = rnd.uniform(0.5, 0.92)
        content = rnd.choice(sticker_contents)
        # drawtext 里单引号要转义
        content_esc = content.replace("'", "'\\''")
        size = rnd.randint(32, 56)
        stickers.append(
            f"drawtext=text='{content_esc}':font='{font}':fontsize={size}:"
            f"fontcolor=white@{alpha:.2f}:x={x}:y={y}:enable='between(t,{start:.2f},{end:.2f})'"
        )

    # note 风格额外加「纸胶带」矩形：宽高、位置按 seed 随机，避免每次一样
    if style_kind == "note":
        for _ in range(rnd.randint(1, 2)):
            start = rnd.uniform(0.1, max(0.11, dur - 1.2))
            end = min(dur, start + rnd.uniform(0.6, 1.5))
            corner = rnd.choice(["tl", "tr"])
            w = rnd.randint(300, 380)
            h = rnd.randint(100, 140)
            if corner == "tl":
                x, y = str(rnd.randint(30, 55)), str(rnd.randint(30, 55))
            else:
                x, y = f"w-{w + rnd.randint(30, 60)}", str(rnd.randint(30, 55))
            tapes.append(
                f"drawbox=x={x}:y={y}:w={w}:h={h}:color=#f0e6d6@0.55:t=fill:"
                f"enable='between(t,{start:.2f},{end:.2f})'"
            )

    if style_kind == "note":
        # 更明亮、对比更强
        base_vf = "eq=contrast=1.06:saturation=1.08:brightness=0.02"
    elif style_kind == "sparkle":
        base_vf = "eq=contrast=1.04:saturation=1.10"
    else:
        # film
        base_vf = "eq=contrast=1.03:saturation=1.05"

    vf = ",".join(
        [
            "fps=30",
            "format=yuv420p",
            base_vf,
            "vignette=PI/7",
            "noise=alls=8:allf=t+u",
            *tapes,
            *stickers,
        ]
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        vf,
        "-c:a",
        "copy",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not out.exists():
            return None
        return _local_to_api_path(out)
    except Exception:
        return None

