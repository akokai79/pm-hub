"""총괄판 업데이트 검수 — 새 dashboard.html 을 올리기 전에 오류 · 충돌 가능성을 점검한다.

사용:
  python -X utf8 update-check.py --new <저장소 최신 dashboard.html> --live <지금 총괄판 HTML> \
      [--base <지금 버전의 배포본 dashboard.html>] [--caps db,sample,artifact] [--out result.json]

점검 (실행 시험이 아니라 파일 대조다):
  1. 스크립트 무결성 — 스크립트가 중간에 끊기지 않는지(</script> 문자열), 문법(node 가 있으면)
  2. 올리는 형식 — 문서 뼈대(<html> · <body>)를 또 넣지 않았는지, 크기
  3. 권한 충돌 — 새 파일이 쓰는 권한(claude.use 이름)이 총괄판에 선언된 권한 안인지
  4. 데이터 충돌 — 새 파일이 읽고 쓰는 컬렉션이 지금 것과 어떻게 다른지
  5. 외부 주소 · 위험 호출 — 새로 생긴 외부 주소, eval · Function · document.write
  6. 내 수정 충돌 — 지금 총괄판이 배포본(--base)과 다르면 업데이트가 그 변경을 덮어쓴다
결과 JSON: {from, to, ok, items:[{level: 오류|주의|정보, text}]} — 오류가 하나라도 있으면 ok=false.
"""
import argparse, hashlib, json, re, shutil, subprocess, sys, tempfile, os


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def version(s):
    m = re.search(r'PMHUB_VERSION\s*=\s*"(\d+\.\d+\.\d+)"', s)
    return m.group(1) if m else ""


def vkey(v):
    return tuple(int(x) for x in v.split(".")) if re.match(r"^\d+\.\d+\.\d+$", v or "") else (0, 0, 0)


def scripts(s):
    """HTML 파서처럼: 첫 <script> 부터 가장 먼저 나오는 </script> 까지가 스크립트다."""
    out, i = [], 0
    while True:
        a = s.find("<script", i)
        if a < 0:
            break
        b = s.find(">", a)
        e = s.find("</script>", b)
        if b < 0 or e < 0:
            out.append((s[a:], False))
            break
        out.append((s[b + 1:e], True))
        i = e + 9
    return out


def collections(s):
    names = set()
    for m in re.finditer(r'\.(?:doc|collection)\(\s*["\']([A-Za-z0-9_-]+)', s):
        names.add(m.group(1))
    for m in re.finditer(r'\[\s*((?:"[A-Za-z0-9_-]+"\s*,?\s*)+)\]\.forEach\(c=>', s):
        names.update(re.findall(r'"([A-Za-z0-9_-]+)"', m.group(1)))
    return names


def writes(s):
    return set(re.findall(r'\.doc\(\s*["\']([A-Za-z0-9_-]+)[^)]*\)\s*\.(?:set|update|delete)\(', s))


def caps_used(s):
    return set(re.findall(r'claude\.use\(\s*["\']([a-z]+)["\']', s))


def urls(s):
    return set(m.group(1).lower() for m in re.finditer(r'https?://([A-Za-z0-9.-]+)', s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", required=True)
    ap.add_argument("--live", required=True)
    ap.add_argument("--base", default="")
    ap.add_argument("--caps", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    new, live = read(a.new), read(a.live)
    items = []

    def add(level, text):
        items.append({"level": level, "text": text})

    vn, vl = version(new), version(live)
    if not vn:
        add("오류", "새 파일에 버전 표시(PMHUB_VERSION)가 없습니다. 올바른 총괄판 파일이 아닙니다.")
    elif vkey(vn) <= vkey(vl):
        add("주의", "새 파일 버전(%s)이 지금(%s)보다 높지 않습니다. 올릴 필요가 없을 수 있습니다." % (vn, vl or "?"))
    else:
        add("정보", "버전 %s → %s" % (vl or "?", vn))

    # 1. 스크립트 무결성
    sc = scripts(new)
    if not sc:
        add("오류", "새 파일에 스크립트가 없습니다.")
    else:
        if any(not ok for _, ok in sc):
            add("오류", "스크립트가 닫히지 않았습니다(</script> 없음).")
        main_js = max((t for t, ok in sc if ok), key=len, default="")
        if main_js.count("</script") or new.count("</script>") != len(sc):
            add("오류", "스크립트 안에 </script> 문자열이 있어 브라우저가 스크립트를 중간에서 끊습니다 — 총괄판이 멈춥니다(1.9.0 에서 났던 오류).")
        node = shutil.which("node")
        if node:
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, "x.js")
                with open(p, "w", encoding="utf-8") as f:
                    f.write(main_js)
                r = subprocess.run([node, "--check", p], capture_output=True, text=True)
                if r.returncode != 0:
                    lines = [l for l in r.stderr.splitlines() if "Error" in l]
                    msg = (lines[0] if lines else "문법 오류")
                    add("오류", "스크립트 문법 오류: " + msg[:160])
                else:
                    add("정보", "스크립트 문법 검사 통과")
        else:
            add("주의", "node 가 없어 문법 검사를 하지 못했습니다(나머지 점검만 했습니다).")

    # 2. 올리는 형식 · 크기
    outside = re.sub(r"<script[\s\S]*?</script>", "", new)  # 스크립트 안 문자열(자기 갱신용 뼈대 등)은 제외
    if re.search(r"<!doctype|<html[\s>]|<body[\s>]", outside, re.I):
        add("오류", "새 파일에 문서 뼈대(<!doctype> · <html> · <body>)가 들어 있습니다. 올리면 뼈대가 겹칩니다.")
    if "<title>" not in new:
        add("주의", "새 파일에 <title> 이 없습니다.")
    size = len(new.encode("utf-8"))
    if size > 2_000_000:
        add("주의", "파일이 큽니다(%d KB)." % (size // 1024))

    # 3. 권한 충돌
    cn, cl = caps_used(new), caps_used(live)
    declared = set(x.strip() for x in a.caps.split(",") if x.strip()) or cl
    declared |= {"permissions"}
    need = cn - declared
    if need:
        add("오류", "새 파일이 총괄판에 선언되지 않은 권한을 씁니다: %s. 올릴 때 capabilities 에 넣어야 합니다." % ", ".join(sorted(need)))
    elif cn - cl:
        add("주의", "새 파일이 새 권한을 씁니다: %s (선언된 권한 안이라 올릴 수는 있습니다)." % ", ".join(sorted(cn - cl)))
    else:
        add("정보", "권한 충돌 없음 (%s)" % ", ".join(sorted(cn)))

    # 4. 데이터 충돌
    colN, colL = collections(new), collections(live)
    addc, delc = colN - colL, colL - colN
    if addc:
        add("정보", "새로 읽는 컬렉션: %s (없어도 화면은 열립니다)" % ", ".join(sorted(addc)))
    if delc:
        add("주의", "더 이상 읽지 않는 컬렉션: %s (데이터는 지워지지 않고 화면에만 안 보입니다)" % ", ".join(sorted(delc)))
    wn = writes(new) - writes(live)
    if wn:
        add("주의", "새로 쓰기 시작하는 컬렉션: %s (총괄판 규칙상 소유자만 씁니다)" % ", ".join(sorted(wn)))
    if not addc and not delc and not wn:
        add("정보", "데이터 구조 변화 없음")

    # 5. 외부 주소 · 위험 호출
    newu = urls(new) - urls(live)
    if newu:
        add("주의", "새로 생긴 외부 주소: %s" % ", ".join(sorted(newu)))
    for pat, nm in ((r"\beval\s*\(", "eval("), (r"new\s+Function\s*\(", "new Function("), (r"document\.write\s*\(", "document.write(")):
        if re.search(pat, new) and not re.search(pat, live):
            add("주의", "새로 생긴 위험 호출: " + nm)

    # 6. 내 수정 충돌
    if a.base:
        base = read(a.base)
        if base.strip() != live.strip():
            bl, ll = set(base.splitlines()), set(live.splitlines())
            diff = len(bl ^ ll)
            add("주의", "지금 총괄판이 배포본(%s)과 다릅니다(차이 약 %d줄). 업데이트하면 직접 고친 부분이 사라집니다." % (vl or "?", diff))
        else:
            add("정보", "지금 총괄판은 배포본과 같습니다(직접 고친 부분 없음)")
    else:
        add("주의", "배포본(--base)이 없어 직접 고친 부분이 있는지는 확인하지 못했습니다.")

    sha = {"new": hashlib.sha256(new.encode("utf-8")).hexdigest(), "live": hashlib.sha256(live.encode("utf-8")).hexdigest()}
    add("정보", "파일 지문(sha256 앞 12자) 새 %s · 지금 %s — 올린 뒤 같은 값이 나오는지 대조하세요(줄바꿈은 LF 로 맞춰 계산)" % (sha["new"][:12], sha["live"][:12]))
    res = {"from": vl, "to": vn, "sha256": sha, "ok": not any(i["level"] == "오류" for i in items), "items": items}
    s = json.dumps(res, ensure_ascii=False, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(s)
    print(s)
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
