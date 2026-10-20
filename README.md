# Samsung EHS Monitor

Use a Shelly clamp or another Home Assistant power sensor for electrical input power and estimated live COP. Existing integration setup, entity unique IDs and persisted seven-day history are retained. No extra helpers are required; enable the managed dashboard for the daily chart.

## Installation and configuration

### Install with HACS

Requires [HACS](https://www.hacs.dev/docs/use/) installed and configured in Home Assistant. This integration is available as a custom repository.

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=zakalibit&repository=samsung_ehs_monitor&category=integration)

1. Click **Open in HACS** above, select your Home Assistant instance, and confirm adding the repository.
2. Download **Samsung EHS Monitor** in HACS, then restart Home Assistant.
3. Open **Settings → Devices & services → Add integration**, search for **Samsung EHS Monitor**, enter your SmartThings access token, and select your heat pump. If already configured, keep your existing entry.
4. Open the integration's **Configure** options, select your external power sensor if desired, and enable **Create Samsung EHS dashboard**.

If the link does not work, open **HACS → ⋮ → Custom repositories**, add `https://github.com/zakalibit/samsung_ehs_monitor` with type **Integration**, then download it from HACS.

HACS also handles future downloads and updates. Restart Home Assistant after installing an update and refresh your browser for frontend changes. Without GitHub releases, HACS downloads the repository's default branch.

### Manual installation

1. [Download the repository ZIP](https://github.com/zakalibit/samsung_ehs_monitor/archive/refs/heads/main.zip) and copy only `custom_components/samsung_ehs_monitor/` into `/config/custom_components/`, replacing the existing integration folder when upgrading.
2. Restart Home Assistant, then add/configure the integration as described above.

Select a power sensor in **W** or **kW**, not a Wh/kWh energy sensor. The selector lists sensors with device class power. Saving options reloads the integration automatically. Existing entity IDs and dashboard references are retained.

You can change the selected source later, or clear the field and save to return to Samsung API power. The selected entity must already exist and declare W/kW units; unavailable numeric states are allowed after selection. Sensors from Samsung EHS Monitor itself are rejected to prevent direct feedback loops.

## Behaviour

- W values are used directly; kW values are multiplied by 1000. Live COP = estimated thermal kW × 1000 / electrical W.
- Shelly state changes refresh electrical input and live COP immediately, without additional SmartThings API calls. Samsung thermal data is still limited by its own reporting/polling frequency, so the ratio compares current electrical input with the latest valid heat estimate; it is not a synchronized heat-meter measurement.
- Electrical input power displays 0 W for valid readings at or below zero, with COP unknown. Missing, unknown, unavailable, nonfinite or unsupported-unit readings remain unknown. Negative readings are still excluded from daily energy/COP accounting; the display clamp does not change those calculations.
- When an external source is selected, Samsung raw power is never substituted automatically and Samsung power-report freshness is not required for live COP. Valid recent thermal packets are still required. A stable HA power state is accepted without an arbitrary age timeout; availability comes from its integration.
- Source entity and unit are exposed as attributes of electrical input power and live COP. The same source remains configured across restarts. If the sensor is renamed or removed, reselect it in Configure.
- Samsung raw power, electrical energy and energy difference remain available as separate diagnostic measurements.
- With no external sensor selected, the integration uses Samsung power: use device presentation metadata for EHS power scaling and require recent electricity-report timestamps for live COP.

## Seven-day COP scope

This option replaces instantaneous electricity measurement only. Seven-day electricity continues to use differences of Samsung's accumulated Wh counter, converted to kWh. It does NOT integrate the chosen Shelly power sensor into seven-day electricity. Existing stored seven-day data is not mixed with a different source.

The existing rolling 168-hour algorithm integrates signed thermal power (including negative heat during defrost) and matches it with electricity counter differences between valid polls. Data gaps, stale readings, resets and restart downtime are not filled with zero. Complete valid coverage is required before publishing seven-day heat, electricity and COP values. Coverage indicates progress; missing/off-state cycle reports can delay completeness. Earlier thermal history is not backfilled. Boundary energy is interpolated within polling intervals; reporting delays and five-minute thermal samples affect accuracy.

Water heat uses 0.0698 × flow L/min × (flow − return °C), assuming mostly water. Fresh off-compressor samples yield zero net heat; residual heat with the compressor stopped is omitted. Use for heating/DHW, not a window containing cooling. This is estimated heat entering the water circuit, not certified energy reaching the rooms. The selected clamp should cover the electrical loads you want the COP to include (for example, compressor plus associated pumps/backup heater).

## Polling and dashboard

Default SmartThings polling is 300 seconds. If you previously changed `DEFAULT_SCAN_INTERVAL` in `const.py` to 60 seconds, retain/reapply that edit when replacing files. External sensor updates do not wait for this interval. No device commands are sent.

For an automatically installed dashboard, enable **Create Samsung EHS dashboard** in the integration’s Configure options. It resolves actual sensor IDs automatically, including area prefixes and renamed entities. The complete dashboard includes a bundled custom daily COP card that the integration loads automatically, plus built-in cards.

## Verification

38 automated tests pass, including dashboard registry resolution, renaming, missing/disabled sensors, path collisions, multiple heat pumps, unload/reload and opt-out behavior. Existing coverage includes: original decoder, seven-day accounting, external W/kW conversion, invalid/unavailable/zero values, Samsung-zero override, state-event refresh without changing seven-day accounting, fallback when no source is selected, options save/clear and self-reference/unit validation. Coordinator/options tests use lightweight HA stubs, not a live Home Assistant instance. Python/JSON syntax and dashboard YAML are checked. Live installation and Shelly verification remain required.

## References

- [HA options flow](https://developers.home-assistant.io/docs/config_entries_options_flow_handler/)
- [HA event helpers](https://developers.home-assistant.io/docs/integration_listen_events/)
- [Official SmartThings power/energy definitions](https://github.com/home-assistant/core/blob/dev/homeassistant/components/smartthings/sensor.py#L742-L815)
- Original community binary decoder: https://www.dwellsee.com/decoding-guide/

## Daily energy and COP

The plugin accumulates signed net heat and selected external power over matching valid intervals, and calculates daily COP as heat kWh divided by electrical kWh. It never averages live COP samples. Daily sensors: Estimated heat energy today, Electrical energy today, Estimated COP today, and Daily performance coverage. The existing seven-day calculation still uses the Samsung counter.

Daily collection requires an external W/kW power sensor selected in Configure. Negative or unavailable power breaks collection rather than being treated as zero or positive. Both energies cover only matching valid intervals. Partial-day COP is available immediately after positive energy accumulates; check coverage before interpreting it as whole-day efficiency.

Calendar days follow Home Assistant timezone, including 23/25-hour daylight-saving days. Midnight splits intervals and resets today totals. A minute timer and external state events integrate left-held power; no extra SmartThings requests are made. Heat holds expire at the earlier cycle-packet timestamp plus 15 minutes. Gaps longer than 180 seconds and restart downtime are not integrated. Signed defrost heat reduces delivered heat. Existing net-heat assumptions about stopped compressors and water/glycol remain.

Up to 35 days of daily results persist across restarts. The bundled chart shows 14 days by default, with complete days solid, partial/today days striped, and missing days shown as gaps. Its expandable table shows energy totals and coverage. This is a chart of backend daily ratios, not mean statistics from a COP sensor. A partial date represents only observed paired data and can be biased by missing intervals. Changing the power source or Home Assistant timezone starts a new daily series to avoid mixing sources or day boundaries. Existing seven-day history is retained separately.

Enable **Create Samsung EHS dashboard** under Settings → Devices & services → Samsung EHS Monitor → Configure to add a sidebar dashboard for each heat pump. No YAML copying is required. The managed dashboard follows entity registry changes. It is read-only; copy its raw configuration into a separate manual dashboard if you want to customize it. Disabling the option or unloading/removing the integration removes only its managed panel. Existing dashboards are never modified.

The root `dashboard_example.yaml` remains an optional manual example: replace your dashboard raw configuration with it. The integration automatically serves frontend/daily-cop-card.js; no HACS installation or manual resource registration is required. Refresh the browser after upgrading. The card type is custom:samsung-ehs-daily-cop-card and its entity is the Estimated COP today sensor. Entity IDs in the manual example assume no area prefix; adjust them to match your registered entities.

Daily history starts on installation; earlier days are not backfilled. No full-day result can be guaranteed if Samsung cycle packets stop arriving while the heat pump is off. Persistence is saved periodically and on clean unload; an abrupt outage can lose the most recent unsaved minute. The chart was tested in headless Chrome with synthetic complete/partial/missing-day data and mobile sizing. The full integration has not been tested in a live Home Assistant installation.
