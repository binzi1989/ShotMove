# 会员体系与积分体系设计

## 一、会员体系结构

### 1.1 档位定义

| 档位 code   | 名称   | 等级 level | 每日任务配额 | 分镜镜头上限 | 可导出成片 | 月费(积分) | 说明           |
|------------|--------|------------|-------------|-------------|-----------|-----------|----------------|
| free       | 免费   | 0          | 3           | 5           | 否        | 0         | 体验基础能力   |
| basic      | 基础   | 1          | 10          | 10          | 是        | 100       | 日常轻度创作   |
| premium    | 专业   | 2          | 30          | 20          | 是        | 300       | 高频创作与导出 |
| vip        | 尊享   | 3          | 不限        | 不限        | 是        | 800       | 无限制+优先队列 |

- **每日任务配额**：当日可发起「生成脚本/分镜」或「生成视频」的次数（按 pipeline 合计）。
- **分镜镜头上限**：单次创作允许的最大分镜条数，超出可截断或拒绝。
- **可导出成片**：是否允许使用「自动剪辑成一片」并下载合并视频；free 仅可下载单段。
- **月费(积分)**：用积分兑换该档位 1 个月时长的价格（见积分体系）。

### 1.2 数据模型

- **membership_tiers**（档位配置表，可后台配置或迁移写入）
  - `id` TEXT PK
  - `code` TEXT UNIQUE  // free, basic, premium, vip
  - `name` TEXT
  - `level` INTEGER
  - `daily_task_quota` INTEGER  // -1 表示不限
  - `max_storyboard_shots` INTEGER  // -1 表示不限
  - `can_export_merged_video` INTEGER 0/1
  - `price_per_month_credits` INTEGER
  - `description` TEXT
  - `created_at`, `updated_at`

- **users**（用户表，支持先匿名后绑定）
  - `id` TEXT PK (uuid)
  - `device_id` TEXT UNIQUE nullable  // 匿名用户用设备标识
  - `created_at`, `updated_at`
  - 后续可扩展：phone, open_id 等

- **user_memberships**（用户当前/历史会员记录）
  - `id` TEXT PK
  - `user_id` TEXT FK users.id
  - `tier_code` TEXT  // 关联 membership_tiers.code
  - `started_at` TEXT ISO
  - `expires_at` TEXT ISO  // 过期后视为降级到 free
  - `is_active` INTEGER 0/1  // 当前生效的一条为 1
  - `created_at`, `updated_at`

**业务规则**：
- 无任何会员记录或已过期：按 `free` 档位权益计算。
- 同一用户同一时刻仅一条 `is_active=1`；续费/升级时旧记录 `is_active=0`，新记录写入并 `is_active=1`。
- 权益判断：取当前生效会员的 `tier_code`，再查 `membership_tiers` 得到配额与权限。

---

## 二、积分体系结构

### 2.1 积分用途

- **获取（增加）**：签到、完成任务、分享、活动、管理员调整。
- **消耗（减少）**：兑换会员时长、兑换道具/额度（可扩展）、违规扣减。

### 2.2 数据模型

- **user_points**（用户积分余额）
  - `user_id` TEXT PK FK users.id
  - `balance` INTEGER  // 当前余额，可为负（仅在有风控时允许）
  - `updated_at` TEXT

- **point_transactions**（积分流水）
  - `id` TEXT PK
  - `user_id` TEXT FK users.id
  - `amount` INTEGER  // 正为增加，负为减少
  - `type` TEXT  // 见下表
  - `ref_id` TEXT nullable  // 关联业务 id，如 task_id、membership_id
  - `description` TEXT nullable
  - `created_at` TEXT

**流水类型 type**

| type                | 说明         | amount | ref_id 示例   |
|---------------------|-------------|--------|--------------|
| sign_in             | 每日签到     | +10    | -            |
| task_content        | 完成内容生成 | +5     | task_id      |
| task_video          | 完成视频生成 | +15    | task_id      |
| share               | 分享成功     | +8     | -            |
| redeem_membership    | 兑换会员     | 负值   | user_membership_id |
| admin_adjust        | 管理员调整   | +/-    | -            |

### 2.3 规则约定

- 所有变动必须写流水，再更新 `user_points.balance`（同一事务）。
- 扣减时校验 `balance >= 扣减值`，不足可拒绝并返回提示。
- 积分有效期：默认长期有效；若需「年底清零」等，可在流水或余额表增加 `expires_at` 或按规则在定时任务中处理。

---

## 三、与现有创作的联动

1. **任务配额**：在「生成内容」或「生成视频」前，根据当前用户会员档位检查当日已用次数，超过 `daily_task_quota` 则拒绝并提示升级或明日再试。
2. **分镜条数**：生成分镜后若条数 > `max_storyboard_shots`，后端截断或前端仅展示前 N 条并提示会员上限。
3. **成片导出**：`can_export_merged_video=0` 时，不返回合并视频链接或接口直接 403，前端隐藏「下载成片」。
4. **积分发放**：在「内容生成成功」「视频生成成功」的回调或接口内，调用积分服务增加对应流水（如 task_content / task_video）。

---

## 四、API 设计概要（已实现）

**用户标识**：所有 `/api/me/*` 接口需在请求头中携带 `X-Device-ID`（设备/匿名用户标识），无则返回 400。后端按 device_id 自动创建或关联用户。

- `GET /api/me/profile`  
  返回当前用户摘要：user_id、当前档位（membership）、今日已用配额（usage_today）、积分余额（points）。

- `GET /api/me/membership`  
  返回当前生效会员摘要：tier_code, tier_name, level, daily_task_quota, max_storyboard_shots, can_export_merged_video, expires_at。

- `GET /api/membership/tiers`  
  公开接口，返回所有档位列表（用于前端展示升级选项）。

- `GET /api/me/points`  
  返回当前用户积分余额（user_id, balance）。

- `GET /api/me/points/history?limit=50&offset=0`  
  分页积分流水。

- `POST /api/me/points/sign-in`  
  每日签到（每日一次），成功则增加 10 积分并返回本次获得积分与签到后余额；今日已签到则 400。

- `POST /api/me/membership/redeem`  
  用积分兑换会员：body `{ "tier_code": "basic", "months": 1 }`，扣积分、写 user_memberships、写扣减流水；免费档位或积分不足返回 400。

- 创作相关接口（如 `/api/content`, `/api/video`）可在后续迭代中增加配额校验：请求头传 `X-Device-ID`，后端调用 `check_can_use_quota`、`increment_daily_usage` 并校验 `can_export_merged_video` 等。

---

## 五、前端展示建议

- 顶部或个人中心展示：当前档位徽章 + 积分余额。
- 创作页：若接近或已达当日配额，提示「今日次数已用尽，升级会员可增加配额」。
- 设置/会员页：档位对比表、当前权益、积分流水、签到按钮、兑换会员入口。

以上为会员体系与积分体系的结构设计，实现时可按迭代先做「档位与配额校验 + 积分余额与流水」，再补签到与兑换。
