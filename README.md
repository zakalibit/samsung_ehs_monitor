# Samsung EHS Monitor

Use a Shelly clamp or another Home Assistant power sensor for electrical input power and estimated live COP. Existing integration setup, entity unique IDs and persisted seven-day history are retained. No extra helpers are required; enable the managed dashboard for the daily chart.

## Installation and configuration

### Install with HACS

Requires [HACS](https://www.hacs.dev/docs/use/) installed and configured in Home Assistant. This integration is available as a custom repository.

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=zakalibit&repository=samsung_ehs_monitor&category=integration)

1. Click **Open in HACS** above, select your Home Assistant instance, and confirm adding the repository.
2. Download **Samsung EHS Monitor** in HACS, then restart Home Assistant.
3. Add **Samsung EHS Monitor**, choose **Sign in with Samsung (OAuth)**, and complete Samsung authorization using your own OAuth application credentials (see [OAuth setup](#oauth-setup)). Then select your heat pump. A manual access token is also supported. If already configured, keep your existing entry.
4. Open the integration's **Configure** options, select your external power sensor if desired, and enable **Create Samsung EHS dashboard**.

If the link does not work, open **HACS → ⋮ → Custom repositories**, add `https://github.com/zakalibit/samsung_ehs_monitor` with type **Integration**, then download it from HACS.

HACS also handles future downloads and updates. Restart Home Assistant after installing an update and refresh your browser for frontend changes. Without GitHub releases, HACS downloads the repository's default branch.

### Manual installation

1. [Download the repository ZIP](https://github.com/zakalibit/samsung_ehs_monitor/archive/refs/heads/main.zip) and copy only `custom_components/samsung_ehs_monitor/` into `/config/custom_components/`, replacing the existing integration folder when upgrading.
2. Restart Home Assistant, then add/configure the integration as described above.

Select a power sensor in **W** or **kW**, not a Wh/kWh energy sensor. The selector lists sensors with device class power. Saving options reloads the integration automatically. Existing entity IDs and dashboard references are retained.

You can change the selected source later, or clear the field and save to return to Samsung API power. The selected entity must already exist and declare W/kW units; unavailable numeric states are allowed after selection. Sensors from Samsung EHS Monitor itself are rejected to prevent direct feedback loops.

## Authentication

Samsung EHS Monitor has its own OAuth authorization flow, access/refresh tokens and Home Assistant OAuth session. The built-in SmartThings integration is not required. Home Assistant validates the authorization callback/state, stores the tokens, refreshes expired access tokens, and persists rotated refresh tokens. Token rotation does not reload this integration. A revoked grant requires a fresh Samsung sign-in; transient refresh failures are retried through normal polling.

### OAuth setup

1. Register an **OAuth-In App** with SmartThings and obtain its **Client ID** and **Client Secret**. SmartThings documents this under [OAuth-based API authentication](https://developer.smartthings.com/docs/getting-started/quickstart). The app needs the read-only `r:devices:*` scope; no device command permissions are requested.
2. Register the Home Assistant callback URL in your OAuth app. With **My Home Assistant** enabled, use `https://my.home-assistant.io/redirect/oauth`. Otherwise use the exact callback Home Assistant uses for your instance, normally `https://YOUR-HA-URL/auth/external/callback`. The registered URI must match exactly.
3. In **Settings → Devices & services → ⋮ → Application credentials**, add credentials for **Samsung EHS Monitor**, entering your Client ID and Client Secret. These are OAuth application credentials, not a personal access token. Never commit the client secret to GitHub.
4. Add Samsung EHS Monitor, choose **Sign in with Samsung (OAuth)**, select your application credentials if prompted, and approve access in Samsung's browser page. Then select the heat pump.

This implementation uses SmartThings' documented production endpoints `https://api.smartthings.com/oauth/authorize` and `https://api.smartthings.com/oauth/token`, with HTTP Basic client authentication for authorization-code exchange and refresh. SmartThings also documents a newer API Access Apps experience as a preview; credentials for a different endpoint family may require corresponding changes. See [SmartThings authentication](https://developer.smartthings.com/docs/getting-started/quickstart) and [Home Assistant OAuth/application credentials](https://developers.home-assistant.io/docs/core/platform/application_credentials/).

Existing manual-token users can choose **Samsung EHS Monitor → ⋮ → Reconfigure → Sign in with Samsung (OAuth)** and authorize access to the same heat pump. This preserves device/entity identity, options and stored energy history. Do not delete the integration to change authentication. Manual tokens remain available through **Enter a manual access token**.

The code contains no bundled Client ID/Secret. Registering a SmartThings OAuth app and configuring its credentials is required before the independent sign-in flow can be used. The OAuth transport and flow have automated tests; a live Samsung authorization/refresh test is still required with real application credentials.

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

## Weather compensation and power trends

The Overview tab includes a read-only **Weather compensation · water law** card. It uses Samsung FSV 2011/2012 for the outdoor endpoints, 2021/2022 for WL1, 2031/2032 for WL2 and 2041 for curve selection. Available curves are drawn with the selected curve highlighted; current outdoor temperature, calculated base target and actual flow temperature are marked. The line interpolates between endpoints and stays flat outside them. Missing or invalid settings produce a waiting message rather than an assumed curve. A missing selection leaves both available curves unselected. The chart uses °C and normalizes temperature sensor states reported in °F.

This is the configured space-heating base curve before controller offset. It does not change heat-pump settings; actual flow can differ during DHW or defrost. The bundled frontend module supplies the card without another card installation.

The Trends tab adds a combined 24-hour **Electrical input and estimated heat** history graph. Both lines use kW: a new Electrical input power kW sensor mirrors the existing electrical input measurement divided by 1000, alongside Estimated thermal output. This compares power, rather than cumulative kWh. The new electrical kW sensor starts recording history after upgrading; existing electrical W readings and COP calculations are retained.

## Verification

125 automated tests pass, including dashboard registry resolution, renaming, missing/disabled sensors, path collisions, multiple heat pumps, unload/reload and opt-out behavior. Existing coverage includes: original decoder, seven-day accounting, external W/kW conversion, invalid/unavailable/zero values, Samsung-zero override, state-event refresh without changing seven-day accounting, fallback when no source is selected, options save/clear and self-reference/unit validation. Coordinator/options tests use lightweight HA stubs, not a live Home Assistant instance. Regression checks also cover shared packet decoding, deferred daily replay, grouped failures, API structure validation and generated example consistency. The standalone frontend test (`node tests/test_frontend_card.mjs`) checks ten malformed/valid daily history cases, missing entities and reconfiguration using a DOM stub, plus water-law interpolation, endpoint clamping, WL selection, missing/invalid settings, markers and temperature-unit conversion. The weather card was also visually checked in headless Chrome with synthetic settings. Python/JSON syntax and dashboard YAML are checked. Live installation and Shelly verification remain required.

## References

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

The root `dashboard_example.yaml` is generated from the bundled managed template using `python3 scripts/generate_dashboard_example.py` (use `--check` to verify it is current). It remains an optional manual example: replace your dashboard raw configuration with it. The integration automatically serves frontend/daily-cop-card.js; no separate HACS card installation or manual resource registration is required. The integration registers the versioned module with Home Assistant’s frontend loader, independently of YAML resource collections, so resource reloads do not remove the components. Storage-mode Lovelace resource entries are also maintained for compatibility. Refresh the browser after upgrading. The card type is custom:samsung-ehs-daily-cop-card and its entity is the Estimated COP today sensor. Entity IDs in the manual example assume no area prefix; adjust them to match your registered entities.

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
