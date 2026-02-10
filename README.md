# 短视频 + 短剧 · 多智能体创作

根据用户输入（**商品链接** / **剧本** / 自然语言）自动路由到「商品种草短视频」或「短剧创作」管线，生成脚本与分镜。包含**后端服务**、**前端界面**与 **Cursor Skills**。

---

## 项目结构

```
d:\test\
├── backend/                 # FastAPI 多 Agent 服务
│   ├── app/
│   │   ├── main.py          # 入口、/api/create
│   │   ├── schemas.py       # 请求/响应模型
│   │   ├── agents/          # 路由 + 商品短视频 + 短剧 Agent
│   │   └── services/        # 商品 API、LLM 模板
│   ├── requirements.txt
│   └── .env.example
├── frontend/                 # React + Vite + Tailwind 前端
│   ├── src/
│   │   ├── App.tsx
│   │   ├── api.ts
│   │   └── ...
│   ├── package.json
│   └── vite.config.ts       # 开发时代理 /api -> 后端
├── .cursor/skills/           # Cursor/Agent Skills（路由、商品、短剧）
├── 可接入API清单表.md
├── 短视频短剧多智能体创作规划.md
└── README.md
```

---

## 快速运行

### 1. 后端（必须先启动）

**方式一：用 run 文件（推荐）**

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate    # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
python run.py
```

或在 Windows 下双击 `backend/run.bat`（需已安装 Python 且已执行过 `pip install -r requirements.txt`）。

**方式二：命令行 uvicorn**

```bash
cd backend
# 激活虚拟环境并安装依赖后
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- 接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

### 2. 前端

```bash
cd frontend
npm install
npm run dev
```

- 打开 <http://localhost:5173>，在输入框粘贴**商品链接**或**剧本**，点击「生成」即可。

### 3. 环境变量（Kimi + MiniMax）

- **Kimi**：种草脚本与短剧分镜；Key 写在 `backend/.env` 的 `KIMI_API_KEY`（未配置时用模板生成）。
- **MiniMax**：前端勾选「同时生成视频」时，根据分镜在 MiniMax 生成视频（文生视频/图生视频/智能多帧），Key 为 `MINIMAX_API_KEY`；未配置时仅返回脚本与分镜，不调用视频 API。
- 复制 `backend/.env.example` 为 `backend/.env` 并按需填写；`.env` 已加入 `.gitignore`。
- 商品 API、其他 LLM 见 `可接入API清单表.md`。

### 4. 可选：Docker 仅跑后端

```bash
cp backend/.env.example backend/.env   # 可选
docker compose up -d backend
# 前端仍在本机：cd frontend && npm run dev，会代理 /api 到 127.0.0.1:8000
```

---

## API 说明

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务信息 |
| GET | `/health` | 健康检查 |
| POST | `/api/create` | 创作入口，Body: `{ "input": "用户输入" }` |

**响应**：`input_type`（商品链接/剧本/自然语言）、`pipeline`（商品短视频/短剧/澄清）、`result`（商品摘要+脚本+分镜 或 分镜+Prompt 或 引导文案）。

---

## 多 Agent 协作流程

1. **路由 Agent**：根据输入判断类型（链接正则 + 剧本特征 + 意图关键词）。
2. **商品短视频 Agent**：解析链接 → 拉取商品信息（Mock 或真实 API）→ 生成种草脚本与分镜。
3. **短剧创作 Agent**：解析剧本 → 生成分镜表与文生视频用 Prompt 列表。
4. **需求澄清**：意图不清时返回引导文案，提示用户提供链接或剧本。
5. **视频生成（可选）**：用户勾选「生成视频」且已配置 `MINIMAX_API_KEY` 时，根据分镜自动选择文生视频(T2V)/图生视频(I2V)/智能多帧，在 MiniMax 生成视频并返回下载链接；多段成片可后续用 ffmpeg 做自动剪辑合并。

---

## 扩展与接入

- **商品 API**：在 `backend/app/services/product_api.py` 的 `fetch_product` 中接入淘宝/京东/拼多多等，密钥见 `可接入API清单表.md`。
- **LLM 生成脚本/分镜**：在 `backend/app/services/llm.py` 中增加真实 API 调用（OpenAI 兼容或通义），未配置时继续使用模板。
- **文生视频/短剧 API**：在 Agent 中增加对通义万相等接口的调用，见规划文档与 API 清单表。

---

## 参考文档

- [短视频短剧多智能体创作规划.md](短视频短剧多智能体创作规划.md)
- [可接入API清单表.md](可接入API清单表.md)
- [.cursor/skills/README.md](.cursor/skills/README.md)（Skills 与 Anthropic/agentskills.io 对齐说明）
