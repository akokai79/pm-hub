"""PM 총괄 토큰 · 금액 집계.

세션 기록(~/.claude/projects/*/<세션 id>.jsonl)에서 assistant 응답의 usage 를 모아
모델 · 노력별 토큰과 Anthropic 공식 API 가격 기준 금액(달러 · 원화)을 계산한다.
구독 요금제로 쓴 경우 실제 청구액이 아니라 「API 로 냈다면」의 환산값이다.

사용: python usage.py --ids <세션id> [<세션id> ...] [--krw 1360] [--lean-cli 10055] [--out usage.json]
"""
import argparse, glob, json, os, sys
from collections import defaultdict

# 1M 토큰당 달러 (Anthropic 공식 표, 2026-11 기준). 캐시 쓰기 5분 = 입력 x1.25, 1시간 = 입력 x2.
PRICES = {
    "claude-fable-5-2":  {"in": 8.0,  "out": 40.0, "read": 0.20},
    "claude-fable-5-1":  {"in": 10.0, "out": 50.0, "read": 1.00},
    "claude-opus-5-5":   {"in": 4.0,  "out": 20.0, "read": 0.20},
    "claude-opus-5":     {"in": 5.0,  "out": 25.0, "read": 0.50},
    "claude-sonnet-5-5": {"in": 2.0,  "out": 10.0, "read": 0.10},
    "claude-sonnet-5":   {"in": 2.0,  "out": 10.0, "read": 0.20},
    "claude-haiku-5-5":  {"in": 0.10, "out": 0.50, "read": 0.01},
}
DEFAULT = "claude-opus-5-5"


def price(model):
    for k, v in PRICES.items():
        if model and model.startswith(k):
            return k, v
    return model or "unknown", PRICES[DEFAULT]


def files_for(sid):
    home = os.path.expanduser("~/.claude/projects")
    out = glob.glob(os.path.join(home, "*", sid + ".jsonl"))
    out += glob.glob(os.path.join(home, "*", sid, "**", "*.jsonl"), recursive=True)  # 하위 에이전트
    return out


def cost(u, p):
    cc = u.get("cache_creation") or {}
    w1h = cc.get("ephemeral_1h_input_tokens", 0)
    w5m = cc.get("ephemeral_5m_input_tokens", u.get("cache_creation_input_tokens", 0) - w1h)
    return (u.get("input_tokens", 0) * p["in"] + u.get("output_tokens", 0) * p["out"]
            + u.get("cache_read_input_tokens", 0) * p["read"]
            + w5m * p["in"] * 1.25 + w1h * p["in"] * 2.0) / 1e6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="+", required=True)
    ap.add_argument("--krw", type=float, default=1360.0)
    ap.add_argument("--lean-cli", type=int, default=10055, help="MCP 끈 claude -p 의 시작 토큰(측정값)")
    ap.add_argument("--out")
    a = ap.parse_args()

    msgs = {}            # message id -> (model, effort, usage, session)
    first_prefix = {}    # session -> 첫 요청 프롬프트 크기
    for sid in a.ids:
        for f in files_for(sid):
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get("type") != "assistant":
                        continue
                    m = d.get("message") or {}
                    u = m.get("usage")
                    if not u or not m.get("id"):
                        continue
                    msgs[m["id"]] = (m.get("model"), d.get("effort") or "?", u, sid)
                    if sid not in first_prefix and not d.get("isSidechain"):
                        first_prefix[sid] = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                                             + u.get("cache_creation_input_tokens", 0))

    rows = defaultdict(lambda: {"requests": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "usd": 0.0})
    for model, effort, u, sid in msgs.values():
        name, p = price(model)
        r = rows[(name, effort)]
        r["requests"] += 1
        r["input"] += u.get("input_tokens", 0)
        r["output"] += u.get("output_tokens", 0)
        r["cache_read"] += u.get("cache_read_input_tokens", 0)
        r["cache_write"] += u.get("cache_creation_input_tokens", 0)
        r["usd"] += cost(u, p)

    total_tok = sum(r["input"] + r["output"] + r["cache_read"] + r["cache_write"] for r in rows.values())
    total_usd = sum(r["usd"] for r in rows.values())
    n_req = sum(r["requests"] for r in rows.values())

    # CLI 추정: 요청마다 싣는 고정 부담(도구 · 설명)이 가벼운 CLI 만큼 줄었다고 가정.
    # 줄어든 부분은 대부분 캐시 읽기로 실리므로 읽기 단가 + 세션당 한 번 1시간 쓰기 단가로 계산.
    save_tok, save_usd = 0, 0.0
    for model, effort, u, sid in msgs.values():
        delta = max(0, first_prefix.get(sid, 0) - a.lean_cli)
        _, p = price(model)
        save_tok += delta
        save_usd += delta * p["read"] / 1e6
    for sid, fp in first_prefix.items():
        delta = max(0, fp - a.lean_cli)
        save_usd += delta * PRICES[DEFAULT]["in"] * 2.0 / 1e6

    out = {
        "sessions": len(first_prefix), "requests": n_req,
        "tokens": total_tok, "usd": round(total_usd, 2), "krw": round(total_usd * a.krw),
        "krw_rate": a.krw,
        "by": [dict(model=k[0], effort=k[1], tokens=v["input"] + v["output"] + v["cache_read"] + v["cache_write"],
                    output=v["output"], requests=v["requests"], usd=round(v["usd"], 2), krw=round(v["usd"] * a.krw))
               for k, v in sorted(rows.items(), key=lambda kv: -kv[1]["usd"])],
        "cli": {"tokens": save_tok, "usd": round(save_usd, 2), "krw": round(save_usd * a.krw),
                "pct": round(100 * save_usd / total_usd, 1) if total_usd else 0,
                "lean_cli_prefix": a.lean_cli,
                "app_prefix_avg": round(sum(first_prefix.values()) / len(first_prefix)) if first_prefix else 0},
    }
    js = json.dumps(out, ensure_ascii=False, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(js)
    sys.stdout.write(js + "\n")


if __name__ == "__main__":
    main()
