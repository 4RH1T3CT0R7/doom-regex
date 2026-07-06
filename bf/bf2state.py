# -*- coding: utf-8 -*-
"""BF-исходник -> начальное состояние .rvstate.

Запуск:
  py -3.11 bf/bf2state.py programs/hello.bf -o gen/hello.rvstate [--input "text"]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bf.rules_bf import make_state  # noqa: E402
from proto.driver import save_state  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--input", default="", help="строка, кладётся в IN-очередь")
    args = ap.parse_args()

    state = make_state(args.source.read_text(encoding="utf-8"),
                       args.input.encode("latin-1"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_state(args.output, state, passes=0)
    print(f"{args.output}: len {len(state)}")


if __name__ == "__main__":
    main()
