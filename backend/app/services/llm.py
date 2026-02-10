"""可选 LLM 调用：用于生成脚本/分镜。支持 Kimi（Moonshot），未配置时使用模板。"""
import math
import json
import os
import re
from typing import Any, Optional

import httpx

from app.services.scene_prompts import get_scene_guidance_for_refine

# Kimi（Moonshot）API：与官方文档一致，支持 KIMI_API_KEY 或 MOONSHOT_API_KEY，base_url 与官方示例一致
KIMI_API_KEY = (os.getenv("KIMI_API_KEY") or os.getenv("MOONSHOT_API_KEY") or "").strip()
KIMI_BASE_URL = (os.getenv("KIMI_BASE_URL") or "https://api.moonshot.ai/v1").rstrip("/")
# 模型与官方示例一致：kimi-k2-turbo-preview
KIMI_MODEL = os.getenv("KIMI_MODEL", "kimi-k2-turbo-preview")
# 短剧分镜建议用深度思考模型，输出导演级文生视频用 Prompt
KIMI_MODEL_STORYBOARD = os.getenv("KIMI_MODEL_STORYBOARD", "kimi-thinking-preview")

# 兼容：其他 LLM（可选）
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


def has_llm() -> bool:
    return bool(KIMI_API_KEY or OPENAI_API_KEY or DASHSCOPE_API_KEY)


def _estimate_dialogue_duration_sec(text: str) -> float:
    """
    估算台词朗读所需时长（秒），用于给分镜 duration_sec 做合理兜底/校正：
    - 中文按字数（默认约 4.5 字/秒）
    - 英文按词数（默认约 2.8 词/秒）
    - 标点增加少量停顿
    """
    if not text or not str(text).strip():
        return 0.0
    t = " ".join(str(text).split()).strip()
    if not t:
        return 0.0
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", t))
    en_words = len(re.findall(r"[A-Za-z0-9]+", t))
    punct = len(re.findall(r"[，,。.!！？?；;：:、】【「」“”\"'…—-]", t))
    zh_rate = float(os.getenv("DRAMA_SPEECH_ZH_CHARS_PER_SEC", "4.5") or 4.5)
    en_rate = float(os.getenv("DRAMA_SPEECH_EN_WORDS_PER_SEC", "2.8") or 2.8)
    pause = float(os.getenv("DRAMA_SPEECH_PUNCT_PAUSE_SEC", "0.10") or 0.10)
    base = (zh_chars / max(1e-3, zh_rate)) + (en_words / max(1e-3, en_rate))
    return max(0.8, base + punct * pause)


def _is_action_only_no_speech(text: str) -> bool:
    """粗判该段是否像「纯动作/神态」而非可朗读台词（避免把动作描述当对白拉长镜头）。"""
    if not text or not str(text).strip():
        return True
    t = str(text).strip()
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


def _strip_tts_speaker_prefix(text: str) -> str:
    """去掉「旁白：」「角色名：」等前缀，只保留 TTS 应朗读的正文。"""
    if not text or not str(text).strip():
        return ""
    t = str(text).strip()
    if re.match(r"^旁白\s*[：:]\s*", t):
        t = re.sub(r"^旁白\s*[：:]\s*", "", t).strip()
    # 仅剥掉非常短的「角色名：」，避免误伤正常句子里的冒号
    for sep in ("：", ":"):
        if sep in t:
            left, right = t.split(sep, 1)
            if 1 <= len(left.strip()) <= 6 and right.strip():
                t = right.strip()
            break
    return t


def _recommended_duration_sec_from_copy(copy_text: str) -> Optional[int]:
    """
    根据台词估算一个「不至于念不完」的时长下限（整数秒）。
    只做下限推荐：若分镜原本时长更长则不缩短。
    """
    tts_text = _strip_tts_speaker_prefix(copy_text or "")
    if not tts_text or _is_action_only_no_speech(tts_text):
        return None
    tail_pad = float(os.getenv("DRAMA_TTS_TAIL_PAD_SEC", "0.25") or 0.25)
    # 分镜生成阶段按短剧镜头规范：最短 2 秒
    min_shot_sec = max(2.0, float(os.getenv("DRAMA_MIN_SHOT_SEC", "1.0") or 1.0))
    base = _estimate_dialogue_duration_sec(tts_text)
    need = max(min_shot_sec, base + max(0.0, tail_pad))
    # duration_sec 字段为 int，向上取整避免「刚好念不完」
    return int(math.ceil(need))


def _kimi_chat(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
    model: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """调用 Kimi 聊天接口。返回 (content, error)：成功时 (content, None)，失败时 (None, 错误信息)。"""
    if not KIMI_API_KEY:
        return (None, "未配置 KIMI_API_KEY 或 MOONSHOT_API_KEY，请在 .env 中填写并在平台控制台申请 Key（中国站 platform.moonshot.cn / 国际站 platform.moonshot.ai）")
    url = f"{KIMI_BASE_URL}/chat/completions"
    payload = {
        "model": model or KIMI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.6,
    }
    headers = {
        "Authorization": f"Bearer {KIMI_API_KEY}",
        "Content-Type": "application/json",
    }
    timeout = 120.0 if (model or KIMI_MODEL) == "kimi-thinking-preview" else 60.0
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message") or {}
            return ((msg.get("content") or "").strip(), None)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        err = f"HTTP {code}：{e.response.url}"
        if code == 401:
            err += "。未授权：Key 与接口必须同站——在中国站 platform.moonshot.cn 申请的 Key 只能配 KIMI_BASE_URL=https://api.moonshot.cn/v1；在国际站 platform.moonshot.ai 申请的 Key 配 KIMI_BASE_URL=https://api.moonshot.ai/v1。请核对 .env 中 KIMI_BASE_URL 与 Key 申请站点一致。"
        elif code == 404:
            err += "。接口不存在：请设 KIMI_BASE_URL=https://api.moonshot.ai/v1"
        elif code == 429:
            err += "。请求过多/限流：可改用国际站，在 .env 中设 KIMI_BASE_URL=https://api.moonshot.ai/v1（勿用 .cn）"
        return (None, err)
    except Exception as e:
        return (None, str(e))


def generate_storyboard_from_script_drama_llm(script_text: str) -> tuple[Optional[list[dict]], Optional[str]]:
    """用 Kimi 深度思考模型根据剧本生成分镜。返回 (分镜列表, 错误信息)，失败时 (None, 错误)。"""
    system = """你是短剧分镜师兼导演。根据剧本/小说内容，**严格按原文情节与对白**逐镜输出分镜表。每行一条，格式为（共11列，用英文竖线|分隔）：
序号|景别|画面描述|对白/旁白|时长(秒)|镜头安排|拍摄方式|镜头手法|文生视频用Prompt|生成方式|本镜出镜角色名

【剧本与分镜规划】（优先：合理、紧凑）
- **规划合理**：先把握整段戏的起承转合，再落镜；每镜承担明确功能（建立/推进/转折/收束），不堆砌无效镜头。时间线、地点、人物动机前后一致，不自相矛盾。
- **节奏紧凑**：删繁就简，能一句话说清的不拆成三镜；无信息量的描写并入有戏的镜内或省略。观众感受是「一环扣一环」，不是「拖沓或碎碎念」。

【剧情紧凑度】（优先遵守，避免节奏松散）
- **信息密度**：每一镜必须推进剧情或情绪，禁止「纯过渡」「重复建立」类空镜。能在一镜内完成的动作与对白不要拆成多镜；**宁少勿碎**，观众感觉「一镜有一镜的戏」。
- **对白合并**：同一场景、同一情绪下的连续 2–4 句对白**尽量合并为一镜**（对白列写全，用空格或顿号衔接）；只在**情绪转折、换人说话、关键金句、冲突爆发**时切镜。避免「一句对白一镜」导致节奏拖沓。
- **总镜数控制**：1 分钟内容约 **6–10 镜**，2 分钟约 **12–18 镜**，3 分钟约 **18–24 镜**；总镜数宁可偏少、不要碎片化。无信息量的铺垫、重复解释压缩或并入有戏的镜内。
- **连续动作一镜完成**：如「起床→睁眼→坐起」「摔倒→倒地」「推门→进屋→环顾」等同一条动作线、无台词或仅一句语气词（如「砰」「痛」）的，**用一镜描述完整动作**，不要拆成多镜。

【镜头规划合理性】（必须遵守）
- **镜头数量与节奏**：按**情节单元**切镜，不按单句切镜。同一场景内连续对话 2–3 句合并为一镜（对白列可合并为一段）；重要情绪转折或关键台词可单独一镜。总镜数参考上条「总镜数控制」，避免无意义的碎片化。
- **景别与视觉节奏**：遵循「建立空间→叙事→情绪」。开场或换场用**全景/中景**建立环境与人物关系；日常对话多用**中景/近景**；情绪高潮、关键表情用**特写**。避免连续多镜同一景别，也避免景别乱跳；相邻镜尽量有景别或机位变化。
- **时长分配**：**每镜 2~4 秒最佳，最长不超过 5 秒**；除非本镜对白较长、正常语速念不完时可略放长，原则是**台词念完镜头才过**。不要单镜拖到 6–10 秒除非该镜台词确实很长。
- **镜头手法**（第6–7列）：与情绪匹配。平静多用固定；紧张/推进用缓慢推镜；收尾或转场可用拉镜。同一场景内风格、光线、色调一致。

【镜头连续性与机位衔接】（重点避免“拼接感”）
- 同一时间、地点、人物的连续镜头要被视为一个连续场次：画面背景、光线、服装、天气等在相关镜头中保持一致，**不要一镜在门口下一镜突然跳到完全不同的背景**。
- 机位设计遵循「建立空间→叙事→情绪」的渐进：先用全景/大中景建立环境和人物关系，再切中景/近景表现对话或动作，最后用近景/特写收情绪；相邻镜头的景别变化要平滑，不要频繁从特写跳回远景再跳回特写。
- 对话场景中，相邻镜头应沿同一条机位轴线设计：例如第一镜正面中景，第二镜在此基础上轻微推近，第三镜对切到对方的中近景，人物的站位和朝向前后镜一致，避免左右颠倒、空间错乱。
- 描述连续镜头时，可在画面描述或镜头手法中明确写出「沿上一镜微推」「从上一镜机位轻摇带出某人入画」「再切回某人更紧的特写」等，帮助后续生成的画面在时间和空间上自然连起来，而不是每一镜都重开一个完全独立的构图。

【镜头表现与台词安排】（紧凑、有代入感、表述具体）
- **画面与台词一一对应、表述具体**：每一镜的「画面描述」和「对白/旁白」要让用户一眼看懂**这镜在演什么、谁在说什么**。画面描述写清具体动作与状态（如「苏平蜷睡蒙头、只露乱发」「猛地睁眼、瞳孔收缩」），不要空泛的「人物在场景中」「角色反应」；对白列只写本镜实际会念出的台词或旁白，**紧凑、口语化**，关键情绪用短句或语气词强化，避免长段总结或播音腔。
- **紧凑有代入感**：同一镜内对白以 2–5 秒能念完为佳；多镜之间节奏清晰——该紧张的镜台词短促，该抒情的镜可稍长但仍有重点句。**能合并到一镜的对白不要拆镜**；旁白与对话区分清楚。每镜对白列须有内容（对白/旁白/心里独白），不留空。
- **用户看分镜即知具体表述**：读者只看分镜表就能理解「镜头1：谁在做什么、说什么；镜头2：…」，无需猜。第3列画面描述与第4列对白/旁白互相印证，不矛盾、不重复啰嗦。

【镜头表现：内容优先、运镜为辅】（避免平淡无趣、让每镜「有戏」）
- **第9列文生视频用Prompt 重点写「镜头表现出来的内容」**：观众要有代入感、觉得有戏可看，靠的是**画面里正在发生什么、情绪与氛围**，不是运镜术语。每镜要写出**正在发生的事、情绪、氛围、戏剧感**——例如紧张时写「光线压暗、眼神锐利、呼吸紧绷」；温情时写「暖光、柔和表情、氛围宁静」；冲突时写「对峙感、肢体张力、光影对比」。**景别与运镜放在最后、一笔带过即可**（如「中景固定」「近景固定」），不喧宾夺主。
- **避免无趣的笼统描述**：禁止多镜都写成「人物在场景中」「角色站立」「自然光电影感」等缺乏辨识度的套话；每镜在一致性的前提下要有**具体的光影、神态、氛围词**（如「晨雾朦胧」「逆光剪影」「眉头紧蹙」「嘴角微动」），让观众有代入感、觉得「有戏可看」。

【每镜必有配音、禁止空镜】（必须遵守）
- **每一镜都要有配音输出**，牵着观众走：至少有**对白、旁白或心里独白**之一；**禁止空信息镜头**（无台词、无旁白、无内心独白、无推进信息的镜头不要）。
- 若某镜画面无角色开口，必须补**心里独白**或简短**旁白/环境音描述**，保证该镜有可念出的内容用于配音；不能出现「画面描述有、对白列为空」且无任何可配音文案的镜头。
- 无台词镜头要非常克制；若必须保留建立镜/转场镜，也要在该镜对白列写一句内心独白或旁白（如「他心想……」「远处传来……」），不交白卷。

要求：
- **第5列 时长(秒)**：**2、3、4、5 秒为主**（2~4 秒最佳）；仅当本镜对白较长、念完需更长时间时可填 6、7、8，原则是台词完镜头过。**最短 2 秒**。
- **第11列 本镜出镜角色名**（必守）：本镜头**画面中出现的所有人物**名字，与剧本中角色名一致。**凡画面中同时出现两人或多人**（如两人对话、对峙、同框、多人群戏），第11列**必须写全所有人名，用英文逗号分隔**，例如「李华,小明」「张姐,店员,顾客」；仅一人出镜填一个名字；无人物写「旁白」或留空。用于按镜拉取每位角色的参考图（多人同镜会传多张参考图，缺写则只传一人）。示例：镜头为「李华与小明面对面争执」→ 第11列填「李华,小明」；镜头为「苏平独自醒来」→ 填「苏平」。
- **第4列 对白/旁白**：只写**角色说出口的台词或旁白**，**禁止写动作/神态描述**。例如禁止写「微微点头」「点头」「微笑」「沉默」「不语」「示意」等（这些应只出现在画面描述中）；无台词或仅动作时留空或「—」。尽量保留剧本原文语气，紧凑口语化，关键情绪用短句。
- **画面描述**、**对白/旁白**、**文生视频用Prompt** 必须来自剧本原文或据此提炼，禁止填表头占位符（如「画面描述」「序号」「------」）。
- **第9列 文生视频用Prompt** 必须采用「主体+场景+动作+风格+镜头语言」结构，**每镜一个主主体、一个主要动作**，描述具体可被 T2V 模型稳定执行；禁止敏感词、自相矛盾、极短模板。**严禁任何文字/文字动画**：禁止画面中出现或弹出任何可读文字、数字、价格、对白字、字幕、标题、logo；禁止「弹出“xxx”」「手势配文字」「比心配价格」等描述；只写人物动作与场景，不写任何会在画面上显示的文字，所有文字由后期叠加。
  - **主体**：谁/什么（人物、角色、物体），一镜一主；
  - **场景与景观一致性**：**规划场景**——同一场戏内建筑、陈设、天气、光线、色调在多镜之间保持一致（如「同上镜的客厅」「同一街道、雨夜」）；换场再切换场景描述。
  - **角色名与场景区分**：若角色名与场景/自然物同名（如角色名「绿竹」与竹林、竹景），第9列 Prompt 中**不要用该角色名描述场景**；用「少女/女子/人物」等指代角色，场景单独写「竹林/雪竹/庭院」等，避免角色名融入背景、观众误以为角色是背景一部分。
  - **在一致性前提下增加丰富性**：每镜在保证与同场戏景观一致的基础上，**尽可能写出画面丰富性**——如具体陈设、道具、光影层次、景深、氛围（雾气/尘埃/逆光）、质感与层次感，使每镜有辨识度又不跳戏；避免多镜都写成「人物+场景」的笼统描述。
  - **代入感与镜头感染力**：每镜要有**情绪与氛围**，不能平淡无趣。**重点写画面正在发生什么、观众能感受到什么**；景别与运镜与情绪匹配即可、简写放在末尾（如「近景固定」「中景固定」），运镜不是重点。根据剧情写出与当下情绪匹配的光影、神态、氛围（如紧张：光线压暗、眼神锐利；温情：暖光、柔和表情；冲突：对峙感、肢体张力）。避免「人物在场景中」「自然光电影感」等无辨识度套话，要有具体可感的神态、光影、氛围词。
  - **有对白的镜头必须让人物嘴动起来**：若第4列对白/旁白非空，第9列 Prompt **必须**在动作或画面中明确写出人物正在说话、嘴部在动，例如「嘴唇随说话自然开合」「口型持续张合、明显在说话」「嘴部随对白张合、下颌微动」，以便文生视频模型生成会动嘴的人物，而不是静态闭嘴。
  - **动作**：一个主要动作（2–5 秒内可完成），用肢体/表情而非抽象心理；有台词时动作须包含「嘴/口型在动」。无对白镜头也要有轻微动作或神态变化（如呼吸、微转头、衣角/发丝微动），避免完全静态站桩。
  - **风格**：所有镜头的风格、光线、色调保持一致，与剧本时代/场景统一；
  - **镜头语言**：景别+运镜（如中景固定、缓慢推镜），与当前镜情绪匹配。
  示例（无对白）：「拾荒车（主体），凌晨荒原、天边鱼肚白、沙丘轮廓、远处公路延伸（场景），车身沿公路飞驰、卷起烟尘、车灯微亮（动作），冷色调黎明、略带雾霭、层次分明（风格），大景固定镜头、地平线稳定（镜头语言）。」示例（有对白）：「孟川近景（主体），雪后道院石阶（场景），正在说话、嘴唇随对白自然开合、下颌微动、目光温和（动作），冷灰晨光、电影感（风格），近景固定（镜头语言）。」禁止「1，大景。固定。」等模板。
- **生成方式**（第10列）：文生视频、图生视频、首尾帧 三选一。文生视频=大场景/环境；图生视频=特写/近景/人物表情/精细构图；首尾帧=与上一镜连续动作或明显过渡。
- 只输出真实镜头行，不要输出表头行或分隔行。"""
    user = f"""请为以下剧本/小说逐镜生成分镜表。每行11列。
要求：
- **剧本规划合理、节奏紧凑**：先整体把握再落镜；每镜有明确功能，不堆砌、不拖沓；对白能合并就合并为一镜（2–4 句/镜），总镜数宁少勿碎（约 1 分钟 6–10 镜）。
- 第3列画面描述、第4列对白/旁白具体、紧凑、有代入感。**第9列文生视频用Prompt：内容优先、运镜为辅**——重点写**画面正在发生什么、情绪与氛围**（光影、神态、戏剧感），让观众有代入感；景别与运镜放在最后、简写即可（如「中景固定」），不喧宾夺主；在保证场景一致的前提下写具体、有丰富性，避免平淡无趣。
- **第11列本镜出镜角色名**：画面中有几人就填几人，两人及以上用英文逗号分隔（如 李华,小明），不要只填一个名字导致主配角同镜时缺参考图。

剧本：
{script_text[:3000]}"""
    out, last_error = _kimi_chat(
        system,
        user,
        max_tokens=4096,
        model=KIMI_MODEL_STORYBOARD,
    )
    if not out and KIMI_MODEL_STORYBOARD != KIMI_MODEL:
        out, last_error = _kimi_chat(system, user, max_tokens=2048, model=KIMI_MODEL)
    if not out:
        return (None, last_error or "Kimi 未返回有效分镜")
    result = []
    for line in out.strip().split("\n"):
        line = line.strip()
        if not line or "|" not in line:
            continue
        # 表头或说明行：首列非数字则跳过
        first = line.split("|", 1)[0].strip()
        if first and not first.isdigit() and "序号" not in first and "景别" not in first:
            continue
        if first in ("序号", "景别", "---", "—", "－"):
            continue
        # 从左侧拆出最多 11 段（第9列文生Prompt内可能含|），兼容 10 列旧格式
        parts = [p.strip() for p in line.split("|", 10)]
        if len(parts) < 5:
            continue
        try:
            idx = int(parts[0]) if parts[0].isdigit() else len(result) + 1
        except ValueError:
            idx = len(result) + 1
        shot_type = (parts[1] or "中景")[:80]
        shot_desc = (parts[2] or "").strip()
        copy = (parts[3] if len(parts) > 3 else "").strip()
        raw_dur = int(parts[4]) if len(parts) > 4 and str(parts[4]).isdigit() else 4
        duration = max(2, min(10, raw_dur))
        # 校正：若台词按正常语速 2~4 秒念不完，则自动拉长该镜头（避免出现「4秒镜头塞不下台词」）
        try:
            need_min = _recommended_duration_sec_from_copy(copy)
            if need_min is not None:
                duration = max(duration, min(10, need_min))
        except Exception:
            pass
        shot_arrangement = parts[5] if len(parts) > 5 else ""
        shooting_approach = parts[6] if len(parts) > 6 else ""
        camera_technique = parts[7] if len(parts) > 7 else ""
        t2v_prompt = (parts[8] if len(parts) > 8 else "").strip()
        gen_raw = (parts[9] if len(parts) > 9 else "").strip()
        character_name_raw = (parts[10] if len(parts) > 10 else "").strip()
        # 过滤表头/占位行：景别列为「序号」「景别」「------」或画面描述为占位符
        if shot_type in ("序号", "景别", "------", "—", "－") or (shot_type and all(c in " -－—\t" for c in shot_type)):
            continue
        if shot_desc in ("画面描述", "对白/旁白", "------------", "—", "－") or len(shot_desc) < 3:
            continue
        if shot_desc and all(c in " -－—\t" for c in shot_desc):
            continue
        # 若 t2v_prompt 是表头式模板（含「序号」「景别。拍摄方式」等）则视为无效
        if t2v_prompt and (
            ("序号" in t2v_prompt and "景别" in t2v_prompt)
            or t2v_prompt.replace(" ", "").replace("，", ",") == "序号,景别。拍摄方式。"
            or t2v_prompt.strip().startswith("------")
        ):
            t2v_prompt = ""
        if "图生" in gen_raw or "i2v" in gen_raw.lower():
            generation_method = "i2v"
        elif "首尾" in gen_raw or "fl2v" in gen_raw.lower():
            generation_method = "fl2v"
        else:
            generation_method = "t2v"
        if not t2v_prompt or len(t2v_prompt) < 30:
            # 五段式：主体+场景+动作+风格+镜头语言（带氛围，避免过于平淡）
            camera = camera_technique.strip() or "固定镜头"
            t2v_prompt = f"人物（主体），{shot_desc}（场景与动作），自然光电影感、画面有层次与氛围感（风格），{shot_type}、{camera}（镜头语言）。"
        # 本镜出镜角色名：第11列支持多人用英文逗号分隔（如 李华,小明），解析为 character_names；character_name 取第一个以兼容旧逻辑
        character_names = []
        if character_name_raw and character_name_raw not in ("旁白", "无", "-", "—"):
            character_names = [n.strip()[:50] for n in character_name_raw.split(",") if n and n.strip()]
        if not character_names and copy:
            m = re.match(r"^([A-Za-z\u4e00-\u9fa5]{1,6})\s*[：:]\s*", copy)
            if m:
                character_names = [m.group(1).strip()]
        character_name = character_names[0] if character_names else None
        result.append({
            "index": idx,
            "shot_type": shot_type,
            "shot_desc": shot_desc[:300],
            "copy": copy[:200],
            "duration_sec": duration,
            "shot_arrangement": shot_arrangement[:200],
            "shooting_approach": shooting_approach[:200],
            "camera_technique": camera_technique[:200],
            "t2v_prompt": t2v_prompt[:1600],
            "generation_method": generation_method,
            "character_name": character_name or None,
            "character_names": character_names if character_names else None,
        })
    # 过滤后重排序号为 1, 2, 3, ...
    for i, r in enumerate(result, 1):
        r["index"] = i
    return (result if result else None, None)


def generate_storyboard_from_script_drama_template(script_text: str) -> list[dict]:
    """无 LLM 时从剧本按意群拆镜，从内容提炼画面描述与文生视频用 Prompt（景别+场景+角色+光线+运镜）。"""
    import re
    # 按句号、问号、感叹号、换行拆成意群，避免一整段只出一镜
    raw = script_text.replace("\r\n", "\n").replace("\r", "\n")
    chunks = re.split(r"[。！？\n]+", raw)
    chunks = [c.strip() for c in chunks if len(c.strip()) > 2][:12]
    if not chunks:
        chunks = [script_text[:200]]
    result = []
    for i, block in enumerate(chunks, 1):
        copy = block[:200]
        block_lower = block
        character_name_from_block = None
        # 从内容推断景别与画面
        if "凌晨" in block or "天边" in block or "荒原" in block or "地平线" in block or "月亮" in block:
            shot_type = "全景"
            shot_desc = block[:80] if len(block) <= 80 else (block[:77] + "...")
            light = "晨光熹微"
        elif "驾驶室" in block or "系统提示" in block or "提示音" in block:
            shot_type = "中景"
            shot_desc = "驾驶室内系统提示音或界面" if "提示" in block or "提示音" in block else block[:60]
            light = "车内冷光"
        elif "香烟" in block or "点燃" in block:
            shot_type = "近景"
            shot_desc = "角色点燃香烟吸一口" + ("，塞西" if "塞西" in block else "")
            light = "车内冷光"
        elif "转身" in block or "离开" in block or "走向" in block or "拖车室" in block:
            shot_type = "中景"
            shot_desc = "角色离开驾驶室走向拖车室" + ("，塞西" if "塞西" in block else "")
            light = "车内光"
        elif "一震" in block or "撞" in block or "拖车壁" in block:
            shot_type = "中景"
            shot_desc = "车身震动，角色撞在拖车壁上" + ("，穆林" if "穆林" in block else "")
            light = "昏暗拖车室内"
        elif "揉" in block or "眼冒金星" in block or "缓过神" in block:
            shot_type = "近景"
            shot_desc = "角色揉脑袋缓过神" + ("，穆林" if "穆林" in block else "")
            light = "昏暗拖车室内"
        elif "：" in block or ":" in block:
            role, _, rest = block.partition("：" if "：" in block else ":")
            shot_type = "中景"
            shot_desc = f"{role.strip()} 对白：{rest[:40]}".strip()
            light = "室内光"
            character_name_from_block = role.strip()[:50] if role.strip() else None
        else:
            shot_type = "中景"
            shot_desc = block[:60] if len(block) <= 60 else (block[:57] + "...")
            light = "自然光"
            character_name_from_block = None
        # 本镜生成方式：根据内容选文生/图生/首尾帧
        if "香烟" in block or "点燃" in block or "揉" in block or "眼冒金星" in block or "缓过神" in block or shot_type == "近景":
            generation_method = "i2v"
        elif "转身" in block or "离开" in block or "走向" in block or "拖车室" in block or "一震" in block or "撞" in block or "拖车壁" in block:
            generation_method = "fl2v"
        else:
            generation_method = "t2v"
        # 五段式：主体+场景+动作+风格+镜头语言（详细）
        t2v_prompt = f"角色（主体），{shot_desc}（场景与动作），{light}、画面有层次（风格），{shot_type}、固定镜头（镜头语言）。"
        character_name = character_name_from_block
        if not character_name and copy:
            m = re.match(r"^([A-Za-z\u4e00-\u9fa5]{1,6})\s*[：:]\s*", copy)
            if m:
                character_name = m.group(1).strip()
        # 模板分镜：按台词估算时长，避免固定 4 秒导致对白念不完
        duration_sec = 4
        try:
            need_min = _recommended_duration_sec_from_copy(copy)
            if need_min is not None:
                duration_sec = max(duration_sec, min(10, need_min))
        except Exception:
            duration_sec = 4
        if not (copy or "").strip():
            copy = "（内心独白）" + (shot_desc[:30] or " ")
        result.append({
            "index": i,
            "shot_type": shot_type,
            "shot_desc": shot_desc,
            "copy": (copy or "").strip()[:200],
            "duration_sec": duration_sec,
            "shot_arrangement": "",
            "shooting_approach": "",
            "camera_technique": "切",
            "t2v_prompt": t2v_prompt[:500],
            "generation_method": generation_method,
            "character_name": character_name or None,
        })
    if not result:
        result = [{"index": 1, "shot_type": "全景", "shot_desc": "场景", "copy": script_text[:100] or "（旁白）", "duration_sec": 4, "shot_arrangement": "", "shooting_approach": "", "camera_technique": "切", "t2v_prompt": "人物（主体），场景与环境（场景与动作），自然光、画面有层次（风格），全景、固定镜头（镜头语言）。", "generation_method": "t2v", "character_name": None}]
    return result


# 文生视频 Prompt 精修：对齐 video-prompt-quality-skill，并加入「提高生成成功率」硬性规则
# 目标：让整部片子“像一个整体”——先产出全片连续性 bible，再逐镜落地
REFINE_T2V_SYSTEM_DRAMA = """你是短剧文生视频/图生视频的导演级 Prompt 总监。你要把整段短剧的分镜提示词改成“有整体感、连续性强”的一组镜头提示词，而不是互相割裂的单镜描述。

你必须先做全局规划，再逐镜改写：
1) 先通读整段剧本摘要与完整分镜列表（shot_desc + copy + 原 t2v_prompt）。
2) 输出一份「全片连续性 bible」（style_bible），用于统一全片的时空、场景、光线色调、人物外观、道具与镜头语法。
3) 再按 bible 为每一镜输出 refined_t2v_prompt：每镜都要把关键一致性点**直接写出来**（同场景/同光线/同服装/同道具等），同时保持每镜一个主动作与强代入感。

【重要：单镜自洽（避免视频模型丢上下文）】
- 每条 refined_t2v_prompt 必须“单镜自洽”，能独立喂给视频模型。
- 严禁使用跨镜指代与依赖上下文的说法：禁止出现「同一」「上一镜」「上一个镜头」「沿上一镜」「延续上镜」「继续上镜」等词；需要把场景与人物的连续性细节直接写出来（例如“狭窄冷灰金属驾驶舱、仪表微光、雨夜反光”），而不是写“同一驾驶舱”。

【输出格式（严格遵守）】
只输出一个 JSON 对象：
{
  "style_bible": {
    "time_of_day": "...",
    "weather": "...",
    "main_location": "...",
    "color_palette": "...",
    "lighting_rules": "...",
    "camera_grammar": "...",
    "character_look_rules": "...",
    "prop_continuity": "...",
    "do_not_do": "..."
  },
  "shots": [
    {"index": 1, "refined_t2v_prompt": "..."},
    ...
  ]
}
不要 markdown 包裹，不要任何解释文字。

【核心原则：内容优先、运镜为辅】
镜头表现出来的内容（正在发生什么、情绪与氛围）才是重点；景别与运镜放在最后、简写即可（如“中景固定”“近景固定”），不喧宾夺主。

【结构规范（每条 refined_t2v_prompt 必须遵守）】
- 主体 + 场景 + 动作 + 风格 + 镜头语言。动作与场景占主要篇幅；镜头语言一笔带过。
- 同一场戏多镜之间：建筑/陈设/天气/光线/色调一致；人物服装、发型、主要道具一致；避免“上一镜雪庭院，下一镜突然室内暖光”。
- 每镜一个主主体、一个主要动作（2–6 秒内能完成），避免复杂多线叙事。
- 有对白/旁白的镜头：必须写“正在说话、嘴唇/口型随说话自然张合、下颌微动”等（但**严禁引用具体台词文字**）。

【提高成功率的硬性规则】
- 画面中严禁任何文字与文字动画：禁止出现或弹出任何可读文字、数字、价格、字幕、标题、logo；所有文字由后期叠加。
- 禁止：表头占位、极短敷衍、敏感词、自相矛盾描述。
- 角色名与场景区分：若角色名与自然物同名（如“绿竹”与竹林），不要用角色名描述场景；用“少女/女子/人物”指代角色，场景单独写“竹林/雪竹/庭院”等。

【整体感加分项（强烈要求做到）】
- 让每镜的场景描述带“连续细节锚点”：例如“庭院石阶/檐下竹影/积雪纹理”“屋内灯影与陈设”——直接写锚点，不要用“同一”指代。
- 让机位衔接自然：可以写“机位轻微推近/轻微摇移/对切”，但不要写“沿上一镜…”。但不要写太多术语。
"""


def refine_storyboard_t2v_prompts_llm(
    storyboard: list[dict[str, Any]],
    pipeline: str,
    script_snippet: str = "",
) -> Optional[list[dict[str, Any]]]:
    """
    用 Kimi 对分镜表中每条 t2v_prompt 做导演级精修，对齐 video-prompt-quality-skill 规范。
    成功时返回带精修后 t2v_prompt 的分镜列表（深拷贝并替换 t2v_prompt）；失败返回 None，调用方保留原分镜。
    """
    if not (KIMI_API_KEY and storyboard):
        return None
    system = REFINE_T2V_SYSTEM_DRAMA
    def _sanitize_cross_shot_refs(text: str) -> str:
        """尽量去掉跨镜头指代词，让单镜 prompt 更自洽（不依赖上下文）。"""
        if not text:
            return text
        t = text
        for bad in (
            "同一",
            "上一镜",
            "上一个镜头",
            "沿上一镜",
            "沿上一个镜头",
            "延续上镜",
            "继续上镜",
            "承接上一镜",
        ):
            t = t.replace(bad, "")
        t = re.sub(r"\s{2,}", " ", t).strip()
        t = re.sub(r"[，,]\s*[，,]+", "，", t)
        t = re.sub(r"^[，,]\s*", "", t)
        return t

    def _style_bible_prefix(style_bible: Optional[dict[str, Any]]) -> str:
        if not isinstance(style_bible, dict) or not style_bible:
            return ""
        parts: list[str] = []
        for k in ("color_palette", "lighting_rules", "character_look_rules", "prop_continuity"):
            v = style_bible.get(k)
            if isinstance(v, str) and v.strip():
                parts.append(f"{k}：{v.strip()}")
        return ("全局一致要素：" + "；".join(parts) + "。") if parts else ""

    def _parse_llm_json(out: str) -> Any:
        if not out:
            return None
        s = out.strip()
        if "```" in s:
            for sep in ("```json", "```"):
                if sep in s:
                    i = s.find(sep) + len(sep)
                    j = s.find("```", i)
                    if j > i:
                        s = s[i:j].strip()
                        break
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None

    def _normalize_refined(refined_obj: Any) -> tuple[Optional[dict[str, Any]], Optional[list[dict[str, Any]]]]:
        """兼容：数组 / 对象（含 style_bible + shots）。"""
        style_bible = None
        refined_list = None
        if isinstance(refined_obj, list):
            refined_list = refined_obj
        elif isinstance(refined_obj, dict):
            sb = refined_obj.get("style_bible")
            if isinstance(sb, dict):
                style_bible = sb
            shots = refined_obj.get("shots")
            if isinstance(shots, list):
                refined_list = shots
        return style_bible, refined_list

    def _shot_for_llm(s: dict[str, Any], fallback_index: int) -> dict[str, Any]:
        idx = s.get("index", fallback_index)
        shot_desc = (s.get("shot_desc") or "").strip()
        copy = (s.get("copy") or s.get("copy_text") or "").strip()
        t2v = (s.get("t2v_prompt") or "").strip()
        return {
            "index": idx,
            "shot_desc": shot_desc[:260],
            "copy": copy[:160],
            "t2v_prompt": t2v[:700],
        }

    all_shots_for_llm = [_shot_for_llm(s, i + 1) for i, s in enumerate(storyboard)]
    batch_size = 10
    batches = [all_shots_for_llm[i:i + batch_size] for i in range(0, len(all_shots_for_llm), batch_size)]

    style_bible_final: Optional[dict[str, Any]] = None
    refined_items_all: list[dict[str, Any]] = []

    # 根据分镜内容注入影视场景类型指引（战斗/竞速/情感等），见 scene-type-prompts-skill
    scene_guidance = get_scene_guidance_for_refine(storyboard)

    for bi, batch in enumerate(batches):
        user = (
            "请精修以下分镜的文生视频用 Prompt，**严格按系统约定输出一个 JSON 对象**："
            '{"style_bible": {...}, "shots": [{"index": 1, "refined_t2v_prompt": "..."}]}。'
            "要求：每条单主体、单主动作、无敏感词、无自相矛盾，便于可灵/MiniMax 稳定生成。"
            "**务必删除 prompt 中任何「画面出现/弹出文字、数字、字幕、标题、logo」等描述，只保留纯视觉动作与场景。"
            "精修后的 prompt 中不要包含该镜的台词/对白文字，台词仅用于字幕与配音。**"
            "有对白的镜头必须在动作中明确写出「嘴唇/口型随说话张合」或「嘴部明显在动」等。"
            "另外：每条 prompt 必须单镜自洽，严禁出现「同一/上一镜/延续上镜/继续」等跨镜指代词；把连续性细节直接写出来。"
            "\n\n分镜列表（JSON）：\n"
            + json.dumps(batch, ensure_ascii=False, indent=2)
        )
        if bi == 0 and script_snippet and script_snippet.strip():
            user += f"\n\n剧本摘要（供上下文）：\n{script_snippet.strip()[:1200]}"
        if bi == 0 and scene_guidance and scene_guidance.strip():
            user += f"\n\n【本片涉及的影视场景类型与提示词指引（精修时请参考）】\n{scene_guidance.strip()}"
        if style_bible_final:
            user += "\n\n已确定的全片连续性 bible（请保持一致，不要自相矛盾，必要时可微调措辞但不改设定）：\n"
            user += json.dumps(style_bible_final, ensure_ascii=False, indent=2)[:3500]

        out, _ = _kimi_chat(system, user, max_tokens=4096)
        refined_obj = _parse_llm_json(out or "")
        style_bible, refined_list = _normalize_refined(refined_obj)
        if not isinstance(refined_list, list) or not refined_list:
            return None
        if style_bible and not style_bible_final:
            style_bible_final = style_bible
        refined_items_all.extend(refined_list)

    # 按 index 建 map
    by_index = {
        int(item.get("index", 0)): (item.get("refined_t2v_prompt") or "").strip()
        for item in refined_items_all
        if isinstance(item, dict)
    }
    if not by_index:
        return None
    style_prefix = _style_bible_prefix(style_bible_final)
    result = []
    for s in storyboard:
        row = dict(s)
        idx = row.get("index", len(result) + 1)
        new_prompt = by_index.get(idx) or by_index.get(int(idx) if isinstance(idx, (int, float)) else 0)
        if new_prompt and len(new_prompt) >= 30 and not re.match(r"^\d+[,，]\s*[^，]+[。.]\s*(固定|移动|跟随)", new_prompt.replace(" ", "")):
            p = _sanitize_cross_shot_refs(new_prompt)
            if style_prefix and style_prefix not in p:
                p = (style_prefix + " " + p).strip()
            row["t2v_prompt"] = p[:1600]
        result.append(row)
    return result if result else None


# 短剧/配音智能选音色：允许返回的 MiniMax 中文音色 ID（与 platform.minimaxi.com 系统音色列表一致）
VOICE_IDS_ALLOWED = frozenset({
    "male-qn-qingse", "male-qn-jingying", "male-qn-badao", "male-qn-daxuesheng",
    "female-shaonv", "female-yujie", "female-chengshu", "female-tianmei",
    "Chinese (Mandarin)_Gentleman", "Chinese (Mandarin)_Warm_Girl",
    "Chinese (Mandarin)_Gentle_Youth", "Chinese (Mandarin)_Crisp_Girl",
    "Chinese (Mandarin)_Reliable_Executive", "Chinese (Mandarin)_News_Anchor",
    "Chinese (Mandarin)_Sweet_Lady", "Chinese (Mandarin)_Lyrical_Voice",
    "junlang_nanyou", "tianxin_xiaoling", "qiaopi_mengmei", "wumei_yujie",
})

# 按性别分组的音色，便于按角色性别选音、避免男女搞反
VOICE_IDS_MALE = frozenset({
    "male-qn-qingse", "male-qn-jingying", "male-qn-badao", "male-qn-daxuesheng",
    "Chinese (Mandarin)_Gentleman", "Chinese (Mandarin)_Gentle_Youth",
    "Chinese (Mandarin)_Reliable_Executive", "Chinese (Mandarin)_Lyrical_Voice",
    "junlang_nanyou",
})
VOICE_IDS_FEMALE = frozenset({
    "female-shaonv", "female-yujie", "female-chengshu", "female-tianmei",
    "Chinese (Mandarin)_Warm_Girl", "Chinese (Mandarin)_Crisp_Girl",
    "Chinese (Mandarin)_News_Anchor", "Chinese (Mandarin)_Sweet_Lady",
    "tianxin_xiaoling", "qiaopi_mengmei", "wumei_yujie",
})


# 常见女性角色名（含从对白「XXX：」里抽到的）：优先用女声，不交给 LLM 避免判错
KNOWN_FEMALE_NAMES = frozenset({
    "绿竹", "小雅", "青萝", "丫鬟", "姑娘", "小姐", "女", "柳姑娘", "梅姑娘",
    "小翠", "小红", "阿秀", "玉儿", "婉儿", "灵儿", "雪儿", "月儿", "芳儿",
    "苏瑶", "林小姐", "李姑娘", "阿妹", "小女", "夫人", "娘娘", "公主",
    "小梅", "小兰", "小竹", "小菊", "春香", "秋香", "冬梅", "夏荷",
})
# 常见男性角色名：优先用男声
KNOWN_MALE_NAMES = frozenset({
    "孟川", "公子", "少爷", "师兄", "男", "师弟", "师父", "老爷", "先生",
    "穆林", "苏平", "塞西", "李华", "小明", "张哥", "王兄", "赵爷", "刘伯",
    "公子哥", "大少爷", "二少爷", "师父父",
})


def _voice_gender_from_name_keywords(name: str) -> Optional[str]:
    """根据角色名中的称谓/关键词推断性别，减少 LLM 误判。返回 'female' | 'male' | None。"""
    if not name or not name.strip():
        return None
    n = name.strip()
    # 女性称谓/后缀优先（避免「师兄」里的兄被当男）
    if any(k in n for k in ("小姐", "姑娘", "丫鬟", "夫人", "娘娘", "公主", "妃", "婆", "婶")):
        return "female"
    if n.endswith("妹") or n.endswith("姐") or ("姐" in n and "师兄" not in n) or "妹" in n or ("女" in n and "男女" not in n):
        return "female"
    if any(k in n for k in ("公子", "少爷", "师兄", "师弟", "师父", "老爷", "先生", "爷", "叔", "伯", "郎")):
        return "male"
    if n.endswith("兄") or n.endswith("弟") or n.endswith("郎"):
        return "male"
    # 君：仅当以「君」结尾且非「君君」时判男（君君多为女名）
    if n.endswith("君") and "君君" not in n and n != "君君":
        return "male"
    return None


def infer_voice_for_drama_line(
    line_text: str,
    character_name: Optional[str] = None,
    script_snippet: str = "",
    voice_cache: Optional[dict[str, str]] = None,
) -> str:
    """
    按镜推断配音音色。若提供 character_name，先按已知男女名或称谓关键词直接选音色（避免 LLM 判错），
    再查 voice_cache（同一剧本内同一角色复用，避免前后镜男女混乱）；否则用 LLM 判断。
    无角色名时退化为仅根据对白内容推断。
    """
    if not (line_text and line_text.strip()):
        return "male-qn-qingse"

    name = (character_name or "").strip()
    # 同一剧本内同一角色复用音色，避免前后镜男女混乱
    if voice_cache is not None and name and name in voice_cache:
        return voice_cache[name]

    # 有角色名时优先按已知名单或称谓关键词直接选性别，不依赖 LLM
    if name:
        for n in KNOWN_FEMALE_NAMES:
            if n in name or name == n:
                out = "female-yujie"
                if voice_cache is not None:
                    voice_cache[name] = out
                return out
        for n in KNOWN_MALE_NAMES:
            if n in name or name == n:
                out = "male-qn-jingying"
                if voice_cache is not None:
                    voice_cache[name] = out
                return out
        # 称谓关键词：小姐/姑娘/公子/少爷等
        gender = _voice_gender_from_name_keywords(name)
        if gender == "female":
            out = "female-yujie"
            if voice_cache is not None:
                voice_cache[name] = out
            return out
        if gender == "male":
            out = "male-qn-jingying"
            if voice_cache is not None:
                voice_cache[name] = out
            return out

    if not KIMI_API_KEY:
        return "male-qn-qingse"
    sample = line_text.strip()[:800]
    snippet = (script_snippet or "").strip()[:600]

    if name:
        # 已知名单未命中，再用 LLM；结果仍用名单兜底，并写入 cache
        system = """你是配音选角助手。根据「角色名」「该句对白」和「剧本摘要」判断说话人性别与气质，**必须**在对应性别音色中选一个，不得搞错男女。
只回复一个音色ID，不要任何解释或标点。

男声（只能从以下选一个）：
male-qn-qingse, male-qn-jingying, male-qn-badao, male-qn-daxuesheng,
Chinese (Mandarin)_Gentleman, Chinese (Mandarin)_Gentle_Youth,
Chinese (Mandarin)_Reliable_Executive, Chinese (Mandarin)_Lyrical_Voice, junlang_nanyou

女声（只能从以下选一个）：
female-shaonv, female-yujie, female-chengshu, female-tianmei,
Chinese (Mandarin)_Warm_Girl, Chinese (Mandarin)_Crisp_Girl,
Chinese (Mandarin)_News_Anchor, Chinese (Mandarin)_Sweet_Lady,
tianxin_xiaoling, qiaopi_mengmei, wumei_yujie

规则：先根据角色名和剧本**明确判断该角色是男是女**，女性角色必须从女声列表选，男性必须从男声列表选；再在对应性别中选最贴合气质的一个（如少女→female-shaonv/tianxin_xiaoling，御姐→female-yujie，公子/温润→Chinese (Mandarin)_Gentleman，沉稳→male-qn-jingying）。常见女性：绿竹、小雅、青萝、丫鬟、姑娘、小姐、XX姑娘；男性：孟川、公子、少爷、师兄、XX公子。"""
        user = f"角色名：{name}\n本句对白：{sample}"
        if snippet:
            user += f"\n剧本摘要（供判断角色性别与气质）：{snippet}"
        out, _ = _kimi_chat(system, user, max_tokens=80)
        if out:
            voice_id = out.strip().split("\n")[0].strip().strip(".").strip()
            voice_lower = voice_id.lower().replace(" ", "_")
            
            # 先根据角色名判断性别
            name_lower = name.lower()
            is_male_name = any(n in name_lower for n in ("孟川", "公子", "少爷", "师兄", "少年", "男子", "大侠"))
            is_female_name = any(n in name_lower for n in ("绿竹", "小雅", "青萝", "丫鬟", "姑娘", "小姐", "少女", "女子", "师姐"))
            
            # 如果角色名明显是男性但 LLM 返回女性音色，拒绝
            if is_male_name:
                is_female_suggested = (
                    voice_id in VOICE_IDS_FEMALE or 
                    any(v in voice_id for v in ("female", "女", "Girl", "Lady", "Anchor", "tianxin", "qiaopi", "wumei"))
                )
                if is_female_suggested:
                    logger.warning("配音选择：角色'%s'是男性，但LLM返回女性音色'%s'，已拒绝", name, voice_id)
                    res = "male-qn-jingying"
                    if voice_cache is not None and name:
                        voice_cache[name] = res
                    return res
            
            # 如果角色名明显是女性但 LLM 返回男性音色，拒绝
            if is_female_name:
                is_male_suggested = (
                    voice_id in VOICE_IDS_MALE or 
                    any(v in voice_id for v in ("male", "男", "Gentleman", "Youth", "Executive", "Lyrical", "junlang"))
                )
                if is_male_suggested:
                    logger.warning("配音选择：角色'%s'是女性，但LLM返回男性音色'%s'，已拒绝", name, voice_id)
                    res = "female-yujie"
                    if voice_cache is not None and name:
                        voice_cache[name] = res
                    return res
            
            # 现在可以安全接受 LLM 的建议了
            if voice_id in VOICE_IDS_ALLOWED:
                if voice_cache is not None and name:
                    voice_cache[name] = voice_id
                return voice_id
            for vid in VOICE_IDS_ALLOWED:
                if vid.lower().replace(" ", "_") == voice_lower:
                    if voice_cache is not None and name:
                        voice_cache[name] = vid
                    return vid
            
            # 兜底：根据性别选择
            if is_male_name:
                res = "male-qn-jingying"
                if voice_cache is not None and name:
                    voice_cache[name] = res
                return res
            if is_female_name:
                res = "female-yujie"
                if voice_cache is not None and name:
                    voice_cache[name] = res
                return res
            
            # 最后的兜底
            if voice_id in VOICE_IDS_MALE or any(v in voice_id for v in ("male", "男", "Gentleman", "Youth", "Executive", "Lyrical", "junlang")):
                res = "male-qn-jingying"
                if voice_cache is not None and name:
                    voice_cache[name] = res
                return res
            if voice_id in VOICE_IDS_FEMALE or any(v in voice_id for v in ("female", "女", "Girl", "Lady", "Anchor", "tianxin", "qiaopi", "wumei")):
                res = "female-yujie"
                if voice_cache is not None and name:
                    voice_cache[name] = res
                return res
        
        # 无角色名时沿用原有逻辑
        return infer_voice_for_drama(line_text)


def infer_voice_for_drama(script_text: str) -> str:
    """
    根据短剧旁白/对白内容，用 LLM 推断主要叙述者或主角的性别与气质，返回 MiniMax 音色 ID。
    用于成片配音时智能选择男声/女声及风格（青年/成熟、活泼/沉稳等）。
    失败或未配置 LLM 时返回默认男声 male-qn-qingse。
    """
    if not (KIMI_API_KEY and script_text and script_text.strip()):
        return "male-qn-qingse"
    sample = script_text.strip()[:1500]
    system = """你是配音选角助手。根据以下短剧旁白/对白内容，判断主要叙述者或主角的性别与气质。
只回复一个音色ID，不要任何解释或标点。

可选音色ID（只能回复其中一个）：
- 男声青年/青涩：male-qn-qingse
- 男声精英/沉稳：male-qn-jingying
- 男声霸道/强势：male-qn-badao
- 男声大学生：male-qn-daxuesheng
- 女声少女：female-shaonv
- 女声御姐：female-yujie
- 女声成熟：female-chengshu
- 女声甜美：female-tianmei
- 男声温润：Chinese (Mandarin)_Gentleman
- 女声温暖少女：Chinese (Mandarin)_Warm_Girl
- 男声温润青年：Chinese (Mandarin)_Gentle_Youth
- 女声清脆少女：Chinese (Mandarin)_Crisp_Girl
- 男声沉稳高管：Chinese (Mandarin)_Reliable_Executive
- 女声新闻主播：Chinese (Mandarin)_News_Anchor
- 女声甜美：Chinese (Mandarin)_Sweet_Lady
- 男声抒情：Chinese (Mandarin)_Lyrical_Voice
- 俊朗男友：junlang_nanyou
- 甜心小玲：tianxin_xiaoling
- 俏皮萌妹：qiaopi_mengmei
- 妩媚御姐：wumei_yujie

根据内容中的人称（他/她）、语气、角色设定选择最贴合的单一音色ID。"""
    user = f"短剧旁白/对白摘要：\n{sample}"
    out, _ = _kimi_chat(system, user, max_tokens=80)
    if not out:
        return "male-qn-qingse"
    voice_id = out.strip().split("\n")[0].strip().strip(".").strip()
    if voice_id in VOICE_IDS_ALLOWED:
        return voice_id
    voice_lower = voice_id.lower().replace(" ", "_")
    for vid in VOICE_IDS_ALLOWED:
        if vid.lower().replace(" ", "_") == voice_lower:
            return vid
    return "male-qn-qingse"


# 与火山 TTS 多情感音色对齐的情绪标签
DRAMA_EMOTION_VALUES = frozenset({"happy", "sad", "angry", "surprised", "fear", "excited", "coldness", "neutral", "hate"})


def infer_emotion_for_drama_lines(
    shots: list[dict],
    script_snippet: str = "",
) -> list[Optional[str]]:
    """
    根据剧本上下文为每条分镜的台词推断情绪，用于 TTS 情感合成（火山多情感音色等）。
    短句单独看难以判断情绪，结合前后镜对白与剧本摘要可显著提升代入感。
    返回与 shots 等长的 list，每项为 DRAMA_EMOTION_VALUES 之一或 None（fallback 到关键词推断）。
    """
    if not (KIMI_API_KEY and shots):
        return [None] * len(shots)
    lines_for_llm = []
    for i, s in enumerate(shots):
        copy = (s.get("copy") or s.get("copy_text") or "").strip()
        prev = (shots[i - 1].get("copy") or shots[i - 1].get("copy_text") or "").strip() if i > 0 else ""
        nxt = (shots[i + 1].get("copy") or shots[i + 1].get("copy_text") or "").strip() if i + 1 < len(shots) else ""
        lines_for_llm.append({"index": i + 1, "line": copy[:200], "prev": prev[:120], "next": nxt[:120]})
    system = """你是短剧配音情绪标注助手。根据每一句台词、前后句、说话人身份与剧本语境，判断该句应使用的**说话情绪**，使 TTS 合成有代入感、情感分明、不显平淡。

规则：
- 只输出一个 JSON 数组，每项形如 {"index": 镜头序号, "emotion": "情绪"}。
- emotion 只能从以下选一：happy（开心/亲切）, sad（难过/委屈）, angry（生气/不耐烦）, surprised（惊讶）, fear（害怕/紧张）, excited（激动/兴奋）, coldness（冷淡/克制）, hate（厌恶）, neutral（平静/中性）。

【严禁整段戏大量 neutral，必须情感分明、有起伏】
- **至少三分之二镜头**应有明确非 neutral 情绪（happy / excited / coldness / sad / surprised / angry 等），整段戏要有明显起伏，避免整体偏平。
- 邀请、请求、期待、热情告知、催促 → 必须用 happy 或 excited，禁止用 neutral。例如：请公子、同去、别院可宿、快来、美得很、想请、求求、拜托 → excited 或 happy。
- 拒绝、婉拒、解释、推辞、冷淡回应、告辞 → 必须用 coldness 或 neutral。例如：不必了、太远、不能同行、转告、算了、告辞、免了 → coldness。
- 问候、温和回应、礼节性 → 用 happy 或 neutral。例如：诸位师弟师妹早、多谢公子、幸会、有劳 → happy。
- 少女/丫鬟跑着喊人、催促、兴奋通知 → excited。例如：公子公子、小姐、快来 → excited。
- 遗憾、告别、舍不得、叹气 → sad 或 neutral。惊讶、意外、怎会 → surprised。生气、烦、放肆 → angry。
- 无台词或纯动作的镜头填 neutral。"""
    user = "请为以下每镜台词标注情绪，输出 JSON 数组，不要 markdown 包裹。要求：**情感分明、起伏明显**，至少三分之二镜头标非 neutral，邀请/热情用 happy 或 excited、拒绝/冷淡用 coldness，严禁大量标 neutral。\n"
    if script_snippet and script_snippet.strip():
        user += f"剧本摘要（供语境与人物关系）：\n{script_snippet.strip()[:600]}\n\n"
    user += "每镜台词（含前后句，便于判断情绪起伏）：\n" + json.dumps(lines_for_llm, ensure_ascii=False, indent=2)
    out, _ = _kimi_chat(system, user, max_tokens=1024)
    if not out or not out.strip():
        return [None] * len(shots)
    out = out.strip()
    if "```" in out:
        for sep in ("```json", "```"):
            if sep in out:
                i = out.find(sep) + len(sep)
                j = out.find("```", i)
                if j > i:
                    out = out[i:j].strip()
                    break
    try:
        arr = json.loads(out)
    except json.JSONDecodeError:
        return [None] * len(shots)
    if not isinstance(arr, list):
        return [None] * len(shots)
    by_index = {}
    for item in arr:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        em = (item.get("emotion") or "").strip().lower()
        if em not in DRAMA_EMOTION_VALUES:
            em = "neutral" if em else None
        by_index[int(idx) if idx is not None else 0] = em
    result = []
    for i in range(len(shots)):
        em = by_index.get(i + 1) or by_index.get(i)
        result.append(em if em in DRAMA_EMOTION_VALUES else None)
    # 加强：LLM 标成 neutral 的镜头，若台词有关键词情绪则用关键词覆盖，避免情感过平
    try:
        from app.services import volcano_speech
        for i in range(len(shots)):
            if result[i] != "neutral":
                continue
            copy = (shots[i].get("copy") or shots[i].get("copy_text") or "").strip()
            if not copy:
                continue
            kw_em = volcano_speech.infer_emotion_from_text(copy)
            if kw_em and kw_em != "neutral" and kw_em in DRAMA_EMOTION_VALUES:
                result[i] = kw_em
    except Exception:
        pass
    return result


def suggest_video_mode_llm(
    storyboard: list[dict],
    script_summary: str,
    pipeline: str = "script_drama",
) -> Optional[tuple[str, str]]:
    """
    由 Kimi 根据分镜与剧本上下文推荐最佳视频生成方式（无人物/商品参考图时使用）。
    返回 (mode, reason)，mode 为 t2v_single | t2v_multi | i2v_multi | smart_multiframe；失败返回 None。
    """
    if not (KIMI_API_KEY and storyboard):
        return None
    shots_text = "\n".join(
        f"{i+1}. 画面：{s.get('shot_desc','')} | 文案：{(s.get('copy') or '')[:80]}"
        for i, s in enumerate(storyboard[:10])
    )
    system = """你是视频生成策略助手。根据分镜与剧本，从以下四种方式中选一种最合适的，只输出一行：模式|一句话理由。
- smart_multiframe：智能多帧。多张关键帧图片，每两帧之间用提示词描述过渡（发生了什么），首尾帧(FL2V)串联成一段视频；适合多镜头、每镜有明确画面且需要连贯过渡时。
- t2v_single：单段文生视频。仅当分镜只有 1 条时用。
- t2v_multi：多段文生再拼接。每镜单独一条文生视频，生成多段再拼接；适合多段独立镜头、无关键帧图时。
- i2v_multi：先生图再图生视频。每镜先文生图再图生视频，画面更可控；适合对画面质量要求高、场景描述具体的分镜。

输出格式严格为：模式|理由。例如：smart_multiframe|多镜有明确画面，智能多帧用关键帧+过渡提示词串联。"""
    user = f"""管线类型：{pipeline}
剧本摘要（前 300 字）：
{script_summary[:300]}

分镜列表：
{shots_text}

请选择最合适的生成方式并输出：模式|理由（一行）。"""
    out, _ = _kimi_chat(system, user, max_tokens=150)
    if not out or "|" not in out:
        return None
    line = out.strip().split("\n")[0]
    if "|" in line:
        mode_part, reason = line.split("|", 1)
        mode = mode_part.strip().lower().replace(" ", "")
        reason = reason.strip()[:200]
        if mode in ("t2v_single", "t2v_multi", "i2v_multi", "smart_multiframe"):
            return (mode, reason)
    return None
