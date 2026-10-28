"""dafeiyu-pack 安装器（带图形界面）

双击「安装.bat」打开界面；也可以命令行静默安装：
    python install.py --root "<core 路径>" --admin-qq <QQ> --silent
    python install.py --set-admin --root "<core 路径>" --admin-qq <QQ> --silent

管理员只有一个：写入 cmd_perm.json 的永远是一个哈希，换人即覆盖。
"""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "install_state.json")

EXPECTED_ANCHORS = {
    "astrbot/core/pipeline/waking_check/stage.py": "# 检查插件的 handler filter",
    "astrbot/core/pipeline/result_decorate/stage.py": "def _is_voice_on_demand_request",
    "astrbot/core/pipeline/respond/stage.py": "def _calc_comp_interval",
    "astrbot/core/provider/sources/edge_tts_source.py": "pyffmpeg",
}

MARKERS = {
    "astrbot/core/pipeline/waking_check/stage.py": "指令白名单（data/cmd_perm.json）",
    "astrbot/core/pipeline/result_decorate/stage.py": "_VOICE_MARKER_RE",
    "astrbot/core/pipeline/respond/stage.py": "_load_typing_per_char",
    "astrbot/core/provider/sources/edge_tts_source.py": "ffmpeg_bin",
}

CONFIG_TEMPLATES = [
    ("config_templates/addressee_config.example.json", "data/addressee_config.json"),
    (
        "config_templates/human_delay_config.example.json",
        "data/config/astrbot_plugin_human_delay_config.json",
    ),
    ("config_templates/tidy_config.example.json", "data/tidy_config.json"),
    ("config_templates/mode_state.example.json", "data/mode_state.json"),
]


# ---------------- 状态 ----------------
def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_state(root: str) -> None:
    try:
        d = load_state()
        d["root"] = root
        d["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def valid_root(root: str) -> bool:
    return bool(root) and os.path.isdir(os.path.join(root, "astrbot"))


# ---------------- 安装步骤 ----------------
def copy_plugins(root: str, log) -> None:
    src = os.path.join(HERE, "plugins")
    dst = os.path.join(root, "data", "plugins")
    os.makedirs(dst, exist_ok=True)
    for name in sorted(os.listdir(src)):
        s = os.path.join(src, name)
        if not os.path.isdir(s):
            continue
        d = os.path.join(dst, name)
        if os.path.exists(d):
            log(f"  跳过（已存在）：{name}")
            continue
        shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        log(f"  已安装插件：{name}")


def write_configs(root: str, log) -> None:
    for rel_src, rel_dst in CONFIG_TEMPLATES:
        src = os.path.join(HERE, rel_src)
        dst = os.path.join(root, rel_dst.replace("/", os.sep))
        if not os.path.exists(src):
            continue
        if os.path.exists(dst):
            log(f"  跳过（已存在）：{rel_dst}")
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        log(f"  已写入配置模板：{rel_dst}")


def patch_core(root: str, log) -> bool:
    ok_all = True
    for rel, anchor in EXPECTED_ANCHORS.items():
        target = os.path.join(root, rel.replace("/", os.sep))
        src = os.path.join(HERE, "patched", rel.replace("/", os.sep))
        if not os.path.exists(src):
            log(f"  [跳过] 包里没有 {rel}")
            continue
        if not os.path.exists(target):
            log(f"  [失败] 目标不存在：{target}")
            ok_all = False
            continue
        with open(target, "r", encoding="utf-8") as f:
            cur = f.read()
        if MARKERS.get(rel, "") and MARKERS[rel] in cur:
            log(f"  [已打过] {rel}")
            continue
        if anchor not in cur:
            log(f"  [拒绝] {rel} 找不到锚点「{anchor}」，版本不符，未替换")
            ok_all = False
            continue
        bak = target + f".bak-{time.strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(target, bak)
        shutil.copy2(src, target)
        try:
            subprocess.run(
                [sys.executable, "-m", "py_compile", target],
                check=True,
                capture_output=True,
            )
            log(f"  [已替换] {rel}（备份 {os.path.basename(bak)}）")
        except subprocess.CalledProcessError as e:
            shutil.copy2(bak, target)
            log(f"  [失败] {rel} 编译失败，已回滚：{e.stderr.decode(errors='ignore')[:200]}")
            ok_all = False
    return ok_all


def hash_admin(qq: str) -> dict:
    salt = secrets.token_hex(8)
    h = hashlib.sha256((salt + str(qq).strip()).encode()).hexdigest()
    return {"enabled": True, "salt": salt, "allowed_hashes": [h], "allowed_ids": []}


def write_cmd_perm(root: str, qq: str, log, overwrite: bool = False) -> None:
    p = os.path.join(root, "data", "cmd_perm.json")
    if os.path.exists(p) and not overwrite:
        log("  跳过（已存在）：data/cmd_perm.json")
        return
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data = hash_admin(qq)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    action = "已更新" if overwrite else "已写入"
    log(f"  {action}管理员：QQ {str(qq)[:3]}***{str(qq)[-2:]}（唯一，salt={data['salt']}）")


def do_install(root: str, admin_qq: str, log) -> bool:
    if not valid_root(root):
        log(f"[错误] {root} 里没有 astrbot 包，请选择 AstrBot 的 core 目录")
        return False
    log(f"安装到：{root}\n")
    log("[1/4] 插件")
    copy_plugins(root, log)
    log("\n[2/4] 配置模板")
    write_configs(root, log)
    log("\n[3/4] 内核补丁")
    ok = patch_core(root, log)
    log("\n[4/4] 指令白名单")
    if admin_qq:
        write_cmd_perm(root, admin_qq, log, overwrite=False)
    else:
        log("  未填管理员 QQ，跳过（之后可用「修改管理员.bat」设置）")
    save_state(root)
    log("\n完成，请重启 AstrBot。" if ok else "\n完成，但有补丁未应用，见上面的 [拒绝]/[失败]。")
    return True


def do_set_admin(root: str, admin_qq: str, log) -> bool:
    if not valid_root(root):
        log(f"[错误] {root} 里没有 astrbot 包")
        return False
    if not str(admin_qq).strip().isdigit():
        log("[错误] 管理员 QQ 必须是纯数字")
        return False
    log(f"目标：{root}\n")
    write_cmd_perm(root, str(admin_qq).strip(), log, overwrite=True)
    save_state(root)
    log("\n完成。管理员已替换（旧的哈希已作废），无需重启即可生效。")
    return True


# ---------------- 图形界面 ----------------
def run_gui(admin_only: bool = False) -> None:
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext

    state = load_state()
    root_win = tk.Tk()
    root_win.title("大肥鱼部署包 - " + ("修改管理员" if admin_only else "安装"))
    root_win.geometry("640x520")
    root_win.resizable(False, False)

    tk.Label(root_win, text="AstrBot 的 core 目录（包含 astrbot 包和 data 目录）：").pack(
        anchor="w", padx=12, pady=(12, 2)
    )
    frm_root = tk.Frame(root_win)
    frm_root.pack(fill="x", padx=12)
    ent_root = tk.Entry(frm_root)
    ent_root.pack(side="left", fill="x", expand=True)
    ent_root.insert(0, state.get("root", ""))

    def browse():
        d = filedialog.askdirectory(title="选择 AstrBot 的 core 目录")
        if d:
            ent_root.delete(0, "end")
            ent_root.insert(0, os.path.normpath(d))

    tk.Button(frm_root, text="浏览…", command=browse).pack(side="left", padx=(6, 0))

    frm_admin = tk.Frame(root_win)
    frm_admin.pack(fill="x", padx=12, pady=(10, 0))
    tk.Label(frm_admin, text="管理员 QQ（只有一个）：").pack(side="left")
    ent_admin = tk.Entry(frm_admin)
    ent_admin.pack(side="left", fill="x", expand=True, padx=(6, 0))
    ent_admin.focus_set()

    tk.Label(
        root_win,
        text="说明：管理员是唯一的。写入的是加盐哈希，配置文件里不含明文 QQ；换人即覆盖旧管理员。",
        fg="#555",
        wraplength=600,
        justify="left",
    ).pack(anchor="w", padx=12, pady=(8, 0))

    log_box = scrolledtext.ScrolledText(root_win, height=18, state="disabled")
    log_box.pack(fill="both", expand=True, padx=12, pady=10)

    def log(msg=""):
        log_box.configure(state="normal")
        log_box.insert("end", str(msg) + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")
        root_win.update_idletasks()

    def worker(admin_only_mode: bool):
        root = ent_root.get().strip().strip('"')
        qq = ent_admin.get().strip()
        try:
            if admin_only_mode:
                do_set_admin(root, qq, log)
            else:
                do_install(root, qq, log)
        except Exception as e:  # noqa: BLE001
            log(f"[异常] {type(e).__name__}: {e}")
        finally:
            for b in buttons:
                b.configure(state="normal")

    buttons = []

    def start(admin_only_mode=False):
        for b in buttons:
            b.configure(state="disabled")
        threading.Thread(target=worker, args=(admin_only_mode,), daemon=True).start()

    if admin_only:
        b1 = tk.Button(root_win, text="修改管理员", command=lambda: start(True), height=2)
    else:
        b1 = tk.Button(root_win, text="开始安装 / 修复", command=lambda: start(False), height=2)
    b1.pack(fill="x", padx=12, pady=(0, 6))
    buttons.append(b1)

    if not admin_only:
        b2 = tk.Button(root_win, text="仅修改管理员", command=lambda: start(True))
        b2.pack(fill="x", padx=12)
        buttons.append(b2)

    if not ent_root.get().strip():
        log("请先选择 AstrBot 的 core 目录（里面应该有 astrbot 文件夹）。")

    def on_close():
        if admin_only and not ent_admin.get().strip():
            if not messagebox.askokcancel("确认", "还没填管理员 QQ，确定关闭？"):
                return
        root_win.destroy()

    root_win.protocol("WM_DELETE_WINDOW", on_close)
    root_win.mainloop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="")
    ap.add_argument("--admin-qq", default="")
    ap.add_argument("--set-admin", action="store_true", help="只修改管理员")
    ap.add_argument("--silent", action="store_true", help="不打开界面（命令行模式）")
    args = ap.parse_args()

    if not args.silent and not (args.root and args.admin_qq):
        run_gui(admin_only=args.set_admin)
        return

    root = os.path.abspath(args.root or load_state().get("root", ""))
    ok = (
        do_set_admin(root, args.admin_qq, print)
        if args.set_admin
        else do_install(root, args.admin_qq, print)
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
