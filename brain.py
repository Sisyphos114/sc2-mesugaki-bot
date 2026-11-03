"""战术大脑：后台线程定时询问本地 llama-server，产出打法指令和一句垃圾话。"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request

DEFAULT_DIRECTIVE = {
    "stance": "macro",
    "attack_at_army": 30,
    "composition": "balanced",
    "expand": True,
    "say": "",
}

STANCES = {"macro", "defend", "harass", "all_in"}
COMPOSITIONS = {"zealot", "stalker", "balanced"}

SYSTEM_PROMPT = """你是《星际争霸2》里神族 AI 的战术大脑，同时扮演一个嘴上不饶人的雌小鬼。
对手是一个真人玩家，你要一边决定战术，一边用雌小鬼的口吻嘲讽他。

只输出一个 JSON 对象，不要解释，不要 markdown 代码块，不要思考过程。
字段固定为：
- stance: "macro"(稳健运营) | "defend"(龟缩防守) | "harass"(骚扰牵制) | "all_in"(全军压上)
- attack_at_army: 整数 15-100，攒够多少人口兵力才发起总攻
- composition: "zealot" | "stalker" | "balanced"
- expand: 布尔，是否去开分矿
- say: 一句不超过 30 个字的中文雌小鬼台词，纯粹的嘴炮

say 的硬性要求：只说给对手听的嘲讽，不许出现数字、兵种名、资源量、战术说明或任何解释；
不要报自己的兵力、不要说自己要做什么、不要给对手建议。

判断依据：自己兵力明显领先就压上；落后就龟缩补经济；对手视野里没兵就骚扰；钱花不出去就多造兵。

雌小鬼的口癖（每次随机混用，不要照抄同一句）：
- 开头爱用「杂鱼~」「杂鱼大人~」「笨蛋~」「诶嘿~」「哼~」
- 喜欢波浪号和叠词：「杂鱼~杂鱼~」「好弱好弱~」「就这~？」
- 得意时：「连这个都不会吗~」「大人真没用呢~」「才不是在夸你哦~」
- 被压制时嘴硬：「只是让着你而已~」「哼，运气好罢了~」「等我认真起来哦~」
- 只允许撒娇式挑衅：禁止脏话、人身威胁、色情内容，也不要提「死」字
say 每次都要换新词，别重复上一句。/no_think"""


class Brain:
    def __init__(
        self,
        url: str,
        model: str = "local",
        interval: float = 20.0,
        timeout: float = 90.0,
        enabled: bool = True,
    ) -> None:
        self.url = url
        self.model = model
        self.interval = interval
        self.timeout = timeout
        self.enabled = enabled

        self._lock = threading.Lock()
        self._state: dict | None = None
        self._directive = dict(DEFAULT_DIRECTIVE)
        self._message: str | None = None
        self._last_say = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._errors = 0
        self.last_latency: float | None = None

    def start(self) -> None:
        if not self.enabled:
            print("[brain] 已禁用，使用默认打法")
            return
        self._thread = threading.Thread(target=self._loop, name="brain", daemon=True)
        self._thread.start()
        print(f"[brain] 已启动，每 {self.interval:.0f}s 决策一次 -> {self.url}")

    def stop(self) -> None:
        self._stop.set()

    @property
    def directive(self) -> dict:
        with self._lock:
            return dict(self._directive)

    def update_state(self, state: dict) -> None:
        with self._lock:
            self._state = state

    def take_message(self) -> str | None:
        with self._lock:
            msg, self._message = self._message, None
            return msg

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            with self._lock:
                state = dict(self._state) if self._state else None
                current = dict(self._directive)
            if state is None:
                continue
            started = time.time()
            result = self._ask(state, current)
            if result is None:
                self._errors += 1
                if self._errors in (1, 10, 50):
                    print("[brain] 决策失败，继续沿用上一条指令（检查 llama-server 是否在跑）")
                continue
            self._errors = 0
            self.last_latency = time.time() - started
            with self._lock:
                self._directive = result
                say = result.get("say", "").strip()
                if say and say != self._last_say:
                    self._last_say = say
                    self._message = say
            print(
                f"[brain] {state.get('time', 0):>4.0f}s "
                f"stance={result['stance']} army>={result['attack_at_army']} "
                f"comp={result['composition']} expand={result['expand']} "
                f"({self.last_latency:.1f}s)  「{result.get('say', '')}」"
            )

    def _ask(self, state: dict, current: dict) -> dict | None:
        user = (
            "当前局势：\n"
            + json.dumps(state, ensure_ascii=False)
            + "\n上一条指令：\n"
            + json.dumps(current, ensure_ascii=False)
            + "\n输出新的指令 JSON。"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "temperature": 0.9,
            "max_tokens": 160,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        for attempt in range(2):
            body = dict(payload)
            if attempt == 0:
                body["response_format"] = {"type": "json_object"}
            text = self._post(body)
            if text is None:
                return None
            parsed = _parse(text)
            if parsed:
                return parsed
        return None

    def _post(self, body: dict) -> str | None:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload["choices"][0]["message"]["content"]
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            if self._errors == 0:
                print(f"[brain] 请求失败: {exc}")
            return None


def _parse(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    stance = str(raw.get("stance", "")).lower()
    if stance not in STANCES:
        stance = DEFAULT_DIRECTIVE["stance"]
    composition = str(raw.get("composition", "")).lower()
    if composition not in COMPOSITIONS:
        composition = DEFAULT_DIRECTIVE["composition"]
    try:
        threshold = int(raw.get("attack_at_army", DEFAULT_DIRECTIVE["attack_at_army"]))
    except (TypeError, ValueError):
        threshold = DEFAULT_DIRECTIVE["attack_at_army"]

    say = str(raw.get("say", ""))[:40]
    return {
        "stance": stance,
        "attack_at_army": max(15, min(100, threshold)),
        "composition": composition,
        "expand": bool(raw.get("expand", True)),
        "say": say,
    }
