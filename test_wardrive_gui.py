import unittest
import tempfile
import io
import json
import queue
import stat
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from wardrive_gui import (
    CaptureSource, WardriveApp, access_point_count, observed_access_point_count, adapter_pickup_stats, build_command, export_wigle_csv, format_file_size, gps_text,
    kismet_web_url,
    device_map_points, managed_interface_name, monitor_interface_name, network_details,
    load_wdgwars_api_key, save_wdgwars_api_key, upload_wdgwars_csv,
    adsb_snapshot_status, upload_wdgwars_adsb, read_adsb_aircraft, filter_adsb_aircraft,
    format_duration, load_upload_history, parse_wigle_session, responsive_window_size,
    save_upload_history, valid_wdgwars_api_key, wdgwars_account,
    compare_sessions, export_geodata, find_recoverable_databases, read_wigle_records,
    session_analytics, load_profiles, save_profiles,
    gps_device_choices, gps_device_kind, gps_report_device, gps_active_device,
    ap_activity_level, adapter_stats_visible_rows, OUTPUT_BATCH_LINES,
    CHANNEL_PLANS, CUSTOM_HOP_PLAN, FIXED_CHANNEL_PLAN, channel_plan_for,
    hopping_configuration_errors, source_channel_status,
)


class BuildCommandTests(unittest.TestCase):
    WIGLE_HEADER = (
        "WigleWifi-1.6,appRelease=Kismet\n"
        "MAC,SSID,AuthMode,FirstSeen,Channel,Frequency,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,RCOIs,MfgrId,Type\n"
    )

    def test_classifies_and_lists_gpsd_receivers(self):
        self.assertEqual(gps_device_kind("/dev/ttyACM0"), "USB")
        self.assertEqual(gps_device_kind("/dev/ttyS0"), "Internal")
        self.assertEqual(gps_device_choices({}), [])
        self.assertEqual(gps_device_choices([
            {"path": "/dev/ttyUSB0"}, {"path": "/dev/ttyS0"}, {"activated": 1},
        ]), [
            ("Internal — /dev/ttyS0", "/dev/ttyS0"),
            ("USB — /dev/ttyUSB0", "/dev/ttyUSB0"),
        ])

    def test_normalizes_gps_report_receiver(self):
        self.assertEqual(gps_report_device({"device": " /dev/ttyUSB0 "}), "/dev/ttyUSB0")
        self.assertEqual(gps_report_device({"class": "TPV"}), "")
        self.assertEqual(gps_report_device(None), "")

    def test_automatic_gps_waits_for_a_working_receiver(self):
        no_fix = {"class": "TPV", "device": "/dev/ttyS0", "mode": 1}
        usb_fix = {"class": "TPV", "device": "/dev/ttyUSB0", "mode": 3}
        self.assertEqual(gps_active_device("", "", no_fix), "")
        self.assertEqual(gps_active_device("", "", usb_fix), "/dev/ttyUSB0")
        self.assertEqual(gps_active_device("/dev/ttyUSB0", "", no_fix), "/dev/ttyUSB0")

    def test_configured_gps_receiver_remains_selected(self):
        report = {"class": "TPV", "device": "/dev/ttyUSB0", "mode": 3}
        self.assertEqual(gps_active_device("", "/dev/ttyACM0", report), "/dev/ttyACM0")

    def test_classifies_ap_pickup_activity(self):
        self.assertEqual(ap_activity_level(35, 1), ("VERY ACTIVE", "success"))
        self.assertEqual(ap_activity_level(12, 2), ("ACTIVE", "cyan"))
        self.assertEqual(ap_activity_level(3, 5), ("LIGHT ACTIVITY", "amber"))
        self.assertEqual(ap_activity_level(0, 31), ("DEAD ZONE", "danger"))

    @patch("wardrive_gui.time.monotonic", return_value=100.0)
    def test_ap_activity_uses_fast_window_and_colors_count(self, _monotonic):
        app = WardriveApp.__new__(WardriveApp)
        app.ap_activity_samples = [(85.0, 10)]
        app._last_ap_pickup = 85.0
        app.process = MagicMock()
        app.process.poll.return_value = None
        app.ap_activity = MagicMock()
        app.ap_activity_label = MagicMock()
        app.ap_count_label = MagicMock()
        app.ap_activity_canvas = None

        WardriveApp._record_ap_activity(app, 11)

        self.assertIn("rolling 15s", app.ap_activity.set.call_args.args[0])
        self.assertEqual(
            app.ap_count_label.configure.call_args.kwargs["fg"],
            app.ap_activity_label.configure.call_args.kwargs["fg"],
        )

    def test_compares_analyzes_and_exports_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old.wiglecsv"
            new = root / "new.wiglecsv"
            old.write_text(self.WIGLE_HEADER +
                           "AA:AA:AA:AA:AA:AA,Cafe,[WPA2],2026-01-01 10:00:00,6,2437,-60,28.1,-82.5,0,5,,,WIFI\n",
                           encoding="utf-8")
            new.write_text(self.WIGLE_HEADER +
                           "AA:AA:AA:AA:AA:AA,Cafe,[WPA3],2026-01-02 10:00:00,11,2462,-45,28.1,-82.5,0,5,,,WIFI\n"
                           "BB:BB:BB:BB:BB:BB,Park,[ESS],2026-01-02 10:01:00,1,2412,-75,28.2,-82.6,0,5,,,WIFI\n",
                           encoding="utf-8")
            comparison = compare_sessions(old, new)
            self.assertEqual(len(comparison["new"]), 1)
            self.assertEqual(len(comparison["changed"]), 1)
            self.assertEqual(session_analytics(new)["Channels"], {"11": 1, "1": 1})
            for kind in ("geojson", "kml", "gpx"):
                output = root / f"map.{kind}"
                export_geodata(new, output, kind)
                self.assertGreater(output.stat().st_size, 40)
            self.assertEqual(len(read_wigle_records(new)), 2)

    def test_finds_recoverable_databases_and_persists_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.kismet"
            complete = root / "complete.kismet"
            missing.touch(); complete.touch(); complete.with_suffix(".wiglecsv").touch()
            self.assertEqual(find_recoverable_databases(root), [missing])
            profile_path = root / "profiles.json"
            profiles = {"city": {"log_directory": "/captures", "sources": {}}}
            save_profiles(profiles, profile_path)
            self.assertEqual(load_profiles(profile_path), profiles)
            self.assertEqual(stat.S_IMODE(profile_path.stat().st_mode), 0o600)

    @patch("wardrive_gui.datetime")
    def test_builds_wardrive_command(self, fake_datetime):
        fake_datetime.now.return_value = datetime(2026, 8, 16, 12, 0, 0)
        command = build_command("wlan0", Path("/tmp/drive logs"))
        self.assertEqual(command[0], "kismet")
        self.assertIn("wardrive", command)
        self.assertEqual(command[-2:], ["-c", "wlan0:channel_hop=true"])
        self.assertEqual(command[command.index("--log-prefix") + 1], "/tmp/drive logs")

    @patch("wardrive_gui.datetime")
    def test_builds_authenticated_loopback_api(self, fake_datetime):
        fake_datetime.now.return_value = datetime(2026, 8, 16, 12, 0, 0)
        command = build_command("wlan0", Path("/tmp/logs"), Path("/tmp/api.conf"))
        self.assertEqual(command[command.index("--override", 4) + 1], "/tmp/api.conf")

    @patch("wardrive_gui.datetime")
    def test_builds_multiple_sources_with_channel_modes(self, fake_datetime):
        fake_datetime.now.return_value = datetime(2026, 8, 16, 12, 0, 0)
        sources = [
            CaptureSource("wlan0", "hop", "1, 6, 11"),
            CaptureSource("wlan1", "fixed", "36"),
        ]
        command = build_command(sources, Path("/tmp/logs"))
        definitions = [command[index + 1] for index, value in enumerate(command) if value == "-c"]
        self.assertEqual(definitions, [
            'wlan0:channel_hop=true,channels="1,6,11"',
            "wlan1:channel_hop=false,channel=36",
        ])

    def test_rejects_a_one_channel_hop_plan(self):
        self.assertEqual(len(hopping_configuration_errors([CaptureSource("wlan0", "hop", "1")])), 1)
        self.assertEqual(hopping_configuration_errors([CaptureSource("wlan0", "hop", "1,6,11")]), [])
        self.assertEqual(hopping_configuration_errors([CaptureSource("wlan0", "hop", "")]), [])
        self.assertEqual(hopping_configuration_errors([CaptureSource("wlan0", "fixed", "1")]), [])

    def test_reports_live_source_channel_health(self):
        sources = [{
            "kismet.datasource.capture_interface": "wlan0mon",
            "kismet.datasource.channel": "6",
            "kismet.datasource.hopping": 1,
            "kismet.datasource.hop_channels": ["1", "6", "11"],
        }]
        self.assertEqual(source_channel_status(sources), [("wlan0mon", "6", 3, True)])

    def test_recognizes_channel_group_presets_and_custom_sources(self):
        self.assertEqual(channel_plan_for("hop", "1, 6, 11"), "2.4 GHz priority (1, 6, 11)")
        self.assertEqual(channel_plan_for("hop", "3,7"), CUSTOM_HOP_PLAN)
        self.assertEqual(channel_plan_for("fixed", "36"), FIXED_CHANNEL_PLAN)
        self.assertEqual(CHANNEL_PLANS["5 GHz non-DFS"][0], "hop")

    def test_formats_gps_values(self):
        self.assertEqual(gps_text(41.25, "°"), "41.250000°")
        self.assertEqual(gps_text(None), "—")

    def test_builds_authenticated_kismet_web_url(self):
        self.assertEqual(
            kismet_web_url("wardrive launcher", "secret/token"),
            "http://127.0.0.1:2501/",
        )

    def test_extracts_access_point_view_size(self):
        views = [
            {"kismet.devices.view.id": "all", "kismet.devices.view.size": 50},
            {"kismet.devices.view.id": "phydot11_accesspoints", "kismet.devices.view.size": 17},
        ]
        self.assertEqual(access_point_count(views), 17)
        self.assertIsNone(access_point_count({}))

    def test_ap_count_falls_back_to_device_list(self):
        devices = [{"key": "ap-1"}, {"key": "ap-2"}]
        self.assertEqual(observed_access_point_count({}, devices), 2)
        self.assertEqual(observed_access_point_count([], []), 0)
        self.assertIsNone(observed_access_point_count(None, None))

    def test_ap_view_count_wins_over_device_fallback(self):
        views = [{
            "kismet.devices.view.id": "phydot11_accesspoints",
            "kismet.devices.view.size": 17,
        }]
        self.assertEqual(observed_access_point_count(views, [{"key": "partial"}]), 17)

    def test_ap_count_accepts_wrapped_and_tjson_kismet_shapes(self):
        views = {"views": [{
            "kismet_devices_view_id": "phy80211_accesspoints",
            "kismet_devices_view_size": 18,
        }]}
        devices = {"data": [{"key": "ap-1"}], "recordsTotal": 19}
        self.assertEqual(access_point_count(views), 18)
        self.assertEqual(observed_access_point_count(views, devices), 19)

    def test_ap_count_ignores_a_stale_lower_view_summary(self):
        views = [{
            "kismet.devices.view.id": "phydot11_accesspoints",
            "kismet.devices.view.size": 2,
        }]
        self.assertEqual(observed_access_point_count(views, [{}, {}, {}]), 3)

    def test_extracts_and_sorts_network_details(self):
        devices = [
            {
                "kismet.device.base.name": "Weak network",
                "kismet.device.base.signal": {"kismet.common.signal.last_signal": -82},
            },
            {"kismet.device.base.name": "Strong network", "kismet.device.base.signal": -41},
            {"kismet.device.base.name": "", "kismet.device.base.signal": {}},
        ]
        self.assertEqual(network_details(devices), [
            ("Strong network", -41), ("Weak network", -82), ("<hidden>", None),
        ])
        self.assertEqual(network_details(None), [])

    def test_attributes_unique_aps_and_packets_to_adapters(self):
        sources = [
            {"kismet.datasource.uuid": "source-1", "kismet.datasource.interface": "wlan0mon"},
            {"kismet.datasource.uuid": "source-2", "kismet.datasource.name": "rtl-usb"},
        ]
        devices = [
            {"kismet.device.base.seenby": {
                "source-1": {"kismet.common.seenby.num_packets": 12},
                "source-2": {"kismet.common.seenby.num_packets": 3},
            }},
            {"kismet.device.base.seenby": {
                "source-1": {"kismet.common.seenby.num_packets": 8},
                "unknown": {"kismet.common.seenby.num_packets": 99},
            }},
        ]

        self.assertEqual(adapter_pickup_stats(sources, devices), [
            ("wlan0mon", 2, 20), ("rtl-usb", 1, 3),
        ])

    def test_adapter_stats_tolerate_incomplete_api_data(self):
        self.assertEqual(adapter_pickup_stats(None, []), [])
        self.assertEqual(adapter_pickup_stats([{"kismet.datasource.uuid": "id"}], [{}]), [])

    def test_adapter_stats_support_current_kismet_list_shape(self):
        sources = [{
            "kismet.datasource.uuid": "source-1",
            "kismet.datasource.capture_interface": "wlan0mon",
        }]
        devices = [{"kismet.device.base.seenby": [{
            "kismet.common.seenby.uuid": "source-1",
            "kismet.common.seenby.num_packets": 42,
        }]}]

        self.assertEqual(adapter_pickup_stats(sources, devices), [("wlan0mon", 1, 42)])

    def test_adapter_stats_table_grows_then_caps_for_scrolling(self):
        self.assertEqual(adapter_stats_visible_rows(0), 3)
        self.assertEqual(adapter_stats_visible_rows(6), 6)
        self.assertEqual(adapter_stats_visible_rows(20), 8)

    def test_output_drain_yields_after_a_bounded_batch(self):
        app = WardriveApp.__new__(WardriveApp)
        app.output_queue = queue.Queue()
        for index in range(OUTPUT_BATCH_LINES + 1):
            app.output_queue.put(f"line {index}\n")
        app._append_output = MagicMock()
        app.after = MagicMock()

        WardriveApp._drain_output(app)

        self.assertEqual(app.output_queue.qsize(), 1)
        app._append_output.assert_called_once()
        self.assertEqual(app._append_output.call_args.args[0].count("\n"), OUTPUT_BATCH_LINES)
        app.after.assert_called_once_with(10, app._drain_output)

    def test_output_widget_discards_old_console_lines(self):
        app = WardriveApp.__new__(WardriveApp)
        app.output = MagicMock()
        app._output_line_count = 4999

        WardriveApp._append_output(app, "one\ntwo\nthree\n")

        app.output.delete.assert_called_once_with("1.0", "3.0")
        self.assertEqual(app._output_line_count, 5000)

    def test_extracts_only_valid_gps_tagged_map_points(self):
        devices = [{
            "kismet.device.base.key": "ap-1",
            "kismet.device.base.name": "Cafe WiFi",
            "kismet.device.base.macaddr": "00:11:22:33:44:55",
            "kismet.device.base.signal": {"kismet.common.signal.last_signal": -52},
            "kismet.device.base.location": {
                "kismet.common.location.last": {
                    "kismet.common.location.geopoint": [-82.5, 28.1],
                },
            },
        }, {"kismet.device.base.name": "No GPS"}]
        self.assertEqual(device_map_points(devices), [{
            "key": "ap-1", "name": "Cafe WiFi", "mac": "00:11:22:33:44:55",
            "lat": 28.1, "lon": -82.5, "signal": -52,
        }])

    def test_formats_capture_file_size(self):
        self.assertEqual(format_file_size(None), "Waiting for Wigle CSV…")
        self.assertEqual(format_file_size(800), "800 B")
        self.assertEqual(format_file_size(1536), "1.5 KiB")

    def test_sizes_window_for_common_and_small_displays(self):
        self.assertEqual(responsive_window_size(1920, 1080), (900, 980))
        self.assertEqual(responsive_window_size(1366, 768), (900, 668))
        self.assertEqual(responsive_window_size(800, 600), (720, 500))
        self.assertEqual(responsive_window_size(640, 480), (600, 420))

    def test_parses_wigle_session_statistics(self):
        content = (
            "WigleWifi-1.6,appRelease=Kismet\n"
            "MAC,SSID,AuthMode,FirstSeen,Channel,Frequency,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,RCOIs,MfgrId,Type\n"
            "AA:AA:AA:AA:AA:AA,Cafe,[WPA2_PSK],2026-08-17 12:00:00,6,2437,-42,1,2,0,5,,,WIFI\n"
            "BB:BB:BB:BB:BB:BB,,[ESS],2026-08-17 12:03:30,11,2462,-80,1,2,0,5,,,WIFI\n"
            "AA:AA:AA:AA:AA:AA,Cafe,[WPA2_PSK],2026-08-17 12:04:00,6,2437,-30,1,2,0,5,,,WIFI\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.wiglecsv"
            path.write_text(content, encoding="utf-8")
            stats = parse_wigle_session(path)

        self.assertEqual(stats.access_points, 2)
        self.assertEqual(stats.hidden, 1)
        self.assertEqual(stats.strongest_rssi, -30)
        self.assertEqual(stats.channels, ("6", "11"))
        self.assertEqual(dict(stats.security), {"WPA2": 1, "Open": 1})
        self.assertEqual(stats.duration_seconds, 240)
        self.assertEqual(format_duration(stats.duration_seconds), "4:00")

    def test_persists_upload_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "uploads.json"
            history = {"/captures/test.wiglecsv": "2026-08-17T12:00:00"}
            save_upload_history(history, path)
            self.assertEqual(load_upload_history(path), history)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_finds_managed_interface_for_monitor_vif(self):
        with patch("wardrive_gui.Path.exists", return_value=True):
            self.assertEqual(managed_interface_name("wlan0mon"), "wlan0")
        self.assertEqual(managed_interface_name("wlan0"), "wlan0")

    def test_finds_monitor_vif_for_managed_interface(self):
        with patch("wardrive_gui.Path.exists", return_value=True):
            self.assertEqual(monitor_interface_name("wlan0"), "wlan0mon")
            self.assertEqual(monitor_interface_name("wlan0mon"), "wlan0mon")

    def test_validates_wdgwars_api_key_shape(self):
        self.assertTrue(valid_wdgwars_api_key("a1" * 32))
        self.assertTrue(valid_wdgwars_api_key("  " + "A1" * 32 + "  "))
        self.assertFalse(valid_wdgwars_api_key("not-a-key"))
        self.assertFalse(valid_wdgwars_api_key("z" * 64))

    def test_persists_wdgwars_key_with_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "nested" / "settings.json"
            key = "ef" * 32
            save_wdgwars_api_key(key, settings)

            self.assertEqual(load_wdgwars_api_key(settings), key)
            self.assertEqual(stat.S_IMODE(settings.stat().st_mode), 0o600)

    @patch("wardrive_gui.urllib.request.urlopen")
    def test_fetches_wdgwars_account_without_exposing_key_in_url(self, urlopen):
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"ok":true,"username":"driver","total":12}')
        urlopen.return_value = response

        result = wdgwars_account("ab" * 32)

        request = urlopen.call_args.args[0]
        self.assertEqual(result["username"], "driver")
        self.assertEqual(request.full_url, "https://wdgwars.pl/api/me")
        self.assertEqual(request.get_header("X-api-key"), "ab" * 32)
        self.assertEqual(request.get_header("User-agent"), "CIACORE-Wardrive/1.0")

    @patch("wardrive_gui.time.sleep")
    @patch("wardrive_gui.urllib.request.urlopen")
    def test_retries_temporary_wdgwars_account_failure(self, urlopen, sleep):
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"ok":true,"username":"driver"}')
        urlopen.side_effect = [
            urllib.error.HTTPError("https://wdgwars.pl/api/me", 503, "busy", {}, None),
            response,
        ]

        self.assertEqual(wdgwars_account("ab" * 32)["username"], "driver")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch("wardrive_gui.urllib.request.urlopen")
    def test_uploads_wigle_csv_as_multipart(self, urlopen):
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"ok":true,"added":1}')
        urlopen.return_value = response
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "drive.wiglecsv"
            csv_path.write_text("WigleWifi-1.6\nMAC,SSID\n", encoding="utf-8")
            result = upload_wdgwars_csv("cd" * 32, csv_path)

        request = urlopen.call_args.args[0]
        self.assertEqual(result["added"], 1)
        self.assertEqual(request.full_url, "https://wdgwars.pl/api/upload-csv")
        self.assertEqual(request.get_header("User-agent"), "CIACORE-Wardrive/1.0")
        self.assertIn(b'filename="drive.csv"', request.data)
        self.assertNotIn(b'filename="drive.wiglecsv"', request.data)
        self.assertIn(b"WigleWifi-1.6", request.data)

    def test_reads_live_adsb_snapshot_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "aircraft.json"
            snapshot.write_text(json.dumps({
                "messages": 42,
                "aircraft": [{"hex": "abc123", "lat": 1.0, "lon": 2.0}, {"hex": "def456"}],
            }), encoding="utf-8")
            self.assertEqual(adsb_snapshot_status(snapshot), (2, 1, 42))

    def test_normalizes_aircraft_and_calculates_receiver_distance(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "aircraft.json"
            snapshot.write_text(json.dumps({"aircraft": [
                {"hex": "abc123", "flight": " TEST1  ", "lat": 28.0, "lon": -82.0, "seen": 1},
            ]}), encoding="utf-8")
            rows = read_adsb_aircraft(snapshot, (28.0, -82.1))
        self.assertEqual(rows[0]["flight"], "TEST1")
        self.assertGreater(rows[0]["distance_nm"], 5)
        self.assertLess(rows[0]["distance_nm"], 6)
        self.assertGreater(rows[0]["bearing"], 80)
        self.assertLess(rows[0]["bearing"], 100)

    def test_filters_aircraft_operational_views(self):
        rows = [
            {"hex": "low", "alt_baro": 5000, "distance_nm": 20, "baro_rate": 640, "seen": 2},
            {"hex": "down", "alt_baro": 30000, "distance_nm": 80, "baro_rate": -512, "seen": 12},
            {"hex": "alert", "alt_baro": 12000, "distance_nm": 60, "squawk": "7700", "seen": 1},
        ]
        self.assertEqual([r["hex"] for r in filter_adsb_aircraft(rows, "Nearby (<50 nm)")], ["low"])
        self.assertEqual([r["hex"] for r in filter_adsb_aircraft(rows, "Low (<10,000 ft)")], ["low"])
        self.assertEqual([r["hex"] for r in filter_adsb_aircraft(rows, "Climbing")], ["low"])
        self.assertEqual([r["hex"] for r in filter_adsb_aircraft(rows, "Descending")], ["down"])
        self.assertEqual([r["hex"] for r in filter_adsb_aircraft(rows, "Emergency")], ["alert"])
        self.assertEqual([r["hex"] for r in filter_adsb_aircraft(rows, "Stale (>10s)")], ["down"])

    @patch("wardrive_gui.subprocess.run")
    def test_uploads_adsb_with_muninn_without_key_on_command_line(self, run):
        run.return_value = MagicMock(returncode=0, stdout="accepted", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "aircraft.json"
            muninn = root / "muninn.py"
            snapshot.write_text('{"aircraft": []}', encoding="utf-8")
            muninn.write_text("# test", encoding="utf-8")
            detail = upload_wdgwars_adsb("ab" * 32, snapshot, muninn)
        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual(detail, "accepted")
        self.assertNotIn("ab" * 32, command)
        self.assertEqual(environment["WDGWARS_API_KEY"], "ab" * 32)
        self.assertIn("--no-save", command)

    def test_reports_wdgwars_bad_request_detail(self):
        error = urllib.error.HTTPError(
            "https://wdgwars.pl/api/upload-csv",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"ok":false,"error":"Invalid WiGLE CSV header"}'),
        )

        self.assertEqual(WardriveApp._wdgwars_error(error), "Invalid WiGLE CSV header")

    @patch("wardrive_gui.subprocess.run")
    @patch("wardrive_gui.shutil.which", return_value="/usr/bin/kismetdb_to_wiglecsv")
    def test_exports_wigle_csv_to_selected_directory(self, _which, run):
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            database = log_dir / "wardrive-test-20260817-12-00-00-1.kismet"
            database.touch()

            output, error = export_wigle_csv(log_dir, "wardrive-test")

        self.assertEqual(output, database.with_suffix(".wiglecsv"))
        self.assertEqual(error, "")
        self.assertEqual(run.call_args.args[0][4], str(database.with_suffix(".wiglecsv")))


if __name__ == "__main__":
    unittest.main()
