"""视频生成 Agent：短剧视频由可灵（Kling）生成并返回下载链接。"""
import logging
import os
import re
import time
from typing import Optional, List

logger = logging.getLogger(__name__)

from app.schemas import StoryboardItem
from app.services.volcano_speech import text_to_speech
from app.services.kling_video import (
    has_kling,
    create_t2v_task as create_kling_t2v_task,
    create_omni_video_task,
    get_kling_download_url,
    query_kling_task,
    query_kling_omni_task,
)


# 可灵 prompt 上限约 1700，保留余量；描述越完整越利于生成质量
SHOT_PROMPT_MAX_LEN = 1600

# 兼容：__init__ 导出 has_minimax，当前视频生成走可灵，故与 has_kling 一致
has_minimax = has_kling


def _shot_prompt(s: StoryboardItem, pipeline: Optional[str] = None) -> str:
    """单镜文生视频用 prompt。内容优先、运镜为辅：重点写画面正在发生什么（场景与动作、情绪与氛围），镜头语言简写放在末尾。不包含分镜台词（台词仅用于字幕/配音）。"""
    base = (getattr(s, "t2v_prompt", None) or "").strip()
    shot_type = getattr(s, "shot_type", "") or "中景"
    camera = getattr(s, "camera_technique", None) or ""
    camera = (camera.strip() or "固定")
    # 短剧：仅当 t2v_prompt 明显是简短模板时才弃用，改由内容优先的拼装
    if pipeline == "script_drama" and base:
        if len(base) < 50 or re.match(r"^\d+[,，]\s*[^，]+[。.]\s*(固定|移动|跟随)", base.replace(" ", "")):
            base = ""
    if not base:
        subject = "人物"
        # 仅用画面描述，不加入台词（copy_text 仅用于字幕/配音）；内容为主，镜头语言一笔带过
        scene_action = s.shot_desc
        style = "自然光、电影感、画面有层次"
        cam_brief = f"{shot_type}，{camera}"
        base = f"{subject}（主体），{scene_action}（场景与动作），{style}（风格）。{cam_brief}。"
    # 轻量后处理：规范空格与换行，避免 API 解析异常；可灵等对过长 prompt 易失败，适度截断
    base = re.sub(r"[\r\n]+", " ", base)
    base = re.sub(r"  +", " ", base).strip()
    return base[:SHOT_PROMPT_MAX_LEN]

# 可灵视频生成策略：不限时、不并发。流程为：一个任务一个任务地做 → 每镜轮询至完成（无总时长上限）→ 返回 download_urls → 主流程将各段下载到本地 → 最后 ffmpeg 剪辑合并成片。


def _kling_create_with_retry(create_fn, *args, **kwargs):
    """创建可灵任务：遇资源包并发上限则长时间等待后重试，不限制次数，直到提交成功或非并发类错误。"""
    base_sleep = float(os.getenv("KLING_CREATE_RETRY_SLEEP_SEC", "30"))
    attempt = 0
    while True:
        tid, err = create_fn(*args, **kwargs)
        if tid or not err:
            return tid, err
        msg = str(err)
        if "parallel task over resource pack limit" in msg.lower():
            attempt += 1
            sleep_s = base_sleep * attempt  # 不设上限，可一直等
            logger.warning("可灵并发上限，等待 %.0fs 后重试（第 %d 次）", sleep_s, attempt)
            time.sleep(sleep_s)
            continue
        return tid, err


def _drain_kling_inflight(
    inflight: list[str],
    status_by_task: dict[str, str],
    download_urls: list[str],
    limit: Optional[int] = None,
    use_omni: bool = False,
) -> None:
    """依次等待 inflight 中的可灵任务完成并记录结果（会从 inflight 原地 pop）。Omni-Video 必须 use_omni=True 用 omni 接口查询。"""
    drained = 0
    query_fn = query_kling_omni_task if use_omni else query_kling_task
    while inflight:
        if limit is not None and drained >= limit:
            break
        tid = inflight.pop(0)
        url = get_kling_download_url(tid, use_omni=use_omni)
        if url:
            status_by_task[tid] = "Success"
            download_urls.append(url)
        else:
            st = query_fn(tid)
            status_by_task[tid] = st.get("status", "Fail")
        drained += 1


# 短剧：可灵文生视频，每镜一段；有角色参考图且为 HTTP URL 时可用 omni（需 backend_public_url）
# 小剧镜头数不限制，全部生成（商品短视频等其他流程另有上限）
SCRIPT_DRAMA_STYLE_PREFIX = os.getenv("SCRIPT_DRAMA_STYLE_PREFIX", "电影感、自然光、色调统一。")


def _estimate_dialogue_duration_sec(text: str) -> float:
    """粗略估算对白时长(秒), 用于选择可灵 5或10秒片段时长."""
    if not text:
        return 0.0
    t = " ".join(str(text).split()).strip()
    if not t:
        return 0.0
    # 去掉「旁白：」「角色名：」前缀，避免冒号前名字影响估算
    if t.startswith("旁白") and ("：" in t[:4] or ":" in t[:4]):
        t = t.split("：" if "：" in t[:4] else ":", 1)[-1].strip()
    else:
        # 仅剥掉非常短的前缀（一般是角色名），避免误伤正常句子里的冒号
        for sep in ("：", ":"):
            if sep in t:
                left, right = t.split(sep, 1)
                if 1 <= len(left) <= 6 and right.strip():
                    t = right.strip()
                break
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", t))
    en_words = len(re.findall(r"[A-Za-z0-9]+", t))
    punct = len(re.findall(r"[，,。.!！？?；;：:、】【「」\"\"''…—-]", t))
    zh_rate = float(os.getenv("DRAMA_SPEECH_ZH_CHARS_PER_SEC", "4.5") or 4.5)
    en_rate = float(os.getenv("DRAMA_SPEECH_EN_WORDS_PER_SEC", "2.8") or 2.8)
    pause = float(os.getenv("DRAMA_SPEECH_PUNCT_PAUSE_SEC", "0.10") or 0.10)
    base = (zh_chars / max(1e-3, zh_rate)) + (en_words / max(1e-3, en_rate))
    return max(0.8, base + punct * pause)


def _calculate_actual_dialogue_durations(storyboard: List[StoryboardItem]) -> List[float]:
    """计算每个分镜台词的实际时长(秒), 通过 TTS 生成语音并分析时长."""
    durations = []
    for shot in storyboard:
        copy = (getattr(shot, "copy_text", None) or getattr(shot, "copy", "") or "").strip()
        if not copy:
            durations.append(0.0)
            continue
        
        # 生成语音并获取实际时长
        audio_path, error, duration = text_to_speech(copy)
        if error:
            logger.warning(f"计算台词时长失败: {error}, 使用估算值")
            # 如果 TTS 失败, 使用估算值
            duration = _estimate_dialogue_duration_sec(copy)
        else:
            # 清理临时音频文件
            if audio_path and os.path.exists(audio_path):
                try:
                    os.unlink(audio_path)
                except Exception:
                    pass
        
        durations.append(duration)
    return durations


def _kling_duration_for_shot(s: StoryboardItem) -> str:
    """可灵文生/omni 时长仅支持 5或10秒. 这里按台词/分镜时长粗选一个更接近的."""
    try:
        d = float(getattr(s, "duration_sec", None) or 0)
    except Exception:
        d = 0.0
    if d <= 0:
        copy = (getattr(s, "copy_text", None) or getattr(s, "copy", "") or "").strip()
        d = _estimate_dialogue_duration_sec(copy)
    # 阈值稍向上，避免 6~7 秒的句子硬塞 5 秒导致后期大量补帧
    return "10" if d >= 7.0 else "5"


def _run_script_drama_kling(
    storyboard: list[StoryboardItem],
    character_reference_image: Optional[str] = None,
    character_references_with_urls: Optional[list[dict]] = None,
    backend_public_url: Optional[str] = None,
    wait_and_download: bool = True,
) -> dict:
    """短剧视频走可灵：文生视频每镜一段；有角色参考图且可转为公网 URL 时用 omni。多角色时按镜拉取 character_references_with_urls 中对应 character_name 的参考图。小剧镜头数不限制。"""
    shots = storyboard
    if not shots:
        return {
            "video_mode": "kling_t2v",
            "task_ids": [],
            "download_urls": [],
            "status_by_task": {},
            "error": "分镜为空",
        }
    if character_references_with_urls:
        n_lead = sum(1 for r in character_references_with_urls if r.get("role") == "主角")
        n_support = sum(1 for r in character_references_with_urls if r.get("role") == "配角")
        logger.info("短剧可灵: 角色参考图 主角=%s 配角=%s，总镜数=%s", n_lead, n_support, len(shots))
    # 多角色：按镜解析参考图 URL（支持主角+配角同镜时多张参考图）；单角色：沿用全局 character_reference_image
    def _shot_ref_urls(s: StoryboardItem) -> list[str]:
        if character_references_with_urls:
            return _resolve_shot_character_urls(s, character_references_with_urls)
        ref_url = (character_reference_image or "").strip()
        if ref_url.startswith("http://") or ref_url.startswith("https://"):
            return [ref_url]
        if ref_url.startswith("/") and (backend_public_url or "").strip():
            return [(backend_public_url or "").strip().rstrip("/") + ref_url]
        return []

    def _to_http_urls(url_list: list[str]) -> list[str]:
        base = (backend_public_url or "").strip().rstrip("/")
        out = []
        for u in url_list:
            if not u or not u.strip():
                continue
            u = u.strip()
            if u.startswith("http://") or u.startswith("https://"):
                out.append(u)
            elif u.startswith("/") and base:
                out.append(base + u)
        return out

    task_ids: list[str] = []
    download_urls: list[str] = []
    status_by_task: dict[str, str] = {}
    first_error: Optional[str] = None

    # 可灵：不并发，一镜一镜来，每镜轮询至完成（不限时）后再发下一镜
    inflight: list[str] = []

    style_prefix = (SCRIPT_DRAMA_STYLE_PREFIX or "").strip()
    for i, s in enumerate(shots):
        prompt = _shot_prompt(s, "script_drama")
        if style_prefix:
            prompt = f"{style_prefix} {prompt}"
        shot_duration = _kling_duration_for_shot(s)
        shot_ref_list = _shot_ref_urls(s)
        image_urls = _to_http_urls(shot_ref_list)
        use_omni = bool(image_urls)
        # O1 官方示例：文生也可直接走 omni-video（不传 image_list）。
        # 这样可规避部分环境下 text2video 对 kling-video-o1 返回 "model is not supported" 的问题。
        kling_model = os.getenv("KLING_MODEL", "kling-video-o1")
        use_omni_endpoint = use_omni or (kling_model == "kling-video-o1")
        if use_omni_endpoint:
            kling_prompt = f"<<<image_1>>>{prompt}"[:1700]
            tid, err = _kling_create_with_retry(
                create_omni_video_task,
                kling_prompt,
                image_urls,
                duration=str(shot_duration),
            )
        else:
            tid, err = _kling_create_with_retry(
                create_kling_t2v_task,
                prompt,
                duration=str(shot_duration),
            )
        if err and not first_error:
            first_error = err
        if not tid:
            logger.warning("可灵短剧第 %d 镜创建失败: %s", i + 1, err)
            continue
        task_ids.append(tid)
        if not wait_and_download:
            status_by_task[tid] = "Processing"
            continue

        inflight.append(tid)
        _drain_kling_inflight(inflight, status_by_task, download_urls, limit=1, use_omni=use_omni_endpoint)
        if i < len(shots) - 1:
            time.sleep(5)

    if wait_and_download and inflight:
        _drain_kling_inflight(inflight, status_by_task, download_urls, use_omni=use_omni_endpoint)

    err_msg = None
    if not download_urls and wait_and_download:
        err_msg = first_error or "部分或全部可灵任务未成功（请确认可灵资源包充足、提示词/参考图符合规范；若为并发上限可稍后重试）"
    return {
        "video_mode": "kling_omni" if use_omni_endpoint else "kling_t2v",
        "task_ids": task_ids,
        "download_urls": download_urls,
        "status_by_task": status_by_task,
        "error": err_msg,
    }


def _resolve_shot_character_url(
    shot: StoryboardItem,
    character_references_with_urls: list[dict],
) -> Optional[str]:
    """按镜解析该镜应使用的角色参考图 URL（单张，兼容旧逻辑）。"""
    urls = _resolve_shot_character_urls(shot, character_references_with_urls)
    return urls[0] if urls else None


def _resolve_shot_character_urls(
    shot: StoryboardItem,
    character_references_with_urls: list[dict],
) -> list[str]:
    """按镜解析该镜应使用的角色参考图 URL 列表。支持主角与配角同镜：本镜出镜多人时返回多张参考图（顺序与 character_names 一致）。"""
    if not character_references_with_urls:
        return []
    # 兼容 shot 为 Pydantic 模型或 dict（如一步成片时 result.storyboard 为 StoryboardItem，API 入参也为模型）
    def _get(obj, key: str, default=None):
        if hasattr(obj, "get") and callable(getattr(obj, "get")):
            return obj.get(key, default)
        return getattr(obj, key, default)
    # 优先用 character_names（多人同镜），否则用 character_name（单人）
    names: list[str] = []
    raw_names = _get(shot, "character_names")
    if raw_names and isinstance(raw_names, (list, tuple)):
        names = [str(n).strip() for n in raw_names if n and str(n).strip()]
    if not names:
        single = (_get(shot, "character_name") or "").strip()
        if single:
            names = [single]
    seen_urls: set[str] = set()
    result: list[str] = []
    for name in names:
        for ref in character_references_with_urls:
            if (ref.get("name") or "").strip() == name and ref.get("url"):
                u = ref["url"].strip()
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    result.append(u)
                    logger.debug("按镜角色参考: name=%s -> ref role=%s", name, ref.get("role"))
                break
    if result:
        return result
    # 无 name 或未匹配到：先主角后配角，保证有参考图时至少用上一张
    for ref in character_references_with_urls:
        if ref.get("role") == "主角" and ref.get("url"):
            u = ref["url"].strip()
            if u:
                logger.debug("按镜角色参考: 未匹配到 name，使用首个主角")
                return [u]
    for ref in character_references_with_urls:
        if ref.get("role") == "配角" and ref.get("url"):
            u = ref["url"].strip()
            if u:
                logger.debug("按镜角色参考: 未匹配到 name，使用首个配角")
                return [u]
    first = character_references_with_urls[0].get("url", "").strip()
    return [first] if first else []


def run_video_generation(
    storyboard: list[StoryboardItem],
    script_summary: str = "",
    mode: Optional[str] = None,
    prefer_i2v: bool = False,
    character_reference_image: Optional[str] = None,
    character_references_with_urls: Optional[list[dict]] = None,
    pipeline: Optional[str] = None,
    wait_and_download: bool = True,
    backend_public_url: Optional[str] = None,
) -> dict:
    """
    根据分镜生成视频（短剧用可灵文生视频）。
    返回 { "video_mode", "task_ids", "download_urls", "status_by_task", "error" }。
    """
    # 计算台词的实际时长
    dialogue_durations = _calculate_actual_dialogue_durations(storyboard)
    logger.info(f"台词实际时长计算完成: {dialogue_durations}")
    
    # 为每个分镜设置时长属性
    for i, shot in enumerate(storyboard):
        if i < len(dialogue_durations):
            setattr(shot, "duration_sec", dialogue_durations[i])
            logger.debug(f"第 {i+1} 镜台词时长: {dialogue_durations[i]} 秒")
    
    # 短剧视频只用可灵生成
    if pipeline == "script_drama" or pipeline is None:
        if has_kling():
            return _run_script_drama_kling(
                storyboard,
                character_reference_image=character_reference_image,
                character_references_with_urls=character_references_with_urls,
                backend_public_url=backend_public_url,
                wait_and_download=wait_and_download,
            )
        return {
            "video_mode": "kling_t2v",
            "task_ids": [],
            "download_urls": [],
            "status_by_task": {},
            "error": "短剧视频生成使用可灵，请配置 KLING_ACCESS_KEY",
        }

    return {
        "video_mode": None,
        "task_ids": [],
        "download_urls": [],
        "status_by_task": {},
        "error": "仅支持短剧流程(script_drama)",
    }
