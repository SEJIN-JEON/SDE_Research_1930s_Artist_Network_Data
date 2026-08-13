1930년대 예술가 네트워크 재편찬 데이터셋 — 심사용 공개 패키지
논문 「학술적 디지털 판본으로서의 데이터 재편찬 연구 — 1930년대 예술가 네트워크
데이터 판본 비교를 중심으로」의 재편찬 데이터셋 및 검증 자료 패키지입니다.
논문 각주 33)에서 "원천 데이터셋, 인물·기관 식별자 구성에
참조한 RDB 재편 제안 파일, 그리고 이를 변환하여 얻은 인물·기관 노드 파일과 관계
발생 기록 파일의 형태로 함께 공개 및 공유한다"고 밝힌 자료 일체입니다.
패키지 구성
파일	형식	내용
`SDE\_ver.2\_raw\_dataset.xlsx`	Excel	원천 데이터셋 (51,796행)
`RDB\_재편\_제안.xlsx`	Excel	인물·기관 식별자 사전 및 구조 설계안. `SDE\\\_ver.1`(기존 원본 데이터셋), `SDE\\\_ver.2(재편찬 데이터셋)`(현재 원천 데이터셋), `1. Person\\\_Info`, `2. Group\\\_Info`(전체 기관 통합 목록), `2.Workplace\\\_Info`/`2.1.W\\\_P\\\_Joint\\\_Table`, `3.School\\\_Info`/`3.1.S\\\_P\\\_Joint\\\_Table`, `4.Group\\\_Info`(소속단체)/`4.1.G\\\_P\\\_Joint\\\_Table`, `5. Publication\\\_Info`/`5.1.P\\\_P\\\_Joint\\\_Table`, `6. Notes` 시트로 구성
`schema.ttl`	RDF/Turtle	온톨로지 정의. 2.3절에서 서술한 술어 계층(art:participatesIn → studiedAt/memberOf/workedAt, art:publishedIn → authored/edited/issued 등)을 포함
`persons.csv` / `persons.ttl`	CSV / RDF	인물 노드 10,440개
`institutions.csv` / `institutions.ttl`	CSV / RDF	기관 노드 775개
`relations.csv`	CSV	관계 발생 기록 51,796건 (엑셀에서 바로 열어보는 용도)
`relations\_rdfstar.ttl`	RDF-Star/Turtle	관계 발생 기록 51,796건, 술어 계층이 반영된 전체 변환분
`README.md`	Markdown	본 문서
술어(predicate) 구조 — 2.3절 중심
소속·참여 관계: `art:participatesIn`을 상위 술어로 하여 `art:studiedAt`(학교),
`art:memberOf`(소속단체), `art:workedAt`(직장)을 `rdfs:subPropertyOf`로 배치하였습니다.
발표매체 관계: `art:publishedIn`을 상위 술어로 하여 원문 비고(note)에 따라
`art:authored`(기본값, 48,601건), `art:issued`(비고에 '발행' 포함, 15건)로 세분하였습니다.
`art:contributedTo`·`art:edited`·`art:printed`는 스키마상으로는 존재하나 현재
데이터셋에서는 해당하는 사례는 없습니다.
출생 관계: 단일값 사실이므로 `art:bornIn` 독립 술어로 유지하였습니다.
검증
```bash
pip install rdflib==7.6.0 pandas networkx openpyxl

# 1) RDF-Star 파싱 검증 (2.3절 서술과 동일)
python3 -c "
import rdflib
lines = \[l for l in open('relations\_rdfstar.ttl', encoding='utf-8') if not l.strip().startswith('<<')]
open('\_check.ttl','w',encoding='utf-8').writelines(lines)
g = rdflib.Graph()
g.parse('\_check.ttl', format='turtle')
print(len(g), 'triples parsed OK')
"
# → 538,480 triples parsed OK

# 2) 논문 \[표 6]·\[표 7] 재현
python3 reproduce\_centrality\_tables.py SDE\_ver.2\_raw\_dataset.xlsx
```
주의사항
학교(59.7%)·직장(72.3%)·소속단체(22.2%) 관계는 발표매체(100%)에 비해 출처 확보가
진행 중입니다.
제국미술학교·도쿄미술학교·도쿄음악학교 관련 자료는 다른 기관에 비해 상대적으로
집중적으로 보완되어 있습니다. 이들 기관의 중심성 지표를 해석할 때는 논문 3.1~3.2절에서
밝힌 자료 밀도의 한계를 함께 고려해야 합니다.
이 데이터셋은 완결된 정본이 아니라 지속적으로 갱신되는 디지털 판본입니다.
현재는 두 번째 판본에 해당합니다. 후속 연구가 이 데이터를 토대로 다른 관심사(여성 예술가,
영화·연극 분야, 특정 매체 등)를 향하여 확장하는 것을 전제로 공개합니다.
