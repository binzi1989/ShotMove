---
name: video-character-consistency-skill
description: Ensures character/subject consistency across video shots in short drama and product videos. Use when generating multi-shot video where the same person, character, or product must look the same in every shot — critical for short drama, branded storytelling, and product种草 videos.
---

# 视频人物/主体一致性 Skill

在多镜头短视频、短剧中，**人物/角色/商品一致性**非常重要：同一角色或同一商品在不同镜头中应保持同一外观，否则观感断裂。本 Skill 约定如何通过 API 与管线设计保证**人物一致性**与**商品视频一致性**（后者为重点之一）。

## 何时触发

- 用户要求「保持角色一致」「人物别变脸」「同一主角」
- **商品链接管线（product_video）**：多镜种草视频中**商品必须一致**，需下载主商品图并作为参考图（**重点**）
- 短剧创作管线（script_drama）：多场多镜，同一角色多次出现
- 商品口播/数字人：同一出镜人贯穿全片
- 任何多镜头成片且需「同一人」或「同一商品」的场景

---

## 商品视频一致性（重点）

**商品链接管线**下，生成视频时必须保证**商品在每一镜中外观一致**，否则会像换了商品。

1. **主图注入**：**product-video-skill** 拉取商品信息时**必须**拿到**主商品图 URL**（`main_image_url`），并写入商品摘要。见 `.cursor/skills/product-video-skill/SKILL.md`。
2. **下载主图**：视频生成前，后端应**下载主图**（避免外链失效或 MiniMax 无法访问），转为 data URL 或公网可访问 URL。
3. **作为参考图**：将主图作为**主体参考图**传入 MiniMax S2V-01（或 I2V 首帧），多镜时**同一张主图**、不同 prompt，保证商品一致。
4. **API 约定**：`VideoRequest` 支持 `product_main_image_url`；若有则优先使用（下载后）作为参考图，再考虑演员参考图。

## 实现方式（按优先级）

### 1. MiniMax 主体参考生成视频（S2V-01）— 推荐

MiniMax 提供 **主体参考生成视频** 接口，同一张人物参考图可保证生成视频中人物一致。

- **接口**：`POST /v1/video_generation`，**model** 使用 `S2V-01`
- **参数**：`subject_reference`: `[{ "type": "character", "image": ["人物参考图 URL"] }]`（目前仅支持单个主体）
- **用法**：多镜头时，**每条分镜各创建一次任务**，每次请求使用**同一张** `subject_reference` 图片 + 该镜头的 `prompt`（动作/场景描述），即可保证所有镜头中人物一致。
- **参考图来源**：用户上传；或由文生图生成一张「角色定妆图」后，全程复用该图 URL。
- **文档**：[创建主体参考生成视频任务](https://platform.minimaxi.com/docs/api-reference/video-generation-s2v)

### 2. 图生视频（I2V）共用首帧

若暂不使用 S2V，可用 **图生视频（I2V）** 实现一致性：

- **做法**：先生成或指定**一张**「角色/人物」图作为首帧；**所有分镜**的 I2V 任务都使用**这一张图**作为 `first_frame_image`，仅 `prompt` 按镜头描述动作与场景。
- **注意**：当前管线若「每镜一图」由文生图按 `shot_desc` 生成，则每镜人物会不同；要一致则必须改为「全片共用一张角色首帧」。

### 3. 通义万相 参考生视频（wan2.6-r2v）

- **能力**：参考图/视频**角色一致**、多镜头、支持 character1/character2 等多角色。
- **用法**：分镜/Prompt 中标注角色与参考图对应关系（如 character1、character2），同一角色绑定同一参考图。
- **文档与密钥**：见项目 `可接入API清单表.md` — 2.3 参考生视频。

## 管线约定（与 minimax-video-skill / script-drama-skill / product-video-skill 配合）

1. **商品管线（product-video-skill）— 重点**  
   - 拉取商品信息时**必须**返回 **main_image_url**（主商品图）。  
   - 生成视频时**必须**传入该主图（后端下载后作为参考图），保证**商品视频一致性**。

2. **分镜阶段**（script-drama-skill / product-video-skill）  
   - 若后续要做「人物一致」视频，分镜中应标明**主角/角色**（如「主角 A」「旁白出镜人」），或输出「角色定妆描述」供生成一张参考图。

3. **视频生成阶段**（minimax-video-skill）  
   - 若为**商品管线**且提供 `product_main_image_url`：**优先**下载主图并作为参考图，使用 S2V-01 或 I2V 保证商品一致。  
   - 若启用**人物一致性**：  
     - **优先**：使用 MiniMax **S2V-01**，传入同一张人物参考图，每镜一个任务、同一 `subject_reference`、不同 `prompt`。  
     - **备选**：I2V 模式下，仅生成一张角色首帧，所有镜头共用该首帧。  
   - 未启用一致性时，保持现有 T2V/每镜一图 I2V 行为即可。

4. **参考图从哪来**  
   - **商品**：商品详情 API 返回的**主图 URL**，后端下载后传入。  
   - **人物**：用户上传一张「人物参考图」并传入管线；或由 LLM 生成「角色定妆」文本描述，调用文生图生成一张图，将该图 URL 作为全片人物参考。

## 配置

- **MiniMax**：`MINIMAX_API_KEY`（S2V-01 与 T2V/I2V 共用同一密钥）。
- **通义万相 参考生视频**：`DASHSCOPE_API_KEY`，见 `可接入API清单表.md`。

## 商品视频一致性约束（必须遵守）

- **仅用 S2V**：商品管线下，只要有 `product_main_image_url`，**必须**使用主体参考生成（S2V-01），不得使用纯 T2V 或「每镜单独文生图再 I2V」（会导致每镜商品外观不同）。
- **同一主图**：多镜中**同一张**商品主图作为 `subject_reference`，仅 prompt 按镜头变化（场景/动作），不得每镜换图或重新生图。
- **Prompt 强化**：每条 S2V prompt 前加「参考图中的商品在画面中保持外观一致。」（代码已实现）；分镜/脚本中的画面描述须与「主图商品」一致，避免描述成其他物品。

## Guidelines

- **商品视频一致性**：商品管线下主图拉取与注入是**重点**；视频生成时必须下载主图并作为参考图，多镜同一商品 = 同一主图参考。
- 人物一致性是短剧与品牌短视频的**核心体验**，实现时应优先选用支持「主体参考/角色参考」的 API（如 MiniMax S2V-01、通义 wan2.6-r2v）。
- 多镜头时：**同一角色/同一商品 = 同一参考图/同一 subject_reference**，不要在每镜重新生成不同的人脸或商品图。
- 若使用「角色定妆」文生图，建议只生成一次并缓存 URL，全片复用。
