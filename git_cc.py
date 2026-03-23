#!/usr/bin/env python3
import os
import subprocess
import json
import urllib.request
import urllib.error

# ─── CONFIG ───────────────────────────────────────────────────────────────────
GITHUB_USERNAME = "logicencoder"
GITHUB_EMAIL    = "240409637+logicencoder@users.noreply.github.com"
# ──────────────────────────────────────────────────────────────────────────────

PRESETS = {
    "1": ("Python",  ["__pycache__/", "*.pyc", "*.pyo", ".env", "venv/", ".venv/", "dist/", "build/", "*.egg-info/"]),
    "2": ("Node.js", ["node_modules/", "dist/", ".env", "npm-debug.log", "yarn-error.log"]),
    "3": ("C / C++", ["*.o", "*.out", "*.exe", "*.a", "*.so", "build/", "cmake-build-*/", ".cache/"]),
    "4": ("Java",    ["*.class", "*.jar", "target/", ".gradle/", "build/"]),
    "5": ("General", [".DS_Store", "Thumbs.db", "*.log", "*.tmp", "*.swp", ".idea/", ".vscode/"]),
}

def run(cmd, capture=False):
    if capture:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    else:
        code = subprocess.call(cmd, shell=True)
        return None, None, code

def choose(prompt, options):
    """Show numbered options, user types a number."""
    while True:
        print(f"\n  {prompt}")
        for i, opt in enumerate(options, 1):
            print(f"   {i} = {opt}")
        val = input("  Your choice: ").strip()
        if val.isdigit() and 1 <= int(val) <= len(options):
            return options[int(val) - 1].lower().split()[0]
        print(f"  ⚠️  Type a number between 1 and {len(options)}")

def ask_text(prompt):
    """Ask for free text input."""
    while True:
        val = input(f"\n  {prompt} ").strip()
        if val:
            return val
        print("  ⚠️  Please type something, don't leave it empty.")

def section(title):
    print(f"\n{'─'*50}")
    print(f"  {title}")
    print(f"{'─'*50}")

def check_git_installed():
    section("🔍 Checking Git installation")
    _, _, code = run("git --version", capture=True)
    if code != 0:
        print("❌ Git is NOT installed on your machine.")
        print("   Fix: run this command first:")
        print("        sudo apt install git")
        while True:
            input("\n   Press Enter to retry after installing... ")
            _, _, code = run("git --version", capture=True)
            if code == 0:
                print("✅ Git is now installed!")
                return
            print("❌ Still not found. Make sure the install finished without errors.")
    else:
        out, _, _ = run("git --version", capture=True)
        print(f"✅ Git found: {out}")

def get_folder():
    section("📁 Project Folder")
    current = os.getcwd()
    print(f"  You are currently in:")
    print(f"  👉 {current}")
    choice = choose("Which folder to use?", ["Use this folder", "Choose a different folder"])
    if choice == "use":
        print(f"✅ Using: {current}")
        return current
    while True:
        folder = ask_text("Enter the full path to your folder:")
        if os.path.isdir(folder):
            os.chdir(folder)
            print(f"✅ Moved into: {folder}")
            return folder
        else:
            print(f"❌ That folder doesn't exist: {folder}")
            print("   Example: /home/yourname/myapp")

def get_token():
    section("🔑 GitHub Token")
    print("  You need a Personal Access Token from GitHub.")
    print("  Don't have one? Go to:")
    print("  https://github.com/settings/tokens → Generate new token (classic)")
    print("  Make sure to tick the 'repo' checkbox!")
    while True:
        token = ask_text("Paste your GitHub token:")
        if token.startswith("ghp_") or token.startswith("github_pat_"):
            print("✅ Token looks good.")
            return token
        else:
            print("⚠️  That doesn't look like a valid GitHub token.")
            print("   It should start with 'ghp_' or 'github_pat_'")
            choice = choose("What do you want to do?", ["Try again", "Use it anyway"])
            if choice == "use":
                return token

def do_gitignore():
    section("🙈 .gitignore Setup")
    gitignore_path = os.path.join(os.getcwd(), ".gitignore")
    existing_lines = []

    if os.path.exists(gitignore_path):
        with open(gitignore_path, "r") as f:
            existing_lines = [l.strip() for l in f.readlines() if l.strip()]
        print(f"✅ .gitignore already exists with {len(existing_lines)} entries:")
        for l in existing_lines:
            print(f"   {l}")
        choice = choose("What do you want to do?", ["Keep it as is", "Add more entries", "Replace everything"])
        if choice == "keep":
            print("⏭️  Keeping existing .gitignore.")
            return
        elif choice == "replace":
            existing_lines = []

    new_entries = list(existing_lines)

    # ── Presets ──
    print("\n  Available presets (you can pick multiple):")
    for key, (name, _) in PRESETS.items():
        print(f"   {key} = {name}")
    print("   0 = Skip presets")

    while True:
        picks = input("\n  Enter preset numbers separated by spaces (e.g. 1 3 5), or 0 to skip: ").strip()
        if not picks:
            continue
        tokens = picks.split()
        invalid = [t for t in tokens if t not in PRESETS and t != "0"]
        if invalid:
            print(f"  ⚠️  Invalid choices: {', '.join(invalid)}. Use numbers from the list above.")
            continue
        if "0" not in tokens:
            for t in tokens:
                _, entries = PRESETS[t]
                for e in entries:
                    if e not in new_entries:
                        new_entries.append(e)
            print("  ✅ Added preset entries.")
        break

    # ── Custom entries ──
    print("\n  Now add your own files or folders to ignore.")
    print("  ┌─────────────────────────────────────────────────────┐")
    print("  │  FORMAT GUIDE                                        │")
    print("  │                                                      │")
    print("  │  Specific file:        secrets.txt                  │")
    print("  │  Specific folder:      build/       (slash at end!) │")
    print("  │  All files by type:    *.log        (* = any name)  │")
    print("  │  File in subfolder:    config/db.json               │")
    print("  │  Any folder by name:   __pycache__/                 │")
    print("  │  All files with name:  .env                         │")
    print("  └─────────────────────────────────────────────────────┘")
    print("  Type one entry at a time. Press Enter on empty line when done.")

    while True:
        entry = input("\n  Add entry (or press Enter to finish): ").strip()
        if not entry:
            break
        if entry in new_entries:
            print(f"  ⚠️  '{entry}' is already in the list, skipping.")
        else:
            new_entries.append(entry)
            print(f"  ✅ Added: {entry}")

    if not new_entries:
        print("  No entries — skipping .gitignore creation.")
        return

    print(f"\n  Final .gitignore will contain {len(new_entries)} entries:")
    for e in new_entries:
        print(f"   {e}")

    choice = choose("Save this .gitignore?", ["Yes, save it", "No, skip it"])
    if choice == "no,":
        print("⏭️  Skipped .gitignore.")
        return

    with open(gitignore_path, "w") as f:
        f.write("\n".join(new_entries) + "\n")
    print("✅ .gitignore saved!")

def do_git_init():
    section("1️⃣  Git Init")
    _, _, code = run("git rev-parse --is-inside-work-tree", capture=True)
    if code == 0:
        print("✅ Already a git repo — skipping init.")
        return
    print("⚙️  Initializing git repo...")
    run(f'git config --global user.name "{GITHUB_USERNAME}"')
    run(f'git config --global user.email "{GITHUB_EMAIL}"')
    _, err, code = run("git init", capture=True)
    if code != 0:
        print(f"❌ git init failed: {err}")
        print("   Possible reason: no write permission to this folder.")
        print("   Try: chmod -R u+w " + os.getcwd())
        input("   Fix it and press Enter to retry... ")
        do_git_init()
        return
    run("git branch -M main")
    print("✅ Git initialized!")

def do_commit():
    section("2️⃣  Commit your files")
    _, _, code = run("git log --oneline -1", capture=True)
    already_committed = (code == 0)

    if already_committed:
        choice = choose("Repo already has commits. Make a new commit?", ["Yes, commit new changes", "No, skip"])
        if choice == "no,":
            print("⏭️  Skipping commit.")
            return

    msg = input("\n  Enter commit message (press Enter to use 'update'): ").strip()
    if not msg:
        msg = "update"

    run("git add .")
    _, err, code = run(f'git commit -m "{msg}"', capture=True)
    if code != 0:
        if "nothing to commit" in err or "nothing added" in err:
            print("⚠️  Nothing to commit — files haven't changed since last commit.")
            print("   That's fine, you can still push.")
        else:
            print(f"❌ Commit failed: {err}")
            choice = choose("What do you want to do?", ["Try again", "Skip commit"])
            if choice == "try":
                do_commit()
    else:
        print("✅ Committed!")

def create_github_repo(token, repo_name, private):
    url = "https://api.github.com/user/repos"
    payload = json.dumps({"name": repo_name, "private": private}).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"token {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            return data.get("html_url"), None
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return None, (e.code, body)
    except urllib.error.URLError as e:
        return None, (0, str(e))

def do_connect(token):
    section("3️⃣  Connect to GitHub Repo")
    out, _, code = run("git remote get-url origin", capture=True)
    if code == 0 and out:
        print(f"✅ Already connected to: {out}")
        choice = choose("What do you want to do?", ["Keep this connection", "Connect to a different repo"])
        if choice == "keep":
            repo_name = out.rstrip(".git").split("/")[-1]
            return token, repo_name
        run("git remote remove origin")

    while True:
        repo_name = ask_text("Enter new repo name (no spaces, use dashes e.g. my-app):")
        if " " in repo_name:
            print("❌ No spaces allowed. Use dashes, e.g. my-app")
            continue

        visibility = choose("Public or private repo?", ["Public", "Private"])
        is_private = visibility == "private"

        print(f"\n⚙️  Creating '{repo_name}' on GitHub ({visibility})...")
        html_url, err = create_github_repo(token, repo_name, is_private)

        if err:
            code, body = err
            if code == 0:
                print("❌ Could not reach GitHub. Check your internet connection.")
            elif code == 401:
                print("❌ GitHub rejected your token (invalid or expired).")
                print("   Go to: https://github.com/settings/tokens")
                print("   Generate a new token with 'repo' permission checked.")
                token = ask_text("Paste your new token:")
                continue
            elif code == 422:
                print(f"❌ A repo named '{repo_name}' already exists on your account.")
                print("   Choose a different name.")
                continue
            else:
                print(f"❌ GitHub error {code}: {body}")
            choice = choose("What do you want to do?", ["Try again", "Skip this step"])
            if choice == "skip":
                return token, None
            continue

        auth_url = f"https://{token}@github.com/{GITHUB_USERNAME}/{repo_name}.git"
        run(f"git remote add origin {auth_url}")
        print(f"✅ Repo created and connected: {html_url}")
        return token, repo_name

def do_push(token, repo_name):
    section("4️⃣  Push to GitHub")
    if not repo_name:
        print("⚠️  No repo connected — skipping push.")
        print("   Run the script again and go through the connect step.")
        return

    choice = choose("Push your files to GitHub now?", ["Yes, push!", "No, skip"])
    if choice == "no,":
        print("⏭️  Skipped. Run the script again whenever you want to push.")
        return

    print("⚙️  Pushing...")
    run("git branch -M main")
    _, err, code = run("git push -u origin main", capture=True)

    if code == 0:
        print(f"\n🎉 Done! Your code is live at:")
        print(f"   https://github.com/{GITHUB_USERNAME}/{repo_name}")
    else:
        if "rejected" in err and "fetch first" in err:
            print("⚠️  GitHub has stuff your local repo doesn't (e.g. a README).")
            choice = choose("What do you want to do?", ["Force push (overwrites remote)", "Skip push"])
            if choice == "force":
                _, err2, code2 = run("git push -u origin main --force", capture=True)
                if code2 == 0:
                    print(f"✅ Force pushed! Live at: https://github.com/{GITHUB_USERNAME}/{repo_name}")
                else:
                    print(f"❌ Force push also failed: {err2}")
                    print("   Try running the script again from scratch.")
        elif "authentication" in err.lower() or "could not read" in err.lower():
            print("❌ Authentication failed during push.")
            print("   Your token is wrong or missing 'repo' permissions.")
            print("   Go to: https://github.com/settings/tokens and make a new one.")
        elif "does not exist" in err:
            print("❌ Remote repo not found.")
            print(f"   Check: https://github.com/{GITHUB_USERNAME}")
        else:
            print(f"❌ Push failed: {err}")
            print("   Common fixes:")
            print("   - Check your internet connection")
            print("   - Make sure your token has 'repo' permissions")
            print("   - Run the script again")

def main():
    print("\n╔══════════════════════════════════╗")
    print("║      GitHub Push Helper 🚀        ║")
    print("╚══════════════════════════════════╝")

    check_git_installed()
    get_folder()
    token = get_token()
    do_git_init()
    do_gitignore()
    do_commit()
    token, repo_name = do_connect(token)
    do_push(token, repo_name)

    print("\n✅ All done! Run this script again any time to commit & push.\n")

if __name__ == "__main__":
    main()