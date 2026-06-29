# LG트윈스 유튜브 쇼츠 자동화 파이프라인 — 기술 설계서

> **문서 유형:** Technical Design Document (기술팀장 작성)  
> **버전:** v1.0  
> **작성일:** 2026-06-27  
> **대상 채널:** LG트윈스 단일팀 유튜브 쇼츠 채널 (신규 런칭)  
> **참조:** LGTWINSTV 공식 채널 (33만 구독자)

---

## 목차

1. [법적·정책적 제약 분석](#1-법적정책적-제약-분석)
2. [엔드투엔드 자동화 파이프라인 아키텍처](#2-엔드투엔드-자동화-파이프라인-아키텍처)
3. [영상 소싱 전략](#3-영상-소싱-전략)
4. [자동 편집 파이프라인](#4-자동-편집-파이프라인)
5. [자막 생성 파이프라인](#5-자막-생성-파이프라인)
6. [Content ID / 저작권 우회 전략](#6-content-id--저작권-우회-전략)
7. [업로드 자동화 (YouTube Data API v3)](#7-업로드-자동화-youtube-data-api-v3)
8. [자동화 스크립트 구성](#8-자동화-스크립트-구성)
9. [모니터링·알림 체계](#9-모니터링알림-체계)
10. [인프라 구성](#10-인프라-구성)
11. [예상 개발 기간과 난이도](#11-예상-개발-기간과-난이도)
12. [리스크 및 대응 방안](#12-리스크-및-대응-방안)

---

## 1. 법적·정책적 제약 분석

### 1.1 TVING/KBO 2차 창작 가이드라인

TVING-CJ ENM이 2024년 발표한 KBO 2차 창작 가이드라인의 핵심:

| 항목 | 내용 | 영향 |
|------|------|------|
| **영상 길이** | 40초 미만 (39.9초까지) | 모든 쇼츠는 엄격히 39초 이하로 제한 |
| **사용 목적** | **비상업적 목적 ONLY** | 애드센스 수익화 불가 — 채널 수익 모델에 결정적 제약 |
| **허용 플랫폼** | 제한 없음 (YouTube, Instagram, TikTok 등) | 크로스 플랫폼 배포 가능 |
| **허용 대상** | 누구나 (일반 팬) | 진입 장벽 없음 |
| **원본 출처** | TVING 편집본만 허용 (논란 있음) | 구단 자체 촬영 영상은 별도 검토 필요 |

> ⚠️ **결정적 제약:** TVING의 "비상업적 목적 ONLY" 조항은 애드센스 수익화 채널 운영을 불가능하게 만든다.  
> → **전략적 대안:** (A) 비수익화 팬 채널로 시작해 구독자 확보 후 구단과 공식 파트너십 추진, (B) 구단 자체 콘텐츠만 사용하는 수익화 채널

### 1.2 구단 자체 콘텐츠 (LGTWINSTV)

구단이 직접 제작·소유한 콘텐츠는 TVING 제약에서 자유롭다:
- **저작권:** LG트윈스 구단 소유 → 2차 가공·수익화 모두 가능 (구단 허가 전제)
- **소싱:** LGTWINSTV 공식 유튜브 채널 (33만 구독자)
- **콘텐츠 유형:** 하이라이트, 비하인드, 인터뷰, 훈련 영상 등

### 1.3 추천 전략 (하이브리드 접근)

```
┌─────────────────────────────────────────────────────┐
│  전략: 구단 공식 콘텐츠 중심 + 팬 창작 콘텐츠 혼합    │
├─────────────────────────────────────────────────────┤
│  70%: LGTWINSTV 공식 영상 2차 편집 (수익화 가능)      │
│  20%: TVING 중계 하이라이트 (비수익화, 팬 유입용)      │
│  10%: 오리지널 숏폼 콘텐츠 (자체 제작)                 │
├─────────────────────────────────────────────────────┤
│  목표: 1일 3~5개 쇼츠 업로드, 구독자 1만 명 달성 시    │
│        구단과 정식 라이선스 협상 추진                    │
└─────────────────────────────────────────────────────┘
```

---

## 2. 엔드투엔드 자동화 파이프라인 아키텍처

### 2.1 전체 흐름도

```
┌─────────────────────────────────────────────────────────────────┐
│                    LG TWINS SHORTS PIPELINE                      │
│                                                                  │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐     │
│  │  SOURCE  │   │  INGEST  │   │  EDIT    │   │ SUBTITLE │     │
│  │ DISCOVER │──▶│ & FETCH  │──▶│ & RENDER │──▶│ & BURN   │     │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘     │
│       │              │              │              │             │
│  ┌────┴────┐   ┌────┴────┐   ┌────┴────┐   ┌────┴────┐        │
│  │•KBO경기 │   │•yt-dlp  │   │•FFmpeg  │   │•Whisper │        │
│  │•LGTWINS │   │•API poll│   │•9:16    │   │•SRT→ASS │        │
│  │•Web scr │   │•DB store│   │•템플릿   │   │•자막번인 │        │
│  └─────────┘   └─────────┘   └─────────┘   └─────────┘        │
│                                                   │             │
│                                                   ▼             │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐     │
│  │ ANALYTICS│   │  ALERT   │   │  UPLOAD  │   │  REVIEW  │     │
│  │ & REPORT │◀──│ & MONITOR│◀──│ & SCHED  │◀──│ & HUMAN  │     │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘     │
│       │              │              │              │             │
│  ┌────┴────┐   ┌────┴────┐   ┌────┴────┐   ┌────┴────┐        │
│  │•조회수  │   │•Discord │   │•YT API  │   │•웹 대시 │        │
│  │•구독자  │   │•Slack   │   │•v3       │   │•보드      │        │
│  │•저작권  │   │•Email   │   │•OAuth2   │   │•승인/반려│        │
│  └─────────┘   └─────────┘   └─────────┘   └─────────┘        │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 파이프라인 단계별 상세

| 단계 | 입력 | 처리 | 출력 | 담당 모듈 |
|------|------|------|------|-----------|
| **1. Discover** | KBO 경기 일정, LGTWINSTV RSS | 신규 영상 감지, 우선순위 결정 | 작업 큐 | `discovery.py` |
| **2. Fetch** | YouTube URL, TVING VOD URL | yt-dlp 다운로드, 메타데이터 추출 | 원본 mp4 + JSON | `ingest.py` |
| **3. Clip** | 원본 영상 | 장면 전환 감지, 하이라이트 구간 추출 | 15~39초 클립 | `clip.py` |
| **4. Edit** | 클립 + 템플릿 | 9:16 크롭, PIP, 모션그래픽 | 편집본 mp4 | `edit.py` |
| **5. Transcribe** | 편집본 mp4 | Whisper 음성 인식, SRT 생성 | SRT 자막 | `transcribe.py` |
| **6. Burn** | 편집본 + SRT | ASS 변환, 스타일 적용, 번인 | 최종 mp4 | `burn.py` |
| **7. Review** | 최종 mp4 + 메타 | 사람 검수 대시보드 표시 | 승인/반려 | `review.py` |
| **8. Upload** | 승인된 mp4 | YouTube Data API v3 업로드/예약 | Video ID | `upload.py` |
| **9. Monitor** | Video ID | 24h 후 저작권 상태 확인 | 상태 리포트 | `monitor.py` |

---

## 3. 영상 소싱 전략

### 3.1 소스 A: LGTWINSTV 공식 채널 (★★★ 우선)

**방식:** YouTube Data API v3 + yt-dlp

```python
# PlaylistItems: list로 신규 영상 감지
# yt-dlp로 고화질 다운로드 (구단 소유 콘텐츠 = 저작권 안전)
```

**구현:**
1. `playlistItems.list` API로 LGTWINSTV 업로드 피드 폴링 (1시간 간격)
2. 신규 영상 감지 시 `yt-dlp`로 최고 화질 다운로드
3. 메타데이터 (제목, 설명, 업로드일, 조회수) DB 저장
4. `last_processed_video_id` 기반 증분 수집

**장점:**
- 저작권 100% 안전 (구단 소유 콘텐츠)
- 수익화 가능
- 구단 공식 콘텐츠 = 신뢰도 높음

**단점:**
- 업로드 주기가 불규칙적
- 콘텐츠 양 제한적

### 3.2 소스 B: TVING/KBO 중계 영상 (★★☆ 보조)

**방식 A — yt-dlp 직접 다운로드 (권장)**

```bash
# KBO 하이라이트 채널 등에서 다운로드
yt-dlp -f "best[height<=1080]" \
  --download-archive archive.txt \
  "https://www.youtube.com/@KBO_Highlights"
```

**방식 B — TVING VOD 웹 스크래핑 (제한적)**

```python
# Selenium/Playwright로 TVING VOD 페이지 접근
# DRM 없는 미리보기 클립 추출 (전체 VOD는 DRM 보호)
# 40초 미만 클립만 허용됨에 주의
```

> ⚠️ TVING VOD는 DRM이 적용되어 있어 전체 영상 추출은 불법.  
> 미리보기 클립이나 KBO 공식 유튜브 하이라이트 채널 활용이 현실적.

**방식 C — 화면 녹화 (비권장)**

```python
# OBS Studio + Python 스크립트로 실시간 중계 녹화
# 품질 저하, 실시간 자원 소모, 법적 리스크
# → 다른 방법이 모두 실패했을 때만 고려
```

**TVING 소스 사용 시 주의사항:**
- 반드시 39.9초 이하로 편집
- 비수익화 채널에만 업로드 (TVING 비상업적 조건)
- TVING 로고/워터마크 유지 (제거 시 법적 리스크)

### 3.3 소스 C: 팬 직접 촬영 영상 (★☆☆ 니치)

- 경기장 직관 팬이 촬영한 영상
- 트위터/X, 인스타그램에서 해시태그 수집
- 사용 전 반드시 원작자 허가 필요

### 3.4 소스 D: 자체 제작 오리지널 콘텐츠

- 선수 기록 데이터 기반 모션그래픽
- 경기 프리뷰/리뷰 카드뉴스 영상화
- 스탯 시각화 쇼츠

---

## 4. 자동 편집 파이프라인

### 4.1 FFmpeg 편집 워크플로우

```bash
# ─── Step 1: 하이라이트 구간 추출 ───
# 씬 전환 감지 (scene change detection)
ffmpeg -i source.mp4 \
  -filter:v "select='gt(scene,0.3)',showinfo" \
  -f null - 2> scenes.txt

# ─── Step 2: 9:16 세로 크롭 (1080x1920) ───
ffmpeg -i source.mp4 \
  -vf "crop=ih*9/16:ih,scale=1080:1920,setsar=1" \
  -c:a copy \
  vertical.mp4

# ─── Step 3: PIP (Picture-in-Picture) ───
# 메인 화면 + 반응 영상 오버레이
ffmpeg -i main.mp4 -i reaction.mp4 \
  -filter_complex "[1:v]scale=320:180[ov];[0:v][ov]overlay=W-w-10:H-h-10" \
  output.mp4

# ─── Step 4: 템플릿 오버레이 (채널 브랜딩) ───
ffmpeg -i content.mp4 -i template.png \
  -filter_complex "[0:v][1:v]overlay=0:0" \
  branded.mp4

# ─── Step 5: 오디오 처리 ───
# 음성 피치 변조 (±5%) — Content ID 음성 매칭 우회
ffmpeg -i input.mp4 \
  -af "asetrate=44100*1.05,aresample=44100,atempo=1.0" \
  output.mp4
```

### 4.2 자동 클리핑 알고리즘

```python
# clip_engine.py — 지능형 하이라이트 구간 추출
class ClipEngine:
    """
    1. Scene Change Detection: FFmpeg select 필터로 컷 탐지
    2. Audio Peak Detection: 큰 함성/효과음 구간 탐지
    3. Score Detection: 스코어보드 변화 감지 (OCR)
    4. Rule-based: 경기 시작/종료, 홈런, 삼진 등 이벤트 매칭
    """
```

### 4.3 쇼츠 템플릿 시스템

```yaml
# templates/lg_twins_default.yaml
name: "LG Twins Default"
canvas: 1080x1920  # 9:16 portrait
zones:
  main:
    x: 0, y: 280, w: 1080, h: 1080  # 1:1 정사각형 메인 영상
  title_bar:
    x: 0, y: 0, w: 1080, h: 280     # 상단 타이틀 영역
  caption:
    x: 0, y: 1360, w: 1080, h: 280  # 하단 캡션 영역
  logo:
    x: 30, y: 30, w: 120, h: 120    # LG Twins 로고
bgm: "assets/lg_twins_theme_15s.mp3"
intro: "assets/intro_3s.mp4"
outro: "assets/outro_2s.mp4"
```

---

## 5. 자막 생성 파이프라인

### 5.1 OpenAI Whisper 통합

```python
# transcribe.py — Whisper 모델로 음성 → 자막
import whisper

class SubtitleGenerator:
    def __init__(self, model_size="large-v3"):
        # large-v3: 최고 정확도, GPU 권장
        # medium: CPU에서도 사용 가능, 정확도 준수
        # turbo: 속도 최적화
        self.model = whisper.load_model(model_size)
    
    def transcribe(self, video_path: str) -> dict:
        result = self.model.transcribe(
            video_path,
            language="ko",           # 한국어 고정
            task="transcribe",       # STT (translate X)
            word_timestamps=True,    # 단어별 타임스탬프
            verbose=False
        )
        return result
    
    def to_srt(self, segments: list) -> str:
        # Whisper segments → SRT 포맷 변환
        ...
    
    def to_ass(self, srt: str, style: dict) -> str:
        # SRT → ASS 변환 + 스타일 적용
        # ASS: Advanced SubStation Alpha (글꼴, 색상, 위치, 애니메이션)
        ...
```

### 5.2 자막 스타일 가이드

```ini
[V4+ Styles]
Style: LG_Default,Noto Sans KR,18,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1
# 폰트: Noto Sans KR (한글 최적화)
# 크기: 18pt (쇼츠 화면 최적)
# 색상: 흰색 텍스트 + 검은색 외곽선
# 위치: 하단 중앙
# 효과: Karaoke 템포 매칭 (단어 단위 하이라이트)
```

### 5.3 자막 번인 (Burn-in)

```bash
# FFmpeg로 ASS 자막을 영상에 직접 번인
ffmpeg -i video.mp4 -vf "ass=subtitle.ass" output_burned.mp4
# 번인 = 쇼츠에서 자막 ON/OFF 불필요, 접근성 향상
```

---

## 6. Content ID / 저작권 우회 전략

### 6.1 YouTube Content ID 작동 원리

```
┌─────────────────────────────────────────────────┐
│  YouTube Content ID 매칭 파이프라인                │
│                                                  │
│  업로드 → 오디오 지문 매칭 → 영상 지문 매칭        │
│       ↓                          ↓               │
│  [Audio Fingerprint]      [Video Fingerprint]    │
│  • 멜로디/비트 매칭        • 프레임 유사도 매칭    │
│  • 피치 변조에 약함        • 크롭/색보정에 강함    │
│  • 커버/리믹스도 감지      • PIP/오버레이는 우회    │
└─────────────────────────────────────────────────┘
```

### 6.2 다층 방어 전략 (Defense in Depth)

| 계층 | 기법 | 효과 | 적용 대상 |
|------|------|------|-----------|
| **L1: 길이** | 39초 미만 유지 | Shorts는 Content ID가 완화됨 (60초 미만) | 모든 영상 |
| **L2: 오디오** | 피치 변조 (±5~7%), BGM 교체 | 오디오 지문 매칭 우회 | 중계 하이라이트 |
| **L3: 영상** | 9:16 크롭 + PIP + 오버레이 | 영상 지문 변형 | 모든 영상 |
| **L4: 색상** | 색보정/필터 (대비 +15%, 채도 ±10%) | 프레임 매칭 방해 | 중계 하이라이트 |
| **L5: 프레임** | 0.1초 블랙 프레임 주기적 삽입 | 타임라인 매칭 방해 | TVING 콘텐츠 |
| **L6: 메타** | 2차 창작/리믹스로 분류, 공정이용 주장 | 사후 대응 | 전체 |

### 6.3 FFmpeg 변형 필터 체인

```bash
# Content ID 우회용 종합 필터 체인
ffmpeg -i source.mp4 \
  -vf "
    crop=ih*9/16:ih,                    # L3: 9:16 크롭
    scale=1080:1920,
    eq=contrast=1.15:saturation=0.9,    # L4: 색보정
    drawbox=x=0:y=0:w=1080:h=1920:
           color=black@0.3:t=2,         # L3: 반투명 오버레이
    fps=30                              # 프레임레이트 정규화
  " \
  -af "
    asetrate=44100*1.05,                # L2: 피치 +5%
    aresample=44100,
    atempo=1.0,
    volume=1.1                          # 볼륨 +10%
  " \
  -t 39                                 # L1: 39초 제한
  output.mp4
```

### 6.4 안전한 BGM 라이브러리

- **YouTube Audio Library** (완전 무료, Content ID 면제)
- **Epidemic Sound** (상업용 라이선스, 월 구독)
- **Artlist** (상업용 라이선스)
- 자체 제작 BGM (AI 작곡: Suno, Udio 등)

### 6.5 저작권 스트라이크 대응 프로토콜

```
스트라이크 발생 시:
1. 즉시 해당 영상 비공개 처리 (자동)
2. 모든 예약 업로드 일시 중지 (자동)
3. 기술팀장에게 Discord/Slack 알림
4. 24시간 내 이의제기 또는 삭제 결정
5. 패턴 분석 → 필터 체인 조정
```

---

## 7. 업로드 자동화 (YouTube Data API v3)

### 7.1 OAuth 2.0 설정

```python
# upload.py — YouTube Data API v3 업로드

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

class YouTubeUploader:
    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly"
    ]
    
    def __init__(self, client_secrets_path: str, token_path: str):
        self.credentials = self._authenticate(client_secrets_path, token_path)
        self.youtube = build("youtube", "v3", credentials=self.credentials)
    
    def upload_shorts(self, video_path: str, metadata: dict) -> str:
        """
        metadata = {
            "title": "오지환 역전 홈런! | 2026.06.27 LG vs KT",
            "description": "... #Shorts #LGTwins",
            "tags": ["LG트윈스", "KBO", "홈런", "Shorts"],
            "category_id": "17",  # Sports
            "privacy_status": "private",  # 검수 후 public
            "publish_at": "2026-06-28T12:00:00+09:00",  # 예약 업로드
            "made_for_kids": False,
            "self_declared_made_for_kids": False
        }
        """
        body = {
            "snippet": {
                "title": metadata["title"],
                "description": metadata["description"],
                "tags": metadata["tags"],
                "categoryId": metadata["category_id"]
            },
            "status": {
                "privacyStatus": metadata.get("privacy_status", "private"),
                "selfDeclaredMadeForKids": False
            }
        }
        
        media = MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            resumable=True,       # 대용량 업로드 재개 지원
            chunksize=1024*1024*5 # 5MB 청크
        )
        
        request = self.youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )
        
        response = request.execute()
        return response["id"]  # YouTube Video ID
```

### 7.2 Shorts 인식 조건

YouTube Shorts로 분류되기 위한 조건:
1. **세로 영상 (9:16)** — 가로보다 세로가 긴 비율
2. **60초 이하** (2025년부터 3분까지 확대되었으나, 60초 미만 권장)
3. **제목/설명에 `#Shorts` 포함** (필수는 아니나 권장)

### 7.3 API 할당량 관리

| 작업 | 할당량 소모 | 일일 한도 |
|------|------------|----------|
| `videos.insert` (업로드) | 1,600 units | 6회/일 (10,000 units 한도 내) |
| `videos.list` (상태 확인) | 1 unit | 여유 충분 |
| `playlistItems.list` | 1 unit | 여유 충분 |
| `search.list` | 100 units | 주의 필요 |

> **전략:** 하루 3~5개 업로드 시 4,800~8,000 units 소모 → 할당량 내 충분

### 7.4 업로드 스케줄링 전략

```python
# scheduler.py — 최적 업로드 시간대
BEST_TIMES_KST = [
    "07:00",  # 출근길
    "12:00",  # 점심시간
    "18:00",  # 퇴근길
    "21:00",  # 야간 프라임타임
]

# 경기 직후 30분 이내 업로드 → 최대 도달률
# 예약 업로드로 최적 시간대에 자동 배치
```

---

## 8. 자동화 스크립트 구성

### 8.1 프로젝트 구조

```
lg-twins-shorts-pipeline/
├── config/
│   ├── settings.yaml           # 전체 설정
│   ├── channels.yaml           # 소스 채널 목록
│   ├── templates/              # 쇼츠 템플릿 정의
│   │   └── lg_default.yaml
│   └── credentials/            # API 키 (gitignore)
│       ├── client_secrets.json # YouTube OAuth
│       └── service_account.json
├── src/
│   ├── __init__.py
│   ├── discovery.py            # 신규 영상 감지
│   ├── ingest.py               # 영상 다운로드
│   ├── clip.py                 # 하이라이트 클리핑
│   ├── edit.py                 # FFmpeg 편집
│   ├── transcribe.py           # Whisper 자막
│   ├── burn.py                 # 자막 번인
│   ├── upload.py               # YouTube API 업로드
│   ├── monitor.py              # 상태 모니터링
│   ├── alert.py                # 알림 발송
│   ├── scheduler.py            # 스케줄링
│   ├── pipeline.py             # 파이프라인 오케스트레이션
│   └── db.py                   # SQLite 상태 관리
├── assets/
│   ├── logo_lg_twins.png
│   ├── intro_3s.mp4
│   ├── outro_2s.mp4
│   ├── bgm_lg_theme_15s.mp3
│   └── fonts/
│       └── NotoSansKR-Bold.ttf
├── tests/
│   ├── test_discovery.py
│   ├── test_ingest.py
│   ├── test_edit.py
│   ├── test_transcribe.py
│   └── test_upload.py
├── scripts/
│   ├── run_pipeline.py         # 메인 실행 스크립트
│   ├── run_daily.sh            # cron 트리거
│   └── setup_env.sh            # 환경 설정
├── data/
│   ├── raw/                    # 다운로드 원본
│   ├── processed/              # 편집 완료본
│   └── uploaded/               # 업로드 완료본 (아카이브)
├── logs/
│   └── pipeline.log
├── requirements.txt
├── Makefile
└── README.md
```

### 8.2 핵심 의존성

```txt
# requirements.txt
yt-dlp>=2024.0
openai-whisper>=20240930
google-api-python-client>=2.140
google-auth-oauthlib>=1.2
google-auth-httplib2>=0.2
Pillow>=10.0
pyyaml>=6.0
requests>=2.31
httpx>=0.27
pydantic>=2.0
schedule>=1.2
rich>=13.0          # CLI 출력
typer>=0.12         # CLI 프레임워크
python-dotenv>=1.0

# 옵션 (성능 향상)
faster-whisper>=1.0  # Whisper CTranslate2 최적화
onnxruntime>=1.18    # Whisper ONNX 추론
```

### 8.3 메인 파이프라인 오케스트레이션

```python
# pipeline.py — 전체 파이프라인 조율

class LGTwinsShortsPipeline:
    def __init__(self, config_path: str):
        self.config = load_config(config_path)
        self.discovery = Discovery(self.config)
        self.ingest = Ingest(self.config)
        self.clipper = Clipper(self.config)
        self.editor = Editor(self.config)
        self.transcriber = Transcriber(self.config)
        self.burner = Burner(self.config)
        self.uploader = Uploader(self.config)
        self.monitor = Monitor(self.config)
        self.db = PipelineDB(self.config.db_path)
    
    def run(self, mode: str = "auto"):
        """
        mode: "auto" (완전 자동), "semi" (검수 후 업로드), "dry" (시뮬레이션)
        """
        # 1. 신규 소스 발견
        sources = self.discovery.find_new()
        
        for src in sources:
            job_id = self.db.create_job(src)
            try:
                # 2. 다운로드
                raw_path = self.ingest.download(src)
                self.db.update_job(job_id, "downloaded")
                
                # 3. 클리핑
                clips = self.clipper.extract_highlights(raw_path)
                
                for i, clip in enumerate(clips):
                    # 4. 편집
                    edited = self.editor.process(clip)
                    
                    # 5. 자막 생성
                    srt = self.transcriber.transcribe(edited)
                    
                    # 6. 자막 번인
                    final = self.burner.burn(edited, srt)
                    
                    # 7. 검수 or 자동 업로드
                    if mode == "semi":
                        self.db.enqueue_review(job_id, final)
                    else:
                        video_id = self.uploader.upload(final, src.metadata)
                        self.db.update_job(job_id, "uploaded", video_id)
                        
            except Exception as e:
                self.db.update_job(job_id, "failed", str(e))
                self.alert.send(f"Pipeline failed: {job_id}", e)
        
        # 8. 모니터링 (24h+ 지난 영상 상태 체크)
        self.monitor.check_all_recent()
```

### 8.4 크론 스케줄링

```bash
# crontab -e
# 매시간 정각: 신규 영상 감지
0 * * * * cd /path/to/pipeline && python3 scripts/run_pipeline.py --mode auto

# 경기 종료 후 15분 (KBO 경기 일정 기반)
15 22 * * * cd /path/to/pipeline && python3 scripts/run_pipeline.py --mode auto --post-game

# 매일 09:00: 업로드 상태 리포트
0 9 * * * cd /path/to/pipeline && python3 scripts/run_pipeline.py --mode monitor

# 매주 월요일 10:00: 주간 리포트
0 10 * * 1 cd /path/to/pipeline && python3 scripts/run_pipeline.py --mode weekly-report
```

---

## 9. 모니터링·알림 체계

### 9.1 모니터링 대시보드 (Streamlit)

```python
# dashboard.py — 실시간 모니터링 웹 대시보드
# streamlit run dashboard.py

import streamlit as st
from src.monitor import Monitor

monitor = Monitor()

st.title("LG Twins Shorts Pipeline Monitor")

# 파이프라인 상태
col1, col2, col3, col4 = st.columns(4)
col1.metric("오늘 업로드", monitor.today_uploads())
col2.metric("저작권 클레임", monitor.copyright_claims(), delta="-2")
col3.metric("총 조회수", monitor.total_views())
col4.metric("구독자", monitor.subscriber_count())

# 작업 큐
st.subheader("작업 큐")
st.dataframe(monitor.get_job_queue())

# 저작권 경고
st.subheader("⚠️ 저작권 경고")
st.dataframe(monitor.get_copyright_warnings())

# 최근 업로드
st.subheader("최근 업로드")
for video in monitor.recent_uploads():
    st.video(video.url)
    st.caption(f"조회수: {video.views} | 상태: {video.status}")
```

### 9.2 알림 체계

| 이벤트 | 채널 | 수신자 | 응답 시간 |
|--------|------|--------|----------|
| 업로드 성공 | Discord (정보) | 전체 팀 | - |
| 업로드 실패 | Discord (오류) + Slack | 기술팀장 | 1시간 이내 |
| 저작권 클레임 | Discord (긴급) + 이메일 | 기술팀장, 대표 | 즉시 (15분) |
| 저작권 스트라이크 | Discord (심각) + SMS | 전원 | 즉시 (5분) |
| 주간 리포트 | 이메일 | 대표, 마케팅팀장 | - |
| API 할당량 80% | Discord (경고) | 기술팀장 | 당일 |
| 디스크 90% | Discord (경고) | 기술팀장 | 1시간 이내 |

### 9.3 Discord 알림 구현

```python
# alert.py
class DiscordAlert:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
    
    def send(self, level: str, title: str, message: str, **kwargs):
        colors = {
            "info": 0x3498DB,
            "warning": 0xF1C40F,
            "error": 0xE74C3C,
            "critical": 0x992D22
        }
        embed = {
            "title": title,
            "description": message,
            "color": colors.get(level, 0x95A5A6),
            "timestamp": datetime.utcnow().isoformat()
        }
        if "video_url" in kwargs:
            embed["url"] = kwargs["video_url"]
        
        requests.post(self.webhook_url, json={"embeds": [embed]})
```

---

## 10. 인프라 구성

### 10.1 하드웨어 요구사항 (WSL Ubuntu)

| 리소스 | 최소 | 권장 | 용도 |
|--------|------|------|------|
| CPU | 4코어 | 8코어+ | FFmpeg 인코딩, Whisper 추론 |
| RAM | 16GB | 32GB | Whisper large-v3 모델 (VRAM ≈ 10GB) |
| GPU | 없음 | NVIDIA RTX 3060+ | Whisper 추론 가속 (CUDA) |
| 디스크 | 100GB | 500GB SSD | 원본 영상 + 가공본 + 아카이브 |

### 10.2 GPU 가속 (선택사항)

```bash
# WSL2에서 NVIDIA GPU 사용 설정
# 1. Windows에 NVIDIA 드라이버 설치
# 2. WSL2에 CUDA toolkit 설치
sudo apt install nvidia-cuda-toolkit

# 3. faster-whisper로 GPU 추론
pip install faster-whisper
# large-v3 모델 기준: CPU 4분 → GPU 15초 (16배 향상)
```

### 10.3 상시 가동 설정

```bash
# systemd 서비스 등록 (WSL에서는 systemd 활성화 필요)
# /etc/systemd/system/lg-shorts-pipeline.service
[Unit]
Description=LG Twins Shorts Pipeline Scheduler
After=network.target

[Service]
Type=simple
User=kbm
WorkingDirectory=/home/kbm/F:ai-projects/10_PROJECTS/lg-twins-shorts
ExecStart=/usr/bin/python3 scripts/run_pipeline.py --mode daemon
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
```

---

## 11. 예상 개발 기간과 난이도

### 11.1 개발 로드맵

```
Phase 1: 기반 구축 (1~2주)
├── WSL 환경 설정 (FFmpeg, Python, CUDA)
├── YouTube API OAuth 인증
├── SQLite DB 설계
└── 설정 파일 구조

Phase 2: 소싱·수집 (2~3주)
├── yt-dlp 통합
├── LGTWINSTV 채널 폴링
├── TVING/KBO 소스 리서치
└── 메타데이터 수집기

Phase 3: 편집 엔진 (3~4주) ★핵심 난이도
├── FFmpeg 필터 체인 설계
├── 씬 전환 감지
├── 9:16 크롭 + PIP
├── 템플릿 시스템
└── Content ID 우회 필터

Phase 4: 자막 시스템 (2~3주)
├── Whisper 모델 설정
├── SRT/ASS 변환
├── 자막 스타일링
└── 번인 통합

Phase 5: 업로드 자동화 (1~2주)
├── YouTube Data API v3 통합
├── 메타데이터 최적화
├── 예약 업로드
└── 할당량 관리

Phase 6: 모니터링·알림 (1~2주)
├── 업로드 상태 추적
├── 저작권 클레임 감지
├── Discord/Slack 알림
└── Streamlit 대시보드

Phase 7: 안정화·테스트 (2~3주)
├── 통합 테스트
├── 엣지 케이스 처리
├── 성능 최적화
└── 운영 문서화

총 예상: 12~19주 (3~5개월)
```

### 11.2 난이도 평가

| 구성 요소 | 난이도 | 사유 |
|-----------|--------|------|
| yt-dlp 통합 | ★☆☆☆☆ | 검증된 라이브러리, API 안정적 |
| FFmpeg 편집 | ★★★☆☆ | 필터 체인 설계에 전문성 필요, 디버깅 까다로움 |
| Content ID 우회 | ★★★★★ | 가장 높은 난이도. 지속적 실험·조정 필요. 실패 리스크 존재 |
| Whisper 자막 | ★★☆☆☆ | 모델 성숙도 높음, 한국어 정확도 양호 |
| YouTube API | ★★☆☆☆ | 공식 문서 충실, 할당량 관리만 주의 |
| 모니터링 | ★★☆☆☆ | 표준 웹훅/대시보드 패턴 |
| 자동 클리핑 | ★★★★☆ | 야구 중계의 하이라이트 자동 탐지는 ML 문제 |

---

## 12. 리스크 및 대응 방안

### 12.1 리스크 매트릭스

| 리스크 | 확률 | 영향 | 대응 방안 |
|--------|------|------|-----------|
| TVING 저작권 클레임 | **높음** | 치명적 | 구단 자체 콘텐츠 우선, TVING 소스 최소화 |
| Content ID 매칭 | **중간** | 높음 | 다층 방어 필터, 지속적 튜닝 |
| 채널 정지 (3스트라이크) | 낮음 | 치명적 | 자동 정지 시스템, human-in-the-loop 검수 |
| YouTube API 제한 강화 | 낮음 | 중간 | 다중 API 키 로테이션 |
| Whisper 정확도 저하 | 낮음 | 낮음 | 사람 검수 단계에서 보완 |
| WSL 리소스 부족 | 중간 | 중간 | 클라우드 GPU 인스턴스로 마이그레이션 계획 |

### 12.2 채널 보호 전략

```
1. Human-in-the-loop: 모든 영상은 업로드 전 사람 검수 (semi-auto 모드)
2. 점진적 확장: 첫 달 1일 1개 → 안정화 후 3~5개
3. 백업 채널: 보조 채널 사전 생성, 스트라이크 시 신속 전환
4. 법률 자문: 스포츠 저작권 전문 변호사 사전 검토
5. 구단 관계: LG트윈스 구단과 공식 채널 인정 협의 추진
```

---

## 부록 A: 초기 셋업 체크리스트

- [ ] WSL Ubuntu에 FFmpeg 6.0+ 설치
- [ ] Python 3.11+ 가상환경 구성
- [ ] yt-dlp 설치 및 테스트
- [ ] OpenAI Whisper large-v3 다운로드
- [ ] Google Cloud Console에서 YouTube Data API v3 활성화
- [ ] OAuth 2.0 클라이언트 ID 생성
- [ ] Discord 웹훅 URL 확보
- [ ] LGTWINSTV 채널 ID 확인
- [ ] SQLite DB 초기화
- [ ] 템플릿 에셋 제작 (로고, 인트로, 아웃트로, BGM)
- [ ] 첫 수동 업로드로 Shorts 분류 확인
- [ ] Content ID 테스트 (비공개 업로드 후 24h 모니터링)

## 부록 B: 참고 자료

- [YouTube Data API v3 문서](https://developers.google.com/youtube/v3)
- [FFmpeg Filters 문서](https://ffmpeg.org/ffmpeg-filters.html)
- [Whisper GitHub](https://github.com/openai/whisper)
- [yt-dlp GitHub](https://github.com/yt-dlp/yt-dlp)
- [TVING KBO 2차 창작 가이드라인](https://namu.wiki/w/TVING/KBO%20%EB%A6%AC%EA%B7%B8%20%EA%B4%80%EB%A0%A8%20%EB%85%BC%EB%9E%80)
- [YouTube Shorts 제작 가이드](https://support.google.com/youtube/answer/13486873)
