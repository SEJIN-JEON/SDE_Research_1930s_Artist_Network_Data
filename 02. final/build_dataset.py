#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_dataset.py
================================================================================
1930년대 예술가 네트워크 재편찬 데이터셋 — 결정론적 변환 스크립트

입력 (단일 소스):
    RDB_재편_제안.xlsx
        · 'SDE_ver.2(재편찬 데이터셋)' : 원천 관계 기록 51,796행
        · '1. Person_Info'             : 인물 식별자 마스터 10,440건
        · '2. Group_Info'              : 기관 식별자 마스터 775건
        · '6. Notes'                   : 편집 결정 기록

출력 (output/):
    persons.csv, persons.ttl
    institutions.csv, institutions.ttl
    schema.ttl
    relations.csv
    relations_rdfstar.ttl
    relation_type_summary.csv
    person_matching_exceptions.csv
    build_report.json

--------------------------------------------------------------------------------
설계 원칙
--------------------------------------------------------------------------------
[P1] 단일 소스(single source of truth)
     인물·기관 식별자 마스터와 원천 관계 데이터를 모두 동일한 파일에서 읽는다.
     서로 다른 사본을 대조하지 않으므로, 사본 간 행 순서 불일치로 인한
     출처(art:source)·행번호(art:sourceRow) 오류가 원천적으로 발생하지 않는다.

[P2] 원본 행 순서 보존
     어떠한 단계에서도 원천 시트의 행 순서를 재배열하지 않는다.
     relation_id R{n:08d}는 원천 시트의 n번째 데이터 행에 1:1로 대응하며,
     art:sourceRow는 그 행의 실제 엑셀 행 번호(n+1)를 가리킨다.
     이에 따라 relation_id 번호 n ↔ sourceRow 번호 n+1의 관계가 전 행에서
     성립하며, 이는 논문 본문의 예시 표기 규약과 동일하다.

[P3] 완전 중복 행의 보존
     원천 8개 필드가 모두 동일한 행이라도 병합하지 않고 각각 독립적인
     관계 발생 기록(RelationOccurrence)으로 보존한다. 논문 3장에서 밝힌 바와 같이
     "하나의 관계에 대하여 서로 다른 자료가 서로 다른 정보를 제공할 때
     그 차이를 병합하여 지우지 않고 병렬적으로 남겨 두려는" 설계 원칙에 따른다.

[P4] 출처의 명시적 처리
     출처가 공란인 관계는 공란으로 남기지 않고 '연구자'로 명시한다.
     이에 따라 출처 필드 충족률은 100%가 되지만, 이는 문헌으로 확인된 비율과
     구분되어야 한다. 문헌출처 확인률은 별도로 산출하여
     relation_type_summary.csv에 기록한다.

[P5] 편집 결정의 불개입
     '6. Notes' 시트의 "직장에서 조광 삭제" 결정은 식별자 사전
     ('2.Workplace_Info' 시트)에 적용된 것으로, 관계 발생 기록에는 적용하지 않는다.
     원천 관계 데이터 51,796행 전체를 변환 대상으로 삼으며, 이는 논문 3장이
     보고하는 관계 유형별 분포(직장 1,092건, 합계 51,796건)와 정합한다.
--------------------------------------------------------------------------------
"""

import re
import json
import unicodedata
from pathlib import Path
from collections import defaultdict

import pandas as pd

SRC_XLSX = "RDB_재편_제안.xlsx"
SRC_SHEET = "SDE_ver.2(재편찬 데이터셋)"
OUT_DIR = Path("output")

EDITION = "edition:재편찬판본"
RESEARCHER_LABEL = "연구자"

NS = {
    "art": "https://sde1930.org/ontology#",
    "person": "https://sde1930.org/person/",
    "inst": "https://sde1930.org/institution/",
    "rel": "https://sde1930.org/relation/",
    "edition": "https://sde1930.org/edition/",
}


# ------------------------------------------------------------------ utilities
def norm(s):
    if pd.isna(s):
        return ""
    return unicodedata.normalize("NFC", str(s)).strip()


def reformat_id(raw, prefix, width=6):
    return f"{prefix}{int(re.search(r'\d+', str(raw)).group()):0{width}d}"


def extract_year(raw):
    m = re.search(r"\d{4}", str(raw))
    return m.group(0) if m else ""


def ttl_escape(s):
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace("\t", "\\t")
    )


def assign_predicate(attribute, note):
    """논문 3장의 술어 이층 구조에 따른 배정.

    art:participatesIn  ← studiedAt / memberOf / workedAt
    art:publishedIn     ← authored / edited / issued / contributedTo / printed
    art:bornIn          (단일값 사실, 상위 술어 없음)

    발표매체 관계의 하위 술어는 비고(note)에 기록된 역할 표기로 판정한다.
    '편집'과 '발행'이 함께 나타나는 경우 발행을 우선한다(발행인 명기가
    더 구체적인 서지 사실이므로). 이 경우 편집 역할은 predicate로 반영되지
    않고 art:note 원문에 보존된다. 한 관계 발생 기록당 하나의 술어만
    부여하는 현재 스키마의 구조적 제약이며, 해당 사례는 build_report.json의
    multi_role_notes 항목에 개별 기록된다.
    """
    if attribute == "출생":
        return "art:bornIn"
    if attribute == "학교":
        return "art:studiedAt"
    if attribute == "소속단체":
        return "art:memberOf"
    if attribute == "직장":
        return "art:workedAt"
    if attribute == "발표매체":
        if "발행" in note:
            return "art:issued"
        if "편집" in note:
            return "art:edited"
        return "art:authored"
    return None


# ------------------------------------------------------------------ main build
def main():
    OUT_DIR.mkdir(exist_ok=True)
    report = {}

    # ---------- 1. 원천 자료 적재 ----------
    person_info = pd.read_excel(SRC_XLSX, sheet_name="1. Person_Info").fillna("")
    group_info = pd.read_excel(SRC_XLSX, sheet_name="2. Group_Info").fillna("")
    src = pd.read_excel(SRC_XLSX, sheet_name=SRC_SHEET).fillna("")
    src.columns = ["작가", "직업", "국적", "년도", "소속단체", "속성", "비고", "출처"]

    for c in src.columns:
        src[c] = src[c].map(norm)
    for c in ["작가 이름", "직업", "국적"]:
        person_info[c] = person_info[c].map(norm)
    for c in ["단체명", "단체 속성"]:
        group_info[c] = group_info[c].map(norm)

    person_info["person_id"] = person_info["Person_ID"].apply(lambda x: reformat_id(x, "P"))
    group_info["institution_id"] = group_info["단체_ID"].apply(lambda x: reformat_id(x, "I"))

    report["input"] = {
        "source_file": SRC_XLSX,
        "source_sheet": SRC_SHEET,
        "source_relation_rows": len(src),
        "person_master_rows": len(person_info),
        "institution_master_rows": len(group_info),
    }

    # ---------- 2. 노드 파일 ----------
    persons_out = person_info[["person_id", "작가 이름", "직업", "국적"]].rename(
        columns={"작가 이름": "name", "직업": "job", "국적": "nationality"}
    )
    persons_out.to_csv(OUT_DIR / "persons.csv", index=False, encoding="utf-8")

    insts_out = group_info[["institution_id", "단체명", "단체 속성"]].rename(
        columns={"단체명": "name", "단체 속성": "relation_type_hint"}
    )
    insts_out.to_csv(OUT_DIR / "institutions.csv", index=False, encoding="utf-8")

    # ---------- 3. 식별자 해소 ----------
    exact, by_name_nat, by_name = {}, defaultdict(set), defaultdict(set)
    for _, r in person_info.iterrows():
        exact[(r["작가 이름"], r["직업"], r["국적"])] = r["person_id"]
        by_name_nat[(r["작가 이름"], r["국적"])].add(r["person_id"])
        by_name[r["작가 이름"]].add(r["person_id"])
    inst_lookup = dict(zip(group_info["단체명"], group_info["institution_id"]))

    fallback_log = []

    def resolve_person(name, job, nat, excel_row):
        """(이름, 직업, 국적) 완전일치 → (이름, 국적) → (이름) 순 단계적 완화.

        Person_Info의 '작가 이름'은 10,440명 전원 유일하므로 최종 단계에서
        해소가 보장된다. 완화가 적용된 행은 전부 기록하여 감사 가능하게 한다.
        """
        if (name, job, nat) in exact:
            return exact[(name, job, nat)], "exact"
        c = by_name_nat.get((name, nat), set())
        if len(c) == 1:
            fallback_log.append(
                {"sourceRow": f"데이터수정!{excel_row}", "name": name,
                 "source_job": job, "source_nationality": nat, "tier": "name+nationality"}
            )
            return next(iter(c)), "name+nationality"
        c = by_name.get(name, set())
        if len(c) == 1:
            fallback_log.append(
                {"sourceRow": f"데이터수정!{excel_row}", "name": name,
                 "source_job": job, "source_nationality": nat, "tier": "name-only"}
            )
            return next(iter(c)), "name-only"
        return None, "unresolved"

    # ---------- 4. 관계 발생 기록 ----------
    rows, unresolved, multi_role = [], [], []

    for i, r in src.iterrows():
        excel_row = i + 2            # 헤더 1행 + 0-index 보정
        relation_no = i + 1          # relation_id 번호 = sourceRow 번호 - 1

        pid, tier = resolve_person(r["작가"], r["직업"], r["국적"], excel_row)
        iid = inst_lookup.get(r["소속단체"])
        if pid is None or iid is None:
            unresolved.append({
                "sourceRow": f"데이터수정!{excel_row}",
                "작가": r["작가"], "소속단체": r["소속단체"],
                "reason": "person" if pid is None else "institution",
            })
            continue

        note = r["비고"]
        if r["속성"] == "발표매체" and "편집" in note and "발행" in note:
            multi_role.append({"sourceRow": f"데이터수정!{excel_row}", "note": note})

        rows.append({
            "relation_id": f"R{relation_no:08d}",
            "subject": pid,
            "predicate": assign_predicate(r["속성"], note),
            "object": iid,
            "year": extract_year(r["년도"]),
            "attributeLabel": r["속성"],
            "note": note,                                  # 원문 그대로 보존
            "source": r["출처"] or RESEARCHER_LABEL,        # [P4]
            "sourceRow": f"데이터수정!{excel_row}",
            "edition": EDITION,
        })

    relations = pd.DataFrame(rows)
    relations.to_csv(OUT_DIR / "relations.csv", index=False, encoding="utf-8")

    # ---------- 5. 관계 유형별 분포 및 문헌출처 확인률 ----------
    summary_rows = []
    for attr in ["출생", "학교", "소속단체", "직장", "발표매체"]:
        sub = relations[relations["attributeLabel"] == attr]
        lit = (sub["source"] != RESEARCHER_LABEL).sum()
        summary_rows.append({
            "관계유형": attr,
            "빈도수": len(sub),
            "문헌출처_확인건수": int(lit),
            "문헌출처_확인률(%)": round(lit / len(sub) * 100, 1) if len(sub) else 0.0,
        })
    lit_all = (relations["source"] != RESEARCHER_LABEL).sum()
    summary_rows.append({
        "관계유형": "합계",
        "빈도수": len(relations),
        "문헌출처_확인건수": int(lit_all),
        "문헌출처_확인률(%)": round(lit_all / len(relations) * 100, 1),
    })
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(OUT_DIR / "relation_type_summary.csv", index=False, encoding="utf-8")

    pd.DataFrame(fallback_log).to_csv(
        OUT_DIR / "person_matching_exceptions.csv", index=False, encoding="utf-8"
    )

    # ---------- 6. TTL 산출 ----------
    write_schema_ttl(relations)
    write_persons_ttl(persons_out)
    write_institutions_ttl(insts_out)
    write_relations_ttl(relations)

    # ---------- 7. 리포트 ----------
    report["output"] = {
        "persons": len(persons_out),
        "institutions": len(insts_out),
        "relation_occurrences": len(relations),
        "predicate_distribution": relations["predicate"].value_counts().to_dict(),
        "attribute_distribution": relations["attributeLabel"].value_counts().to_dict(),
        "source_field_completeness": "100.0%",
        "literature_source_rate_overall": f"{lit_all / len(relations) * 100:.1f}%",
    }
    report["identifier_convention"] = {
        "rule": "relation_id 번호 n ↔ sourceRow 번호 n+1",
        "verified_all_rows": bool(
            (relations["relation_id"].str[1:].astype(int) + 1
             == relations["sourceRow"].str.extract(r"(\d+)")[0].astype(int)).all()
        ),
    }
    report["person_matching"] = {
        "fallback_rows": len(fallback_log),
        "fallback_unique_persons": len({x["name"] for x in fallback_log}),
        "tier_breakdown": pd.Series([x["tier"] for x in fallback_log]).value_counts().to_dict()
        if fallback_log else {},
        "unresolved_rows": len(unresolved),
    }
    report["multi_role_notes"] = multi_role
    report["relation_type_summary"] = summary_rows
    report["exact_duplicate_rows_preserved"] = int(
        relations.duplicated(
            subset=["subject", "predicate", "object", "year",
                    "attributeLabel", "note", "source"], keep=False
        ).sum()
    )

    with open(OUT_DIR / "build_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"persons.csv                : {len(persons_out):,}")
    print(f"institutions.csv           : {len(insts_out):,}")
    print(f"relations.csv              : {len(relations):,}")
    print(f"미해결 행                  : {len(unresolved)}")
    print(f"완화 매칭 적용 행          : {len(fallback_log)}")
    print(f"복수 역할 표기 행          : {len(multi_role)}")
    print(f"ID 규약 전행 검증          : {report['identifier_convention']['verified_all_rows']}")
    print("\n관계 유형별 분포 및 문헌출처 확인률")
    print(summary.to_string(index=False))


# ------------------------------------------------------------------ TTL writers
def write_schema_ttl(relations):
    c = relations["predicate"].value_counts().to_dict()
    ttl = f"""@prefix art:     <{NS['art']}> .
@prefix rdf:     <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs:    <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl:     <http://www.w3.org/2002/07/owl#> .
@prefix xsd:     <http://www.w3.org/2001/XMLSchema#> .

################################################################################
# 1930년대 예술가 네트워크 재편찬 데이터셋 — 온톨로지
# 논문 3장 「재편찬 데이터의 설계 원칙과 구조」의 술어 이층 구조를 구현한다.
################################################################################

#-------------------------------------------------------------------- 클래스
art:Person       a rdfs:Class ; rdfs:label "인물" .
art:Institution  a rdfs:Class ; rdfs:label "기관" .
art:Place        a rdfs:Class ; rdfs:label "장소" .

art:School       a rdfs:Class ; rdfs:subClassOf art:Institution ; rdfs:label "학교" .
art:Workplace    a rdfs:Class ; rdfs:subClassOf art:Institution ; rdfs:label "직장" .
art:Group        a rdfs:Class ; rdfs:subClassOf art:Institution ; rdfs:label "소속단체" .
art:Publication  a rdfs:Class ; rdfs:subClassOf art:Institution ; rdfs:label "발표매체" .

# 관계 발생 기록: 인물-기관의 연결 그 자체를 독립적 기술 대상으로 삼는 노드
art:RelationOccurrence a rdfs:Class ; rdfs:label "관계 발생 기록" .

#-------------------------------------------------------- 술어 계층 (1) 소속·참여
# 인물이 특정 기관에 소속되어 활동하였다는 공통 성격을 갖는 관계
art:participatesIn a rdf:Property ; rdfs:label "참여" ;
    rdfs:domain art:Person ; rdfs:range art:Institution .

art:studiedAt  a rdf:Property ; rdfs:subPropertyOf art:participatesIn ;
    rdfs:label "재학" ; rdfs:range art:School .        # {c.get('art:studiedAt', 0):,}건
art:memberOf   a rdf:Property ; rdfs:subPropertyOf art:participatesIn ;
    rdfs:label "소속" ; rdfs:range art:Group .         # {c.get('art:memberOf', 0):,}건
art:workedAt   a rdf:Property ; rdfs:subPropertyOf art:participatesIn ;
    rdfs:label "재직" ; rdfs:range art:Workplace .     # {c.get('art:workedAt', 0):,}건

#-------------------------------------------------------- 술어 계층 (2) 발표매체
# 소속 여부가 아니라 매체에 대해 수행한 역할의 문제
art:publishedIn a rdf:Property ; rdfs:label "발표" ;
    rdfs:domain art:Person ; rdfs:range art:Publication .

art:authored      a rdf:Property ; rdfs:subPropertyOf art:publishedIn ;
    rdfs:label "집필" .        # {c.get('art:authored', 0):,}건
art:edited        a rdf:Property ; rdfs:subPropertyOf art:publishedIn ;
    rdfs:label "편집" .        # {c.get('art:edited', 0):,}건
art:issued        a rdf:Property ; rdfs:subPropertyOf art:publishedIn ;
    rdfs:label "발행" .        # {c.get('art:issued', 0):,}건
art:contributedTo a rdf:Property ; rdfs:subPropertyOf art:publishedIn ;
    rdfs:label "기여" .        # 스키마상 정의, 현 판본에 해당 사례 없음
art:printed       a rdf:Property ; rdfs:subPropertyOf art:publishedIn ;
    rdfs:label "인쇄" .        # 스키마상 정의, 현 판본에 해당 사례 없음

#-------------------------------------------------------- 술어 계층 (3) 출생
# 한 인물에 대해 하나의 값만을 갖는 단일한 사실이므로 상위 술어 없이 독립
art:bornIn a rdf:Property ; rdfs:label "출생" ;
    rdfs:domain art:Person ; rdfs:range art:Place .    # {c.get('art:bornIn', 0):,}건

#------------------------------------------- 관계 발생 기록 연결 및 기술 술어
art:hasOccurrence a rdf:Property ; rdfs:label "관계 발생 기록 연결" ;
    rdfs:range art:RelationOccurrence .

art:subject        a rdf:Property ; rdfs:label "관계의 주체" ; rdfs:range art:Person .
art:predicate      a rdf:Property ; rdfs:label "관계 유형" .
art:object         a rdf:Property ; rdfs:label "관계의 대상" .
art:year           a rdf:Property ; rdfs:label "관계 형성 시기" ; rdfs:range xsd:gYear .
art:attributeLabel a rdf:Property ; rdfs:label "원천 데이터 속성명" ; rdfs:range xsd:string .
art:note           a rdf:Property ; rdfs:label "보충 설명" ; rdfs:range xsd:string .
art:source         a rdf:Property ; rdfs:label "관계의 출처" ; rdfs:range xsd:string .
art:sourceRow      a rdf:Property ; rdfs:label "원천 데이터 행 번호" ; rdfs:range xsd:string .
art:edition        a rdf:Property ; rdfs:label "판본 정보" .

#-------------------------------------------------------------- 노드 기술 술어
art:name              a rdf:Property ; rdfs:label "명칭" .
art:job               a rdf:Property ; rdfs:label "직업" .
art:nationality       a rdf:Property ; rdfs:label "국적" .
art:relationTypeHint  a rdf:Property ; rdfs:label "기관 속성 구분" .
"""
    (OUT_DIR / "schema.ttl").write_text(ttl, encoding="utf-8")


def write_persons_ttl(persons):
    out = [
        f"@prefix art:    <{NS['art']}> .",
        f"@prefix person: <{NS['person']}> .",
        "",
        f"# 인물 노드 {len(persons):,}건",
        "",
    ]
    for _, r in persons.iterrows():
        out.append(f"person:{r['person_id']} a art:Person ;")
        out.append(f'    art:name "{ttl_escape(r["name"])}" ;')
        if r["job"]:
            out.append(f'    art:job "{ttl_escape(r["job"])}" ;')
        out.append(f'    art:nationality "{ttl_escape(r["nationality"])}" .')
        out.append("")
    (OUT_DIR / "persons.ttl").write_text("\n".join(out), encoding="utf-8")


def write_institutions_ttl(insts):
    out = [
        f"@prefix art:  <{NS['art']}> .",
        f"@prefix inst: <{NS['inst']}> .",
        "",
        f"# 기관 노드 {len(insts):,}건",
        "",
    ]
    for _, r in insts.iterrows():
        out.append(f"inst:{r['institution_id']} a art:Institution ;")
        out.append(f'    art:name "{ttl_escape(r["name"])}" ;')
        out.append(f'    art:relationTypeHint "{ttl_escape(r["relation_type_hint"])}" .')
        out.append("")
    (OUT_DIR / "institutions.ttl").write_text("\n".join(out), encoding="utf-8")


def write_relations_ttl(relations):
    """논문 3장 [표] 「RDF-Star 변환 구조」의 세 층위를 그대로 구현한다.

        (1) 기본 트리플        person:P  art:authored  inst:I .
        (2) 인용 트리플        << person:P art:authored inst:I >> art:hasOccurrence rel:R .
        (3) RelationOccurrence rel:R a art:RelationOccurrence ; art:subject ... .

    논문 본문의 예시는 지면 제약상 축약 구문(인용 트리플에 속성을 직접 부여)으로
    표기하였으나, 두 표기는 동일한 구조를 가리킨다. 완전 중복 행을 각각
    독립적으로 보존해야 하므로([P3]) 산출 파일에서는 층위를 명시적으로 풀어 쓴다.
    """
    out = [
        f"@prefix art:     <{NS['art']}> .",
        f"@prefix person:  <{NS['person']}> .",
        f"@prefix inst:    <{NS['inst']}> .",
        f"@prefix rel:     <{NS['rel']}> .",
        f"@prefix edition: <{NS['edition']}> .",
        "@prefix xsd:     <http://www.w3.org/2001/XMLSchema#> .",
        "",
        "#" * 78,
        f"# 관계 발생 기록 {len(relations):,}건",
        "#",
        "# 각 기록은 다음 세 층위로 표현된다.",
        "#   (1) 기본 트리플        — 인물과 기관의 연결",
        "#   (2) 인용 트리플        — 그 연결을 주어로 삼아 관계 발생 기록에 연결",
        "#   (3) RelationOccurrence — 시기·출처·비고·판본을 결합한 독립 기술 단위",
        "#",
        "# 원천 8개 필드가 모두 동일한 행이라도 병합하지 않고 각각 독립적인",
        "# 관계 발생 기록으로 보존한다(설계 원칙 P3).",
        "#" * 78,
        "",
    ]

    # (1) 기본 트리플 — 중복 제거 후 1회만 선언
    base = relations[["subject", "predicate", "object"]].drop_duplicates()
    out.append(f"# ---- (1) 기본 트리플 {len(base):,}건 (고유 인물-술어-기관 조합) ----")
    out.append("")
    for _, b in base.iterrows():
        out.append(f"person:{b['subject']} {b['predicate']} inst:{b['object']} .")
    out.append("")

    # (2)+(3) 인용 트리플과 RelationOccurrence
    out.append(f"# ---- (2)(3) 인용 트리플 및 관계 발생 기록 {len(relations):,}건 ----")
    out.append("")
    for _, r in relations.iterrows():
        rid = f"rel:{r['relation_id']}"
        out.append(
            f"<< person:{r['subject']} {r['predicate']} inst:{r['object']} >> "
            f"art:hasOccurrence {rid} ."
        )
        out.append(f"{rid} a art:RelationOccurrence ;")
        out.append(f"    art:subject person:{r['subject']} ;")
        out.append(f"    art:predicate {r['predicate']} ;")
        out.append(f"    art:object inst:{r['object']} ;")
        if r["year"]:
            out.append(f'    art:year "{r["year"]}"^^xsd:gYear ;')
        out.append(f'    art:attributeLabel "{ttl_escape(r["attributeLabel"])}" ;')
        if r["note"]:
            out.append(f'    art:note "{ttl_escape(r["note"])}" ;')
        out.append(f'    art:source "{ttl_escape(r["source"])}" ;')
        out.append(f'    art:sourceRow "{ttl_escape(r["sourceRow"])}" ;')
        out.append(f"    art:edition {EDITION} .")
        out.append("")

    (OUT_DIR / "relations_rdfstar.ttl").write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    main()
