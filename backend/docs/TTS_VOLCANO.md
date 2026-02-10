# 火山 TTS 接入说明（配音 + 情绪）

## 1. 方案概述

- **音色列表**：当前采用**静态列表**（见 `app/services/volcano_speech.py` 内 `VOLCANO_VOICE_OPTIONS`），从火山文档「大模型音色列表」整理，包含通用、多情感、角色扮演、趣味口音等。若需动态拉取，可使用火山控制台 OpenAPI **ListBigModelTTSTimbres**（需 AK/SK 鉴权，与 TTS 调用的 App ID/Token 非同一套）。
- **情绪匹配**：
  - **按镜自动推断**：对每镜台词做关键词匹配（开心→happy、伤心→sad、生气→angry 等），得到火山 `emotion` 传参。
  - **多情感音色**：当选用「北京小爷（多情感）」「柔美女友（多情感）」等时，请求中会带 `enable_emotion=true` 和 `emotion=xxx`，合成时会有对应情绪，增强代入感。
  - 非多情感音色传入的 `emotion` 会被忽略。

## 2. 配置方式

在 `backend/.env` 中设置：

```env
# 使用火山配音
TTS_ENGINE=volcano

# 火山豆包语音（控制台获取）
VOLCANO_APP_ID=你的APP_ID
VOLCANO_ACCESS_TOKEN=你的Access_Token
```

- **APP ID**、**Access Token** 从火山控制台「应用管理 → 接入详情」获取。
- **Secret Key**：当前大模型 TTS 接口使用 App ID + Access Token 鉴权，暂未使用 Secret Key；若后续接入 ListBigModelTTSTimbres 等 OpenAPI，会用到 AK/SK。

## 3. 接口行为

- **GET /api/voices**：当 `TTS_ENGINE=volcano` 时返回火山音色列表，每项含 `id`（voice_type）、`name`、`language`、`gender`；多情感音色另含 `emotions` 数组（如 `["happy","sad","angry",...]`）。
- **配音流程**：与讯飞一致，按镜或整段调用 `text_to_speech(text, voice_id=..., emotion=...)`；`emotion` 由 `infer_emotion_from_text(台词)` 自动推断，仅对多情感音色生效。

## 4. 情绪关键词与传参

| 关键词示例       | 火山 emotion |
|------------------|--------------|
| 开心、高兴、笑…  | happy        |
| 伤心、难过、哭…  | sad          |
| 生气、怒、烦…    | angry        |
| 惊讶、居然…      | surprised    |
| 害怕、恐惧…      | fear         |
| 激动、兴奋…      | excited      |
| 冷漠、无所谓…    | coldness     |
| 讨厌、厌恶…      | hate         |
| 无匹配           | neutral      |

可在 `volcano_speech.EMOTION_KEYWORDS` 中增删关键词以调整匹配效果；后续也可改为 LLM 对整句做情绪分类以更精准。
