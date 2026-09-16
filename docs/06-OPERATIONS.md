# 操作

> 合并自 `20-操作指引` 与 `21-确认清单` / `23-待确认清单-早期`。
> **已完成的事不再复述怎么做的，只列结论。** 还没做的写清怎么开始。

---

## 一、现在是什么状态

```
✅ 仓库            67 个文件，真相源在 /home/weathour/公共/pool-build
✅ 五份 JSONL      data/requests|deliverables|credits|keyring|maintainer-log
✅ 11 条规则       全部实现，且每条都有一个能被抓住的反例测试
✅ 工具            10 个（见下）
✅ 测试            101 项全通过（38 单元 + 21 规则 + 21 元一致性 + 21 端到端）
✅ Gitea           本机 3000 端口，systemd 常驻，开机自启
✅ 分支保护        main 禁直推、禁强推
✅ 主页定制        自定义主页 + 实时数字
✅ 公开镜像        github.com/weathour/pool（pre-push 钩子自动同步）
✅ 周期签名 tag    cycle-2026-Q3（从公开 clone 独立验证过）
⬜ 域名            weathour.xyz 注册审核中
⬜ Cloudflare      Tunnel + Pages（等域名）
⬜ Gitea Actions runner  工作流配了，但没装 runner
⬜ 定时器          设计过，未实现
```

**账号：**

| 用途 | 用户名 | 密码/凭据在哪 |
|---|---|---|
| Gitea 管理员 | `JiaWeathour` | `~/gitea/首次登录密码.txt` |
| Gitea 测试号 | `yan` / `xinren` | `~/gitea/雁的密码.txt` 等 |
| GitHub | `weathour` | `gh` CLI 已登录 |
| 签名身份 | `JiaWeathour` | `~/.pool/keys/JiaWeathour.key` |

**★ 测试账号 `yan` / `xinren` 是我做端到端验证造的，现在是公开的。要退休就追加 `status: retired`（记录不能删）。**

---

## 二、日常怎么用

```bash
cd ~/公共/pool-build

make status     # 一眼看清现状（含"通过了但没人用"）
make cycle      # 周期结算（只报告，不写）
make check      # 跑全部 101 项测试
make home       # 刷新 Gitea 主页的数字
make site       # 生成展示页到 site/

python3 tools/validate      # 只校验账本
python3 tools/verify-meta   # 只校验仓库自身的一致性
```

**开一张单：**

```bash
python3 tools/sign req --kind question \
  --title "一句话说清缺什么" \
  --spec "产物长什么样：形态、范围、精度要求" \
  --acceptance "怎么算做完了（必须可判定）"
```

**★ 唯一不能省的是 `--acceptance`。** 判断方法：把这句话给一个不认识你的人，**他能不能只靠它决定"这东西算不算做完了"**。写不出来，说明这张单还不该开。

**改错一条记录：**

```bash
python3 tools/sign amend deliverables d-xxx --reason "为什么改" --set 'links=["d-yyy"]'
```

**★ 永远不要用编辑器直接改 `data/*.jsonl`** —— 会破坏哈希链，校验器立刻报 `hash 与内容不符`。（作者本人踩过。）

**推送到 Gitea（会顺带镜像到 GitHub）：**

```bash
git push origin main
```

---

## 三、十个工具

| 工具 | 干什么 |
|---|---|
| `tools/sign` | 生成密钥、开单、接单、交付、复核、确认、入账、复用、修正 |
| `tools/validate` | **唯一发号施令的地方** —— 链 + 签名 + 11 条规则 |
| `tools/status` | 一眼看清现状（`--json` 给机器读） |
| `tools/cycle` | 周期结算；`--tag` 打签名 tag |
| `tools/verify-meta` | 管"记录之外"的一致性 |
| `tools/resolve` | git 合并冲突后去掉标记、去重、重排、重链 |
| `tools/fixlinks` | 链尾错位时重算 `seq` / `prev` |
| `tools/build-site` | 生成自包含静态展示页 |
| `tools/gitea-home-data` | 生成 Gitea 主页要用的 JSON |
| （Makefile） | 上面那些的快捷方式 |

---

## 四、接下来要做的事

### 1. 域名到了之后（约 15 分钟）

```
① cloudflared tunnel create pool
② DNS: git.weathour.xyz → 隧道
③ 装 systemd 服务（开机自启 + 断线重连）
④ 改 Gitea 的 ROOT_URL / DOMAIN（不再指向 localhost）
⑤ 改仓库文档里写死的 localhost 地址
⑥ 建 Cloudflare Pages 项目，从 GitHub 镜像构建
⑦ 从公网验证两个地址（用 web_fetch 从外部看，不只看本机）
⑧ 更新 .allowed_signers 与各处地址
```

**需要你先做的**：注册域名 → 在 Cloudflare 加站点 → 把 NS 改过去 → 跑一次 `cloudflared tunnel login`（那会生成一份只对这个域名有效的证书）。

**做完会拿到：**
```
git.weathour.xyz   ← 协作界面
weathour.xyz       ← 公开展示页
```

### 2. 装 Gitea Actions runner（可选）

工作流文件已经配好（`.gitea/workflows/validate.yml`），但没有 runner 就不会执行。**现在校验靠手动跑 `make check`。**

**★ 不装也行** —— 装 runner 要拉 Docker 镜像，而这台机器连不上 Docker Hub。

### 3. 定时器（设计好了，未实现）

设计要点在 [`05-GOVERNANCE.md`](05-GOVERNANCE.md)。**最有价值的那条告警是"通过了但没人用"** —— 它已经实现在 `make status` 里了，定时器只是把它推给你。

### 4. 备份（★ 建议现在做）

**三种状态里，两种没有冗余：**

| | 冗余 | 丢了会怎样 |
|---|---|---|
| 内容（`data/` + 代码） | ✅ 三份（本地 / Gitea / GitHub） | 能还原 |
| **协作过程**（`gitea.db`） | ❌ 一份 | **issue / PR / 评论全丢** |
| **身份**（私钥） | ❌ 一份 | **再也无法用这个身份签名** |

```bash
tar czf ~/pool-critical-$(date +%F).tar.gz \
    -C ~ .pool/keys .pool/handle \
    gitea/data/gitea.db gitea/custom
```

**★ 拷到另一个地方** —— 放同一个盘上等于没备。

---

## 五、还没定的事

| # | 事项 | 现状 / 选项 |
|---|---|---|
| 1 | **域名** | `weathour.xyz` 审核中。★ 注意 `.xyz` 在大陆部分网络会被拦 —— 认真做的话 `.com` 更好 |
| 2 | **衰减参数** | 设计是"活跃度衰减、半衰期 180 天"，**没定也没实现**（credit 还换不到东西，不急） |
| 3 | **单一来源上限** | 实现成 60%，且"少于 3 人拿过 credit 时不执行" |
| 4 | **Gitea 账号归属** | 现在的主账号是我用你邮箱建的、密码我知道。**要不要你自己重新注册、我把仓库转给你？** |
| 5 | **两个测试账号** | `yan` / `xinren` 已公开在 GitHub 上。留还是退休？ |
| 6 | **语料要不要公开** | 那 363 篇 B 站转写在另一个仓库，**与池子仓库无关**。要公开得先拿到讲者的书面许可 |
| 7 | **机器人 / 定时器** | 要不要做，什么时候做 |
| 8 | **第二个维护者** | `MAINTAINERS.md` 里现在只有一个人 |
| 9 | **第一个真人** | ★ **最要紧的一条** —— 见下 |

---

## 六、★ 最要紧的一件事：第一个真人

**现在池子里所有活动都是作者一个人的。** 四个身份里有三个是测试号。

**域名、Cloudflare、runner、定时器都不解决这件事。**

**该做的：**

```
① 开一张【对你现在真有价值】的单子（不要用测试数据）
② 找一个真人，只给他 CONTRIBUTING.md
③ 看他在 5 分钟内能不能开出第一张单
④ 看他作为复核者，只看 spec + acceptance，能不能判定
```

**③④ 是真正的验证点** —— 比任何技术指标都重要：

- **机械步骤实测只要 0.8 秒**，所以门槛不在工具
- **真门槛是"写出可判定的验收标准"** —— 这一步不行，整套记账的可信度就不成立

**如果这一步过不了，加多少前端功能都没用。**

---

## 七、一句话

> **技术侧已经跑通并验证过了 —— 缺的不是功能，是人。**
> **域名只是让池子被看见；而"验收标准可判定"决定它值不值得被看见。**
