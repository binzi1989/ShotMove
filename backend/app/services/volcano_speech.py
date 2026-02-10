"""火山引擎豆包语音大模型 TTS（HTTP 一次性合成）。与 iflytek_speech 同接口：返回 (本地 mp3 路径, error_msg)。
支持多情感音色：传入 emotion 时对支持情感的 voice_type 生效，增强代入感。"""
import base64
import os
import tempfile
import uuid
from typing import Optional, Tuple

import httpx

# 尝试导入 mutagen 来计算音频时长
try:
    from mutagen.mp3 import MP3  # 如果 mutagen 未安装，后续会捕获 ImportError 并降级到 ffmpeg

except ImportError:
    # 如果 mutagen 不可用，使用 ffmpeg 命令行工具
    import subprocess

logger = __import__("logging").getLogger(__name__)


def get_audio_duration(file_path: str) -> float:
    """
    计算音频文件的时长（秒）。
    优先使用 mutagen 库，若不可用则使用 ffmpeg 命令行工具。
    """
    try:
        # 优先使用 mutagen 库
        if 'MP3' in globals():
            audio = MP3(file_path)
            return audio.info.length
        else:
            # 使用 ffmpeg 命令行工具
            result = subprocess.run(
                ['ffmpeg', '-i', file_path, '-hide_banner', '-loglevel', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                return float(result.stdout.strip())
            else:
                # 如果 ffmpeg 失败，返回默认值
                return 0.0
    except Exception:
        # 任何异常都返回默认值
        return 0.0


VOLCANO_APP_ID = (os.getenv("VOLCANO_APP_ID") or os.getenv("VOLCANO_TTS_APP_ID") or "").strip()
VOLCANO_ACCESS_TOKEN = (os.getenv("VOLCANO_ACCESS_TOKEN") or os.getenv("VOLCANO_TTS_TOKEN") or "").strip()
VOLCANO_TTS_URL = (os.getenv("VOLCANO_TTS_URL") or "https://openspeech.bytedance.com/api/v1/tts").strip()

# 单次请求文本建议 <300 字符，接口限制 1024 字节
MAX_TEXT_BYTES = 900

# 火山大模型音色：voice_type, 中文名, 语种, 性别, 是否多情感, 支持的情感列表(emotion 传参)
# 多情感音色（文档 1257544）：传 enable_emotion=true + emotion=xxx
VOLCANO_VOICE_OPTIONS: list[tuple[str, str, str, str, bool, list[str]]] = [
    # 多情感
    ("zh_male_beijingxiaoye_emo_v2_mars_bigtts", "北京小爷（多情感）", "中文", "男", True, ["angry", "surprised", "fear", "excited", "coldness", "neutral"]),
    ("zh_female_roumeinvyou_emo_v2_mars_bigtts", "柔美女友（多情感）", "中文", "女", True, ["happy", "sad", "angry", "surprised", "fear", "hate", "excited", "coldness", "neutral"]),
    ("zh_male_yangguangqingnian_emo_v2_mars_bigtts", "阳光青年（多情感）", "中文", "男", True, ["happy", "sad", "angry", "fear", "excited", "coldness", "neutral"]),
    ("zh_female_meilinvyou_emo_v2_mars_bigtts", "魅力女友（多情感）", "中文", "女", True, ["sad", "fear", "neutral"]),
    ("zh_female_shuangkuaisisi_emo_v2_mars_bigtts", "爽快思思（多情感）", "中文", "女", True, ["happy", "sad", "angry", "surprised", "excited", "coldness", "neutral"]),
    # 通用 / 角色扮演（情景多，无情感参数）
    ("zh_female_cancan_mars_bigtts", "灿灿/Shiny", "中文", "女", False, []),
    ("zh_female_qingxinnvsheng_mars_bigtts", "清新女声", "中文", "女", False, []),
    ("zh_male_wennuanahu_moon_bigtts", "温暖阿虎/Alvin", "中文", "男", False, []),
    ("zh_male_shaonianzixin_moon_bigtts", "少年梓辛/Brayan", "中文", "男", False, []),
    ("zh_female_zhixingnvsheng_mars_bigtts", "知性女声", "中文", "女", False, []),
    ("zh_male_qingshuangnanda_mars_bigtts", "清爽男大", "中文", "男", False, []),
    ("zh_female_linjianvhai_moon_bigtts", "邻家女孩", "中文", "女", False, []),
    ("zh_male_yuanboxiaoshu_moon_bigtts", "渊博小叔", "中文", "男", False, []),
    ("zh_male_yangguangqingnian_moon_bigtts", "阳光青年", "中文", "男", False, []),
    ("zh_female_tianmeixiaoyuan_moon_bigtts", "甜美小源", "中文", "女", False, []),
    ("zh_female_qingchezizi_moon_bigtts", "清澈梓梓", "中文", "女", False, []),
    ("zh_male_jieshuoxiaoming_moon_bigtts", "解说小明", "中文", "男", False, []),
    ("zh_female_kailangjiejie_moon_bigtts", "开朗姐姐", "中文", "女", False, []),
    ("zh_male_linjiananhai_moon_bigtts", "邻家男孩", "中文", "男", False, []),
    ("zh_female_tianmeiyueyue_moon_bigtts", "甜美悦悦", "中文", "女", False, []),
    ("zh_female_gaolengyujie_moon_bigtts", "高冷御姐", "中文", "女", False, []),
    ("zh_male_aojiaobazong_moon_bigtts", "傲娇霸总", "中文", "男", False, []),
    ("zh_female_meilinvyou_moon_bigtts", "魅力女友", "中文", "女", False, []),
    ("zh_male_shenyeboke_moon_bigtts", "深夜播客", "中文", "男", False, []),
    ("zh_female_wenrouxiaoya_moon_bigtts", "温柔小雅", "中文", "女", False, []),
    ("zh_male_wenrouxiaoge_mars_bigtts", "温柔小哥", "中文", "男", False, []),
    ("zh_male_beijingxiaoye_moon_bigtts", "北京小爷", "中文", "男", False, []),
    ("zh_male_dongfanghaoran_moon_bigtts", "东方浩然", "中文", "男", False, []),
]

# 情绪关键词 -> 火山 emotion 传参（优先匹配，顺序靠前优先；尽量覆盖短剧常见台词，保证情感起伏）
# 邀请/热情/期待类用 excited 或 happy；拒绝/婉拒/克制用 coldness；问候/温和用 happy
EMOTION_KEYWORDS: list[tuple[list[str], str]] = [
    (["生气", "怒", "气死", "烦", "火大", "愤怒", "凭什么", "闭嘴", "滚", "放肆", "大胆"], "angry"),
    (["伤心", "悲伤", "难过", "哭", "泪", "心痛", "委屈", "抱歉", "对不起", "舍不得", "别走", "遗憾", "可惜", "唉", "罢了"], "sad"),
    (["害怕", "恐惧", "慌", "吓", "恐怖", "别过来", "不敢"], "fear"),
    (["讨厌", "厌恶", "恶心", "烦人"], "hate"),
    (["惊讶", "吃惊", "居然", "竟然", "吓一跳", "呀", "咦", "真的吗", "怎会", "如何可能"], "surprised"),
    # 激动/热情/邀请（优先 excited 再 happy，避免都变成平淡）
    (["公子公子", "小姐小姐", "快来", "快看", "想请", "同去", "赏雪", "美得很", "请公子", "同游", "别院", "可宿", "一起来", "走吧", "赶紧", "太好了", "真好呀", "好呀好呀", "求求", "拜托", "务必"], "excited"),
    (["开心", "高兴", "哈哈", "笑", "欢喜", "快乐", "多谢", "早啊", "来啦", "真好", "多谢公子", "极美", "诸位", "师弟师妹早", "温和", "微笑", "幸会", "有劳", "请"], "happy"),
    (["冷漠", "冷淡", "无所谓", "随便", "不必", "不能", "无法", "转告", "告诉", "算了", "不必了", "太远", "难返", "就不去了", "婉拒", "推辞", "恕难", "告辞", "免了", "无需"], "coldness"),
]


def _voice_id_to_voice_type(voice_id: Optional[str]) -> tuple[str, bool, list[str]]:
    """将业务 voice_id（火山 voice_type、中文名、或 MiniMax 风格如 female-yujie/male-qn-jingying）映射到火山 voice_type。"""
    s = (voice_id or "").strip()
    if not s:
        return VOLCANO_VOICE_OPTIONS[0][0], VOLCANO_VOICE_OPTIONS[0][4], VOLCANO_VOICE_OPTIONS[0][5]
    v = s.lower().replace(" ", "")
    for voice_type, name, _lang, _gender, multi_emo, emotions in VOLCANO_VOICE_OPTIONS:
        if voice_type.lower() == v or name.replace(" ", "").replace("/", "").replace("（", "(").replace("）", ")").lower().startswith(v[:8]):
            return voice_type, multi_emo, emotions
        if v in name.replace(" ", "").lower() or name.replace(" ", "").lower() in v:
            return voice_type, multi_emo, emotions
    # 业务侧常用 MiniMax 风格 ID（如 female-yujie, male-qn-jingying）：先判女再判男，避免 female 含 male
    # 优先选多情感音色，便于 TTS 带情绪、有代入感
    if "female" in v or "女" in v or "yujie" in v or "shaonv" in v or "tianmei" in v or "chengshu" in v:
        for voice_type, name, _lang, gender, multi_emo, emotions in VOLCANO_VOICE_OPTIONS:
            if gender == "女" and multi_emo:
                return voice_type, multi_emo, emotions
        for voice_type, name, _lang, gender, multi_emo, emotions in VOLCANO_VOICE_OPTIONS:
            if gender == "女":
                return voice_type, multi_emo, emotions
    if "male" in v or "男" in v:
        for voice_type, name, _lang, gender, multi_emo, emotions in VOLCANO_VOICE_OPTIONS:
            if gender == "男" and multi_emo:
                return voice_type, multi_emo, emotions
        for voice_type, name, _lang, gender, multi_emo, emotions in VOLCANO_VOICE_OPTIONS:
            if gender == "男":
                return voice_type, multi_emo, emotions
    return VOLCANO_VOICE_OPTIONS[0][0], VOLCANO_VOICE_OPTIONS[0][4], VOLCANO_VOICE_OPTIONS[0][5]


def infer_emotion_from_text(text: str) -> Optional[str]:
    """从台词/文案推断情绪，返回火山 emotion 传参；关键词优先，再结合标点做轻度推断，避免全是 neutral。"""
    if not text or not text.strip():
        return "neutral"
    t = text.strip()[:200]
    for keywords, emotion in EMOTION_KEYWORDS:
        for kw in keywords:
            if kw in t:
                return emotion
    # 无关键词时：感叹号多偏激动/兴奋，问号偏惊讶，省略号+短句偏冷淡/遗憾
    if "！" in t or "!" in t:
        return "excited"
    if "？" in t or "?" in t:
        return "surprised"
    if ("…" in t or "..." in t) and len(t) < 30:
        return "coldness"
    return "neutral"


def text_to_speech(
    text: str,
    voice_id: Optional[str] = None,
    speed: int = 50,
    volume: int = 50,
    pitch: int = 50,
    emotion: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[float]]:
    """
    火山豆包大模型 TTS，返回 (本地 mp3 路径, error_msg, 音频时长秒数)。
    当 voice_type 为多情感音色且传入 emotion 时，会设置 enable_emotion=true 以增强代入感。
    文本过长会分段合成并拼接为单文件。
    """
    if not text or not text.strip():
        return None, "文本为空", 0.0
    if not VOLCANO_APP_ID or not VOLCANO_ACCESS_TOKEN:
        return None, "VOLCANO_APP_ID / VOLCANO_ACCESS_TOKEN 未配置", 0.0

    text = text.strip()
    voice_type, supports_emotion, allowed_emotions = _voice_id_to_voice_type(voice_id)
    use_emotion = False
    emotion_val = (emotion or "").strip().lower()
    if supports_emotion and emotion_val:
        if emotion_val in allowed_emotions:
            use_emotion = True
        elif not allowed_emotions:
            use_emotion = emotion_val in ("happy", "sad", "angry", "surprised", "fear", "excited", "coldness", "neutral", "hate")
        else:
            # 音色不支持该情绪时落到最近支持项，避免完全不用情感
            if emotion_val in ("excited", "happy"):
                emotion_val = "happy" if "happy" in allowed_emotions else ("excited" if "excited" in allowed_emotions else "neutral")
            elif emotion_val in ("coldness", "angry"):
                emotion_val = "coldness" if "coldness" in allowed_emotions else ("angry" if "angry" in allowed_emotions else "neutral")
            elif emotion_val in ("sad", "fear", "surprised", "hate"):
                emotion_val = next((e for e in (emotion_val, "sad", "fear", "surprised", "neutral") if e in allowed_emotions), "neutral")
            else:
                emotion_val = "neutral" if "neutral" in allowed_emotions else (allowed_emotions[0] if allowed_emotions else "neutral")
            use_emotion = emotion_val in allowed_emotions

    chunks: list[str] = []
    remaining = text.encode("utf-8")
    while remaining:
        if len(remaining) <= MAX_TEXT_BYTES:
            chunks.append(remaining.decode("utf-8"))
            break
        chunk_bytes = remaining[:MAX_TEXT_BYTES]
        last = chunk_bytes.rfind(b" ")
        if last <= 0:
            last = len(chunk_bytes)
        chunk_bytes = remaining[:last]
        remaining = remaining[last:]
        chunks.append(chunk_bytes.decode("utf-8", errors="ignore"))

    audio_parts: list[bytes] = []
    for chunk_text in chunks:
        if not chunk_text.strip():
            continue
        payload = {
            "app": {
                "appid": VOLCANO_APP_ID,
                "token": VOLCANO_ACCESS_TOKEN,
                "cluster": "volcano_tts",
            },
            "user": {"uid": "uid1"},
            "audio": {
                "voice_type": voice_type,
                "encoding": "mp3",
                # speed 0–100：50 为正常(1.0)，<50 略慢，>50 略快；范围约 0.8–1.2
                "speed_ratio": (0.8 + (speed / 100) * 0.4) if 0 <= speed <= 100 else 1.0,
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "text": chunk_text[:1024],
                "operation": "query",
            },
        }
        if use_emotion:
            payload["audio"]["enable_emotion"] = True
            payload["audio"]["emotion"] = emotion_val or "neutral"
        headers = {"Authorization": f"Bearer;{VOLCANO_ACCESS_TOKEN}", "Content-Type": "application/json"}

        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.post(VOLCANO_TTS_URL, json=payload, headers=headers)
        except Exception as e:
            return None, str(e), 0.0

        data = r.json() if r.content else {}
        code = data.get("code", -1)
        if code != 3000:
            msg = data.get("message") or r.text or f"code={code}"
            return None, msg, 0.0
        b64 = data.get("data")
        if not b64:
            return None, "返回无音频数据", 0.0
        try:
            audio_parts.append(base64.b64decode(b64))
        except Exception as e:
            return None, f"base64 解码失败: {e}", 0.0

    if not audio_parts:
        return None, "无音频数据", 0.0

    out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    for part in audio_parts:
        out.write(part)
    out.close()
    
    # 计算音频时长
    duration = get_audio_duration(out.name)
    return out.name, None, duration


def has_volcano() -> bool:
    return bool(VOLCANO_APP_ID and VOLCANO_ACCESS_TOKEN)
