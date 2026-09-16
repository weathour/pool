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

#: 四份数据文件
DATA_FILES = ("requests", "deliverables", "credits", "keyring")

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
