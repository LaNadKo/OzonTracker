from __future__ import annotations

from datetime import datetime
from html import escape

from .models import Order, RouteEvent, StatusHistory
from .provider import TrackingSnapshot


def format_order(order: Order) -> str:
    emoji = "✅" if order.is_delivered else ("📦" if order.is_active else "🗄")
    status = order.current_status or "ещё не проверялся"
    lines = [
        f"{emoji} <b>#{order.id} — {escape(order.title)}</b>",
        f"Трек: <code>{escape(order.tracking_number)}</code>",
        f"Статус: {escape(status)}",
    ]
    if order.last_event_text:
        lines.append(f"Последнее событие: {escape(order.last_event_text)}")
    if order.last_error:
        lines.append(f"Ошибка последней проверки: {escape(order.last_error)}")
    if order.tracking_url:
        lines.append(f'<a href="{escape(order.tracking_url, quote=True)}">Открыть трекинг</a>')
    return "\n".join(lines)


def format_snapshot(snapshot: TrackingSnapshot) -> str:
    lines = [f"Статус: <b>{escape(snapshot.status)}</b>"]
    if snapshot.latest_event:
        lines.append(f"Событие: {escape(snapshot.latest_event.full_text)}")
        if snapshot.latest_event.event_at:
            lines.append(f"Время события: {format_dt(snapshot.latest_event.event_at)}")
    if snapshot.tracking_url:
        lines.append(f'<a href="{escape(snapshot.tracking_url, quote=True)}">Открыть трекинг</a>')
    return "\n".join(lines)


def format_history(history: list[StatusHistory]) -> str:
    if not history:
        return "История пока пуста."
    lines = ["<b>История статусов</b>"]
    for item in history:
        when = format_dt(item.event_at or item.received_at)
        text = escape(item.event_text or item.status)
        lines.append(f"• {when} — <b>{escape(item.status)}</b>: {text}")
    return "\n".join(lines)


def format_timeline(status_history: list[StatusHistory], route_events: list[RouteEvent]) -> str:
    lines: list[str] = []
    if route_events:
        lines.append("<b>Путь отправления</b>")
        # The route is in page (sequence) order: any undated step before the
        # last dated one has necessarily completed — Ozon simply does not
        # show dates for every milestone.
        last_dated_index = max(
            (index for index, item in enumerate(route_events) if item.event_at is not None),
            default=-1,
        )
        for index, event in enumerate(route_events):
            details: list[str] = []
            if event.status:
                details.append(escape(event.status))
            if event.location:
                details.append(f"где: {escape(event.location)}")
            if event.courier:
                details.append(f"служба: {escape(event.courier)}")
            suffix = f" — {'; '.join(details)}" if details else ""
            if event.event_at is not None:
                lines.append(f"• {format_dt(event.event_at)}{suffix}")
            elif index < last_dated_index:
                lines.append(f"• ✅ {escape(event.status or event.event_text)}")
            else:
                lines.append(f"• ⏳ {escape(event.status or event.event_text)}")
            if event.event_text and event.event_text != event.status:
                lines.append(f"  {escape(event.event_text)}")
    elif status_history:
        lines.append(format_history(status_history))

    if status_history and route_events:
        lines.append("\n<b>Смены статуса</b>")
        for item in status_history:
            when = format_dt(item.event_at or item.received_at)
            lines.append(f"• {when} — <b>{escape(item.status)}</b>")

    if not lines:
        return "История пока пуста."
    result = "\n".join(lines)
    if len(result) > 3800:
        result = result[:3750].rstrip() + "\n… История сокращена, данные в БД сохранены."
    return result


def format_change(order: Order, previous_status: str | None) -> str:
    old = escape(previous_status or "—")
    current = escape(order.current_status or "—")
    lines = [
        f"📦 <b>{escape(order.title)}</b>",
        f"Статус изменился: {old} → <b>{current}</b>",
    ]
    if order.last_event_text:
        lines.append(escape(order.last_event_text))
    if order.tracking_url:
        lines.append(f'<a href="{escape(order.tracking_url, quote=True)}">Открыть трекинг</a>')
    return "\n".join(lines)


def format_event_update(order: Order) -> str:
    lines = [f"📦 <b>{escape(order.title)}</b>"]
    if order.last_event_text:
        lines.append(f"Новое событие: <b>{escape(order.last_event_text)}</b>")
    if order.last_event_at:
        lines.append(f"Время события: {format_dt(order.last_event_at)}")
    if order.tracking_url:
        lines.append(f'<a href="{escape(order.tracking_url, quote=True)}">Открыть трекинг</a>')
    return "\n".join(lines)


def format_dt(value: datetime) -> str:
    return value.astimezone().strftime("%d.%m.%Y %H:%M")
