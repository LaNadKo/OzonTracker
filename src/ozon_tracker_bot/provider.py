from __future__ import annotations

import logging
import hashlib
import asyncio
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, NoReturn
from urllib.parse import quote, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

DEFAULT_OZON_TRACK_PAGE_URL = (
    "https://tracking.ozon.ru/?__rr=1&abt_att=1&origin_referer=www.google.com"
)

_FALLBACK_CHROME_MAJOR = "152"


class ProviderError(RuntimeError):
    """A safe, user-facing error from the tracking provider."""


@dataclass(frozen=True, slots=True)
class TrackingEvent:
    text: str
    status: str | None = None
    status_code: str | None = None
    event_at: datetime | None = None
    courier: str | None = None
    location: str | None = None
    source_id: str | None = None

    @property
    def full_text(self) -> str:
        """Milestone name and its description combined for display."""
        if self.status and self.text != self.status:
            return f"{self.status} — {self.text}"
        return self.status or self.text

    @property
    def fingerprint(self) -> str:
        raw = "\x1f".join(
            (
                self.source_id or "",
                self.event_at.isoformat() if self.event_at else "",
                self.status or "",
                self.status_code or "",
                self.text,
                self.courier or "",
                self.location or "",
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TrackingSnapshot:
    tracking_number: str
    status: str
    status_code: str | None = None
    delivered: bool = False
    latest_event: TrackingEvent | None = None
    events: tuple[TrackingEvent, ...] = ()
    tracking_url: str | None = None


class OzonTrackerProvider:
    """Read the official Ozon Track page without calling its private API directly.

    Ozon Track is a JavaScript page with a form rather than a documented public
    consumer API. A browser session keeps the integration aligned with the
    user-facing flow and avoids depending on undocumented request details.
    """

    def __init__(
        self,
        page_url: str,
        timeout_seconds: int,
    ) -> None:
        self._page_url = page_url
        self._timeout_ms = timeout_seconds * 1000
        # The antibot challenge resolves asynchronously after the initial 403;
        # give it its own budget regardless of how tight the HTTP timeout is.
        self._challenge_timeout_ms = max(self._timeout_ms, 30_000)
        self._playwright: Any = None
        self._browser: Any = None
        self._ua_override: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def get_tracking(self, tracking_number: str) -> TrackingSnapshot:
        async with self._lock:
            browser = await self._ensure_browser()
            page = await browser.new_page(
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                viewport={"width": 1920, "height": 1080},
            )
            try:
                await _present_as_chrome(page, self._ua_override)
                response = await page.goto(
                    self._page_url,
                    wait_until="domcontentloaded",
                    timeout=self._timeout_ms,
                )
                if response is not None and response.status == 429:
                    raise ProviderError(
                        "Ozon Track временно ограничил частоту запросов: HTTP 429."
                    )
                # The first response is typically 403 with an antibot challenge
                # page; its JavaScript posts the verdict to /abt/result and the
                # real page appears once the challenge passes.
                input_locator = page.get_by_role("textbox")
                try:
                    await input_locator.first.wait_for(
                        state="visible", timeout=self._challenge_timeout_ms
                    )
                except Exception:
                    await _raise_for_ozon_access_block(page)
                await input_locator.first.fill(tracking_number)
                await page.get_by_role(
                    "button", name="Отследить", exact=True
                ).click()
                await page.get_by_text(
                    "Ожидаемая дата доставки", exact=True
                ).wait_for(state="visible", timeout=self._timeout_ms)

                expand = page.get_by_role(
                    "button", name="Показать больше", exact=True
                )
                if await expand.count() and await expand.is_visible():
                    await expand.click()

                body_text = await page.locator("body").inner_text()
                return parse_ozon_page_text(
                    body_text,
                    tracking_number,
                    self._page_url,
                )
            except ProviderError:
                raise
            except Exception as exc:
                logger.warning("Ozon Track browser request failed: %s", type(exc).__name__)
                raise ProviderError("Ozon Track временно недоступен.") from exc
            finally:
                await page.close()
    async def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ProviderError("В контейнере не установлен браузерный модуль Playwright.") from exc

        self._playwright = await async_playwright().start()
        executable_path = next(
            (
                shutil.which(name)
                for name in ("chromium", "chromium-browser", "google-chrome")
                if shutil.which(name)
            ),
            None,
        )
        launch_options: dict[str, Any] = {
            "headless": True,
            # Ozon's challenge checks navigator.webdriver; Playwright's default
            # --enable-automation trips it and the challenge never passes.
            "ignore_default_args": ["--enable-automation"],
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if executable_path:
            launch_options["executable_path"] = executable_path
        self._browser = await self._playwright.chromium.launch(**launch_options)
        self._ua_override = _build_chrome_ua_override(
            await _detect_chrome_major(executable_path)
        )
        return self._browser


async def _present_as_chrome(page: Any, ua_override: dict[str, Any] | None) -> None:
    """Apply the Chrome-on-Windows presentation before any request goes out."""
    if not ua_override:
        return
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Network.setUserAgentOverride", ua_override)
    await page.add_init_script(
        "Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});"
    )


async def _detect_chrome_major(executable_path: str | None) -> str:
    if not executable_path:
        return _FALLBACK_CHROME_MAJOR
    try:
        proc = await asyncio.create_subprocess_exec(
            executable_path,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        match = re.search(r"(\d+)\.", out.decode("utf-8", "replace"))
        if match:
            return match.group(1)
    except Exception:
        logger.warning("Could not detect browser version, using fallback UA")
    return _FALLBACK_CHROME_MAJOR


def _build_chrome_ua_override(major_version: str) -> dict[str, Any]:
    """Ozon's antibot rejects the Debian-Chromium fingerprint: its challenge
    JS posts to /abt/result and the IP stays rejected while the client hints
    say "Chromium on Linux". Presenting as desktop Google Chrome passes the
    challenge with the same IP."""
    full = f"{major_version}.0.0.0"
    return {
        "userAgent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major_version}.0.0.0 Safari/537.36"
        ),
        "acceptLanguage": "ru-RU,ru;q=0.9",
        "platform": "Win32",
        "userAgentMetadata": {
            "brands": [
                {"brand": "Not?A_Brand", "version": "24"},
                {"brand": "Chromium", "version": major_version},
                {"brand": "Google Chrome", "version": major_version},
            ],
            "fullVersionList": [
                {"brand": "Not?A_Brand", "version": full},
                {"brand": "Chromium", "version": full},
                {"brand": "Google Chrome", "version": full},
            ],
            "fullVersion": full,
            "platform": "Windows",
            "platformVersion": "15.0.0",
            "architecture": "x86",
            "model": "",
            "mobile": False,
            "bitness": "64",
            "wow64": False,
        },
    }


async def _raise_for_ozon_access_block(page: Any) -> NoReturn:
    """Convert a stuck antibot challenge into an actionable provider error."""
    title = ""
    try:
        title = (await page.title()).strip()
    except Exception:
        pass

    if "Antibot" in title or "нет соединения" in title:
        raise ProviderError(
            "Ozon Track отклонил антибот-проверку браузера. "
            "Она выполняется автоматически при каждом запросе; попробуйте позже."
        )
    raise ProviderError("Ozon Track временно недоступен.")


_OZON_PAGE_IGNORED_LINES = {
    "Ожидаемая дата доставки",
    "Показать больше",
    "Показать меньше",
}

_OZON_PAGE_STATUS_LINES = {
    "Создан",
    "Cоздан",
    "Передается в доставку",
    "Передаётся в доставку",
    "В пути",
    "Заказ принят перевозчиком",
    "Заказ везут на таможню в стране отправления",
    "Заказ привезли на таможню для экспортного таможенного оформления",
    "Заказ покинул зону экспортного таможенного оформления",
    "Заказ привезли в страну назначения",
    "Заказ передан на импортное таможенное оформление",
    "Заказ проходит импортное таможенное оформление",
    "Заказ выпущен импортной таможней",
    "Заказ отправили на сортировочный терминал",
    "Заказ покинул сортировочный терминал",
    "Заказ ожидает отправки в город получателя",
    "Заказ везут в город получателя",
    "Заказ везут",
    "Заказ в пункте выдачи",
    "Заказ получен в пункте выдачи",
}


def parse_ozon_page_text(
    body_text: str,
    tracking_number: str,
    page_url: str = DEFAULT_OZON_TRACK_PAGE_URL,
) -> TrackingSnapshot:
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    try:
        expected_index = lines.index("Ожидаемая дата доставки")
    except ValueError as exc:
        raise ProviderError("Ozon Track не вернул данные по этому номеру.") from exc

    marker_index = next(
        (
            index
            for index in range(expected_index + 1, len(lines))
            if lines[index] in {"Показать больше", "Показать меньше"}
        ),
        len(lines),
    )
    summary_events = _parse_ozon_events(lines[expected_index + 2 : marker_index])
    expanded_events = _parse_ozon_events(lines[marker_index + 1 :])
    if not summary_events:
        raise ProviderError("Ozon Track вернул страницу без текущего статуса.")

    current = summary_events[-1]
    status = current.status or current.text
    # The summary's last entry is the coarse position marker ("В пути"); the
    # precise position is the newest dated milestone of the detailed route.
    all_events = (*summary_events, *expanded_events)
    dated_events = [event for event in all_events if event.event_at is not None]
    latest_event = dated_events[-1] if dated_events else current
    # Coarse status ladder: once the pickup-point milestones complete, they
    # promote the coarse status past "В пути" (the summary itself never
    # leaves "В пути" even after the shipment is handed out).
    completed_statuses = {event.status for event in dated_events}
    if "Заказ получен в пункте выдачи" in completed_statuses:
        status = "Заказ получен в пункте выдачи"
    elif "Заказ в пункте выдачи" in completed_statuses:
        status = "Заказ в пункте выдачи"
    # The route keeps the page's sequence order (it is chronological). The
    # undated coarse marker is not a milestone and is dropped from the route.
    route_source = list(summary_events) + list(expanded_events)
    if current.event_at is None:
        route_source.remove(current)
    events = _deduplicate_events(route_source)
    return TrackingSnapshot(
        tracking_number=tracking_number,
        status=status,
        status_code=_ozon_status_code(status),
        delivered=_is_delivered_status(status, None),
        latest_event=latest_event,
        events=tuple(events),
        tracking_url=_build_tracking_link(page_url, tracking_number),
    )


def _build_tracking_link(page_url: str, tracking_number: str) -> str:
    """Ozon Track accepts the shipment code as ?track= and shows the result
    without re-entering it; rebuild the link from the page origin so antibot
    parameters (``__rr``, ``abt_att``) never leak into the shared URL."""
    parts = urlsplit(page_url)
    if not parts.netloc:
        return page_url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, f"track={quote(tracking_number)}", ""))


def _parse_ozon_events(lines: list[str]) -> list[TrackingEvent]:
    parsed: list[TrackingEvent] = []
    current_status: str | None = None
    current_date: datetime | None = None
    current_description: list[str] = []

    def flush() -> None:
        if current_status is None:
            return
        description = " ".join(current_description).strip()
        parsed.append(
            TrackingEvent(
                text=description or current_status,
                status=current_status,
                status_code=_ozon_status_code(current_status),
                event_at=current_date,
            )
        )

    for line in lines:
        if line in _OZON_PAGE_IGNORED_LINES or line.startswith("©"):
            continue
        if _is_ozon_status_line(line):
            flush()
            current_status = line
            current_date = None
            current_description = []
            continue
        parsed_date = _parse_ozon_page_datetime(line)
        if parsed_date is not None and current_status is not None and current_date is None:
            current_date = parsed_date
            continue
        if _is_ozon_date_range(line):
            continue
        if current_status is not None:
            current_description.append(line)
    flush()
    return parsed


def _deduplicate_events(events: list[TrackingEvent]) -> list[TrackingEvent]:
    seen: set[str] = set()
    result: list[TrackingEvent] = []
    for event in events:
        if event.fingerprint in seen:
            continue
        seen.add(event.fingerprint)
        result.append(event)
    return result


def _is_ozon_status_line(line: str) -> bool:
    return line in _OZON_PAGE_STATUS_LINES


def _is_ozon_date_range(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:с\s+)?\d{2}\.\d{2}\.\d{2}\s*(?:-|–)\s*\d{2}\.\d{2}\.\d{2}",
            value,
        )
    )


def _parse_ozon_page_datetime(value: str) -> datetime | None:
    for format_string in ("%d.%m.%y, %H:%M", "%d.%m.%y %H:%M", "%d.%m.%y"):
        try:
            return datetime.strptime(value, format_string).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _ozon_status_code(status: str) -> str | None:
    normalized = status.lower()
    if "создан" in normalized:
        return "created"
    if "тамож" in normalized:
        return "customs"
    if "получен" in normalized or "доставлен" in normalized:
        return "delivered"
    if "пункт" in normalized:
        return "pickup"
    if "пути" in normalized or "везут" in normalized:
        return "in_transit"
    if "переда" in normalized:
        return "handing_over"
    return None


def parse_tracking_payload(payload: dict[str, Any], tracking_number: str) -> TrackingSnapshot:
    candidate: Any = payload.get("data") or payload.get("tracking") or payload.get("shipment")
    if candidate is None:
        candidate = payload.get("result")
    if isinstance(candidate, list):
        candidate = candidate[0] if candidate else {}
    if not isinstance(candidate, dict):
        candidate = payload

    events = _parse_events(candidate)
    status, status_code = _extract_status(candidate)
    if not status and events:
        status = events[0].status or "Статус получен из последнего события"
        status_code = status_code or events[0].status_code
    if not status:
        result = str(payload.get("result", "")).strip().lower()
        status = "Ожидание данных" if result == "waiting" else "Статус не указан"

    delivered = _as_bool(
        candidate.get("is_delivered", candidate.get("delivered", candidate.get("isDelivered")))
    )
    if delivered is None:
        delivered = _is_delivered_status(status, status_code)

    latest = events[0] if events else None
    return TrackingSnapshot(
        tracking_number=tracking_number,
        status=status,
        status_code=status_code,
        delivered=delivered,
        latest_event=latest,
        events=tuple(events),
        tracking_url=_first_string(candidate, "tracking_url", "tracker_url", "url"),
    )


def _extract_status(data: dict[str, Any]) -> tuple[str | None, str | None]:
    for key in ("status", "current_status", "status_name", "state", "currentState"):
        value = data.get(key)
        if isinstance(value, dict):
            name = _first_string(value, "name", "title", "status_name", "value")
            code = _first_string(value, "code", "status_code", "id")
            if name:
                return name, code
        elif isinstance(value, str) and value.strip():
            return value.strip(), _first_string(data, "status_code", "code", "state_code")
    return None, None


def _parse_events(data: dict[str, Any]) -> list[TrackingEvent]:
    raw_events: Any = None
    for key in ("events", "checkpoints", "history", "statuses"):
        if isinstance(data.get(key), list):
            raw_events = data[key]
            break
    if not raw_events:
        return []

    parsed: list[TrackingEvent] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        text = _first_string(raw, "message", "status_name", "description", "text", "status_raw")
        status = _first_string(raw, "status", "status_name", "state")
        code = _first_string(raw, "status_code", "code")
        event_at = _parse_datetime(_first_string(raw, "date", "time", "timestamp", "created_at"))
        courier = _extract_nested_name(raw.get("courier") or raw.get("carrier"))
        location = _extract_location(raw)
        source_id = _first_string(raw, "id", "event_id", "eventId", "checkpoint_id")
        if text or status:
            parsed.append(
                TrackingEvent(
                    text=text or status or "Событие без описания",
                    status=status,
                    status_code=code,
                    event_at=event_at,
                    courier=courier,
                    location=location,
                    source_id=source_id,
                )
            )
    if any(event.event_at for event in parsed):
        parsed.sort(key=lambda event: event.event_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return parsed


def _first_string(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _extract_nested_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return _first_string(value, "name", "title", "display_name", "slug")
    if value is not None and str(value).strip():
        return str(value).strip()
    return None


def _extract_location(data: dict[str, Any]) -> str | None:
    value = data.get("location")
    if isinstance(value, dict):
        return _first_string(value, "name", "title", "city", "address")
    return _first_string(
        data,
        "location_translated",
        "location_raw",
        "city",
        "place",
        "location",
    )


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "да", "delivered"}:
            return True
        if normalized in {"false", "0", "no", "нет"}:
            return False
    return None


def _is_delivered_status(status: str, status_code: str | None) -> bool:
    value = f"{status} {status_code or ''}".lower()
    return any(marker in value for marker in ("deliver", "достав", "выдан", "получен", "complete"))


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for format_string in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(value, format_string)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
