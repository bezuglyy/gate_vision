"""Константы интеграции gate_vision."""

from __future__ import annotations

DOMAIN = "gate_vision"
NAME = "Ворота (gate-vision)"
MANUFACTURER = "techlan.su"
VERSION = "1.0.0"

# --- ключи настроек (config entry data/options) ---
CONF_CAMERA_ENTITY = "camera_entity"
CONF_SNAPSHOT_URL = "snapshot_url"
CONF_GO2RTC_BASE = "go2rtc_base"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ZONES = "zones"
CONF_THRESHOLDS = "thresholds"
CONF_REACTIONS = "reactions"
CONF_CONTROL = "control"
CONF_LEARN_MODE = "learn_mode"

DEFAULT_GO2RTC_BASE = "http://127.0.0.1:1984"
DEFAULT_SCAN_INTERVAL = 5
MIN_SCAN_INTERVAL = 2
MAX_SCAN_INTERVAL = 300

# Камера ворот по умолчанию: NVR 192.168.4.200, канал 6 (гараж изнутри, вид на ворота)
DEFAULT_RTSP = "rtsp://admin:P0$tCorp@192.168.4.200:9784/cameras/6/streaming/main"

# --- геометрия кадра по умолчанию (доли), если зоны не заданы ---
ROI_X0 = 0.57
ROI_X1 = 0.82
FLOOR_F = 0.545
TOP_F = 0.02

# --- пороги по умолчанию ---
DEFAULT_THRESHOLDS: dict[str, float] = {
    "bright": 190.0,  # «светло» (пересвет улицы/окон)
    "frame_t": 0.65,  # «зажата тёмным»: фон темнее 65 % от яркости полосы
    "min_band_f": 0.08,  # мин. высота полосы окон (доля линии пола)
    "max_band_f": 0.55,  # макс. высота полосы окон
    "street_low_t": 140.0,  # день: низ проёма светлее => улица => ОТКРЫТО
    "dark_low_t": 60.0,  # ночь/ИК: низ проёма темнее => улица => ОТКРЫТО
    "gray_sat": 0.02,  # ниже — ч/б кадр (ИК/ночь)
    "frame_min_mean": 15.0,  # ниже — кадр почти чёрный
    "move_diff": 6.0,  # порог «полотно движется» (разница профилей)
}

# --- зоны ---
ZONE_ROLES = ("closed", "open", "ignore")
ROLE_CLOSED = "closed"  # зона окон полотна: подтверждает «закрыто»
ROLE_OPEN = "open"  # низ проёма: где появляется улица
ROLE_IGNORE = "ignore"  # маска помех (тележка, столб, край)

# --- состояния ---
STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_UNKNOWN = "unknown"

# --- зоны по умолчанию (совпадают с проверенной геометрией 16.09.2026) ---
DEFAULT_ZONES: list[dict] = [
    {
        "id": "z1",
        "name": "Окна полотна (закрыто)",
        "role": ROLE_CLOSED,
        "x": 0.57,
        "y": 0.10,
        "w": 0.25,
        "h": 0.25,
    },
    {
        "id": "z2",
        "name": "Низ проёма (открыто)",
        "role": ROLE_OPEN,
        "x": 0.57,
        "y": 0.40,
        "w": 0.25,
        "h": 0.15,
    },
]

UNKNOWN_GRACE = 6  # сколько циклов «неизвестно» держим последнее состояние
FETCH_RETRIES = 3
FETCH_TIMEOUT = 12

# --- события интеграции ---
EVENT_STATE = f"{DOMAIN}_state_changed"
EVENT_OPENED = "opened"
EVENT_CLOSED = "closed"
EVENT_LEFT_OPEN = "left_open"
EVENT_MOVING = "moving"
EVENT_UNKNOWN = "unknown"
EVENT_CAMERA_LOST = "camera_lost"
EVENT_CAMERA_BACK = "camera_back"

EVENTS: tuple[str, ...] = (
    EVENT_OPENED,
    EVENT_CLOSED,
    EVENT_LEFT_OPEN,
    EVENT_MOVING,
    EVENT_UNKNOWN,
    EVENT_CAMERA_LOST,
    EVENT_CAMERA_BACK,
)

EVENT_TITLES: dict[str, str] = {
    EVENT_OPENED: "Ворота открылись",
    EVENT_CLOSED: "Ворота закрылись",
    EVENT_LEFT_OPEN: "Ворота оставлены открытыми",
    EVENT_MOVING: "Движение полотна ворот",
    EVENT_UNKNOWN: "Состояние ворот не определяется",
    EVENT_CAMERA_LOST: "Камера ворот недоступна",
    EVENT_CAMERA_BACK: "Камера ворот снова доступна",
}

# --- каналы реагирования ---
CHANNELS: tuple[str, ...] = (
    "notify",  # служба notify.* (например notify.mobile_app_...)
    "persistent",  # persistent_notification внутри HA
    "tts",  # озвучка через media_player.play_media (TTS-провайдер HA)
    "script",  # запуск script.* / automation.* / любых служб
    "mqtt",  # публикация в MQTT
    "webhook",  # HTTP POST на URL
)

DEFAULT_REACTION: dict[str, object] = {
    "enabled": True,
    "channels": [],
    "notify_service": "",
    "tts_entity": "",
    "script_entity": "",
    "mqtt_topic": f"{DOMAIN}/event",
    "webhook_url": "",
    "message": "",
    "repeat_min": 0,  # повтор, мин (0 — не повторять)
    "quiet_from": "",  # тихие часы, ЧЧ:ММ
    "quiet_to": "",
}

DEFAULT_REACTIONS: dict[str, dict] = {event: dict(DEFAULT_REACTION) for event in EVENTS}
# по умолчанию осмысленное: «оставлены открытыми» включено с push
DEFAULT_REACTIONS[EVENT_LEFT_OPEN].update({"enabled": True, "repeat_min": 10})
DEFAULT_LEFT_OPEN_MIN = 15  # через сколько минут открытого состояния сообщать

# --- управление (этап 4, по умолчанию выключено) ---
DEFAULT_CONTROL: dict[str, object] = {
    "enabled": False,  # управление выключено, пока не настроено
    "mode": "switch_impulse",  # switch_impulse | mqtt_impulse
    "switch_entity": "",  # например switch.dingtian_relay8777832_switch26
    "mqtt_topic": "",  # например dingtian/relay8777832/in/r26
    "impulse_ms": 800,  # длительность импульса
    "confirm_timeout": 45,  # сколько ждать подтверждения по камере, с
    "check_clear_before_close": True,  # перед закрытием проверять, что проём пуст
}

# --- HTTP/панель ---
URL_STATIC = f"/{DOMAIN}_static"
URL_FRAME = f"/api/{DOMAIN}/frame"
URL_STATE = f"/api/{DOMAIN}/state"
URL_SETTINGS = f"/api/{DOMAIN}/settings"
URL_TEST = f"/api/{DOMAIN}/test"
URL_EVENTS = f"/api/{DOMAIN}/events"
PANEL_URL = "gate-vision"
PANEL_TITLE = "Ворота"
PANEL_ICON = "mdi:garage-variant"

MAX_EVENT_LOG = 200  # сколько последних решений/событий держать в памяти
