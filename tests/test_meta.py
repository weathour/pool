#!/usr/bin/env python3
"""元一致性检查的测试 —— 确认它会真的抓问题，而不是永远说"一致"。

做法：把仓库拷到临时目录，故意制造不一致，跑 tools/verify-meta，
断言它报出了对应的项。

★ 为什么需要这个：一个永远返回 0 的检查器看起来和"全部一致"一模一样。
  只有"制造问题 → 它报错"才能证明它在工作。

用法：python3 tests/test_meta.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FAILED: list[str] = []
PASSED = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASSED
    if cond:
        PASSED += 1
        print(f"  ✓ {label}")
    else:
        FAILED.append(label)
        print(f"  ✗ {label}" + (f"\n      {detail}" if detail else ""))


def section(name: str) -> None:
    print(f"\n[{name}]")


def make_repo() -> Path:
    """拷一份精简的仓库（只带 verify-meta 需要的文件）。"""
    tmp = Path(tempfile.mkdtemp(prefix="pool-meta-"))
    keep = [
        "tools",
        "data",
        "docs",
        "lib",
        "tests",
        "policies.jsonl",
        "README.md",
        "CONTRIBUTING.md",
        "CONTRIBUTORS.md",
        "别人怎么用.md",
        "MAINTAINERS.md",
        "CODEOWNERS",
        ".allowed_signers",
        ".gitea",
        ".github",
    ]
    tmp.mkdir(parents=True, exist_ok=True)
    for name in keep:
        src = ROOT / name
        if not src.exists():
            continue
        dst = tmp / name
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, dst)
    return tmp


def run_meta(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / "verify-meta")],
        capture_output=True,
        text=True,
        cwd=repo,
    )


# ---------------------------------------------------------------- 基线

section("基线：原样的仓库应当通过")
repo = make_repo()
try:
    res = run_meta(repo)
    check("干净的仓库通过", res.returncode == 0, res.stdout + res.stderr)
    check("输出说全部一致", "全部一致" in res.stdout, res.stdout)
finally:
    shutil.rmtree(repo, ignore_errors=True)

# ---------------------------------------------------------------- CODEOWNERS

section("CODEOWNERS 里出现 MAINTAINERS.md 之外的人")
repo = make_repo()
try:
    co = repo / "CODEOWNERS"
    co.write_text(co.read_text(encoding="utf-8") + "\n/some/path @outsider\n", encoding="utf-8")
    res = run_meta(repo)
    check("被抓住", res.returncode != 0, res.stdout)
    check("指出是谁", "@outsider" in res.stdout, res.stdout)
finally:
    shutil.rmtree(repo, ignore_errors=True)

section("MAINTAINERS.md 引用不存在的 keyring id")
repo = make_repo()
try:
    mt = repo / "MAINTAINERS.md"
    mt.write_text(mt.read_text(encoding="utf-8") + "\n见 `k-deadbeef-9999`\n", encoding="utf-8")
    res = run_meta(repo)
    check("被抓住", res.returncode != 0, res.stdout)
    check("指出是哪个 id", "k-deadbeef-9999" in res.stdout, res.stdout)
finally:
    shutil.rmtree(repo, ignore_errors=True)

# ---------------------------------------------------------------- 文档断链

section("README 引用不存在的路径")
repo = make_repo()
try:
    r = repo / "README.md"
    r.write_text(r.read_text(encoding="utf-8") + "\n\n见 `docs/不存在的文档.md`\n", encoding="utf-8")
    res = run_meta(repo)
    check("被抓住", res.returncode != 0, res.stdout)
    check("指出是哪个路径", "docs/不存在的文档.md" in res.stdout, res.stdout)
finally:
    shutil.rmtree(repo, ignore_errors=True)

# ---------------------------------------------------------------- 工具无文档

section("新增一个工具但没写进文档")
repo = make_repo()
try:
    tool = repo / "tools" / "brand-new-tool"
    tool.write_text("#!/usr/bin/env python3\nprint('hi')\n", encoding="utf-8")
    tool.chmod(0o755)
    res = run_meta(repo)
    check("被警告（不是 error）", res.returncode == 0 and "brand-new-tool" in res.stdout, res.stdout)
    check("级别是 WARN", "WARN" in res.stdout, res.stdout)
finally:
    shutil.rmtree(repo, ignore_errors=True)

# ---------------------------------------------------------------- 规则未实现

section("policies.jsonl 声明了但校验器没实现的规则")
repo = make_repo()
try:
    pol = repo / "policies.jsonl"
    pol.write_text(
        pol.read_text(encoding="utf-8")
        + '{"id":"p-099","name":"假的规则","statement":"x","expr":"x",'
        '"applies_to":"all","severity":"error","since":"v1"}\n',
        encoding="utf-8",
    )
    res = run_meta(repo)
    check("被抓住", res.returncode != 0, res.stdout)
    check("指出是哪条", "p-099" in res.stdout, res.stdout)
finally:
    shutil.rmtree(repo, ignore_errors=True)

# ---------------------------------------------------------------- DATA_FILES

section("DATA_FILES 里的文件缺失")
repo = make_repo()
try:
    (repo / "data" / "credits.jsonl").unlink()
    res = run_meta(repo)
    check("被抓住", res.returncode != 0, res.stdout)
    check("指出是哪个文件", "credits.jsonl" in res.stdout, res.stdout)
finally:
    shutil.rmtree(repo, ignore_errors=True)

# ---------------------------------------------------------------- docs 索引

section("docs 里有文件但索引没收录")
repo = make_repo()
try:
    (repo / "docs" / "99-新文档.md").write_text("# 新文档\n", encoding="utf-8")
    res = run_meta(repo)
    check("被警告", "99-新文档.md" in res.stdout, res.stdout)
finally:
    shutil.rmtree(repo, ignore_errors=True)

# ---------------------------------------------------------------- 签名名单

section("缺 .allowed_signers")
repo = make_repo()
try:
    (repo / ".allowed_signers").unlink()
    res = run_meta(repo)
    check("被警告（别人无法验证 tag）", "allowed_signers" in res.stdout, res.stdout)
finally:
    shutil.rmtree(repo, ignore_errors=True)

# ---------------------------------------------------------------- JSON 输出

section("--json 输出")
repo = make_repo()
try:
    (repo / "data" / "credits.jsonl").unlink()
    res = subprocess.run(
        [sys.executable, str(repo / "tools" / "verify-meta"), "--json"],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    import json

    try:
        items = json.loads(res.stdout)
        check("输出是合法 JSON", True)
        check("有内容", len(items) > 0, res.stdout)
        check("每项有 level/where/msg", all({"level", "where", "msg"} <= set(i) for i in items))
    except json.JSONDecodeError as exc:
        check("输出是合法 JSON", False, f"{exc}\n{res.stdout[:200]}")
finally:
    shutil.rmtree(repo, ignore_errors=True)


print()
if FAILED:
    print(f"✗ {len(FAILED)} 项失败（{PASSED} 项通过）")
    for f in FAILED:
        print(f"    - {f}")
    sys.exit(1)
print(f"★ 全部通过（{PASSED} 项）—— verify-meta 确实会抓问题，不是永远说一致")
sys.exit(0)
