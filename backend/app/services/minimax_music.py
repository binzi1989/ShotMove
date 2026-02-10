"""MiniMax 音乐生成：BGM/背景音乐，用于成片配乐。"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import httpx

MINIMAX_API_KEY = (os.getenv("MINIMAX_API_KEY") or "").strip()
MINIMAX_BASE = (os.getenv("MINIMAX_BASE") or "https://api.minimaxi.com").rstrip("/")
MUSIC_MODEL = "music-2.5"

# BGM 兜底：当 MiniMax 余额不足/不可用时，可走本地文件或本地合成
BGM_PROVIDER = (os.getenv("BGM_PROVIDER") or "minimax").strip().lower()  # minimax | local
LOCAL_BGM_PATH = (os.getenv("LOCAL_BGM_PATH") or "").strip()


def _headers() -> dict:
    if not MINIMAX_API_KEY:
        raise ValueError("MINIMAX_API_KEY not set")
    return {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }


def _try_local_bgm_copy() -> tuple[Optional[str], Optional[str]]:
    """若配置了 LOCAL_BGM_PATH，则复制到临时文件返回（避免调用方清理时误删原文件）。"""
    if not LOCAL_BGM_PATH:
        return None, "LOCAL_BGM_PATH 未配置"
    p = Path(LOCAL_BGM_PATH)
    if not p.exists() or not p.is_file():
        return None, f"LOCAL_BGM_PATH 不存在：{LOCAL_BGM_PATH}"
    tmp = tempfile.NamedTemporaryFile(suffix=p.suffix or ".mp3", delete=False)
    tmp.close()
    try:
        shutil.copy2(str(p), tmp.name)
        return tmp.name, None
    except Exception as e:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        return None, str(e)


def _try_local_bgm_synth(duration_sec: int) -> tuple[Optional[str], Optional[str]]:
    """
    用 ffmpeg 合成一段简单“氛围底噪”作为 BGM（兜底方案，不依赖外部 API）。
    """
    dur = int(duration_sec or 0)
    if dur <= 0:
        dur = 20
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    lavfi = f"anoisesrc=color=pink:amplitude=0.05:duration={dur}"
    af = "highpass=f=80,lowpass=f=1800,volume=0.6"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        lavfi,
        "-filter:a",
        af,
        "-ar",
        "24000",
        "-ac",
        "1",
        "-b:a",
        "128000",
        tmp.name,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if r.returncode != 0 or not os.path.isfile(tmp.name) or os.path.getsize(tmp.name) <= 0:
            err = (r.stderr or r.stdout or "").strip()[:500] or f"ffmpeg failed code={r.returncode}"
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
            return None, err
        return tmp.name, None
    except (FileNotFoundError, OSError) as e:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        return None, f"ffmpeg 不可用：{e}"
    except subprocess.TimeoutExpired:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        return None, "ffmpeg 合成超时"


def _fallback_bgm(duration_sec: Optional[int] = None) -> tuple[Optional[str], Optional[str]]:
    """BGM 兜底：优先用本地文件，其次用 ffmpeg 合成。"""
    p, err = _try_local_bgm_copy()
    if p and not err:
        return p, None
    return _try_local_bgm_synth(duration_sec or 20)


def generate_bgm(
    prompt: str = "轻快, 短视频背景音乐, 无歌词, instrumental",
    lyrics: str = "[Inst]\n纯音乐",
    duration_sec: Optional[int] = None,
    sample_rate: int = 44100,
    bitrate: int = 256000,
    output_format: str = "hex",
) -> tuple[Optional[str], Optional[str]]:
    """
    生成 BGM 音频。返回 (本地 mp3 路径, error_msg)。
    prompt: 风格/情绪/场景描述；lyrics: 必填 1–3500 字符，纯 BGM 可用 "[Inst]\\n纯音乐"。
    """
    # 指定本地 BGM 或未配置 MiniMax key：直接走兜底
    if BGM_PROVIDER == "local" or not MINIMAX_API_KEY:
        return _fallback_bgm(duration_sec=duration_sec)

    url = f"{MINIMAX_BASE}/v1/music_generation"
    payload = {
        "model": MUSIC_MODEL,
        "prompt": (prompt or "轻快, 短视频背景音乐")[:2000],
        "lyrics": (lyrics or "[Inst]\n纯音乐").strip()[:3500],
        "audio_setting": {
            "sample_rate": sample_rate,
            "bitrate": bitrate,
            "format": "mp3",
        },
        "output_format": output_format,
        "stream": False,
    }
    try:
        with httpx.Client(timeout=120.0) as client:
            r = client.post(url, json=payload, headers=_headers())
            data = r.json() if r.content else {}
            base = data.get("base_resp", {})
            code = base.get("status_code")
            if r.status_code != 200:
                err = base.get("status_msg") or r.text or f"HTTP {r.status_code}"
                return None, err
            if code != 0:
                return None, base.get("status_msg") or f"status_code={code}"
            raw = data.get("data", {}).get("audio")
            if not raw:
                return None, "无音频数据"
            if output_format == "url":
                # 返回的是 URL，下载后保存到本地
                with httpx.Client(timeout=60.0, follow_redirects=True) as c:
                    resp = c.get(raw)
                    resp.raise_for_status()
                    content = resp.content
            else:
                import binascii
                content = binascii.unhexlify(raw)
            out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            out.write(content)
            out.close()
            return out.name, None
    except ValueError as e:
        # key 未配置时也兜底
        if "MINIMAX_API_KEY" in str(e):
            return _fallback_bgm(duration_sec=duration_sec)
        # 其他 ValueError：兜底一次，兜底失败再返回原错误
        p, err = _fallback_bgm(duration_sec=duration_sec)
        return (p, None) if p and not err else (None, str(e))
    except Exception as e:
        # 余额不足等错误：兜底生成本地 BGM，保证流程不断
        p, err = _fallback_bgm(duration_sec=duration_sec)
        return (p, None) if p and not err else (None, str(e))
