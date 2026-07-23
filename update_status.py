import sys
import os
import re
import subprocess
import shutil
import stat
import time
import concurrent.futures
from datetime import datetime, timezone

REPO_URL = "https://github.com/keiyoushi/extensions-source.git"
TEMP_DIR = "temp_repo"

def on_rm_error(func, path, exc_info):
    """
    Error handler for `shutil.rmtree`.
    If the error is due to an access error (read only file)
    it attempts to add write permission and then retries.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)

def clone_repo():
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR, onerror=on_rm_error)
    print("Cloning repository (sparse checkout)...")
    subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", REPO_URL, TEMP_DIR], check=True)
    subprocess.run(["git", "sparse-checkout", "set", "src", "lib-multisrc"], cwd=TEMP_DIR, check=True)

def parse_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    match = re.search(r'libVersion\s*=\s*"([^"]+)"', content)
    if not match:
        return None
        
    version = match.group(1)
    
    # Extract extension name and language/type
    rel_path = os.path.relpath(os.path.dirname(file_path), TEMP_DIR)
    parts = rel_path.replace("\\", "/").split("/")
    
    if parts[0] == "src" and len(parts) >= 3:
        ext_type = parts[1]
        ext_name = parts[2]
    elif parts[0] == "lib-multisrc" and len(parts) >= 2:
        ext_type = "multisrc"
        ext_name = parts[1]
    else:
        return None
        
    theme_match = re.search(r'theme\s*=\s*"([^"]+)"', content)
    theme = theme_match.group(1) if theme_match else None
        
    entry = {"name": ext_name, "type": ext_type, "theme": theme}
    return entry, version

def parse_versions():
    migrated = []
    not_migrated = []
    
    paths_to_check = [
        os.path.join(TEMP_DIR, "src"),
        os.path.join(TEMP_DIR, "lib-multisrc")
    ]
    
    files_to_parse = []
    for base_path in paths_to_check:
        if not os.path.exists(base_path):
            continue
            
        files_to_parse.extend(
            os.path.join(root, "build.gradle.kts")
            for root, _, files in os.walk(base_path)
            if "build.gradle.kts" in files
        )
                
    with concurrent.futures.ThreadPoolExecutor() as executor:
        results = executor.map(parse_file, files_to_parse)
        
    for res in results:
        if res:
            entry, version = res
            if version == "1.6":
                migrated.append(entry)
            elif version == "1.4":
                not_migrated.append(entry)
                
    return migrated, not_migrated

LANGUAGE_FLAGS = {
    "all": "🌐",
    "ar": "🇸🇦",
    "bg": "🇧🇬",
    "ca": "🇦🇩",
    "de": "🇩🇪",
    "en": "🇬🇧",
    "es": "🇪🇸",
    "fa": "🇮🇷",
    "fr": "🇫🇷",
    "he": "🇮🇱",
    "hi": "🇮🇳",
    "hu": "🇭🇺",
    "id": "🇮🇩",
    "it": "🇮🇹",
    "ja": "🇯🇵",
    "ko": "🇰🇷",
    "nl": "🇳🇱",
    "pl": "🇵🇱",
    "pt": "🇧🇷",
    "pt-BR": "🇧🇷",
    "ro": "🇷🇴",
    "ru": "🇷🇺",
    "th": "🇹🇭",
    "tl": "🇵🇭",
    "tr": "🇹🇷",
    "uk": "🇺🇦",
    "vi": "🇻🇳",
    "zh": "🇨🇳",
    "zh-HK": "🇭🇰",
    "zh-TW": "🇹🇼",
}

def get_language_display(lang):
    flag = LANGUAGE_FLAGS.get(lang, "🏳️")
    return f"{flag} {lang}"

def generate_markdown(migrated, not_migrated, exec_time):
    migrated_multisrc = [ext for ext in migrated if ext['type'] == 'multisrc']
    migrated_themed = [ext for ext in migrated if ext['type'] != 'multisrc' and ext.get('theme')]
    migrated_standalone = [ext for ext in migrated if ext['type'] != 'multisrc' and not ext.get('theme')]
    
    not_migrated_multisrc = [ext for ext in not_migrated if ext['type'] == 'multisrc']
    not_migrated_themed = [ext for ext in not_migrated if ext['type'] != 'multisrc' and ext.get('theme')]
    not_migrated_standalone = [ext for ext in not_migrated if ext['type'] != 'multisrc' and not ext.get('theme')]
    
    migrated_standalone.sort(key=lambda x: (x["type"], x["name"]))
    not_migrated_standalone.sort(key=lambda x: (x["type"], x["name"]))
    
    def build_multisrc_table(multisrc_list, themed_list):
        theme_to_exts = {}
        for ext in themed_list:
            theme = ext['theme']
            if theme not in theme_to_exts:
                theme_to_exts[theme] = []
            theme_to_exts[theme].append(ext)
            
        for exts in theme_to_exts.values():
            exts.sort(key=lambda x: (x["type"], x["name"]))
            
        all_themes = {t['name'] for t in multisrc_list}
        all_themes.update(ext['theme'] for ext in themed_list)
        
        theme_names = sorted(list(all_themes))
        
        if not theme_names:
            return ""
            
        md = f"### Multisrc Themes ({len(theme_names)})\n\n"
        md += "| Theme | Extensions |\n"
        md += "| --- | --- |\n"
        for theme_name in theme_names:
            exts = theme_to_exts.get(theme_name, [])
            if not exts:
                md += f"| {theme_name} | |\n"
            else:
                ext_strings = []
                for ext in exts:
                    lang_display = get_language_display(ext['type'])
                    ext_strings.append(f"{ext['name']} ({lang_display})")
                exts_html = "<br>".join(ext_strings)
                details = f"<details><summary>{len(exts)} extensions</summary>{exts_html}</details>"
                md += f"| {theme_name} | {details} |\n"
        md += "\n"
        return md

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    md = "# Keiyoushi Extension Migration Status\n\n"
    md += f"*Last updated: {now}*\n"
    md += f"*Execution time: {exec_time:.2f} seconds*\n\n"
    md += "This repository automatically tracks the migration of extensions from `libVersion 1.4` to `1.6` in the [Keiyoushi extensions-source](https://github.com/keiyoushi/extensions-source) repository.\n\n"
    md += "The data is automatically generated and updated every 6 hours via GitHub Actions.\n\n"
    
    md += f"## Migrated to 1.6 ({len(migrated)})\n\n"
    md += build_multisrc_table(migrated_multisrc, migrated_themed)
        
    if migrated_standalone:
        md += f"### Standalone Extensions ({len(migrated_standalone)})\n\n"
        md += "| Extension | Language |\n"
        md += "| --- | --- |\n"
        for ext in migrated_standalone:
            lang_display = get_language_display(ext['type'])
            md += f"| {ext['name']} | {lang_display} |\n"
            
    md += f"\n## Still Needs Migration from 1.4 ({len(not_migrated)})\n\n"
    md += build_multisrc_table(not_migrated_multisrc, not_migrated_themed)
        
    if not_migrated_standalone:
        md += f"### Standalone Extensions ({len(not_migrated_standalone)})\n\n"
        md += "| Extension | Language |\n"
        md += "| --- | --- |\n"
        for ext in not_migrated_standalone:
            lang_display = get_language_display(ext['type'])
            md += f"| {ext['name']} | {lang_display} |\n"
        
    return md

def main():
    start_time = time.time()
    success = False
    try:
        clone_repo()
        print("Parsing versions...")
        migrated, not_migrated = parse_versions()
        
        exec_time = time.time() - start_time
        print("Generating markdown...")
        md_content = generate_markdown(migrated, not_migrated, exec_time)
        
        with open("README.md", "w", encoding="utf-8") as f:
            f.write(md_content)
        success = True
            
    except Exception as e:
        print(f"Error during execution: {e}", file=sys.stderr)
        
    finally:
        print("Cleaning up...")
        if os.path.exists(TEMP_DIR):
            try:
                shutil.rmtree(TEMP_DIR, onerror=on_rm_error)
            except Exception as e:
                print(f"Warning: could not delete {TEMP_DIR}: {e}")
                
    if not success:
        sys.exit(1)
        
    print("Done!")

if __name__ == "__main__":
    main()
