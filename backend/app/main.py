"""多 Agent 协作创作服务：路由 + 短剧/小剧创作"""
import base64
import logging
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

# 优先加载 backend 目录下的 .env
_env = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env)

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.schemas import (
    CreateRequest,
    CreateResponse,
    ClassifyResponse,
    ContentRequest,
    VideoRequest,
    RegenerateShotRequest,
    VoiceoverOnlyRequest,
    ConcatFromSegmentsRequest,
    ConcatAfterKlingTasksRequest,
    ScriptDramaResult,
    ClarifyResult,
    VideoGenerationResult,
    TaskCreate,
    TaskUpdate,
    TaskSummary,
    TaskDetail,
    MembershipTier,
    UserMembershipSummary,
    RedeemMembershipRequest,
    PointBalance,
    PointTransactionItem,
    SignInResponse,
)
from app.agents import (
    classify_input,
    run_script_drama_agent,
    run_video_generation,
)
from app.services.video_concat import (
    concat_video_segments,
    concat_video_segments_with_durations,
    concat_local_segments,
    download_segments_to_backup,
    single_segment_to_merged,
    single_segment_to_merged_with_duration,
    mix_audio_into_merged,
    retime_local_segments_to_durations,
    MERGED_DIR,
)
from app.services.minimax_music import generate_bgm
from app.services import iflytek_speech, volcano_speech
from app.services.kling_video import get_kling_task_status_batch

# TTS 引擎：volcano | iflytek；默认 volcano（多情感、男女音色映射清晰）
TTS_ENGINE = (os.getenv("TTS_ENGINE") or "volcano").strip().lower()
# 短剧配音语速：0–100，50 为正常；默认 50，语速尽量不调整，只靠语气
try:
    TTS_DRAMA_SPEED = int(os.getenv("TTS_DRAMA_SPEED", "32"))
except (TypeError, ValueError):
    TTS_DRAMA_SPEED = 32
TTS_DRAMA_SPEED = max(15, min(80, TTS_DRAMA_SPEED))
# 短剧成片是否添加轻柔环境音（氛围感，非 BGM 旋律）
DRAMA_AMBIENT_ENABLED = (os.getenv("DRAMA_AMBIENT_ENABLED", "true")).strip().lower() in ("1", "true", "yes")
# 短剧音效：可选，指向一个 mp3 文件（如环境底噪、场景音），与配音和环境音一起混入成片；留空则不叠加
_DRAMA_SFX_PATH_RAW = (os.getenv("DRAMA_SFX_PATH") or "").strip()
DRAMA_SFX_PATH: Optional[str] = None
if _DRAMA_SFX_PATH_RAW:
    _p = Path(_DRAMA_SFX_PATH_RAW)
    if _p.is_absolute() and _p.exists() and _p.is_file():
        DRAMA_SFX_PATH = str(_p)
    else:
        _p_rel = (Path(__file__).resolve().parent.parent / _DRAMA_SFX_PATH_RAW).resolve()
        if _p_rel.exists() and _p_rel.is_file():
            DRAMA_SFX_PATH = str(_p_rel)


def _get_text_to_speech():
    """根据 TTS_ENGINE 返回当前配音使用的 TTS 函数（与 iflytek_speech / volcano_speech 同签名）。"""
    return volcano_speech.text_to_speech if TTS_ENGINE == "volcano" else iflytek_speech.text_to_speech
from app.services.video_post import (
    _ensure_drawtext_font,
    _ffprobe_duration_sec,
    _ffprobe_video_size,
    _render_subtitle_caption_pngs,
    _render_title_caption_pngs,
    build_drawtext_filter_script,
    build_voice_track_from_segments,
    burn_subtitles_drawtext,
    burn_pill_overlays_multipass,
    run_drawtext_script_to_video,
    apply_ambient_and_stickers,
)
from app.services.llm import infer_voice_for_drama, infer_voice_for_drama_line, infer_emotion_for_drama_lines
from app.services.store import (
    init_db,
    create_task,
    update_task,
    list_tasks,
    get_task,
    delete_task,
    get_or_create_user_by_device,
    list_membership_tiers,
    get_tier_by_code,
    get_user_effective_tier,
    get_user_effective_membership,
    create_user_membership,
    get_user_balance,
    add_point_transaction,
    deduct_points,
    list_point_transactions,
    has_signed_in_today,
    get_daily_usage,
    check_can_use_quota,
    increment_daily_usage,
)

app = FastAPI(
    title="短剧/小剧 多智能体创作",
    description="根据用户输入（剧本/对白/自然语言）自动路由到短剧创作管线",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MERGED_DIR.mkdir(parents=True, exist_ok=True)

# 角色参考图：短剧多角色时按镜拉取；保存到 static/merged/character_refs/{job_id}/ref_{i}.jpg
CHAR_REF_DIR = MERGED_DIR / "character_refs"
CHAR_REF_DIR.mkdir(parents=True, exist_ok=True)


@app.on_event("startup")
def on_startup():
    init_db()
    # 启动时打印已注册的 API 路由，便于排查 404
    for r in app.routes:
        if hasattr(r, "path") and hasattr(r, "methods") and r.methods:
            logging.getLogger(__name__).info("路由: %s %s", sorted(r.methods)[0], r.path)


@app.get("/")
def root():
    return {"service": "short-video-drama-agents", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/ping")
def api_ping():
    """用于快速确认后端是否可达（不依赖 LLM/耗时逻辑）"""
    return {"ok": True, "message": "pong"}


def _clarify_message(input_text: str) -> tuple[str, str | None]:
    """生成需求澄清文案与建议管线。"""
    if "短剧" in (input_text or "") or "剧本" in (input_text or "") or "分镜" in (input_text or ""):
        return (
            "检测到您可能想做短剧/剧情短视频。请粘贴剧本或对白内容，我将为您生成分镜与文生视频用 Prompt。",
            "script_drama",
        )
    return (
        "请粘贴剧本或对白内容，我将为您生成短剧分镜与文生视频用 Prompt。",
        None,
    )


@app.post("/api/classify", response_model=ClassifyResponse)
def classify(req: CreateRequest):
    """步骤1：识别输入类型与管线，便于前端逐步展示。"""
    input_type, pipeline, debug_note = classify_input(req.input)
    msg, suggested = None, None
    if pipeline == "clarify":
        msg, suggested = _clarify_message(req.input)
    return ClassifyResponse(
        input_type=input_type,
        pipeline=pipeline,
        debug_router_note=debug_note,
        message=msg,
        suggested_pipeline=suggested,
    )


@app.get("/api/content")
def create_content_get():
    """浏览器直接打开是 GET，会走到这里；实际生成分镜请用 POST（前端点击「开始生成」会发 POST）。"""
    return {
        "detail": "此接口仅接受 POST 请求。请在前端页面点击「开始生成」，或使用 Postman 等工具发送 POST，body: {\"input\": \"剧本内容\", \"pipeline\": \"script_drama\"}",
        "method_required": "POST",
        "docs": "/docs",
    }


def _create_content_impl(req: ContentRequest):
    """步骤2：仅生成脚本/分镜（不生成视频）。共用于 /api/content 与 /api/script-drama/content。"""
    logger.info("分镜接口收到请求，input 长度=%d", len(req.input or ""))
    try:
        result: ScriptDramaResult = run_script_drama_agent(req.input)
        logger.info("分镜接口完成，分镜数=%d", len(result.storyboard))
        return {"pipeline": "script_drama", "result": result.model_dump(by_alias=True)}
    except Exception as e:
        logger.exception("分镜接口执行异常")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/content")
def create_content(req: ContentRequest):
    return _create_content_impl(req)


@app.post("/api/script-drama/content")
def create_content_alt(req: ContentRequest):
    """与 POST /api/content 完全相同，备用路径以防 404。"""
    return _create_content_impl(req)


@app.get("/api/merged/{filename:path}")
def get_merged_video(filename: str):
    """成片/配音/BGM 下载：返回剪辑合并后的视频或音频文件。支持子目录如 segments/xxx/"""
    if ".." in filename:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="文件不存在")
    path = (MERGED_DIR / filename.strip("/")).resolve()
    merged_resolved = MERGED_DIR.resolve()
    if not path.exists() or not path.is_file() or merged_resolved not in path.parents:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="文件不存在")
    media_type = "video/mp4"
    if path.suffix.lower() == ".mp3":
        media_type = "audio/mpeg"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/api/character-refs/{job_id}/{filename:path}")
def get_character_ref_image(job_id: str, filename: str):
    """角色参考图下载：短剧多角色时按镜拉取；供可灵等公网访问。"""
    if ".." in job_id or ".." in filename or "\\" in job_id:
        raise HTTPException(status_code=404, detail="文件不存在")
    safe_name = filename.replace("\\", "/").strip("/").split("/")[-1]
    if not safe_name or safe_name != filename.strip("/"):
        raise HTTPException(status_code=404, detail="文件不存在")
    path = (CHAR_REF_DIR / job_id / safe_name).resolve()
    base_resolved = CHAR_REF_DIR.resolve()
    if not path.exists() or not path.is_file() or base_resolved not in path.parents:
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path, media_type="image/jpeg", filename=safe_name)


def _download_image_to_data_url(url: str) -> str | None:
    """下载图片 URL 转为 data URL，供参考图等使用。"""
    url = (url or "").strip()
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            raw = r.content
        if not raw:
            return None
        ct = (r.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
        if not ct.startswith("image/"):
            ct = "image/jpeg"
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{ct};base64,{b64}"
    except Exception:
        return None


logger = logging.getLogger(__name__)


def _character_names_for_voiceover(
    character_references: Optional[list] = None,
    storyboard: Optional[list] = None,
) -> list[str]:
    """从「角色参考」或分镜中收集角色名列表，供 TTS 剥前缀用。优先 character_references，否则从 storyboard 的 character_name 去重保序。"""
    if character_references:
        names = []
        for r in character_references:
            n = (getattr(r, "name", None) or "").strip()
            if n and n not in names:
                names.append(n)
        if names:
            return names
    if storyboard:
        names = []
        for s in storyboard:
            # 先收集本镜多人 character_names，再补 character_name，保证对白前缀能正确剥除
            multi = getattr(s, "character_names", None)
            if multi and isinstance(multi, (list, tuple)):
                for n in multi:
                    n = (n or "").strip()
                    if n and n not in names:
                        names.append(n)
            n = (getattr(s, "character_name", None) or "").strip()
            if n and n not in names:
                names.append(n)
        if names:
            return names
    return []


def _shot_copy(shot) -> str:
    """从分镜项（StoryboardItem 或 dict）取对白/文案。不用 getattr(shot,'copy')，否则会取到 .copy() 方法。"""
    if isinstance(shot, dict):
        return (shot.get("copy_text") or shot.get("copy") or "").strip()
    v = getattr(shot, "copy_text", None)
    return (v or "").strip() if isinstance(v, str) else ""


def _shot_character_name(shot) -> Optional[str]:
    """从分镜项取本镜主要角色名，用于按角色选男/女声。"""
    if isinstance(shot, dict):
        names = shot.get("character_names")
        if names and isinstance(names, (list, tuple)) and len(names) > 0:
            return (names[0] or "").strip() or None
        return (shot.get("character_name") or "").strip() or None
    names = getattr(shot, "character_names", None)
    if names and isinstance(names, (list, tuple)) and len(names) > 0 and names[0]:
        return (names[0] or "").strip() or None
    n = getattr(shot, "character_name", None)
    return (n or "").strip() or None


def _speaker_from_copy_prefix(copy: str) -> Optional[str]:
    """从对白开头的「XXX：」提取说话人，用于无 character_name 时辅助推断性别。"""
    if not copy or not copy.strip():
        return None
    m = re.match(r"^([^：:]+)[：:]\s*", copy.strip())
    return (m.group(1).strip() or None) if m else None


def _build_drama_tts_and_target_durations(
    storyboard_slice: list,
    character_references: Optional[list] = None,
    voice_id: Optional[str] = None,
    shot_voice_ids: Optional[list[Optional[str]]] = None,
    script_summary: Optional[str] = None,
) -> tuple[Optional[list[Optional[str]]], Optional[list[float]], Optional[list[Optional[str]]]]:
    """
    短剧按镜生成 TTS 并计算目标镜头时长，使最后一镜等可按配音时长分配画面。
    返回 (prebuilt_tts_paths, target_durations, shot_emotions)；失败返回 (None, None, None)。
    """
    if not storyboard_slice:
        return None, None, None
    try:
        character_names = _character_names_for_voiceover(character_references=character_references, storyboard=storyboard_slice)
        shots_as_dicts = [{"copy": _shot_copy(s)} for s in storyboard_slice]
        shot_emotions = infer_emotion_for_drama_lines(shots_as_dicts, script_snippet=script_summary or "")
        if not shot_emotions:
            shot_emotions = [None] * len(storyboard_slice)
        elif len(shot_emotions) < len(storyboard_slice):
            shot_emotions = shot_emotions + [None] * (len(storyboard_slice) - len(shot_emotions))
        prebuilt_tts_paths: list[Optional[str]] = []
        target_durations: list[float] = []
        tail_pad = float(os.getenv("DRAMA_TTS_TAIL_PAD_SEC", "0.25") or 0.25)
        min_shot_sec = float(os.getenv("DRAMA_MIN_SHOT_SEC", "1.0") or 1.0)
        voice_cache: dict[str, str] = {}  # 同一剧本内同一角色复用音色，避免前后镜男女混乱
        for i, shot in enumerate(storyboard_slice):
            copy = _shot_copy(shot)
            tts_content = _strip_tts_speaker_prefix(copy, character_names)
            if not tts_content or _is_action_only_no_speech(tts_content):
                prebuilt_tts_paths.append(None)
                try:
                    d0 = float(getattr(shot, "duration_sec", None) or 3.0)
                except Exception:
                    d0 = 3.0
                target_durations.append(max(1.0, d0))
                continue
            shot_voice = None
            if shot_voice_ids and i < len(shot_voice_ids) and (shot_voice_ids[i] or "").strip():
                shot_voice = (shot_voice_ids[i] or "").strip()
            if not shot_voice:
                char_name = _speaker_from_copy_prefix(copy)
                if not char_name and character_names and len(character_names) == 1:
                    char_name = character_names[0]
                if not char_name:
                    char_name = _shot_character_name(shot)
                shot_voice = (voice_id or "").strip() or infer_voice_for_drama_line(
                    copy, character_name=char_name, script_snippet=script_summary or "", voice_cache=voice_cache
                )
            # 有非 neutral 的 LLM 情绪优先用；否则用关键词情绪，避免情感过平
            emotion = None
            if shot_emotions and i < len(shot_emotions) and shot_emotions[i] and shot_emotions[i] != "neutral":
                emotion = shot_emotions[i]
            if not emotion and TTS_ENGINE == "volcano":
                emotion = volcano_speech.infer_emotion_from_text(copy)
            if not emotion and shot_emotions and i < len(shot_emotions) and shot_emotions[i]:
                emotion = shot_emotions[i]
            speed = max(15, min(80, TTS_DRAMA_SPEED + _emotion_to_speed_delta(emotion)))
            path = None
            try:
                result = _get_text_to_speech()(
                    tts_content[:5000], voice_id=shot_voice or None, emotion=emotion, speed=speed,
                )
                path = result[0] if len(result) > 0 else None
                err = result[1] if len(result) > 1 else None
                if err:
                    logger.warning("drama align: per-shot TTS failed shot %s err=%s", i, err)
            except Exception as e:
                logger.warning("drama align: per-shot TTS exception shot %s: %s", i, e)
            path = path if (path and os.path.isfile(path)) else None
            prebuilt_tts_paths.append(path)
            dur_tts = _ffprobe_duration_sec(path) if path else None
            base = float(dur_tts) if (dur_tts and dur_tts > 0) else _estimate_dialogue_duration_sec(tts_content)
            target_durations.append(max(min_shot_sec, base + tail_pad))
        return prebuilt_tts_paths, target_durations, shot_emotions
    except Exception as e:
        logger.warning("drama align: build TTS/target durations failed: %s", e)
        return None, None, None


def _is_action_only_no_speech(text: str) -> bool:
    """判断是否为纯动作/神态描述（不应送 TTS 朗读）。如「微微点头」「点头」「微笑」「沉默」等。"""
    if not text or not text.strip():
        return True
    t = text.strip()
    if len(t) > 50:
        return False
    action_only = (
        r"^(微微?点头|点头|摇头|微笑|沉默|不语|皱眉|叹气|抬眼|低头|转身|示意|挥手|摆手|"
        r"抬眼看去|目光扫过|目光掠过|眼神?[一]?动|轻轻?点头|轻轻?摇头|"
        r"略一点头|颔首|摇头不语|笑而不语|沉默不语|默然|无语|—|－|-)\s*[。.]?$"
    )
    if re.match(action_only, t, re.I):
        return True
    if re.match(r"^[微微轻略]?[点头摇头笑叹]+[不语默然]?\s*[。.]?$", t):
        return True
    return False


def _strip_tts_speaker_prefix(text: str, character_names: Optional[list[str]] = None) -> str:
    """去掉「旁白：」「角色名：」等前缀，只保留 TTS 应朗读的正文。
    character_names: 从角色参考（上传角色图）处得到的角色名列表，长度不限；若提供则只剥这些名字+冒号，否则用正则剥首个「XXX：」。
    """
    if not text or not text.strip():
        return ""
    t = text.strip()
    # 旁白： / 旁白:
    if re.match(r"^旁白\s*[：:]\s*", t):
        t = re.sub(r"^旁白\s*[：:]\s*", "", t).strip()
    # 角色名：优先用上传角色里的名字（任意长度），否则剥掉首个「XXX：」
    if character_names:
        names = [n.strip() for n in character_names if n and str(n).strip()]
        names = sorted(set(names), key=len, reverse=True)  # 长名优先，避免「欧阳修」被当成「欧」
        for name in names:
            if not name:
                continue
            pattern = re.escape(name) + r"\s*[：:]\s*"
            if re.match("^" + pattern, t):
                t = re.sub("^" + pattern, "", t).strip()
                break
    else:
        m = re.match(r"^([^\s：:]+)\s*[：:]\s*", t)
        if m:
            t = t[m.end() :].strip()
    return t


def _ffprobe_duration_sec(path: str) -> float | None:
    """返回音频/视频时长（秒）；失败返回 None。需要 ffprobe。"""
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                path,
            ],
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


def _estimate_dialogue_duration_sec(text: str) -> float:
    """
    估算台词朗读所需时长（秒），用于「镜头时长按正常语速」对齐。
    - 中文：按字数估算（默认约 4.5 字/秒）
    - 英文：按词数估算（默认约 2.8 词/秒）
    - 标点：增加少量停顿
    """
    if not text:
        return 0.0
    t = " ".join(str(text).split()).strip()
    if not t:
        return 0.0
    # 统计中文字符与英文单词
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", t))
    en_words = len(re.findall(r"[A-Za-z0-9]+", t))
    punct = len(re.findall(r"[，,。.!！？?；;：:、】【「」“”\"'…—-]", t))
    zh_rate = float(os.getenv("DRAMA_SPEECH_ZH_CHARS_PER_SEC", "4.5") or 4.5)
    en_rate = float(os.getenv("DRAMA_SPEECH_EN_WORDS_PER_SEC", "2.8") or 2.8)
    pause = float(os.getenv("DRAMA_SPEECH_PUNCT_PAUSE_SEC", "0.10") or 0.10)
    base = (zh_chars / max(1e-3, zh_rate)) + (en_words / max(1e-3, en_rate))
    # 过短时给一点起音缓冲
    return max(0.8, base + punct * pause)


def _filter_script_for_tts(script: str) -> str:
    """过滤掉「画面」「镜头」等描述性语句，只保留适合 TTS 念的台词/旁白。"""
    if not script or not script.strip():
        return ""
    text = script.strip()
    # 按行或按句拆分，便于过滤
    lines = re.split(r"[\n]+|[；;]\s*", text)
    kept = []
    # 描述性前缀/整句关键词：这类行不念
    desc_pattern = re.compile(
        r"^(镜头\d*[：:]?|画面[：:]?|景别[：:]?|机位[：:]?|分镜\d*[：:]?|"
        r"固定镜头|推镜|拉镜|特写|中景|远景|大景|全景|近景|"
        r"序号[：:]?\s*\d*|本镜[：:]?|拍摄方式|镜头安排)\s*[。.]?.*$",
        re.I,
    )
    # 行内描述性片段，删掉（保留前后台词）
    inline_desc = re.compile(
        r"(画面[：:]|镜头\d*[：:]|景别[：:]|固定镜头[，。]?|推镜[，。]?|拉镜[，。]?)\s*[^，。]*[，。]?",
        re.I,
    )
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if desc_pattern.match(line):
            continue
        line = inline_desc.sub("", line).strip()
        if len(line) >= 2:
            kept.append(line)
    out = " ".join(kept).strip()
    if not out:
        out = inline_desc.sub("", text).strip()
    return out[:10000] if out else ""


def _storyboard_to_dicts(storyboard) -> list[dict]:
    out: list[dict] = []
    if not storyboard:
        return out
    for s in storyboard:
        try:
            if isinstance(s, dict):
                out.append(s)
            elif hasattr(s, "model_dump"):
                out.append(s.model_dump(by_alias=True))
            else:
                # 兼容：尽量取字段
                out.append(
                    {
                        "copy": _shot_copy(s),
                        "duration_sec": getattr(s, "duration_sec", None),
                        "shot_title": getattr(s, "shot_title", "") or "",
                    }
                )
        except Exception:
            continue
    return out


def _postprocess_visuals(
    merged_url: str,
    storyboard,
    pipeline: Optional[str],
    with_captions: bool,
    with_stickers: bool,
    subtitle_style: str = "clean",
    sticker_style: str = "film",
    title_caption_style: Optional[str] = None,
    caption_narration: bool = True,
    segment_durations: Optional[list[float]] = None,
) -> str:
    """成片视觉后期：花纸/氛围层 + 字幕烧录。短剧仅纯字幕、无花字、与每镜配音精确对齐（segment_durations）。失败时返回原 merged_url。"""
    if not merged_url or (not with_captions and not with_stickers):
        return merged_url
    sb = _storyboard_to_dicts(storyboard)
    if with_stickers:
        try:
            seed = merged_url.replace("/api/merged/", "").split(".")[0]
            new_url = apply_ambient_and_stickers(merged_url, style_kind=sticker_style, seed=seed)
            merged_url = new_url or merged_url
        except Exception as e:
            logger.warning("postprocess visuals stickers failed: %s", e)
    if with_captions and sb:
        try:
            default_dur = int(os.getenv("KLING_DURATION", "5") or "5")
        except Exception:
            default_dur = 5
        max_items = None
        title_style = (title_caption_style or os.getenv("TITLE_CAPTION_STYLE", "bubble_yellow") or "").strip() or "bubble_yellow"
        skip_narration = not caption_narration
        drama_plain_subtitle = True
        try:
            seed = merged_url.replace("/api/merged/", "").split(".")[0]
            font_rel = _ensure_drawtext_font()
            if not font_rel:
                logger.warning("postprocess visuals: no drawtext font available, skip captions")
            else:
                video_path = MERGED_DIR / merged_url.strip().replace("/api/merged/", "").strip("/")
                use_pill = False
                pill_ok = False
                if use_pill:
                    w, h = _ffprobe_video_size(str(video_path))
                    w, h = w or 1080, h or 1920
                    font_abs = MERGED_DIR / font_rel
                    subtitle_pngs = _render_subtitle_caption_pngs(
                        sb, title_style, w, h, font_abs, default_dur, max_items,
                        skip_narration_caption=skip_narration,
                    )
                    # 短剧不需要标题贴纸/花字
                    title_pngs = []
                    all_pngs = list(subtitle_pngs) + list(title_pngs)
                    if all_pngs:
                        # 标题贴纸顶部居中 y=88，字幕在底 y=H-h-120；多轮 overlay 避免 Windows 下多段链解析问题
                        pngs_with_y = [(p, s, e, "H-h-120") for p, s, e in subtitle_pngs]
                        pngs_with_y += [(p, s, e, "88") for p, s, e in title_pngs]
                        out_cap = f"{video_path.stem}_cap{video_path.suffix}"
                        script_path = None
                        base_video: str | Path = merged_url
                        if not subtitle_pngs and title_pngs:
                            script_name = build_drawtext_filter_script(
                                sb,
                                default_dur_sec=default_dur,
                                max_items=max_items,
                                font_rel=font_rel,
                                style_kind=str(subtitle_style or "clean"),
                                seed=seed,
                                title_style=title_style,
                                titles_only=True,
                                include_titles=False,
                            )
                            if script_name:
                                script_path = MERGED_DIR / script_name
                                temp_name = run_drawtext_script_to_video(merged_url, script_path)
                                if temp_name:
                                    base_video = temp_name
                        new_url = burn_pill_overlays_multipass(base_video, pngs_with_y, out_cap)
                        if new_url:
                            merged_url = new_url
                            pill_ok = True
                            if isinstance(base_video, str) and base_video.startswith("_pill_base_"):
                                try:
                                    (MERGED_DIR / base_video).unlink(missing_ok=True)
                                except Exception:
                                    pass
                            png_dirs = {os.path.dirname(p) for p, _, _ in all_pngs}
                            for d in png_dirs:
                                if d and os.path.isdir(d):
                                    try:
                                        shutil.rmtree(d, ignore_errors=True)
                                    except Exception:
                                        pass
                        if script_path is not None and getattr(script_path, "is_file", lambda: False)():
                            try:
                                script_path.unlink()
                            except FileNotFoundError:
                                pass
                            except Exception:
                                pass
                if not pill_ok:
                    script_name = build_drawtext_filter_script(
                        sb,
                        default_dur_sec=default_dur,
                        max_items=max_items,
                        font_rel=font_rel,
                        style_kind=str(subtitle_style or "clean"),
                        seed=seed,
                        title_style=title_style,
                        titles_only=False,
                        include_titles=False,
                        skip_narration_caption=skip_narration,
                        segment_durations=segment_durations,
                        plain_subtitle_only=drama_plain_subtitle,
                    )
                    if script_name:
                        new_url = burn_subtitles_drawtext(merged_url, script_name)
                        merged_url = new_url or merged_url
                    else:
                        logger.warning("postprocess visuals: build_drawtext_filter_script returned None (sb items=%s)", len(sb))
        except Exception as e:
            logger.warning("postprocess visuals captions failed: %s", e)
    return merged_url


def _emotion_to_speed_delta(emotion: Optional[str]) -> int:
    """根据情绪微调语速，增强情感层次。设 DRAMA_EMOTION_SPEED_DELTA=1 时：excited/happy 略快，sad/coldness 略慢。"""
    try:
        delta_enabled = int(os.getenv("DRAMA_EMOTION_SPEED_DELTA", "0") or 0)
    except ValueError:
        delta_enabled = 0
    if delta_enabled <= 0:
        return 0
    if not emotion or emotion == "neutral":
        return 0
    d = {"excited": 4, "happy": 2, "angry": 3, "surprised": 2, "fear": -2, "sad": -4, "coldness": -2, "hate": 1}
    return d.get(emotion, 0)


def _add_bgm_and_voiceover(
    merged_url: str,
    script_text: str,
    with_bgm: bool,
    with_voiceover: bool,
    pipeline: Optional[str] = None,
    bgm_style_prompt: Optional[str] = None,
    storyboard: Optional[list] = None,
    segment_durations: Optional[list[float]] = None,
    voice_id: Optional[str] = None,
    character_names: Optional[list[str]] = None,
    shot_voice_ids: Optional[list[Optional[str]]] = None,
    prebuilt_tts_paths: Optional[list[Optional[str]]] = None,
    voice_align_mode: str = "time_stretch",
    shot_emotions: Optional[list[Optional[str]]] = None,
    script_summary_for_emotion: Optional[str] = None,
) -> tuple[str, Optional[str], Optional[str]]:
    """在成片上混入 BGM 和/或 TTS 配音，返回 (成片路径, 配音下载路径, BGM下载路径)；失败则返回 (原merged_url, None, None)。
    短剧(pipeline=script_drama)：不添加 BGM；若 with_voiceover 且传入 storyboard + segment_durations，则按镜生成 TTS，无台词镜用静音，再混入成片。
    shot_emotions：每镜情绪标签（happy/sad/angry/...），用于 TTS 情感合成与语速微调；为 None 时用剧本上下文自动推断。"""
    if not merged_url or not (with_bgm or with_voiceover):
        return (merged_url, None, None)
    # 短剧不添加 BGM
    if pipeline == "script_drama":
        with_bgm = False
    voice_path = None
    bgm_path = None
    voiceover_url = None
    bgm_url = None
    try:
        # 短剧：按镜配音，镜头与台词一一对应，无台词镜音轨空（静音）
        if pipeline == "script_drama" and with_voiceover and storyboard and segment_durations:
            # 测试经验：配音总长与成片时长对齐，避免时间点错位；按成片实际时长等比缩放每镜时长
            segment_durations = list(segment_durations)
            name = merged_url.strip().replace("/api/merged/", "").strip("/").split("/")[-1]
            if name and ".." not in name:
                video_path = MERGED_DIR / name
                if video_path.is_file():
                    video_duration = _ffprobe_duration_sec(str(video_path))
                    if video_duration and video_duration > 0:
                        total = sum(segment_durations)
                        # 仅当配音总长与成片时长差异超过容差时才等比缩放，避免轻微偏差导致错位
                        scale_tolerance = float(os.getenv("DRAMA_VOICE_SCALE_TOLERANCE", "0.5") or 0.5)
                        if total > 0 and abs(total - video_duration) > scale_tolerance:
                            scale = video_duration / total
                            segment_durations = [max(1.0, d * scale) for d in segment_durations]
            # 测试经验：语速不拉伸，只裁切/静音填充，保持自然
            if voice_align_mode == "time_stretch":
                voice_align_mode = "pad_trim"
            # 若无预计算的每镜情绪，则根据剧本上下文推断一次（短句也能有合理情绪）
            if shot_emotions is None and storyboard:
                shots_as_dicts = [{"copy": _shot_copy(s)} for s in storyboard]
                shot_emotions = infer_emotion_for_drama_lines(
                    shots_as_dicts,
                    script_snippet=script_summary_for_emotion or "",
                )
            segments: list[tuple[Optional[str], float]] = []
            voice_cache: dict[str, str] = {}  # 同一剧本内同一角色复用音色，避免前后镜男女混乱
            for i, shot in enumerate(storyboard):
                copy = _shot_copy(shot)
                d = segment_durations[i] if i < len(segment_durations) else 5.0
                if d <= 0:
                    d = 5.0
                tts_content = _strip_tts_speaker_prefix(copy, character_names)
                if not tts_content or _is_action_only_no_speech(tts_content):
                    segments.append((None, d))
                    continue
                # 若外部已预先生成 TTS（用于镜头对齐），则复用，避免重复扣费
                if prebuilt_tts_paths and i < len(prebuilt_tts_paths):
                    p = prebuilt_tts_paths[i]
                    if p and os.path.isfile(p):
                        segments.append((p, d))
                        continue
                # 每镜可指定音色：shot_voice_ids[i] 优先，否则全局 voice_id，否则按对白推断（说话人优先，单人镜兜底）
                shot_voice = None
                if shot_voice_ids and i < len(shot_voice_ids) and (shot_voice_ids[i] or "").strip():
                    shot_voice = (shot_voice_ids[i] or "").strip()
                if not shot_voice:
                    char_name = _speaker_from_copy_prefix(copy)
                    if not char_name and character_names and len(character_names) == 1:
                        char_name = character_names[0]
                    if not char_name:
                        char_name = _shot_character_name(shot)
                    shot_voice = (voice_id or "").strip() or infer_voice_for_drama_line(
                        copy,
                        character_name=char_name,
                        script_snippet=script_summary_for_emotion or "",
                        voice_cache=voice_cache,
                    )
                # 有非 neutral 的 LLM 情绪优先；否则用关键词情绪，加强情感起伏
                emotion = None
                if shot_emotions and i < len(shot_emotions) and shot_emotions[i] and shot_emotions[i] != "neutral":
                    emotion = shot_emotions[i]
                if not emotion and TTS_ENGINE == "volcano":
                    emotion = volcano_speech.infer_emotion_from_text(copy)
                if not emotion and shot_emotions and i < len(shot_emotions) and shot_emotions[i]:
                    emotion = shot_emotions[i]
                speed = max(15, min(80, TTS_DRAMA_SPEED + _emotion_to_speed_delta(emotion)))
                try:
                    result = _get_text_to_speech()(
                        tts_content[:5000],
                        voice_id=shot_voice or None,
                        emotion=emotion,
                        speed=speed,
                    )
                    path = result[0] if len(result) > 0 else None
                    err = result[1] if len(result) > 1 else None
                except Exception as e:
                    logger.warning("drama per-shot TTS exception shot %s: %s", i, e)
                    path, err = None, str(e)
                segments.append((path if path and os.path.isfile(path) else None, d))
            name = merged_url.strip().replace("/api/merged/", "").strip("/")
            base, _ = os.path.splitext(name)
            if not base or ".." in base:
                base = "audio"
            MERGED_DIR.mkdir(parents=True, exist_ok=True)
            voice_dest = MERGED_DIR / f"{base}_voice.mp3"
            out_path = build_voice_track_from_segments(
                segments,
                voice_dest,
                align_mode="pad_trim" if (voice_align_mode or "").strip().lower() == "pad_trim" else "time_stretch",
            )
            if out_path and os.path.isfile(out_path):
                voice_path = out_path
                voiceover_url = f"/api/merged/{voice_dest.name}"
            for _path, _ in segments:
                if _path and _path != voice_path:
                    try:
                        if os.path.isfile(_path):
                            os.unlink(_path)
                    except Exception:
                        pass
            # 短剧可选：轻柔环境音/氛围音（无旋律），增强沉浸感
            if DRAMA_AMBIENT_ENABLED and voice_path and segment_durations:
                total_duration = max(30, int(sum(segment_durations)))
                bgm_path, _ = generate_bgm(
                    prompt="轻柔环境音 无旋律 影视剧氛围 低音量 纯氛围 不抢戏",
                    duration_sec=min(total_duration, 120),
                )
        elif with_voiceover and script_text and script_text.strip():
            tts_text = _filter_script_for_tts(script_text)
            if not tts_text:
                tts_text = script_text.strip()[:10000]
            if pipeline == "script_drama" and tts_text:
                # 整段时也去掉各句前的「旁白：」「角色名：」，避免 TTS 念出
                lines = [_strip_tts_speaker_prefix(line, character_names) for line in re.split(r"[\n]+", tts_text)]
                tts_text = "\n".join(l for l in lines if l).strip()
            if tts_text:
                resolved_voice = (voice_id or "").strip()
                if not resolved_voice:
                    resolved_voice = infer_voice_for_drama(tts_text)
                emotion = volcano_speech.infer_emotion_from_text(tts_text) if TTS_ENGINE == "volcano" else None
                try:
                    result = _get_text_to_speech()(
                        tts_text[:10000],
                        voice_id=resolved_voice or None,
                        emotion=emotion,
                        speed=TTS_DRAMA_SPEED if pipeline == "script_drama" else 50,
                    )
                    voice_path = result[0] if len(result) > 0 else None
                    err = result[1] if len(result) > 1 else None
                except Exception as e:
                    logger.warning("BGM/voiceover: TTS exception %s", e)
                    voice_path, err = None, str(e)
                if err or not voice_path:
                    logger.warning("BGM/voiceover: TTS failed err=%s voice_path=%s", err, voice_path)
                    if not with_bgm:
                        return (merged_url, None, None)
        if with_bgm:
            bgm_prompt = (bgm_style_prompt or "轻快, 短视频背景音乐, 无歌词, instrumental").strip()[:2000]
            bgm_path, err = generate_bgm(prompt=bgm_prompt)
            if err or not bgm_path:
                logger.warning("BGM/voiceover: BGM failed err=%s bgm_path=%s", err, bgm_path)
                bgm_path = None
        if not voice_path and not bgm_path:
            return (merged_url, None, None)
        name = merged_url.strip().replace("/api/merged/", "").strip("/")
        base, _ = os.path.splitext(name)
        if not base or ".." in base:
            base = "audio"
        MERGED_DIR.mkdir(parents=True, exist_ok=True)
        voice_dest = None
        bgm_dest = None
        if voice_path and os.path.isfile(voice_path):
            voice_dest = MERGED_DIR / f"{base}_voice.mp3"
            # 短剧按镜配音已直接写入 voice_dest，避免 copy2 自复制或文件占用
            if os.path.abspath(voice_path) != os.path.abspath(voice_dest):
                shutil.copy2(voice_path, voice_dest)
            voiceover_url = f"/api/merged/{voice_dest.name}"
        if bgm_path and os.path.isfile(bgm_path):
            bgm_dest = MERGED_DIR / f"{base}_bgm.mp3"
            shutil.copy2(bgm_path, bgm_dest)
            bgm_url = f"/api/merged/{bgm_dest.name}"
        # 混音使用 MERGED_DIR 内文件，与成片同目录，保证 BGM/配音 正常合成
        mix_voice = voice_dest if (voice_dest and voice_dest.is_file()) else (bgm_dest if (bgm_dest and bgm_dest.is_file()) else None)
        mix_bgm = bgm_dest if (voice_dest and voice_dest.is_file() and bgm_dest and bgm_dest.is_file()) else None
        if not mix_voice:
            return (merged_url, voiceover_url, bgm_url)
        bgm_vol = 0.15 if (pipeline == "script_drama" and mix_bgm) else 0.25
        sfx_path = str(DRAMA_SFX_PATH) if (pipeline == "script_drama" and DRAMA_SFX_PATH) else None
        new_url = mix_audio_into_merged(
            merged_url, str(mix_voice), str(mix_bgm) if mix_bgm else None,
            sfx_mp3_path=sfx_path,
            voice_volume=1.0, bgm_volume=bgm_vol, sfx_volume=0.2,
        )
        if not new_url:
            logger.warning("BGM/voiceover: mix_audio_into_merged returned None")
        else:
            logger.info("BGM/voiceover: 已混入成片 %s -> %s", name, (MERGED_DIR / name.replace(".mp4", "_vo.mp4")).name)
        return (new_url or merged_url, voiceover_url, bgm_url)
    finally:
        merged_resolved = Path(MERGED_DIR).resolve()
        for p in {voice_path, bgm_path}:
            if p and os.path.isfile(p):
                try:
                    if merged_resolved not in Path(p).resolve().parents:
                        os.unlink(p)
                except Exception:
                    pass


def _resolve_character_reference(req) -> str | None:
    """从演员列表或兼容 URL 中解析出用于 S2V 的参考图。支持 VideoRequest / CreateRequest。"""
    refs = getattr(req, "character_references", None)
    if refs:
        for item in refs:
            if getattr(item, "role", None) == "主角" and (getattr(item, "image_base64", None) or "").strip():
                return (getattr(item, "image_base64", None) or "").strip()
        for item in refs:
            if getattr(item, "role", None) == "配角" and (getattr(item, "image_base64", None) or "").strip():
                return (getattr(item, "image_base64", None) or "").strip()
    ref_url = getattr(req, "character_reference_image", None) or ""
    if ref_url.strip():
        return ref_url.strip()
    return None


def _save_character_refs_and_build_urls(
    character_references: list,
    backend_public_url: Optional[str] = None,
) -> tuple[list[dict], Optional[str]]:
    """
    将角色参考图（data URL/base64）保存到 static/merged/character_refs/{job_id}/ref_{i}.jpg，
    返回 ([{name, role, url}, ...], job_id)。
    可灵从外网拉图，仅支持 http(s) URL。未配置 BACKEND_PUBLIC_URL 时 url 为相对路径，参考图不会传给可灵。
    """
    refs_with_image: list[tuple[str, str, bytes]] = []  # (name, role, raw_bytes)
    for item in character_references:
        raw = (getattr(item, "image_base64", None) or "").strip()
        if not raw:
            continue
        if raw.startswith("data:"):
            idx = raw.find("base64,")
            raw = raw[idx + 7:] if idx >= 0 else ""
        try:
            raw_bytes = base64.b64decode(raw, validate=True)
        except Exception:
            continue
        name = (getattr(item, "name", None) or "").strip() or f"角色{len(refs_with_image)}"
        role = getattr(item, "role", "主角") or "主角"
        refs_with_image.append((name, role, raw_bytes))
    if not refs_with_image:
        return ([], None)
    job_id = uuid.uuid4().hex
    out_dir = CHAR_REF_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    base = (backend_public_url or "").strip().rstrip("/")
    result: list[dict] = []
    for i, (name, role, raw_bytes) in enumerate(refs_with_image):
        path = out_dir / f"ref_{i}.jpg"
        path.write_bytes(raw_bytes)
        url = f"{base}/api/character-refs/{job_id}/ref_{i}.jpg" if base else f"/api/character-refs/{job_id}/ref_{i}.jpg"
        result.append({"name": name, "role": role, "url": url})
    return (result, job_id)


@app.get("/api/voices")
def list_voice_options():
    """返回可选配音音色列表。TTS_ENGINE=volcano 时返回火山豆包音色（含多情感/情景角色）；否则返回讯飞超拟人。"""
    if TTS_ENGINE == "volcano":
        return [
            {
                "id": v[0],
                "name": v[1],
                "language": v[2],
                "gender": v[3],
                "emotions": v[5] if v[4] else None,
            }
            for v in volcano_speech.VOLCANO_VOICE_OPTIONS
        ]
    return [
        {"id": vcn, "name": name, "language": lang, "gender": gender}
        for vcn, name, lang, gender in iflytek_speech.VOICE_OPTIONS
    ]


@app.post("/api/video")
def generate_video(req: VideoRequest):
    """步骤3：根据分镜生成视频（短剧用可灵）。"""
    ref_image = _resolve_character_reference(req)
    backend_public_url = os.getenv("BACKEND_PUBLIC_URL", "").strip() or None
    character_refs_with_urls: Optional[list[dict]] = None
    if req.character_references and any(getattr(r, "image_base64", None) and (getattr(r, "image_base64") or "").strip() for r in req.character_references):
        character_refs_with_urls, _ = _save_character_refs_and_build_urls(req.character_references, backend_public_url)
        if character_refs_with_urls and not (backend_public_url and backend_public_url.startswith("http")):
            logger.warning("角色参考图已保存但 BACKEND_PUBLIC_URL 未配置或非 http(s)，可灵无法拉取参考图，请配置 ngrok 等公网地址")
    if not character_refs_with_urls:
        character_refs_with_urls = None
    pipeline_type = getattr(req, "pipeline", None)
    wait_before_concat = getattr(req, "wait_for_tasks_before_concat", True)
    video_out = run_video_generation(
        req.storyboard,
        script_summary=req.script_summary,
        character_reference_image=ref_image if not character_refs_with_urls else None,
        character_references_with_urls=character_refs_with_urls,
        pipeline=req.pipeline or "script_drama",
        wait_and_download=wait_before_concat,
        backend_public_url=backend_public_url,
    )
    download_urls = video_out.get("download_urls", [])
    if not wait_before_concat and video_out.get("task_ids") and pipeline_type == "script_drama":
        return {
            "video_mode": video_out.get("video_mode", ""),
            "task_ids": video_out.get("task_ids", []),
            "download_urls": [],
            "status_by_task": video_out.get("status_by_task", {}),
            "error": video_out.get("error"),
            "merged_download_url": None,
            "voiceover_download_url": None,
            "bgm_download_url": None,
        }
    # 可灵：先拿到各段 URL，再下载本地、最后剪辑。短剧+配音时需 segment_durations 做按镜对齐
    with_voiceover = getattr(req, "with_voiceover", False)
    need_segment_durations = pipeline_type == "script_drama" and with_voiceover and getattr(req, "storyboard", None) and len(getattr(req, "storyboard", [])) > 0
    # 短剧：若需要「配音对齐画面」，先按镜生成 TTS 并推导目标镜头时长，再按目标时长裁切/补齐各镜头
    # 使用统一的 _build_drama_tts_and_target_durations 函数，确保「先 TTS 再裁片」逻辑一致
    prebuilt_tts_paths: Optional[list[Optional[str]]] = None
    target_durations: Optional[list[float]] = None
    shot_emotions: Optional[list[Optional[str]]] = None
    shots_for_voice = None
    character_names_for_voice = None
    if pipeline_type == "script_drama" and with_voiceover and download_urls and getattr(req, "storyboard", None):
        try:
            shots_for_voice = req.storyboard[: len(download_urls)]
            character_names_for_voice = _character_names_for_voiceover(
                character_references=getattr(req, "character_references", None),
                storyboard=shots_for_voice,
            )
            # 调用统一函数：先按镜生成 TTS，得到真实时长，再计算目标镜头时长（TTS 时长 + 尾缓冲）
            prebuilt_tts_paths, target_durations, shot_emotions = _build_drama_tts_and_target_durations(
                shots_for_voice,
                character_references=getattr(req, "character_references", None),
                voice_id=getattr(req, "voice_id", None),
                shot_voice_ids=getattr(req, "shot_voice_ids", None),
                script_summary=getattr(req, "script_summary", None) or "",
            )
            if not target_durations or len(target_durations) != len(shots_for_voice):
                logger.warning("drama align: TTS/target durations count mismatch, fallback to storyboard duration_sec")
                prebuilt_tts_paths = None
                target_durations = None
                shot_emotions = None
        except Exception as e:
            logger.warning("drama align: build prebuilt TTS/target durations failed: %s", e)
            prebuilt_tts_paths = None
            target_durations = None
            shot_emotions = None
    # 短剧：无配音预计算时用分镜 duration_sec 作为目标时长兜底
    if pipeline_type == "script_drama" and getattr(req, "storyboard", None) and download_urls and not target_durations:
        n = len(download_urls)
        story = req.storyboard[:n]
        target_durations = []
        # 兜底也要遵守「台词念完镜头才过」：若 duration_sec 太短则按台词估算拉长
        try:
            character_names_for_voice = _character_names_for_voiceover(
                character_references=getattr(req, "character_references", None),
                storyboard=story,
            )
        except Exception:
            character_names_for_voice = None
        try:
            tail_pad = float(os.getenv("DRAMA_TTS_TAIL_PAD_SEC", "0.25") or 0.25)
        except Exception:
            tail_pad = 0.25
        try:
            min_shot_sec = float(os.getenv("DRAMA_MIN_SHOT_SEC", "1.0") or 1.0)
        except Exception:
            min_shot_sec = 1.0
        min_shot_sec = max(2.0, min_shot_sec)  # 短剧分镜最短 2 秒
        for i, s in enumerate(story):
            copy = _shot_copy(s)
            # 若台词长度按正常语速明显念不完，则把目标时长抬到估算时长+尾缓冲
            need_min = None
            try:
                tts_content = _strip_tts_speaker_prefix(copy, character_names_for_voice)
                if tts_content and not _is_action_only_no_speech(tts_content):
                    need_min = max(min_shot_sec, _estimate_dialogue_duration_sec(tts_content) + max(0.0, tail_pad))
            except Exception:
                need_min = None
            try:
                d = float(getattr(s, "duration_sec", None) or (s.get("duration_sec") if isinstance(s, dict) else None) or 0)
            except (TypeError, ValueError):
                d = 0.0
            if d <= 0:
                d = 4.0
            if need_min is not None:
                d = max(d, float(need_min))
            # 允许 >5 秒：对白较长时短剧镜头可更长（上限 10 秒）
            target_durations.append(max(2.0, min(10.0, d)))
        while len(target_durations) < n:
            target_durations.append(4.0)
    merged_url = None
    segment_durations: list[float] = []
    if req.concat_segments and len(download_urls) >= 2:
        # 短剧且有分镜时一律按 target_durations 做剪辑再拼接；有配音时 need_segment_durations 为 True 还需 segment_durations
        use_durations = need_segment_durations or (pipeline_type == "script_drama" and target_durations and len(target_durations) >= len(download_urls))
        if use_durations:
            merged_url, segment_durations = concat_video_segments_with_durations(
                download_urls,
                with_transitions=getattr(req, "with_transitions", True),
                target_durations=target_durations[: len(download_urls)] if target_durations else None,
                retime_to_target=bool(target_durations),
            )
        else:
            merged_url = concat_video_segments(download_urls, with_transitions=getattr(req, "with_transitions", True))
    elif len(download_urls) == 1:
        single_target = (target_durations[0] if (target_durations and len(target_durations) >= 1) else None)
        if need_segment_durations or (pipeline_type == "script_drama" and single_target is not None):
            merged_url, segment_durations = single_segment_to_merged_with_duration(
                download_urls[0],
                target_duration_sec=single_target,
            )
        else:
            merged_url = single_segment_to_merged(download_urls[0])
    voiceover_url = None
    bgm_url = None
    with_captions = getattr(req, "with_captions", False)
    with_stickers = getattr(req, "with_stickers", False)
    # 短剧：每一句对白都加字幕，有分镜时默认烧录字幕（与配音一一对应）
    if pipeline_type == "script_drama" and getattr(req, "storyboard", None) and merged_url:
        with_captions = True
    if merged_url and (with_captions or with_stickers):
        merged_url = _postprocess_visuals(
            merged_url,
            getattr(req, "storyboard", None),
            pipeline_type,
            with_captions,
            with_stickers,
            getattr(req, "subtitle_style", "clean"),
            getattr(req, "sticker_style", "film"),
            getattr(req, "title_caption_style", None),
            getattr(req, "caption_narration", True),
            segment_durations=segment_durations if pipeline_type == "script_drama" else None,
        )
    if merged_url and (getattr(req, "with_bgm", False) or with_voiceover):
        # 短剧：按镜配音，传入 storyboard + segment_durations 使台词与画面一一对应
        if pipeline_type == "script_drama" and with_voiceover and segment_durations and getattr(req, "storyboard", None):
            shots_for_voice = (shots_for_voice or req.storyboard)[: len(segment_durations)]
            merged_url, voiceover_url, bgm_url = _add_bgm_and_voiceover(
                merged_url,
                req.script_summary or "",
                False,
                True,
                pipeline=pipeline_type,
                storyboard=shots_for_voice,
                segment_durations=segment_durations,
                voice_id=getattr(req, "voice_id", None),
                character_names=character_names_for_voice or _character_names_for_voiceover(
                    character_references=getattr(req, "character_references", None),
                    storyboard=shots_for_voice,
                ),
                shot_voice_ids=getattr(req, "shot_voice_ids", None),
                prebuilt_tts_paths=prebuilt_tts_paths,
                voice_align_mode="pad_trim" if target_durations else "time_stretch",
                shot_emotions=shot_emotions,
                script_summary_for_emotion=req.script_summary or "",
            )
        else:
            tts_text = req.script_summary or ""
            merged_url, voiceover_url, bgm_url = _add_bgm_and_voiceover(
                merged_url,
                tts_text,
                getattr(req, "with_bgm", False),
                with_voiceover,
                pipeline=pipeline_type,
                voice_id=getattr(req, "voice_id", None),
                character_names=_character_names_for_voiceover(
                    character_references=getattr(req, "character_references", None),
                    storyboard=getattr(req, "storyboard", None),
                ) if pipeline_type == "script_drama" else None,
            )
    # 若短剧对齐模式生成了预先 TTS，但最终未走混音（例如合成失败），这里兜底清理临时文件
    if prebuilt_tts_paths and (not merged_url or not voiceover_url):
        try:
            merged_resolved = Path(MERGED_DIR).resolve()
        except Exception:
            merged_resolved = None
        for p in prebuilt_tts_paths:
            if p and os.path.isfile(p):
                try:
                    if not merged_resolved or merged_resolved not in Path(p).resolve().parents:
                        os.unlink(p)
                except Exception:
                    pass
    return VideoGenerationResult(
        video_mode=video_out.get("video_mode", ""),
        task_ids=video_out.get("task_ids", []),
        download_urls=download_urls,
        status_by_task=video_out.get("status_by_task", {}),
        error=video_out.get("error"),
        video_mode_reason=video_out.get("video_mode_reason"),
        merged_download_url=merged_url,
        voiceover_download_url=voiceover_url,
        bgm_download_url=bgm_url,
    )


@app.post("/api/video/regenerate-shot")
def regenerate_shot(req: RegenerateShotRequest):
    """短剧：对单个镜头按（可选）覆盖的提示词重新生成，返回该镜的下载 URL。支持多角色按镜拉取参考图。"""
    if req.shot_index < 0 or req.shot_index >= len(req.storyboard):
        raise HTTPException(status_code=400, detail="shot_index 超出分镜范围")
    shot = req.storyboard[req.shot_index]
    if req.override_t2v_prompt and req.override_t2v_prompt.strip():
        shot = shot.model_copy(update={"t2v_prompt": req.override_t2v_prompt.strip()})
    ref_image = None
    character_refs_with_urls = None
    backend_public_url = os.getenv("BACKEND_PUBLIC_URL", "").strip() or None
    if req.character_references and any(getattr(r, "image_base64", None) and (getattr(r, "image_base64") or "").strip() for r in req.character_references):
        character_refs_with_urls, _ = _save_character_refs_and_build_urls(req.character_references, backend_public_url)
    if not character_refs_with_urls:
        if req.character_references:
            for item in req.character_references:
                if item.role == "主角" and item.image_base64 and item.image_base64.strip():
                    ref_image = item.image_base64.strip()
                    break
            if not ref_image:
                for item in req.character_references:
                    if item.role == "配角" and item.image_base64 and item.image_base64.strip():
                        ref_image = item.image_base64.strip()
                        break
        if not ref_image and req.character_reference_image and req.character_reference_image.strip():
            ref_image = req.character_reference_image.strip()
    video_out = run_video_generation(
        [shot],
        script_summary="",
        pipeline="script_drama",
        character_reference_image=ref_image if not character_refs_with_urls else None,
        character_references_with_urls=character_refs_with_urls,
        wait_and_download=True,
        backend_public_url=backend_public_url,
    )
    download_urls = video_out.get("download_urls", [])
    if not download_urls:
        raise HTTPException(
            status_code=502,
            detail=video_out.get("error") or "该镜生成失败",
        )
    return {"download_url": download_urls[0], "shot_index": req.shot_index}


@app.post("/api/video/voiceover-only")
def voiceover_only(req: VoiceoverOnlyRequest):
    """仅对已有成片按分镜添加配音（不重新生成视频）。用分镜的 duration_sec 作为每镜时长拼配音轨后混入。"""
    if not req.merged_url or not req.storyboard:
        raise HTTPException(status_code=400, detail="merged_url 与 storyboard 不能为空")
    # 成片必须在 MERGED_DIR 下
    name = req.merged_url.strip().replace("/api/merged/", "").strip("/")
    if not name or ".." in name:
        raise HTTPException(status_code=400, detail="merged_url 格式须为 /api/merged/xxx.mp4")
    video_path = MERGED_DIR / name
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail=f"成片不存在: {name}")
    segment_durations = []
    for s in req.storyboard:
        dur = getattr(s, "duration_sec", None) or (s.get("duration_sec") if isinstance(s, dict) else None)
        try:
            segment_durations.append(max(1.5, min(10, float(dur or 5))))
        except (TypeError, ValueError):
            segment_durations.append(5.0)
    # 仅配音场景：用成片实际时长对齐，避免配音总长与画面不一致、时间点错位；按比例缩放每镜时长
    video_duration = _ffprobe_duration_sec(str(video_path))
    if video_duration and video_duration > 0 and segment_durations:
        total = sum(segment_durations)
        if total > 0 and abs(total - video_duration) > 0.5:
            scale = video_duration / total
            segment_durations = [max(1.0, d * scale) for d in segment_durations]
    merged_url = req.merged_url if req.merged_url.startswith("/") else f"/api/merged/{name}"
    try:
        merged_url, voiceover_url, _ = _add_bgm_and_voiceover(
            merged_url,
            req.script_summary or "",
            False,
            True,
            pipeline="script_drama",
            storyboard=req.storyboard,
            segment_durations=segment_durations,
            voice_id=req.voice_id,
            character_names=_character_names_for_voiceover(storyboard=req.storyboard),
            shot_voice_ids=req.shot_voice_ids,
            script_summary_for_emotion=req.script_summary or "",
            voice_align_mode="pad_trim",
        )
    except Exception as e:
        logger.exception("voiceover-only 执行失败")
        raise HTTPException(status_code=500, detail=f"配音流程失败: {type(e).__name__}: {e}")
    return {"merged_download_url": merged_url, "voiceover_download_url": voiceover_url}


@app.get("/api/video/kling-task-status")
def kling_task_status(task_ids: str, use_omni: bool = True):
    """
    查询可灵任务状态，供前端按镜头展示：绿 succeed / 蓝 processing / 红 failed。
    task_ids 为逗号分隔的 task_id 列表。全部成功时 all_succeed 为 true，再调用 POST /api/video/concat-after-kling-tasks 进行剪辑。
    """
    ids = [x.strip() for x in (task_ids or "").split(",") if x and x.strip()]
    if not ids:
        return {"items": [], "all_succeed": False}
    items = get_kling_task_status_batch(ids, use_omni=use_omni)
    all_succeed = all(item.get("status") == "succeed" for item in items)
    return {"items": items, "all_succeed": all_succeed}


@app.post("/api/video/concat-after-kling-tasks")
def concat_after_kling_tasks(req: ConcatAfterKlingTasksRequest):
    """
    可灵任务全部成功后再下载并剪辑成片。前端轮询 kling-task-status 直到 all_succeed 后调用本接口，
    使用任务返回的下载 URL 立即下载（减少过期/超时），再拼接、字幕、配音。
    """
    if not req.task_ids or not req.storyboard:
        raise HTTPException(status_code=400, detail="task_ids 与 storyboard 不能为空")
    items = get_kling_task_status_batch(req.task_ids, use_omni=req.use_omni)
    by_id = {x["task_id"]: x for x in items}
    download_urls: list[str] = []
    for tid in req.task_ids:
        x = by_id.get(tid, {})
        if x.get("status") != "succeed":
            raise HTTPException(
                status_code=400,
                detail=f"任务 {tid} 未成功（状态: {x.get('status', 'unknown')}），请等全部任务成功后再剪辑。",
            )
        url = x.get("url")
        if not url:
            raise HTTPException(status_code=400, detail=f"任务 {tid} 成功但无下载地址")
        download_urls.append(url)
    if len(download_urls) != len(req.task_ids):
        raise HTTPException(status_code=400, detail="部分任务无下载地址")

    # 第一步：根据链接把素材下载到本地，再走剪辑逻辑（先 TTS 定时长 -> 裁切 -> 拼接 -> 字幕 -> 配音）
    job_id = uuid.uuid4().hex
    logger.info("concat_after_kling: 根据 %d 个链接下载素材…", len(download_urls))
    local_paths = download_segments_to_backup(download_urls, job_id)
    if not local_paths:
        raise HTTPException(status_code=502, detail="视频下载失败")
    if len(local_paths) < len(download_urls):
        raise HTTPException(status_code=502, detail="部分视频下载失败（网络超时），请稍后重试")
    logger.info("concat_after_kling: 素材下载完成共 %d 个，开始剪辑（先 TTS 定时长、裁切、拼接、字幕、配音）", len(local_paths))

    # 有配音时先按 TTS 时长分配画面，避免话没读完就切
    target_durations: Optional[list[float]] = None
    prebuilt_tts_paths: Optional[list[Optional[str]]] = None
    shot_emotions: Optional[list[Optional[str]]] = None
    if req.with_voiceover and req.storyboard and len(req.storyboard) >= len(local_paths):
        try:
            shots_for_concat = req.storyboard[: len(local_paths)]
            prebuilt_tts_paths, target_durations, shot_emotions = _build_drama_tts_and_target_durations(
                shots_for_concat,
                character_references=getattr(req, "character_references", None),
                voice_id=req.voice_id,
                shot_voice_ids=req.shot_voice_ids,
                script_summary=req.script_summary or "",
            )
            if target_durations and len(target_durations) == len(local_paths):
                # 按 TTS 时长裁切镜头
                retimed = retime_local_segments_to_durations(local_paths, target_durations)
                segment_durations = [_ffprobe_duration_sec(str(p)) or 5.0 for p in retimed]
                merged_url = concat_local_segments(
                    retimed,
                    with_transitions=False,  # 转场会让镜头时间轴发生叠加，不利于「镜头-对白」一一对齐
                )
            else:
                # TTS 生成失败，回退到原逻辑
                target_durations = None
                prebuilt_tts_paths = None
                shot_emotions = None
                segment_durations = [_ffprobe_duration_sec(str(p)) or 5.0 for p in local_paths]
                merged_url = concat_local_segments(
                    local_paths,
                    with_transitions=req.with_transitions,
                )
        except Exception as e:
            logger.warning("concat_after_kling: TTS-first alignment failed, fallback: %s", e)
            target_durations = None
            prebuilt_tts_paths = None
            shot_emotions = None
            segment_durations = [_ffprobe_duration_sec(str(p)) or 5.0 for p in local_paths]
            merged_url = concat_local_segments(
                local_paths,
                with_transitions=req.with_transitions,
            )
    else:
        segment_durations = [_ffprobe_duration_sec(str(p)) or 5.0 for p in local_paths]
        merged_url = concat_local_segments(
            local_paths,
            with_transitions=req.with_transitions,
        )
    if not merged_url:
        raise HTTPException(
            status_code=502,
            detail="视频拼接失败（若为 Windows 请确认 ffmpeg 可用；详见后端日志 concat_local_segments）",
        )
    # 短剧：默认烧录字幕
    use_captions = True
    if use_captions and req.storyboard:
        merged_url = _postprocess_visuals(
            merged_url,
            req.storyboard,
            "script_drama",
            True,
            False,
            "clean",
            "film",
            None,
            True,
            segment_durations=segment_durations,
        )
    voiceover_url = None
    bgm_url = None
    if req.with_voiceover and req.storyboard and len(segment_durations) >= len(req.storyboard):
        # 复用预生成的 TTS（如果已生成），避免重复合成
        merged_url, voiceover_url, bgm_url = _add_bgm_and_voiceover(
            merged_url,
            " ".join(_shot_copy(s) for s in req.storyboard),
            req.with_bgm,
            req.with_voiceover,
            pipeline="script_drama",
            storyboard=req.storyboard,
            segment_durations=segment_durations,
            voice_id=req.voice_id,
            character_names=_character_names_for_voiceover(
                character_references=getattr(req, "character_references", None),
                storyboard=req.storyboard,
            ),
            shot_voice_ids=req.shot_voice_ids,
            prebuilt_tts_paths=prebuilt_tts_paths,
            voice_align_mode="pad_trim" if target_durations else "time_stretch",
            shot_emotions=shot_emotions,
            script_summary_for_emotion=req.script_summary or "",
        )
    return {
        "merged_download_url": merged_url,
        "voiceover_download_url": voiceover_url,
        "bgm_download_url": bgm_url,
    }


@app.post("/api/video/concat-from-segments")
def concat_from_segments(req: ConcatFromSegmentsRequest):
    """短剧：从已有分段视频 URL 剪辑成片（转场 + 字幕/画面标题 + 可选按镜配音），无 BGM。"""
    if not req.segment_urls or not req.storyboard:
        raise HTTPException(status_code=400, detail="segment_urls 与 storyboard 不能为空")
    backend_public_url = (os.getenv("BACKEND_PUBLIC_URL") or "").strip().rstrip("/")
    urls = []
    for u in req.segment_urls:
        u = (u or "").strip()
        if not u:
            continue
        if u.startswith("/") and backend_public_url:
            u = backend_public_url + u
        urls.append(u)
    if len(urls) != len(req.segment_urls):
        raise HTTPException(status_code=400, detail="segment_urls 含空项")
    job_id = uuid.uuid4().hex
    local_paths = download_segments_to_backup(urls, job_id)
    if not local_paths:
        raise HTTPException(status_code=502, detail="分段视频下载失败")
    if len(local_paths) < len(urls):
        raise HTTPException(status_code=502, detail="部分分段视频下载失败（网络超时），请稍后重试")
    segment_durations = [_ffprobe_duration_sec(str(p)) or 5.0 for p in local_paths]
    merged_url = concat_local_segments(
        local_paths,
        with_transitions=req.with_transitions,
    )
    if not merged_url:
        raise HTTPException(status_code=502, detail="视频拼接失败")
    # 有配音或勾选字幕时做视觉后期；有配音时一律烧录字幕，保证每句对白都有字幕
    use_captions = req.with_captions or req.with_voiceover
    if use_captions:
        merged_url = _postprocess_visuals(
            merged_url,
            req.storyboard,
            "script_drama",
            use_captions,
            False,
            "clean",
            "film",
            req.title_caption_style,
            getattr(req, "caption_narration", True),
            segment_durations=segment_durations,
        )
    voiceover_url = None
    if req.with_voiceover and req.storyboard and len(segment_durations) >= len(req.storyboard):
        merged_url, voiceover_url, _ = _add_bgm_and_voiceover(
            merged_url,
            "",
            False,
            True,
            pipeline="script_drama",
            storyboard=req.storyboard,
            segment_durations=segment_durations,
            voice_id=getattr(req, "voice_id", None),
            character_names=_character_names_for_voiceover(storyboard=req.storyboard),
            shot_voice_ids=getattr(req, "shot_voice_ids", None),
        )
    return {"merged_download_url": merged_url, "voiceover_download_url": voiceover_url}


# ---------- 数据持久化：任务 CRUD ----------


@app.post("/api/tasks")
def api_create_task(body: TaskCreate):
    """保存一条创作任务，返回 task_id。可含 character_references 快照，刷新后可恢复角色与配音。"""
    task_id = create_task(
        pipeline=body.pipeline,
        input_text=body.input,
        content_result=body.content_result,
        video_result=body.video_result,
        merged_download_url=body.merged_download_url,
        title=body.title,
        character_references=getattr(body, "character_references", None),
    )
    return {"id": task_id}


@app.get("/api/tasks", response_model=list[TaskSummary])
def api_list_tasks(
    pipeline: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """分页列出任务摘要。"""
    rows = list_tasks(pipeline=pipeline, limit=limit, offset=offset)
    return [TaskSummary(**r) for r in rows]


@app.get("/api/tasks/{task_id}", response_model=TaskDetail)
def api_get_task(task_id: str):
    """按 id 获取完整任务。"""
    from fastapi import HTTPException
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return TaskDetail(**task)


@app.patch("/api/tasks/{task_id}")
def api_update_task(task_id: str, body: TaskUpdate):
    """更新任务（仅更新传入的字段）。"""
    from fastapi import HTTPException
    ok = update_task(
        task_id,
        title=body.title,
        content_result=body.content_result,
        video_result=body.video_result,
        merged_download_url=body.merged_download_url,
        character_references=getattr(body, "character_references", None),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True}


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: str):
    """删除一条任务。"""
    from fastapi import HTTPException
    ok = delete_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True}


# ---------- 会员与积分 API（需请求头 X-Device-ID 标识用户） ----------

SIGN_IN_POINTS = 10


def _get_user_id_from_header(x_device_id: Optional[str] = Header(None, alias="X-Device-ID")):
    """从请求头 X-Device-ID 解析用户 id（无则 400）。"""
    if not (x_device_id and x_device_id.strip()):
        raise HTTPException(status_code=400, detail="请提供 X-Device-ID 请求头以标识用户")
    return get_or_create_user_by_device(x_device_id.strip())


@app.get("/api/membership/tiers", response_model=list[MembershipTier])
def api_list_membership_tiers():
    """获取所有会员档位配置（公开）。"""
    rows = list_membership_tiers()
    return [MembershipTier(**r) for r in rows]


@app.get("/api/me/profile")
def api_me_profile(user_id: str = Depends(_get_user_id_from_header)):
    """当前用户摘要：档位、今日配额使用、积分余额。需 X-Device-ID。"""
    tier = get_user_effective_tier(user_id)
    balance = get_user_balance(user_id)
    content_used, video_used = get_daily_usage(user_id)
    can_use, total_used, quota = check_can_use_quota(user_id)
    return {
        "user_id": user_id,
        "membership": UserMembershipSummary(
            tier_code=tier["code"],
            tier_name=tier["name"],
            level=tier["level"],
            daily_task_quota=tier["daily_task_quota"],
            max_storyboard_shots=tier["max_storyboard_shots"],
            can_export_merged_video=tier["can_export_merged_video"],
            expires_at=tier.get("expires_at"),
        ),
        "points": PointBalance(user_id=user_id, balance=balance),
        "usage_today": {"content_count": content_used, "video_count": video_used, "total_used": total_used, "quota": quota, "can_use": can_use},
    }


@app.get("/api/me/membership", response_model=UserMembershipSummary)
def api_me_membership(user_id: str = Depends(_get_user_id_from_header)):
    """当前用户会员摘要。需 X-Device-ID。"""
    tier = get_user_effective_tier(user_id)
    return UserMembershipSummary(
        tier_code=tier["code"],
        tier_name=tier["name"],
        level=tier["level"],
        daily_task_quota=tier["daily_task_quota"],
        max_storyboard_shots=tier["max_storyboard_shots"],
        can_export_merged_video=tier["can_export_merged_video"],
        expires_at=tier.get("expires_at"),
    )


@app.get("/api/me/points", response_model=PointBalance)
def api_me_points(user_id: str = Depends(_get_user_id_from_header)):
    """当前用户积分余额。需 X-Device-ID。"""
    balance = get_user_balance(user_id)
    return PointBalance(user_id=user_id, balance=balance)


@app.get("/api/me/points/history", response_model=list[PointTransactionItem])
def api_me_points_history(limit: int = 50, offset: int = 0, user_id: str = Depends(_get_user_id_from_header)):
    """积分流水记录。需 X-Device-ID。"""
    rows = list_point_transactions(user_id, limit=limit, offset=offset)
    return [PointTransactionItem(**r) for r in rows]


@app.post("/api/me/points/sign-in", response_model=SignInResponse)
def api_me_sign_in(user_id: str = Depends(_get_user_id_from_header)):
    """每日签到，获得积分。需 X-Device-ID。"""
    if has_signed_in_today(user_id):
        raise HTTPException(status_code=400, detail="今日已签到")
    add_point_transaction(user_id, SIGN_IN_POINTS, "sign_in", description="每日签到")
    balance = get_user_balance(user_id)
    return SignInResponse(points_earned=SIGN_IN_POINTS, balance_after=balance)


@app.post("/api/me/membership/redeem")
def api_me_redeem_membership(body: RedeemMembershipRequest, user_id: str = Depends(_get_user_id_from_header)):
    """用积分兑换会员。需 X-Device-ID。"""
    tier = get_tier_by_code(body.tier_code)
    if not tier:
        raise HTTPException(status_code=400, detail="无效的档位")
    if tier["code"] == "free":
        raise HTTPException(status_code=400, detail="免费档位无需兑换")
    cost = tier["price_per_month_credits"] * body.months
    balance = get_user_balance(user_id)
    if balance < cost:
        raise HTTPException(status_code=400, detail=f"积分不足，需要 {cost} 积分，当前余额 {balance}")
    ok = deduct_points(user_id, cost, "redeem_membership", description=f"兑换{tier['name']}{body.months}个月")
    if not ok:
        raise HTTPException(status_code=400, detail="扣减积分失败")
    membership_id = create_user_membership(user_id, body.tier_code, body.months)
    if not membership_id:
        raise HTTPException(status_code=500, detail="开通会员失败")
    return {"ok": True, "membership_id": membership_id, "tier_code": body.tier_code, "months": body.months, "points_spent": cost}


@app.post("/api/create", response_model=CreateResponse, response_model_by_alias=True)
def create(req: CreateRequest):
    """
    统一创作入口：根据用户输入自动判断类型并执行对应管线。
    - 剧本/短剧 → script_drama：返回分镜表 + 文生视频用 Prompt 列表
    - 自然语言且意图不清 → clarify：返回引导文案
    """
    input_type, pipeline, debug_note = classify_input(req.input)

    if pipeline == "script_drama":
        result: ScriptDramaResult = run_script_drama_agent(req.input)
        if req.with_video and result.storyboard:
            script_summary = " ".join(
                (s.shot_desc + " " + _shot_copy(s))
                for s in result.storyboard[:10]
            ) if result.storyboard else ""
            backend_public_url = os.getenv("BACKEND_PUBLIC_URL", "").strip() or None
            character_refs_with_urls: Optional[list[dict]] = None
            ref_image = _resolve_character_reference(req)
            if getattr(req, "character_references", None) and any(
                getattr(r, "image_base64", None) and (getattr(r, "image_base64") or "").strip()
                for r in req.character_references
            ):
                character_refs_with_urls, _ = _save_character_refs_and_build_urls(req.character_references, backend_public_url)
            if not character_refs_with_urls:
                character_refs_with_urls = None
            wait_before_concat = getattr(req, "wait_for_tasks_before_concat", True)
            video_out = run_video_generation(
                result.storyboard,
                script_summary=script_summary[:800],
                character_reference_image=ref_image if not character_refs_with_urls else None,
                character_references_with_urls=character_refs_with_urls,
                pipeline="script_drama",
                wait_and_download=wait_before_concat,
                backend_public_url=backend_public_url,
            )
            download_urls = video_out.get("download_urls", [])
            merged_url = None
            segment_durations: list[float] = []
            prebuilt_tts_create: Optional[list[Optional[str]]] = None
            shot_emotions_create: Optional[list[Optional[str]]] = None
            # 仅创建任务、不等待下载剪辑时，直接返回 task_ids，由前端轮询状态后调 concat-after-kling-tasks
            if not wait_before_concat and video_out.get("task_ids"):
                result.video = VideoGenerationResult(
                    video_mode=video_out.get("video_mode", ""),
                    task_ids=video_out.get("task_ids", []),
                    download_urls=[],
                    status_by_task=video_out.get("status_by_task", {}),
                    error=video_out.get("error"),
                    merged_download_url=None,
                    voiceover_download_url=None,
                    bgm_download_url=None,
                )
                return CreateResponse(input_type=input_type, pipeline="script_drama", result=result, debug_router_note=debug_note)
            if req.concat_segments and len(download_urls) >= 2:
                job_id = uuid.uuid4().hex
                local_paths = download_segments_to_backup(download_urls, job_id)
                if not local_paths or len(local_paths) < len(download_urls):
                    merged_url = None
                    result.video = VideoGenerationResult(
                        video_mode=video_out.get("video_mode", ""),
                        task_ids=video_out.get("task_ids", []),
                        download_urls=download_urls,
                        status_by_task=video_out.get("status_by_task", {}),
                        error="部分视频片段下载失败（网络超时），请稍后重试",
                        merged_download_url=None,
                        voiceover_download_url=None,
                        bgm_download_url=None,
                    )
                    return CreateResponse(input_type=input_type, pipeline="script_drama", result=result, debug_router_note=debug_note)
                if local_paths:
                    # 有配音时先按 TTS 时长分配画面，最后一镜按配音时长，避免话没说完就切
                    if req.with_voiceover and result.storyboard and len(result.storyboard) >= len(local_paths):
                        prebuilt_tts_create, target_durations, shot_emotions_create = _build_drama_tts_and_target_durations(
                            result.storyboard[: len(local_paths)],
                            character_references=getattr(req, "character_references", None),
                            voice_id=getattr(req, "voice_id", None),
                            shot_voice_ids=getattr(req, "shot_voice_ids", None),
                            script_summary=getattr(req, "script_summary", None),
                        )
                        if target_durations and len(target_durations) == len(local_paths):
                            retimed = retime_local_segments_to_durations(local_paths, target_durations)
                            segment_durations = [_ffprobe_duration_sec(str(p)) or 5.0 for p in retimed]
                            merged_url = concat_local_segments(
                                retimed,
                                with_transitions=False,
                            )
                        else:
                            segment_durations = [_ffprobe_duration_sec(str(p)) or 5.0 for p in local_paths]
                            merged_url = concat_local_segments(
                                local_paths,
                                with_transitions=getattr(req, "with_transitions", True),
                            )
                    else:
                        segment_durations = [_ffprobe_duration_sec(str(p)) or 5.0 for p in local_paths]
                        merged_url = concat_local_segments(
                            local_paths,
                            with_transitions=getattr(req, "with_transitions", True),
                        )
                if not merged_url:
                    merged_url = concat_video_segments(download_urls, with_transitions=getattr(req, "with_transitions", True))
                    if merged_url and result.storyboard and len(download_urls) == len(result.storyboard):
                        segment_durations = [5.0] * len(result.storyboard)
            elif len(download_urls) == 1:
                merged_url = single_segment_to_merged(download_urls[0])
                if merged_url and result.storyboard:
                    single_d = _ffprobe_duration_sec(str(MERGED_DIR / merged_url.strip().replace("/api/merged/", "").strip("/")))
                    segment_durations = [single_d or 5.0]
            script_text = ""
            if result.storyboard:
                script_text = " ".join(_shot_copy(s) for s in result.storyboard)
            voiceover_url = None
            bgm_url = None
            with_captions = getattr(req, "with_captions", False)
            with_stickers = getattr(req, "with_stickers", False)
            # 短剧：每一句对白都加字幕，有分镜时默认烧录
            if result.storyboard and merged_url:
                with_captions = True
            if merged_url and (with_captions or with_stickers):
                merged_url = _postprocess_visuals(
                    merged_url,
                    result.storyboard,
                    "script_drama",
                    with_captions,
                    with_stickers,
                    getattr(req, "subtitle_style", "clean"),
                    getattr(req, "sticker_style", "film"),
                    getattr(req, "title_caption_style", None),
                    getattr(req, "caption_narration", True),
                    segment_durations=segment_durations if (segment_durations and result.storyboard and len(segment_durations) >= len(result.storyboard)) else None,
                )
            if merged_url and (req.with_bgm or req.with_voiceover):
                merged_url, voiceover_url, bgm_url = _add_bgm_and_voiceover(
                    merged_url,
                    script_text,
                    req.with_bgm,
                    req.with_voiceover,
                    pipeline="script_drama",
                    storyboard=result.storyboard,
                    segment_durations=segment_durations if (segment_durations and result.storyboard and len(segment_durations) >= len(result.storyboard)) else None,
                    voice_id=getattr(req, "voice_id", None),
                    character_names=_character_names_for_voiceover(
                        character_references=getattr(req, "character_references", None),
                        storyboard=result.storyboard,
                    ),
                    shot_voice_ids=getattr(req, "shot_voice_ids", None),
                    prebuilt_tts_paths=prebuilt_tts_create,
                    shot_emotions=shot_emotions_create,
                    script_summary_for_emotion=getattr(req, "script_summary", None) or "",
                )
            result.video = VideoGenerationResult(
                video_mode=video_out.get("video_mode", ""),
                task_ids=video_out.get("task_ids", []),
                download_urls=download_urls,
                status_by_task=video_out.get("status_by_task", {}),
                error=video_out.get("error"),
                merged_download_url=merged_url,
                voiceover_download_url=voiceover_url,
                bgm_download_url=bgm_url,
            )
        return CreateResponse(
            input_type=input_type,
            pipeline="script_drama",
            result=result,
            debug_router_note=debug_note,
        )

    # clarify
    if "短剧" in (req.input or "") or "剧本" in (req.input or "") or "分镜" in (req.input or ""):
        message = "检测到您可能想做短剧/剧情短视频。请粘贴剧本或对白内容，我将为您生成分镜与文生视频用 Prompt。"
        suggested = "script_drama"
    else:
        message = "请粘贴剧本或对白内容，我将为您生成短剧分镜与文生视频用 Prompt。"
        suggested = None

    return CreateResponse(
        input_type="natural_language",
        pipeline="clarify",
        result=ClarifyResult(suggested_pipeline=suggested, message=message),
        debug_router_note=debug_note,
    )
