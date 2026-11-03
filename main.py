"""入口：你 vs 本地 LLM 驱动的神族 bot。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

os.environ.setdefault("SC2PATH", r"E:\StarCraft II")

from sc2 import maps  # noqa: E402
from sc2.data import Race  # noqa: E402
from sc2.main import run_game  # noqa: E402
from sc2.player import Bot, Human  # noqa: E402

from brain import Brain  # noqa: E402
from bot import ExecutorBot  # noqa: E402

MAPS = {
    "kairos": "KairosJunctionLE",
    "kingscove": "KingsCoveLE",
    "newrepugnancy": "NewRepugnancyLE",
}
RACES = {"protoss": Race.Protoss, "terran": Race.Terran, "zerg": Race.Zerg}


def llm_alive(url: str) -> bool:
    base = url.rsplit("/v1/", 1)[0]
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=3) as resp:
            return json.loads(resp.read().decode()).get("status") == "ok"
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="和本地 LLM 打星际争霸2")
    parser.add_argument("--map", default="kairos", choices=MAPS.keys())
    parser.add_argument("--me", default="terran", choices=RACES.keys(), help="你玩什么族")
    parser.add_argument("--bot-race", default="protoss", choices=["protoss"], help="bot 目前只会神族")
    parser.add_argument("--llm-url", default="http://127.0.0.1:8080/v1/chat/completions")
    parser.add_argument("--interval", type=float, default=20.0, help="多少秒问一次大脑")
    parser.add_argument("--no-llm", action="store_true", help="不接 LLM，按默认打法跑")
    parser.add_argument("--replay", default="replays/last.SC2Replay")
    args = parser.parse_args()

    replay = Path(args.replay)
    replay.parent.mkdir(parents=True, exist_ok=True)

    if not args.no_llm and not llm_alive(args.llm_url):
        print("[main] 连不上模型服务，先双击跑 run_llm.bat，等它打印出 listening on 再开局。")
        print(f"[main] 如果你只想让 bot 用默认打法，加 --no-llm 启动。当前地址: {args.llm_url}")
        sys.exit(1)

    brain = Brain(args.llm_url, interval=args.interval, enabled=not args.no_llm)
    brain.start()

    if not args.no_llm:
        print(f"[main] 模型服务正常: {args.llm_url}")
    print(f"[main] 你 = {args.me}，对手 = {args.bot_race}，地图 = {args.map}")
    print("[main] 正在拉起两个星际客户端，稍等 10~20 秒……")

    try:
        result = run_game(
            maps.get(MAPS[args.map]),
            [
                Human(RACES[args.me], name="You"),
                Bot(RACES[args.bot_race], ExecutorBot(brain), name="LLM-Bot"),
            ],
            realtime=True,
            save_replay_as=str(replay),
        )
    finally:
        brain.stop()

    print(f"[main] 结果: {result}")
    print(f"[main] 回放: {replay.resolve()}")


if __name__ == "__main__":
    main()
