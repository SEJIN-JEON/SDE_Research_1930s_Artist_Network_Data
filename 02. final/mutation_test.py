#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mutation_test.py
================================================================================
검증 도구 자체의 탐지력 확인 — 변이 검증(mutation testing)

--------------------------------------------------------------------------------
문제 설정
--------------------------------------------------------------------------------
validate_dataset.py가 "오류 0건"을 보고하였다는 사실만으로는 데이터가 정확하다고
단정할 수 없다. 검증 스크립트 자체에 결함이 있어 오류를 탐지하지 못하는 경우에도
동일한 결과가 나오기 때문이다. 검증의 신뢰성을 주장하려면, 그 검증이 실제로
오류를 탐지할 수 있다는 사실을 먼저 입증해야 한다.

이 스크립트는 정상 데이터에 알려진 수의 오류를 의도적으로 주입한 뒤
validate_dataset.py를 실행하여, 표본 검증이 그 오류를 통계적으로 기대되는
수준에서 탐지하는지 확인한다. 주입된 오류율이 표본 검증의 신뢰구간 안에
포함되면 검증 도구가 정상 작동한다고 판단한다.

--------------------------------------------------------------------------------
검증 논리
--------------------------------------------------------------------------------
모집단 N에 k건의 오류를 주입하면 참 오류율은 k/N이다. 크기 n의 표본에서
관측되는 오류 수는 초기하분포를 따르며, 그 기댓값은 n·k/N이다.
표본 검증이 산출하는 Wilson 신뢰구간이 참 오류율 k/N을 포함하면
검증 도구가 오류를 정상적으로 탐지하고 있다고 볼 수 있다.

--------------------------------------------------------------------------------
사용법
--------------------------------------------------------------------------------
    python3 mutation_test.py
    python3 mutation_test.py --injections 100 --seed 42

원본 relations.csv는 변경되지 않는다. 변이본은 임시 파일로 생성한 뒤
검증 종료 시 삭제한다.
"""

import json
import math
import shutil
import random
import argparse
import subprocess
from pathlib import Path

import pandas as pd


def hypergeom_pmf(k, N, K, n):
    """초기하분포 확률질량함수. 큰 조합수를 다루므로 로그감마로 계산한다."""
    if k < max(0, n - (N - K)) or k > min(n, K):
        return 0.0
    lc = lambda a, b: (math.lgamma(a + 1) - math.lgamma(b + 1) - math.lgamma(a - b + 1))
    return math.exp(lc(K, k) + lc(N - K, n - k) - lc(N, n))


def hypergeom_interval(N, K, n, alpha=0.01):
    """탐지 건수의 양측 (1-alpha) 예측구간.

    모집단 N에 오류 K건을 주입하고 크기 n의 표본을 뽑을 때
    표본에 포함되는 오류 건수는 초기하분포 Hypergeom(N, K, n)을 따른다.
    """
    lo_k, hi_k = max(0, n - (N - K)), min(n, K)
    pmf = {k: hypergeom_pmf(k, N, K, n) for k in range(lo_k, hi_k + 1)}
    cum, lower = 0.0, lo_k
    for k in sorted(pmf):
        cum += pmf[k]
        if cum >= alpha / 2:
            lower = k
            break
    cum, upper = 0.0, hi_k
    for k in sorted(pmf, reverse=True):
        cum += pmf[k]
        if cum >= alpha / 2:
            upper = k
            break
    return lower, upper


MUTATIONS = {
    "year":       lambda v: "9999",
    "source":     lambda v: "__변조된출처__",
    "predicate":  lambda v: "art:__invalid__",
    "note":       lambda v: (v or "") + "__변조__",
}


def main():
    ap = argparse.ArgumentParser(description="검증 도구의 오류 탐지력 확인")
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--injections", type=int, default=100, help="주입할 오류 건수")
    ap.add_argument("--seed", type=int, default=42, help="표본 추출 시드")
    ap.add_argument("--inject-seed", type=int, default=None,
                    help="오류 주입 시드. 표본 추출 시드와 반드시 달라야 한다. "
                         "동일하면 주입 대상이 표본에 그대로 포함되어 "
                         "탐지력이 과대평가된다. 미지정 시 seed+1로 설정.")
    ap.add_argument("--confidence", type=float, default=0.99)
    ap.add_argument("--margin", type=float, default=0.03)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    original = out_dir / "relations.csv"
    backup = out_dir / "_relations_original.csv"
    tmp_report = out_dir / "_mutation_validation.json"

    print("=" * 72)
    print("변이 검증 — 검증 도구의 오류 탐지력 확인")
    print("=" * 72)

    inject_seed = args.inject_seed if args.inject_seed is not None else args.seed + 1
    if inject_seed == args.seed:
        raise SystemExit(
            "오류: 주입 시드와 표본 추출 시드가 같습니다. 동일한 시드를 사용하면 "
            "random.sample이 같은 난수열을 생성하여 주입 대상이 표본에 그대로 "
            "포함되므로, 탐지력이 실제보다 과대평가됩니다."
        )

    df = pd.read_csv(original, dtype=str).fillna("")
    N = len(df)

    # 오류를 여러 필드에 고르게 분산 주입한다.
    # 표본 추출과 독립적인 시드를 사용하여 주입 대상과 표본이
    # 우연히 일치하지 않도록 한다.
    rng = random.Random(inject_seed)
    targets = rng.sample(range(N), args.injections)
    fields = list(MUTATIONS)
    injected = []
    for i, idx in enumerate(targets):
        field = fields[i % len(fields)]
        before = df.at[idx, field]
        df.at[idx, field] = MUTATIONS[field](before)
        injected.append({"row": int(idx), "field": field,
                         "before": before, "after": df.at[idx, field]})

    true_rate = args.injections / N
    print(f"  모집단 크기 N        : {N:,}")
    print(f"  주입 오류 건수 k     : {args.injections:,}")
    print(f"  참 오류율 k/N        : {true_rate*100:.4f}%")
    print(f"  주입 필드            : {sorted({x['field'] for x in injected})}")
    print(f"  주입 시드 / 표본 시드: {inject_seed} / {args.seed}  (독립)")

    shutil.copy(original, backup)
    try:
        df.to_csv(original, index=False, encoding="utf-8")
        subprocess.run(
            ["python3", "validate_dataset.py",
             "--confidence", str(args.confidence),
             "--margin", str(args.margin),
             "--seed", str(args.seed),
             "--out-dir", str(out_dir),
             "--report", str(tmp_report)],
            check=True, capture_output=True,
        )
        result = json.load(open(tmp_report, encoding="utf-8"))
    finally:
        shutil.copy(backup, original)
        backup.unlink(missing_ok=True)

    s3 = result["stage3"]
    n, e = s3["sample_size_n"], s3["errors_in_sample"]
    lo, hi = s3["wilson_95_ci"]
    expected = n * true_rate

    print(f"\n  표본 크기 n          : {n:,}")
    print(f"  기대 탐지 건수 n·k/N : {expected:.2f}")
    print(f"  실제 탐지 건수       : {e}")
    print(f"  관측 오류율          : {s3['observed_error_rate']*100:.4f}%")
    print(f"  95% Wilson 신뢰구간  : [{lo*100:.4f}%, {hi*100:.4f}%]")
    print(f"  필드별 탐지          : "
          f"{ {k: v for k, v in s3['field_error_counts'].items() if v} }")

    # 탐지 건수는 초기하분포를 따른다. 관측값이 그 99% 예측구간 안에 들어오면
    # 검증 도구가 이론적으로 기대되는 수준으로 오류를 탐지하고 있다고 판단한다.
    pi_lo, pi_hi = hypergeom_interval(N, args.injections, n, alpha=0.01)
    within = pi_lo <= e <= pi_hi
    detected = e > 0

    print(f"  99% 초기하 예측구간  : [{pi_lo}, {pi_hi}] 건")
    print("\n  판정")
    print(f"    오류 탐지 여부                : {'탐지됨' if detected else '탐지 실패'}")
    print(f"    탐지 건수의 예측구간 부합     : {'부합' if within else '이탈'}")

    verdict = detected and within
    print(f"\n  결과: 검증 도구 {'정상 작동' if verdict else '점검 필요'}")

    tmp_report.unlink(missing_ok=True)
    with open(out_dir / "mutation_test_report.json", "w", encoding="utf-8") as f:
        json.dump({
            "population_N": N,
            "injections_k": args.injections,
            "true_error_rate": true_rate,
            "mutated_fields": sorted({x["field"] for x in injected}),
            "sample_size_n": n,
            "expected_detections": expected,
            "actual_detections": e,
            "observed_error_rate": s3["observed_error_rate"],
            "wilson_95_ci": [lo, hi],
            "field_error_counts": s3["field_error_counts"],
            "hypergeometric_99_prediction_interval": [pi_lo, pi_hi],
            "detections_within_prediction_interval": within,
            "errors_detected": detected,
            "verdict": "PASS" if verdict else "FAIL",
            "sampling_seed": args.seed,
            "injection_seed": inject_seed,
        }, f, ensure_ascii=False, indent=2)
    print(f"  리포트 저장: {out_dir / 'mutation_test_report.json'}")
    print("=" * 72)


if __name__ == "__main__":
    main()
