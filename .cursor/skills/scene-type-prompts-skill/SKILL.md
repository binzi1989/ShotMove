---
name: scene-type-prompts-skill
description: Defines special film/video scene types (battle, racing, chase, dialogue, emotional, suspense, etc.) and their prompt guidelines. Use when refining or generating t2v_prompt for storyboard shots so that scene-specific rules (e.g. 战斗/竞速/情感) are applied via the existing video-prompt-quality and script-drama pipeline.
---

# 影视场景类型提示词 Skill

本 Skill 约定**特殊影视场景**的提示词增强规则，与 **video-prompt-quality-skill**、**script-drama-skill** 配合使用。后端在分镜生成与精修时，会根据镜头内容自动识别场景类型（如战斗、竞速、追逐、情感、悬疑等），并调用本 Skill 的规则对 `t2v_prompt` 做场景化增强，提升成片在对应场景下的表现力。

## 何时触发

- 分镜精修（`refine_storyboard_t2v_prompts_llm`）时，根据整组分镜统计出现的场景类型，将对应指引注入精修 prompt。
- 需要为「战斗」「竞速」「情感」等特定场景单独优化提示词时，可查阅本 Skill 的规则并在 Cursor 中手动补充或调用 `scene_prompts.get_scene_guidance_for_shot`。

## 已规划场景类型（与代码一致）

| 场景 code       | 中文名     | 典型关键词示例 |
|----------------|------------|----------------|
| battle         | 战斗       | 打斗、刀剑、枪战、格斗、战争、对决 |
| racing         | 竞速       | 赛车、飙车、赛道、漂移、超车 |
| chase          | 追逐       | 追逐、追赶、逃跑、追踪、狂奔 |
| dialogue       | 对话       | 对话、对白、会议、室内、办公室 |
| emotional      | 情感       | 离别、重逢、拥抱、哭泣、温情、告白 |
| suspense       | 悬疑       | 悬疑、紧张、侦探、暗影、惊悚、窥视 |
| action         | 动作/冒险  | 跑酷、攀爬、跳跃、冒险、翻越 |
| disaster       | 灾难       | 爆炸、火灾、地震、洪水、废墟 |
| musical        | 歌舞       | 舞蹈、唱歌、舞台、演唱会 |
| sports         | 体育       | 球赛、跑步、运动、比赛、进球 |
| ceremony       | 婚礼/庆典  | 婚礼、庆典、派对、典礼、宴会 |
| court          | 法庭       | 法庭、辩论、审讯、律师、法官 |
| medical        | 医院       | 医院、手术、病房、急救 |
| campus         | 校园       | 教室、操场、宿舍、校园、图书馆 |
| period_wuxia   | 古装/武侠  | 古装、武侠、江湖、宫殿、侠客、竹林 |
| sci_fi         | 科幻       | 科幻、未来、太空、机甲、赛博、飞船 |
| daily          | 日常       | 生活、街头、家庭、厨房、上班 |
| nature         | 自然/风景  | 风景、山水、日出日落、空镜、森林 |

## 与现有 Skills 的配合

1. **video-prompt-quality-skill**：精修时仍以五段式（主体+场景+动作+风格+镜头语言）为基础；本 Skill 仅追加**场景专用**的运镜、光线、节奏建议，不替代通用规范。
2. **script-drama-skill**：分镜生成阶段不强制打场景标签；精修阶段由 `scene_prompts.detect_scene_type` 自动识别，再通过 `get_scene_guidance_for_refine` 将本片中出现的场景指引注入 LLM，从而在现有 `refine_storyboard_t2v_prompts_llm` 流程中生效。
3. **minimax-video-skill**：生成的 t2v_prompt 会随精修结果一并传给视频生成；场景增强后的 prompt 更利于战斗/竞速等镜头在可灵/MiniMax 上的表现。

## 实现位置（本项目）

- **场景定义与检测**：`backend/app/services/scene_prompts.py`
  - `SCENE_REGISTRY`：场景类型注册表
  - `detect_scene_type(shot)`：单镜场景类型
  - `get_scene_guidance_for_shot(shot)`：单镜场景指引文案
  - `get_scene_guidance_for_refine(storyboard)`：整组分镜的场景指引汇总
- **精修注入**：`backend/app/services/llm.py` 中 `refine_storyboard_t2v_prompts_llm` 在构造 user 时调用 `get_scene_guidance_for_refine(storyboard)`，将返回的文案追加到首轮 user 中，供 Kimi 在精修时参考。

## Guidelines

- 新增场景类型时，在 `scene_prompts.SCENE_REGISTRY` 中增加 `SceneTypeDef`，并同步更新本 SKILL.md 上表。
- 场景指引尽量简洁，只写「运镜/光线/景别」等可执行要点，避免与 video-prompt-quality-skill 的通用规则重复或冲突。
