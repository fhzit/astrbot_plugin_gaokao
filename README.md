# AstrBot 高考倒计时每日一言

每天定时向指定群聊发送：

```text
2026.10.07｜高考倒计时243天
名言警句内容
```

## 功能
- 自定义高考日期、发送时间、时区和目标群号。
- 两种 UAPIPro 接口：基础一言 `/api/v1/saying`；高级 `/api/v1/saying/random`，支持随机、每日、场景推荐、此刻。
- 高级接口支持 `source`、`category`、`tag` 过滤；场景模式可设置 `scene`。
- 可选启用发布前 LLM 审核：需填写 AstrBot 文本模型提供商 ID；仅模型明确返回 `PASS` 才发布。模型拒绝、返回格式异常或调用出错均按拒绝处理（fail-closed）。
- API Key 可选；留空匿名请求，有 Key 时通过 Bearer Authorization 传入。

## 配置
在插件配置中填写 `group_ids`（多个群号用英文逗号分隔）。`exam_date` 默认为 `2027-06-07`，每天发送时计算与当前日期的天数差。默认 07:00（Asia/Shanghai）。`umo_prefix` 必须填写 AstrBot 实际平台实例 ID；可参考 AstrBot 会话 UMO 的第一段。未填写时插件拒绝发送，避免把消息发往猜测的实例。

`api_type` 设为 `random` 使用基础接口，设为 `mode` 使用高级接口。高级接口的 `mode` 为 `random`、`daily`、`recommend`、`moment`；仅 `recommend` 需要 `scene`。可选过滤项填写 API 支持的来源、分类和标签；可用逗号或分号分隔多个过滤值。中文筛选建议使用中文语料来源/分类。

接口文档：[基础一言](https://uapis.cn/docs/api-reference/get-saying) · [随机/每日/场景/此刻](https://uapis.cn/docs/api-reference/get-saying-random)。

## 注意
- 插件按 AstrBot `context.send_message` 的会话 UMO 主动发送；请确保目标群确实可由对应平台实例发送。
- 当前实现每次插件进程只对每个日期发送一次；重启后不会持久化去重状态。发送失败会记日志，不自动重试。
- 依赖 `aiohttp`（AstrBot 环境通常已包含）。
