"""手动下载 Chromium 后，自动摆位到 playwright 的浏览器目录。

用法：
  1) 用浏览器（开 VPN）下载下面两个 zip：
       chrome-win64.zip
       chrome-headless-shell-win64.zip
  2) 放到同一个文件夹，例如 C:/Users/10156/Desktop/签到/pw_zips/
  3) 运行：python scripts/place_chromium.py C:/Users/10156/Desktop/签到/pw_zips
  4) 验证：python -m playwright install chromium  （应提示已安装/跳过）

脚本仅做解压 + 写 INSTALLATION_COMPLETE 标记，不联网。
"""
import argparse
import os
import zipfile

# playwright 安装根目录（与 `playwright install` 一致）
BASE = os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright")

# zip 文件名 -> 目标子目录（与 playwright 的 install location 对应）
TARGETS = {
    "chrome-win64.zip": "chromium-1228",
    "chrome-headless-shell-win64.zip": "chromium_headless_shell-1228",
}


def place(zip_path: str, folder: str) -> None:
    dest = os.path.join(BASE, folder)
    # Windows 长路径前缀，绕过 260 字符 MAX_PATH 限制
    PREFIX = "\\\\?\\"
    dest_prefix = dest if dest.startswith(PREFIX) else PREFIX + dest
    os.makedirs(dest_prefix, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for orig in zf.namelist():
            member = orig.replace("/", "\\")
            target = os.path.join(dest_prefix, member)
            if orig.endswith("/"):
                os.makedirs(target, exist_ok=True)
            else:
                d = os.path.dirname(target)
                os.makedirs(d, exist_ok=True)
                with zf.open(orig) as srcf, open(target, "wb") as outf:
                    while True:
                        chunk = srcf.read(1024 * 1024)
                        if not chunk:
                            break
                        outf.write(chunk)
    # playwright 以该标记文件判断安装完成
    with open(os.path.join(dest_prefix, "INSTALLATION_COMPLETE"), "w", encoding="utf-8"):
        pass
    print(f"[OK] {os.path.basename(zip_path)} -> {dest}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="包含下载好的两个 zip 的目录")
    args = ap.parse_args()

    if not os.path.isdir(args.src):
        print(f"目录不存在: {args.src}")
        raise SystemExit(1)

    found = False
    for name, folder in TARGETS.items():
        p = os.path.join(args.src, name)
        if os.path.exists(p):
            place(p, folder)
            found = True
        else:
            print(f"[跳过] 未找到 {name}")
    if not found:
        print("没找到任何要摆位的 zip，请确认文件名与上面一致。")
        raise SystemExit(1)
    print("\n完成。请运行验证：python -m playwright install chromium")


if __name__ == "__main__":
    main()
