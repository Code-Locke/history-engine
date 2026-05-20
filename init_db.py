from api.database import engine, Base
import api.models 

print("PostgreSQL에 테이블 생성을 시도합니다...")
Base.metadata.create_all(bind=engine)
print("테이블 생성 완료!")