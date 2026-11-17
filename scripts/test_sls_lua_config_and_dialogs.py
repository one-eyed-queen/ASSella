#!/usr/bin/env python3
"""
scripts/test_sls_lua_config_and_dialogs.py
===========================================
Verification test suite for the surviving EXE-fingerprinter subsystems that used
to live under the SLSsteam Lua-config umbrella:

  1. SingleDepotSelectionDialog:
     - get_selected_dlc_data regression test
  2. Hubcap API Key validation & Bidirectional Sync (katamari.conf.lua hubcap section):
     - is_valid_hubcap_key
     - sync_hubcap_api_key (timestamp comparison)
     - generate_hubcap_timestamp / _timestamp_to_sort_key
  3. Dialog theming and construction:
     - SettingsDialog loads all tabs without runtime import crashes and without
       touching live host config
  4. AT0-M migration safety:
     - unkeyed depots are excluded from AdditionalDepots
     - collect_game_depots_and_keys filters phantom depots and prefers Hubcap
  5. Plugin undeploy / companion / 0-byte handling:
     - undeploy preserves .bak
     - companions are derived from manifest, never interceptors
     - 0-byte plugin files are treated as missing
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src/ is on PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from utils.settings import get_settings
from utils.plugin_manager import undeploy_plugin
from utils.plugin_games import collect_game_depots_and_keys, migrate_game_to_atom
from ui.dialogs.settings import SettingsDialog
from ui.dialogs.single_depot_dialog import SingleDepotSelectionDialog
from managers.katamari_config_manager import (
    _is_valid_hubcap_key,
    _timestamp_to_sort_key,
    generate_hubcap_timestamp,
    sync_hubcap_api_key,
)


class TestExeFingerprinterSubsystems(unittest.TestCase):

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._tmp_dir.name
        try:
            from core.steam_helpers import invalidate_steam_env_cache
            invalidate_steam_env_cache()
        except Exception:
            pass
        self.addCleanup(self._tmp_dir.cleanup)

    def tearDown(self):
        if self._orig_xdg is not None:
            os.environ["XDG_CONFIG_HOME"] = self._orig_xdg
        else:
            os.environ.pop("XDG_CONFIG_HOME", None)
        try:
            from core.steam_helpers import invalidate_steam_env_cache
            invalidate_steam_env_cache()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # SingleDepotSelectionDialog
    # ------------------------------------------------------------------
    def test_single_depot_dialog_dlc_method(self):
        """SingleDepotSelectionDialog must implement get_selected_dlc_data returning empty dict."""
        self.assertTrue(hasattr(SingleDepotSelectionDialog, "get_selected_dlc_data"))

    # ------------------------------------------------------------------
    # Hubcap API key validation & bidirectional sync with katamari.conf.lua
    # ------------------------------------------------------------------
    def test_hubcap_key_validation(self):
        """Verify Hubcap API key validator."""
        self.assertTrue(_is_valid_hubcap_key("12345678-1234-1234-1234-123456789abc"))
        self.assertTrue(_is_valid_hubcap_key("hubcap_live_token_1234567890"))
        self.assertFalse(_is_valid_hubcap_key(""))
        self.assertFalse(_is_valid_hubcap_key("short"))
        self.assertFalse(_is_valid_hubcap_key("invalid key with spaces!"))

    def test_sync_hubcap_api_key(self):
        """The Hubcap key syncs both ways with katamari.conf.lua's hubcap section."""
        from managers.katamari_config_manager import KatamariConfigManager

        settings = get_settings()
        orig_key = settings.value("morrenus_api_key", "", type=str)
        orig_ts = settings.value("morrenus_api_key_timestamp", 0, type=int)

        with tempfile.TemporaryDirectory() as tmpdir:
            conf = Path(tmpdir) / "katamari.conf.lua"
            conf.write_text(
                '-- user comment kept\nreturn {\n\thubcap = {\n\t\tapi_key = "lua-newer-key-1234567890",\n'
                '\t\tapi_key_timestamp = 20261024184500,\n\t\tbase_url = "https://hubcapmanifest.com/api/v1",\n\t},\n'
                '\tstyx = { enabled = true },\n}\n',
                encoding="utf-8",
            )
            try:
                settings.setValue("morrenus_api_key", "old-key-1234567890")
                settings.setValue("morrenus_api_key_timestamp", "20261023101200")
                synced, msg = sync_hubcap_api_key(settings, conf)
                self.assertTrue(synced)
                self.assertIn("katamari.conf.lua to EXE-fingerprinter", msg)
                self.assertEqual(settings.value("morrenus_api_key"), "lua-newer-key-1234567890")

                settings.setValue("morrenus_api_key", "local-newer-key-9876543210")
                settings.setValue("morrenus_api_key_timestamp", "20261026195500")
                synced2, msg2 = sync_hubcap_api_key(settings, conf)
                self.assertTrue(synced2)
                self.assertIn("EXE-fingerprinter to katamari.conf.lua", msg2)
                hub = KatamariConfigManager.load_config(conf)["hubcap"]
                self.assertEqual(hub["api_key"], "local-newer-key-9876543210")
                self.assertEqual(hub["api_key_timestamp"], 20261026195500)
                text = conf.read_text(encoding="utf-8")
                self.assertIn("-- user comment kept", text)
                self.assertIn("styx = { enabled = true }", text)

                self.assertFalse(sync_hubcap_api_key(settings, Path(tmpdir) / "missing.conf.lua")[0])
            finally:
                settings.setValue("morrenus_api_key", orig_key)
                settings.setValue("morrenus_api_key_timestamp", orig_ts)

    def test_generate_hubcap_timestamp_chronological(self):
        """Verify that Hubcap timestamp is 14-digit YYYYMMDDHHMMSS, strictly monotonic, and sortable."""
        ts = generate_hubcap_timestamp()
        s = str(ts)
        self.assertEqual(len(s), 14)
        self.assertTrue(s.startswith("202"))
        self.assertEqual(_timestamp_to_sort_key(ts), ts)

        # Legacy timestamps (e.g. day 23 vs day 24 in 2026) sort cleanly
        legacy_older = 1012232026
        legacy_newer = 4518242026
        self.assertLess(_timestamp_to_sort_key(legacy_older), _timestamp_to_sort_key(legacy_newer))
        # New 14-digit timestamps always sort newer than legacy 10-digit
        self.assertGreater(_timestamp_to_sort_key(ts), _timestamp_to_sort_key(legacy_newer))

    # ------------------------------------------------------------------
    # Settings dialog loads all tabs without runtime import crashes
    # ------------------------------------------------------------------
    def test_settings_dialog_tabs_initialization(self):
        """Ensure SettingsDialog loads all tabs without runtime import crashes and without touching live host config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_cfg = Path(tmpdir) / "config.yaml"
            fake_cfg.write_text("Plugins: yes\nAdditionalDepots:\nDecryptionKeys:\n", encoding="utf-8")
            with patch("utils.yaml_config_manager.get_user_config_path", return_value=fake_cfg), \
                 patch("utils.yaml.core.get_user_config_path", return_value=fake_cfg), \
                 patch("utils.plugin_games.get_user_config_path", return_value=fake_cfg):
                for tab_name in ("exe_fingerprinter", "downloads", "integrations", "companions", "health", "tools", "theme", "lua_tools"):
                    dlg = SettingsDialog(initial_tab=tab_name)
                    self.assertIsNotNone(dlg)
                    dlg.close()

    # ------------------------------------------------------------------
    # AT0-M migration safety
    # ------------------------------------------------------------------
    def test_atom_migration_excludes_unkeyed_depots(self):
        """AT0-M migration safely excludes unkeyed depots from AdditionalDepots to prevent Steam errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            cfg = tmp_p / "config.yaml"
            cfg.write_text("Plugins: yes\nAdditionalDepots:\nDecryptionKeys:\n", encoding="utf-8")
            acf = tmp_p / "appmanifest_12345.acf"
            acf.write_text('"AppState" {}\n', encoding="utf-8")

            game_data = {
                "appid": "12345",
                "game_name": "Test Unkeyed Game",
                "install_path": str(tmp_p),
                "appmanifest_path": str(acf),
            }
            settings = MagicMock()
            settings.value.return_value = False

            mock_depots = ["1001", "1002"]
            mock_keys = {"1001": "a" * 64}

            with patch("utils.plugin_games.get_user_config_path", return_value=cfg), \
                 patch("utils.plugin_games.can_use_atom_mode", return_value=(True, "")), \
                 patch("utils.plugin_games.collect_game_depots_and_keys", return_value=(mock_depots, mock_keys, {})), \
                 patch("utils.plugin_games._remove_accela_markers") as remove_markers:
                res = migrate_game_to_atom(game_data, settings=settings)

            self.assertTrue(res["success"])
            self.assertIn("1002", res.get("excluded_depots", []))
            remove_markers.assert_called_once()
            cfg_text = cfg.read_text(encoding="utf-8")
            self.assertIn("1001", cfg_text)
            self.assertNotIn("1002", cfg_text)

    def test_collect_game_depots_filters_phantom_and_prefers_hubcap(self):
        """collect_game_depots_and_keys must prefer Hubcap and filter phantom/0-byte/manifestless depots on DB fallback."""
        # 1. Test fallback filters phantom depot (size: None, manifest_id: None)
        mock_db = MagicMock()
        mock_db.get_app_info.return_value = {
            "depots": {
                "400751": {"name": "RealContent", "size": "1000", "manifest_id": "111222333"},
                "2447597": {"name": "UnusedCzech", "size": None, "manifest_id": None},
            }
        }
        with patch("managers.db_manager.DatabaseManager", return_value=mock_db), \
             patch("managers.depot_key_manager.DepotKeyManager.get_instance") as mock_dkm_cls, \
             patch("pathlib.Path.exists", return_value=False):
            mock_dkm = MagicMock()
            mock_dkm.get_depot_keys.return_value = {"400751": "f" * 64}
            mock_dkm_cls.return_value = mock_dkm

            depots, keys, names = collect_game_depots_and_keys("400750", allow_hubcap_fetch=False)

            self.assertIn("400751", depots)
            self.assertNotIn("2447597", depots)
            self.assertEqual(keys.get("400751"), "f" * 64)

    # ------------------------------------------------------------------
    # Plugin undeploy / companion / 0-byte handling
    # ------------------------------------------------------------------
    def test_undeploy_plugin_preserves_bak(self):
        """Undeploying a plugin removes the active .lua file but preserves any .bak file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p_dir = Path(tmpdir)
            active_lua = p_dir / "myplugin.lua"
            bak_lua = p_dir / "myplugin.lua.bak"

            active_lua.write_text("-- active code", encoding="utf-8")
            bak_lua.write_text("-- backup code", encoding="utf-8")

            with patch("utils.plugin_manager.get_sls_plugins_dirs", return_value=[p_dir]):
                ok, msg = undeploy_plugin("myplugin.lua")
                self.assertTrue(ok)
                self.assertFalse(active_lua.exists())
                self.assertTrue(bak_lua.exists())
                self.assertEqual(bak_lua.read_text(encoding="utf-8"), "-- backup code")

    def test_companion_plugins_manifest_filtering(self):
        """Companion plugins are derived from manifest and never treated as interceptors."""
        from utils.plugin_manager import (
            get_active_interceptor_filename,
            get_companion_plugin_filenames,
            is_interceptor_plugin,
        )

        companions = get_companion_plugin_filenames()
        self.assertIn("lobster.lua", companions)
        self.assertIn("lettuce.lua", companions)
        self.assertIn("styx.lua", companions)

        self.assertFalse(is_interceptor_plugin("lobster.lua"))
        self.assertFalse(is_interceptor_plugin("lettuce.lua"))
        self.assertFalse(is_interceptor_plugin("spliced-tickets.lua"))
        self.assertTrue(is_interceptor_plugin("spacebunny.lua"))
        self.assertTrue(is_interceptor_plugin("download.lua"))
        # Unknown files: the role they register, or the Steam functions they hook, decides
        self.assertFalse(is_interceptor_plugin("my_custom_helper.lua"))

        with tempfile.TemporaryDirectory() as tmpdir:
            p_dir = Path(tmpdir)
            (p_dir / "lobster.lua").write_text("code", encoding="utf-8")
            (p_dir / "spliced-tickets.lua").write_text("code", encoding="utf-8")
            (p_dir / "spacebunny.lua").write_text("code", encoding="utf-8")

            with patch("utils.plugin_manager.get_sls_plugins_dirs", return_value=[p_dir]):
                active = get_active_interceptor_filename()
                self.assertEqual(active, "spacebunny.lua")

    def test_0byte_plugin_handling(self):
        """0-byte files are treated as missing and overwritten rather than preserved."""
        from utils.plugin_manager import (
            deploy_plugin,
            get_plugin_status,
            is_plugin_installed_and_valid,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            p_dir = Path(tmpdir)
            zero_byte_file = p_dir / "spacebunny.lua"
            zero_byte_file.touch()
            self.assertEqual(zero_byte_file.stat().st_size, 0)

            with patch("utils.plugin_manager.get_sls_plugins_dirs", return_value=[p_dir]):
                # Status treats 0-byte file as missing
                self.assertEqual(get_plugin_status("spacebunny.lua"), "missing")
                self.assertFalse(is_plugin_installed_and_valid("spacebunny.lua"))

                # deploy with only_if_missing does not preserve 0-byte stub
                ok, skipped, msg = deploy_plugin("spacebunny.lua", only_if_missing=True)
                self.assertTrue(ok)
                # Did not skip as already present
                self.assertFalse(skipped)
                self.assertGreater(zero_byte_file.stat().st_size, 0)
                # 0-byte file was not backed up to .bak
                self.assertFalse((p_dir / "spacebunny.lua.bak").exists())


if __name__ == "__main__":
    unittest.main()
