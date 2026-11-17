from ui.dialogs.settings_tabs.morrenus_stats_widget import MorrenusStatsWidget
from ui.dialogs.settings_tabs.windows_depot_warning import WindowsDepotWarningDialog
from ui.dialogs.settings_tabs.morrenus_tab import (
    create_morrenus_tab,
    create_api_key_setting,
    toggle_api_key_visibility,
    test_single_gateway,
    handle_gateway_test_done,
    on_isp_gateway_changed,
    create_isp_gateway_card,
)
from ui.dialogs.settings_tabs.exe_fingerprinter_tab import create_exe_fingerprinter_tab, uninstall_exe_fingerprinter
from ui.dialogs.settings_tabs.downloads_tab import create_downloads_tab
from ui.dialogs.settings_tabs.advanced_tab import (
    create_advanced_tab,
    steamless_unchecked_warning,
)
from ui.dialogs.settings_tabs.health_tab import (
    HealthStatusTile,
    create_health_tab,
    refresh_health_tab_status,
    on_rec_setting_toggled,
    update_rec_score_badge,
    apply_health_recommended_settings,
)
from ui.dialogs.settings_tabs.tools_tab import (
    create_tools_tab,
    add_tool_button,
    run_schema_grabber_manually,
    launch_terminal_command,
    run_steamless_manually,
    run_steamless_aio_manually,
    browse_aio_script,
    update_achievements_button_state,
)
from ui.dialogs.settings_tabs.style_tab import (
    create_style_tab,
    choose_accent_color,
    reset_accent_color,
    choose_bg_color,
    reset_bg_color,
    on_preset_changed,
    is_too_dark,
    is_too_close,
    show_color_warning,
    choose_font,
    reset_font,
    update_font_button_text,
    on_titlebar_position_changed,
    save_style_settings,
    on_origins_toggled,
    fade_origins_opacity,
    paint_origins_overlay,
)
from ui.dialogs.settings_tabs.webui_tab import (
    create_webui_tab,
    check_port_availability,
    get_service_status,
    is_service_enabled,
    start_service,
    stop_service,
    enable_boot,
    disable_boot,
    update_service_status,
)
from ui.dialogs.settings_tabs.plugins_tab import create_plugins_tab
from ui.dialogs.settings_tabs.luatools_tab import create_luatools_tab

__all__ = [
    "MorrenusStatsWidget",
    "WindowsDepotWarningDialog",
    "HealthStatusTile",
    "create_exe_fingerprinter_tab",
    "create_downloads_tab",
    "create_advanced_tab",
    "create_morrenus_tab",
    "create_health_tab",
    "create_tools_tab",
    "create_style_tab",
    "create_webui_tab",
    "create_plugins_tab",
    "create_luatools_tab",
]
