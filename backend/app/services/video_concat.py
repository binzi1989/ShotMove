"""将多段视频下载到本地备份后使用 ffmpeg 拼接成片，保存到 static/merged；支持 BGM + 配音混音。"""
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)

# 合并后的视频存放目录（与 main 中挂载路径一致）
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
MERGED_DIR = BACKEND_ROOT / "static" / "merged"
# 生成视频分镜备份目录：先下载到此处再合成成片
SEGMENTS_BACKUP_DIR = MERGED_DIR / "segments"


def _ensure_merged_dir() -> Path:
    MERGED_DIR.mkdir(parents=True, exist_ok=True)
    return MERGED_DIR


# 单段下载超时（秒），外网/可灵等可能较慢，拉长并配合重试，避免 WinError 10060 导致素材不全
DOWNLOAD_SEGMENT_TIMEOUT = float(os.getenv("DOWNLOAD_SEGMENT_TIMEOUT", "240"))
DOWNLOAD_SEGMENT_RETRIES = max(0, int(os.getenv("DOWNLOAD_SEGMENT_RETRIES", "4")))

# 可灵等防盗链：下载视频时需带 Referer，否则 CDN 可能返回 403。可通过 DOWNLOAD_REFERER 覆盖
DEFAULT_DOWNLOAD_REFERER = (os.getenv("DOWNLOAD_REFERER") or "").strip() or "https://api-beijing.klingai.com"


def _download_headers(url: str) -> dict:
    """下载用请求头，满足可灵防盗链（Referer）；非可灵 URL 也可通过 DOWNLOAD_REFERER 指定。"""
    referer = DEFAULT_DOWNLOAD_REFERER
    if not referer and "klingai" in (url or "").lower():
        referer = "https://api-beijing.klingai.com"
    h = {}
    if referer:
        h["Referer"] = referer
    ua = (os.getenv("DOWNLOAD_USER_AGENT") or "").strip()
    if ua:
        h["User-Agent"] = ua
    return h


def download_segments_to_backup(download_urls: list[str], job_id: str) -> list[Path]:
    """
    将多段视频 URL 下载到本地备份目录 static/merged/segments/{job_id}/seg_000.mp4 ...
    返回成功下载的本地路径列表（按顺序）；单段失败会重试若干次。
    可灵返回的 URL 为防盗链格式，请求时会带上 Referer，避免 403。
    若仍有片段失败，返回的列表长度会小于 download_urls 长度，调用方必须检查并拒绝使用部分结果。
    """
    if not download_urls:
        return []
    backup_dir = SEGMENTS_BACKUP_DIR / job_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    local_paths: list[Path] = []
    for i, url in enumerate(download_urls):
        if not url or not str(url).strip():
            logger.warning("download_segments_to_backup: segment %s 无有效 URL，跳过", i)
            continue
        url = url.strip()
        path = backup_dir / f"seg_{i:03d}.mp4"
        last_err = None
        headers = _download_headers(url)
        for attempt in range(1 + DOWNLOAD_SEGMENT_RETRIES):
            try:
                with httpx.Client(timeout=DOWNLOAD_SEGMENT_TIMEOUT, follow_redirects=True) as client:
                    r = client.get(url, headers=headers or None)
                    r.raise_for_status()
                    path.write_bytes(r.content)
                local_paths.append(path)
                last_err = None
                break
            except Exception as e:
                last_err = e
                # 便于排查：记录 HTTP 状态码与片段索引
                status = getattr(getattr(e, "response", None), "status_code", None)
                body = ""
                if hasattr(e, "response") and getattr(e.response, "text", None):
                    body = (e.response.text or "")[:200]
                if attempt <= 1:
                    logger.warning(
                        "download_segments_to_backup: segment %s attempt %s failed status=%s err=%s body=%s",
                        i, attempt + 1, status, e, body,
                    )
                if attempt < DOWNLOAD_SEGMENT_RETRIES:
                    time.sleep(2.0 * (attempt + 1))
        if last_err is not None:
            logger.warning("download_segments_to_backup: segment %s 最终失败 err=%s", i, last_err)
    return local_paths


def mix_audio_into_merged(
    merged_api_path: str,
    voice_mp3_path: str,
    bgm_mp3_path: str | None = None,
    sfx_mp3_path: str | None = None,
    voice_volume: float = 1.0,
    bgm_volume: float = 0.25,
    sfx_volume: float = 0.2,
) -> str | None:
    """
    给已合并的视频混入配音（TTS）、可选 BGM、可选音效。生成新文件 xxx_vo.mp4。
    merged_api_path: 如 /api/merged/xxx.mp4
    voice_mp3_path: 配音 mp3 本地路径
    bgm_mp3_path: BGM/环境音 mp3，可为 None
    sfx_mp3_path: 音效 mp3（如环境底噪、场景音），可为 None；与视频等长或较短均可
    返回新成片路径如 /api/merged/xxx_vo.mp4，失败返回 None。
    """
    if not merged_api_path.strip().startswith("/api/merged/"):
        return None
    name = merged_api_path.strip().replace("/api/merged/", "").strip("/")
    if not name or ".." in name:
        return None
    video_path = MERGED_DIR / name
    if not video_path.exists() or not video_path.is_file():
        return None
    base, ext = os.path.splitext(name)
    out_name = f"{base}_vo{ext}"
    out_path = _ensure_merged_dir() / out_name
    if not os.path.isfile(voice_mp3_path):
        logger.warning("mix_audio_into_merged: voice file missing %s", voice_mp3_path)
        return None
    if bgm_mp3_path and not os.path.isfile(bgm_mp3_path):
        bgm_mp3_path = None
    if sfx_mp3_path and not os.path.isfile(sfx_mp3_path):
        sfx_mp3_path = None
    try:
        video_dur = _ffprobe_duration_sec(str(video_path)) or 0.0
        voice_dur = _ffprobe_duration_sec(voice_mp3_path) or 0.0
        need_pad_video = bool(voice_dur > 0 and video_dur > 0 and voice_dur > video_dur + 0.08)
        pad_delta = (voice_dur - video_dur + 0.05) if need_pad_video else 0.0
        # 输入顺序：0=视频, 1=配音, 2=BGM(可选), 3=音效(可选)
        inputs = [str(video_path), voice_mp3_path]
        if bgm_mp3_path:
            inputs.append(bgm_mp3_path)
        if sfx_mp3_path:
            inputs.append(sfx_mp3_path)
        idx = 1
        vol_labels = [f"[{idx}]volume={voice_volume}[v]"]
        idx += 1
        if bgm_mp3_path:
            vol_labels.append(f"[{idx}]volume={bgm_volume}[b]")
            idx += 1
        if sfx_mp3_path:
            vol_labels.append(f"[{idx}]volume={sfx_volume}[s]")
        amix_parts = ["[v]"]
        if bgm_mp3_path:
            amix_parts.append("[b]")
        if sfx_mp3_path:
            amix_parts.append("[s]")
        n_inputs = len(amix_parts)
        audio_filt = ";".join(vol_labels) + ";" + "".join(amix_parts) + f"amix=inputs={n_inputs}:duration=first[a]"
        if need_pad_video and pad_delta > 0:
            # 若配音更长，克隆最后一帧把视频补齐，避免 -shortest 截断尾句
            filt = f"[0:v]tpad=stop_mode=clone:stop_duration={pad_delta:.3f},format=yuv420p[v0];" + audio_filt
        else:
            filt = audio_filt
        cmd = ["ffmpeg", "-y"]
        for inp in inputs:
            cmd.extend(["-i", inp])
        if need_pad_video and pad_delta > 0:
            cmd.extend(
                [
                    "-filter_complex",
                    filt,
                    "-map",
                    "[v0]",
                    "-map",
                    "[a]",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "18",
                    "-shortest",
                    str(out_path),
                ]
            )
        else:
            cmd.extend(
                [
                    "-filter_complex",
                    filt,
                    "-map",
                    "0:v",
                    "-map",
                    "[a]",
                    "-c:v",
                    "copy",
                    "-shortest",
                    str(out_path),
                ]
            )
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0 or not out_path.exists():
            logger.warning(
                "mix_audio_into_merged ffmpeg failed code=%s stderr=%s",
                result.returncode,
                (result.stderr or "").strip()[:500],
            )
            return None
        return f"/api/merged/{out_name}"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("mix_audio_into_merged exception: %s", e)
        return None


def single_segment_to_merged(download_url: str) -> str | None:
    """
    将单段视频先下载到本地备份（static/merged/segments/{job_id}/seg_000.mp4），再复制为成片 static/merged/{uuid}.mp4。
    返回可访问路径如 /api/merged/xxx.mp4；失败返回 None。
    """
    if not download_url or not download_url.strip():
        return None
    try:
        with httpx.Client(timeout=90.0, follow_redirects=True) as client:
            r = client.get(download_url.strip())
            r.raise_for_status()
            content = r.content
        if not content:
            return None
        job_id = uuid4().hex
        backup_dir = SEGMENTS_BACKUP_DIR / job_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / "seg_000.mp4"
        backup_path.write_bytes(content)
        out_name = f"{uuid4().hex}.mp4"
        out_path = _ensure_merged_dir() / out_name
        shutil.copy2(backup_path, out_path)
        return f"/api/merged/{out_name}"
    except Exception as e:
        logger.warning("single_segment_to_merged failed: %s", e)
        return None


def single_segment_to_merged_with_duration(
    download_url: str,
    target_duration_sec: float | None = None,
) -> tuple[str | None, list[float]]:
    """
    与 single_segment_to_merged 相同，但额外返回该段时长 [duration_sec]，用于短剧按镜配音。
    返回 (merged_url, [duration])；失败时 (None, [])。
    """
    if not download_url or not download_url.strip():
        return (None, [])
    try:
        with httpx.Client(timeout=90.0, follow_redirects=True) as client:
            r = client.get(download_url.strip())
            r.raise_for_status()
            content = r.content
        if not content:
            return (None, [])
        job_id = uuid4().hex
        backup_dir = SEGMENTS_BACKUP_DIR / job_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / "seg_000.mp4"
        backup_path.write_bytes(content)
        duration = _ffprobe_duration_sec(str(backup_path)) or 5.0
        # 可选：按目标时长裁切/补齐（用于短剧按镜配音对齐）
        if target_duration_sec is not None:
            try:
                td = float(target_duration_sec)
            except Exception:
                td = None
            if td and td > 0:
                retimed = backup_dir / "seg_000_retimed.mp4"
                if _retime_video_to_duration(backup_path, retimed, td):
                    backup_path = retimed
                    duration = td
        out_name = f"{uuid4().hex}.mp4"
        out_path = _ensure_merged_dir() / out_name
        shutil.copy2(backup_path, out_path)
        return (f"/api/merged/{out_name}", [duration])
    except Exception as e:
        logger.warning("single_segment_to_merged_with_duration failed: %s", e)
        return (None, [])


def concat_local_segments(
    local_paths: list[Path],
    with_transitions: bool = False,
    transition_sec: float = 0.35,
    transition: str = "fade",
) -> str | None:
    """
    将已在本地的多段视频按顺序拼接成片，保存到 static/merged/{uuid}.mp4。
    local_paths: 本地文件路径列表（如 download_segments_to_backup 的返回值）。
    返回可访问路径 /api/merged/xxx.mp4；失败返回 None。
    """
    if not local_paths:
        return None
    str_paths = [str(p) for p in local_paths]
    if with_transitions and len(local_paths) >= 2:
        try:
            from app.services.video_post import concat_with_transitions

            out_path = concat_with_transitions(
                local_paths,
                transition=transition,
                transition_sec=transition_sec,
            )
            if out_path and out_path.exists():
                return f"/api/merged/{out_path.name}"
        except Exception as e:
            logger.warning("concat_local_segments transitions fallback: %s", e)

    if len(str_paths) == 1:
        out_name = f"{uuid4().hex}.mp4"
        out_path = _ensure_merged_dir() / out_name
        shutil.copy2(str_paths[0], out_path)
        return f"/api/merged/{out_name}"

    job_id = uuid4().hex
    list_file = SEGMENTS_BACKUP_DIR / job_id / "list.txt"
    list_file.parent.mkdir(parents=True, exist_ok=True)
    with open(list_file, "w", encoding="utf-8") as f:
        for p in str_paths:
            # Windows: ffmpeg concat demuxer 对反斜杠敏感，统一写成正斜杠
            p_norm = Path(p).resolve().as_posix() if p else p
            escaped = (p_norm or p).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    out_name = f"{uuid4().hex}.mp4"
    out_path = _ensure_merged_dir() / out_name
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0 or not out_path.exists():
            stderr = (result.stderr or "").strip()
            logger.warning(
                "concat_local_segments ffmpeg failed code=%s stderr=%s list_file=%s",
                result.returncode,
                stderr[:800],
                list_file,
            )
            return None
        return f"/api/merged/{out_name}"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("concat_local_segments exception: %s", e)
        return None


def _ffprobe_duration_sec(path: str) -> float | None:
    """返回视频时长（秒）；失败返回 None。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nokey=1:noprint_wrappers=1", path],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if r.returncode != 0:
            return None
        s = (r.stdout or "").strip()
        return float(s) if s else None
    except Exception:
        return None


def _retime_video_to_duration(
    in_path: str | Path,
    out_path: str | Path,
    target_sec: float,
    fps: int = 30,
    preset: str = "veryfast",
    crf: str = "18",
) -> bool:
    """
    将视频裁切/延长到 target_sec：
    - 需要变短：用 -t 裁切
    - 需要变长：用 tpad 克隆最后一帧补齐
    输出统一编码参数，便于后续 concat copy 稳定。
    """
    try:
        target_sec = float(target_sec)
    except Exception:
        return False
    if target_sec <= 0:
        return False
    in_path_s = str(in_path)
    out_path_s = str(out_path)
    cur = _ffprobe_duration_sec(in_path_s) or 0.0
    pad = max(0.0, target_sec - cur)
    vf = f"fps={fps},format=yuv420p"
    if pad > 0.04:
        vf = f"tpad=stop_mode=clone:stop_duration={pad:.3f},{vf}"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        in_path_s,
        "-vf",
        vf,
        "-t",
        f"{target_sec:.3f}",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        crf,
        out_path_s,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return r.returncode == 0 and os.path.isfile(out_path_s)
    except Exception:
        return False


def retime_local_segments_to_durations(
    local_paths: list[Path],
    target_durations: list[float],
    fps: int = 30,
) -> list[Path]:
    """
    将本地分段视频按 target_durations 裁切/补齐为新分段文件，返回新路径列表（长度与 local_paths 一致）。
    若某段处理失败则回退为原文件路径。
    """
    if not local_paths:
        return []
    out: list[Path] = []
    for i, p in enumerate(local_paths):
        try:
            target = float(target_durations[i]) if i < len(target_durations) else (_ffprobe_duration_sec(str(p)) or 5.0)
        except Exception:
            target = _ffprobe_duration_sec(str(p)) or 5.0
        if target <= 0:
            target = 5.0
        # 输出到同目录，避免跨盘符/相对路径导致 ffmpeg concat 限制
        out_path = p.parent / f"retimed_{i:03d}.mp4"
        ok = _retime_video_to_duration(p, out_path, target, fps=fps)
        if not ok:
            # 兜底：哪怕无法严格对齐，也尽量统一编码/去掉音轨，避免后续 concat copy 因流不一致失败
            cur = _ffprobe_duration_sec(str(p)) or 5.0
            ok = _retime_video_to_duration(p, out_path, cur, fps=fps)
        out.append(out_path if ok else p)
    return out


def concat_video_segments(
    download_urls: list[str],
    with_transitions: bool = False,
    transition_sec: float = 0.35,
    transition: str = "fade",
) -> str | None:
    """
    可灵/其他多段视频的「下载本地 + 视频剪辑」：先将多段视频 URL 下载到本地备份（static/merged/segments/{job_id}/），
    再按顺序用 ffmpeg 拼接成片保存到 static/merged/{uuid}.mp4。单段下载失败则跳过该段，用其余成功段合并；仅全部失败时返回 None。
    返回可访问路径，如 /api/merged/xxx.mp4；失败或 ffmpeg 不可用时返回 None。
    """
    if not download_urls:
        return None

    job_id = uuid4().hex
    local_paths = download_segments_to_backup(download_urls, job_id)
    if not local_paths:
        return None

    return concat_local_segments(
        local_paths,
        with_transitions=with_transitions,
        transition_sec=transition_sec,
        transition=transition,
    )


def concat_video_segments_with_durations(
    download_urls: list[str],
    with_transitions: bool = False,
    transition_sec: float = 0.35,
    transition: str = "fade",
    target_durations: list[float] | None = None,
    retime_to_target: bool = True,
) -> tuple[str | None, list[float]]:
    """
    与 concat_video_segments 相同，但额外返回每段时长列表（用于短剧按镜配音对齐）。
    返回 (merged_url, segment_durations)；merged_url 为 None 时 segment_durations 为空列表。
    """
    if not download_urls:
        return (None, [])
    job_id = uuid4().hex
    local_paths = download_segments_to_backup(download_urls, job_id)
    if not local_paths:
        return (None, [])
    if len(local_paths) < len(download_urls):
        logger.error(
            "download_segments_to_backup: incomplete (%s of %s), refuse to merge",
            len(local_paths), len(download_urls),
        )
        return (None, [])
    effective_paths = local_paths
    if retime_to_target and target_durations:
        # 转场会让镜头时间轴发生叠加，不利于「镜头-对白」一一对齐，故此模式下禁用转场
        if with_transitions:
            with_transitions = False
        logger.info(f"调整视频段时长为: {target_durations}")
        effective_paths = retime_local_segments_to_durations(local_paths, target_durations)
    segment_durations = [_ffprobe_duration_sec(str(p)) or 5.0 for p in effective_paths]
    logger.info(f"调整后的视频段时长: {segment_durations}")
    merged = concat_local_segments(
        effective_paths,
        with_transitions=with_transitions,
        transition_sec=transition_sec,
        transition=transition,
    )
    return (merged, segment_durations)


