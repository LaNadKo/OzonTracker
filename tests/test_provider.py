from datetime import timezone

import pytest

from ozon_tracker_bot.provider import (
    ProviderError,
    _FALLBACK_CHROME_MAJOR,
    _build_chrome_ua_override,
    _build_tracking_link,
    _detect_chrome_major,
    _raise_for_ozon_access_block,
    parse_ozon_page_text,
    parse_tracking_payload,
)


def test_parse_generic_payload_and_latest_event() -> None:
    snapshot = parse_tracking_payload(
        {
            "data": {
                "status": "В пути",
                "status_code": "in_transit",
                "events": [
                    {
                        "date": "2026-09-12T10:00:00Z",
                        "message": "Покинул сортировочный центр",
                        "location": "Москва",
                        "courier": {"name": "Ozon Доставка"},
                    },
                    {"date": "2026-09-11T10:00:00Z", "message": "Принято перевозчиком"},
                ],
                "tracking_url": "https://tracker.example/ABC",
            }
        },
        "ABC-123",
    )

    assert snapshot.status == "В пути"
    assert snapshot.status_code == "in_transit"
    assert snapshot.delivered is False
    assert snapshot.latest_event is not None
    assert snapshot.latest_event.text == "Покинул сортировочный центр"
    assert snapshot.latest_event.location == "Москва"
    assert snapshot.latest_event.courier == "Ozon Доставка"
    assert snapshot.latest_event.event_at is not None
    assert snapshot.latest_event.event_at.tzinfo == timezone.utc
    assert snapshot.tracking_url == "https://tracker.example/ABC"


def test_parse_delivered_payload_without_explicit_boolean() -> None:
    snapshot = parse_tracking_payload(
        {"status": "Заказ доставлен", "events": [{"status_code": "delivered"}]},
        "ABC-123",
    )

    assert snapshot.delivered is True


def test_waiting_result_has_stable_status() -> None:
    snapshot = parse_tracking_payload({"result": "waiting", "data": {}}, "ABC-123")

    assert snapshot.status == "Ожидание данных"


def test_parse_ozon_page_route_and_current_status() -> None:
    snapshot = parse_ozon_page_text(
        """
        OZON Track
        Проверка статуса вашей доставки
        12345678-0001-1
        Отследить
        Ожидаемая дата доставки
        с 15.09.26 до 21.09.26
        Создан
        11.09.26, 22:36
        Мы получили заказ, продавец уже собирает его
        Передается в доставку
        12.09.26, 09:11
        Продавец собрал заказ и передаёт его в доставку. Обычно это занимает до 10 дней
        В пути
        15.09.26 - 21.09.26
        Показать меньше
        Заказ принят перевозчиком
        12.09.26, 23:50
        Он отвезёт заказ на таможню. Товары пройдут таможенное оформление в стране отправления и в стране назначения.
        Заказ везут на таможню в стране отправления
        Обычно это занимает до 10 дней
        Заказ проходит импортное таможенное оформление
        Скорость оформления зависит от загруженности таможни
        Заказ в пункте выдачи
        15.09.26 - 21.09.26
        Заказ получен в пункте выдачи
        © 1998 – 2026 ООО «Интернет Решения»
        """,
        "12345678-0001-1",
    )

    assert snapshot.status == "В пути"
    assert snapshot.status_code == "in_transit"
    assert snapshot.delivered is False
    assert snapshot.tracking_url == "https://tracking.ozon.ru/?track=12345678-0001-1"
    assert snapshot.latest_event is not None
    assert snapshot.latest_event.status == "В пути"
    assert snapshot.latest_event.event_at is None
    assert any(event.status == "Заказ принят перевозчиком" for event in snapshot.events)
    assert any(event.status == "Заказ проходит импортное таможенное оформление" for event in snapshot.events)


def test_parse_ozon_page_orders_events_chronologically() -> None:
    snapshot = parse_ozon_page_text(
        """
        OZON Track
        Проверка статуса вашей доставки
        12345678-0001-1
        Отследить
        Ожидаемая дата доставки
        с 15.09.26 до 21.09.26
        Создан
        11.09.26, 22:36
        Мы получили заказ, продавец уже собирает его
        Передается в доставку
        12.09.26, 09:11
        Продавец собрал заказ и передаёт его в доставку
        В пути
        15.09.26 - 21.09.26
        Показать больше
        Заказ принят перевозчиком
        12.09.26, 23:50
        Он отвезёт заказ на таможню
        Заказ везут на таможню в стране отправления
        Обычно это занимает до 10 дней
        Заказ в пункте выдачи
        15.09.26 - 21.09.26
        """,
        "12345678-0001-1",
    )

    dated_statuses = [event.status for event in snapshot.events if event.event_at is not None]
    undated_statuses = [event.status for event in snapshot.events if event.event_at is None]
    assert dated_statuses == [
        "Создан",
        "Передается в доставку",
        "Заказ принят перевозчиком",
    ]
    assert undated_statuses == ["В пути", "Заказ везут на таможню в стране отправления", "Заказ в пункте выдачи"]


def test_tracking_link_keeps_only_track_parameter() -> None:
    assert (
        _build_tracking_link(
            "https://tracking.ozon.ru/?__rr=1&abt_att=1&origin_referer=www.google.com",
            "12345678-0001-1",
        )
        == "https://tracking.ozon.ru/?track=12345678-0001-1"
    )
    assert _build_tracking_link("not-an-absolute-url", "ABC") == "not-an-absolute-url"


def test_parse_ozon_page_rejects_page_without_tracking_result() -> None:
    with pytest.raises(ProviderError, match="не вернул данные"):
        parse_ozon_page_text(
            "OZON Track\nПроверка статуса вашей доставки\n12345678-0001-1",
            "12345678-0001-1",
        )


@pytest.mark.asyncio
async def test_ozon_antibot_challenge_failure_is_actionable() -> None:
    class FakePage:
        async def title(self) -> str:
            return "Antibot Challenge Page"

    with pytest.raises(ProviderError, match="антибот-проверку"):
        await _raise_for_ozon_access_block(FakePage())


@pytest.mark.asyncio
async def test_ozon_challenge_timeout_with_localized_title_is_actionable() -> None:
    class FakePage:
        async def title(self) -> str:
            return "Похоже, нет соединения"

    with pytest.raises(ProviderError, match="антибот-проверку"):
        await _raise_for_ozon_access_block(FakePage())


@pytest.mark.asyncio
async def test_ozon_generic_failure_stays_transient() -> None:
    class FakePage:
        async def title(self) -> str:
            return "OZON Track"

    with pytest.raises(ProviderError, match="временно недоступен"):
        await _raise_for_ozon_access_block(FakePage())


def test_chrome_ua_override_presents_as_desktop_chrome() -> None:
    override = _build_chrome_ua_override("158")

    assert f"Chrome/158.0.0.0" in override["userAgent"]
    assert override["platform"] == "Win32"
    assert override["acceptLanguage"].startswith("ru-RU")
    metadata = override["userAgentMetadata"]
    assert metadata["platform"] == "Windows"
    assert metadata["mobile"] is False
    brands = [brand["brand"] for brand in metadata["brands"]]
    assert "Google Chrome" in brands
    assert metadata["fullVersionList"][-1]["version"] == "158.0.0.0"


@pytest.mark.asyncio
async def test_chrome_major_detection_falls_back_without_executable() -> None:
    assert await _detect_chrome_major(None) == _FALLBACK_CHROME_MAJOR
    assert await _detect_chrome_major("/nonexistent/chromium-binary") == _FALLBACK_CHROME_MAJOR
