---
name: minimax-video-skill
description: Uses LLM (Kimi) to recommend the best video generation mode from storyboard and script context (text-to-video, image-to-video, or smart multi-frame), then calls MiniMax to generate clips and return download URLs. Use when the user wants to generate video from script/storyboard.
---

# MiniMax 视频生成 Skill

根据已生成的脚本与分镜，**由 LLM（Kimi）根据分镜与剧本上下文推荐最合适的生成方式**，再在 MiniMax 创建任务、轮询并返回视频下载链接。支持文生视频、智能多帧、先生图再图生视频、主体参考四种方式，不再死板固定一种。

## 何时触发

- 用户勾选「生成视频」或请求「直接出片」
- 上游已产出**分镜表**（商品短视频或短剧创作管线）

## 镜头模式（五种方式）

| 模式 | 说明 | 何时选用 |
|------|------|----------|
| **smart_multiframe** | **智能多帧**：多张关键帧图片 + 每两帧之间的过渡提示词（中间发生了什么），用**首尾帧(FL2V)**串联成一段视频；先为每镜文生一张关键帧图，再对相邻两图调用 FL2V 生成过渡片段，最后拼接 | 多镜、每镜有明确画面且需要连贯过渡时 |
| **t2v_single** | 单段文生视频：一条 prompt，一次 T2V | 分镜只有 1 条时 |
| **t2v_multi** | **多段文生再拼接**：每镜一个 T2V 任务，生成多段视频再拼接 | 多段独立镜头、无关键帧图时 |
| **i2v_multi** | **先生图再图生视频**：每镜先文生图再图生视频 | **画面更可控、构图更精细**，适合对画面质量要求高、场景描述具体的分镜 |
| **s2v_multi** | **主体参考**多帧：同一人物/商品参考图 + 每镜不同 prompt | **需人物/商品一致性**时（短剧角色、商品主图） |

## 模式选择逻辑（LLM 推荐 + 规则兜底）

1. **有商品主图或人物参考图**：**固定 s2v_multi**（主体参考），不交给 LLM 选。
2. **无参考图且已配置 Kimi**：**由 Kimi 根据分镜与剧本摘要推荐**  
   - 输入：分镜列表（画面描述 + 文案）、剧本/脚本摘要、管线类型（script_drama / product_video）  
   - 输出：在 **smart_multiframe / t2v_single / t2v_multi / i2v_multi** 中选一个 + 一句话理由  
   - 例如：多镜有明确画面、需要连贯过渡 → 推荐 smart_multiframe（智能多帧：多张关键帧图+过渡提示词串联）；场景描述具体、需精细构图 → 推荐 i2v_multi。
3. **无参考图且未配置 Kimi 或 LLM 调用失败**：**规则兜底**  
   - 分镜≤1 → t2v_single  
   - 显式偏好图生视频 → i2v_multi  
   - 否则 → t2v_multi  

## 步骤

1. **输入**：分镜表（`storyboard`）+ 脚本摘要（`script_summary`）+ 管线（`pipeline`：product_video / script_drama）+ 可选商品主图 / 演员参考图。
2. **选模式**：有参考图 → s2v_multi；否则调用 `suggest_video_mode_llm(storyboard, script_summary, pipeline)` 得到推荐模式与理由；失败则 `choose_video_mode(...)` 规则兜底。
3. **智能多帧(smart_multiframe)**：为每镜文生一张关键帧图 → 对相邻两图调用 MiniMax **首尾帧(FL2V)**，prompt 为两帧之间的过渡描述 → 拼接 (N-1) 段视频。
4. **文生视频(T2V)**：t2v_single 单镜一条 prompt；t2v_multi 每镜一个 T2V 任务再拼接。
5. **图生视频(I2V)**：每镜先文生图得到首帧，再图生视频；画面更可控。
6. **主体参考(S2V)**：同一参考图 + 每镜不同 prompt，保证人物/商品一致。
7. **轮询与输出**：`video_mode`、`video_mode_reason`（LLM 理由）、`download_urls`、`merged_download_url` 等。

## 配置

- **MINIMAX_API_KEY**（必填）：MiniMax 视频/图片接口。
- **KIMI_API_KEY**（可选）：用于推荐视频生成方式；未配置时使用规则兜底。

## Guidelines

- **智能多帧**：多张关键帧图片 + 每两帧之间的过渡提示词，用首尾帧(FL2V)串联；不是「一条长 prompt 一次 T2V」。
- **不要死板只用文生视频**：短剧/分镜可根据内容选用智能多帧（smart_multiframe）、多段文生再拼接（t2v_multi）或先生图再图生（i2v_multi），由 LLM 根据分镜与剧本上下文推荐。
- **商品/人物一致性**：有商品主图或人物参考图时固定 s2v_multi；详见 **product-video-skill** 与 **video-character-consistency-skill**。
- 返回中可带 `video_mode_reason`，便于前端展示「本次选用该模式的理由」。
