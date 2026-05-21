"""개발용 DB 테이블 초기화 스크립트."""

import api.models  # noqa: F401 - 모델 import로 metadata 등록을 보장한다.
from api.database import init_db


print("PostgreSQL 테이블 생성을 시작합니다.")
init_db()
print("테이블 생성 완료!")
