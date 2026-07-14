# -*- coding: utf-8 -*-
"""Живое демо DOOM-на-regex: движок + вьювер одним запуском.

Поднимает: (1) rvm.exe с live-экспортом в demo/web/live/,
(2) http-сервер на demo/web/, (3) открывает браузер.

Запуск:  py -3.11 demo/start_demo.py [--state path.rvstate] [--every N]
По умолчанию берёт снапшот кадра E1M1 (см. demo/README.md, где взять).
"""
from __future__ import annotations

import argparse
import http.server
import os
import subprocess
import sys
import threading
import webbrowser
from functools import partial
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = Path(os.environ.get("DOOMREGEX_OUT", "C:/dev/doom-regex-out"))
PORT = 8137


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state",
                    default=str(OUT / "run" / "g2d_fb_snapshot.rvstate"))
    ap.add_argument("--rvm", default=str(OUT / "build" / "rvm.exe"))
    ap.add_argument("--rules", default=str(ROOT / "vm" / "rules_rvm.rgxset"))
    ap.add_argument("--every", type=int, default=20,
                    help="экспорт live каждые N подстановок")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    for f, what in [(args.state, "снапшот"), (args.rvm, "rvm.exe"),
                    (args.rules, "правила")]:
        if not Path(f).exists():
            sys.exit(f"нет файла ({what}): {f}\nсм. demo/README.md")

    live = HERE / "web" / "live"
    live.mkdir(parents=True, exist_ok=True)

    # 1) движок
    eng = subprocess.Popen(
        [args.rvm, "--rules", args.rules, "--state", args.state,
         "--live-dir", str(live), "--live-every", str(args.every),
         "--trace-every", "500", "--quiet"],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    print(f"[демо] rvm.exe запущен (pid {eng.pid}), live -> {live}")

    # 2) вьювер
    handler = partial(http.server.SimpleHTTPRequestHandler,
                      directory=str(HERE / "web"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}/"
    print(f"[демо] вьювер: {url}  (Ctrl+C — остановить)")
    if not args.no_browser:
        webbrowser.open(url)

    try:
        eng.wait()
        print("[демо] машина остановилась (фикс-точка)")
        input("Enter — закрыть вьювер…")
    except KeyboardInterrupt:
        eng.terminate()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
