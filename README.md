# 璃芽项目说明

璃芽是运行在 QQ 群中的 AI 小龙娘，采用 MaiBot、QQ 官方适配器和独立陪伴插件。当前版本已部署在 `10.249.254.57:/opt/dragon-maibot`；本目录保存设计、工作流程、部署参考、插件源码、形象素材和验收证据。

整理日期为 2026年11月3日，时区 Asia/Shanghai。当前陪伴插件为 1.0.1，源码提交为 `ed9205b`。模型为 `gpt-6.1-sol`，推理档位 `low`。

## 阅读入口

| 内容 | 入口 |
| --- | --- |
| 角色性格与形象 | [角色与形象设计](docs/01角色与形象设计.md) |
| 消息处理、情绪和记忆 | [工作方式](docs/02工作方式.md) |
| 服务器、启动、备份与恢复 | [部署与维护](docs/03部署与维护.md) |
| 修改源码、测试与升级 | [开发与验证](docs/04开发与验证.md) |
| 已通过的测试和实际验证范围 | [验收记录](docs/05验收记录.md) |
| 完整人格配置 | [人格配置 JSON](design/liya-personality-v2.json) |
| 形象与运行设计 | [设计配置 JSON](design/liya-virtual-character-design.json) |

## 目录用途

```text
LiYa/
  README.md                    项目入口
  docs/                        设计、运行、部署、开发与验收说明
  design/                      人格与形象设计配置
  assets/                      头像、九状态立绘、GIF和动画图集
  src/liya_companion/           已部署的独立陪伴插件及运行素材
  tests/                       针对 MaiBot 原生契约的集成测试
  deploy/                      Compose、备份脚本、脱敏配置与兼容补丁
  validation/                  结构化验收证据
  FILES.sha256                 本次整理的文件哈希
```

`src/liya_companion/assets/` 是插件运行所需的素材副本；`assets/expressions/` 供设计预览和后续创作使用。这里只保存可读配置模板，不包含实际 API Key、QQ AppSecret、聊天数据库、登录令牌、SSH 私钥或服务器备份包。

现网已经运行，不需要为了使用本目录重新部署。`deploy/compose.example.yaml` 应放在服务器部署目录根部使用；直接在本地 `deploy/` 下启动会挂载错误的数据位置。新服务器部署还需要 QQ 适配器、模型文件和管理员填写的真实凭证，步骤见部署文档。

## 形象预览

![璃芽九状态动画](assets/expressions/liya-nine-states.gif)
