import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import MessageChain
from astrbot.api.star import Context, Star, register


@register("astrbot_plugin_gaokao", "HelloFHZ", "每日高考倒计时与 UAPIPro 一言", "1.1.0")
class GaokaoCountdown(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.task = None
        self._sent_date = None

    async def initialize(self):
        self.task = asyncio.create_task(self._scheduler())

    async def terminate(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _scheduler(self):
        while True:
            try:
                tz = ZoneInfo(str(self.config.get("timezone", "Asia/Shanghai")))
                now = datetime.now(tz)
                hour = int(self.config.get("send_hour", 7))
                minute = int(self.config.get("send_minute", 0))
                if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                    raise ValueError("发送时间必须为有效的小时和分钟")
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                await asyncio.sleep((target - now).total_seconds())
                today = datetime.now(tz).date().isoformat()
                if self._sent_date != today:
                    await self._send_daily()
                    self._sent_date = today
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"定时任务配置或执行异常，将在 60 秒后重试：{exc}")
                await asyncio.sleep(60)

    async def _saying(self):
        api_type = self.config.get("api_type", "mode")
        params = {}
        if api_type == "random":
            url = "https://uapis.cn/api/v1/saying"
        elif api_type == "mode":
            url = "https://uapis.cn/api/v1/saying/random"
            mode = str(self.config.get("mode", "daily"))
            if mode not in {"random", "daily", "recommend", "moment"}:
                raise ValueError("高级一言 mode 只支持 random/daily/recommend/moment")
            params["mode"] = mode
            if mode == "recommend":
                scene = str(self.config.get("scene", "")).strip()
                if not scene:
                    raise ValueError("recommend 模式必须填写 scene")
                params["scene"] = scene
            for key in ("source", "category", "tag"):
                value = str(self.config.get(key, "")).strip()
                if value:
                    params[key] = value
        else:
            raise ValueError("api_type 只支持 random 或 mode")
        headers = {}
        api_key = str(self.config.get("api_key", "")).strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        item = data.get("item", data)
        text = item.get("text") or item.get("content")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("UAPIPro 响应中没有语录正文")
        return text.strip()

    async def _send_daily(self):
        groups = [
            value.strip()
            for value in str(self.config.get("group_ids", "")).replace("，", ",").split(",")
            if value.strip()
        ]
        if not groups:
            logger.warning("高考倒计时：未配置群聊 ID")
            return
        prefix = str(self.config.get("umo_prefix", "")).strip()
        if not prefix:
            logger.error("必须填写实际 AstrBot 平台实例 ID（umo_prefix），已停止发送")
            return
        tz = ZoneInfo(str(self.config.get("timezone", "Asia/Shanghai")))
        today = datetime.now(tz).date()
        try:
            exam_date = datetime.strptime(
                str(self.config.get("exam_date", "2027-06-07")), "%Y-%m-%d"
            ).date()
            days = (exam_date - today).days
            saying = await self._saying()
            message = f"{today:%Y.%m.%d}｜高考倒计时{days}天\n{saying}"
        except Exception as exc:
            logger.error(f"获取一言或构造消息失败：{exc}")
            return
        if bool(self.config.get("llm_review_enabled", False)):
            try:
                provider_id = str(self.config.get("llm_provider_id", "")).strip()
                if not provider_id:
                    logger.warning("LLM 审核已启用但未配置模型提供商 ID，按拒绝发布处理")
                    return
                review = await self.context.llm_generate(
                    chat_provider_id=provider_id,
                    system_prompt=(
                        "你是高考倒计时群消息的内容审核员。只检查下面待发布内容是否适合学生群。"
                        "若无明显违法、色情、仇恨、欺凌、危险行为鼓励、政治敏感或明显不当内容，返回 PASS；"
                        "否则返回 REJECT。只输出 PASS 或 REJECT，不要解释。"
                    ),
                    prompt=f"审核以下内容：\n{message}",
                )
                result = str(getattr(review, "completion_text", "") or "").strip().upper()
                if result != "PASS":
                    logger.info(f"LLM 审核未通过或无法识别结果，已阻止发布：{result[:80]}")
                    return
            except Exception as exc:
                logger.error(f"LLM 审核调用失败，按拒绝发布处理：{exc}")
                return
        try:
            from astrbot.core.platform.message_session import MessageSession
            parsed = MessageSession.from_str(f"{prefix}:GroupMessage:{groups[0]}")
            platform_id = parsed.platform_name
        except Exception as exc:
            logger.error(f"群聊会话 UMO 配置格式错误：{exc}")
            return
        for group in groups:
            try:
                umo = f"{platform_id}:GroupMessage:{group}"
                sent = await self.context.send_message(umo, MessageChain().message(message))
                if sent is False:
                    logger.error(f"向群 {group} 发送失败：没有找到匹配的平台实例")
            except Exception as exc:
                logger.error(f"向群 {group} 推送失败：{exc}")
