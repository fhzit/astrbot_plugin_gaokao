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
message_event = types.ModuleType("astrbot.core.message.message_event")

class Session:
    def __init__(self, platform_name):
        self.platform_name = platform_name

    @classmethod
    def from_str(cls, value):
        parts = value.split(":")
        if len(parts) != 3 or parts[1] != "GroupMessage" or not all(parts):
            raise ValueError("invalid UMO")
        return cls(parts[0])

message_event.MessageSesion = Session
for name, mod in {
    "astrbot": types.ModuleType("astrbot"),
    "astrbot.api": api,
    "astrbot.api.event": event,
    "astrbot.api.star": star,
    "astrbot.core": types.ModuleType("astrbot.core"),
    "astrbot.core.message": types.ModuleType("astrbot.core.message"),
    "astrbot.core.message.message_event": message_event,
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
    plugin = instance({"api_type":"mode","mode":"recommend","scene":"morning","source":"中文来源","category":"文学","tag":"励志"}, ContextStub())
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): return None
        def raise_for_status(self): pass
        async def json(self): return {"mode":"recommend","item":{"content":"逐梦前行"}}
    class SessionMock:
        def __init__(self): self.params=None
        async def __aenter__(self): return self
        async def __aexit__(self,*args): return None
        def get(self,url,**kwargs):
            self.params=(url,kwargs)
            return Response()
    old_session=plugin_main.aiohttp.ClientSession
    holder={}
    def factory():
        holder["session"]=SessionMock()
        return holder["session"]
    plugin_main.aiohttp.ClientSession=factory
    try:
        assert await plugin._saying()=="逐梦前行"
        url,kw=holder["session"].params
        assert url.endswith("/saying/random") and kw["params"]=={"mode":"recommend","scene":"morning","source":"中文来源","category":"文学","tag":"励志"}
    finally:
        plugin_main.aiohttp.ClientSession=old_session

async def main():
    await saying_smoke()
    base={"group_ids":"123,456","umo_prefix":"platform-A","exam_date":"2027-06-07","timezone":"Asia/Shanghai","api_type":"random"}
    for review,fail,expected_sends in [("PASS",False,2),("REJECT",False,0),("uncertain",False,0),("",True,0)]:
        ctx=ContextStub(review,fail)
        plugin=instance({**base,"llm_review_enabled":True,"llm_provider_id":"provider"},ctx)
        plugin._saying=lambda: asyncio.sleep(0,result="保持努力")
        await plugin._send_daily()
        assert len(ctx.sent)==expected_sends,(review,fail,ctx.sent)
        assert ctx.review_calls==1
        if expected_sends:
            assert ctx.sent[0][0]=="platform-A:GroupMessage:123"
            assert "高考倒计时" in ctx.sent[0][1]
    ctx=ContextStub()
    plugin=instance({**base,"llm_review_enabled":True},ctx)
    plugin._saying=lambda: asyncio.sleep(0,result="正文")
    await plugin._send_daily()
    assert not ctx.sent and not ctx.review_calls
    ctx=ContextStub()
    plugin=instance({**base,"llm_review_enabled":False},ctx)
    plugin._saying=lambda: asyncio.sleep(0,result="正文")
    await plugin._send_daily()
    assert len(ctx.sent)==2 and ctx.review_calls==0
    print("PASS: UAPIPro filter parsing; LLM pass/reject/uncertain/error gates; optional-off bypass; group sends and UMO/MessageChain shape")

asyncio.run(main())
