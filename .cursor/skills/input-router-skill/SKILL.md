---
name: input-router-skill
description: Routes user input (product links, scripts, or natural language) to the correct creation pipeline. Use when the user sends a product URL, a script/drama text, or asks to create short video or short drama content.
---

# 输入路由 Skill（短视频 / 短剧多智能体）

根据用户输入自动判断类型，并引导调用对应创作管线（商品短视频 Skill 或 短剧创作 Skill）。

## 何时触发

- 用户发来**商品链接**（淘宝/京东/拼多多等）
- 用户发来**剧本/分镜/对白**等长文本
- 用户说「做短视频」「做短剧」「写剧本」「种草」「卖货」等创作类需求

## 判断规则（按顺序执行）

### 1. 商品链接

若输入包含 `http://` 或 `https://`，且域名匹配以下任一，则判定为**商品链接** → 调用 **product-video-skill**：

- `item.taobao.com`、`detail.tmall.com`
- `item.jd.com`、`item.m.jd.com`
- `mobile.yangkeduo.com`、`yangkeduo.com`
- 其他电商域名（可在本 Skill 中补充）

**正则参考（可扩展）：**

```
https?://(item\.taobao|detail\.tmall|item\.jd|item\.m\.jd|mobile\.yangkeduo|.*\.yangkeduo)\.(com|cn)/.*
```

### 2. 剧本 / 短剧

若输入为**纯文本**（无上述链接），且满足以下至少两项，则判定为**剧本/短剧** → 调用 **script-drama-skill**：

- 含多角色对白（如「A：」「B：」或明显对话结构）
- 含场景/镜头描述（如「场景：」「镜头一」「内景/外景」）
- 含分镜、转场、时长等关键词
- 段落较多、结构清晰（多段且每段有明确语义）

### 3. 自然语言需求（意图不清）

若以上都不满足，则视为**自然语言需求**：

- 若含「商品」「链接」「种草」「卖」「带货」等 → 引导用户提供商品链接，或说明商品信息后走 **product-video-skill**
- 若含「短剧」「剧本」「分镜」「剧情」等 → 引导用户提供剧本或需求描述，再走 **script-drama-skill**
- 若仍不明确 → 用一句话询问：「您是想做商品种草短视频（请发商品链接），还是做短剧/剧情短视频（请发剧本或需求）？」

## 输出

- 明确类型后，**直接说明**将使用哪条管线，并**调用对应 Skill** 的步骤（或由 Agent 执行对应 Skill 逻辑）。
- 示例：「检测到淘宝商品链接，将使用商品短视频管线为您生成种草脚本与分镜。」

## 配置与扩展

- 新增电商域名：在「商品链接」规则中补充域名或正则。
- 新增剧本特征词：在「剧本/短剧」规则中补充关键词。
- 无需 API Key；本 Skill 仅做规则判断与路由，不调用外部接口。

## Examples

- **输入**：`https://item.taobao.com/item.htm?id=633123456789` → **输出**：判定为商品链接，调用 product-video-skill。
- **输入**：多段对白「A：你怎么来了？ B：我来看看你。」+ 场景描述「内景，咖啡厅」→ **输出**：判定为剧本/短剧，调用 script-drama-skill。
- **输入**：「帮我做一个卖这款手机的短视频」且无链接 → **输出**：自然语言需求，引导用户提供商品链接或说明商品信息后走 product-video-skill。

## Guidelines

- 判断时严格按顺序：先链接、再剧本特征、最后意图关键词；避免把自然语言误判为剧本。
- 新增电商平台时，在「商品链接」规则中补充域名或正则，保持正则可读、可维护。
- 路由结果用一句话明确告知用户将使用哪条管线，再执行对应 Skill 的步骤。
