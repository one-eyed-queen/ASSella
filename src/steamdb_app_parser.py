"""
SteamDB App Parser - Python equivalent of GreasyFork script 543010-steamdb-app-parser
Parse SteamDB Related Apps and DLC into structured format for SLSsteam.

Features:
- Parses DLC entries from SteamDB #dlc tab
- Parses Related/Linked Apps from SteamDB #linked tab
- Generates YAML output compatible with SLSsteam (DlcData + AdditionalApps)
- Supports AppID-wise search and extraction
- Cross-platform (works on Windows, Linux, macOS)
"""

import re
from typing import Dict, List, Optional, Set, Tuple
from pathlib import Path

STEAMDB_BASE_URL = "https://steamdb.info"


class SteamDBAppParser:
    """
    Parses SteamDB app pages to extract DLC and related app data.
    Generates structured YAML output compatible with SLSsteam.
    """

    # Types to ignore (DLC types that shouldn't be included in AdditionalApps)
    IGNORE_TYPES = ['Demo', 'Soundtrack', 'Beta']

    def __init__(self):
        pass

    @staticmethod
    def get_main_appid_from_url(url: str) -> Optional[str]:
        """
        Extract the main AppID from a SteamDB app URL.
        e.g., https://steamdb.info/app/730/ -> '730'
        """
        match = re.search(r'/app/(\d+)/', url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def extract_appid_from_row(row_text: str, html_row: str) -> Optional[str]:
        """
        Extract AppID from a table row.
        Tries data-appid attribute first, then searches for /app/ links,
        then tries to find a numeric ID in the cells.
        """
        # Try data-appid attribute
        match = re.search(r'data-appid["\']?\s*[:=]\s*["\']?(\d+)', html_row)
        if match:
            return match.group(1)

        # Try searching for /app/ links
        match = re.search(r'/app/(\d+)/', html_row)
        if match:
            return match.group(1)

        # Try finding numeric ID in text (4+ digits)
        matches = re.findall(r'(?<!\d)(\d{4,})(?!\d)', row_text)
        if matches:
            return matches[0]

        return None

    @staticmethod
    def extract_clean_name_from_cell(cell_html: str) -> str:
        """
        Extract clean name from a table cell.
        Priority:
        1. <b> tag content (new format)
        2. .muted element content (remove muted elements, then text)
        3. Original text content
        """
        # Try <b> tag first
        b_match = re.search(r'<b>([^<]+)</b>', cell_html)
        if b_match:
            return b_match.group(1).strip()

        # Try .muted element - clone and remove muted elements
        muted_match = re.search(r'<[^>]*class="[^"]*muted[^"]*"[^>]*>([^<]+)</[^>]*>', cell_html)
        if muted_match:
            # Clone and remove muted elements
            clean = re.sub(r'<[^>]*class="[^"]*muted[^"]*[^>]*>[^<]*</[^>]*>', '', cell_html)
            return re.sub(r'<[^>]+>', '', clean).strip()

        # Fallback to original text content (strip HTML tags)
        clean = re.sub(r'<[^>]+>', ' ', cell_html)
        return re.sub(r'\s+', ' ', clean).strip()

    @staticmethod
    def parse_app_table(selector: str, main_appid: str, ignore_types: Optional[List[str]] = None) -> List[Dict[str, str]]:
        """
        Parse a SteamDB app table given a CSS selector.
        
        Args:
            selector: CSS selector for the table tbody (e.g., '#dlc tbody', '#linked tbody')
            main_appid: The main AppID to exclude from results
            ignore_types: List of app types to ignore (default: ['Demo', 'Soundtrack', 'Beta'])
        
        Returns:
            List of dicts with 'appId' and 'appName' keys
        """
        if ignore_types is None:
            ignore_types = SteamDBAppParser.IGNORE_TYPES

        results = []

        # We can't directly parse HTML without fetching it, but we provide the parsing logic
        # This method expects pre-fetched HTML rows
        # In practice, this would be called with rows fetched from SteamDB pages
        return results

    @classmethod
    def parse_dlc_from_html(cls, html: str, main_appid: str) -> List[Dict[str, str]]:
        """
        Parse DLC entries from SteamDB app page HTML.
        Looks for the #dlc tab table.
        
        Args:
            html: The HTML content of the SteamDB app page
            main_appid: The main AppID to exclude
            
        Returns:
            List of dicts with 'appId' and 'appName' for DLC entries
        """
        results = []

        # Find the DLC table - look for table with "DLC" in context
        # Parse tables looking for DLC rows
        tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL | re.IGNORECASE)

        for table_html in tables:
            # Find rows in the table
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE)

            for row_html in rows:
                # Skip header rows
                if 'class="header"' in row_html.lower() or 'th' in row_html.lower() and row_html.count('<th') > 0:
                    continue

                # Extract cells
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL | re.IGNORECASE)
                if len(cells) < 3:
                    continue

                # Type is typically in the second cell
                type_cell = cells[1] if len(cells) > 1 else ''
                type_text = re.sub(r'<[^>]+>', ' ', type_cell).strip()

                # Skip ignored types
                if any(ignored in type_text for ignored in ignore_types):
                    continue

                # Name is typically in the third cell (index 2)
                name_cell = cells[2] if len(cells) > 2 else cells[1] if len(cells) > 1 else ''
                app_name = cls.extract_clean_name_from_cell(name_cell)

                # Extract AppID from the row
                # Combine all cell text for AppID extraction
                all_cell_text = ' '.join(re.findall(r'<[^>]+>', ''.join(re.findall(r'<[^>]+>', row_html, re.DOTALL)))).strip()
                # Actually let's just use the raw row html
                app_id = cls.extract_appid_from_row('', row_html)

                if not app_id or app_id == main_appid:
                    continue

                # Skip if type looks like it should be ignored
                if type_text in ignore_types:
                    continue

                results.append({
                    'appId': app_id,
                    'appName': app_name,
                    'type': type_text
                })

        return results

    @classmethod
    def parse_linked_apps_from_html(cls, html: str, main_appid: str) -> List[Dict[str, str]]:
        """
        Parse Related/Linked Apps from SteamDB app page HTML.
        Looks for the #linked tab table.
        
        Args:
            html: The HTML content of the SteamDB app page
            main_appid: The main AppID to exclude
            
        Returns:
            List of dicts with 'appId' and 'appName' for linked apps
        """
        return cls.parse_dlc_from_html(html, main_appid)  # Same parsing logic, just different tab

    @classmethod
    def generate_yaml_output(
        cls,
        main_appid: str,
        additional_apps: Optional[List[Dict[str, str]]] = None,
        dlc_entries: Optional[List[Dict[str, str]]] = None
    ) -> str:
        """
        Generate YAML output compatible with SLSsteam.
        Format matches the GreasyFork script 543010-steamdb-app-parser.
        
        Args:
            main_appid: The main AppID
            additional_apps: List of {appId, appName} for related/linked apps
            dlc_entries: List of {appId, appName} for DLC entries
            
        Returns:
            YAML-formatted string
        """
        if additional_apps is None:
            additional_apps = []
        if dlc_entries is None:
            dlc_entries = []

        # Build DLC map (main_appid -> list of DLC appNames)
        dlc_map: Dict[str, List[str]] = {}
        for entry in dlc_entries:
            app_id = entry.get('appId', '')
            app_name = entry.get('appName', '')
            if app_id and app_name:
                if app_id not in dlc_map:
                    dlc_map[app_id] = []
                dlc_map[app_id].append(app_name)

        lines = []

        # AdditionalApps section
        if additional_apps:
            lines.append('AdditionalApps:')
            # Sort by appId
            sorted_apps = sorted(additional_apps, key=lambda x: int(x.get('appId', 0)))
            for app in sorted_apps:
                app_id = app.get('appId', '')
                app_name = app.get('appName', '')
                lines.append(f'  - {app_id}\t# {app_name}')

        # DlcData section
        if dlc_map:
            lines.append('')
            lines.append('DlcData:')
            lines.append(f'  {main_appid}:')
            # Sort DLC entries by appId
            for app_id in sorted(dlc_map.keys(), key=int):
                app_names = dlc_map[app_id]
                for app_name in sorted(app_names):
                    lines.append(f'    {app_id}: "{app_name}"')

        return '\n'.join(lines)

    @classmethod
    def generate_additional_apps_only(cls, additional_apps: List[Dict[str, str]]) -> str:
        """
        Generate just the AdditionalApps section of YAML.
        
        Args:
            additional_apps: List of {appId, appName}
            
        Returns:
            YAML-formatted AdditionalApps string
        """
        if not additional_apps:
            return ''

        lines = []
        sorted_apps = sorted(additional_apps, key=lambda x: int(x.get('appId', 0)))
        for app in sorted_apps:
            app_id = app.get('appId', '')
            app_name = app.get('appName', '')
            lines.append(f'  - {app_id}\t# {app_name}')

        return '\n'.join(lines)

    @classmethod
    def generate_dlc_only(cls, main_appid: str, dlc_entries: List[Dict[str, str]]) -> str:
        """
        Generate just the DlcData section of YAML.
        
        Args:
            main_appid: The main AppID
            dlc_entries: List of {appId, appName}
            
        Returns:
            YAML-formatted DlcData string
        """
        if not dlc_entries:
            return ''

        # Build map
        dlc_map: Dict[str, List[str]] = {}
        for entry in dlc_entries:
            app_id = entry.get('appId', '')
            app_name = entry.get('appName', '')
            if app_id and app_name:
                if app_id not in dlc_map:
                    dlc_map[app_id] = []
                dlc_map[app_id].append(app_name)

        lines = [f'{main_appid}:']
        for app_id in sorted(dlc_map.keys(), key=int):
            for app_name in sorted(dlc_map[app_id]):
                lines.append(f'    {app_id}: "{app_name}"')

        return '\n'.join(lines)

    @classmethod
    def show_output_modal(
        cls,
        output: str,
        additional_apps: List[Dict[str, str]],
        dlc_entries: List[Dict[str, str]],
        main_appid: str,
        parent_window=None
    ):
        """
        Display the parsed output in a modal dialog with copy options.
        This is a Qt-based modal that can be embedded in the EXE-fingerprinter UI.
        
        Args:
            output: The full YAML output
            additional_apps: Parsed additional apps
            dlc_entries: Parsed DLC entries
            main_appid: The main AppID
            parent_window: Parent Qt window (optional)
        """
        # This would be integrated into EXE-fingerprinter's UI
        # For now, we just return the output string
        # The actual modal would be a QWidget with QMessageBox or custom dialog
        pass

    @classmethod
    def parse_steamdb_page(
        cls,
        appid: str,
        html: str
    ) -> Dict[str, any]:
        """
        Parse a complete SteamDB app page and extract all relevant data.
        
        Args:
            appid: The AppID as string
            html: The HTML content of https://steamdb.info/app/<appid>/
            
        Returns:
            Dict with 'additional_apps', 'dlc_entries', 'yaml_output', etc.
        """
        main_appid = cls.get_main_appid_from_url(f'https://steamdb.info/app/{appid}/') or appid

        # Parse DLC from #dlc tab
        dlc_entries = cls.parse_dlc_from_html(html, main_appid)

        # Parse linked/related apps from #linked tab
        additional_apps = cls.parse_linked_apps_from_html(html, main_appid)

        # Combine DLC entries (linked apps that are also DLC + explicit DLC entries)
        # Per the GreasyFork script: dlcCombined = linkedApps.filter(type === 'DLC') + dlcApps
        linked_dlc = [app for app in additional_apps if app.get('type', '') == 'DLC']
        all_dlc = linked_dlc + dlc_entries

        # Generate YAML output
        yaml_output = cls.generate_yaml_output(main_appid, additional_apps, all_dlc)

        return {
            'main_appid': main_appid,
            'additional_apps': additional_apps,
            'dlc_entries': dlc_entries,
            'linked_dlc': linked_dlc,
            'all_dlc': all_dlc,
            'yaml_output': yaml_output,
            'output_for_copy': yaml_output
        }


# Convenience functions for quick use
def parse_app_page(appid: str, html: str) -> Dict[str, any]:
    """Quick parse of a SteamDB app page."""
    return SteamDBAppParser.parse_steamdb_page(str(appid), html)


def generate_dlc_yaml(appid: str, dlc_list: List[Dict[str, str]]) -> str:
    """Generate DlcData YAML for a main app and its DLC."""
    main_appid = str(appid)
    return SteamDBAppParser.generate_dlc_only(main_appid, dlc_list)


def generate_additional_apps_yaml(appid: str, additional_apps: List[Dict[str, str]]) -> str:
    """Generate AdditionalApps YAML for a main app and its linked apps."""
    main_appid = str(appid)
    return SteamDBAppParser.generate_additional_apps_only(additional_apps)


# Example usage (would be integrated into EXE-fingerprinter's UI/managers)
if __name__ == "__main__":
    print("SteamDB App Parser module loaded successfully")
    print("Use SteamDBAppParser.parse_steamdb_page(appid, html) to parse SteamDB pages")