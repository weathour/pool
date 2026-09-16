#!/usr/bin/env python3
"""端到端测试：走通一张真单子，并验证校验器能抓住篡改。

这不是单元测试，是"第 2 步验证点"的自动化版本：
  验证 验收标准可判定 + 复核者≠交付者 + 篡改可被发现

用法：python3 selftest.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(args: list[str], env: dict, expect_ok: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env,
    )
    if expect_ok and r.returncode != 0:
        print(f"✗ 命令失败：{' '.join(args)}")
        print(r.stdout)
        print(r.stderr)
        raise SystemExit(1)
    return r


def latest_id(name: str) -> str:
    """读某份文件的最后一条记录，取它的 id。"""
    path = ROOT / "data" / f"{name}.jsonl"
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return json.loads(lines[-1])["id"]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pool-selftest-"))
    env = {**os.environ, "POOL_KEYDIR": str(tmp / "keys"), "POOL_HANDLE_FILE": str(tmp / "handle")}

    backup = {}
    for name in ("requests", "deliverables", "credits", "keyring"):
        p = ROOT / "data" / f"{name}.jsonl"
        backup[name] = p.read_bytes()

    # 记录基线，这样仓库里已经有真数据时也能跑（按增量断言，不按绝对值）
    baseline = {
        name: len([l for l in blob.decode("utf-8").splitlines() if l.strip()])
        for name, blob in backup.items()
    }
    print(f"\n基线条数：{baseline}")

    ok = True

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        mark = "✓" if cond else "✗"
        print(f"  {mark} {label}" + (f"  — {detail}" if detail and not cond else ""))
        if not cond:
            ok = False

    try:
        print("\n[1] 第二个人加入（另一个身份）")
        run(["tools/sign", "init", "--handle", "TestReviewer"], env)
        run(["tools/sign", "init", "--handle", "TestDeliverer"], env)
        check("两个新身份已登记", True)

        print("\n[2] 开一张单（验收标准必须可判定）")
        run(
            [
                "tools/sign", "req",
                "--handle", "TestReviewer",
                "--kind", "question",
                "--title", "为什么制度化必然形式主义化",
                "--spec", "一个能站住的问题，含它为什么重要",
                "--acceptance", "包含问题陈述与 why_it_matters 两栏，且 why 栏不少于一句话",
            ],
            env,
        )
        reqs = [
            l
            for l in (ROOT / "data" / "requests.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if l.strip()
        ]
        check("单已追加（比基线多 1 条）", len(reqs) == baseline["requests"] + 1,
              f"基线 {baseline['requests']}，现在 {len(reqs)}")
        rid = latest_id("requests")
        check("单号由公钥派生（不会与别人撞车）", rid.startswith("r-") and rid.count("-") == 2, rid)
        print(f"     单号：{rid}")

        print("\n[3] 试着开一张没有验收标准的单（应当被拒）")
        r = run(
            [
                "tools/sign", "req",
                "--handle", "TestReviewer",
                "--kind", "answer",
                "--title", "空的验收标准",
                "--spec", "随便",
                "--acceptance", "   ",
            ],
            env,
            expect_ok=False,
        )
        check("p-004：空的 acceptance 被拒绝", r.returncode != 0)

        print("\n[4] 另一个人接单并交付")
        run(["tools/sign", "claim", "--handle", "TestDeliverer", rid], env)
        content = tmp / "content.json"
        content.write_text(
            json.dumps(
                {
                    "question": "为什么制度化必然形式主义化？",
                    "why_it_matters": "因为它决定了一个组织该在什么时候停止增加规则。",
                    "context": "row 288 现实的本体论化",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        run(
            [
                "tools/sign", "deliver",
                "--handle", "TestDeliverer",
                "--request", rid,
                "--kind", "question",
                "--content-file", str(content),
            ],
            env,
        )
        check("交付已追加", True)
        did = latest_id("deliverables")
        print(f"     交付物号：{did}")

        print("\n[5] 交付者试图复核自己（应当被拒 —— p-003）")
        r = run(
            [
                "tools/sign", "review",
                "--handle", "TestDeliverer",
                did,
                "--result", "pass",
                "--reason", "我自己觉得挺好",
            ],
            env,
            expect_ok=False,
        )
        check("p-003：自己批自己被拒绝", r.returncode != 0 and "p-003" in (r.stdout + r.stderr))

        print("\n[6] 另一个人复核（应当通过）")
        run(
            [
                "tools/sign", "review",
                "--handle", "TestReviewer",
                did,
                "--result", "pass",
                "--reason", "两栏都在，why 栏说清了它为什么重要",
                "--reproduce-minutes", "30",
            ],
            env,
        )
        check("复核已记录", True)

        print("\n[7] 开单人确认")
        run(["tools/sign", "confirm", "--handle", "TestReviewer", rid], env)
        check("确认已记录", True)

        print("\n[8] 校验器应当全部通过")
        r = run(["tools/validate"], env)
        check("validate 通过", r.returncode == 0)

        print("\n[9] ★ 篡改测试：偷偷改一条已落账的记录")
        dpath = ROOT / "data" / "deliverables.jsonl"
        original = dpath.read_text(encoding="utf-8")
        tampered = original.replace('"reuse_count":0', '"reuse_count":999')
        check("替换确实生效（否则测试无效）", tampered != original)
        dpath.write_text(tampered, encoding="utf-8")

        r = run(["tools/validate"], env, expect_ok=False)
        caught = r.returncode != 0
        check("★ 校验器抓住了篡改", caught, r.stdout)
        if "--quiet" in sys.argv:
            pass
        else:
            print("     校验器输出：")
            for line in r.stdout.strip().splitlines():
                print(f"       {line}")

        dpath.write_text(original, encoding="utf-8")

        print("\n[10] 篡改测试：删掉中间一条（链断裂）")
        keyring = ROOT / "data" / "keyring.jsonl"
        k_orig = keyring.read_text(encoding="utf-8")
        lines = k_orig.strip().splitlines()
        if len(lines) >= 3:
            keyring.write_text("\n".join([lines[0], *lines[2:]]) + "\n", encoding="utf-8")
            r = run(["tools/validate"], env, expect_ok=False)
            check("★ 校验器抓住了断裂", r.returncode != 0)
            keyring.write_text(k_orig, encoding="utf-8")
        else:
            print("  (keyring 行数不足，跳过)")

        print("\n[11] 恢复后应当再次通过")
        r = run(["tools/validate"], env)
        check("validate 通过", r.returncode == 0)

        print("\n[12] ★ 重链测试：fixlinks 能否在不破坏签名的前提下修好链")
        rpath = ROOT / "data" / "requests.jsonl"
        rbackup = rpath.read_text(encoding="utf-8")
        import json as _json

        lines = [_json.loads(l) for l in rbackup.splitlines() if l.strip()]
        if len(lines) >= 2:
            lines[1]["prev"] = None
            rpath.write_text(
                "".join(
                    _json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                    + "\n"
                    for x in lines
                ),
                encoding="utf-8",
            )
            r = run(["tools/validate"], env, expect_ok=False)
            check("重链前：校验器抓到 prev 错位", r.returncode != 0)

            run(["tools/fixlinks", "--write"], env)

            # 关键：重链后 hash 与签名都必须仍然有效
            import sys as _sys

            _sys.path.insert(0, str(ROOT / "lib"))
            import pool as _pool

            recs = _pool.read_jsonl(rpath)
            all_ok = all(
                _pool.verify_hash(x) and _pool.verify_sig(x["by"], x["hash"], x["sig"])
                for x in recs
            )
            check("★ 重链后 hash 与签名仍然有效", all_ok)

            r = run(["tools/validate"], env)
            check("重链后 validate 通过", r.returncode == 0)

            rpath.write_text(rbackup, encoding="utf-8")
        else:
            print("  (记录不足，跳过)")

    finally:
        for name, blob in backup.items():
            (ROOT / "data" / f"{name}.jsonl").write_bytes(blob)
        shutil.rmtree(tmp, ignore_errors=True)

    # ★ 关键：确认测试没有污染真仓库。这条断言能防止"测试跑完留下垃圾记录"。
    restored = True
    for name, blob in backup.items():
        if (ROOT / "data" / f"{name}.jsonl").read_bytes() != blob:
            restored = False
            print(f"  ✗ data/{name}.jsonl 没有被正确恢复！")
    check("★ 测试没有污染仓库（数据已完全恢复）", restored)

    print()
    if ok:
        print("\033[1m★ 全部通过 —— 第 2 步验证点成立：验收标准可判定，篡改可被发现。\033[0m")
        return 0
    print("\033[1m✗ 有失败项\033[0m")
    return 1


if __name__ == "__main__":
    sys.exit(main())
