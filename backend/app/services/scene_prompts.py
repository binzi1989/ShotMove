"""
影视场景类型与提示词增强：为不同场景（战斗、竞速、追逐、情感等）提供专用 prompt 规则，
与 video-prompt-quality-skill、script-drama-skill 配合，在分镜精修或生成时按场景增强 t2v_prompt。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 影视场景类型定义（与 scene-type-prompts-skill 一致）
# 每类包含：标识、中文名、检测关键词、提示词增强指引（用于 LLM 精修时注入）
# ---------------------------------------------------------------------------

SCENE_TYPE_BATTLE = "battle"           # 战斗
SCENE_TYPE_RACING = "racing"           # 竞速
SCENE_TYPE_CHASE = "chase"             # 追逐
SCENE_TYPE_DIALOGUE = "dialogue"       # 对话
SCENE_TYPE_EMOTIONAL = "emotional"     # 情感
SCENE_TYPE_SUSPENSE = "suspense"       # 悬疑
SCENE_TYPE_ACTION = "action"           # 动作/冒险
SCENE_TYPE_DISASTER = "disaster"       # 灾难
SCENE_TYPE_MUSICAL = "musical"         # 歌舞
SCENE_TYPE_SPORTS = "sports"           # 体育
SCENE_TYPE_CEREMONY = "ceremony"       # 婚礼/庆典
SCENE_TYPE_COURT = "court"             # 法庭
SCENE_TYPE_MEDICAL = "medical"         # 医院
SCENE_TYPE_CAMPUS = "campus"           # 校园
SCENE_TYPE_PERIOD_WUXIA = "period_wuxia"  # 古装/武侠
SCENE_TYPE_SCI_FI = "sci_fi"           # 科幻
SCENE_TYPE_DAILY = "daily"             # 日常
SCENE_TYPE_NATURE = "nature"          # 自然/风景
SCENE_TYPE_DEFAULT = "default"        # 未识别时默认


@dataclass
class SceneTypeDef:
    """单种场景类型定义"""
    code: str
    name_cn: str
    keywords: list[str]  # 用于从 shot_desc / copy / t2v_prompt 中检测
    prompt_guidance: str  # 给 LLM 精修时的场景专用指引（可追加到 system 或 user）


# 场景类型注册表：code -> SceneTypeDef
SCENE_REGISTRY: dict[str, SceneTypeDef] = {
    SCENE_TYPE_BATTLE: SceneTypeDef(
        code=SCENE_TYPE_BATTLE,
        name_cn="战斗",
        keywords=[
            "打斗", "刀剑", "枪战", "格斗", "搏斗", "厮杀", "对决", "比武", "过招",
            "战争", "战场", "冲锋", "开火", "爆炸", "硝烟", "剑", "刀", "拳", "踢",
        ],
        prompt_guidance="战斗场景：强调动态与张力——肢体碰撞、武器轨迹、尘土/火花、人物重心与发力方向；"
                       "景别以中近景与特写为主，可写「快速切镜」「跟拍动作」；光线可写逆光剪影、烟尘透光、冷兵器反光。",
    ),
    SCENE_TYPE_RACING: SceneTypeDef(
        code=SCENE_TYPE_RACING,
        name_cn="竞速",
        keywords=[
            "赛车", "竞速", "飙车", "赛道", "引擎", "漂移", "超车", "冲刺", "终点",
            "摩托车", "赛车手", "弯道", "直线加速", "起跑",
        ],
        prompt_guidance="竞速场景：强调速度与动势——车身/骑手与环境的相对运动、轮胎烟、气流、速度线感；"
                       "景别可用跟拍、低角度、车侧掠过的中景；光线可写赛道灯、车灯拖影、日光/夜景统一。",
    ),
    SCENE_TYPE_CHASE: SceneTypeDef(
        code=SCENE_TYPE_CHASE,
        name_cn="追逐",
        keywords=[
            "追逐", "追赶", "逃跑", "追踪", "追捕", "狂奔", "躲藏", "逃脱", "尾随",
        ],
        prompt_guidance="追逐场景：强调方向与节奏——奔跑方向、障碍物、呼吸与脚步、跟拍或前跟；"
                       "景别以中景、跟拍为主，可写「镜头随人物移动」「掠过障碍」。",
    ),
    SCENE_TYPE_DIALOGUE: SceneTypeDef(
        code=SCENE_TYPE_DIALOGUE,
        name_cn="对话",
        keywords=[
            "对话", "对白", "交谈", "会议", "谈判", "讨论", "说话", "询问", "回答",
            "室内", "办公室", "客厅", "会议室", "咖啡厅",
        ],
        prompt_guidance="对话场景：保持镜头轴线与人物朝向一致；有对白时必须在动作中写「嘴唇/口型随说话张合」；"
                       "景别以中景、近景、正反打为主，光线统一室内光源。",
    ),
    SCENE_TYPE_EMOTIONAL: SceneTypeDef(
        code=SCENE_TYPE_EMOTIONAL,
        name_cn="情感",
        keywords=[
            "离别", "重逢", "拥抱", "哭泣", "眼泪", "温情", "感动", "告白", "分手",
            "思念", "沉默", "对视", "牵手", "依偎", "安慰",
        ],
        prompt_guidance="情感场景：突出神态与氛围——表情微动、眼神、肢体接触、光线柔和；"
                       "景别以近景、特写为主，可写「柔光」「暖色调」「景深浅」。",
    ),
    SCENE_TYPE_SUSPENSE: SceneTypeDef(
        code=SCENE_TYPE_SUSPENSE,
        name_cn="悬疑",
        keywords=[
            "悬疑", "紧张", "侦探", "暗影", "惊悚", "诡异", "神秘", "窥视", "脚步声",
            "阴影", "门缝", "回头", "屏住呼吸",
        ],
        prompt_guidance="悬疑场景：强调光影与节奏——明暗对比、局部光、阴影移动、缓慢运镜；"
                       "景别以特写、过肩、窥视视角为主，可写「低照度」「冷色调」。",
    ),
    SCENE_TYPE_ACTION: SceneTypeDef(
        code=SCENE_TYPE_ACTION,
        name_cn="动作/冒险",
        keywords=[
            "跑酷", "攀爬", "跳跃", "冒险", "闯关", "翻越", "滑索", "攀岩", "跑酷",
        ],
        prompt_guidance="动作/冒险场景：强调肢体与环境的互动——发力、落点、障碍、跟拍或固定机位捕捉动作弧线。",
    ),
    SCENE_TYPE_DISASTER: SceneTypeDef(
        code=SCENE_TYPE_DISASTER,
        name_cn="灾难",
        keywords=[
            "爆炸", "火灾", "地震", "洪水", "坍塌", "海啸", "龙卷风", "灾难", "废墟",
        ],
        prompt_guidance="灾难场景：强调规模与冲击——烟尘、碎片、人群反应、广角与特写结合；光线可写火光、烟尘透光。",
    ),
    SCENE_TYPE_MUSICAL: SceneTypeDef(
        code=SCENE_TYPE_MUSICAL,
        name_cn="歌舞",
        keywords=[
            "舞蹈", "唱歌", "舞台", "演唱会", "排练", "伴舞", "节奏", "旋律",
        ],
        prompt_guidance="歌舞场景：强调节奏与肢体——动作与音乐节拍一致、舞台光效、群舞队形；景别可写全景与中近景切换。",
    ),
    SCENE_TYPE_SPORTS: SceneTypeDef(
        code=SCENE_TYPE_SPORTS,
        name_cn="体育",
        keywords=[
            "球赛", "跑步", "运动", "篮球", "足球", "游泳", "田径", "比赛", "进球",
        ],
        prompt_guidance="体育场景：强调动势与结果——发力瞬间、球/人的轨迹、观众反应；景别可写跟拍、慢动作特写。",
    ),
    SCENE_TYPE_CEREMONY: SceneTypeDef(
        code=SCENE_TYPE_CEREMONY,
        name_cn="婚礼/庆典",
        keywords=[
            "婚礼", "庆典", "派对", "典礼", "宴会", "仪式", "婚纱", "捧花", "香槟",
        ],
        prompt_guidance="婚礼/庆典场景：强调氛围与仪式感——暖光、装饰、人群、微笑与掌声；景别以中景、全景为主。",
    ),
    SCENE_TYPE_COURT: SceneTypeDef(
        code=SCENE_TYPE_COURT,
        name_cn="法庭",
        keywords=[
            "法庭", "辩论", "审讯", "律师", "法官", "被告", "原告", "开庭",
        ],
        prompt_guidance="法庭场景：强调严肃与对峙——正反打、法袍与桌案、庄重光线；景别以中景、过肩为主。",
    ),
    SCENE_TYPE_MEDICAL: SceneTypeDef(
        code=SCENE_TYPE_MEDICAL,
        name_cn="医院",
        keywords=[
            "医院", "手术", "病房", "医生", "护士", "急救", "手术室", "病床", "输液",
        ],
        prompt_guidance="医院场景：强调冷静与生命感——冷光、器械、监护仪、人物表情；景别可写特写与中景。",
    ),
    SCENE_TYPE_CAMPUS: SceneTypeDef(
        code=SCENE_TYPE_CAMPUS,
        name_cn="校园",
        keywords=[
            "教室", "操场", "宿舍", "校园", "图书馆", "食堂", "下课", "上课", "考试",
        ],
        prompt_guidance="校园场景：保持自然光与青春感；景别以中景、全景为主，可写「日光」「课桌」「黑板」。",
    ),
    SCENE_TYPE_PERIOD_WUXIA: SceneTypeDef(
        code=SCENE_TYPE_PERIOD_WUXIA,
        name_cn="古装/武侠",
        keywords=[
            "古装", "武侠", "江湖", "宫殿", "宫廷", "侠客", "剑客", "竹林", "客栈",
            "长袍", "发髻", "马匹", "城门", "阁楼",
        ],
        prompt_guidance="古装/武侠场景：服装与场景时代感统一；可写「衣袂飘动」「剑光」「竹林/宫殿/客栈」；光线偏自然或烛火。",
    ),
    SCENE_TYPE_SCI_FI: SceneTypeDef(
        code=SCENE_TYPE_SCI_FI,
        name_cn="科幻",
        keywords=[
            "科幻", "未来", "太空", "机甲", "赛博", "飞船", "星球", "全息", "机器人",
            "激光", "舱内", "星际", "太空站",
        ],
        prompt_guidance="科幻场景：强调科技感与空间——金属质感、冷光/霓虹、景深与纵深感；可写「舱内」「全息」「机械」。",
    ),
    SCENE_TYPE_DAILY: SceneTypeDef(
        code=SCENE_TYPE_DAILY,
        name_cn="日常",
        keywords=[
            "生活", "街头", "家庭", "厨房", "吃饭", "散步", "买菜", "上班", "下班",
        ],
        prompt_guidance="日常场景：自然光、生活化动作与陈设；景别以中景为主，避免过度戏剧化。",
    ),
    SCENE_TYPE_NATURE: SceneTypeDef(
        code=SCENE_TYPE_NATURE,
        name_cn="自然/风景",
        keywords=[
            "风景", "山水", "日出", "日落", "天空", "云海", "森林", "大海", "空镜",
            "远山", "湖面", "草原", "雪山",
        ],
        prompt_guidance="自然/风景场景：强调层次与氛围——天空与地面、光影变化、景深；可写「大景固定」「缓慢推镜」。",
    ),
}


def detect_scene_type(shot: dict[str, Any]) -> str:
    """
    根据单条分镜的 shot_desc、copy、t2v_prompt 检测最匹配的场景类型。
    返回 SCENE_TYPE_* 常量，未匹配时返回 SCENE_TYPE_DEFAULT。
    优先级：按关键词命中数量与顺序，先命中先返回（战斗/竞速等强类型优先）。
    """
    text_parts = [
        (shot.get("shot_desc") or "").strip(),
        (shot.get("copy") or shot.get("copy_text") or "").strip(),
        (shot.get("t2v_prompt") or "").strip(),
    ]
    combined = " ".join(p for p in text_parts if p).lower()

    # 强类型优先顺序（避免被「对话」等泛词抢走）
    priority_order = [
        SCENE_TYPE_BATTLE,
        SCENE_TYPE_RACING,
        SCENE_TYPE_CHASE,
        SCENE_TYPE_DISASTER,
        SCENE_TYPE_MUSICAL,
        SCENE_TYPE_SPORTS,
        SCENE_TYPE_CEREMONY,
        SCENE_TYPE_COURT,
        SCENE_TYPE_MEDICAL,
        SCENE_TYPE_CAMPUS,
        SCENE_TYPE_PERIOD_WUXIA,
        SCENE_TYPE_SCI_FI,
        SCENE_TYPE_SUSPENSE,
        SCENE_TYPE_EMOTIONAL,
        SCENE_TYPE_ACTION,
        SCENE_TYPE_NATURE,
        SCENE_TYPE_DIALOGUE,
        SCENE_TYPE_DAILY,
    ]

    for code in priority_order:
        defn = SCENE_REGISTRY.get(code)
        if not defn:
            continue
        for kw in defn.keywords:
            if kw in combined:
                return code
    return SCENE_TYPE_DEFAULT


def get_scene_guidance_for_shot(shot: dict[str, Any]) -> str:
    """返回该镜头对应场景类型的 prompt_guidance，用于精修时注入。若无则返回空字符串。"""
    code = detect_scene_type(shot)
    if code == SCENE_TYPE_DEFAULT:
        return ""
    defn = SCENE_REGISTRY.get(code)
    return (defn.prompt_guidance or "").strip()


def get_scene_guidance_for_refine(storyboard: list[dict[str, Any]]) -> str:
    """
    根据整组分镜统计场景类型，汇总成一段「场景指引」文案，供 refine_storyboard_t2v_prompts_llm
    的 user 或 system 追加使用。仅包含本片出现的场景类型，避免冗长。
    """
    seen: set[str] = set()
    parts: list[str] = []
    for shot in storyboard:
        code = detect_scene_type(shot)
        if code in seen or code == SCENE_TYPE_DEFAULT:
            continue
        seen.add(code)
        defn = SCENE_REGISTRY.get(code)
        if defn and defn.prompt_guidance:
            parts.append(f"【{defn.name_cn}】{defn.prompt_guidance}")
    if not parts:
        return ""
    return "\n\n".join(parts)


def list_scene_types() -> list[dict[str, str]]:
    """返回所有已注册场景类型（code + name_cn），供前端或配置使用。"""
    return [
        {"code": defn.code, "name_cn": defn.name_cn}
        for defn in SCENE_REGISTRY.values()
        if defn.code != SCENE_TYPE_DEFAULT
    ]
