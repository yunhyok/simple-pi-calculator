# Simple PI Calculator — 프로젝트 현황 (2026-09-15, v0.3.0)

## 목적
PCB/MLO stack-up(엑셀) + decap SPICE(.mod)/.s2p 모델 + via 설정으로 PWR별 PDN 임피던스 |Z(f)|를 계산·표시하는 Windows 프로그램.
저장소: https://github.com/yunhyok/simple-pi-calculator (public, MIT). push 완료(사용자 PC의 git/gh로 수행 — 세션 git 프록시는 이 저장소 접근 불가). v0.1.0 Release: SimplePICalculator-Setup-0.1.0.exe(46.5 MB), -win64.zip(67.3 MB). PC 작업 사본: D:\Works\simple\simple-pi-calculator

## 확정 사양
- Plane 모델: 직사각형 cavity resonator modal 전개(Okoshi; Lei/Techentin/Gilbert; Novak), 손실: tanδ + 도체 표면임피던스(Γ_c 보정), 포트 크기 sinc 인자, 고차모드 준정적 tail 분리.
- 기하: decap/PAD 모두 Top. PAD와 decap은 plane 양 끝단, 양단 margin 20% → H = 1.4 × D_ref (D_ref = 해당 PWR decap 행의 최대 Distance to PAD), PAD = (W/2, 0.2·D_ref). 사용자는 Width만 입력.
- Via: drill, anti-pad, **via pitch(PWR–GND via 간격, 기본 1.0 mm)** 공통 입력; loop L = image via-pair 항 + anti-pad 구간(리뷰 F1), 길이는 가까운 plane 표면까지(F2), 다중 via는 GMD 포트 폭(F3). Goldfarb–Pucel 옵션.
- Dummy Cap: decap 행별 체크. 활성 시 절반이 dummy → ceil(N/2) via set, set당 2개 병렬(홀수면 마지막 1개).
- 입력 표 헤더: Stack-up = Layer Number, Layer Name(선택), Thickness(mm), Conductivity(S/m), Dk, Df(퍼지 매칭). PWR = PWR Name, Layer Number, GND Layer Number, PWR Plane Width. Decap = PWR Name, Decap File Name, Number of Decaps, Distance to PAD (mm), Dummy Cap(선택).
- Plot: pyqtgraph log-log, 1/10/100 MHz 마커 + 읽기 표, Ω/mΩ/µΩ 전환, zoom/pan/reset, plane-only 곡선 옵션.
- 지속성: %APPDATA%\SimplePICalculator\autosave.spical.json 1 s debounce 자동 저장·복구, Save/Save As(.spical.json), 최근 파일.
- Help: QTextBrowser HTML 15페이지 + 9개 개념도(PNG), "Open in Browser".
- 배포: PyInstaller onedir + Inno Setup, GitHub Actions(windows-latest) → `v*` 태그 시 Release 첨부.

## 검증
- 530 pytest 통과(물리 골든값: VDD_CORE 38.69/3.294/35.42/139.5 mΩ, 2.902 Ω @100k/1M/10M/100M/1G; VDD_IO 152.0/12.37/62.72/881.7 mΩ, 4.494 Ω). 독립 리뷰 2회(docs/REVIEW-physics.md, docs/REVIEW-code.md).
- Windows CI(windows-latest) 테스트 530개 통과, PyInstaller + Inno Setup 빌드 성공(v0.1.0). 실제 설치/실행 확인은 아직 사용자 미수행.

## 참고
- 기존 PI-Expectation 코드는 "Internal tool" 표기 → 코드 복사 없이 새로 구현.
- 설계 문서: docs/DESIGN.md v1.2 (+ Appendix C 리뷰 응답, D 구현 노트).

## v0.2.0 (2026-09-15) 변경
- PWR별 Number of PADs 열(엑셀 "Number of PADs"/"PAD Count"); pad들은 PAD 끝단에 행으로 배치, 각 pad마다 via set, 이상적 공통 노드에서 병렬 결합(§2.8). N_pad=1은 0.1.0과 동일.
- "Vias per decap pad"(pad당 병렬 via 수, 기본 1)로 의미 변경; 프로젝트 스키마 3, 자동 마이그레이션.
- 성능: row별 그룹 정적 모달 합, BLAS 기반 Z(f) 조립, 주파수 청크·PWR 병렬 스레드, 캐시, rcond 추정 → 대형 plane 6–15× 빠름; Advanced > Worker threads.
- Export: CSV(전체/PWR별), Touchstone .s1p/.sNp(S/Z, RI/MA, R=1 Ω), 모든 plot PNG/SVG 일괄 저장; Reset view 버튼 + Ctrl+D(Ctrl+0), Duplicate Row는 Ctrl+Shift+D.
- 버그 수정: Run/Save 전 열린 셀 편집 커밋(입력값 무시되던 문제 — 사용자가 보고한 Dummy Cap "결과 동일" 원인), 체크박스 셀 아무 곳 클릭으로 토글, decap 모델 캐시를 파일 내용 해시로.
- Dummy cap 물리 검토: 2N+dummy가 10 MHz에서 2N 일반보다 낮게 나오는 것은 bank 간 공진 피크 이동(9.66→8.81 MHz) 때문으로 정상.
- 리뷰 문서: docs/REVIEW-v0.2.md. 테스트 671 통과. Release: SimplePICalculator-Setup-0.2.0.exe.

## v0.3.0 (2026-09-15) 변경
- Decaps 탭 전역 옵션 "Distance: Fixed / Normal (±1σ)": 각 via set의 거리를 행의 평균거리 D 중심, σ(mm, 기본 0.5) 절단정규분포(±1σ)에서 seed 기반 난수 추출(inverse-CDF, Acklam Φ⁻¹, numpy default_rng). Dummy Cap 행은 via set당 1 샘플. D_ref = max(D_k)+σ로 plane 높이는 seed와 무관. 프로젝트 스키마 4.
- 리뷰(docs/REVIEW-v0.3.md): Φ⁻¹ 상위 꼬리 정밀도 수정, σ 필드가 다른 컨트롤 편집 시 반올림되던 GUI 버그 수정. σ=0.5 mm에서 seed 간 |Z| 편차 ≤1.5 %. 테스트 705 통과.
