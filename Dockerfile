FROM public.ecr.aws/lambda/python:3.11

# 작업 디렉토리 설정
WORKDIR ${LAMBDA_TASK_ROOT}

# 필요한 파일들 복사
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/

# 메인 핸들러 설정
CMD ["app.main.handler"]