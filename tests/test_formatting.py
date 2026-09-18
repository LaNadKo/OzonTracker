from datetime import datetime, timezone

from ozon_tracker_bot.formatting import format_timeline
from ozon_tracker_bot.models import RouteEvent


def _route_event(event_key: str, status: str, text: str, event_at, received_at) -> RouteEvent:
    return RouteEvent(
        order_id=1,
        event_key=event_key,
        status=status,
        event_text=text,
        event_at=event_at,
        received_at=received_at,
    )


def test_timeline_marks_undated_steps_before_last_dated_as_completed() -> None:
    route_events = [
        _route_event(
            "k1",
            "Заказ везут на таможню в стране отправления",
            "Обычно это занимает до 10 дней",
            datetime(2026, 9, 14, 11, 52, tzinfo=timezone.utc),
            datetime(2026, 9, 18, 20, 11, tzinfo=timezone.utc),
        ),
        _route_event(
            "k2",
            "Заказ привезли на таможню для экспортного таможенного оформления",
            "Скорость оформления зависит от загруженности таможни",
            None,
            datetime(2026, 9, 18, 20, 11, tzinfo=timezone.utc),
        ),
        _route_event(
            "k3",
            "Заказ покинул зону экспортного таможенного оформления",
            "Заказ спешит в страну назначения",
            datetime(2026, 9, 15, 6, 12, tzinfo=timezone.utc),
            datetime(2026, 9, 18, 20, 11, tzinfo=timezone.utc),
        ),
        _route_event(
            "k4",
            "Заказ в пункте выдачи",
            "Успейте забрать его в течение 14 дней",
            None,
            datetime(2026, 9, 18, 20, 11, tzinfo=timezone.utc),
        ),
    ]

    result = format_timeline([], route_events)

    assert "✅ Заказ привезли на таможню для экспортного таможенного оформления" in result
    assert "⏳ Заказ в пункте выдачи" in result
    assert "14.09.2026" in result
    assert "15.09.2026" in result


def test_timeline_keeps_real_dates_and_marks_planned_steps() -> None:
    route_events = [
        _route_event(
            "k1",
            "Создан",
            "Мы получили заказ, продавец уже собирает его",
            datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 13, 19, 41, tzinfo=timezone.utc),
        ),
        _route_event(
            "k2",
            "Заказ в пункте выдачи",
            "Успейте забрать его в течение 14 дней",
            None,
            datetime(2026, 9, 13, 19, 41, tzinfo=timezone.utc),
        ),
        _route_event(
            "k3",
            "В пути",
            "В пути",
            None,
            datetime(2026, 9, 13, 19, 41, tzinfo=timezone.utc),
        ),
    ]

    result = format_timeline([], route_events)

    assert "11.09.2026" in result
    assert "19:41" not in result
    assert "⏳ Заказ в пункте выдачи" in result
    assert "Создан" in result
    assert result.count("В пути") == 1
