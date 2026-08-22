"""프로세스를 넘어 살아남는 캐시.

st.cache_data는 Streamlit 프로세스 메모리다. 그래서 두 가지가 안 된다.
  - 컨테이너를 다시 세우면 날아가서, 배포 직후 첫 접속자가 외부 API를 전부 기다린다.
  - 크론(별도 프로세스)이 미리 받아둬도 화면 쪽 캐시는 여전히 비어 있다.

data/.cache/는 볼륨이라 재기동을 견디고 프로세스도 공유한다. 값이 자주 바뀌지 않는
외부 조회(시세 바스켓·공매도·시가총액)를 여기에 둔다.
"""

import json
import time
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).parent / "data" / ".cache"


def _path(name: str, suffix: str) -> Path:
    return CACHE_DIR / f"{name}.{suffix}"


def age_seconds(name: str, suffix: str = "json") -> float | None:
    path = _path(name, suffix)
    return (time.time() - path.stat().st_mtime) if path.exists() else None


def fresh(name: str, max_age: float, suffix: str = "json") -> bool:
    age = age_seconds(name, suffix)
    return age is not None and age <= max_age


def save_json(name: str, payload) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _path(name, "json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def load_json(name: str, max_age: float):
    if not fresh(name, max_age):
        return None
    try:
        return json.loads(_path(name, "json").read_text(encoding="utf-8"))
    except Exception:
        return None


def save_frame(name: str, frame: pd.DataFrame) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        frame.to_json(_path(name, "frame.json"), orient="split", date_format="iso")
    except Exception:
        pass


def load_frame(name: str, max_age: float) -> pd.DataFrame | None:
    if not fresh(name, max_age, "frame.json"):
        return None
    try:
        return pd.read_json(_path(name, "frame.json"), orient="split")
    except Exception:
        return None


HEALTH_CACHE = "source_health"


def record_health(source: str, rows: int) -> None:
    """소스별 수집 건수를 남긴다.

    **부분 실패가 가장 찾기 어렵다.** 세 소스 중 하나가 죽어도 나머지가 캐시를 갱신하므로
    신선도 표는 정상으로 보이고, 화면에는 그냥 데이터가 조금 적을 뿐이다. 실제로 Yahoo
    바스켓 포맷 불일치와 애널리스트 오탐이 그렇게 숨어 있었다. 건수를 기록해 두면
    0으로 떨어진 소스가 드러난다.
    """
    stored = load_json(HEALTH_CACHE, 86400 * 7) or {}
    stored[source] = {"rows": int(rows), "at": pd.Timestamp.now(tz="UTC").isoformat()}
    save_json(HEALTH_CACHE, stored)


def health() -> dict:
    return load_json(HEALTH_CACHE, 86400 * 7) or {}
