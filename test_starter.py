"""Тесты для функции recommend_price (тестовое задание ТОЛЬКООПТ).

Запуск через pytest:
    pytest -v test_starter.py

Запуск через стандартную библиотеку Python (без установки зависимостей):
    python -m unittest -v test_starter.py
"""

import asyncio
from decimal import Decimal
import os
import sys
from typing import Any
import unittest

# Добавляем директорию скрипта в sys.path для корректного импорта из любой папки
sys.path.insert(0, os.path.dirname(__file__))

from starter import recommend_price


# ─────────────────────────────────────────────────────────────────────────────
# Фабрики моков (удобные хелперы для генерации изолированных ответов)
# ─────────────────────────────────────────────────────────────────────────────

def make_fetch(offers: list[dict], status: str = "success"):
    """Возвращает асинхронную функцию с заданным статусом и списком предложений."""
    async def _fetch() -> dict[str, Any]:
        return {"status": status, "offers": offers}
    return _fetch


def make_timeout_fetch(delay: float = 0.5):
    """Возвращает асинхронную функцию, зависающую дольше заданного таймаута."""
    async def _fetch() -> dict[str, Any]:
        await asyncio.sleep(delay)
        return {"status": "success", "offers": []}
    return _fetch


def make_error_fetch(exc: Exception = ConnectionError("Источник недоступен")):
    """Возвращает асинхронную функцию, выбрасывающую исключение."""
    async def _fetch() -> dict[str, Any]:
        raise exc
    return _fetch


# ─────────────────────────────────────────────────────────────────────────────
# Тестовый набор
# ─────────────────────────────────────────────────────────────────────────────

class TestRecommendPrice(unittest.IsolatedAsyncioTestCase):
    """Полный набор проверок: штатная логика, сбои источников и граничные условия."""

    async def test_standard_sample_case(self):
        """Штатный расчёт из примера: отсечение MYSHOP и долгой доставки, скидка 1.5% и APPLY."""
        fetch = make_fetch([
            {"seller": "myshop", "price": Decimal("4300.00"), "delivery_days": 2},
            {"seller": "PARTS", "price": Decimal("4800.00"), "delivery_days": 3},
            {"seller": "SLOW", "price": Decimal("4700.00"), "delivery_days": 8},
        ])
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("4900.00"),
            min_price=Decimal("4500.00"),
        )
        self.assertEqual(result["price"], Decimal("4728.00"))
        self.assertEqual(result["decision"], "APPLY")
        self.assertIn("успешно", result["reason"])

    async def test_min_price_clamping(self):
        """Граничный случай: расчётная цена конкурента ниже min_price подтягивается до min_price."""
        fetch = make_fetch([
            {"seller": "CHEAP", "price": Decimal("1000.00"), "delivery_days": 2},
        ])
        # 1000 * 0.985 = 985.00, но min_price = 1100.00 -> итоговая цена 1100.00
        # Отклонение: |1100 - 1200| / 1200 = 8.3% <= 30% -> APPLY
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("1200.00"),
            min_price=Decimal("1100.00"),
        )
        self.assertEqual(result["price"], Decimal("1100.00"))
        self.assertEqual(result["decision"], "APPLY")

    async def test_floor_rounding_to_kopeck(self):
        """Округление вниз до копейки (ROUND_FLOOR): дробные доли копейки усекаются."""
        fetch = make_fetch([
            {"seller": "COMP", "price": Decimal("100.05"), "delivery_days": 1},
        ])
        # 100.05 * 0.985 = 98.54925 -> при округлении вниз должно быть ровно 98.54 (а не 98.55)
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("100.00"),
            min_price=Decimal("50.00"),
        )
        self.assertEqual(result["price"], Decimal("98.54"))
        self.assertEqual(result["decision"], "APPLY")

    async def test_timeout_fallback_to_hold(self):
        """Сбой: превышение таймаута источника возвращает текущую цену и статус HOLD."""
        slow_fetch = make_timeout_fetch(delay=0.5)
        current = Decimal("5000.00")
        result = await recommend_price(
            fetch_offers=slow_fetch,
            current_price=current,
            min_price=Decimal("4000.00"),
            timeout_s=0.05,
        )
        self.assertEqual(result["price"], current)
        self.assertEqual(result["decision"], "HOLD")
        self.assertIn("таймаут", result["reason"].lower())

    async def test_source_exception_fallback_to_hold(self):
        """Сбой: сетевое исключение при запросе возвращает текущую цену и статус HOLD."""
        failing_fetch = make_error_fetch(ConnectionResetError("Соединение разорвано"))
        current = Decimal("5000.00")
        result = await recommend_price(
            fetch_offers=failing_fetch,
            current_price=current,
            min_price=Decimal("4000.00"),
        )
        self.assertEqual(result["price"], current)
        self.assertEqual(result["decision"], "HOLD")
        self.assertIn("Ошибка источника", result["reason"])

    async def test_non_success_status_hold(self):
        """Сбой: статус ответа partial или error возвращает статус HOLD."""
        partial_fetch = make_fetch(
            offers=[{"seller": "COMP", "price": Decimal("4000.00"), "delivery_days": 1}],
            status="partial",
        )
        current = Decimal("5000.00")
        result = await recommend_price(
            fetch_offers=partial_fetch,
            current_price=current,
            min_price=Decimal("3000.00"),
        )
        self.assertEqual(result["price"], current)
        self.assertEqual(result["decision"], "HOLD")
        self.assertIn("отличен от success", result["reason"])

    async def test_no_valid_offers_preserves_current_price_below_min(self):
        """Правило 3: нет подходящих предложений -> возврат current_price, даже если она < min_price."""
        fetch = make_fetch([
            {"seller": "MYSHOP", "price": Decimal("2000.00"), "delivery_days": 1},
            {"seller": "SLOW", "price": Decimal("2500.00"), "delivery_days": 8},
        ])
        current = Decimal("2800.00")
        min_p = Decimal("3000.00")  # текущая цена ниже минимума
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=current,
            min_price=min_p,
        )
        self.assertEqual(result["price"], current)
        self.assertEqual(result["decision"], "HOLD")

    async def test_own_shop_case_insensitive_exclusion(self):
        """Фильтрация: собственный магазин исключается независимо от регистра и пробелов."""
        fetch = make_fetch([
            {"seller": " MyShop ", "price": Decimal("1000.00"), "delivery_days": 1},
            {"seller": "MYSHOP", "price": Decimal("1100.00"), "delivery_days": 2},
            {"seller": "COMP", "price": Decimal("1500.00"), "delivery_days": 3},
        ])
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("1600.00"),
            min_price=Decimal("1000.00"),
        )
        # Единственный валидный конкурент — COMP (1500 * 0.985 = 1477.50)
        self.assertEqual(result["price"], Decimal("1477.50"))
        self.assertEqual(result["decision"], "APPLY")

    async def test_delivery_days_boundary(self):
        """Фильтрация доставки: ровно 6 дней включается, 7 дней и None отсекаются."""
        fetch = make_fetch([
            {"seller": "UNKNOWN", "price": Decimal("1000.00"), "delivery_days": None},
            {"seller": "DAY_7", "price": Decimal("1100.00"), "delivery_days": 7},
            {"seller": "DAY_6", "price": Decimal("2000.00"), "delivery_days": 6},
        ])
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("2100.00"),
            min_price=Decimal("1000.00"),
        )
        # Должен пройти только DAY_6 (2000 * 0.985 = 1970.00)
        self.assertEqual(result["price"], Decimal("1970.00"))
        self.assertEqual(result["decision"], "APPLY")

    async def test_price_deviation_over_30_percent_requires_review(self):
        """Бизнес-правило: отклонение более 30% переводит решение в REVIEW."""
        fetch = make_fetch([
            {"seller": "COMP", "price": Decimal("600.00"), "delivery_days": 1},
        ])
        # 600 * 0.985 = 591.00. От текущей 1000.00 это падение на 40.9% (> 30%)
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("1000.00"),
            min_price=Decimal("500.00"),
        )
        self.assertEqual(result["price"], Decimal("591.00"))
        self.assertEqual(result["decision"], "REVIEW")
        self.assertIn("ручная проверка", result["reason"])

    async def test_boundary_exact_30_percent_is_applied(self):
        """Граничный случай: отклонение ровно на 30% допускается и дает APPLY."""
        fetch = make_fetch([
            {"seller": "COMP", "price": Decimal("600.00"), "delivery_days": 1},
        ])
        # Текущая 1000.00. Задаем min_price = 700.00 (ровно -30%).
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("1000.00"),
            min_price=Decimal("700.00"),
        )
        self.assertEqual(result["price"], Decimal("700.00"))
        self.assertEqual(result["decision"], "APPLY")


if __name__ == "__main__":
    unittest.main()
