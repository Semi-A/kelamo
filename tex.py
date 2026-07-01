from pathlib import Path

# مسیر پروژه
PROJECT_DIR = Path(r"C:\Users\Nima\Desktop\kalemo")  # ← تغییر بده

OUTPUT_FILE = "project_dump.md"

# پسوندهایی که می‌خواهیم
INCLUDE_EXTENSIONS = {
    ".py",
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".env",
    ".sql",
    ".html",
    ".css",
    ".js",
    ".xml",
    ".csv"
}

# پوشه‌هایی که نباید بررسی شوند
EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    "venv",
    ".venv",
    "env",
    "node_modules",
    "dist",
    "build"
}

with open(OUTPUT_FILE, "w", encoding="utf-8") as out:

    out.write("# Project Dump\n\n")

    for file in sorted(PROJECT_DIR.rglob("*")):

        if not file.is_file():
            continue

        if any(part in EXCLUDE_DIRS for part in file.parts):
            continue

        if file.suffix.lower() not in INCLUDE_EXTENSIONS:
            continue

        relative = file.relative_to(PROJECT_DIR)

        out.write("\n")
        out.write("=" * 80 + "\n")
        out.write(f"FILE: {relative}\n")
        out.write("=" * 80 + "\n\n")

        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = file.read_text(encoding="utf-8-sig")
            except:
                text = file.read_text(errors="ignore")

        out.write("```")
        out.write(file.suffix[1:] if file.suffix else "text")
        out.write("\n")
        out.write(text)
        out.write("\n```\n\n")

print("Done!")
print(f"Saved to: {OUTPUT_FILE}")