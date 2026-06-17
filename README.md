# Seedance Studio（重建版）

面向创作团队的内部 AI 视频生产工具，视频生成由 **Seedance（火山引擎）** 提供。
技术栈：**Streamlit + SQLAlchemy + 火山引擎 SDK**。

---

## 第一步：账号 / 角色 / 额度基础（当前已完成）

这一步搭好了整个工具的底座，重点是你要的**权限与额度治理**：

- **登录 + 角色**：管理员（admin）/ 成员（member）
- **管理后台**（仅管理员可见）
  - 新增成员、设初始密码
  - 停用 / 启用成员、重置成员密码
  - 给成员**分配 / 追加 / 重设** token 额度
  - **用量统计**：各成员已用额度、生成数、累计费用 + 柱状图 + CSV 导出
  - **额度流水**：分配、预占、释放、消耗全链路记录，可导出 CSV
  - **内容审核**：按成员查看创作记录与视频
- **成员侧**：登录后在侧边栏看到自己的剩余额度进度条
- **数据库**：`users` / `generations` / `quota_transactions`（额度变动全程留痕）

> 关于额度：Seedance 账号只有**一个真实 token 池**。这里给成员“分配额度”是在这个池子上做的**逻辑子预算**（用于约束和统计），不是物理隔离。所有成员的消耗都从同一个真实池子扣减。

---

## 第二步：Seedance 创作场 + 硬约束额度（当前已完成）

**模块 1 创作场**（直连火山方舟官方接口）：

- 视频剧本 Prompt + 参数（模型档位、宽高比、分辨率、时长、智能时长、Seed）
- 文生视频 / 多模态参考生视频（图片、视频、音频 URL / 上传素材 / Asset ID）
- 同步生成音频、水印、首尾帧模式等开关；尾帧/联网搜索字段需按真实 API 响应继续校准
- 调用 `POST /contents/generations/tasks` 创建任务，轮询 `GET .../tasks/{id}` 拿结果
- 后台任务队列执行，页面不阻塞；完成后展示视频、音频、尾帧（若接口返回）
- **生成前额度硬预检**：剩余额度不够本次预估则直接拦截
- 提交时预占预估 token，完成后按**接口返回的实际计费 token** 结算，失败自动释放预占
- 搜索历史（提示词/任务 ID/日期）+ 创作灵感库（一键复用提示词）

**硬约束额度**（生产级）：

- 账号 token 总额可配置（资源包大小 / 自设上限），后台「账号额度总览」可见可改
- **分配硬闸门**：所有成员额度之和**不得超过**账号总额，超额分配/重设会被拒绝
- **本地账本为实时闸门**：火山大模型是后付费、按小时结算，没有可实时调用的“硬剩余”，
  billing API 有延迟，**绝不能**放在每次生成的关键路径上。本应用作为账号唯一消费方，
  本地累计消耗即等同账号真实消耗；账号总额作为分配与消耗的硬基准。
- **预估更保守**：`TOKEN_ESTIMATE_SAFETY_MULTIPLIER` 默认 1.10；含参考视频时会按“每条参考视频≈输出时长”
  额外预占输入视频 token，最终仍按 Ark 返回的 `usage.total_tokens` 结算。

> 需要在 `.env` 配置 `ARK_API_KEY` 与 TOS 信息才能真实生成和上传素材；不要把 `.env` 提交到 Git。
> 默认使用 `doubao-seedance-2-0-260128`（Doubao-Seedance-2.0）。
> `doubao-seedance-2-0-fast-260128` 只有开通 fast 资源包后再把 `SEEDANCE_ENABLE_FAST=true` 打开，避免误走余额计费。
> 计费换算可配置 `TOKEN_UNIT_PRICE_WITH_VIDEO_YUAN` / `TOKEN_UNIT_PRICE_NO_VIDEO_YUAN`；
> `TOKEN_ESTIMATE_SAFETY_MULTIPLIER` 可按真实账单继续校准。
> 资源包总额默认是 `0`，必须在「管理后台 → 成员与额度 → 设置资源包额度基准」填入真实 token 后，才能安全分配和生成。

---

## 第三步：私域资产库（当前已完成）

**模块 2 资产库全览**（元数据自建库 + TOS 预览）：

- 浏览：资产组列表 + 组内素材（图片用 `st.image`、视频用 `st.video`）
- **多用户共享缓存**：组列表 5 分钟、组内素材 10 分钟（`@st.cache_data` 服务端共享）
- **懒加载**：展开资产组后点「加载素材」才拉取；组列表 / 组内素材均分页，每页 20
- 缓存刷新：刷新单组 / 刷新全部
- **管理增删**：新建 / 重命名 / 删除资产组；登记 / 删除素材（删除会 best-effort 清理 TOS 对象）
- **引用**：图片素材一键「用作创作场首帧」跳到创作场做图生视频

> 素材的正式入库路径是「人脸素材提交」（模块 3，下一步）；本页的「手动登记 TOS 地址」用于测试或登记已有对象。
> 数据模型：`asset_groups` / `assets`（含类型、TOS 地址与 key、审核状态）。

---

## 第四步：人脸素材入库（当前已完成）

**模块 3 人脸素材入库系统**（校验 + TOS 上传 + 可插拔审核）：

- 多文件上传（图片/视频，可多选）
- **格式校验**（对齐 Seedance 规则）：
  - 图片：jpg/png/webp/bmp/tiff/gif/heic/heif；≤30MB；300~6000px；宽高比 0.4~2.5（用 Pillow）
  - 视频：mp4/mov；≤50MB；2~15s；24~60fps；480p~1080p（用 opencv，缺失则降级为仅查格式与大小）
- **上传到火山 TOS**（`tos_client.upload_bytes`）
- **可插拔审核**：默认入库为待审核（pending），进入「管理后台 → 素材审核」由管理员批准/驳回；
  `review.py` 留好钩子，确认火山内容安全/肖像授权接口后接上即可
- **资产组配置**：从现有列表选择 / 手动输入 ID / 创建新组；批量提交并显示每个文件的结果
- **闭环**：批准后的图片立即可在创作场「选用首帧」中引用

> 视频深度校验需要 opencv（可选）：`pip install opencv-python-headless`。不装也能跑，只是视频仅校验格式与大小。
> HEIC/HEIF 图片如需校验尺寸，可选装：`pip install pillow-heif`。

**至此三大模块形成闭环**：人脸入库（模块 3）→ 资产库（模块 2）→ 创作场引用生成（模块 1）。

---

## 第五步：部署与运维收尾（当前已完成）

- **Ark 错误提示**：账号欠费、Safe Experience Mode / 限额、鉴权失败、参数错误会转成可读提示。
- **安全重试**：查询任务（GET）对 429 / 5xx / 网络抖动做有限重试；创建任务（POST）不自动重试，避免重复生成和重复扣费。
- **轮转日志**：默认写入 `logs/seedance.log`，记录任务提交、Ark task_id、成功/失败、TOS 转存等事件。
- **后台运维页**：管理员可查看 TOS 配置状态、账号剩余额度、最近日志，并下载日志。
- **TOS 连通性小样**：`scripts/tos_smoke.py --run` 会上传并删除一个临时对象。
- **Docker 健康检查**：镜像内置 Streamlit healthcheck，便于部署平台判断服务状态。

### TOS 访问模式

资产库和创作场默认使用 `TOS_URL_MODE=public`，也就是直接访问
`https://<bucket>.<endpoint>/<key>`。如果「打开原文件」显示 `403 AccessDenied`，
说明匿名公共读还没有真正对对象生效。可以任选一种处理方式：

- 在火山 TOS 控制台确认桶公共读/桶策略允许匿名 `GetObject`，并关闭阻止公共访问类开关。
- 在 `.env` 设置 `TOS_UPLOAD_ACL=public-read`，让后续新上传对象显式公共读。
- 更稳妥：设置 `TOS_URL_MODE=signed`，系统会基于 `tos_key` 动态生成临时签名 URL，旧素材无需重新上传。

修改 `.env` 后需要重启 Streamlit。

---

## 安装与运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
#   填入火山引擎信息（第一步只用到 DATABASE_URL，其余第二步再填也行）

# 3. 创建第一个管理员（如 冯宇轩）
python seed_admin.py
# 也可非交互创建：
# ADMIN_USERNAME=admin ADMIN_PASSWORD='强密码' python seed_admin.py

# 4. 启动
streamlit run app.py
```

用管理员登录后，进入「管理后台 → 成员与额度」即可创建成员并分配额度。
用成员账号登录，则只能看到「创作场 / 资产库 / 人脸素材提交」三个页面和自己的额度。

首次进入后台，请先设置资源包额度基准：

1. 打开火山控制台，进入「费用中心 / 资源包」或「火山方舟 / 用量统计」查看 Doubao-Seedance-2.0 资源包 token 总量与已用量。
2. 在本系统「管理后台 → 成员与额度 → 设置资源包额度基准」填写：
   - 资源包 token 总额：购买/开通的 Seedance 2.0 资源包总 tokens
   - 期初/外部已消耗 tokens：在本系统接管之前已经消耗的 tokens
3. 系统会用「资源包总额 - 期初/外部已消耗 - 本地已消耗 - 运行中预占」作为账号可用额度。

额度账本采用两阶段扣减：

- 提交前按参数做保守估算并预占额度，避免并发任务把成员额度或账号额度提交超。
- 任务成功后只按火山返回的 `usage.total_tokens` 结算真实消耗，预估值不会作为最终扣减值。
- 如果真实消耗高于预占，系统仍按真实 tokens 入账，并在流水/后台标记“超预占/成员超额”，后续提交会被额度闸门拦住。
- 如果任务已经提交到 Ark，但本地查询超时、查询失败，或成功响应缺少 `usage.total_tokens`，系统不会释放预占，会进入 `needs_settlement`，管理员需用 Task ID 到火山确认真实 tokens 后人工结算；确认未计费时再释放预占。
- 本地账本只能自动覆盖本系统发起的任务。任何绕过本系统直接调用火山的消耗，需要及时更新“期初/外部已消耗 tokens”，否则本地剩余额度会高于火山真实剩余额度。

### 配置与小样测试

```bash
# 检查配置是否完整（不会打印密钥）
python scripts/check_config.py

# 只打印最小化请求 payload 和预估成本，不调用火山
python scripts/seedance_smoke.py

# 确认要消耗额度时再真实提交；--poll 会轮询到终态并打印原始响应
python scripts/seedance_smoke.py --run --poll

# 检查 TOS；--run 会真实上传并删除一个 smoke/*.txt 临时对象
python scripts/tos_smoke.py --run

# 查看本地额度账本摘要
python scripts/quota_audit.py

# 从命令行设置资源包额度基准
python scripts/set_account_baseline.py --total <资源包总tokens> --external-used <已消耗tokens>

# 已有 task_id 时查询状态和解析结果
python scripts/inspect_task.py <task_id> --raw

# 手动把已成功任务的输出转存到自己的 TOS
python scripts/mirror_task_outputs.py <task_id>
```

当前本机验证结果：

- 配置检查通过：Ark API Key、Seedance 模型 ID、TOS Bucket 均已读取。
- TOS 小样通过：成功上传并删除临时对象。
- 默认模型已切到 `pro`：`python scripts/seedance_smoke.py` 会使用 `doubao-seedance-2-0-260128`。
- `fast` 默认禁用：`python scripts/seedance_smoke.py --model fast` 会在本地拦截，不会提交到 Ark。
- 已用 `pro` 模型真实小样跑通：任务 `cgt-20260613120138-f8jgg`，实际消耗 `40,594` tokens，
  输出视频字段为 `content.video_url`。
- 后台队列闭环已跑通：创建 Generation → Ark 任务 → 轮询成功 → 转存到自有 TOS → 按实际 token 结算。
- 4 秒 480p 预估为 `42,224` tokens，实际为 `40,594` tokens，安全系数覆盖正常。

如果小样返回 `SetLimitExceeded`，并提示 “Safe Experience Mode”，说明账号在该模型上触发了火山方舟的安全体验限额，
需要到火山方舟控制台的模型开通/模型详情页调整或关闭 Safe Experience Mode 后再测试。

成功响应解析已用真实 `pro` 小样确认：

- 输出视频：`content.video_url`
- 实际 token：`usage.total_tokens`
- 辅助字段：`seed`、`resolution`、`ratio`、`duration`、`framespersecond`
- 输出 URL 是带有效期的 TOS 签名 URL；后台任务成功后会尽量转存到自己的 TOS 桶，转存失败则保留 Ark 原始 URL。

### Docker 部署

```bash
docker compose up -d --build
```

`docker-compose.yml` 默认使用 PostgreSQL，并通过 `.env` 读取 Ark / TOS 配置。
生产环境必须在 `.env` 设置 `POSTGRES_PASSWORD`，并把域名、HTTPS、访问控制改成正式配置。
注意：`docker compose config` 会展开 `.env` 中的密钥，只能在本机排查时使用，不要把输出粘到群里或工单里。

---

## 目录结构

```
seedance-studio/
├── app.py            # 入口：登录 + 角色导航 + 侧边栏额度
├── config.py         # 环境变量配置
├── database.py       # 数据库连接 / 会话 / 建表
├── models.py         # User / Generation / QuotaTransaction
├── auth.py           # 密码哈希、登录、权限守卫
├── quota.py          # 额度分配 / 调整 / 校验 / 消耗 / 统计
├── seed_admin.py     # 创建首个管理员
├── task_runner.py    # 后台任务队列 / 轮询 / 结算
├── requirements.txt
├── .env.example
├── scripts/          # 配置检查 / 小样测试 / 任务查询
└── views/
    ├── admin.py      # 管理后台（成员/额度/用量/审核）
    ├── studio.py     # 模块 1 创作场（第二步实现）
    ├── assets.py     # 模块 2 资产库（第三步实现）
    └── face.py       # 模块 3 人脸入库（第四步实现）
```

---

## 后续步骤

| 步骤 | 内容 | 状态 |
|------|------|------|
| 第一步 | 账号 / 角色 / 额度治理底座 | ✅ 已完成 |
| 第二步 | 模块 1 创作场 + 硬约束额度 | ✅ 已完成 |
| 第三步 | 模块 2 私域资产库（浏览/缓存/分页/管理/引用） | ✅ 已完成 |
| 第四步 | 模块 3 人脸素材入库（校验/TOS 上传/审核/资产组） | ✅ 已完成 |
| 第五步 | 部署优化：错误提示、安全重试、日志、TOS 小样、Docker 健康检查 | ✅ 已完成 |

---

## 默认数据库

默认用 SQLite（`seedance_studio.db`，单文件、零配置），适合团队内部使用。
多实例部署可在 `.env` 把 `DATABASE_URL` 换成 PostgreSQL，代码无需改动。
