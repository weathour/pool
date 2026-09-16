"""池子核心库 —— 纯逻辑：规范化 JSON、哈希、签名、链校验。

设计原则（见 设计总览与体检.md）：
  - 记录内签名是唯一身份来源（公钥即身份）
  - hash = sha256(canonical_json(记录去掉 hash 和 sig))
  - prev 串成链，让数据脱离 git 也能自证
  - 本模块不做任何 IO、不读当前时间、不用随机数（除了密钥生成）
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

# ---------------------------------------------------------------- 常量

SCHEMA_VERSION = "v1"

#: 数据文件。前四份是核心，maintainer-log 记录维护者的动作
#: （异议、回应、权限变更）—— p-006/p-007/p-010 需要它。
DATA_FILES = ("requests", "deliverables", "credits", "keyring", "maintainer-log")

#: 不参与哈希的字段分两类：
#:
#:   CHAIN_FIELDS —— 链位置信息。它们【故意】不参与哈希，因为
#:     多人同时追加后需要重链（见 tools/fixlinks），而重链必须
#:     保持签名有效。链位置变了，内容没变，签名就该继续成立。
#:
#:   SELF_FIELDS —— 哈希与签名自身，显然要排除。
CHAIN_FIELDS = ("seq", "prev")
SELF_FIELDS = ("hash", "sig")
HASH_EXCLUDED = CHAIN_FIELDS + SELF_FIELDS

_ED25519_PREFIX = "ed25519:"
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?([+-]\d{2}:\d{2}|Z)$"
)


# ---------------------------------------------------------------- 规范化与哈希


def canonical_json(obj) -> str:
    """规范化 JSON：键按字典序、无多余空格、UTF-8、不转义非 ASCII。

    这是全套东西的互验基础 —— 换一个字符，所有历史哈希都会变。
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_bytes(obj) -> bytes:
    return canonical_json(obj).encode("utf-8")


def content_of(record: dict) -> dict:
    """取一条记录里"属于作者本人的内容"——去掉链位置、哈希、签名。

    签名与哈希都只覆盖这一部分。这样做的理由：
    重链（改 seq / prev）不应该让签名失效，因为那只是位置变化。
    """
    return {k: v for k, v in record.items() if k not in HASH_EXCLUDED}


def compute_hash(record: dict) -> str:
    """算一条记录的 hash —— 只覆盖内容，不覆盖它在链上的位置。"""
    digest = hashlib.sha256(canonical_bytes(content_of(record))).hexdigest()
    return f"sha256:{digest}"


def verify_hash(record: dict) -> bool:
    stored = record.get("hash")
    if not isinstance(stored, str):
        return False
    return stored == compute_hash(record)


# ---------------------------------------------------------------- 密钥


def generate_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def private_to_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def private_from_bytes(raw: bytes) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_to_str(pub: Ed25519PublicKey) -> str:
    raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _ED25519_PREFIX + base64.b64encode(raw).decode("ascii")


def load_private(raw: bytes) -> Ed25519PrivateKey:
    return private_from_bytes(raw)


def public_of(key: Ed25519PrivateKey) -> str:
    return public_to_str(key.public_key())


def _parse_public(pubkey: str) -> Ed25519PublicKey:
    if not isinstance(pubkey, str) or not pubkey.startswith(_ED25519_PREFIX):
        raise ValueError(f"公钥格式不对（应以 {_ED25519_PREFIX} 开头）")
    raw = base64.b64decode(pubkey[len(_ED25519_PREFIX) :])
    return Ed25519PublicKey.from_public_bytes(raw)


def sign_hash(key: Ed25519PrivateKey, record_hash: str) -> str:
    sig = key.sign(record_hash.encode("utf-8"))
    return _ED25519_PREFIX + base64.b64encode(sig).decode("ascii")


def verify_sig(pubkey: str, record_hash: str, sig: str) -> bool:
    if not isinstance(sig, str) or not sig.startswith(_ED25519_PREFIX):
        return False
    try:
        pub = _parse_public(pubkey)
        raw = base64.b64decode(sig[len(_ED25519_PREFIX) :])
        pub.verify(raw, record_hash.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# ---------------------------------------------------------------- 记录封口


def seal(record: dict, key: Ed25519PrivateKey) -> dict:
    """给一条记录算 hash 并签名，返回封口后的新 dict。

    record 里不应含 hash / sig（会被覆盖）。
    """
    # ★ 只剥掉 hash 和 sig 自身；seq / prev 要【留在输出里】——
    #   它们不参与哈希（因为重链时不能失效），但必须被写进文件。
    sealed = {k: v for k, v in record.items() if k not in SELF_FIELDS}
    if "by" not in sealed:
        sealed["by"] = public_of(key)
    h = compute_hash(sealed)
    sealed["hash"] = h
    sealed["sig"] = sign_hash(key, h)
    return sealed


# ---------------------------------------------------------------- 读取


def read_jsonl(path: Path) -> list[dict]:
    """读一份 JSONL。空文件返回空列表。"""
    if not path.exists():
        return []
    out = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{lineno} 不是合法 JSON：{exc}") from exc
    return out


def write_jsonl(path: Path, records: list[dict]) -> None:
    """整份重写（只在 bootstrap / 工具内部用；正常追加走 append_record）。"""
    text = "".join(canonical_json(r) + "\n" for r in records)
    path.write_text(text, encoding="utf-8")


def append_record(path: Path, record: dict) -> None:
    """严格末尾追加一行。这是唯一的正常写入方式。"""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(canonical_json(record) + "\n")


def next_link(path: Path) -> tuple[int, str | None]:
    """算下一条记录该用的 (seq, prev)。

    这是"严格末尾追加"的实现基础 —— 也正因为严格追加，
    多人的追加在 git 里几乎不会冲突（各自在末尾加一行）。
    """
    records = read_jsonl(path)
    if not records:
        return 1, None
    return len(records) + 1, records[-1].get("hash")


def build_record(path: Path, body: dict, key) -> dict:
    """填好 seq / at / prev，然后算 hash 并签名。返回可直接追加的记录。"""
    seq, prev = next_link(path)
    record = {"seq": seq, "at": now_iso(), "prev": prev}
    record.update(body)
    return seal(record, key)


# ---------------------------------------------------------------- 时间


def now_iso() -> str:
    """带时区的 ISO 8601，秒级。"""
    from datetime import datetime

    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def is_iso(s: str) -> bool:
    return isinstance(s, str) and bool(_ISO_RE.match(s))


# ---------------------------------------------------------------- 当前状态推导


def latest_per_id(records: list[dict]) -> dict[str, dict]:
    """每个 id 取最后一条（只追加模型的"当前状态"）。"""
    out: dict[str, dict] = {}
    for rec in records:
        rid = rec.get("id")
        if isinstance(rid, str):
            out[rid] = rec
    return out


#: 带实体本身字段的操作。其余（claim / confirm / close…）只带增量。
_BASE_OPS = ("open", "create", "join", "amend")

#: 增量事件永远不该覆盖这些字段 —— 链位置、哈希、签名、身份。
_STRUCTURAL = frozenset(CHAIN_FIELDS) | frozenset(SELF_FIELDS) | {"op", "id", "by"}


def project(records: list[dict]) -> dict[str, dict]:
    """把只追加的事件流投影成"每个 id 的当前样子"。

    ★ 为什么需要这个：claim / confirm / close 这些记录只带增量
      （比如只有 claimant 和 op），它们里面【没有】title、spec、acceptance。
      直接取"最后一条记录"会让标题变成空字符串 —— 真踩过这个坑。

    规则：遇到带实体字段的记录（open / create / join）就重置为该记录；
    遇到 amend 就叠加；遇到增量事件只在字段缺失时补齐。
    """
    out: dict[str, dict] = {}
    for rec in records:
        rid = rec.get("id")
        if not isinstance(rid, str):
            continue
        op = rec.get("op")

        if op in _BASE_OPS and not (op == "amend" and rid in out):
            out[rid] = dict(rec)
            continue

        if rid not in out:
            continue

        if op == "amend":
            # amend 是显式覆盖：它的字段优先
            out[rid].update(rec)
        else:
            # 增量事件：只补空缺，绝不覆盖已有实体字段
            for k, v in rec.items():
                if k not in _STRUCTURAL:
                    out[rid].setdefault(k, v)

        # 保留"最新一次操作"的信息，但放在单独字段里，
        # 这样实体字段（title 等）和"最后发生了什么"两者都不丢。
        out[rid]["last_op"] = op
    return out


def deliverable_status(records: list[dict]) -> dict[str, str]:
    """从事件推导每件交付物的当前状态。"""
    ops: dict[str, set[str]] = {}
    for rec in records:
        did, op = rec.get("id"), rec.get("op")
        if isinstance(did, str) and isinstance(op, str):
            ops.setdefault(did, set()).add(op)

    out: dict[str, str] = {}
    for did, seen in ops.items():
        if "review" in seen:
            # 通过还是打回，要看那条 review 记录本身
            out[did] = "reviewed"
        elif "create" in seen:
            out[did] = "awaiting_review"
        else:
            out[did] = "unknown"
    return out


# ---------------------------------------------------------------- credit 定价
#
# 依据见 docs/03-credit基于什么.md：
#   定基     = 复核者估的复现时间 / 10   ← 定价权在复核者手里，不在交付者手里
#   效果加成 = × (1 + min(复用次数, 5) × 0.2)
#   门槛     = 复核通过（复核者 ≠ 交付者）+ 开单人确认
#
# ★ 为什么要"复核者估"而不是"交付者报"：自报必然虚报且无法验证。
#   复核者反正要读一遍，估这个是顺手的事，换个复核者还能交叉校对。

MINUTES_PER_CREDIT = 10
REUSE_BONUS_PER = 0.2
REUSE_BONUS_CAP = 5


def base_credit(reproduce_minutes) -> float:
    """定基：复现时间换算成 credit。"""
    if not isinstance(reproduce_minutes, (int, float)) or reproduce_minutes <= 0:
        return 0.0
    return round(float(reproduce_minutes) / MINUTES_PER_CREDIT, 4)


def reuse_multiplier(reuse_count) -> float:
    """效果加成。复用是唯一无法伪造的信号 —— 引用别人要先读懂它。"""
    if not isinstance(reuse_count, (int, float)) or reuse_count <= 0:
        return 1.0
    return 1.0 + min(int(reuse_count), REUSE_BONUS_CAP) * REUSE_BONUS_PER


def final_credit(reproduce_minutes, reuse_count=0) -> float:
    return round(base_credit(reproduce_minutes) * reuse_multiplier(reuse_count), 4)


QUESTION_ROYALTY_RATE = 0.2
QUESTION_ROYALTY_CAP = 200.0


def royalty_for(generated: float) -> float:
    """一个问题的版税 = 它引发的产物 credit 之和 × 20%，有上限。

    ★ 问题不能按复现成本算：一个好问题的复现成本是负的 ——
      别人不是"从零想出这个问题"，是根本想不到它。
    """
    if generated <= 0:
        return 0.0
    return round(min(generated * QUESTION_ROYALTY_RATE, QUESTION_ROYALTY_CAP), 4)


def generated_by(requests: list[dict], deliverables: list[dict], credits: list[dict],
                 question_id: str) -> float:
    """某个问题（question 类型的单）已经引发的产物 credit 之和。"""
    # ★ 用 project 而不是"最后一条记录"：artifact_kind / links 在 open 记录里，
    #   claim/confirm 那些增量记录里没有它们（踩过两次这个坑）。
    proj = project(requests)
    req = proj.get(question_id)
    if req is None or req.get("artifact_kind") != "question":
        return 0.0

    # ★ links 在【申请单】上，不在交付物上（踩过这个坑）。
    #   所以要先找出"哪些单引用了这个问题"，再找那些单下面的交付物。
    linked_requests = {question_id}
    for rid, r in proj.items():
        if question_id in (r.get("links") or []):
            linked_requests.add(rid)

    linked = {
        d.get("id")
        for d in deliverables
        if d.get("op") == "create" and d.get("request_id") in linked_requests
    }

    total = 0.0
    for c in credits:
        if c.get("op") in ("mint", "reuse", "reuse_bonus"):
            refs = c.get("refs") or []
            if any(r in linked for r in refs):
                total += c.get("amount") or 0
    return round(total, 4)


def is_reviewed_pass(deliverables: list[dict], did: str) -> dict | None:
    """某件交付物的通过复核记录（没有就返回 None）。"""
    found = None
    for rec in deliverables:
        if rec.get("id") == did and rec.get("op") == "review":
            if (rec.get("review") or {}).get("result") == "pass":
                found = rec
    return found


def review_minutes(deliverables: list[dict], did: str) -> float | None:
    """从复核记录里取复现时间估计。"""
    rec = is_reviewed_pass(deliverables, did)
    if not rec:
        return None
    m = (rec.get("review") or {}).get("reproduce_minutes")
    return m if isinstance(m, (int, float)) and m > 0 else None


def derive_reuse(deliverables: list[dict]) -> dict[str, int]:
    """从 links 推导每件交付物被引用了几次。

    ★ 为什么要推导而不是让作者自己报：复用是 credit 唯一的加成项，
      如果靠自报就失去了"无法伪造"这个性质。
      引用一件东西必须先读懂它 —— 没人会为了给别人刷分去读没用的东西。

    ★ 必须基于 project() 而不是原始事件流：一件交付物被 amend 之后，
      它的 links 在【修正记录】里，原始 create 记录里还是旧值。
      （踩过这个坑：amend 改对了 links，但复用计数仍然是 0）
    """
    counts: dict[str, int] = {}
    for did, rec in project(deliverables).items():
        if not rec.get("deliverer"):
            continue  # 不是真正的交付物
        links = rec.get("links")
        if not isinstance(links, list):
            # 类型不对就当没有 —— 宁可少算，不要因为脏数据算错
            continue
        for link in links:
            if isinstance(link, str) and link.startswith("d-") and link != did:
                counts[link] = counts.get(link, 0) + 1
    return counts


def credited_deliverables(credits: list[dict]) -> set[str]:
    """已经入过账的交付物 id —— 保证幂等，不会重复发。"""
    out = set()
    for rec in credits:
        if rec.get("op") in ("mint", "reuse"):
            for ref in rec.get("refs") or []:
                if isinstance(ref, str) and ref.startswith("d-"):
                    out.add(ref)
    return out


def request_status(records: list[dict]) -> dict[str, str]:
    """从事件推导每张单的当前状态。永远以推导为准，不看 status_declared。"""
    ops: dict[str, set[str]] = {}
    for rec in records:
        rid = rec.get("id")
        op = rec.get("op")
        if isinstance(rid, str) and isinstance(op, str):
            ops.setdefault(rid, set()).add(op)

    out: dict[str, str] = {}
    for rid, seen in ops.items():
        if "close" in seen:
            out[rid] = "closed"
        elif "withdraw" in seen:
            out[rid] = "withdrawn"
        elif "confirm" in seen:
            out[rid] = "accepted"
        elif "deliver" in seen:
            out[rid] = "delivered"
        elif "claim" in seen:
            out[rid] = "claimed"
        elif "open" in seen:
            out[rid] = "open"
        else:
            out[rid] = "unknown"
    return out


#: 单子多久没人接就算过期（天）。v1 定为 90 天，因为池子还小，
#: 过早过期会把本来有效的需求误杀掉。有人抱怨太慢再调。
EXPIRES_DAYS = 90

#: 异议必须在这个天数内被回应（p-007）。一个人掌权时最常用、
#: 最难防的手段不是乱做事，是不回应。
OBJECTION_SLA_DAYS = 7

#: 单一来源在单周期内计入的 credit 占比上限（p-008）。
#: 活跃成员少于 3 人时不执行 —— 三人阶段一个人产出多是正常且应被鼓励的。
SOURCE_SHARE_CAP = 0.60
SOURCE_SHARE_MIN_MEMBERS = 3
