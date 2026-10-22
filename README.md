# Samsung EHS Monitor

Use a Shelly clamp or another Home Assistant power sensor for electrical input power and estimated live COP. Existing integration setup, entity unique IDs and persisted seven-day history are retained. No extra helpers are required; enable the managed dashboard for the daily chart.

## Installation and configuration

### Install with HACS

Requires [HACS](https://www.hacs.dev/docs/use/) installed and configured in Home Assistant. This integration is available as a custom repository.

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=zakalibit&repository=samsung_ehs_monitor&category=integration)

1. Click **Open in HACS** above, select your Home Assistant instance, and confirm adding the repository.
2. Download **Samsung EHS Monitor** in HACS, then restart Home Assistant.
3. Add **Samsung EHS Monitor**, choose **Reuse a SmartThings connection** or **Enter a manual access token**, then select your heat pump. If already configured, keep your existing entry.
4. Open the integration's **Configure** options, select your external power sensor if desired, and enable **Create Samsung EHS dashboard**.

If the link does not work, open **HACS → ⋮ → Custom repositories**, add `https://github.com/zakalibit/samsung_ehs_monitor` with type **Integration**, then download it from HACS.

HACS also handles future downloads and updates. Restart Home Assistant after installing an update and refresh your browser for frontend changes. Without GitHub releases, HACS downloads the repository's default branch.

The integration includes its own icon in `custom_components/samsung_ehs_monitor/brand/`. Local integration icons require Home Assistant 2026.3 or later. See the [Home Assistant brand-image documentation](https://contributors.home-assistant.io/core/integrations/brands/#brand-images-for-custom-integrations).

### Manual installation

1. [Download the repository ZIP](https://github.com/zakalibit/samsung_ehs_monitor/archive/refs/heads/main.zip) and copy only `custom_components/samsung_ehs_monitor/` into `/config/custom_components/`, replacing the existing integration folder when upgrading.
2. Restart Home Assistant, then add/configure the integration as described above.

Select a power sensor in **W** or **kW**, not a Wh/kWh energy sensor. The selector lists sensors with device class power. Saving options reloads the integration automatically. Existing entity IDs and dashboard references are retained.

You can change the selected source later, or clear the field and save to return to Samsung API power. The selected entity must already exist and declare W/kW units; unavailable numeric states are allowed after selection. Sensors from Samsung EHS Monitor itself are rejected to prevent direct feedback loops.

## Download an API response

Open **Settings → Devices & services → Samsung EHS Monitor**, open the heat pump entry’s **⋮** menu and select **Download diagnostics**. Each download makes a new read-only SmartThings status request and saves the JSON response using Home Assistant’s native download action. It uses your configured authentication, including shared token refresh. The status data is exported before sensor decoding/validation so unsupported fields can be inspected later; credentials and known identifying fields are excluded or redacted. Home Assistant wraps the response in its diagnostics JSON (`data`). Request failures fail the download instead of returning cached readings. Restart Home Assistant after installing this update to enable the action.

## Authentication

Choose one of two authentication methods:

- **Reuse a SmartThings connection:** first configure the built-in [SmartThings integration](https://www.home-assistant.io/integrations/smartthings/), then select that connection in Samsung EHS Monitor. No client ID, client secret or separate Samsung authorization is needed. The selected connection must be loaded and able to read the heat pump. Samsung EHS Monitor stores its entry ID only and calls its existing token-refresh callback before each API request. SmartThings remains responsible for refreshing and storing its tokens. Reloading that connection is supported; requests resume once it is loaded again.
- **Enter a manual access token:** provide a SmartThings token with permission to read devices. This works without the built-in integration. Replace the token when it expires; manual tokens are not refreshed automatically.

To switch authentication, use **Samsung EHS Monitor → ⋮ → Reconfigure** and choose either method for the same heat pump. This preserves entities, options and stored energy history. Existing standalone OAuth entries will request reauthentication after this update; select one of these two methods. Do not delete the entry. Standalone OAuth and application credential setup have been removed.

Connection reuse follows the built-in integration's [runtime client and refresh callback](https://github.com/home-assistant/core/blob/2026.10.2/homeassistant/components/smartthings/__init__.py). Older Home Assistant versions without that callback require an update or a manual token. Shared authentication has automated regression coverage but still needs verification on a live Home Assistant installation.

## Behaviour

- W values are used directly; kW values are multiplied by 1000. Live COP = estimated thermal kW × 1000 / electrical W matched to the thermal snapshot timestamp.
- Shelly state changes refresh electrical input and live COP immediately, without additional SmartThings API calls. Live COP uses the nearest historical power report to the indoor thermal snapshot, within 90 seconds. Indoor/outdoor packets must also be within 90 seconds of each other. The electrical input sensor continues to display the current reading.
- Electrical input power displays 0 W for valid readings at or below zero, with COP unknown. Missing, unknown, unavailable, nonfinite or unsupported-unit readings remain unknown. Negative readings are still excluded from daily energy/COP accounting; the display clamp does not change those calculations.
- When an external source is selected, Samsung raw power is never substituted automatically and Samsung power-report freshness is not required for live COP. Valid recent thermal packets are still required. Power reports are timestamped using Home Assistant last_reported (or last_updated); rereading a state does not make an old report fresh. Without a close report, live COP falls back to the latest electrical report.
- Source entity and unit are exposed as attributes of electrical input power and live COP. The same source remains configured across restarts. If the sensor is renamed or removed, reselect it in Configure.
- Samsung raw power, electrical energy and energy difference remain available as separate diagnostic measurements.
- With no external sensor selected, the integration uses Samsung power: use device presentation metadata for EHS power scaling and require recent electricity-report timestamps for live COP.

## Seven-day COP scope

This option replaces instantaneous electricity measurement only. Seven-day electricity continues to use differences of Samsung's accumulated Wh counter, converted to kWh. It does NOT integrate the chosen Shelly power sensor into seven-day electricity. Existing stored seven-day data is not mixed with a different source.

The rolling 168-hour algorithm integrates signed thermal snapshots (including negative heat during defrost) and matches them with electricity counter differences at the counter report timestamps. Data gaps, stale readings, resets and restart downtime are not filled with zero. Seven-day heat and electricity totals are published as soon as any valid paired interval is available; COP uses their unrounded energy ratio when both totals are positive. These are totals over available matched data within the last 168 hours, without scaling up for missing periods. The sensor attributes expose coverage_percent and window_hours so partial results can be identified. No paired data leaves the values unknown; a small delay in the latest counter report no longer hides existing totals. Thermal history outside the retained snapshot window is not backfilled. Boundary energy is interpolated within accounted counter intervals; reporting delays and five-minute thermal samples affect accuracy.

Water heat uses 0.0698 × flow L/min × (flow − return °C), assuming mostly water. Fresh off-compressor samples yield zero net heat; residual heat with the compressor stopped is omitted. Use for heating/DHW, not a window containing cooling. This is estimated heat entering the water circuit, not certified energy reaching the rooms. The selected clamp should cover the electrical loads you want the COP to include (for example, compressor plus associated pumps/backup heater).

## Polling and dashboard

Default SmartThings polling is 300 seconds. If you previously changed `DEFAULT_SCAN_INTERVAL` in `const.py` to 60 seconds, retain/reapply that edit when replacing files. External sensor updates refresh live power/COP immediately and record each observed power change. Daily accounting runs on the 60-second heartbeat and on API batches; daily totals can lag live power by up to a minute. No device commands are sent.

For an automatically installed dashboard, enable **Create Samsung EHS dashboard** in the integration’s Configure options. It resolves actual sensor IDs automatically, including area prefixes and renamed entities. The complete dashboard includes a bundled custom daily COP card that the integration loads automatically, plus built-in cards.

The At a glance card includes one Compressor row showing actual frequency / discharge temperature / circulation pump speed (for example, `40Hz / 37°C / 65%`). The separate Compressor and circulation card is omitted; detailed compressor, pump and discharge history remains in Trends.

Estimated performance combines live readings and today's energy/COP in one card. Its three paired rows show Power instant/day total (electrical input / today's electricity), Heat instant/day total (signed net heat flow / today's heat energy), and COP instant/day (live COP / today's ratio of energy). For example: `1,200W / 12.34kWh`, `4.8kW / 38.5kWh`, and `4 / 3.12`. Daily values use valid paired intervals. Daily coverage remains available to calculations and the daily chart but is omitted from this summary card.

## Weather compensation and power trends

The Overview tab includes a read-only **Weather compensation · water law** card. It uses Samsung FSV 2011/2012 for the outdoor endpoints, 2021/2022 for WL1, 2031/2032 for WL2 and 2041 for curve selection. Available curves are drawn with the selected curve highlighted; current outdoor temperature, calculated base target and actual flow temperature are marked. The line interpolates between endpoints and stays flat outside them. Missing or invalid settings produce a waiting message rather than an assumed curve. When FSV 2041 is missing, both available curves remain solid and unselected; each has its own calculated base-target marker and value at the current outdoor temperature. The chart uses °C and normalizes temperature sensor states reported in °F.

This is the configured space-heating base curve before controller offset. It does not change heat-pump settings; actual flow can differ during DHW or defrost. The bundled frontend module supplies the card without another card installation.

The Trends tab uses Home Assistant's own `ha-chart-base` component (the renderer used by its Energy graphs), with stacked colored bars, the native legend/zoom controls and a shared hover/tap tooltip. By default, the graphs show the current day from midnight to now in Home Assistant’s configured timezone, including on daylight-saving transition days. Each bar shows an hourly average, weighted by how long each recorded state was active. Unavailable intervals are excluded; hours without valid readings leave gaps, and partial hours use only the available portion. The tooltip lists each visible series' hourly average and units for that hour. These display averages do not change COP or energy accounting. No energy or power total is calculated by summing the displayed series. History cards retry temporary load/render failures after 5 seconds, with backoff up to one minute, and refresh immediately after a Home Assistant reconnect. History requests time out after 20 seconds so a stalled request cannot block later updates. The last displayed chart remains visible during refresh failures; browser-console warnings identify load/render exceptions.

The bundled adapter reads Recorder history over the last 24 hours, refreshes once a minute while displayed and stops its timer when removed. It loads the native chart through Home Assistant's card helpers, without another chart library or HACS card. It requires Recorder history for the displayed sensors and cannot recover readings Recorder did not retain. The combined **Electrical input and estimated heat** chart uses kW for both series: Electrical input power kW mirrors the existing W measurement divided by 1000, alongside Estimated thermal output. These are power readings, rather than interval kWh. The electrical kW sensor starts recording history after upgrading; existing W readings and COP calculations are retained.

## Verification

126 automated tests pass, including dashboard registry resolution, renaming, missing/disabled sensors, path collisions, multiple heat pumps, unload/reload and opt-out behavior. Existing coverage includes: original decoder, seven-day accounting, external W/kW conversion, invalid/unavailable/zero values, Samsung-zero override, state-event refresh without changing seven-day accounting, fallback when no source is selected, options save/clear and self-reference/unit validation. Coordinator/options tests use lightweight HA stubs, not a live Home Assistant instance. Regression checks also cover shared packet decoding, deferred daily replay, grouped failures, API structure validation and generated example consistency. The standalone frontend test (`node tests/test_frontend_card.mjs`) checks ten malformed/valid daily history cases, missing entities and reconfiguration using a DOM stub, plus water-law interpolation, endpoint clamping, WL selection, missing/invalid settings, markers and temperature-unit conversion. Raw-history chart checks cover native module loading, stacked state alignment, unchanged values and original source timestamps, missing/zero/negative samples, colored shared tooltips, hidden series and refresh/unload handling. The weather card was also visually checked in headless Chrome with synthetic settings. Python/JSON syntax and dashboard YAML are checked. Live installation and Shelly verification remain required.

## References

- [HA Energy usage chart implementation](https://github.com/home-assistant/frontend/blob/20260930.4/src/panels/lovelace/cards/energy/hui-energy-usage-graph-card.ts)
- [HA shared chart component](https://github.com/home-assistant/frontend/blob/20260930.4/src/components/chart/ha-chart-base.ts)
- [HA frontend module registration](https://github.com/home-assistant/core/blob/dev/homeassistant/components/frontend/__init__.py)
- [HA options flow](https://developers.home-assistant.io/docs/config_entries_options_flow_handler/)
- [HA event helpers](https://developers.home-assistant.io/docs/integration_listen_events/)
- [Official SmartThings power/energy definitions](https://github.com/home-assistant/core/blob/dev/homeassistant/components/smartthings/sensor.py#L742-L815)
- Original community binary decoder: https://www.dwellsee.com/decoding-guide/

## Daily energy and COP

The plugin accumulates signed net heat and selected external power over matching valid intervals, and calculates daily COP as heat kWh divided by electrical kWh. It never averages live COP samples. Daily sensors: Estimated heat energy today, Electrical energy today, Estimated COP today, and Daily performance coverage. The existing seven-day calculation still uses the Samsung counter.

Daily collection requires an external W/kW power sensor selected in Configure. Negative or unavailable power breaks collection rather than being treated as zero or positive. Both energies cover only matching valid intervals. Partial-day COP is available immediately after positive energy accumulates; check coverage before interpreting it as whole-day efficiency.

Calendar days follow Home Assistant timezone, including 23/25-hour daylight-saving days. Midnight splits intervals and resets today totals. A minute timer and external state events integrate left-held power; no extra SmartThings requests are made. Heat holds expire at the earlier cycle-packet timestamp plus 15 minutes. Gaps longer than 180 seconds and restart downtime are not integrated. Signed defrost heat reduces delivered heat. Existing net-heat assumptions about stopped compressors and water/glycol remain.

Up to 35 days of daily results persist across restarts. The bundled chart shows 14 days by default, with complete days solid, partial/today days striped, and missing days shown as gaps. Its expandable table shows energy totals and coverage. Chart history is exposed only on the daily COP sensor and excluded from Recorder; the integration’s own storage retains the daily records. The daily summary and history are calculated once per accounting pass. This is a chart of backend daily ratios, not mean statistics from a COP sensor. A partial date represents only observed paired data and can be biased by missing intervals. Changing the power source or Home Assistant timezone starts a new daily series to avoid mixing sources or day boundaries. Existing seven-day history is retained separately.

Enable **Create Samsung EHS dashboard** under Settings → Devices & services → Samsung EHS Monitor → Configure to add a sidebar dashboard for each heat pump. No YAML copying is required. The managed dashboard follows entity registry changes. It is read-only; copy its raw configuration into a separate manual dashboard if you want to customize it. Disabling the option or unloading/removing the integration removes only its managed panel. Existing dashboards are never modified.

The root `dashboard_example.yaml` is generated from the bundled managed template using `python3 scripts/generate_dashboard_example.py` (use `--check` to verify it is current). It remains an optional manual example: replace your dashboard raw configuration with it. The integration automatically serves frontend/daily-cop-card.js; no separate HACS card installation or manual resource registration is required. The integration registers the versioned module with Home Assistant’s frontend loader, independently of YAML resource collections, so resource reloads do not remove the components. Card classes and registrations wait until Home Assistant’s own root element is registered, avoiding the [early-module/scoped-registry startup race](https://github.com/home-assistant/frontend/issues/53890) that can otherwise produce intermittent “Custom element doesn’t exist” errors after refresh. Storage-mode Lovelace resource entries are also maintained for compatibility. Refresh the browser after upgrading. The card type is custom:samsung-ehs-daily-cop-card and its entity is the Estimated COP today sensor. Entity IDs in the manual example assume no area prefix; adjust them to match your registered entities.

Daily history starts on installation; earlier days are not backfilled. No full-day result can be guaranteed if Samsung cycle packets stop arriving while the heat pump is off. Persistence is saved periodically and on clean unload; an abrupt outage can still lose recent readings that have not reached a periodic save. The chart was tested in headless Chrome with synthetic complete/partial/missing-day data and mobile sizing. The full integration has not been tested in a live Home Assistant installation.

## Dashboard logs

The **Logs** tab shows the latest 100 distinct API status/metadata request and cycle parsing failures, newest first. Repeated failures are grouped with UTC first-seen/last-seen timestamps and a count. Each group emits one warning while it remains in the log; later occurrences update the group without another warning. It records HTTP/authentication and network errors, invalid JSON/status structures, unsupported binary IDs, missing/malformed cycle packets, and stale/invalid timestamps. Messages exclude tokens and raw payloads. The failure log sensor stays available during polling failures and excludes its entries from Recorder. API responses are validated before coordinator/sensor use. Malformed packet contents remain decoder failures, allowing valid snapshots in the same batch to be used. Unexpected programming errors propagate to Home Assistant’s normal exception logging instead of silently becoming unknown readings. The in-memory list clears on restart or integration reload. Failures before the integration finishes initial setup remain in Home Assistant’s system logs. Zero compressor frequency is normal operation and is not a parsing failure.

## Timestamped cycle snapshots

All timestamped indoor/outdoor cycle records are validated and decoded into shared samples containing timestamp, readings, validity and failure reason. A bounded packet cache reuses repeated packets; sensors read coordinator results and historical accounting uses these samples without reconstructing JSON or decoding bytes again. Records are sorted and deduplicated by source timestamp. Live sensors use the newest valid cached record, falling back past malformed or missing packets while enforcing the 15-minute freshness limit. Accounting uses the same decoded readings but treats a new invalid packet as a break in the historical hold; it does not fill that gap with an older packet. Signed net heat (including defrost and fresh off-state zero) remains separate from positive heating output. Valid zero values are retained. Historical heat pairs each indoor record with the latest outdoor record at or before that time (and vice versa); it never pairs an older sample with a future one. Packets still expire after 15 minutes.

Daily energy/COP revises the most recent 30 minutes when delayed snapshots arrive. Heat is held between snapshot timestamps and paired with the external electrical power changes actually observed by this integration. Repeated API batches do not add energy twice. No electrical history is invented before initial setup, and unobserved restart/reload gaps remain excluded. Previously saved measurements are restored to allow corrections to periods actually observed before the restart. Revisions can adjust recent daily totals, including across local midnight. Older totals are finalized and retained.

Seven-day accounting retains Samsung electricity-counter differences even when thermal packets are missing. Counter intervals within the most recent 30 minutes are retried on every API response, including repeated counter reports, so delayed snapshots can complete or revise them. Each interval is counted once; incomplete retries preserve already accepted results. Pending intervals persist across restarts and can recover using restored or newly returned snapshots, while new counter accounting never bridges restart downtime. Unresolved intervals expire from the retry buffer without being filled with zero; accepted results remain in the seven-day history. Complete thermal coverage is still required for each accepted counter interval. Published seven-day totals use all accepted intervals available within the window, even when window coverage is partial. External power remains exclusive to daily and live COP. This cannot recover snapshots that Samsung no longer returns.

## Live COP timestamp matching

The integration retains 30 minutes of power reports in memory (up to 4096 reports), rather than only three readings, so fast electrical updates do not discard the history needed by delayed Samsung packets. It selects the report closest to the actual indoor packet used for heat, within 90 seconds, preferring the earlier report on a tie. If there is no report within that tolerance, it uses the latest electrical report as a best-effort fallback. Invalid or zero reports are not skipped in favor of older positive ones. The power_matching_method attribute distinguishes closest matches from latest_fallback. Missing, invalid or zero electrical readings still leave COP unknown. External meter reports continue to be captured during Samsung API failures.

The COP sensor exposes thermal_timestamp, outdoor_timestamp, power_timestamp, power_watts, time_difference_seconds (power minus thermal), and max_time_difference_seconds. These explain why its denominator can differ from the current electrical input power card. Samsung's own power reports use their API timestamps; external power uses the time Home Assistant received the report, which may differ from the physical measurement time. Live history clears on restart/reload. Daily and seven-day accounting remain separate from this live matching buffer.

## Durable daily energy records

Daily energy is stored as timestamped one-minute records containing accumulated heat kWh, electrical kWh and valid duration. Changes within a minute are integrated before aggregation, so the minute records preserve the calculated energy. The ledger retains 35 local calendar days. Daily totals are reconstructed from these records on load, rather than relying only on cached dashboard totals.

The same store record also contains the recent 30-minute replay intervals and their thermal/power measurement context. They are saved together during periodic saves and clean unload. On reload, already observed intervals can be revised by late valid snapshots without double counting. A restart explicitly breaks measurement continuity, including short reloads; electricity and heat are not invented for the downtime.

Missing, malformed or unsupported API responses cannot delete accepted historical measurements or erase accepted daily energy. New invalid packets still prevent new unverified intervals from being added. A later valid measurement can legitimately revise the energy for a previously estimated interval. Existing totals-only stores are migrated as a preserved baseline, and new ledger entries are added separately. Changing the selected electrical source or timezone still starts a new daily series.

This recovery uses the integration's own persisted measurements. Recorder history and dashboard charts are not required. Recorder backfilling is not implemented, and readings that were never observed or saved cannot be reconstructed by this update.
