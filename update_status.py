import sys
import os
import re
import subprocess
import shutil
import stat
import time
import concurrent.futures
import urllib.request
import json
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
        
    version = match[1]
    
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
    theme = theme_match[1] if theme_match else None
        
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

def verify_pr_migration(pr_num, token):
    url = f"https://api.github.com/repos/keiyoushi/extensions-source/pulls/{pr_num}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.v3.diff",
        "User-Agent": "kei-extlib-migration-status"
    })
    if token:
        req.add_header("Authorization", f"Bearer {token}")
        
    migrated_files = set()
    try:
        with urllib.request.urlopen(req) as response:
            diff_text = response.read().decode('utf-8')
            
        current_file = None
        for line in diff_text.split('\n'):
            if line.startswith('+++ b/'):
                current_file = line[6:]
            elif line.startswith('+') and not line.startswith('+++'):
                if current_file and current_file.endswith('build.gradle.kts') and re.search(r'libVersion\s*=\s*"1\.6"', line):
                    migrated_files.add(current_file)
                        
    except Exception as e:
        print(f"Error fetching diff for PR {pr_num}: {e}", file=sys.stderr)
        
    return migrated_files

def _extract_touched_extensions(pr):
    touched_exts = set()
    for file in pr["files"]["nodes"]:
        path = file["path"]
        parts = path.replace("\\", "/").split("/")
        
        if parts[0] == "src" and len(parts) >= 3 and parts[-1] == "build.gradle.kts":
            touched_exts.add((parts[1], parts[2], path))
        elif parts[0] == "lib-multisrc" and len(parts) >= 2 and parts[-1] == "build.gradle.kts":
            touched_exts.add(("multisrc", parts[1], path))
    return touched_exts

def _process_pull_request(pr, token, pr_map):
    pr_num = pr["number"]
    touched_exts = _extract_touched_extensions(pr)
            
    if not touched_exts:
        return
        
    migrated_paths = verify_pr_migration(pr_num, token)
    for ext_type, ext_name, path in touched_exts:
        if path in migrated_paths:
            pr_map.setdefault((ext_type, ext_name), []).append({"number": pr_num, "url": pr["url"]})

def fetch_open_prs():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN not found, skipping PR fetch.")
        return {}
    
    print("Fetching open PRs via GraphQL...")
    query = """
    query($cursor: String) {
      repository(owner: "keiyoushi", name: "extensions-source") {
        pullRequests(states: OPEN, first: 100, after: $cursor) {
          pageInfo {
            hasNextPage
            endCursor
          }
          nodes {
            number
            url
            files(first: 100) {
              nodes {
                path
              }
            }
          }
        }
      }
    }
    """
    
    pr_map = {}
    url = "https://api.github.com/graphql"
    cursor = None
    
    while True:
        variables = {"cursor": cursor}
        payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        })
        
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                
            prs = data["data"]["repository"]["pullRequests"]
            for pr in prs["nodes"]:
                _process_pull_request(pr, token, pr_map)
            
            if not prs["pageInfo"]["hasNextPage"]:
                break
            cursor = prs["pageInfo"]["endCursor"]
        except Exception as e:
            print(f"Error fetching PRs: {e}", file=sys.stderr)
            break
            
    return pr_map

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

def generate_markdown(migrated, not_migrated, pr_map, exec_time):
    migrated_multisrc = [ext for ext in migrated if ext['type'] == 'multisrc']
    migrated_themed = [ext for ext in migrated if ext['type'] != 'multisrc' and ext.get('theme')]
    migrated_standalone = [ext for ext in migrated if ext['type'] != 'multisrc' and not ext.get('theme')]
    
    inactive_multisrc = []
    inactive_themed = []
    inactive_standalone = []
    active_multisrc = []
    active_themed = []
    active_standalone = []
    
    for ext in not_migrated:
        is_active = (ext['type'], ext['name']) in pr_map
        if ext['type'] == 'multisrc':
            if is_active:
                active_multisrc.append(ext)
            else:
                inactive_multisrc.append(ext)
        elif ext.get('theme'):
            if is_active:
                active_themed.append(ext)
            else:
                inactive_themed.append(ext)
        else:
            if is_active:
                active_standalone.append(ext)
            else:
                inactive_standalone.append(ext)
    
    migrated_standalone.sort(key=lambda x: (x["type"], x["name"]))
    inactive_standalone.sort(key=lambda x: (x["type"], x["name"]))
    active_standalone.sort(key=lambda x: (x["type"], x["name"]))
    
    def build_multisrc_table(multisrc_list, themed_list, current_pr_map=None, show_pr_column=False):
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
        if show_pr_column:
            md += "| Theme | Extensions | Open PRs |\n"
            md += "| --- | --- | --- |\n"
        else:
            md += "| Theme | Extensions |\n"
            md += "| --- | --- |\n"
            
        for theme_name in theme_names:
            exts = theme_to_exts.get(theme_name, [])
            
            # Collect theme PRs if needed
            theme_prs = set()
            if current_pr_map and show_pr_column:
                for pr in current_pr_map.get(('multisrc', theme_name), []):
                    theme_prs.add((pr['number'], pr['url']))
                for ext in exts:
                    for pr in current_pr_map.get((ext['type'], ext['name']), []):
                        theme_prs.add((pr['number'], pr['url']))
            
            if not exts:
                if show_pr_column:
                    pr_links = " ".join([f"[#{pr[0]}]({pr[1]})" for pr in sorted(list(theme_prs))])
                    pr_str = f" 🚧 {pr_links}" if pr_links else ""
                    md += f"| {theme_name} | |{pr_str} |\n"
                else:
                    md += f"| {theme_name} | |\n"
            else:
                ext_strings = []
                for ext in exts:
                    lang_display = get_language_display(ext['type'])
                    base_str = f"{ext['name']} ({lang_display})"
                    if current_pr_map and not show_pr_column:
                        prs = current_pr_map.get((ext['type'], ext['name']), [])
                        if prs:
                            pr_links = " ".join([f"[#{pr['number']}]({pr['url']})" for pr in prs])
                            base_str += f" 🚧 {pr_links}"
                    ext_strings.append(base_str)
                exts_html = "<br>".join(ext_strings)
                details = f"<details><summary>{len(exts)} extensions</summary>{exts_html}</details>"
                
                if show_pr_column:
                    pr_links = " ".join([f"[#{pr[0]}]({pr[1]})" for pr in sorted(list(theme_prs))])
                    pr_str = f" 🚧 {pr_links}" if pr_links else ""
                    md += f"| {theme_name} | {details} |{pr_str} |\n"
                else:
                    md += f"| {theme_name} | {details} |\n"
        md += "\n"
        return md

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    md = "# Keiyoushi Extension Migration Status\n\n"
    md += f"*Last updated: {now}*<br>\n"
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
            
    md += f"\n## Active Migration PRs ({len(active_multisrc) + len(active_themed) + len(active_standalone)})\n\n"
    md += build_multisrc_table(active_multisrc, active_themed, pr_map, show_pr_column=True)
        
    if active_standalone:
        md += f"### Standalone Extensions ({len(active_standalone)})\n\n"
        md += "| Extension | Language | Open PRs |\n"
        md += "| --- | --- | --- |\n"
        for ext in active_standalone:
            lang_display = get_language_display(ext['type'])
            prs = pr_map.get((ext['type'], ext['name']), [])
            pr_links = " ".join([f"[#{pr['number']}]({pr['url']})" for pr in prs])
            md += f"| {ext['name']} | {lang_display} | 🚧 {pr_links} |\n"
            
    md += f"\n## Still Needs Migration from 1.4 ({len(inactive_multisrc) + len(inactive_themed) + len(inactive_standalone)})\n\n"
    md += build_multisrc_table(inactive_multisrc, inactive_themed)
        
    if inactive_standalone:
        md += f"### Standalone Extensions ({len(inactive_standalone)})\n\n"
        md += "| Extension | Language |\n"
        md += "| --- | --- |\n"
        for ext in inactive_standalone:
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
        
        pr_map = fetch_open_prs()
        
        exec_time = time.time() - start_time
        print("Generating markdown...")
        md_content = generate_markdown(migrated, not_migrated, pr_map, exec_time)
        
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
