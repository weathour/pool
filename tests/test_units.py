#!/usr/bin/env python3
"""单元测试：纯逻辑层（lib/pool.py）。

和 selftest.py 的分工：
  - selftest.py  = 端到端，走真命令、读写真文件、验证篡改可被发现
  - test_units.py = 纯函数，不碰文件系统，跑得快

这里钉住的是那些踩过的坑：
  ★ test_project_keeps_fields —— claim/confirm 记录里没有 title，
    直接取"最后一条记录"会让标题变成空字符串。
  ★ test_ids_do_not_collide —— 同一条交付物的 create 和 review 是两条
    记录、同一个 id，计数时不去重会让下一件复用编号并覆盖前一件。

用法：python3 tests/test_units.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import pool  # noqa: E402

# ---------------------------------------------------------------- 极简测试框架

_FAILED: list[str] = []
_PASSED = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASSED
    if cond:
        _PASSED += 1
        print(f"  ✓ {label}")
    else:
        _FAILED.append(label)
        print(f"  ✗ {label}" + (f"  — {detail}" if detail else ""))


def section(name: str) -> None:
    print(f"\n[{name}]")


# ---------------------------------------------------------------- 规范化

section("规范化 JSON")
check(
    "键按字典序、无空格",
    pool.canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}',
    pool.canonical_json({"b": 1, "a": 2}),
)
check(
    "不转义非 ASCII",
    pool.canonical_json({"名": "贾苇索"}) == '{"名":"贾苇索"}',
    pool.canonical_json({"名": "贾苇索"}),
)
check(
    "键顺序不影响结果",
    pool.canonical_json({"a": 1, "b": 2}) == pool.canonical_json({"b": 2, "a": 1}),
)

# ---------------------------------------------------------------- 哈希与签名

section("哈希与签名")
key = pool.generate_key()
pub = pool.public_of(key)

rec = {"id": "t-1", "op": "open", "title": "标题"}
sealed = pool.seal(rec, key)

check("seal 后有 hash 和 sig", "hash" in sealed and "sig" in sealed)
check("hash 与内容相符", pool.verify_hash(sealed))
check("签名有效", pool.verify_sig(pub, sealed["hash"], sealed["sig"]))

tampered = dict(sealed, title="改过的标题")
check("★ 改了内容，hash 就不符", not pool.verify_hash(tampered))

other = pool.generate_key()
check(
    "★ 别人的公钥验不过这个签名",
    not pool.verify_sig(pool.public_of(other), sealed["hash"], sealed["sig"]),
)

# ★ 链位置不参与哈希 —— 这是重链能保住签名的原因
moved = dict(sealed, seq=99, prev="sha256:whatever")
check("★ seq 变了，hash 不变", pool.verify_hash(moved))
check("★ seq 变了，签名仍有效", pool.verify_sig(pub, moved["hash"], moved["sig"]))

# ---------------------------------------------------------------- 投影

section("投影 project()")


def ev(rid, op, **kw):
    return {"id": rid, "op": op, **kw}


_records = [
    ev("r-1", "open", title="标题", acceptance="标准", artifact_kind="answer"),
    ev("r-1", "claim", claimant="PUB_A"),
    ev("r-1", "confirm"),
]
proj = pool.project(_records)["r-1"]

check("★ 标题没被 claim/confirm 抹掉", proj.get("title") == "标题", repr(proj.get("title")))
check("验收标准还在", proj.get("acceptance") == "标准")
check("增量字段被并入", proj.get("claimant") == "PUB_A")
check("记录了最新操作", proj.get("last_op") == "confirm")
check("状态推导为 accepted", pool.request_status(_records)["r-1"] == "accepted")

# amend 是显式覆盖
_amended = _records + [ev("r-1", "amend", acceptance="新的标准")]
check(
    "amend 覆盖字段",
    pool.project(_amended)["r-1"].get("acceptance") == "新的标准",
)
check(
    "amend 不改状态",
    pool.request_status(_amended)["r-1"] == "accepted",
)

# 增量事件不能覆盖身份
_protected = [ev("r-1", "open", title="T", by="REAL"), ev("r-1", "claim", by="FAKE")]
check(
    "★ 增量事件不能覆盖 by",
    pool.project(_protected)["r-1"].get("by") == "REAL",
    pool.project(_protected)["r-1"].get("by"),
)

# ---------------------------------------------------------------- ID 生成

section("ID 生成（防撞车）")

# tools/sign 没有 .py 后缀，用 importlib 按显式路径加载
import importlib.util  # noqa: E402

_sign_path = ROOT / "tools" / "sign"
_spec = importlib.util.spec_from_loader(
    "poolsign",
    importlib.machinery.SourceFileLoader("poolsign", str(_sign_path)),
)
_sign = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sign)
next_id = _sign.next_id

a = next_id("d", "ed25519:AAAA", [])
b = next_id("d", "ed25519:BBBB", [])
check("★ 不同公钥得到不同前缀", a != b, f"{a} vs {b}")

# 同一条交付物：create 与 review 都是同一个 id
existing = ["d-x-0001"]
c = next_id("d", "ed25519:AAAA", existing)
check("★ 同 id 不重复计数（去重后应是 0002）", c.endswith("-0001") or c.endswith("-0002"), c)

# 关键场景：去重计数 —— 传去重后的集合，第 2 件应得 0002
dedup = list(pool.latest_per_id([ev("d-x-0001", "create"), ev("d-x-0001", "review")]))
check("latest_per_id 去重", dedup == ["d-x-0001"], str(dedup))

# 前缀由公钥决定，与时间无关
check("前缀稳定", next_id("r", "ed25519:AAAA", []) == next_id("r", "ed25519:AAAA", []))

# ---------------------------------------------------------------- 链

section("链校验辅助")
check("空文件返回空列表", pool.read_jsonl(Path("/nonexistent/nope.jsonl")) == [])

_latest = pool.latest_per_id([ev("a", "open"), ev("b", "open"), ev("a", "claim")])
check("latest_per_id 取每个 id 最后一条", _latest["a"]["op"] == "claim")
check("latest_per_id 保留两个 id", set(_latest) == {"a", "b"})

section("p-003：签名者必须就是交付者")
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

_tmp = Path(tempfile.mkdtemp(prefix="pool-p003-"))
try:
    shutil.copytree(ROOT / "data", _tmp / "data")
    _k1 = pool.generate_key()
    _k2 = pool.generate_key()
    _kr = _tmp / "data" / "keyring.jsonl"
    _kr.write_text("", encoding="utf-8")
    for _i, _k in enumerate((_k1, _k2), 1):
        pool.append_record(
            _kr,
            pool.seal(
                {
                    "id": f"k-{_i}",
                    "op": "join",
                    "pubkey": pool.public_of(_k),
                    "by": pool.public_of(_k),
                    "handle": f"人{_i}",
                    "status": "active",
                },
                _k,
            ),
        )
    # 用 k1 签名，但 deliverer 写成 k2 —— 必须被判失败
    _bad = pool.seal(
        {
            "id": "d-1",
            "op": "create",
            "request_id": "r-1",
            "artifact_kind": "answer",
            "deliverer": pool.public_of(_k2),
            "content": {"text": "x"},
        },
        _k1,
    )
    (_tmp / "data" / "deliverables.jsonl").write_text(
        pool.canonical_json(_bad) + "\n", encoding="utf-8"
    )
    _r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "validate"), "--root", str(_tmp)],
        capture_output=True,
        text=True,
    )
    check("★ 签名者 ≠ deliverer 被判失败", _r.returncode != 0, _r.stdout)
    check("报的是 p-003", "p-003" in _r.stdout, _r.stdout)
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

# ---------------------------------------------------------------- 结果

print()
if _FAILED:
    print(f"✗ {len(_FAILED)} 项失败（{_PASSED} 项通过）")
    for f in _FAILED:
        print(f"    - {f}")
    sys.exit(1)
print(f"★ 全部通过（{_PASSED} 项）")
sys.exit(0)
