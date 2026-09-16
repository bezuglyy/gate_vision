"""Регистрация веб-панели «Ворота» в Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import PANEL_ICON, PANEL_TITLE, PANEL_URL, URL_STATIC

_LOGGER = logging.getLogger(__name__)


async def async_register_panel(hass: HomeAssistant) -> None:
    """Отдать статику панели и добавить её в боковое меню."""
    frontend_dir = Path(__file__).parent / "frontend"
    # версия для сброса кэша браузера = время правки panel.js
    try:
        js_version = int((frontend_dir / "panel.js").stat().st_mtime)
    except OSError:
        js_version = 1
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(URL_STATIC, str(frontend_dir), True)]
        )
    except RuntimeError:
        # уже зарегистрирован (например, после перезагрузки записи)
        _LOGGER.debug("gate_vision: статика панели уже зарегистрирована")

    if PANEL_URL in hass.data.get("frontend_panels", {}):
        return

    try:
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL,
            webcomponent_name="gate-vision-panel",
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            module_url=f"{URL_STATIC}/panel.js?v={js_version}",
            embed_iframe=False,
            require_admin=False,
            config={"domain": "gate_vision"},
        )
        _LOGGER.info("gate_vision: панель «%s» добавлена в боковое меню", PANEL_TITLE)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("gate_vision: не удалось добавить панель: %s", err)


async def async_unregister_panel(hass: HomeAssistant) -> None:
    """Убрать панель (при выгрузке интеграции)."""
    try:
        from homeassistant.components.frontend import async_remove_panel

        async_remove_panel(hass, PANEL_URL)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("gate_vision: панель не удалена: %s", err)
