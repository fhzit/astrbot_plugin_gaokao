import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import Context, Star, register


@register("astrbot_plugin_gaokao_countdown", "HelloFHZ", "每日高考倒计时与 UAPIPro 一言", "1.0.0")
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
            tz = ZoneInfo(str(self.config.get("timezone", "Asia/Shanghai")))
            now = datetime.now(tz)
            target = now.replace(
                hour=int(self.config.get("send_hour", 7)),
                minute=int(self.config.get("send_minute", 0)),
                second=0,
                microsecond=0,
            )
            if target <= now:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            today = datetime.now(tz).date().isoformat()
            if self._sent_date != today:
                await self._send_daily()
                self._sent_date = today

    async def _saying(self):
        api_type = self.config.get("api_type", "mode")
        params = {}
        if api_type == "random":
            url = "https://uapis.cn/api/v1/saying"
        else:
            url = "https://uapis.cn/api/v1/saying/random"
            mode = self.config.get("mode", "daily")
            params["mode"] = mode
            if mode == "recommend":
                params["scene"] = str(self.config.get("scene", "morning"))
            for key in ("source", "category", "tag"):
                value = str(self.config.get(key, "")).strip()
                if value:
                    params[key] = value
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
        prefix = str(self.config.get("umo_prefix", "default"))
        for group in groups:
            try:
                await self.context.send_message(f"{prefix}:GroupMessage:{group}", message)
            except Exception as exc:
                logger.error(f"向群 {group} 推送失败：{exc}")
