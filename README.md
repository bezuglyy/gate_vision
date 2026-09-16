# Обнаружение (gate_vision)
![Release](https://img.shields.io/github/v/release/bezuglyy/gate_vision?label=Release&style=flat-square) ![HACS](https://img.shields.io/badge/HACS-Custom%20Repository-purple?style=flat-square) ![License](https://img.shields.io/github/license/bezuglyy/gate_vision?style=flat-square) ![HA](https://img.shields.io/badge/HA-2025.1%2B-2ea44f?style=flat-square)
Кастомная интеграция для [Home Assistant](https://www.home-assistant.io) · версия **1.5.0**.

![icon](custom_components/gate_vision/brand/icon.png)

| | |
|---|---|
| Домен | `gate_vision` |
| Версия | 1.5.0 |
| Тип | custom integration |
| Тип опроса | `local_polling` |
| Зависимости | нет (numpy и Pillow уже есть в Home Assistant) |

## Описание
Определение состояния подъёмных ворот **по камере** — без магнитов, герконов и меток.
Интеграция смотрит кадр камеры, которая видит полотно ворот, и говорит **ЗАКРЫТО / ОТКРЫТО**.
Внутри — веб-панель с редактором зон, порогами, реагированиями и журналом.

### Возможности
- ✅ Сущность `binary_sensor` с `device_class: garage_door` — открыто/закрыто, готово для
  автоматизаций, дашборда и голосового ассистента
- ✅ **Работает днём и ночью**: днём «открыто» видно по светлой улице под полотном; ночью камера
  уходит в ИК — правило зеркальное, а окна полотна читаются как тёмные ячейки на подсвеченном полотне
- ✅ **Окна полотна — главный признак «закрыто»** (яркая полоса днём, тёмная ночью), поэтому состояние
  не «плывёт» при смене экспозиции на рассвете/закате
- ✅ **Точные размеры зон**: у выбранной зоны можно задать X/Y/ширину/высоту в процентах,
  двигать стрелками на кадре, а также выбирать одну или несколько зон для массовых действий
- ✅ **Зоны детекции**: где искать признак «открыто» (низ проёма), где «закрыто» (окна полотна) и
  что игнорировать (тележка, столб, край кадра)
- ✅ **Режим обучения состояний областей**: приводишь объект в состояние и нажимаешь
  «Запомнить ОТКРЫТО» / «Запомнить ЗАКРЫТО» — детектор сравнивает с обученными замерами
  (можно запомнить несколько раз: день, ночь, разное освещение — берётся ближайший)
- ✅ **Сущность для каждой области**: галочка «создавать сущность» + **выбор типа** —
  «Открыто / Закрыто» (`device_class: garage_door`) или «Включено / Выключено» (без device_class)
- ✅ **Расписания запуска реле**: несколько автоматизаций — время, дни недели (ежедневно / будни /
  выходные / свой набор) и действие (импульс / открыть / закрыть / стоп)
- ✅ **Проверка состояния при выполнении автоматизации**: условие «выполнять, если состояние»
  (любое / закрыто / открыто / движется) и **проверка результата по камере** — если состояние
  не подтвердилось, в журнал и в событие `schedule_unconfirmed` попадает запись
- ✅ **Обучение по структуре**: замер хранит не только яркость, но и наличие полосы окон,
  поэтому состояния не путаются на рассвете/закате, когда яркости совпадают
- ✅ **Автокалибровка**: кнопка «Авто (10 замеров)» — медиана из 10 замеров вместо одного
- ✅ **Быстрая настройка**: одна вкладка «Настройка» (пресеты объекта, камера, зоны, обучение,
  сущность), пороги убраны под «Дополнительно»
- ✅ **Тип реле**: импульсное (реле само даёт импульс — время нажатия не задаётся) или постоянное
  (держим заданное время)
- ✅ **Источник кадра — выбором из списка**: камера Home Assistant (`camera.*`, рекомендуется) или
  RTSP-адрес / имя потока go2rtc / готовый URL
- ✅ **Получатели выбираются из списка**, а не вписываются руками: службы `notify.*`, сущности
  `tts.*`, `media_player.*`, `script.*` / `automation.*` / `scene.*`, реле `switch.*`
- ✅ **Веб-панель «Ворота»** в боковом меню: живой кадр, рисование зон мышью, пороги с моментальным
  применением, журнал событий
- ✅ **Реагирования на события**: открылись, закрылись, оставлены открытыми, движение полотна,
  состояние не определяется, камера недоступна/вернулась. Каналы: push (`notify.*`), уведомление в HA,
  озвучка (TTS), скрипт/служба, MQTT, webhook. Плюс повтор и тихие часы
- ✅ **Событие на шине** `gate_vision_state_changed` для любых автоматизаций
- ✅ **Диагностика**: режим кадра (день/ИК), яркость зон, движение полотна, доступность камеры,
  текст решения детектора
- ✅ **Управление (опционально, по умолчанию выключено)**: `cover` с импульсным реле, памятью
  направления, ожиданием подтверждения по камере и запретом команд при неизвестном состоянии
- ✅ Настройка через UI (config flow + options), иконка интеграции, переводы: русский, английский

### Установка
1. Скопируйте папку `custom_components/gate_vision/` в каталог `custom_components/` конфигурации Home Assistant.
2. Перезапустите Home Assistant.
3. Настройки → Устройства и службы → Добавить интеграцию → **Ворота (gate-vision)**.

> HACS: добавьте `https://github.com/bezuglyy/gate_vision` как Custom repository (категория Integration).

### Настройка камеры
Источник кадра выбирается в мастере: **камера Home Assistant** (`camera.*`) или адрес напрямую:

```
rtsp://admin:PASS@192.168.4.200:9784/cameras/6/streaming/main   # RTSP напрямую
Ворота                                                          # имя потока в go2rtc
http://127.0.0.1:1984/api/frame.jpeg?src=...                    # готовый URL
```

Совет: добавьте поток в `go2rtc.yaml` и указывайте в интеграции просто его имя.

### Панель «Ворота»
Открывается из бокового меню Home Assistant.

- 🟩 **Закрыто (окна)** — область окон полотна, подтверждает «закрыто»
- 🟧 **Открыто (низ проёма)** — где появляется улица при подъёме полотна, **основной признак**
- ⬜ **Исключение** — помехи, исключаются из расчёта

ЛКМ по пустому месту — нарисовать зону, тянуть — сдвинуть, за уголок — изменить размер.
**Положение зоны сохраняется автоматически** через долю секунды после правки (в шапке на время
правки показывается «● не сохранено»).
Стрелки на кадре двигают выбранную зону (с Shift — мелким шагом).
У выбранной зоны есть блок **точных размеров** (X, Y, ширина, высота в %).
Галочками можно выбрать **одну или несколько зон** и применить к ним роль или удалить их разом.
Кнопка «Зоны по умолчанию» возвращает проверенную геометрию, «◀ В меню» — возврат в меню Home Assistant.

### Сущности
| Сущность | Тип | Что показывает |
|---|---|---|
| `binary_sensor.<имя>` | `garage_door` | **открыто / закрыто** (главная) |
| `binary_sensor.<имя>_gate_leaf_moving` | `moving` | полотно движется |
| `binary_sensor.<имя>_camera` | `connectivity` | камера отвечает |
| `sensor.<имя>_frame_mode` | — | режим кадра: день / ночь-ИК |
| `sensor.<имя>_zone_open_brightness` | — | яркость зоны «открыто» |
| `sensor.<имя>_zone_closed_brightness` | — | яркость зоны «закрыто» |
| `sensor.<имя>_open_seconds` | `duration` | сколько ворот открыты |
| `sensor.<имя>_last_change` | `timestamp` | когда состояние изменилось |
| `sensor.<имя>_last_event` | — | последнее событие |
| `sensor.<имя>_detector_decision` | — | текстовое решение детектора |
| `cover.<имя>` | `garage` | управление (только если включено) |

### События
```yaml
event: opened | closed | left_open | moving | unknown | camera_lost | camera_back
state: closed | open | unknown
reason: "ч/б (ИК): зона «открыто» тёмная (32 < 60) — видна улица"
```

### HTTP API
| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/gate_vision/frame?fresh=1` | последний кадр (JPEG) |
| GET | `/api/gate_vision/state` | состояние + анализ + настройки + журнал |
| GET | `/api/gate_vision/settings` | текущие настройки |
| POST | `/api/gate_vision/settings` | изменить зоны/пороги/реагирования/управление |
| POST | `/api/gate_vision/test` | принудительная проверка кадра |
| GET | `/api/gate_vision/events?limit=50` | журнал событий |

### Как это работает
1. Интеграция получает кадр (по умолчанию через go2rtc) с повторами — go2rtc иногда отвечает 500
   на «холодном» старте ad-hoc потока.
2. Считает профиль яркости по строкам в плоскости ворот и измеряет яркость зон.
3. **День (цветной кадр):** низ проёма светлее порога → ОТКРЫТО, иначе ЗАКРЫТО.
   **Ночь/ИК (ч/б):** низ проёма темнее порога → ОТКРЫТО, иначе ЗАКРЫТО.
4. Дополнительно ищет полосу окон (яркая зона, зажатая тёмным полотном) — подтверждение «закрыто».
5. Короткие провалы «неизвестно» сглаживаются, движение полотна определяется по разнице профилей.
6. Смена состояния и события уходят в шину HA и в настроенные каналы реагирования.

Логика проверена на реальных данных: 4196 замеров за сутки, у закрытых ворот яркость низа проёма
60–140, у открытых 140–220, **в зоне неоднозначности 135–145 — ни одного замера**.

### Ограничения
- Нужна камера, которая **видит полотно ворот** (снаружи или изнутри).
- Если камера уходит в ИК и улица тоже тёмная, признак «открыто» опирается на контраст полотна;
  при полной темноте возможен статус «неизвестно».
- Управление (`cover`) требует известного номера реле и по умолчанию выключено.

---

# Gate Vision (English)

Custom [Home Assistant](https://www.home-assistant.io) integration · version **1.5.0**.

Detects the state of a **sectional / overhead garage gate from a camera** — no magnets, reed
switches or markers. The integration reads a frame from a camera that sees the gate leaf and
reports **CLOSED / OPEN**, with a web panel for detection zones, thresholds, reactions and a log.

### Features
- `binary_sensor` with `device_class: garage_door` — ready for automations, dashboards and voice
- Works **day and night**: by day "open" is seen as the bright street under the leaf; at night the
  camera switches to IR and the rule mirrors — leaf windows appear as dark cells on the IR-lit leaf
- **Detection zones**: where to look for "open" (bottom of the opening), "closed" (leaf windows)
  and what to ignore (cart, pole, frame edge)
- **Web panel** in the sidebar: live frame, draw zones with the mouse, thresholds applied instantly, event log
- **Frame source and recipients are picked from lists**: HA camera entity (`camera.*`) or a direct URL;
  `notify.*` services, `tts.*`, `media_player.*`, `script.*`/`automation.*`/`scene.*`, relay `switch.*`
- **Reactions**: opened, closed, left open, leaf moving, state unknown, camera lost/back.
  Channels: push (`notify.*`), HA notification, TTS, script/service, MQTT, webhook + repeat and quiet hours
- Bus event `gate_vision_state_changed` for custom automations
- Diagnostics: frame mode (day/IR), zone brightness, leaf movement, camera availability, detector reason
- **Optional control (disabled by default)**: `cover` with impulse relay, direction memory,
  camera confirmation and command lockout while the state is unknown
- UI configuration, integration icon, Russian and English translations

### Installation
1. Copy `custom_components/gate_vision/` into your Home Assistant `custom_components/` directory.
2. Restart Home Assistant.
3. Settings → Devices & Services → Add Integration → **Gate Vision**.

> HACS: add `https://github.com/bezuglyy/gate_vision` as a Custom repository (category Integration).

### Limitations
- A camera that **sees the gate leaf** is required.
- In full darkness (IR off / no light) the state may become `unknown`.
- Control (`cover`) needs the known relay and is disabled by default.

---
**Автор / Author:**
![Bezuglyj E.N.](logo-bezuglyj.png)

## License / Лицензия
MIT
