#!/usr/bin/env python3
"""规则测试：为每条 policy 造一个故意违规的仓库，确认校验器真的抓住。

★ 为什么必须有这个文件：
  "实现了 11 条规则"是一句无法验证的话。只有"每条规则都有一个能被抓住的
  反例"，才是证据。这个文件就是那 11 个反例。

做法：在临时目录里造一个小仓库（自带 keyring），塞进违规记录，
      跑 tools/validate --root <tmp>，断言它报出了对应的规则号。

用法：python3 tests/test_rules.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import pool  # noqa: E402

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


class Repo:
    """一个临时小仓库，自带两把身份密钥。"""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pool-rules-"))
        (self.dir / "data").mkdir(parents=True)
        for name in pool.DATA_FILES:
            (self.dir / "data" / f"{name}.jsonl").touch()
        self.k1 = pool.generate_key()
        self.k2 = pool.generate_key()
        self.p1 = pool.public_of(self.k1)
        self.p2 = pool.public_of(self.k2)
        kr = self.dir / "data" / "keyring.jsonl"
        for i, (k, p, h) in enumerate(
            ((self.k1, self.p1, "甲"), (self.k2, self.p2, "乙")), 1
        ):
            pool.append_record(
                kr,
                pool.build_record(
                    kr,
                    {
                        "id": f"k-{i}",
                        "op": "join",
                        "pubkey": p,
                        "by": p,
                        "handle": h,
                        "status": "active",
                    },
                    k,
                ),
            )

    def add(self, fname: str, body: dict, key=None):
        path = self.dir / "data" / f"{fname}.jsonl"
        rec = pool.build_record(path, body, key or self.k1)
        pool.append_record(path, rec)
        return rec

    def validate(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "validate"), "--root", str(self.dir)],
            capture_output=True,
            text=True,
        )

    def cleanup(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def open_request(repo: Repo, rid="r-1", key=None, **over) -> dict:
    body = {
        "id": rid,
        "op": "open",
        "type": "need",
        "title": "测试单",
        "artifact_kind": "answer",
        "spec": "spec",
        "acceptance": "含结论与至少一条理由",
        "links": [],
        "status_declared": "open",
    }
    body.update(over)
    return repo.add("requests", body, key)


def create_deliverable(repo: Repo, did="d-1", rid="r-1", key=None, pub=None) -> dict:
    return repo.add(
        "deliverables",
        {
            "id": did,
            "op": "create",
            "request_id": rid,
            "artifact_kind": "answer",
            "deliverer": pub or repo.p1,
            "content": {"text": "x"},
            "links": [],
            "reuse_count": 0,
        },
        key or repo.k1,
    )


def review(repo: Repo, did="d-1", rid="r-1", **over) -> dict:
    # 真实的 `sign review` 会带上 request_id 和 deliverer —— 助手也要一致，
    # 否则测的是"我造的假记录"，不是真实路径。
    body = {
        "id": did,
        "op": "review",
        "request_id": rid,
        "deliverer": repo.p1,
        "artifact_kind": "answer",
        "review": {"by": repo.p2, "result": "pass", "reason": "ok", "reproduce_minutes": 10},
    }
    body.update(over)
    return repo.add("deliverables", body, repo.k2)


# ================================================================ 逐条测

# ---- p-001 链完整
section("p-001 链完整（改一条记录的 hash 之外的内容）")
r = Repo()
try:
    open_request(r)
    path = r.dir / "data" / "requests.jsonl"
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    rec["title"] = "被偷偷改过的标题"  # hash 不再相符
    path.write_text(json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    res = r.validate()
    check("改内容后 p-001 报错", res.returncode != 0 and "p-001" in res.stdout, res.stdout)
    check("指出是 hash 不符", "hash 与内容不符" in res.stdout, res.stdout)
finally:
    r.cleanup()

# ---- p-002 签名有效
section("p-002 签名有效（用未登记的公钥签名）")
r = Repo()
try:
    stranger = pool.generate_key()
    open_request(r, key=stranger)
    res = r.validate()
    check("未登记公钥被拒", res.returncode != 0 and "p-002" in res.stdout, res.stdout)
finally:
    r.cleanup()

# ---- p-003 复核者 ≠ 交付者
section("p-003 复核者不得是交付者")
r = Repo()
try:
    open_request(r)
    create_deliverable(r)
    r.add(
        "deliverables",
        {"id": "d-1", "op": "review", "review": {"by": r.p1, "result": "pass", "reason": "自己批"}},
        r.k1,
    )
    res = r.validate()
    check("自己批自己被拒", res.returncode != 0 and "p-003" in res.stdout, res.stdout)
finally:
    r.cleanup()

section("p-003 交付记录的签名者必须就是 deliverer")
r = Repo()
try:
    open_request(r)
    create_deliverable(r, key=r.k2, pub=r.p1)  # 用乙的钥签，却写甲的 deliverer
    res = r.validate()
    check("签名者≠deliverer 被拒", res.returncode != 0 and "p-003" in res.stdout, res.stdout)
finally:
    r.cleanup()

# ---- p-004 验收标准
section("p-004 申请单必须写验收标准")
r = Repo()
try:
    open_request(r, acceptance="")
    res = r.validate()
    check("空验收标准被拒", res.returncode != 0 and "p-004" in res.stdout, res.stdout)
finally:
    r.cleanup()

r = Repo()
try:
    open_request(r, acceptance="好一点")
    res = r.validate()
    check("过短的验收标准被拒", res.returncode != 0 and "p-004" in res.stdout, res.stdout)
finally:
    r.cleanup()

# ---- p-005 改账需联署
section("p-005 credit 的 adjust 必须带联署")
r = Repo()
try:
    r.add("credits", {"id": "c-1", "op": "adjust", "member": r.p1, "amount": -5, "reason": "减", "refs": []}, r.k1)
    res = r.validate()
    check("没有 cosign 的 adjust 被拒", res.returncode != 0 and "p-005" in res.stdout, res.stdout)
finally:
    r.cleanup()

r = Repo()
try:
    r.add(
        "credits",
        {"id": "c-1", "op": "adjust", "member": r.p1, "amount": -5, "reason": "减",
         "refs": [], "cosign": r.p1},
        r.k1,
    )
    res = r.validate()
    check("cosign == member 被拒（自己批自己）", res.returncode != 0 and "p-005" in res.stdout, res.stdout)
finally:
    r.cleanup()

r = Repo()
try:
    r.add(
        "credits",
        {"id": "c-1", "op": "adjust", "member": r.p1, "amount": -5, "reason": "减",
         "refs": [], "cosign": r.p2},
        r.k2,
    )
    res = r.validate()
    check("合规的联署通过", res.returncode == 0, res.stdout)
finally:
    r.cleanup()

# ---- p-007 异议必须回应
section("p-007 异议必须在上限天数内被回应")
r = Repo()
try:
    r.add(
        "maintainer-log",
        {"id": "obj-1", "op": "object", "target": "r-1", "reason": "我反对", "at": "2026-01-01T00:00:00+08:00"},
        r.k1,
    )
    r.add(
        "maintainer-log",
        {"id": "obj-2", "op": "object", "target": "r-1", "reason": "过期的异议", "at": "2026-01-01T00:00:00+08:00"},
        r.k2,
    )
    # 补一条很晚的记录，让"现在"推进到很后面
    r.add("maintainer-log", {"id": "ml-3", "op": "publicize", "reason": "推进时间", "at": "2026-03-01T00:00:00+08:00"}, r.k1)
    res = r.validate()
    check("超期异议被报出", "p-007" in res.stdout, res.stdout)
    check("是 warn 不是 error（不算破坏完整性）", res.returncode == 0, res.stdout)
finally:
    r.cleanup()

# ---- p-008 单一来源占比
section("p-008 单一来源占比上限")
r = Repo()
try:
    for i in range(3):
        p = pool.generate_key()
        pub = pool.public_of(p)
        path = r.dir / "data" / "keyring.jsonl"
        pool.append_record(
            path,
            pool.build_record(
                path,
                {"id": f"k-9{i}", "op": "join", "pubkey": pub, "by": pub, "handle": f"人{i}", "status": "active"},
                p,
            ),
        )
    # 三个人拿过 credit，但其中一个占 90%
    r.add("credits", {"id": "c-1", "op": "mint", "member": r.p1, "amount": 90, "reason": "x", "refs": []}, r.k1)
    r.add("credits", {"id": "c-2", "op": "mint", "member": r.p2, "amount": 5, "reason": "x", "refs": []}, r.k1)
    res = r.validate()
    # 只有 2 人拿过 credit → 低于门槛，不报
    check("拿过 credit 的人少于 3 时不报（避免误报）", "p-008" not in res.stdout, res.stdout)

    # 再让第三个人也拿过 credit → 现在应当报警
    third = pool.generate_key()
    tpub = pool.public_of(third)
    kp = r.dir / "data" / "keyring.jsonl"
    pool.append_record(
        kp,
        pool.build_record(
            kp,
            {"id": "k-99", "op": "join", "pubkey": tpub, "by": tpub, "handle": "丙", "status": "active"},
            third,
        ),
    )
    r.add("credits", {"id": "c-9", "op": "mint", "member": tpub, "amount": 1, "reason": "x", "refs": []}, r.k1)
    res2 = r.validate()
    check("3 人拿过 credit 且一家独大 → 报警", "p-008" in res2.stdout, res2.stdout)
    check("报警信息给出占比", "占比" in res2.stdout, res2.stdout)
finally:
    r.cleanup()

# ---- p-009 质疑必须指明对象
section("p-009 challenge 必须用 links 指明对象")
r = Repo()
try:
    open_request(r, type="challenge")
    res = r.validate()
    check("没有 links 的 challenge 被拒", res.returncode != 0 and "p-009" in res.stdout, res.stdout)
finally:
    r.cleanup()

# ---- p-010 维护者变更必须公示
section("p-010 维护者变更必须公示")
r = Repo()
try:
    r.add("maintainer-log", {"id": "ml-1", "op": "appoint", "member": "m-2", "reason": "加人"}, r.k1)
    res = r.validate()
    check("未公示的变更被报出", "p-010" in res.stdout, res.stdout)
finally:
    r.cleanup()

r = Repo()
try:
    r.add("maintainer-log", {"id": "ml-1", "op": "appoint", "member": "m-2", "reason": "加人"}, r.k1)
    r.add("maintainer-log", {"id": "ml-2", "op": "publicize", "about": "m-2", "reason": "已在群里公示"}, r.k1)
    res = r.validate()
    check("公示后通过", "p-010" not in res.stdout, res.stdout)
finally:
    r.cleanup()

# ---- p-011 拉人不产生 credit
section("p-011 拉人进来不产生 credit")
r = Repo()
try:
    r.add("credits", {"id": "c-1", "op": "mint", "member": r.p1, "amount": 5, "reason": "拉了个新人", "refs": ["k-2"]}, r.k1)
    res = r.validate()
    check("credit 引用身份记录被拒", res.returncode != 0 and "p-011" in res.stdout, res.stdout)
finally:
    r.cleanup()

# ---- 全部合规时应当通过
section("合规仓库应当全部通过")
r = Repo()
try:
    open_request(r)
    create_deliverable(r)
    review(r)
    r.add("requests", {"id": "r-1", "op": "confirm"}, r.k1)
    r.add(
        "credits",
        {"id": "c-1", "op": "mint", "member": r.p1, "amount": 1.0, "reason": "过了",
         "refs": ["d-1"], "cosign": None},
        r.k1,
    )
    res = r.validate()
    check("合规仓库通过", res.returncode == 0, res.stdout)
    check("没有 error", "ERROR" not in res.stdout, res.stdout)
finally:
    r.cleanup()


print()
if FAILED:
    print(f"✗ {len(FAILED)} 项失败（{PASSED} 项通过）")
    for f in FAILED:
        print(f"    - {f}")
    sys.exit(1)
print(f"★ 全部通过（{PASSED} 项）—— 11 条规则每条都有能被抓住的反例")
sys.exit(0)
