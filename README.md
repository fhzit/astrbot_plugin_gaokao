<div align="center">

# 🎓 高考倒计时每日一言

*每天一句名言，陪你倒数高考*

[![AstrBot](https://img.shields.io/badge/framework-AstrBot-ff6b6b?style=flat-square)](https://github.com/AstrBotDevs/AstrBot)
[![UAPIPro](https://img.shields.io/badge/API-UAPIPro-7c3aed?style=flat-square)](https://uapis.cn)

</div>

## ✨ 简介

这是一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件：每天在指定时刻向配置的群聊推送高考倒计时与一句来自 [UAPIPro](https://uapis.cn) 的名言警句。插件无状态，不保存任何聊天数据。

- 插件仓库：<https://github.com/fhzit/astrbot_plugin_gaokao>
- 消息格式：

```text
2026.10.07｜高考倒计时243天
不必害怕未知，你付出的每一分努力，都在悄悄塑造未来的自己。沉下心，稳住脚步，一点点靠近目标。
```

## ✨ 功能特性

- ⏰ **每日定时**：自定义高考日期、发送时刻（时/分）与时区，每天自动计算剩余天数并推送。
- 🎯 **多群投递**：`group_ids` 填多个群号（英文逗号分隔），逐群投递。
- 💬 **两种一言接口**：基础随机一言 `/api/v1/saying`；高级一言 `/api/v1/saying/random`，支持随机 / 每日 / 场景推荐 / 此刻四种模式。
- 🔍 **高级筛选**：高级一言支持 `source`（来源语料库）、`category`（分类）、`tag`（标签）多选过滤；场景推荐模式可指定 `scene`。
- 🔑 **API Key 可选**：留空匿名请求；填写后同时以 `?token=`、`Token` 头、`Authorization: Bearer` 三种方式传递，管理面板遮罩显示。
- 🛡️ **可选 LLM 审核**：发布前交给 AstrBot 文本模型审核，仅模型明确返回 `PASS` 才发送；拒绝、格式异常或调用出错一律阻止（fail-closed）。

---

## 快速开始

### 1. 安装插件

将本仓库放到 AstrBot 的插件目录：

```sh
cd AstrBot/data/plugins
git clone https://github.com/fhzit/astrbot_plugin_gaokao.git
```

也可以用 AstrBot 管理面板的 `插件市场` / `安装插件`（支持仓库地址或上传 zip）安装，然后在面板中重载插件。

依赖仅 `aiohttp`（AstrBot 环境通常已包含）。

### 2. 配置插件

在 AstrBot 管理面板 → `插件` → `高考倒计时每日一言` 中填写。**最小可用配置：`group_ids` + `umo_prefix`。**

- `group_ids`（必填）：目标群号，多个用英文逗号分隔，例如 `123456,234567`。
- `umo_prefix`（必填）：AstrBot 平台**实例 ID**（UMO 的第一段）。未填写时插件拒绝发送，避免把消息发往猜测的实例。
- `exam_date`：高考日期，格式 `YYYY-MM-DD`，默认 `2027-06-07`。
- `send_hour` / `send_minute`：每日发送时刻，默认 `7:00`。
- `timezone`：时区，默认 `Asia/Shanghai`。

### 3. 一言设置

- `uapi_token`（可选）：UAPIPro 的 API Token，前往 [uapis.cn](https://uapis.cn) 获取；留空则匿名调用。设置为敏感项，管理界面遮罩显示。

**高级一言**（`hitokoto_advanced` 配置组）：

- `enabled`：开关。关闭使用基础随机一言接口；开启后走高级接口。
- `mode`：`random` 随机一言；`daily` 每日固定一言；`recommend` 按场景推荐（需选 `scene`）；`moment` 按当前时段自动适配。
- `scene`：仅 `recommend` 模式生效，提供 13 个场景选项（清晨、上午、工作、编程、哲学等）。
- `source` / `category` / `tag`：多选过滤，不选则不限制。选项与 UAPIPro 文档一致，多个值以逗号拼接传参。

接口文档：[基础一言](https://uapis.cn/docs/api-reference/get-saying) · [随机/每日/场景/此刻](https://uapis.cn/docs/api-reference/get-saying-random)。

### 4. LLM 审核（可选）

- `llm_review_enabled`：默认关闭。开启后每次发送前把完整消息交给模型审核。
- `llm_provider_id`：AstrBot 文本模型提供商 ID。

审核规则为 **fail-closed**：仅模型明确返回 `PASS` 才发布；模型拒绝、返回格式异常、未配置提供商或调用出错均阻止发送并记录日志。

## 工作方式

```
定时触发（send_hour:send_minute，timezone）
        │
        ├─ 计算剩余天数（exam_date - 今天）
        ├─ 请求 UAPIPro 一言（基础 / 高级 + 筛选）
        ├─ （可选）LLM 审核：仅 PASS 放行
        └─ context.send_message(UMO, MessageChain) ──▶ 各群投递
```

- 主动推送使用 UMO `平台实例ID:GroupMessage:群号` 与 `MessageChain`。
- 每个日期只发送一次；插件重启后不记忆当日是否已发送。发送失败记日志，不自动重试。

## 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| 到点没有发送 | 检查 `group_ids`、`umo_prefix` 是否填写；`umo_prefix` 必须是平台实例 ID（UMO 第一段），不是适配器类型名。 |
| 日志出现「高级一言参数错误」 | `mode`/`scene` 配置不合法：`recommend` 模式必须选 `scene`，其余模式忽略它。 |
| 日志出现「未找到满足筛选条件的语录」 | `source`/`category`/`tag` 过滤太严，放宽筛选或清空。 |
| 一言请求 401 | Token 无效，检查 `uapi_token` 或留空匿名调用。 |
| 开了 LLM 审核但不发送 | 审核是 fail-closed：模型未明确返回 `PASS`（拒绝、格式异常、出错）都不发送；检查 `llm_provider_id` 与模型可用性。 |
| 消息内容重复 | `daily` 模式每日固定一言；想每天不同改用 `random`/`moment`。 |

## 开发与测试

```sh
python3 -m py_compile main.py
python3 tests/test_plugin.py
```

测试不需要真实 AstrBot：`tests/test_plugin.py` 注入最小 `astrbot.*` 与 `aiohttp` 桩模块，覆盖一言筛选参数与三种 Token 传递、`item` 包装与裸对象响应、400/404/500 错误分类、LLM 审核放行/拒绝/异常/未配置门控、UMO 与 `MessageChain` 发送形状，以及 `metadata.yaml` 必填字段与 logger 导入契约。

未覆盖：真实适配器的端到端送达、定时触发的真实时钟行为、UAPIPro 线上接口的实际响应。

## 项目结构

```
main.py              插件入口：定时调度、一言请求、LLM 审核、群投递
__init__.py          插件元信息（name/version/author/repo）
metadata.yaml        插件市场元数据（name 以 astrbot_plugin_ 开头）
_conf_schema.json    管理面板配置项定义（根级映射 + hitokoto_advanced 配置组）
tests/test_plugin.py 桩 astrbot + 桩 aiohttp 的测试套件
```
