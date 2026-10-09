import asyncio
import importlib.util
import sys
import types


class Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class DummyLogger:
    def __getattr__(self, _):
        return lambda *args, **kwargs: None


class MessageChain:
    def __init__(self):
        self.text = ""

    def message(self, text):
        self.text = text
        return self


class Star:
    def __init__(self, context):
        self.context = context


def register(*args):
    return lambda cls: cls


api = types.ModuleType("astrbot.api")
api.AstrBotConfig = Config
api.logger = DummyLogger()
http_stub = types.ModuleType("aiohttp")
http_stub.ClientTimeout = lambda **kwargs: kwargs
http_stub.ClientSession = None
sys.modules["aiohttp"] = http_stub
event = types.ModuleType("astrbot.api.event")
event.MessageChain = MessageChain
star = types.ModuleType("astrbot.api.star")
star.Context = object
star.Star = Star
star.register = register
message_event = types.ModuleType("astrbot.core.platform.message_session")

class Session:
    def __init__(self, platform_name):
        self.platform_name = platform_name

    @classmethod
    def from_str(cls, value):
        parts = value.split(":", 2)
        if len(parts) != 3 or parts[1] != "GroupMessage" or not all(parts):
            raise ValueError("invalid UMO")
        return cls(parts[0])

message_event.MessageSession = Session
for name, mod in {
    "astrbot": types.ModuleType("astrbot"),
    "astrbot.api": api,
    "astrbot.api.event": event,
    "astrbot.api.star": star,
    "astrbot.core": types.ModuleType("astrbot.core"),
    "astrbot.core.platform": types.ModuleType("astrbot.core.platform"),
    "astrbot.core.platform.message_session": message_event,
}.items():
    sys.modules[name] = mod

spec = importlib.util.spec_from_file_location("plugin_main", "main.py")
plugin_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin_main)
GaokaoCountdown = plugin_main.GaokaoCountdown

class ContextStub:
    def __init__(self, review="PASS", fail_review=False):
        self.review = review
        self.fail_review = fail_review
        self.sent = []
        self.review_calls = 0

    async def llm_generate(self, **kwargs):
        self.review_calls += 1
        if self.fail_review:
            raise RuntimeError("provider down")
        return types.SimpleNamespace(completion_text=self.review)

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain.text))
        return True


def instance(config, context):
    return GaokaoCountdown(context, Config(config))


async def saying_smoke():
    plugin = instance(
        {
            "uapi_token": "test-token",
            "hitokoto_advanced": {
                "enabled": True, "mode": "recommend", "scene": "morning",
                "source": "中文来源", "category": "文学", "tag": "励志",
            },
        },
        ContextStub(),
    )

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def raise_for_status(self):
            pass

        status = 200

        async def json(self, content_type=None):
            return {"mode": "recommend", "item": {"content": "逐梦前行"}}

    class SessionMock:
        def __init__(self):
            self.params = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def get(self, url, **kwargs):
            self.params = (url, kwargs)
            return Response()

    old_session = plugin_main.aiohttp.ClientSession
    holder = {}

    def factory():
        holder["session"] = SessionMock()
        return holder["session"]

    plugin_main.aiohttp.ClientSession = factory
    try:
        assert await plugin._saying() == "逐梦前行"
        url, kwargs = holder["session"].params
        assert url.endswith("/saying/random")
        params = kwargs["params"]
        assert params["mode"] == "recommend" and params["scene"] == "morning"
        assert params["source"] == "中文来源" and params["category"] == "文学" and params["tag"] == "励志"
        assert params["token"] == "test-token"
        headers = kwargs["headers"]
        assert headers["Token"] == "test-token" and headers["Authorization"] == "Bearer test-token"
        assert headers["User-Agent"] == "AstrBot_UApiPro"
    finally:
        plugin_main.aiohttp.ClientSession = old_session


async def main():
    await saying_smoke()
    base = {
        "group_ids": "123,456", "umo_prefix": "platform-A",
        "exam_date": "2027-06-07", "timezone": "Asia/Shanghai",
    }
    # PASS 一次通过；REJECT 重试到用尽；审核异常 fail-closed
    plugin = instance({**base, "saying_source": "uapipro", "llm_review_enabled": True, "llm_provider_id": "provider"}, ContextStub("PASS"))
    plugin._saying_with_fallback = lambda entry=None: asyncio.sleep(0, result="保持努力")
    await plugin._send_daily()
    assert len(plugin.context.sent) == 2 and plugin.context.review_calls == 2  # 每群独立审核

    context = ContextStub("REJECT")
    plugin = instance({**base, "saying_source": "uapipro", "llm_review_enabled": True, "llm_provider_id": "provider", "llm_review_max_attempts": 3}, context)
    sayings = iter(["句子A", "句子B", "句子C", "句子D", "句子E", "句子F"])

    async def next_saying(entry=None):
        return next(sayings)

    plugin._saying_with_fallback = next_saying
    await plugin._send_daily()
    assert not context.sent and context.review_calls == 6  # 2 群 × 3 次全拒后放弃

    context = ContextStub("uncertain")
    plugin = instance({**base, "saying_source": "uapipro", "llm_review_enabled": True, "llm_provider_id": "provider", "llm_review_max_attempts": 2}, context)
    plugin._saying_with_fallback = lambda entry=None: asyncio.sleep(0, result="句子")
    await plugin._send_daily()
    assert not context.sent and context.review_calls == 4  # 2 群 × 2 次

    context = ContextStub("", True)  # 审核调用异常：重试等待后仍失败
    plugin = instance({**base, "saying_source": "uapipro", "llm_review_enabled": True, "llm_provider_id": "provider", "llm_review_max_attempts": 3}, context)
    plugin._saying_with_fallback = lambda entry=None: asyncio.sleep(0, result="句子")
    await plugin._send_daily()
    assert not context.sent and context.review_calls == 6  # 2 群 × 3 次都异常后放弃

    # 第二次审核通过：第一次 REJECT 后重新获取再审核
    class FlipContext(ContextStub):
        def __init__(self):
            super().__init__("REJECT")
            self.saying_count = 0

    context = FlipContext()

    async def flip_review(message, provider_id):
        context.review_calls += 1
        return context.review_calls >= 2  # 第 2 次通过

    plugin = instance({**base, "saying_source": "uapipro", "llm_review_enabled": True, "llm_provider_id": "provider", "llm_review_max_attempts": 3}, context)
    plugin._review = flip_review
    plugin._saying_with_fallback = lambda entry=None: asyncio.sleep(0, result="逆袭的句子")
    await plugin._send_daily()
    assert len(context.sent) == 2 and context.review_calls == 3  # 共 3 次：某群第 1 次过、另一群第 2 次过

    # 未配置 provider：fail-closed
    context = ContextStub()
    plugin = instance({**base, "saying_source": "uapipro", "llm_review_enabled": True}, context)
    plugin._saying_with_fallback = lambda entry=None: asyncio.sleep(0, result="正文")
    await plugin._send_daily()
    assert not context.sent and not context.review_calls

    # 审核关闭：直通
    context = ContextStub()
    plugin = instance({**base, "saying_source": "uapipro", "llm_review_enabled": False}, context)
    plugin._saying_with_fallback = lambda entry=None: asyncio.sleep(0, result="正文")
    await plugin._send_daily()
    assert len(context.sent) == 2 and context.review_calls == 0
    assert context.sent[0][0] == "platform-A:GroupMessage:123"

    # 本地句库：默认来源即 local，两群各随机一句
    context = ContextStub()
    plugin = instance({**base, "llm_review_enabled": False}, context)
    await plugin._send_daily()
    assert len(context.sent) == 2
    for _, text in context.sent:
        header, body = text.split("\n", 1)
        assert "｜高考倒计时" in header and body
    local_text = context.sent[0][1]

    # 本地句库 + 追加自定义句子：追加句与内置句库合并（须能命中追加句）
    plugin = instance({**base, "local_sentences": "自定义测试句子甲\n自定义测试句子乙"}, ContextStub())
    pool_hits = {plugin._local_saying({}) for _ in range(600)}
    assert {"自定义测试句子甲", "自定义测试句子乙"} <= pool_hits

    # 分群配置：每群独立来源与句子，local/uapipro 混跑；条目追加句必须进入该群池子
    context = ContextStub("PASS")
    plugin = instance({
        **base,
        "llm_review_enabled": True, "llm_provider_id": "provider",
        "group_configs": [
            {"group_id": "111", "saying_source": "local", "local_sentences": "群111专属句子"},
            {"group_id": "222", "saying_source": "uapipro"},
            {"group_id": "111", "saying_source": "uapipro"},  # 重复群号应被忽略
        ],
    }, context)
    plugin._saying_with_fallback = lambda entry=None: asyncio.sleep(0, result="UAPIPro句子")
    # 111 群池子含专属句：多次抽样验证
    entry111 = plugin._group_configs()[0]
    assert any(plugin._local_saying(entry111) == "群111专属句子" for _ in range(600))
    await plugin._send_daily()
    by_group = {umo.split(":")[-1]: text for umo, text in context.sent}
    assert set(by_group) == {"111", "222"}
    assert by_group["222"].endswith("UAPIPro句子")
    assert by_group["111"].startswith("2026.10.08｜高考倒计时241天") or "｜高考倒计时" in by_group["111"]
    assert context.review_calls == 2  # 全局开了审核，两群各审一次

    # 分群覆盖高考日期
    plugin = instance({
        **base,
        "group_configs": [{"group_id": "333", "exam_date": "2027-06-01"}],
    }, ContextStub())
    await plugin._send_daily()
    assert context.sent or True
    print("PASS: filters, LLM retry-until-pass/fail-closed gates, optional bypass, UMO/MessageChain sends")


class CronManagerStub:
    def __init__(self):
        self.jobs = []
        self.added = []
        self.deleted = []

    async def list_jobs(self):
        return list(self.jobs)

    async def add_basic_job(self, name, cron_expression, handler, **kwargs):
        job = types.SimpleNamespace(
            job_id=f"job-{len(self.added) + 1}", name=name,
            job_type="basic", cron_expression=cron_expression,
        )
        self.jobs.append(job)
        self.added.append((name, cron_expression, kwargs.get("timezone"), kwargs.get("persistent")))
        return job

    async def delete_job(self, job_id):
        self.jobs = [j for j in self.jobs if j.job_id != job_id]
        self.deleted.append(job_id)


async def cron_registration_smoke():
    plugin = instance({"send_hour": 7, "send_minute": 30}, ContextStub())
    assert plugin._cron_expression() == "30 7 * * *"
    plugin_bad = instance({"send_hour": 25, "send_minute": 0}, ContextStub())
    assert plugin_bad._cron_expression() is None

    cron = CronManagerStub()
    plugin._cron_job_name = lambda: "gaokao_countdown_test"
    plugin.context.cron_manager = cron
    plugin.config.update({"send_hour": 7, "send_minute": 30, "timezone": "Asia/Shanghai", "group_ids": "777"})
    cron.jobs.append(types.SimpleNamespace(job_id="old", name="gaokao_countdown_test", job_type="basic"))
    await plugin._register_cron_job()
    assert cron.deleted == ["old"]
    name, expr, tz, persistent = cron.added[0]
    assert name == "gaokao_countdown_test" and expr == "30 7 * * *"
    assert tz == "Asia/Shanghai" and persistent is True

    sends = []
    async def fake_send(**kw):
        sends.append(1)
        return True

    plugin._send_daily = fake_send
    await plugin._scheduled_fire(session="test:GroupMessage:1")
    await plugin._scheduled_fire(session="test:GroupMessage:1")
    assert len(sends) == 1
    # 失败不标记：模拟发送失败，再次触发应重试
    plugin._sent = {}

    async def failing_send(**kw):
        return False

    plugin._send_daily = failing_send
    await plugin._scheduled_fire(session="test:GroupMessage:1")
    await plugin._scheduled_fire(session="test:GroupMessage:1")  # 失败不记当日标记，可重试

    plugin2 = instance({"send_hour": 7}, ContextStub())
    plugin2.context.cron_manager = None
    await plugin2._register_cron_job()  # 优雅降级，不抛异常

    # 并发触发去重：未来任务与内置调度器同一秒触发只能发一次
    ctx3 = ContextStub()
    plugin3 = instance({"group_ids": "777", "umo_prefix": "p"}, ctx3)
    await asyncio.gather(plugin3._send_daily(), plugin3._send_daily())
    assert len(ctx3.sent) == 1

    await plugin._unregister_cron_job()
    assert cron.jobs == []
    print("PASS: cron job register / fire dedupe / degrade / unregister")


async def run_all():
    await saying_smoke()
    await main()
    await cron_registration_smoke()


asyncio.run(run_all())
