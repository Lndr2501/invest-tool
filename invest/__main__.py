"""Kommandozeile: python -m invest <befehl>"""
from __future__ import annotations

import logging
import sys
import time


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cmd = argv[1] if len(argv) > 1 else "status"
    t0 = time.time()
    if cmd == "backfill":
        from .collect import backfill

        out = backfill()
    elif cmd == "collect":
        from .collect import collect

        out = collect()
    elif cmd == "refresh":
        from .collect import refresh

        out = refresh()
    elif cmd == "analyze":
        from .analysis import run_all

        out = run_all()
    elif cmd == "backtest":
        from .backtest import run_all

        out = run_all()
    elif cmd == "alerts":
        from .alerts import check

        out = check()
    elif cmd == "telegram-setup":
        from .alerts import telegram_setup

        out = telegram_setup()
    elif cmd == "telegram-test":
        from .alerts import telegram_test

        out = telegram_test()
    elif cmd == "report":
        from .alerts import daily_report

        out = daily_report(force=True)
    elif cmd == "bot":
        from .telegram import run_bot

        run_bot()
        return 0
    elif cmd == "web":
        import uvicorn

        host = argv[2] if len(argv) > 2 else "192.168.178.72"
        port = int(argv[3]) if len(argv) > 3 else 8501
        uvicorn.run("web.server:app", host=host, port=port, log_level="warning", proxy_headers=False)
        return 0
    elif cmd == "status":
        from .status import status

        out = status()
    else:
        print(__doc__, "\nBefehle: backfill collect refresh analyze backtest alerts report bot web telegram-setup telegram-test status")
        return 2
    for line in out:
        print(line)
    print(f"[{cmd}] fertig in {time.time() - t0:.1f}s")
    return 1 if any("FEHLER" in m for m in out) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
