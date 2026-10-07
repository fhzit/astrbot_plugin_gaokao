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
        await self._register_cron_job()

    async def terminate(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        await self._unregister_cron_job()

    def _cron_expression(self) -> str | None:
        """把 send_hour/send_minute 换算成五段 cron 表达式，配置非法时返回 None。"""
        try:
            hour = int(self.config.get("send_hour", 7))
            minute = int(self.config.get("send_minute", 0))
        except (TypeError, ValueError):
            return None
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return None
        return f"{minute} {hour} * * *"

    def _cron_job_name(self) -> str:
        return f"gaokao_countdown_{id(self)}"

    async def _register_cron_job(self):
        """把每日推送注册到 AstrBot 未来任务（CronJobManager，basic 型持久任务）。"""
        cron_expression = self._cron_expression()
        if cron_expression is None:
            logger.warning("发送时间配置非法，未注册未来任务；内置调度器仍会重试")
            return
        cron_mgr = getattr(self.context, "cron_manager", None)
        if cron_mgr is None:
            logger.warning("当前 AstrBot 版本没有 cron_manager，仅使用内置调度器")
            return
        try:
            for job in await cron_mgr.list_jobs():
                if job.name == self._cron_job_name() and job.job_type == "basic":
                    await cron_mgr.delete_job(job.job_id)
            job = await cron_mgr.add_basic_job(
                name=self._cron_job_name(),
                cron_expression=cron_expression,
                handler=self._scheduled_fire,
                description="高考倒计时每日一言定时推送",
                timezone=str(self.config.get("timezone", "Asia/Shanghai")) or None,
                payload={},
                enabled=True,
                persistent=True,
            )
            logger.info(f"已注册未来任务 {job.job_id}（cron: {cron_expression}）")
        except Exception as exc:
            logger.error(f"注册未来任务失败，仅使用内置调度器：{exc}")

    async def _unregister_cron_job(self):
        cron_mgr = getattr(self.context, "cron_manager", None)
        if cron_mgr is None:
            return
        try:
            for job in await cron_mgr.list_jobs():
                if job.name == self._cron_job_name() and job.job_type == "basic":
                    await cron_mgr.delete_job(job.job_id)
        except Exception as exc:
            logger.warning(f"清理未来任务失败：{exc}")

    async def _scheduled_fire(self):
        """未来任务触发入口：只发当日第一条，其余交回内置调度器。"""
        tz = ZoneInfo(str(self.config.get("timezone", "Asia/Shanghai")))
        today = datetime.now(tz).date().isoformat()
        if self._sent_date != today:
            self._sent_date = today
            await self._send_daily()

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
        advanced = dict(self.config.get("hitokoto_advanced", {}) or {})
        token = str(self.config.get("uapi_token", "")).strip()
        headers = {"User-Agent": "AstrBot_UApiPro"}
        params = {}
        if token:
            headers["Token"] = token
            headers["Authorization"] = f"Bearer {token}"
            params["token"] = token
        if not advanced.get("enabled", False):
            url = "https://uapis.cn/api/v1/saying"
        else:
            url = "https://uapis.cn/api/v1/saying/random"
            mode = str(advanced.get("mode", "random")).strip()
            if mode not in {"random", "daily", "recommend", "moment"}:
                raise ValueError("高级一言 mode 只支持 random/daily/recommend/moment")
            if mode != "random":
                params["mode"] = mode
            if mode == "recommend":
                scene = str(advanced.get("scene", "")).strip()
                if not scene:
                    raise ValueError("recommend 模式必须填写 scene")
                params["scene"] = scene
            for key in ("source", "category", "tag"):
                value = advanced.get(key) or []
                if isinstance(value, str):
                    value = [v.strip() for v in value.replace("，", ",").split(",") if v.strip()]
                if value:
                    params[key] = ",".join(value)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {}
                if resp.status == 200:
                    item = data.get("item") if isinstance(data, dict) and isinstance(data.get("item"), dict) else data
                    text = (item.get("text") or item.get("content") or "").strip() if isinstance(item, dict) else ""
                    if text:
                        return text
                    raise ValueError("UAPIPro 响应中没有语录正文")
                api_msg = data.get("message") if isinstance(data, dict) else None
                if resp.status == 400:
                    raise ValueError(f"高级一言参数错误: {api_msg or '请检查 mode/scene 等配置是否合法'}")
                if resp.status == 404:
                    raise ValueError("未找到满足当前筛选条件的语录，请调整筛选配置")
                if resp.status == 500:
                    raise ValueError(f"语料库异常: {api_msg or '无法读取语录数据，请稍后再试'}")
                raise ValueError(f"接口响应异常 (HTTP {resp.status})" + (f": {api_msg}" if api_msg else ""))

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
