# -*- coding: utf-8 -*-
"""Тестовый профиль зон v2.0: малые #N/#P — боевые константы дают
58.7МБ-состояния и правила с гигантскими прыжками, которые python-regex
компилирует в ~14ГБ RSS. Выставляется ДО любых импортов vm.isa
(conftest pytest импортирует раньше тестовых модулей). Боевой профиль
покрывается изолированными property-смоуками через rvm.exe (PCRE2)."""
import os
import sys
from pathlib import Path

os.environ.setdefault("RVM_TEST_PROFILE", "small")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
