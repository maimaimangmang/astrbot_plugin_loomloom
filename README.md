# LoomLoom

批量执行云端 Skill，处理文案创作、图片生成与需求审阅。聊天选技能，Excel 批量准备，确认费用后执行并收取结果。

这是连接 **AstrBot 与胜算云 LoomLoom** 的第三方插件。当前支持 **QQ 私聊（NapCat / OneBot v11 / aiocqhttp）**，要求 AstrBot `>=4.29.4,<5`，仍在持续测试。不是胜算云或 AstrBot 官方维护的插件。

## 能做什么

- 同时查找官方模板与市场 Skill，支持关键词搜索。
- 选择技能后自动检查云端流程兼容性，无需手填技能白名单。
- 根据聊天资料生成 Excel 草稿，或下载原始工作簿手动填写。
- 校验输入、展示费用，用户发送确认码后才提交任务。
- 查询当前会话的任务，完成后尝试发送结果 Excel。

**能力边界**：本插件执行已适配的云端工作簿流程，不是任意 Agent Skill 运行器。不执行包内脚本、浏览器操作或网页 PPT 本地生成。自定义或无法识别的 Skill 包会提示未适配。图片、多值输入按原始 Excel 说明准备，暂不支持从 QQ 图片自动上传素材。

## 安装

在 AstrBot「插件」中选择从链接安装，填写：

```
https://github.com/maimaimangmang/astrbot_plugin_loomloom
```

也可以上传包含本仓库完整内容的 ZIP。依赖由 `requirements.txt` 声明，需要联网安装。无需安装 LoomLoom CLI。

打开插件配置：

1. 填写自己的胜算云 LoomLoom API Key，仅在管理页填写，不要发到 QQ。
2. 「允许使用的 QQ 账号」填写你日常使用的账号，不是机器人小号。留空时全部禁用。
3. 保存并重载，在 QQ 私聊发送 `/loom doctor`。不知道自己的账号时先发 `/loom whoami`。

如需自然语言使用，在人格工具权限中允许本插件的 7 个工具，并选择支持工具调用的聊天模型。可参考 [人格工具说明](PERSONA.md)。

### 注册入口

使用 AstrBot 的用户，通过[专属链接新注册胜算云](https://www.shengsuanyun.com/?from=CH_T70U2X9L)，可领取 5 元体验 Token。此链接包含渠道标识；活动资格、领取方式及额度使用范围以胜算云活动说明为准。插件不会自动发放额度，也不保证体验额度适用于所有 LoomLoom 费用。

## 从一条任务开始

```
/loom list
/loom market 电商
/loom inspect 市场技能ID
```

查询和兼容性检查不提交付费任务。选定模板后：

```
/loom use official text-v1
```

填好返回的 Excel，删除不需要的示例行，在同一个私聊中发送 `.xlsx` 文件。插件上传文件校验并报价；检查金额后，发送返回的 `确认执行 确认码` 才会执行。支持的市场技能用 `/loom use market 技能ID`。

自然语言示例：

> 帮我用 LoomLoom 批量改写三条产品介绍，先找模板、问清资料、整理成 Excel 给我核对，不要执行。

模型代填的草稿需先由你发送 `核对完成 草稿码` 获取报价，然后再发送 `确认执行 确认码`。单独说“好”或模型代替确认均不会提交任务。

## 命令

| 命令 | 用途 |
| --- | --- |
| `/loom help` | 查看帮助 |
| `/loom whoami` | 查看自己的 QQ 号 |
| `/loom doctor` | 检查权限、连接、密钥和余额 |
| `/loom list` | 官方目录和市场第一页 |
| `/loom market 关键词` | 搜索市场 |
| `/loom inspect 技能ID` | 检查市场技能兼容性 |
| `/loom use official 模板ID` | 下载官方工作簿 |
| `/loom use market 技能ID` | 检查并下载市场工作簿 |
| `/loom quote 草稿码` | 核对草稿后获取报价 |
| `/loom confirm 确认码` | 确认费用并提交 |
| `/loom cancel 确认码` | 取消尚未提交的报价 |
| `/loom tasks` | 当前私聊的任务 |
| `/loom status 任务ID` | 查询任务状态 |
| `/loom result 任务ID` | 获取结果工作簿 |
| `/loom resolve 确认码 任务ID或none` | 人工核实后处理结果未明的提交 |

## 费用、文件与隐私

- 需要胜算云 LoomLoom 账号和可用额度。默认单次预估上限为 5 CNY，这不是最终账单硬上限，实际费用以平台结算为准。对话模型费用另外计算。
- 每次执行必须确认最新报价；输入、价格或版本变化时重新确认。提交异常时保存结果未明状态，不自动重试付费请求。
- API Key 仅发送到固定的 `https://loomloom.shengsuanyun.com/loom/v1`，不跟随重定向。公开目录和技能包请求不带密钥。
- 输入工作簿上传到胜算云用于校验、报价和执行。下载的公开技能包校验 SHA-256，仅作静态检查，不执行其中代码。
- 仅接受允许账号的 QQ 私聊。任务按账号和会话隔离，不支持群聊。内部标识为 `astrbot_plugin_loomloom`。
- 本地数据保存在 AstrBot `data/plugin_data/astrbot_plugin_loomloom`。输入文件超过 7 天清理、结果临时文件超过 1 天清理；进程运行时定期清理。任务记录与包缓存保留，不在 SQLite 中保存密钥。
- 自动通知最多检查 7 天。QQ 风控、机器人离线、网络故障可能影响发送；本插件不消除第三方 QQ 接入的账号风险。

## Docker 文件传输

默认使用 `http://astrbot:6185` 的 AstrBot 临时文件服务传递 Excel。NapCat 需要能访问该地址；服务名不同请修改「NapCat 读取文件时使用的 AstrBot 地址」。原生部署且双方共享文件系统时可以留空。

只接收不超过 10 MB 的 `.xlsx`，不接收宏文件或外部链接工作簿。附件下载不接受内网地址或任意本机路径。大文件请在 LoomLoom 控制台处理。

## 升级与故障

升级前等待现有任务结束，备份插件配置与数据。通过同名仓库或 ZIP 更新，保留数据。若模型继续沿用旧回答，可在 QQ 发送 `/new`。

“提交结果未明”时先查 LoomLoom 控制台。确认有任务后用 `/loom resolve 确认码 任务ID` 关联；**只有确定没有创建任务时**才用 `none` 解锁。不要重复发送同一输入碰运气。

## 验证与开发

79 项本地测试通过，覆盖权限、工作簿、费用确认、并发、异常恢复、市场兼容性和消息适配。另在真实 AstrBot 4.29.4 的隔离容器中验证过 7 个工具、14 条命令及模拟任务流程。QQ 传输和云端执行在该环境中使用替身；不代表真实账号端到端付费运行已完成验证。

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

兼容性检查会对标准云端包做静态指纹比较；未知生成器版本可能需要适配后才能使用，不会凭“available”推断完整本地能力。

## 参考与许可

- [胜算云 LoomLoom API](https://lean.shengsuanyun.com/apidocs/loomloom/api)
- [LoomLoom CLI](https://github.com/cogfoundry-labs/loomloom)
- [AstrBot 插件开发](https://docs.astrbot.app/dev/star/plugin.html)

本插件代码采用 [MIT License](LICENSE)。外部服务及 Skill 包遵守各自条款。
