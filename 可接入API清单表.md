# 短视频 + 短剧 多智能体 · 可接入 API 清单表

> 按「商品侧」「视频/短剧侧」分类，含平台、接口类型、文档链接与密钥说明。接入时在此表勾选并填写实际 API Key / AppKey。

---

## 一、商品侧 API（链接 → 商品信息）

用于「用户扔商品链接」时解析 URL、拉取标题/价格/主图/卖点等，供种草脚本与视频生成使用。

| 平台 | 接口类型 | 主要能力 | 文档链接 | 认证/密钥 | 备注 |
|------|----------|----------|----------|-----------|------|
| **淘宝开放平台** | 商品详情 | `taobao.item.get`，num_iid 取详情；支持价格、主图、详情 HTML | [淘宝商品详情 API 说明](https://developer.aliyun.com/article/1652442) · [使用指南](https://developer.aliyun.com/article/1687805) | AppKey + AppSecret，需企业认证申请权限 | 链接中 id 即 num_iid |
| **京东宙斯开放平台** | 商品详情 / 价格 / 库存 | `jingdong.ware.product.detail.search.get` 等；商品详情、实时价格、库存 | [京东开放平台文档](https://opendoc.jd.com/isp_all/api/) · [商品 API 集成指南](https://juejin.cn/post/7572776177690869769) | OAuth 2.0，app_key + app_secret → Access Token | 需签名，Token 约 30 天刷新 |
| **拼多多开放平台** | 商品详情 | 商品信息、价格、主图等 | [拼多多开放平台](https://open.pinduoduo.com/)（需登录查看具体接口文档） | 申请应用获取 client_id / client_secret | 链接解析见平台文档 |
| **阿里云市场 · 电商数据** | 淘宝/京东等聚合 | 第三方封装的多平台商品详情、链接解析 | [阿里云市场电商 API](https://www.aliyun.com/sswb/1567341) 等 | 按产品购买/订阅，提供 AppCode 或 Key | 可减少多平台对接成本 |
| **订单侠等第三方** | 多平台商品 / 链接解析 | 京东/淘宝等链接解析、商品详情聚合 | [订单侠 API 测试](https://www.dingdanxia.com/apitest/121/94) | 注册获取 API Key | 适合快速验证，注意合规与限流 |

**链接解析约定（自用可记）：**

- 淘宝/天猫：`item.taobao.com/item.htm?id=*` 或 `detail.tmall.com` → 取 `id` 即 num_iid。
- 京东：`item.jd.com/*.html` 或 `item.m.jd.com` → 取 skuId/商品 ID（见京东文档）。
- 拼多多：`mobile.yangkeduo.com` 等 → 商品 ID 见拼多多开放平台文档。

---

## 二、视频 / 短剧侧 API（脚本/分镜 → 视频）

用于「种草脚本」或「短剧分镜」生成实际视频片段或成片。优先选用云厂商已封装能力。

### 2.1 文生视频（文案/分镜描述 → 短视频）

| 平台 | 接口类型 | 主要能力 | 文档链接 | 认证/密钥 | 备注 |
|------|----------|----------|----------|-----------|------|
| **阿里云 · 通义万相** | 文生视频 | wan2.6-t2v：2–15 秒、720P/1080P、有声、多镜头叙事；wan2.5 支持 5/10 秒、自动配音 | [文生视频 API 参考](https://help.aliyun.com/zh/model-studio/text-to-video-api-reference) · [Prompt 指南](https://help.aliyun.com/zh/model-studio/text-to-video-prompt) | DashScope API Key（控制台创建），请求头 `Authorization: Bearer $DASHSCOPE_API_KEY` | 异步任务：创建任务 → 轮询 task_id；地域分北京/新加坡/弗吉尼亚 |
| **可灵 / 即梦 / Runway 等** | 文生视频 / 图生视频 | 各家长度、分辨率、口型等能力不同 | 各厂商开放平台文档（可灵、即梦、Runway 官网） | 各平台 API Key | 可按成本与效果选一家或组合 |

### 2.2 MiniMax 视频（文生视频 / 图生视频 / 智能多帧，本项目已接入）

| 平台 | 接口类型 | 主要能力 | 文档链接 | 认证/密钥 | 备注 |
|------|----------|----------|----------|-----------|------|
| **MiniMax** | 文生视频(T2V) | MiniMax-Hailuo-2.3：文生视频，支持运镜指令 [推进][拉远] 等 | [文生视频](https://platform.minimaxi.com/docs/api-reference/video-generation-t2v) | Bearer `MINIMAX_API_KEY` | 创建任务→查询状态→文件下载；本项目 Agent 自动选模式 |
| **MiniMax** | 图生视频(I2V) | MiniMax-Hailuo-2.3-Fast：首帧图+文本生成视频 | [图生视频](https://platform.minimaxi.com/docs/api-reference/video-generation-i2v) | 同上 | 首帧可用 MiniMax 文生图接口 |
| **MiniMax** | 文生图 | image-01：分镜首帧图，供 I2V 使用 | [文生图](https://platform.minimaxi.com/docs/api-reference/image-generation-t2i) | 同上 | POST /v1/image_generation，返回 image_urls |
| **MiniMax** | **主体参考视频(S2V)** | **人物一致性**：S2V-01，同一人物参考图 + prompt 生成视频；多镜时每镜一任务、同一 subject_reference | [主体参考生成视频](https://platform.minimaxi.com/docs/api-reference/video-generation-s2v) | 同上 | model=S2V-01，subject_reference=[{type:"character",image:[url]}]；见 video-character-consistency-skill |
| **MiniMax** | **音乐生成(BGM)** | music-2.5：根据 prompt/lyrics 生成 BGM，成片可选添加背景音乐 | [音乐生成](https://platform.minimaxi.com/docs/api-reference/music-generation) | 同上 | 本项目已接入：成片勾选「添加 BGM」时调用 |
| **MiniMax** | **同步语音合成(T2A)** | speech-2.6-turbo 等：脚本/旁白转配音，成片可选添加旁白 | [同步语音合成 HTTP](https://platform.minimaxi.com/docs/api-reference/speech-t2a-http) | 同上 | 本项目已接入：成片勾选「添加配音」时调用 |

### 2.3 参考生视频 / 图生视频（角色一致、短剧多镜头）

| 平台 | 接口类型 | 主要能力 | 文档链接 | 认证/密钥 | 备注 |
|------|----------|----------|----------|-----------|------|
| **阿里云 · 通义万相** | 参考生视频 | wan2.6-r2v：参考图/视频角色一致、多镜头、2–10 秒、有声；支持单/多角色 | [参考生视频 API 参考](https://help.aliyun.com/zh/model-studio/wan-video-to-video-api-reference) | 同上 DashScope API Key | 异步；提示词可引用 character1/character2；shot_type=multi 多镜头 |

### 2.4 数字人 / 口播（种草口播、旁白）

| 平台 | 接口类型 | 主要能力 | 文档链接 | 认证/密钥 | 备注 |
|------|----------|----------|----------|-----------|------|
| **有言 AI / 白日梦 AI 等** | 数字人 / 口型 | 文案 → 数字人口播视频，无需真人出镜 | 各厂商开放平台或官网文档 | 各平台 API Key | 适合种草、讲解类短视频 |
| **阿里云 / 腾讯云 数字人** | 2D/3D 数字人 | 文本驱动口型、表情、可定制形象 | 阿里云/腾讯云「数字人」产品页与 API 文档 | 云账号 + 对应产品 API Key/Secret | 企业级稳定，按量计费 |

### 2.5 短剧一站式（剧本/分镜 → 成片）

| 平台 | 接口类型 | 主要能力 | 文档链接 | 认证/密钥 | 备注 |
|------|----------|----------|----------|-----------|------|
| **阿里云 · 短剧漫剧场景** | 剧本 → 分镜 → 视频 | 剧本分镜、视频生成等一站式短剧能力 | [短剧漫剧场景 - 阿里云](https://www.aliyun.com/benefit/scene/playlet-h5) | 阿里云账号 + 产品开通与 API 配置 | 适合「剧本直接出片」的管线 |

---

## 三、密钥与配置填写处（接入时填写）

以下为占位，实际密钥请写在环境变量或项目配置中，**不要提交到仓库**。

| 用途 | 变量名建议 | 说明 |
|------|------------|------|
| **Kimi（Moonshot）LLM** | `KIMI_API_KEY` | 本项目已接入，用于生成种草脚本与短剧分镜；官方 https://api.moonshot.cn |
| **MiniMax 视频+图片** | `MINIMAX_API_KEY` | 本项目已接入：文生视频/图生视频/智能多帧；官方 https://platform.minimaxi.com |
| 通义万相 / DashScope | `DASHSCOPE_API_KEY` | 阿里云百炼/模型服务控制台创建 |
| **淘宝/天猫商品详情（推荐）** | `TAOBAO_PRODUCT_API_URL`, `TAOBAO_PRODUCT_API_KEY` 或 `DINGDANXIA_API_KEY` | 按商品 id 返回标题、价格、主图，保证商品图/价格与目标一致；未配置时尝试解析详情页，失败则用 Mock |
| 淘宝开放平台 | `TAOBAO_APP_KEY`, `TAOBAO_APP_SECRET` | 淘宝开放平台应用 |
| 京东开放平台 | `JD_APP_KEY`, `JD_APP_SECRET` | 京东宙斯应用，OAuth 得到 access_token |
| 拼多多 | `PDD_CLIENT_ID`, `PDD_CLIENT_SECRET` | 拼多多开放平台应用 |
| 第三方聚合（如订单侠） | `DINGDANXIA_API_KEY` 等 | 与 TAOBAO_PRODUCT_API_URL 配合，填 Key 即可 |
| 其他视频/数字人 | `KELING_API_KEY`、`RUNWAY_API_KEY` 等 | 按各平台文档命名 |

---

## 四、接入优先级建议

1. **先打通一条管线**：例如「淘宝链接 + 通义万相文生视频」或「京东链接 + 通义万相」。
2. **商品侧**：若只做 demo，可先用一家（淘宝或京东）+ 一个第三方聚合；正式多平台再补京东/拼多多官方。
3. **视频侧**：优先用 **通义万相**（文生 + 参考生视频）覆盖「种草短视频」和「短剧多镜头」；数字人/口播按需再加。
4. **短剧一站式**：若希望「剧本直接成片」，优先看阿里云短剧漫剧场景的 API 与计费。

---

*表格中的文档链接均为当前可访问的官方或常见文档，接入前请以各平台最新文档为准。*
