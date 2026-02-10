# 短视频 + 短剧 多智能体 · Skills 综合说明

本目录包含三个 **Agent Skill**，用于「商品链接 → 种草短视频」与「剧本 → 短剧」两条创作管线的路由与执行。格式与 [Anthropic 的 Skills 仓库](https://github.com/anthropics/skills) 及 [Agent Skills 标准](https://agentskills.io) 对齐，可在 **Cursor** 中使用，也可按同一结构在 **Claude Code / Claude.ai / Claude API** 中复用。

---

## 与 Anthropic skills 的对应关系

| 来源 | 说明 |
|------|------|
| **[anthropics/skills](https://github.com/anthropics/skills)** | Anthropic 官方 Skills 示例与模板：每个 Skill 为「文件夹 + SKILL.md」，含 YAML frontmatter（`name`、`description`）与 Instructions/Examples/Guidelines。 |
| **[agentskills.io](https://agentskills.io)** | Agent Skills 标准说明，与本仓库 Skill 结构兼容。 |
| **本目录** | 三个自定义 Skill：`input-router-skill`、`product-video-skill`、`script-drama-skill`，采用相同 SKILL.md 结构，便于 Cursor 发现与 Claude 复用。 |

**格式约定（与 Anthropic 一致）：**

- **Frontmatter**：`name`（小写、连字符）、`description`（一句话说明能力 + 何时使用）。
- **正文**：Instructions（何时触发、步骤/规则）、Examples（示例用法）、Guidelines（注意与扩展）。

---

## 本目录下的三个 Skill

| Skill | 作用 | 何时由 Agent 使用 |
|-------|------|-------------------|
| **input-router-skill** | 根据用户输入（链接/剧本/自然语言）判断类型，路由到对应管线 | 用户发商品链接、剧本或说「做短视频/短剧」时 |
| **product-video-skill** | 解析商品链接、拉取商品信息（**含主图 main_image_url**）、生成种草脚本与分镜；可选对接 MiniMax 视频。**重点：主图下载与注入，保证商品视频一致性** | 已判定为商品链接或用户明确要求「用链接做种草视频」时 |
| **script-drama-skill** | 理解剧本/分镜，输出结构化分镜与视频 API 可用 Prompt；可选对接 MiniMax 视频 | 已判定为剧本/短剧或用户明确要求「按剧本做短剧」时 |
| **minimax-video-skill** | 根据分镜自主选择文生视频/图生视频/智能多帧/主体参考(S2V)，在 MiniMax 生成视频并返回下载链接；支持**商品主图**作参考图 | 用户勾选「生成视频」或请求「直接出片」且上游已产出分镜时 |
| **video-character-consistency-skill** | **保证视频人物/角色/商品一致性**：短剧同一主角、**商品管线同一商品**时，使用主体参考(S2V)或共用首帧。**重点：商品主图下载并作参考图** | 用户要求「角色一致」「同一人」、商品链接成片或短剧/多镜成片需人物一致时 |
| **audio-voice-skill** | 成片添加 **BGM**（MiniMax 音乐）与 **TTS 配音**（MiniMax 语音）；**短剧**根据剧本智能理解角色性别与气质，自动选择配音音色（男声/女声、青年/御姐等） | 用户勾选「添加 BGM」或「添加配音」且成片已本地合成时 |
| **video-prompt-quality-skill** | **文生视频/图生视频 Prompt 质量**：五段式（主体+场景+动作+风格+镜头语言）规范；后端在分镜生成后自动用 LLM 精修 t2v_prompt，提升成片画面质量 | 编写/修改分镜的 t2v_prompt、或成片画面质量不佳需优化提示词时 |

**调用顺序建议：** 先由 **input-router-skill** 判断输入类型，再调用 **product-video-skill** 或 **script-drama-skill**；生成视频时 **product-video-skill** 必须注入主图（main_image_url），**video-character-consistency-skill** 约定商品主图下载与参考图用法。API 与密钥见项目根目录 `可接入API清单表.md`。

---

## 在 Cursor 中使用

- 本目录位于 **项目级** `.cursor/skills/`，Cursor 会自动加载这些 Skill。
- 在对话中直接描述需求即可，例如：
  - 「用这个淘宝链接做一个种草短视频脚本」
  - 「根据下面这段剧本生成短剧分镜」
- Agent 会根据 **input-router-skill** 的规则判断类型，并应用对应 Skill 的步骤。

---

## 在 Claude Code / Claude.ai / Claude API 中复用

1. **方式一（推荐）**：将本目录下三个 Skill 文件夹（含 SKILL.md）复制到你的 Claude 可加载的 Skills 目录，或打包成符合 [anthropics/skills](https://github.com/anthropics/skills) 结构的仓库。
2. **Claude Code**：可将本仓库注册为 Plugin 或把 skills 放到 Plugin 的 skills 目录，使用方式见 [Try in Claude Code](https://github.com/anthropics/skills#try-in-claude-code-claudeai-and-the-api)。
3. **Claude API**：按 [Skills API Quickstart](https://docs.claude.com/en/api/skills-guide#creating-a-skill) 上传或引用相同结构的 Skill。

无需改 SKILL.md 内容即可复用；仅需在运行环境中配置好 **API 密钥**（见 `可接入API清单表.md`）。

---

## 参考链接

- [Anthropic Skills 仓库](https://github.com/anthropics/skills) — 示例与模板
- [Agent Skills 标准](https://agentskills.io)
- [How to create custom skills (Claude)](https://support.claude.com/en/articles/12512198-creating-custom-skills)
- 本项目 API 清单：根目录 `可接入API清单表.md`、规划文档 `短视频短剧多智能体创作规划.md`
