"""Комплексный набор автотестов для recommend_price (тестовое задание ТОЛЬКООПТ).

Запуск через pytest:
    pytest -v test_starter.py

Запуск через стандартную библиотеку Python (без внешних зависимостей):
    python -m unittest -v test_starter.py
"""

import asyncio
from decimal import Decimal
import os
import sys
from typing import Any
import unittest

# Добавляем директорию скрипта в sys.path для корректного импорта из любого каталога
sys.path.insert(0, os.path.dirname(__file__))

from starter import recommend_price


# ─────────────────────────────────────────────────────────────────────────────
# Фабрики моков
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


def make_error_fetch(exc: Exception = ConnectionError("Сетевой сбой")):
    """Возвращает асинхронную функцию, выбрасывающую исключение."""
    async def _fetch() -> dict[str, Any]:
        raise exc
    return _fetch


# ─────────────────────────────────────────────────────────────────────────────
# Комплексный тестовый набор: штатные сценарии, сбои, математика и краевые случаи
# ─────────────────────────────────────────────────────────────────────────────

class TestRecommendPrice(unittest.IsolatedAsyncioTestCase):
    """Тестирование бизнес-правил, финансовой точности и отказоустойчивости."""

    # 1. ШТАТНЫЕ СЦЕНАРИИ
    async def test_standard_sample_case(self):
        """Штатный расчёт из условия: отсечение MYSHOP и доставки >6 дней, скидка 1.5% и APPLY."""
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

    async def test_multiple_competitors_picks_minimum(self):
        """Выбор минимального конкурента среди нескольких валидных предложений."""
        fetch = make_fetch([
            {"seller": "COMP_A", "price": Decimal("5000.00"), "delivery_days": 1},
            {"seller": "COMP_B", "price": Decimal("4600.00"), "delivery_days": 4},
            {"seller": "COMP_C", "price": Decimal("4800.00"), "delivery_days": 2},
        ])
        # min = 4600.00 * 0.985 = 4531.00
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("4700.00"),
            min_price=Decimal("4000.00"),
        )
        self.assertEqual(result["price"], Decimal("4531.00"))
        self.assertEqual(result["decision"], "APPLY")

    # 2. МАТЕМАТИКА ОКРУГЛЕНИЯ И MIN_PRICE (ПРАВИЛО 4)
    async def test_floor_rounding_down_to_kopeck(self):
        """Округление строго вниз (ROUND_FLOOR): дробные доли копейки отсекаются."""
        fetch = make_fetch([
            {"seller": "COMP", "price": Decimal("100.05"), "delivery_days": 1},
        ])
        # 100.05 * 0.985 = 98.54925 -> строго 98.54 (банковский round дал бы 98.55)
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("100.00"),
            min_price=Decimal("50.00"),
        )
        self.assertEqual(result["price"], Decimal("98.54"))
        self.assertEqual(result["decision"], "APPLY")

    async def test_min_price_floor_clamping(self):
        """Ограничение снизу: если цена конкурента -1.5% ниже min_price, возвращается min_price."""
        fetch = make_fetch([
            {"seller": "DUMPING", "price": Decimal("1000.00"), "delivery_days": 2},
        ])
        # 1000 * 0.985 = 985.00, но min_price = 1100.00 -> результат 1100.00
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("1200.00"),
            min_price=Decimal("1100.00"),
        )
        self.assertEqual(result["price"], Decimal("1100.00"))
        self.assertEqual(result["decision"], "APPLY")

    # 3. ОТКАЗОУСТОЙЧИВОСТЬ, ТАЙМАУТЫ И ОШИБКИ ИСТОЧНИКОВ (ПРАВИЛО 1)
    async def test_timeout_fallback_to_hold(self):
        """Сбой: таймаут источника возвращает текущую цену и HOLD."""
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

    async def test_exception_fallback_to_hold(self):
        """Сбой: сетевое исключение источника возвращает текущую цену и HOLD."""
        failing_fetch = make_error_fetch(RuntimeError("Connection reset by peer"))
        current = Decimal("5000.00")
        result = await recommend_price(
            fetch_offers=failing_fetch,
            current_price=current,
            min_price=Decimal("4000.00"),
        )
        self.assertEqual(result["price"], current)
        self.assertEqual(result["decision"], "HOLD")
        self.assertIn("Ошибка источника", result["reason"])

    async def test_status_partial_or_error_fallback_to_hold(self):
        """Сбой: статус ответа partial или error возвращает HOLD."""
        for bad_status in ("partial", "error", "unknown"):
            fetch = make_fetch(
                offers=[{"seller": "COMP", "price": Decimal("4000.00"), "delivery_days": 1}],
                status=bad_status,
            )
            current = Decimal("5000.00")
            result = await recommend_price(
                fetch_offers=fetch,
                current_price=current,
                min_price=Decimal("3000.00"),
            )
            self.assertEqual(result["price"], current)
            self.assertEqual(result["decision"], "HOLD")

    async def test_invalid_payload_fallback_to_hold(self):
        """Сбой: источник вернул None или пустой объект вместо словаря."""
        async def bad_payload():
            return None
        current = Decimal("5000.00")
        result = await recommend_price(
            fetch_offers=bad_payload,
            current_price=current,
            min_price=Decimal("3000.00"),
        )
        self.assertEqual(result["price"], current)
        self.assertEqual(result["decision"], "HOLD")

    # 4. ФИЛЬТРАЦИЯ МАГАЗИНА И СРОКОВ ДОСТАВКИ (ПРАВИЛО 2)
    async def test_own_shop_case_insensitive_and_whitespace(self):
        """Исключение собственного магазина в любых регистрах и с пробелами."""
        fetch = make_fetch([
            {"seller": "myshop", "price": Decimal("1000.00"), "delivery_days": 1},
            {"seller": "MYSHOP", "price": Decimal("1050.00"), "delivery_days": 2},
            {"seller": "  MyShop  ", "price": Decimal("1100.00"), "delivery_days": 3},
            {"seller": "LEGIT_COMPETITOR", "price": Decimal("2000.00"), "delivery_days": 2},
        ])
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("2100.00"),
            min_price=Decimal("1500.00"),
        )
        # Должен пройти только LEGIT_COMPETITOR (2000 * 0.985 = 1970.00)
        self.assertEqual(result["price"], Decimal("1970.00"))
        self.assertEqual(result["decision"], "APPLY")

    async def test_delivery_days_boundaries(self):
        """Границы доставки: 0 и 6 дней допускаются, 7 дней и None отсекаются."""
        fetch = make_fetch([
            {"seller": "SAME_DAY", "price": Decimal("3000.00"), "delivery_days": 0},  # валидно
            {"seller": "MAX_DAY", "price": Decimal("2500.00"), "delivery_days": 6},   # валидно (мин)
            {"seller": "LATE_DAY", "price": Decimal("2000.00"), "delivery_days": 7},  # отсекается
            {"seller": "NO_DELIVERY", "price": Decimal("1800.00"), "delivery_days": None}, # отсекается
        ])
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("2600.00"),
            min_price=Decimal("2000.00"),
        )
        # Мин. валидный — MAX_DAY за 2500.00 -> 2500 * 0.985 = 2462.50
        self.assertEqual(result["price"], Decimal("2462.50"))
        self.assertEqual(result["decision"], "APPLY")

    # 5. ОТСУТСТВИЕ ПОДХОДЯЩИХ ПРЕДЛОЖЕНИЙ (ПРАВИЛО 3)
    async def test_no_valid_offers_current_price_below_min(self):
        """Правило 3: нет предложений -> возврат current_price, даже если current_price < min_price."""
        fetch = make_fetch([
            {"seller": "MYSHOP", "price": Decimal("2000.00"), "delivery_days": 1},
            {"seller": "SLOW", "price": Decimal("2200.00"), "delivery_days": 10},
        ])
        current = Decimal("2800.00")
        min_p = Decimal("3500.00")  # текущая цена намеренно ниже минимальной
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=current,
            min_price=min_p,
        )
        self.assertEqual(result["price"], current)
        self.assertEqual(result["decision"], "HOLD")

    # 6. ПОРОГ ОТКЛОНЕНИЯ 30% И СТАТУСЫ REVIEW / APPLY (ПРАВИЛО 5)
    async def test_price_deviation_over_30_percent_down_review(self):
        """Падение цены более чем на 30% переводит решение в REVIEW."""
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

    async def test_price_deviation_over_30_percent_up_review(self):
        """Рост цены более чем на 30% переводит решение в REVIEW."""
        fetch = make_fetch([
            {"seller": "EXPENSIVE", "price": Decimal("2000.00"), "delivery_days": 1},
        ])
        # 2000 * 0.985 = 1970.00. От текущей 1000.00 это рост на 97% (> 30%)
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("1000.00"),
            min_price=Decimal("500.00"),
        )
        self.assertEqual(result["price"], Decimal("1970.00"))
        self.assertEqual(result["decision"], "REVIEW")

    async def test_boundary_exact_30_percent_decrease_apply(self):
        """Граничный случай: падение ровно на 30.0% допускается и дает APPLY."""
        fetch = make_fetch([
            {"seller": "COMP", "price": Decimal("600.00"), "delivery_days": 1},
        ])
        # Задаем min_price = 700.00. При текущей 1000.00 отклонение |700 - 1000| = 300 (ровно 30%)
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("1000.00"),
            min_price=Decimal("700.00"),
        )
        self.assertEqual(result["price"], Decimal("700.00"))
        self.assertEqual(result["decision"], "APPLY")

    async def test_boundary_exact_30_percent_increase_apply(self):
        """Граничный случай: рост ровно на 30.0% допускается и дает APPLY."""
        # Хотим результат ровно 1300.00 при текущей 1000.00
        # Для этого задаем min_price = 1300.00
        fetch = make_fetch([
            {"seller": "COMP", "price": Decimal("1000.00"), "delivery_days": 1},
        ])
        result = await recommend_price(
            fetch_offers=fetch,
            current_price=Decimal("1000.00"),
            min_price=Decimal("1300.00"),
        )
        self.assertEqual(result["price"], Decimal("1300.00"))
        self.assertEqual(result["decision"], "APPLY")


if __name__ == "__main__":
    unittest.main()
