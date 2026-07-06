# -*- coding: utf-8 -*-
"""Генерирует bf/rules_bf.rgxset из rules_bf.RULES (+ SHA-256 в заголовке).

Формат .rgxset v1 (LF, UTF-8):
    #rgxset/1
    #sha256:<hex хэша тела правил>
    #rule <имя>
    P:<паттерн>
    R:<replacement (PCRE2 ${n})>
    ...
Запуск:  py -3.11 bf/gen_rules.py
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bf.rules_bf import RULES  # noqa: E402


def render_body(rules) -> str:
    lines = []
    for name, pat, repl in rules:
        assert "\n" not in pat and "\n" not in repl, name
        lines.append(f"#rule {name}")
        lines.append(f"P:{pat}")
        lines.append(f"R:{repl}")
    return "\n".join(lines) + "\n"


def main() -> None:
    body = render_body(RULES)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    out = Path(__file__).with_name("rules_bf.rgxset")
    out.write_bytes(
        (f"#rgxset/1\n#sha256:{digest}\n" + body).encode("utf-8")
    )
    print(f"{out} : {len(RULES)} rules, sha256={digest[:16]}…")


if __name__ == "__main__":
    main()
