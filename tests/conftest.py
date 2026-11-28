"""Общие фикстуры тестов.

## Зачем этот файл

§4.6 ограничивает длину пути до сокета (100 символов). Тесты строят
`$NEMEQ_HOME` от `tmp_path`, то есть наследуют `basetemp` pytest:
`/tmp/pytest-of-user/pytest-123/test_<имя>0`.

При длинном `basetemp` (глубокий scratch внешнего ревьюера, CI, длинный
рабочий каталог) HOME перестаёт проходить preflight, и тесты падают с
отказом `socket_path_len` — хотя проверяют не путь, а digest, миграцию или
владельца файлов. Это ложное падение: красный baseline, из которого нельзя
понять, что сломано.

Замер (реальные случаи):

    /home/hermes/.h/test_home_with_space_and_non_ascii0
        71 символ  -> OK
    /home/hermes/.hermes/cache/scratch/pytest-123/test_install_writes_nothing_outside_home0
        107 символов -> ОТКАЗ (>100)

Найдено внешним ревьюером (Гермес): с коротким `--basetemp` прогон зелёный,
с длинным — красный. Значит тесты зависели от окружения, а не от кода.

## Что делает фикстура

На старте сессии проверяет фактический `basetemp`. Если путь до сокета
`<basetemp>/test_<длинное_имя>0/instances/alpha/run` не укладывается в 100
символов — переключает `basetemp` на короткий `/tmp/nqt-XXXX`. Короткий
корень сохраняет смысл тестов (временный каталог) и снимает зависимость от
окружения.

Это лечит весь набор сразу и не требует правки 12 тестов: они продолжают
пользоваться `tmp_path`, но тот становится коротким.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

# §4.6: путь до сокета обязан быть короче 100 символов.
SOCKET_PATH_LIMIT = 100

# Самый длинный суффикс, который тесты добавляют к basetemp:
# <basetemp>/<test_name>0/instances/<instance>/run
_SUFFIX_RESERVE = len("/test_" + "x" * 60 + "0/instances/alpha/run") + 1


@pytest.fixture(scope="session", autouse=True)
def _short_basetemp(tmp_path_factory):
    """Сократить basetemp, если путь до сокета не укладывается в §4.6.

    Автоиспользуемая фикстура сессии: срабатывает до первого теста. Если
    `--basetemp` задан явно и он уже короткий — ничего не меняет.
    """
    current = Path(tmp_path_factory.getbasetemp())
    # Проверяем худший случай: самый длинный суффикс теста.
    worst = len(str(current)) + _SUFFIX_RESERVE
    if worst <= SOCKET_PATH_LIMIT:
        yield
        return

    short = Path(tempfile.mkdtemp(prefix="nqt-"))
    previous = tmp_path_factory._basetemp  # noqa: SLF001 - API pytest
    tmp_path_factory._basetemp = short  # noqa: SLF001
    try:
        yield
    finally:
        tmp_path_factory._basetemp = previous  # noqa: SLF001
        # Удаляем только то, что создали сами.
        if short.exists() and short.name.startswith("nqt-"):
            shutil.rmtree(short, ignore_errors=True)
