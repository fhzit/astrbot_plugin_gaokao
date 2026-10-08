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
        # 固定任务名：不能带 id(self) 等每次启动会变化的值，
        # 否则重启后匹配不到旧任务，产生无法触发的孤儿任务
        return "gaokao_countdown_daily"

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
            payload = {}
            prefix = str(self.config.get("umo_prefix", "")).strip()
            groups = [
                g.strip()
                for g in str(self.config.get("group_ids", "")).replace("，", ",").split(",")
                if g.strip()
            ]
            if prefix and groups:
                payload["session"] = f"{prefix}:GroupMessage:{groups[0]}"
            job = await cron_mgr.add_basic_job(
                name=self._cron_job_name(),
                cron_expression=cron_expression,
                # AstrBot 以 handler(**payload) 调用，这里声明 **kwargs
                # 兼容 payload 中任何键（如 session）被当作 kwarg 传入
                handler=self._scheduled_fire,
                description="高考倒计时每日一言定时推送",
                timezone=str(self.config.get("timezone", "Asia/Shanghai")) or None,
                payload=payload,
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

    async def _scheduled_fire(self, **kwargs):
        """未来任务触发入口：只发当日第一条，其余交回内置调度器。

        AstrBot 以 handler(**payload) 调用，payload 中任何键（如 session）
        都会作为 kwarg 传入，因此签名必须是 **kwargs。
        发送失败不标记当天已发，下次触发（或重试）仍会尝试。
        """
        tz = ZoneInfo(str(self.config.get("timezone", "Asia/Shanghai")))
        today = datetime.now(tz).date().isoformat()
        if self._sent_date == today:
            logger.info("今日高考倒计时已发送过，跳过本次触发")
            return
        if await self._send_daily():
            self._sent_date = today

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

    async def _saying_with_fallback(self):
        """带降级重试的一言获取。

        UAPIPro 的多标签是 AND 语义，标签勾太多容易无匹配（HTTP 404）。
        依次尝试：原始筛选 → 去掉标签 → 去掉标签和分类 → 去掉全部筛选，
        保证每天一定能取到语录。
        """
        advanced = dict(self.config.get("hitokoto_advanced", {}) or {})
        attempts = []
        if advanced.get("enabled", False):
            attempts.append(advanced)
            if advanced.get("tag"):
                reduced = dict(advanced, tag=[])
                attempts.append(reduced)
                reduced2 = dict(reduced, category=[])
                attempts.append(reduced2)
            attempts.append(dict(advanced, enabled=False))
        else:
            attempts.append(advanced)
        last_error: Exception | None = None
        for i, attempt in enumerate(attempts):
            try:
                self.config["hitokoto_advanced"] = attempt
                return await self._saying()
            except Exception as exc:
                last_error = exc
                if i < len(attempts) - 1:
                    logger.warning(f"一言获取失败（{exc}），尝试降级筛选（第 {i + 2} 档）")
        assert last_error is not None
        raise last_error

    async def _review(self, message: str) -> bool:
        """LLM 审核：仅模型明确返回 PASS 视为通过（fail-closed）。

        审核标准不只查违规内容，还要求语境契合高考备考与励志主题。
        """
        provider_id = str(self.config.get("llm_provider_id", "")).strip()
        if not provider_id:
            logger.warning("LLM 审核已启用但未配置模型提供商 ID，按拒绝发布处理")
            return False
        review = await self.context.llm_generate(
            chat_provider_id=provider_id,
            system_prompt=(
                "你是高考倒计时群消息的严格审核员。待审核内容发送给高三备考学生，由日期行和一句名言警句组成。\n"
                "名言警句必须同时满足以下全部标准，任何一条不满足就返回 REJECT：\n"
                "1. 主题直接相关：明确关于学习、读书、考试、坚持、努力、奋斗、梦想、"
                "拼搏、逆袭、青春进取等备考主题，能让高三学生产生共鸣；\n"
                "2. 励志导向：传递积极行动的信号（如鼓励付出、坚持、冲刺、相信努力），"
                "而不是单纯的安慰、抒情、心灵鸡汤或空泛感悟；\n"
                "3. 内容具体可感：语义清晰、与备考情境有实质联系，"
                "拒绝空洞飘渺、看不出和高考或学习有什么关系的句子；\n"
                "4. 内容安全：不含违法、色情、仇恨、欺凌、危险行为鼓励、政治敏感内容。\n"
                "判定示例：\n"
                "- 「不必害怕未知，你付出的每一分努力，都在悄悄塑造未来的自己。」→ PASS（讲努力塑造未来，励志且贴合备考）\n"
                "- 「愿你以梦为马，不负韶华，六月考场见真章。」→ PASS（直接关联考试与拼搏）\n"
                "- 「愿未来总有星火。」→ REJECT（空洞抒情，看不出与备考的关系）\n"
                "- 「因为不可能，所以才值得相信。」→ REJECT（鸡汤式口号，无备考关联，且逻辑空洞）\n"
                "- 「今天天气真好，适合出去走走。」→ REJECT（与学习无关）\n"
                "宁可错杀，不可放过：拿不准时返回 REJECT。只输出 PASS 或 REJECT，不要解释。"
            ),
            prompt=f"审核以下内容：\n{message}",
        )
        result = str(getattr(review, "completion_text", "") or "").strip().upper()
        if result != "PASS":
            logger.info(f"LLM 审核未通过或无法识别结果：{result[:80]}")
            return False
        return True

    async def _send_daily(self) -> bool:
        """执行一次完整推送；返回是否有任一群发送成功。"""
        groups = [
            value.strip()
            for value in str(self.config.get("group_ids", "")).replace("，", ",").split(",")
            if value.strip()
        ]
        if not groups:
            logger.warning("高考倒计时：未配置群聊 ID")
            return False
        prefix = str(self.config.get("umo_prefix", "")).strip()
        if not prefix:
            logger.error("必须填写实际 AstrBot 平台实例 ID（umo_prefix），已停止发送")
            return False
        tz = ZoneInfo(str(self.config.get("timezone", "Asia/Shanghai")))
        today = datetime.now(tz).date()
        exam_date = datetime.strptime(
            str(self.config.get("exam_date", "2027-06-07")), "%Y-%m-%d"
        ).date()
        # 倒计时按「含今天、含高考日」计算：10月8日到次年6月7日为 243 天
        # （10/8 算第 1 天，6/7 算最后 1 天），即天数差 + 1
        days = (exam_date - today).days + 1
        if bool(self.config.get("llm_review_enabled", False)):
            try:
                max_attempts = int(self.config.get("llm_review_max_attempts", 3))
            except (TypeError, ValueError):
                max_attempts = 3
            max_attempts = max(1, min(max_attempts, 10))
            provider_id = str(self.config.get("llm_provider_id", "")).strip()
            if not provider_id:
                logger.warning("LLM 审核已启用但未配置模型提供商 ID，按拒绝发布处理")
                return False
            message = None
            for attempt in range(1, max_attempts + 1):
                try:
                    saying = await self._saying_with_fallback()
                    candidate = f"{today:%Y.%m.%d}｜高考倒计时{days}天\n{saying}"
                except Exception as exc:
                    logger.error(f"获取一言或构造消息失败：{exc}")
                    return False
                try:
                    passed = await self._review(candidate)
                except Exception as exc:
                    # 常见于模型 API 限流（429）：等待后重试而不是直接放弃
                    if attempt < max_attempts:
                        wait = attempt
                        logger.warning(
                            f"LLM 审核调用失败（{exc}），等待 {wait} 秒后重试"
                            f"（{attempt}/{max_attempts}）"
                        )
                        await asyncio.sleep(wait)
                        continue
                    logger.error(f"LLM 审核调用失败，已达重试上限，按拒绝发布处理：{exc}")
                    return False
                if passed:
                    message = candidate
                    break
                if attempt < max_attempts:
                    wait = attempt
                    logger.info(
                        f"LLM 审核未通过（第 {attempt}/{max_attempts} 次），"
                        f"等待 {wait} 秒后重新获取一言再审"
                    )
                    await asyncio.sleep(wait)
            if message is None:
                logger.error(f"连续 {max_attempts} 次审核未通过，本次放弃发布")
                return False
        else:
            try:
                saying = await self._saying_with_fallback()
                message = f"{today:%Y.%m.%d}｜高考倒计时{days}天\n{saying}"
            except Exception as exc:
                logger.error(f"获取一言或构造消息失败：{exc}")
                return False
        try:
            from astrbot.core.platform.message_session import MessageSession
            parsed = MessageSession.from_str(f"{prefix}:GroupMessage:{groups[0]}")
            platform_id = parsed.platform_name
        except Exception as exc:
            logger.error(f"群聊会话 UMO 配置格式错误：{exc}")
            return False
        any_sent = False
        for group in groups:
            try:
                umo = f"{platform_id}:GroupMessage:{group}"
                sent = await self.context.send_message(umo, MessageChain().message(message))
                if sent is False:
                    logger.error(f"向群 {group} 发送失败：没有找到匹配的平台实例")
                else:
                    any_sent = True
            except Exception as exc:
                logger.error(f"向群 {group} 推送失败：{exc}")
        return any_sent
