"""输入路由 Agent：判断用户输入类型并决定走短剧管线或澄清"""
import re
from typing import Literal

# 剧本特征：多角色对白
DIALOGUE_PATTERN = re.compile(
    r"[A-Za-z\u4e00-\u9fa5]{1,4}\s*[：:]\s*"  # 角色名 + 冒号
)

# 剧本特征关键词
SCRIPT_KEYWORDS = [
    "场景", "镜头", "内景", "外景", "分镜", "转场", "场次",
    "第一场", "第二场", "切", "叠", "淡入", "淡出",
]

# 短剧意图
DRAMA_INTENT_KEYWORDS = ["短剧", "剧本", "分镜", "剧情", "对白", "角色"]


def classify_input(user_input: str) -> tuple[Literal["script", "natural_language"], Literal["script_drama", "clarify"], str | None]:
    """
    分类用户输入，返回 (input_type, pipeline, debug_note)。
    - script -> script_drama
    - natural_language -> clarify 或根据关键词建议 pipeline
    """
    text = (user_input or "").strip()
    if not text:
        return "natural_language", "clarify", "输入为空"

    # 剧本/短剧：纯文本且满足剧本特征
    has_dialogue = len(DIALOGUE_PATTERN.findall(text)) >= 2
    has_script_keywords = any(kw in text for kw in SCRIPT_KEYWORDS)
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    has_structure = len(paragraphs) >= 3

    if (has_dialogue and has_script_keywords) or (has_dialogue and has_structure) or (has_script_keywords and has_structure):
        return "script", "script_drama", "检测到剧本/分镜结构"

    # 自然语言需求：根据关键词建议管线
    if any(kw in text for kw in DRAMA_INTENT_KEYWORDS):
        return "natural_language", "clarify", "建议提供剧本或需求描述以走短剧管线"

    return "natural_language", "clarify", "未识别类型，请提供剧本或对白内容"
