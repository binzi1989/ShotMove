---
name: audio-voice-skill
description: Adds BGM (MiniMax music) and voiceover (MiniMax TTS) to merged video; for product video infers voice from script scenario (轻松/专业/甜美等), for short drama infers character gender/style from script. Use when user enables "添加 BGM" or "添加配音".
---

# 成片 BGM 与配音 Skill（音频增强管线）

在本地合成成片后，可选为成片添加 **BGM 背景音乐** 与 **TTS 配音**；**商品短视频**与**短剧**均会根据生成的剧本/场景**自动分析并选择配音音色**（男声/女声、青年/成熟、轻松/专业、甜美/沉稳等）。

## 实现位置（本项目的真实代码）

| 步骤 | 代码位置 | 说明 |
|------|----------|------|
| BGM 生成 | `backend/app/services/minimax_music.py` → `generate_bgm()` | 调用 MiniMax `POST /v1/music_generation`，music-2.5，prompt/lyrics 生成纯 BGM |
| TTS 配音 | `backend/app/services/minimax_speech.py` → `text_to_speech(text, voice_id=...)` | 调用 MiniMax `POST /v1/t2a_v2` 同步语音合成；`voice_id` 可由智能推断传入 |
| 智能选音色（短剧） | `backend/app/services/llm.py` → `infer_voice_for_drama(script_text)` | **短剧管线**：用 Kimi 分析旁白/对白，推断主要叙述者性别与气质，返回 MiniMax 音色 ID |
| 智能选音色（商品） | `backend/app/services/llm.py` → `infer_voice_for_product_script(script_text)` | **商品短视频管线**：用 Kimi 分析种草口播脚本的风格与场景（轻松活泼/专业测评/甜美种草等），返回最贴合的 MiniMax 音色 ID |
| 混音 | `backend/app/services/video_concat.py` → `mix_audio_into_merged(merged_api_path, voice_mp3_path, bgm_mp3_path)` | ffmpeg 将配音 + BGM 混入成片，输出 `xxx_vo.mp4` |
| 入口 | `backend/app/main.py` → `_add_bgm_and_voiceover(..., pipeline=...)` | 当 `with_bgm` 或 `with_voiceover` 且已有 `merged_url` 时调用；`pipeline` 为 `script_drama` 或 `product_video` 时分别调用对应推断函数 |

## 何时触发

- 用户勾选「**添加 BGM**」或「**添加配音**」且成片已本地合成（`merged_url` 存在）
- **商品短视频**：配音会根据**种草脚本的风格与场景**智能选音色（轻松活泼→青年/少女、专业测评→精英/新闻主播、甜美种草→甜美/温暖少女等）；未配置 Kimi 时回退 `male-qn-qingse`
- **短剧**：配音会根据**剧本旁白/对白**推断主角或主要叙述者的性别与气质选音色（见下）

## 商品短视频智能选音色规则

1. **输入**：成片配音所用文案 = 商品种草口播脚本（`result.script`），前约 1500 字送入 LLM。
2. **推断**：由 Kimi 根据脚本风格与场景（轻松活泼、专业测评、甜美种草、沉稳推荐、年轻口吻等）选择最贴合的配音音色。
3. **输出**：LLM 只回复**一个** MiniMax 系统音色 ID，且必须在 `llm.VOICE_IDS_ALLOWED` 内；否则回退为 `male-qn-qingse`。
4. **场景与音色建议**：轻松活泼→male-qn-qingse / female-shaonv / Chinese (Mandarin)_Warm_Girl；专业测评→male-qn-jingying / Reliable_Executive / News_Anchor；甜美种草→female-tianmei / Sweet_Lady / tianxin_xiaoling / qiaopi_mengmei；御姐/气场→female-yujie / wumei_yujie；温润可信→Gentleman / Gentle_Youth。

## 短剧智能选音色规则（必须遵守）

1. **输入**：成片配音所用文案 = 短剧分镜的「对白/旁白」拼接（`script_text`），前约 1500 字送入 LLM。
2. **推断**：由 Kimi 根据人称（他/她）、语气、角色设定判断「主要叙述者或主角」的性别与气质。
3. **输出**：LLM 只回复**一个** MiniMax 系统音色 ID，且必须在 `llm.VOICE_IDS_ALLOWED` 内；否则回退为 `male-qn-qingse`。
4. **允许音色示例**：男声青年 `male-qn-qingse`、男声精英 `male-qn-jingying`、女声少女 `female-shaonv`、女声御姐 `female-yujie`、女声成熟 `female-chengshu`、温润男声 `Chinese (Mandarin)_Gentleman`、温暖少女 `Chinese (Mandarin)_Warm_Girl` 等（完整集合见 `backend/app/services/llm.py` 中 `VOICE_IDS_ALLOWED`）。

## 配置

- **MiniMax**：`MINIMAX_API_KEY`（与视频/图片共用），用于音乐生成与 TTS
- **Kimi**：`KIMI_API_KEY`（商品与短剧智能选音色时使用），用于 `infer_voice_for_product_script`、`infer_voice_for_drama`
- 未配置 Kimi 时，两条管线配音均回退为默认 `male-qn-qingse`

## Guidelines

- BGM 与配音仅在「本地合成成片」且至少 2 段素材时生效；单段视频不会产生 `merged_url`，不会走本管线。
- **商品短视频**与**短剧**在开启配音时都会根据剧本/脚本自动选音色；未配置 `KIMI_API_KEY` 时统一回退为默认男声 `male-qn-qingse`。
- 音色列表以 MiniMax 官方系统音色为准，见 [系统音色列表](https://platform.minimaxi.com/docs/faq/system-voice-id)；新增音色时需同步更新 `VOICE_IDS_ALLOWED` 与 LLM 提示中的可选 ID。
