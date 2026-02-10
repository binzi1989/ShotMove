---
name: product-video-skill
description: Fetches product info from e-commerce links (including main product image) and generates short-video scripts or storyboards for product promotion. Use when the user provides a product URL (Taobao, JD, Pinduoduo, etc.) or asks to create product种草 short video. Main product image is required for video consistency.
---

# 商品短视频 Skill（种草短视频管线）

从用户提供的商品链接拉取商品信息（**含主商品图**），生成种草脚本与分镜；可选对接 MiniMax 视频 API 产出视频。**商品主图下载与注入是重点**，用于保持商品视频一致性。

## 实现位置（本项目的真实代码）

| 步骤 | 代码位置 | 说明 |
|------|----------|------|
| 入口 | `backend/app/main.py` → `POST /api/create`，`classify_input` 判定为商品链接后调用 `run_product_video_agent(req.input)` | 统一创作入口 |
| 解析链接 + 拉商品 | `backend/app/agents/product_video.py` → `run_product_video_agent()`；`backend/app/services/product_api.py` → `parse_product_url()`、`fetch_product()`、`dingdanxia_id_query()` | 淘宝无 id 时用订单侠解析 id；fetch_product 内部：第三方 API → 淘宝详情页解析 → Mock |
| 主图/标题/价格 | `product_api.py` → `fetch_third_party_product()`（读 `TAOBAO_PRODUCT_API_URL` + `TAOBAO_PRODUCT_API_KEY` 或 `DINGDANXIA_API_KEY`）；`fetch_taobao_page_info()`（详情页 HTML 解析）；`fetch_amazon_page_info()`（亚马逊）；未命中则 `fetch_product_mock()` | 主图写入返回的 `main_image_url`，Mock 时主图为 picsum 占位 |
| 种草脚本 | `backend/app/services/llm.py` → `generate_script_with_llm()`（需 `KIMI_API_KEY`），否则 `generate_script_with_template()` | 脚本约 30–60 秒可读 |
| 分镜 | `llm.py` → `generate_storyboard_from_script_llm()` 或 `generate_storyboard_from_script_template()` | 分镜含 shot_desc、copy、t2v_prompt 等 |
| 视频生成 | `main.py` 中若 `req.with_video`：`_download_image_to_data_url(main_image_url)` 后调用 `run_video_generation(..., character_reference_image=ref_image, is_product_reference=True)`；`backend/app/agents/video_generation.py` 固定 s2v_multi，prompt 前加 `PRODUCT_CONSISTENCY_PROMPT_PREFIX` | 商品主图必传，保证多镜一致 |

前端：`frontend/src/pages/ProductVideoPage.tsx` 调 `POST /api/create`，展示 `product_summary.main_image_url`、脚本、分镜、视频下载链接。

## 何时触发

- 已由 **input-router-skill** 或后端 `classify_input` 判定为商品链接（见 `backend/app/agents/router.py` 链接正则与 `main.py` 的 `classify_input`）
- 或用户明确说「用这个链接做短视频」「帮这个商品写种草脚本」并附带链接

## 步骤（与实现对应）

### Step 1：解析链接并拉取商品信息（**重点：必须含主图**）

- **解析**：`product_api.parse_product_url(url)` 从 URL 提取 `(platform, product_id)`。淘宝/天猫认 `id=` 或页面内 num_iid；京东认路径数字或 skuId；拼多多认 goods_id；亚马逊认 /dp/ASIN。
- **淘宝无 id 时**：若 `platform == "taobao"` 且无 product_id，调用 `dingdanxia_id_query(user_input)`（需配置 `DINGDANXIA_API_KEY`），用订单侠从链接/淘口令解析出 id。
- **拉商品**：`fetch_product(platform, product_id, original_url)` 逻辑：
  1. 若配置了 `TAOBAO_PRODUCT_API_URL` +（`TAOBAO_PRODUCT_API_KEY` 或 `DINGDANXIA_API_KEY`），先调 `fetch_product_via_third_party_api(product_id)`，返回含 `main_image_url`、title、price。
  2. 淘宝有 id：可 fallback 到 `fetch_taobao_page_info(product_id)`，用规范详情页 URL 拉 HTML，解析主图/标题/价格（无需密钥）。
  3. 亚马逊有 ASIN：`fetch_amazon_page_info(asin)` 拉详情页解析。
  4. 否则 `fetch_product_mock()`，返回示例主图（picsum），`is_mock=True`。
- **主图必写**：上述任一路径得到的 `main_image_url` 写入 `ProductSummary.main_image_url`，供视频生成时下载并作为 S2V 参考图。

### Step 2：生成种草脚本

- 有 `KIMI_API_KEY` 时：`generate_script_with_llm(title, price, highlights)`（见 `llm.py`）。
- 否则：`generate_script_with_template()`。结构：痛点/场景引入 → 卖点 → 价格/促销 → 行动号召；口语化、约 30–60 秒可读。

### Step 3：生成分镜

- 有 LLM 时：`generate_storyboard_from_script_llm(script)`；否则模板。分镜含 index、shot_desc、copy、duration_sec、t2v_prompt 等；画面描述需可直接用于文生视频 Prompt，且含「商品」「主图同款」等以绑定主图。

### Step 4：对接视频生成（可选，**商品视频一致性为重点**）

- 用户勾选「生成视频」时，`main.py` 用 `result.product_summary.main_image_url` 调用 `_download_image_to_data_url()`，将主图作为 `character_reference_image` 传入 `run_video_generation(..., is_product_reference=True)`。
- `video_generation.py` 中有商品参考图时**固定 s2v_multi**，每条 prompt 前加「参考图中的商品在画面中保持外观一致。」（`PRODUCT_CONSISTENCY_PROMPT_PREFIX`）。详见 `.cursor/skills/minimax-video-skill/SKILL.md` 与 `video-character-consistency-skill`。

## 输出格式

- **必选**：商品摘要（标题、价格、卖点、**main_image_url**、is_mock、product_id）+ 种草脚本全文。
- **可选**：分镜表（StoryboardItem 列表）。
- **可选**：视频任务 ID / 下载链接（`req.with_video` 且已配置 `MINIMAX_API_KEY` 时）。

## 配置（本项目实际使用的环境变量）

| 用途 | 变量名 | 说明 |
|------|--------|------|
| 第三方商品 API（推荐） | `TAOBAO_PRODUCT_API_URL`、`TAOBAO_PRODUCT_API_KEY` 或 `DINGDANXIA_API_KEY` | `product_api.fetch_third_party_product()` 读取；未配置则淘宝走详情页解析或 Mock |
| 淘宝无 id 时解析链接/淘口令 | `DINGDANXIA_API_KEY` | `dingdanxia_id_query()` 使用 |
| 种草脚本 + 分镜 LLM | `KIMI_API_KEY` | 未配置则用模板 |
| MiniMax 视频 | `MINIMAX_API_KEY` | 生成视频必配；商品主图会下载后作为 S2V 参考图 |

更多平台（京东/拼多多官方、通义万相等）见项目根目录 `可接入API清单表.md`。

## Examples（真实接口行为）

- **输入**：淘宝商品链接（含 `id=` 或可被订单侠解析）→ 后端 `run_product_video_agent(req.input)` → **输出**：商品摘要（含 main_image_url，若第三方 API 或详情页解析成功）、30–60 秒口播脚本、分镜表；勾选「生成视频」则主图下载后传 S2V，返回下载链接。
- **输入**：京东/拼多多链接 → 当前实现若未配置对应官方 API，会走 Mock（is_mock=True）；接入京东/拼多多 API 后可在 `product_api.py` 扩展 `fetch_product` 分支。

## 商品视频一致性约束（代码已遵守）

- **主图必传**：`ProductSummary.main_image_url` 由 `fetch_product` 各路径填充；视频生成时 `main.py` 用主图作 `character_reference_image`，`video_generation.py` 固定 s2v_multi，不得 T2V 或每镜单独生图。
- **分镜画面描述**：`llm.py` 中分镜生成提示要求画面描述含「商品」「主图同款」等；模板示例含「手持主图同款商品」「主图同款商品特写」。
- **S2V 一致性前缀**：`video_generation.py` 中 `PRODUCT_CONSISTENCY_PROMPT_PREFIX = "参考图中的商品在画面中保持外观一致。"`，有商品参考图时自动加在 prompt 前。

## Guidelines

- 商品拉取失败或未配置 API 时，会返回 Mock 数据（is_mock=True），前端会提示「当前为示例数据，请使用带 id= 的详情页链接或配置第三方商品 API」；不要编造具体商品信息。
- 主图由 `product_api` 各路径（第三方 API / 淘宝详情页 / 亚马逊 / Mock）写入；视频生成阶段 `main.py` 下载主图并传入 `run_video_generation`。
- 密钥仅从环境变量读取（见上表与 `backend/.env.example`），不在代码中写死。
