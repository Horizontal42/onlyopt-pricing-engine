"""Учебный фрагмент для тестового задания ТОЛЬКООПТ.

Python 3.10+, только стандартная библиотека. Запуск примера: python starter.py
"""

import asyncio
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Awaitable, Callable

DISCOUNT_FACTOR = Decimal("0.985")       # скидка 1.5%
MAX_PRICE_DEVIATION = Decimal("0.30")    # допустимый порог отклонения 30%
MAX_DELIVERY_DAYS = 6                    # максимальный срок доставки
OWN_SHOP_NAME = "myshop"                 # идентификатор собственного магазина
CENT = Decimal("0.01")


async def recommend_price(
    fetch_offers: Callable[[], Awaitable[dict[str, Any]]],
    current_price: Decimal,
    min_price: Decimal,
    timeout_s: float = 2.0,
) -> dict[str, Any]:
    """Рекомендует цену на основе предложений конкурентов в соответствии с бизнес-правилами."""
    curr_p = Decimal(str(current_price)).quantize(CENT)
    min_p = Decimal(str(min_price)).quantize(CENT)

    # 1. Запрос к источнику с таймаутом и обработкой ошибок
    try:
        response = await asyncio.wait_for(fetch_offers(), timeout=timeout_s)
    except (TimeoutError, asyncio.TimeoutError):
        return {
            "price": curr_p,
            "decision": "HOLD",
            "reason": "Превышен таймаут ожидания данных от источника",
        }
    except Exception as exc:
        return {
            "price": curr_p,
            "decision": "HOLD",
            "reason": f"Ошибка источника данных: {exc}",
        }

    if not isinstance(response, dict) or response.get("status") != "success":
        status = response.get("status") if isinstance(response, dict) else "invalid_payload"
        return {
            "price": curr_p,
            "decision": "HOLD",
            "reason": f"Статус ответа источника отличен от success: {status}",
        }

    # 2. Фильтрация: известная доставка <= 6 дней, исключение собственного магазина
    raw_offers = response.get("offers")
    if not isinstance(raw_offers, list):
        raw_offers = []

    valid_offers = [
        offer
        for offer in raw_offers
        if isinstance(offer, dict)
        and offer.get("delivery_days") is not None
        and 0 <= offer["delivery_days"] <= MAX_DELIVERY_DAYS
        and str(offer.get("seller", "")).strip().casefold() != OWN_SHOP_NAME
        and offer.get("price") is not None
    ]

    # 3. Если подходящих предложений нет — сохраняем current_price и HOLD
    if not valid_offers:
        return {
            "price": curr_p,
            "decision": "HOLD",
            "reason": "Подходящие предложения конкурентов не найдены",
        }

    # 4. Расчет: минимальная цена -1.5%, округление вниз до копейки, clamp по min_price
    min_competitor_price = min(Decimal(str(offer["price"])) for offer in valid_offers)
    discounted_price = (min_competitor_price * DISCOUNT_FACTOR).quantize(
        CENT, rounding=ROUND_FLOOR
    )
    recommended_price = max(discounted_price, min_p)

    # 5. Проверка отклонения от текущей цены (> 30% -> REVIEW, <= 30% -> APPLY)
    price_diff = abs(recommended_price - curr_p)
    max_allowed_diff = curr_p * MAX_PRICE_DEVIATION

    if price_diff > max_allowed_diff:
        return {
            "price": recommended_price,
            "decision": "REVIEW",
            "reason": "Отклонение цены превышает 30% от текущей (требуется ручная проверка)",
        }

    return {
        "price": recommended_price,
        "decision": "APPLY",
        "reason": "Цена успешно рассчитана на основе предложений конкурентов",
    }


async def sample_fetch_offers() -> dict[str, Any]:
    return {
        "status": "success",
        "offers": [
            {"seller": "myshop", "price": Decimal("4300.00"), "delivery_days": 2},
            {"seller": "PARTS", "price": Decimal("4800.00"), "delivery_days": 3},
            {"seller": "SLOW", "price": Decimal("4700.00"), "delivery_days": 8},
        ],
    }


if __name__ == "__main__":
    print(
        asyncio.run(
            recommend_price(
                sample_fetch_offers,
                current_price=Decimal("4900.00"),
                min_price=Decimal("4500.00"),
            )
        )
    )
