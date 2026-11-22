"""Claude 설정 한눈에 보기 — 총괄판 「설정」 탭 데이터.

~/.claude 와 지정한 작업 폴더에서 CLAUDE.md · 스킬 · 훅 · 플러그인 · MCP · 권한 · 메모리 · 예약 작업 · 에이전트를
읽어 JSON 하나로 만든다. 비밀값으로 보이는 것(토큰 · 키 · 비밀번호 · 긴 난수 문자열)은 가리고,
환경변수 · MCP 설정은 이름만 적는다.

사용: python -X utf8 config.py --dirs D:\\AI D:\\work --out config.json
"""
import argparse, glob, json, os, re, sys

HOME = os.path.expanduser("~/.claude")
SECRET = [
    (re.compile(r"(?i)\b(sk-[a-z0-9_-]{12,}|gh[pousr]_[a-z0-9]{20,}|xox[abpr]-[a-z0-9-]{10,}|AKIA[0-9A-Z]{16})"), "•••"),
    (re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]{16,}"), r"\1•••"),
    (re.compile(r"(?i)((?:api[_-]?key|token|secret|password|passwd|pwd|auth)[\"'\s]*[:=]\s*[\"']?)[^\s\"',;]+"), r"\1•••"),
    (re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}\b"), "•••"),
]


def clean(s, limit=None):
    s = str(s or "")
    for rx, rep in SECRET:
        s = rx.sub(rep, s)
    if limit and len(s) > limit:
        s = s[:limit] + "…"
    return s


def readj(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def readt(p, limit=60000):
    try:
        with open(p, encoding="utf-8") as f:
            return clean(f.read(), limit)
    except Exception:
        return None


def front(p):
    """SKILL.md 앞머리의 name · description."""
    t = readt(p, 4000) or ""
    m = re.match(r"^---\s*\n(.*?)\n---", t, re.S)
    meta = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"')
    return meta.get("name") or os.path.basename(os.path.dirname(p)), clean(meta.get("description", ""), 220)


def hooks_of(settings, where):
    out = []
    for ev, groups in ((settings or {}).get("hooks") or {}).items():
        for g in groups or []:
            for h in g.get("hooks") or []:
                out.append({"event": ev, "matcher": g.get("matcher", ""), "type": h.get("type", ""),
                            "command": clean(h.get("command") or h.get("prompt") or h.get("url") or "", 300),
                            "where": where})
    return out


def perms_of(settings, where):
    p = (settings or {}).get("permissions") or {}
    out = []
    for k in ("allow", "ask", "deny", "additionalDirectories"):
        for v in p.get(k) or []:
            out.append({"kind": k, "rule": clean(v, 200), "where": where})
    if p.get("defaultMode"):
        out.append({"kind": "defaultMode", "rule": p["defaultMode"], "where": where})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", nargs="*", default=[])
    ap.add_argument("--out")
    a = ap.parse_args()

    user = readj(os.path.join(HOME, "settings.json")) or {}
    claude_json = readj(os.path.expanduser("~/.claude.json")) or {}

    claudemd = []
    t = readt(os.path.join(HOME, "CLAUDE.md"))
    if t:
        claudemd.append({"where": "전체 (~/.claude/CLAUDE.md)", "text": t})
    hooks = hooks_of(user, "전체")
    perms = perms_of(user, "전체")
    mcp = [{"name": n, "type": (v or {}).get("type", "stdio"), "where": "전체"} for n, v in (claude_json.get("mcpServers") or {}).items()]
    memory = []

    for d in a.dirs:
        for name in ("CLAUDE.md", os.path.join(".claude", "CLAUDE.md"), "CLAUDE.local.md"):
            t = readt(os.path.join(d, name))
            if t:
                claudemd.append({"where": d + " (" + name + ")", "text": t})
        for sname in ("settings.json", "settings.local.json"):
            s = readj(os.path.join(d, ".claude", sname))
            if s:
                hooks += hooks_of(s, d + " " + sname)
                perms += perms_of(s, d + " " + sname)
        pj = readj(os.path.join(d, ".mcp.json")) or {}
        mcp += [{"name": n, "type": (v or {}).get("type", "stdio"), "where": d + " .mcp.json"} for n, v in (pj.get("mcpServers") or {}).items()]
        proj = (claude_json.get("projects") or {}).get(d.replace("\\", "/")) or (claude_json.get("projects") or {}).get(d) or {}
        mcp += [{"name": n, "type": (v or {}).get("type", "stdio"), "where": d} for n, v in (proj.get("mcpServers") or {}).items()]
        key = re.sub(r"[^A-Za-z0-9]", "-", d)
        mi = os.path.join(HOME, "projects", key, "memory", "MEMORY.md")
        if os.path.exists(mi):
            for line in (readt(mi) or "").splitlines():
                m = re.match(r"-\s*\[(.+?)\]\((.+?)\)\s*[—-]?\s*(.*)", line.strip())
                if m:
                    memory.append({"title": m.group(1), "file": m.group(2), "hook": clean(m.group(3), 160), "where": d})

    skills, seen = [], set()
    for p in sorted(glob.glob(os.path.join(HOME, "skills", "*", "SKILL.md"))):
        n, desc = front(p)
        skills.append({"name": n, "desc": desc, "source": "내 스킬"}); seen.add(n)
    for p in sorted(glob.glob(os.path.join(HOME, "skills", "synced", "**", "SKILL.md"), recursive=True)):
        n, desc = front(p)
        if n not in seen:
            skills.append({"name": n, "desc": desc, "source": "claude.ai 동기화"}); seen.add(n)
    for d in a.dirs:
        for p in sorted(glob.glob(os.path.join(d, ".claude", "skills", "*", "SKILL.md"))):
            n, desc = front(p)
            skills.append({"name": n, "desc": desc, "source": d}); seen.add(n)

    synced = {}
    for pj in glob.glob(os.path.join(HOME, "plugins", "synced", "*", "*", ".claude-plugin", "plugin.json")):
        meta = readj(pj) or {}
        root = os.path.dirname(os.path.dirname(pj))
        synced[meta.get("name") or os.path.basename(root)] = root
    plugins = []
    for pid, on in (user.get("enabledPlugins") or {}).items():
        name, _, market = pid.partition("@")
        if market == "synced" and name in synced:
            names = sorted({front(p)[0] for p in glob.glob(os.path.join(synced[name], "skills", "*", "SKILL.md"))})
            plugins.append({"id": pid, "on": bool(on), "skills": len(names), "skill_names": names[:40]})
            continue
        name, _, market = pid.partition("@")
        found = glob.glob(os.path.join(HOME, "plugins", "cache", market, name, "*", "skills", "*", "SKILL.md")) \
            or glob.glob(os.path.join(HOME, "plugins", "marketplaces", market, "**", name, "skills", "*", "SKILL.md"), recursive=True) \
            or glob.glob(os.path.join(HOME, "plugins", market, "*", name, "**", "SKILL.md"), recursive=True)
        names = sorted({front(p)[0] for p in found})
        plugins.append({"id": pid, "on": bool(on), "skills": len(names), "skill_names": names[:40]})

    agents = [os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(HOME, "agents", "*.md"))]
    for d in a.dirs:
        agents += [os.path.splitext(os.path.basename(p))[0] + " (" + d + ")" for p in glob.glob(os.path.join(d, ".claude", "agents", "*.md"))]
    tasks = sorted(os.path.basename(p) for p in glob.glob(os.path.join(HOME, "scheduled-tasks", "*")))

    other = {}
    for k in ("theme", "autoUpdatesChannel", "agentPushNotifEnabled", "skipDangerousModePermissionPrompt", "model", "language", "outputStyle"):
        if k in user:
            other[k] = clean(user[k], 80)
    if user.get("statusLine"):
        other["statusLine"] = clean((user["statusLine"] or {}).get("command", ""), 160)
    other["marketplaces"] = ", ".join((user.get("extraKnownMarketplaces") or {}).keys())
    other["env"] = ", ".join((user.get("env") or {}).keys()) or "없음"

    out = {
        "counts": {"claudemd": len(claudemd), "skills": len(skills), "plugins": len(plugins),
                   "plugins_on": sum(1 for p in plugins if p["on"]),
                   "plugin_skills": sum(p["skills"] for p in plugins), "hooks": len(hooks), "mcp": len(mcp),
                   "perms": len(perms), "memory": len(memory), "tasks": len(tasks), "agents": len(agents)},
        "claudemd": claudemd, "skills": skills, "plugins": plugins, "hooks": hooks, "mcp": mcp,
        "perms": perms, "memory": memory, "tasks": tasks, "agents": agents, "other": other,
    }
    js = json.dumps(out, ensure_ascii=False)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(js)
    sys.stdout.write(json.dumps(out["counts"], ensure_ascii=False) + "  bytes=" + str(len(js.encode("utf-8"))) + "\n")


if __name__ == "__main__":
    main()
