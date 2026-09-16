#!/usr/bin/env python3
"""池子引导脚本 —— 把仓库骨架建起来，并做出 genesis 提交。

它会做：
  1. 建 data/ 四份空 JSONL
  2. 初始化 git 仓库（SHA-256）
  3. 生成第一把密钥（Ed25519），登记进 keyring
  4. 做出 genesis 提交

不会做：不问就装 Gitea、不碰网络、不删除任何已有文件。

用法：
    python3 bootstrap.py --handle JiaWeathour --realname 贾苇索
    python3 bootstrap.py --handle JiaWeathour --realname 贾苇索 --no-git
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "lib"))

import pool  # noqa: E402

KEYDIR = Path(os.environ.get("POOL_KEYDIR", Path.home() / ".pool" / "keys"))
HANDLE_FILE = Path(os.environ.get("POOL_HANDLE_FILE", Path.home() / ".pool" / "handle"))

DATA_FILES = ("requests", "deliverables", "credits", "keyring")


def step(msg: str) -> None:
    print(f"\n\033[1m== {msg}\033[0m")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--handle", required=True, help="你的 handle，例如 JiaWeathour")
    ap.add_argument("--realname", default=None, help="真实姓名，例如 贾苇索")
    ap.add_argument("--no-git", action="store_true", help="只生成文件，不初始化 git")
    args = ap.parse_args()

    # ---------------------------------------------------------- 1. data/
    step("1/4 建 data/ 四份 JSONL")
    (ROOT / "data").mkdir(exist_ok=True)
    for name in DATA_FILES:
        path = ROOT / "data" / f"{name}.jsonl"
        if path.exists():
            print(f"  已存在，跳过：{path.name}")
        else:
            path.touch()
            print(f"  新建：{path.name}")

    # ---------------------------------------------------------- 2. keyring 的 genesis 记录
    step("2/4 生成第一把密钥并登记")
    KEYDIR.mkdir(parents=True, exist_ok=True)
    keyfile = KEYDIR / f"{args.handle}.key"
    keyring = ROOT / "data" / "keyring.jsonl"
    existing = pool.read_jsonl(keyring)

    if existing:
        print(f"  keyring 里已有 {len(existing)} 条记录，跳过密钥生成")
        key = pool.load_private(keyfile.read_bytes())
    else:
        if keyfile.exists():
            print(f"  ⚠ 私钥已存在，复用它：{keyfile}")
            key = pool.load_private(keyfile.read_bytes())
        else:
            key = pool.generate_key()
            keyfile.write_bytes(pool.private_to_bytes(key))
            keyfile.chmod(0o600)
            print(f"  私钥已生成：{keyfile}（权限 600）")

        pub = pool.public_of(key)
        body = {
            "id": "k-0001",
            "op": "join",
            "pubkey": pub,
            "by": pub,
            "handle": args.handle,
            "realname": args.realname,
            "status": "active",
        }
        rec = pool.build_record(keyring, body, key)
        pool.append_record(keyring, rec)
        print(f"  公钥：{pub}")
        print(f"  已写入 keyring.jsonl 作为 k-0001")

    HANDLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    HANDLE_FILE.write_text(args.handle, encoding="utf-8")
    print(f"  记住 handle = {args.handle}（写在 {HANDLE_FILE}）")

    # ---------------------------------------------------------- 3. git
    if not args.no_git:
        step("3/4 初始化 git 仓库")
        if (ROOT / ".git").is_dir():
            print("  .git 已存在，跳过 init")
        else:
            # ★ 不用 --object-format=sha256。原因见 docs/22-git哈希方案.md：
            #   GitHub / Gitea / GitLab 全都不支持 SHA-256 仓库，用了就
            #   无法推送到任何 forge，也就没有 issue / PR / Pages。
            #   而我们的信任链在记录层（sha256 内容哈希 + Ed25519），
            #   与 git 的对象格式无关。
            r = run(["git", "init", "-b", "main"])
            if r.returncode != 0:
                print(f"  ✗ git init 失败：{r.stderr.strip()}")
                return 2
            print("  已初始化（分支 main）")

        # 打开 git 自带的碰撞检测：传输和取回时都校验对象完好
        for k, v in (
            ("transfer.fsckObjects", "true"),
            ("fetch.fsckObjects", "true"),
            ("receive.fsckObjects", "true"),
        ):
            run(["git", "config", k, v])
        print("  已打开 fsckObjects（碰撞检测）")

        # .gitignore
        gi = ROOT / ".gitignore"
        if not gi.exists():
            gi.write_text(
                "# 私钥永远不进仓库\n*.key\n.pool/\n"
                "# 构建产物（由 Cloudflare Pages 在构建时生成，不提交）\nsite/\n"
                "# Python\n__pycache__/\n*.pyc\n.venv/\n",
                encoding="utf-8",
            )
            print("  已写 .gitignore")

        step("4/4 genesis 提交")
        run(["git", "add", "-A"])
        r = run(
            [
                "git",
                "-c",
                f"user.name={args.realname or args.handle}",
                "-c",
                "user.email=pool@localhost",
                "commit",
                "-m",
                "genesis: 池子 v1 初始化\n\n"
                "四份 JSONL + policies 十一条 + 校验器 + 签名工具。\n"
                "本提交之后所有记录都链在它后面。",
            ]
        )
        if r.returncode != 0:
            if "nothing to commit" in r.stdout + r.stderr:
                print("  没有新东西要提交")
            else:
                print(f"  ✗ 提交失败：{r.stderr.strip() or r.stdout.strip()}")
                return 2
        else:
            print("  已提交")

        h = run(["git", "rev-list", "--max-parents=0", "HEAD"])
        genesis = h.stdout.strip()
        if genesis:
            print(f"\n  genesis commit = {genesis}")
            print("    （仅供查阅，不是信任锚点 —— 信任锚点是 data/ 里第一条记录的 hash）")
        # 顺手报一下真正的锚：keyring 第一条的 hash
        try:
            first = pool.read_jsonl(ROOT / "data" / "keyring.jsonl")[0]
            print(f"  ★ 真正的信任锚 = {first['hash']}")
        except (IndexError, OSError):
            pass
    else:
        step("3/4 跳过 git（--no-git）")

    # ---------------------------------------------------------- 完成
    step("完成")
    print("  下一步：python3 tools/validate")
    print("          然后 python3 tools/sign req --kind question ... 开第一张真单子")
    return 0


if __name__ == "__main__":
    sys.exit(main())
