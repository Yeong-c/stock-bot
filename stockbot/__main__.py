"""실행 진입점.

python -m stockbot              봇 실행
python -m stockbot scan         검색식 2~6 즉시 실행 (텔레그램 없이 화면 출력)
python -m stockbot check        설정·네트워크·키움 연결 점검
python -m stockbot baseline 005930   분봉 기준값 생성 테스트
python -m stockbot universe     유니버스 통계
python -m stockbot update [check|apply]   업데이트 확인/적용
"""
from __future__ import annotations

import sys

from .config import CONFIG_PATH, kiwoom_configured, load_config, telegram_configured
from .log import setup_logging
from .state import State


LOCK_PORT = 47321  # 단일 실행 잠금용 (localhost 전용)


def acquire_single_instance_lock():
    """같은 PC 에서 봇이 두 개 뜨는 것을 막는다. 성공 시 소켓 반환, 실패 시 None."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    log = setup_logging()
    cfg = load_config()
    cmd = argv[0] if argv else "run"

    if cmd == "run":
        if not telegram_configured(cfg):
            print(f"[오류] {CONFIG_PATH} 의 telegram.bot_token 을 먼저 넣어주세요.")
            return 2
        lock = acquire_single_instance_lock()
        if lock is None:
            print("[안내] 봇이 이미 실행 중입니다. (같은 봇을 두 개 켜면 텔레그램이 충돌합니다)")
            return 3
        from .bot import StockBot
        bot = StockBot(cfg, State())
        try:
            bot.run()
        finally:
            lock.close()
        return bot.exit_code  # 10 = 업데이트 후 재시작 요청

    if cmd == "scan":
        from . import formatting as F
        from .screeners.runner import run_daily_scan
        out = run_daily_scan(cfg, progress=print)
        for m in F.format_daily_results(out, int(cfg["output"].get("max_per_screener", 30))):
            print("\n" + "=" * 60)
            print(m)
        return 0

    if cmd == "check":
        ok = True
        print("설정 파일:", CONFIG_PATH)
        print("텔레그램 토큰:", "설정됨" if telegram_configured(cfg) else "❌ 미설정")
        ok &= telegram_configured(cfg)
        try:
            from .data import naver
            rt = naver.fetch_realtime(["005930"])
            print("네이버 실시간:", "OK" if rt.get("005930") else "❌ 응답 없음", rt.get("005930", {}).get("price"))
        except Exception as e:  # noqa: BLE001
            print("네이버 실시간: ❌", e); ok = False
        try:
            from .data.universe import fetch_hwangi_names, fetch_listing
            print("상장목록:", len(fetch_listing()), "종목")
            print("환기종목:", len(fetch_hwangi_names()), "종목")
        except Exception as e:  # noqa: BLE001
            print("상장목록/환기종목: ❌", e); ok = False
        if kiwoom_configured(cfg):
            from .data.kiwoom import KiwoomClient
            k = cfg["kiwoom"]
            try:
                print("키움 API:", KiwoomClient(k["app_key"], k["app_secret"], bool(k.get("is_mock"))).check())
            except Exception as e:  # noqa: BLE001
                print("키움 API: ❌", e)
        else:
            print("키움 API: 미설정 (분봉 기준값은 네이버 7거래일치 사용)")
        if telegram_configured(cfg):
            try:
                import asyncio
                from telegram import Bot
                me = asyncio.run(Bot(cfg["telegram"]["bot_token"]).get_me())
                print("텔레그램 봇:", f"@{me.username} OK")
            except Exception as e:  # noqa: BLE001
                print("텔레그램 봇: ❌", e); ok = False
        print("\n결과:", "모두 정상" if ok else "문제 있음 (위 ❌ 확인)")
        return 0 if ok else 1

    if cmd == "baseline":
        code = (argv[1] if len(argv) > 1 else "005930").zfill(6)
        from .data.kiwoom import KiwoomClient
        from .screeners.minute import MinuteMonitor
        kiwoom = None
        if kiwoom_configured(cfg):
            k = cfg["kiwoom"]
            kiwoom = KiwoomClient(k["app_key"], k["app_secret"], bool(k.get("is_mock")))
        mon = MinuteMonitor(cfg, State(), kiwoom)
        print(mon.build_baseline(code, force=True, progress=print))
        return 0

    if cmd == "update":
        from . import updater
        sub = argv[1] if len(argv) > 1 else "check"
        print("현재 버전:", updater.current_version())
        if sub == "apply":
            print(updater.apply(cfg, progress=print))
        else:
            print(updater.check(cfg))
        return 0

    if cmd == "universe":
        from .data.universe import build_universe
        df, meta = build_universe(cfg)
        print(meta)
        print(df.head(10).to_string())
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
