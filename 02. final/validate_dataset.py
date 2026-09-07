#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_dataset.py
================================================================================
재편찬 데이터셋 검증 도구 — 3단계 검증

    [1단계] 구문 검증   : 산출된 TTL이 유효한 RDF/Turtle 구문인가
    [2단계] 전수 정합성 : 집계 수준의 불변식이 모두 성립하는가
    [3단계] 표본 검증   : 변환 로직 자체가 원천 데이터를 정확히 반영하였는가

--------------------------------------------------------------------------------
왜 3단계인가
--------------------------------------------------------------------------------
파서가 오류를 내지 않는다는 것은 산출물이 문법적으로 유효하다는 뜻일 뿐,
변환 로직이 원천 데이터를 정확하게 반영하였다는 뜻이 아니다. 코드가 오류 없이
실행되는 것과 코드의 논리가 옳은 것은 별개의 문제이다. 잘못된 규칙으로
일관되게 변환된 데이터도 문법적으로는 완벽하게 유효하다.

따라서 3단계에서는 전체 관계 발생 기록에서 통계적으로 정당화되는 크기의
표본을 무작위로 추출하고, 각 표본을 원천 엑셀 파일로 직접 되짚어
필드 단위로 재대조한다. 이때 재대조 로직은 build_dataset.py를 호출하지 않고
독립적으로 다시 구현하여, 변환 스크립트의 오류가 검증 스크립트에 그대로
전이되지 않도록 한다.

--------------------------------------------------------------------------------
표본 크기 산정
--------------------------------------------------------------------------------
Cochran 공식에 유한모집단 보정을 적용한다.

    n₀ = z² · p(1−p) / e²
    n  = n₀ / (1 + (n₀ − 1) / N)

    z : 신뢰수준에 대응하는 표준정규분포 임계값
    p : 예상 오류 비율. 사전 정보가 없으므로 분산이 최대가 되는 0.5로
        보수적으로 설정한다(가장 큰 표본 크기를 요구하는 값).
    e : 허용 오차
    N : 모집단 크기

오류율의 구간 추정에는 Wilson score interval을 사용한다. 정규근사(Wald)
구간은 관측 오류율이 0에 가까울 때 구간 폭이 0으로 붕괴하여 무의미해지므로,
이 경우에도 타당한 상한을 제공하는 Wilson 구간이 적합하다.

--------------------------------------------------------------------------------
사용법
--------------------------------------------------------------------------------
    python3 validate_dataset.py
    python3 validate_dataset.py --confidence 0.99 --margin 0.03 --seed 42
    python3 validate_dataset.py --confidence 0.95 --margin 0.05 --seed 20260906
"""

import re
import json
import math
import random
import argparse
import unicodedata
from pathlib import Path

import pandas as pd

Z = {0.90: 1.645, 0.95: 1.960, 0.98: 2.326, 0.99: 2.576}
RESEARCHER_LABEL = "연구자"
EDITION = "edition:재편찬판본"


# ------------------------------------------------------------------ statistics
def cochran(N, confidence, margin, p=0.5):
    z = Z.get(round(confidence, 2))
    if z is None:
        raise ValueError(f"지원 신뢰수준: {sorted(Z)}")
    n0 = z**2 * p * (1 - p) / margin**2
    return math.ceil(n0 / (1 + (n0 - 1) / N)), z


def wilson(errors, n, z=1.96):
    if n == 0:
        return 0.0, 0.0, 0.0
    p = errors / n
    d = 1 + z**2 / n
    c = (p + z**2 / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


# ------------------------------------------------------------------ 독립 재구현
# build_dataset.py를 import하지 않고 변환 규칙을 다시 구현한다.
def norm(s):
    return "" if pd.isna(s) else unicodedata.normalize("NFC", str(s)).strip()


def expected_predicate(attribute, note):
    table = {"출생": "art:bornIn", "학교": "art:studiedAt",
             "소속단체": "art:memberOf", "직장": "art:workedAt"}
    if attribute in table:
        return table[attribute]
    if attribute == "발표매체":
        return ("art:issued" if "발행" in note
                else "art:edited" if "편집" in note
                else "art:authored")
    return None


def expected_year(raw):
    m = re.search(r"\d{4}", str(raw))
    return m.group(0) if m else ""


# ------------------------------------------------------------------ 검증 단계
def stage1_syntax(out_dir, log):
    """[1단계] TTL 구문 검증."""
    print("\n" + "=" * 72)
    print("[1단계] 구문 검증")
    print("=" * 72)
    results = {}
    try:
        import rdflib
    except ImportError:
        print("  rdflib 미설치 — 건너뜀 (pip install rdflib==7.6.0)")
        log["stage1"] = {"skipped": "rdflib not installed"}
        return

    for name in ["schema.ttl", "persons.ttl", "institutions.ttl"]:
        g = rdflib.Graph()
        g.parse(out_dir / name, format="turtle")
        results[name] = {"triples": len(g), "status": "OK"}
        print(f"  {name:<22} {len(g):>8,} triples  파싱 성공")

    # RDF-Star 인용 트리플은 rdflib 7.6.0이 지원하지 않으므로,
    # 인용 트리플 구문을 임시 URI로 치환한 뒤 나머지 문법을 검증한다.
    text = (out_dir / "relations_rdfstar.ttl").read_text(encoding="utf-8")
    n_quoted = len(re.findall(r"<<[^>]*>>", text))
    stripped = re.sub(r"<<[^>]*>>", "<https://sde1930.org/_quoted>", text)
    tmp = out_dir / "_syntax_check.ttl"
    tmp.write_text(stripped, encoding="utf-8")
    g = rdflib.Graph()
    g.parse(tmp, format="turtle")
    tmp.unlink()
    results["relations_rdfstar.ttl"] = {
        "triples_excluding_quoted_syntax": len(g),
        "quoted_triples": n_quoted,
        "status": "OK",
        "note": ("rdflib 7.6.0은 RDF-Star 인용 트리플 구문을 지원하지 않으므로 "
                 "해당 구문을 치환한 뒤 나머지 문법을 검증하였다. 전체 구문 검증은 "
                 "Apache Jena, Oxigraph, GraphDB 등 RDF-Star 지원 파서에서 가능하다."),
    }
    print(f"  {'relations_rdfstar.ttl':<22} {len(g):>8,} triples  파싱 성공 "
          f"(인용 트리플 {n_quoted:,}개 치환)")
    log["stage1"] = results


def stage2_integrity(src, relations, persons, insts, log):
    """[2단계] 전수 정합성 검증 — 집계 수준 불변식."""
    print("\n" + "=" * 72)
    print("[2단계] 전수 정합성 검증")
    print("=" * 72)
    checks = []

    def check(name, condition, detail=""):
        checks.append({"항목": name, "결과": "통과" if condition else "실패", "비고": detail})
        print(f"  [{'통과' if condition else '실패'}] {name}" + (f"  — {detail}" if detail else ""))

    check("행 수 보존 (원천 = 산출)",
          len(src) == len(relations), f"{len(src):,} = {len(relations):,}")

    rid = relations["relation_id"].str[1:].astype(int)
    srow = relations["sourceRow"].str.extract(r"(\d+)")[0].astype(int)
    check("relation_id 연속성 (결번·중복 없음)",
          rid.is_monotonic_increasing and rid.is_unique and rid.iloc[0] == 1)
    check("relation_id ↔ sourceRow 규약 (n ↔ n+1)", bool((rid + 1 == srow).all()))
    check("sourceRow 원천 범위 내", bool((srow >= 2).all() and (srow <= len(src) + 1).all()))

    check("인물 식별자 무결성 (참조 대상 존재)",
          relations["subject"].isin(set(persons["person_id"])).all())
    check("기관 식별자 무결성 (참조 대상 존재)",
          relations["object"].isin(set(insts["institution_id"])).all())
    check("인물 식별자 유일성", persons["person_id"].is_unique)
    check("기관 식별자 유일성", insts["institution_id"].is_unique)

    check("술어 미배정 행 없음", relations["predicate"].notna().all())
    check("출처 공란 없음 (연구자 정규화)", (relations["source"].fillna("") != "").all())
    check("판본 정보 단일", relations["edition"].nunique() == 1, relations["edition"].iloc[0])

    src_attr = src["속성"].value_counts().sort_index()
    out_attr = relations["attributeLabel"].value_counts().sort_index()
    check("관계 유형별 빈도 보존", src_attr.equals(out_attr))

    # 술어 계층 정합: attributeLabel과 predicate의 대응이 규칙과 일치하는가
    hierarchy = {
        "출생": {"art:bornIn"}, "학교": {"art:studiedAt"},
        "소속단체": {"art:memberOf"}, "직장": {"art:workedAt"},
        "발표매체": {"art:authored", "art:edited", "art:issued",
                 "art:contributedTo", "art:printed"},
    }
    ok = all(
        set(relations[relations["attributeLabel"] == a]["predicate"]) <= p
        for a, p in hierarchy.items()
    )
    check("술어 계층 정합 (속성 → 술어 대응)", ok)

    failed = [c for c in checks if c["결과"] == "실패"]
    print(f"\n  전체 {len(checks)}개 항목 중 {len(checks) - len(failed)}개 통과, {len(failed)}개 실패")
    log["stage2"] = {"checks": checks, "passed": len(checks) - len(failed), "failed": len(failed)}
    return len(failed) == 0


def stage3_sampling(src, relations, person_info, group_info, args, log):
    """[3단계] 신뢰구간 기반 무작위 표본 검증."""
    print("\n" + "=" * 72)
    print("[3단계] 표본 검증 (신뢰구간 기반 무작위 추출)")
    print("=" * 72)

    # 식별자 해소를 독립적으로 재구현
    exact, by_name_nat, by_name = {}, {}, {}
    for _, r in person_info.iterrows():
        pid = f"P{int(re.search(r'\d+', str(r['Person_ID'])).group()):06d}"
        exact[(norm(r["작가 이름"]), norm(r["직업"]), norm(r["국적"]))] = pid
        by_name_nat.setdefault((norm(r["작가 이름"]), norm(r["국적"])), set()).add(pid)
        by_name.setdefault(norm(r["작가 이름"]), set()).add(pid)
    inst_lookup = {
        norm(r["단체명"]): f"I{int(re.search(r'\d+', str(r['단체_ID'])).group()):06d}"
        for _, r in group_info.iterrows()
    }

    def resolve(name, job, nat):
        if (name, job, nat) in exact:
            return exact[(name, job, nat)]
        for cands in (by_name_nat.get((name, nat), set()), by_name.get(name, set())):
            if len(cands) == 1:
                return next(iter(cands))
        return None

    N = len(relations)
    if args.sample_size:
        n, z = args.sample_size, Z.get(round(args.confidence, 2), 1.96)
    else:
        n, z = cochran(N, args.confidence, args.margin)
    n = min(n, N)

    random.seed(args.seed)
    sample = random.sample(range(N), n)

    field_errors = {k: 0 for k in
                    ["subject", "predicate", "object", "year",
                     "attributeLabel", "note", "source", "edition"]}
    mismatches = []

    for idx in sample:
        row = relations.iloc[idx]
        # sourceRow가 주장하는 원본 위치로 직접 되짚는다.
        excel_row = int(re.search(r"(\d+)", row["sourceRow"]).group(1))
        s = src.iloc[excel_row - 2]

        expected = {
            "subject": resolve(s["작가"], s["직업"], s["국적"]),
            "predicate": expected_predicate(s["속성"], s["비고"]),
            "object": inst_lookup.get(s["소속단체"]),
            "year": expected_year(s["년도"]),
            "attributeLabel": s["속성"],
            "note": s["비고"],
            "source": s["출처"] or RESEARCHER_LABEL,
            "edition": EDITION,
        }
        errs = {f: {"기대값": v, "실제값": row[f]}
                for f, v in expected.items() if row[f] != v}
        if errs:
            for f in errs:
                field_errors[f] += 1
            mismatches.append({"relation_id": row["relation_id"],
                               "sourceRow": row["sourceRow"], "errors": errs})

    e = len(mismatches)
    rate, lo, hi = wilson(e, n, 1.96)

    print(f"  모집단 크기 N            : {N:,}")
    print(f"  신뢰수준 / 허용오차      : {args.confidence*100:.0f}% / ±{args.margin*100:.1f}%p")
    print(f"  산정 표본 크기 n         : {n:,}   (난수 시드 {args.seed})")
    print(f"  표본 내 오류             : {e} / {n:,} 건  (오류율 {rate*100:.4f}%)")
    print(f"  모집단 오류율 95% Wilson : [{lo*100:.4f}%, {hi*100:.4f}%]")
    nz = {k: v for k, v in field_errors.items() if v}
    print(f"  필드별 오류              : {nz if nz else '없음'}")

    log["stage3"] = {
        "method": "Cochran 공식 + 유한모집단 보정, 단순무작위추출(비복원)",
        "population_N": N,
        "confidence_level": args.confidence,
        "margin_of_error": args.margin,
        "assumed_p": 0.5,
        "z_value": z,
        "sample_size_n": n,
        "random_seed": args.seed,
        "errors_in_sample": e,
        "observed_error_rate": round(rate, 6),
        "wilson_95_ci": [round(lo, 6), round(hi, 6)],
        "field_error_counts": field_errors,
        "mismatch_details": mismatches[:50],
        "verified_fields": list(field_errors.keys()),
    }
    return e == 0


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="재편찬 데이터셋 3단계 검증")
    ap.add_argument("--rdb-xlsx", default="RDB_재편_제안.xlsx")
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--confidence", type=float, default=0.99)
    ap.add_argument("--margin", type=float, default=0.03)
    ap.add_argument("--sample-size", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    log = {"validated_at_seed": args.seed}

    src = pd.read_excel(args.rdb_xlsx, sheet_name="SDE_ver.2(재편찬 데이터셋)").fillna("")
    src.columns = ["작가", "직업", "국적", "년도", "소속단체", "속성", "비고", "출처"]
    for c in src.columns:
        src[c] = src[c].map(norm)
    person_info = pd.read_excel(args.rdb_xlsx, sheet_name="1. Person_Info").fillna("")
    group_info = pd.read_excel(args.rdb_xlsx, sheet_name="2. Group_Info").fillna("")

    relations = pd.read_csv(out_dir / "relations.csv", dtype=str).fillna("")
    persons = pd.read_csv(out_dir / "persons.csv", dtype=str).fillna("")
    insts = pd.read_csv(out_dir / "institutions.csv", dtype=str).fillna("")

    stage1_syntax(out_dir, log)
    ok2 = stage2_integrity(src, relations, persons, insts, log)
    ok3 = stage3_sampling(src, relations, person_info, group_info, args, log)

    report_path = Path(args.report) if args.report else \
        out_dir / f"validation_report_seed{args.seed}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 72)
    print(f"종합 결과: {'전 단계 통과' if (ok2 and ok3) else '실패 항목 있음 — 리포트 확인 요망'}")
    print(f"리포트 저장: {report_path}")
    print("=" * 72)


if __name__ == "__main__":
    main()
